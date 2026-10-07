import asyncio
from unittest.mock import AsyncMock

import pytest
from mongomock_motor import AsyncMongoMockClient

import config
from classifier import WALLET_SCHEMA
from sharp_evidence import DAY, evaluate_records, qualified_market_scope

NOW = 1800000000
AUTO = {'risk': 'low_observed', 'marketMakerStyle': False, 'groups': []}


def rows(n=120, category='Counter-Strike', prefix='cs', retention=1):
    return [dict(conditionId=f'{prefix}{i}', eventId=f'{prefix}-e{i}', category=category,
        resolvedAt=NOW - (120 - i * 120 / n) * DAY,
        firstEntryAt=NOW - (120 - i * 120 / n) * DAY - 3600,
        resolutionObserved=True, settled=True, netPnl=10, invested=100,
        acquiredCost=100, retainedCost=100 * retention, retention=retention,
        prematchCost=100, liveCost=0, postResultCost=0, unknownTimingCost=0,
        eventGroupingKnown=True, gameStartTime=NOW - 200 * DAY) for i in range(n)]


def evaluate(records, pnl=100, roi=.1, auto=None):
    return evaluate_records(records, auto or AUTO, config.get_config(), NOW,
        perf={'net_realized': pnl, 'true_roi': roi})


@pytest.mark.parametrize('pnl,roi', [(-5000, -.5), (0, 0), (None, None)])
def test_unrelated_all_market_results_do_not_erase_sports_qualification(pnl, roi):
    ev = evaluate(rows(), pnl, roi)
    assert ev['category'] == 'SHARP'
    assert ev['qualifiedScopes'] == ['Sports', 'Counter-Strike']
    assert ev['scoreStatus'] == 'qualified'
    assert ev['score'] is not None
    assert not any('Polymarket cashflow' in r for r in ev['reasons'])


def test_candidate_is_not_disqualified_by_unrelated_losses():
    ev = evaluate(rows(5), -1000, -.5)
    assert ev['category'] == 'CANDIDATE'
    assert 'Sports' in ev['candidateScopes']
    assert ev['score'] is None


@pytest.mark.parametrize('other_win_profit,other_loss', [(100, -5), (10, -50)])
def test_sport_specialist_qualifies_even_when_aggregate_record_is_weaker(other_win_profit, other_loss):
    specialty = rows()
    for i, row in enumerate(specialty):
        row['netPnl'] = 50 if i < 72 else -20
    other = rows(240, 'NBA', 'nba')
    for i, row in enumerate(other):
        row['netPnl'] = other_win_profit if i < 30 else other_loss
    ev = evaluate(specialty + other, -10000, -.1)
    assert ev['category'] == 'SHARP'
    assert ev['bestScopeKey'] == 'Counter-Strike'
    assert ev['qualifiedScopes'] == ['Counter-Strike']
    profile = {'primary': ev['category'], 'evidence': ev}
    assert qualified_market_scope(profile, 'Counter-Strike') == 'Counter-Strike'
    assert qualified_market_scope(profile, 'NBA') is None


@pytest.mark.parametrize('auto,retention,expected', [
    ({'risk': 'high', 'marketMakerStyle': False, 'groups': []}, 1, 'PROBABLE_BOT'),
    (AUTO, 0, 'ACTIVE_TRADER'),
])
def test_sports_qualification_still_respects_automation_and_holding(auto, retention, expected):
    ev = evaluate(rows(retention=retention), auto=auto)
    assert ev['category'] == expected
    assert not ev['qualifiedScopes']
    assert ev['score'] is None


@pytest.mark.parametrize('market_slug,qualified_scopes,is_bot,expected_scope', [
    ('cs2-team-team-2026-10-07', ['Counter-Strike'], False, 'Counter-Strike'),
    ('nba-team-team-2026-10-07', ['Counter-Strike'], False, None),
    ('nba-team-team-2026-10-07', ['Sports'], False, None),
    ('cs2-team-team-2026-10-07', ['Sports'], False, 'Sports'),
    ('cs2-team-team-2026-10-07', ['Counter-Strike'], True, None),
    ('presidential-election-2026', ['Sports'], False, None),
])
def test_tape_labels_scores_and_filter_follow_market_scope(monkeypatch, market_slug, qualified_scopes, is_bot, expected_scope):
    import server

    async def run():
        address = '0x' + 'a' * 40
        ev = evaluate(rows())
        ev['qualifiedScopes'] = qualified_scopes
        ev['scopes']['Sports']['score'] = 88
        ev['scopes']['Counter-Strike']['score'] = 72
        ev['scopes']['NBA'] = {'events': 10, 'profit': -100, 'winrate': .3, 'category': 'RETAIL'}
        profile = dict(address=address, primary='SHARP', labels=['SHARP', 'WHALE'],
            smartScore=99, evidence=ev, isBot=is_bot,
            schemaVersion=WALLET_SCHEMA, configSignature=config.signature())
        fake_db = AsyncMongoMockClient()['scope_test']
        await fake_db.wallets.insert_one(profile)
        monkeypatch.setattr(server, 'db', fake_db)
        market = dict(id='market', eventSlug=market_slug, prices=[.6, .4])
        monkeypatch.setattr(server, '_fetch_flat_markets', AsyncMock(return_value=[market]))
        monkeypatch.setattr(server.poly, 'trades', AsyncMock(return_value=[dict(
            proxyWallet=address, size=100, price=.5, timestamp=NOW,
            side='BUY', outcomeIndex=0, type='TRADE')]))
        monkeypatch.setattr(server, '_tape', [])
        monkeypatch.setattr(server, '_tape_updated', None)
        await server._build_tape()
        row = server._tape[0]
        assert row['qualificationScope'] == expected_scope
        assert row['sharp'] == (expected_scope is not None)
        assert row['walletPrimary'] == 'SHARP'
        assert row['walletLabels'] == ['SHARP', 'WHALE']
        assert 'WHALE' in row['labels']
        if expected_scope:
            assert row['primary'] == 'SHARP'
            assert 'SHARP' in row['labels']
            assert row['smartScore'] == ev['scopes'][expected_scope]['score']
        else:
            assert row['primary'] not in {'SHARP', 'PROVEN_SHARP'}
            assert 'SHARP' not in row['labels']
            assert row['smartScore'] is None
        filtered = await server.tape(sharp_only=True, min_size=0, limit=80)
        assert filtered['count'] == int(expected_scope is not None)

    asyncio.run(run())
