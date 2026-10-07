import asyncio
import copy
import time

import pytest

import config
from analyzer import _market_result, analysis_summary, compute_pick_from_doc, refresh_analysis_prices
from classifier import reconstruct_performance
from ai_narrative import generate_intel
from polymarket_client import PolymarketClient, UpstreamError
from sharp_evidence import candidate_market_scope, retention_record, underdog_market_scope
from tests.test_sports_scope import evaluate, rows


def profile(kind='SHARP', entry=None):
    records = rows(120 if kind == 'SHARP' else 10)
    if kind == 'UNDERDOG_TRADER':
        for i, row in enumerate(records):
            row['netPnl'] = 100 if i < 4 else -10
    ev = evaluate(records)
    assert ev['category'] == kind
    return dict(primary=kind, evidence=ev, smartScore=ev['score'], stats={},
        marketEntries={'market': {'entryPrice': entry}} if entry is not None else {})


def analyze(profiles, sides=None, capitals=None):
    sides = sides or ['YES'] * len(profiles)
    capitals = capitals or [1000] * len(profiles)
    market = dict(id='market', eventSlug='cs2-team-team-2026-10-07', prices=[.6, .4], outcomes=['A', 'B'])
    ranked = [(str(i), dict(yes=cap if side == 'YES' else 0, no=cap if side == 'NO' else 0,
        yesShares=cap/.6 if side == 'YES' else 0, noShares=cap/.4 if side == 'NO' else 0))
        for i, (side, cap) in enumerate(zip(sides, capitals))]
    return _market_result(market, ranked, profiles, config.signature())


@pytest.mark.parametrize('price,credit', [(.9, 0), (.969, 0), (.97, 1), (.985, 1), (1., 1)])
def test_cashout_policy_has_explicit_97_cent_boundary(price, credit):
    buy = dict(type='TRADE', side='BUY', outcomeIndex=0, size=100, price=.4, usdcSize=40, timestamp=100)
    sell = dict(type='TRADE', side='SELL', outcomeIndex=0, size=100, price=price, usdcSize=100*price, timestamp=150)
    r = retention_record([buy, sell], [], {'resolved':True, 'resolvedAt':200, 'closed':True, 'ended':True}, {'reconciled':True}, 300)
    assert r['retention'] == credit
    assert r['physicalRetention'] == 0
    assert r['settlementCashoutShares'] == credit * 100
    assert r['settlementCashoutCost'] == credit * 40


@pytest.mark.parametrize('entry', [None, 0, float('nan'), 1.1])
def test_missing_or_invalid_entry_does_not_manufacture_tailability(entry):
    doc = analyze([profile(entry=entry)])
    row = doc['topWallets'][0]
    assert row['entryPrice'] is None
    assert row['slippageCents'] is None
    assert row['tailStatus'] is None
    assert doc['sharpPick']['avgEntry'] is None
    assert doc['sharpPick']['slippageCents'] is None
    assert doc['sharpPick']['entryStatus'] == 'unknown'
    assert doc['tailIntelligence']['tailableCount'] == 0


def test_entry_and_slippage_are_outcome_specific_and_in_cents():
    prof = profile()
    prof['marketEntries'] = {'market': {'entryPrices': {'0':.25, '1':.35}, 'entryPrice':.3}}
    doc = analyze([prof], ['NO'])
    row = doc['topWallets'][0]
    assert row['entryPrice'] == .35
    assert row['slippageCents'] == 5
    assert row['tailStatus'] == 'ACCEPTABLE'
    assert doc['sharpPick']['slippageCents'] == 5


def test_partial_entry_coverage_is_explicit_and_does_not_grant_price_label():
    doc = analyze([profile(entry=.5), profile()])
    pick = doc['sharpPick']
    assert pick['avgEntry'] == .5
    assert pick['entryCoverage'] == .5
    assert pick['entryStatus'] == 'partial'
    assert pick['slippageCents'] is None


def test_underdogs_have_distinct_counts_and_contribute_to_combined_pick():
    doc = analyze([profile('UNDERDOG_TRADER')])
    assert doc['combined']['smartCapitalYes'] == 1000
    assert doc['combined']['candidateCount'] == 0
    assert doc['combined']['underdogCount'] == 1
    assert doc['combined']['smartCount'] == 1
    assert doc['combined']['pick']['side'] == 'YES'
    assert doc['combined']['pick']['underdogCount'] == 1
    assert doc['sharpPick'] is None
    assert analysis_summary(doc)['sharpPick'] is None
    narrative = asyncio.run(generate_intel(analysis_summary(doc)))
    assert 'Underdog' in narrative['verdict']


def test_combined_strength_counts_and_pick_include_all_three_cohorts():
    doc = analyze([profile(), profile('CANDIDATE'), profile('UNDERDOG_TRADER')], ['YES', 'NO', 'NO'], [1000, 300, 500])
    comb = doc['combined']
    assert comb['smartCount'] == 3
    assert comb['candidateCount'] == comb['underdogCount'] == comb['sharpCount'] == 1
    assert comb['smartCapitalNo'] == 800
    assert comb['pick']['side'] == comb['leanSide'] == 'YES'
    assert doc['tailIntelligence']['smartCountNo'] == 2
    assert doc['tailIntelligence']['underdogCountNo'] == 1


def test_saved_holder_quantities_reprice_without_mutating_saved_analysis():
    old = analyze([profile(entry=.4)])
    untouched = copy.deepcopy(old)
    market = {'prices':[.9,.1]}
    pick = compute_pick_from_doc(old, market=market, mode='sharp')
    assert pick['currentPrice'] == .9
    new = refresh_analysis_prices(old, market)
    assert old == untouched
    assert new['prices'] == [.9,.1]
    assert new['smartCapitalYes'] == 1500
    assert new['topWallets'][0]['currentPrice'] == .9
    assert new['topWallets'][0]['slippageCents'] == 50
    assert new['sharpPick']['slippageCents'] == 50
    assert new['combined']['smartCapitalYes'] == new['combined']['pick']['smartCapital']


@pytest.mark.parametrize('category', ['Counter-Strike', 'Other', 'Weather'])
def test_wallet_level_exclusions_cannot_leak_through_candidate_scope(category):
    p = profile('CANDIDATE')
    p['primary'] = 'ACTIVE_TRADER'
    assert candidate_market_scope(p, category) is None
    assert underdog_market_scope(p, category) is None


def test_reconstruction_preserves_separate_entry_prices_for_two_outcomes():
    trades = [dict(type='TRADE', side='BUY', conditionId='m', outcomeIndex=i, size=100,
        usdcSize=100*px, price=px, timestamp=100+i) for i, px in enumerate([.4,.6])]
    result = reconstruct_performance(trades, [], {'m': {'resolved':True,'resolvedAt':200,'payouts':[1,0]}}, 300)
    assert result['per_market'][0]['entryPrices'] == {'0':.4, '1':.6}


@pytest.mark.parametrize('deltas,size', [([0,1,1,2],2), ([0]*7+[1],2), ([0,1,1,1,2,2,2,3],3)])
def test_inclusive_pagination_preserves_every_tied_fill(deltas, size):
    async def run():
        client = PolymarketClient()
        now = int(time.time())
        source = [dict(id=str(i), timestamp=now-d) for i, d in enumerate(deltas)]
        calls = []
        async def activity(user, limit, offset=0, end=None):
            calls.append((end, offset))
            filtered = [r for r in source if r['timestamp'] <= end]
            return filtered[offset:offset+limit]
        client.activity = activity
        actual, capped = await client.activity_paginated('wallet', pages=20, size=size, min_days=1000000)
        assert actual == source
        assert not capped
        assert any(offset > 0 for _, offset in calls)
        await client.close()
    asyncio.run(run())


def test_pagination_budget_exhaustion_is_partial_not_complete():
    async def run():
        client = PolymarketClient()
        now = int(time.time())
        async def activity(user, limit, offset=0, end=None):
            return [dict(id=str(i+offset), timestamp=now) for i in range(limit)]
        client.activity = activity
        actual, capped = await client.activity_paginated('wallet', pages=2, size=2)
        assert len(actual) == 4
        assert capped
        await client.close()
    asyncio.run(run())


def test_activity_request_combines_end_with_offset_and_stable_order():
    async def run():
        client = PolymarketClient()
        params = {}
        async def request(base, path, query):
            params.update(query)
            return []
        client._list = request
        await client.activity('wallet', 500, offset=23, end=100)
        assert params['end'] == 100
        assert params['offset'] == 23
        assert params['sortBy'] == 'TIMESTAMP' and params['sortDirection'] == 'DESC'
        assert params['start'] == 1
        await client.close()
    asyncio.run(run())
