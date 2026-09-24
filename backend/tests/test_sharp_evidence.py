import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mongomock_motor import AsyncMongoMockClient

import config
from classifier import reconstruct_performance, analyze_wallet
from evidence_store import normalize_metadata, validate_books, record_observation, baselines
from sharp_evidence import (DAY, RULE_VERSION, assess_scope, automation_evidence,
    build_evidence, holding_bounds, retention_record, deduplicate)
from tests.test_performance import trade, position

NOW = 1800000000


def record(events, positions=None, meta=None):
    perf = reconstruct_performance(events, positions or [], {"m": meta} if meta else {}, NOW)
    return perf["per_market"][0]


def test_dust_is_one_percent_not_full_conviction():
    r = record([trade(cash=50), trade("SELL", size=99, cash=49.5)], [position(size=1)])
    assert r["retention"] == pytest.approx(.01)
    assert r["capitalRetention"] == pytest.approx(.01)
    assert not r["heldToResolution"]


def test_quantity_and_original_cost_are_distinct():
    events = [trade(size=90, cash=.9), trade(size=10, cash=9)]
    sell = trade("SELL", size=90, cash=.9)
    sell["timestamp"] = 101
    r = record(events + [sell], [position(size=10)])
    assert r["retention"] == pytest.approx(.1)
    assert r["capitalRetention"] == pytest.approx(9 / 9.9)
    assert not r["heldToResolution"]


def test_rebuy_cannot_restore_original_lot():
    events = [trade(), trade("SELL"), trade()]
    for i, e in enumerate(events):
        e["timestamp"] = 100 + i
    r = record(events, [position()])
    assert r["retention"] == .5


def test_paired_yes_no_has_no_directional_retention():
    r = record([trade(cash=60), trade(outcome=1, cash=40)], [position(), position(1, price=0)])
    assert r["retention"] == 0
    assert r["hedgedCost"] == 100


def test_partial_hedge_permanently_reduces_continuity():
    events = [trade(), trade(outcome=1, size=30, cash=20), trade("SELL", outcome=1, size=30, cash=20)]
    for i, e in enumerate(events): e["timestamp"] = 100 + i
    r = record(events, [position()])
    assert r["retention"] == pytest.approx(70 / 130)


@pytest.mark.parametrize("price", [0, 1])
def test_losers_and_winners_both_count_as_held(price):
    r = record([trade()], [position(price=price)])
    assert r["retention"] == 1


def test_delayed_redemption_does_not_change_resolution_time():
    redemption = dict(type="REDEEM", conditionId="m", size=100, usdcSize=100, outcomeIndex=0, timestamp=1000)
    r = record([trade(), redemption], meta={"resolved": True, "resolvedAt": 200, "payouts": [1, 0]})
    assert r["retention"] == 1
    assert r["resolvedAt"] == 200


def test_exit_before_unresolved_event_is_pending_for_holding():
    r = record([trade(), trade("SELL")])
    assert r["settled"]
    assert not r["resolutionObserved"]
    assert r["retention"] is None


def test_earlier_exit_enters_cohort_when_event_resolves():
    r = record([trade(), trade("SELL")], meta={"resolved": True, "resolvedAt": 200, "payouts": [1, 0]})
    assert r["resolutionObserved"]
    assert r["retention"] == 0


def test_known_resolution_recovers_missing_zero_value_position():
    r = record([trade()], meta={"resolved": True, "resolvedAt": 200, "payouts": [0, 1]})
    assert r["netPnl"] == -40
    assert r["retention"] == 1


def test_rounding_precision_does_not_erase_performance():
    events = [trade(size=999.98588, cash=500), trade("SELL", size=999, cash=510)]
    r = record(events, [position(size=.9858)])
    assert r["reconciled"]


def test_transfer_and_missing_basis_make_holding_unknown():
    r = record([trade(), dict(type="TRANSFER", conditionId="m", size=50, timestamp=101)], [position()])
    assert r["retention"] is None
    assert "non_directional_or_unattributed_transfer" in r["holdingIssues"]


def test_fee_adjustment_is_applied_once():
    events = [trade(feeUsd=2), trade("SELL", cash=60, feeUsd=1)]
    assert record(events)["netPnl"] == 17
    events = [trade(cash=42, feeUsd=2, feeIncludedInCashflow=True), trade("SELL", cash=59, feeUsd=1, feeIncludedInCashflow=True)]
    assert record(events)["netPnl"] == 17


def test_identified_duplicate_records_only_are_deduplicated():
    e = trade(id="fill-a")
    assert len(deduplicate([e, dict(e)])) == 1
    assert len(deduplicate([trade(), trade()])) == 2


def test_bounds_do_not_turn_unknown_into_a_fake_fifty():
    rows = [{"retention": 1, "acquiredCost": 10, "retainedCost": 10}] * 93
    rows += [{"retention": None, "acquiredCost": 10}] * 7
    h = holding_bounds(rows)
    assert h["positionLower"] == h["capitalLower"] == .93
    assert h["positionUpper"] == 1
    rows = rows[:86] + [{"retention": 0, "acquiredCost": 10, "retainedCost": 0}] * 4 + [{"retention": None, "acquiredCost": 10}] * 10
    h = holding_bounds(rows)
    assert (h["positionLower"], h["positionUpper"]) == (.86, .96)
    assert holding_bounds(rows, False)["positionLower"] is None


def test_many_tiny_holds_cannot_hide_one_large_exit():
    rows = [{"retention": 1, "acquiredCost": 1, "retainedCost": 1}] * 99
    rows += [{"retention": 0, "acquiredCost": 10000, "retainedCost": 0}]
    h = holding_bounds(rows)
    assert h["positionRate"] == .99
    assert h["capitalRate"] < .01


def good_rows(n=120, days=120, offset=0):
    return [{"conditionId": f"m{i}", "eventId": f"e{i}", "eventGroupingKnown": True,
        "resolvedAt": NOW - (days - i * days / n + offset) * DAY,
        "firstEntryAt": NOW - (days - i * days / n + offset) * DAY - 3600,
        "resolutionObserved": True, "settled": True, "netPnl": 10,
        "invested": 100, "acquiredCost": 100, "retainedCost": 100,
        "retention": 1., "clv": .04, "feesKnown": True, "preMatchOnly": True,
        "category": "Counter-Strike"} for i in range(n)]


def assess(rows, cfg=None, frozen=None):
    cfg = cfg or config.get_config()
    cfg["_signature"] = config.signature()
    return assess_scope(rows, "Counter-Strike pre-match", NOW, True, cfg, frozen)


def test_good_evidence_qualifies_and_produces_score():
    result = assess(good_rows())
    assert result["category"] == "SHARP"
    assert 0 <= result["score"] <= 100
    assert all(g["passed"] for g in result["gates"])


@pytest.mark.parametrize("clv, fees, prematch", [(None,False,False), (-.5,False,True), (.04,True,True)])
def test_price_fees_and_live_timing_do_not_gate_or_change_rank(clv, fees, prematch):
    rows = good_rows()
    for r in rows: r.update(clv=clv, feesKnown=fees, preMatchOnly=prematch)
    result = assess(rows)
    assert result["category"] == "SHARP"
    assert result["score"] == assess(good_rows())["score"]
    assert set(result["scoreComponents"]) == {"winrate", "roi", "depth"}


def test_high_winrate_with_negative_roi_is_not_sharp():
    rows = good_rows()
    for i, r in enumerate(rows): r["netPnl"] = 1 if i % 10 < 8 else -10
    result = assess(rows)
    assert result["winrate"] == .8
    assert result["roi"] < 0
    assert result["category"] not in {"SHARP", "PROVEN_SHARP"}


def test_tiny_record_cannot_qualify_despite_perfect_winrate():
    assert assess(good_rows(12))["category"] not in {"SHARP", "PROVEN_SHARP"}


def test_combined_sports_record_qualifies_across_categories_and_ignores_nonsports():
    from sharp_evidence import evaluate_records, qualified_market_scope
    rows = good_rows()
    for i,r in enumerate(rows):
        r.update(category="NBA" if i%2 else "Counter-Strike", prematchCost=0, liveCost=100, postResultCost=0, unknownTimingCost=0)
    non_sports = {**rows[0], "category":"Other", "retention":0, "retainedCost":0, "netPnl":-100000}
    auto = {"risk":"low_observed", "groups":[], "marketMakerStyle":False}
    ev = evaluate_records(rows+[non_sports], auto, config.get_config(), NOW, capped=True)
    assert ev["category"] == "SHARP"
    assert ev["scopes"]["Sports"]["events"] == 120
    assert ev["scopes"]["Sports"]["sampledHistory"]
    assert ev["holding"]["positions"] == 120
    profile = {"primary":ev["category"], "evidence":ev}
    assert qualified_market_scope(profile,"Soccer") == "Sports"
    assert qualified_market_scope(profile,"Weather") is None


def test_missing_cashflows_reduce_sample_instead_of_creating_wins():
    rows=good_rows(120)
    for r in rows[:25]: r.update(settled=False, netPnl=None)
    result=assess(rows)
    assert result["events"] == 95
    assert result["score"] is None


def test_specialist_displays_qualifying_sport_not_losing_overall_record():
    from sharp_evidence import evaluate_records, qualified_market_scope, sports_record
    rows = good_rows(240)
    for i, r in enumerate(rows):
        r.update(category="NBA" if i % 2 else "Counter-Strike", netPnl=-20 if i % 2 else 10,
            prematchCost=100, liveCost=0, postResultCost=0, unknownTimingCost=0)
    auto = {"risk":"low_observed", "groups":[], "marketMakerStyle":False}
    ev = evaluate_records(rows, auto, config.get_config(), NOW)
    assert ev["scopes"]["Sports"]["roi"] < 0
    assert ev["category"] == "SHARP"
    record = sports_record(ev)
    assert record["scope"] == "Counter-Strike"
    assert record["roi"] == .1 and record["events"] == 120
    profile = {"primary":ev["category"], "evidence":ev}
    assert qualified_market_scope(profile, "NBA") is None
    assert qualified_market_scope(profile, "Counter-Strike") == record["scope"]


@pytest.mark.parametrize("field,value", [("retention", .89), ("retainedCost", 89)])
def test_no_profit_or_score_can_bypass_required_evidence(field, value):
    rows = good_rows()
    for r in rows:
        r[field] = value
        r["netPnl"] = 100000
    result = assess(rows)
    assert result["category"] not in {"SHARP", "PROVEN_SHARP"}
    assert result["score"] is None


def test_recent_holding_failure_suspends_current_sharp():
    rows = good_rows(300, days=150)
    for r in rows[-10:]:
        r.update(retention=0, retainedCost=0)
    result = assess(rows)
    assert result["holding"]["positionRate"] > .9
    assert result["recentHolding"]["positionRate"] == .8
    assert result["category"] != "SHARP"


def test_hundred_related_markets_do_not_make_hundred_events():
    rows = good_rows()
    for r in rows: r["eventId"] = "one-match"
    result = assess(rows)
    assert result["events"] == 1
    assert result["score"] is None


def test_window_extends_only_when_needed():
    assert assess(good_rows(120, days=120))["windowDays"] == 180
    assert assess(good_rows(120, days=300))["windowDays"] == 365


def test_positive_roi_does_not_override_winrate_requirement():
    rows = good_rows()
    # 45% wins with cheap tickets can earn a positive return.
    for i, r in enumerate(rows): r["netPnl"] = 200 if i % 20 < 9 else -100
    result = assess(rows)
    assert result["category"] != "SHARP"


def test_one_winner_cannot_establish_repeatability():
    rows = good_rows()
    for r in rows: r["netPnl"] = -10
    rows[-1]["netPnl"] = 10000
    result = assess(rows)
    assert result["profit"] > 0
    assert result["profitWithoutBestEvent"] < 0
    assert result["category"] != "SHARP"


def test_sufficient_historical_record_can_be_proven_without_future_data():
    rows = good_rows(320, days=320)
    result = assess(rows)
    assert result["category"] == "PROVEN_SHARP"
    assert result["forward"]["status"] == "awaiting_baseline"


def test_future_baseline_is_diagnostic_not_a_proven_gate():
    rows = good_rows(320, days=320)
    baseline = {"asOf": NOW - 70 * DAY, "eventIds": [r["eventId"] for r in rows[:-69]],
        "category": "SHARP", "ruleVersion": RULE_VERSION, "configSignature": config.signature()}
    result = assess(rows, frozen=baseline)
    assert result["category"] == "PROVEN_SHARP"
    baseline["configSignature"] = "different"
    assert assess(rows, frozen=baseline)["category"] == "PROVEN_SHARP"


def test_partial_fill_burst_is_not_a_bot():
    trades = [dict(trade(), transactionHash="one-transaction", timestamp=NOW - 100) for _ in range(500)]
    result = automation_evidence(trades, [], config.get_config()["automation"])
    assert result["estimatedEpisodes"] == 1
    assert result["risk"] == "low_observed"


def test_two_strong_groups_are_required_for_probable_bot():
    trades = []
    for day in range(6):
        for i in range(250):
            e = trade(cid=f"m{i % 30}")
            e["timestamp"] = NOW - day * DAY - i * 2
            trades.append(e)
    result = automation_evidence(trades, [], config.get_config()["automation"])
    assert result["risk"] == "high"
    assert {g["group"] for g in result["groups"]} >= {"timing", "distribution"}


def test_rewards_alone_never_prove_automation():
    activity = [dict(type="MAKER_REBATE", usdcSize=10000, timestamp=NOW)]
    result = automation_evidence(activity, [], config.get_config()["automation"])
    assert result["risk"] == "low_observed"
    assert result["incentiveIncome"] == 10000


def test_gamma_close_time_is_not_called_exact_resolution():
    m = normalize_metadata({"conditionId": "m", "closed": True, "umaResolutionStatus": "resolved", "closedTime": "2026-01-01T00:00:00Z", "outcomePrices": '["1", "0"]'}, NOW)
    assert m["resolvedAt"] == NOW
    assert "upper bound" in m["resolutionTimeSource"]


def books():
    return [{"market": "m", "asset_id": str(i), "timestamp": (NOW - 5) * 1000,
        "bids": [{"price": ".49", "size": "10000"}], "asks": [{"price": ".51", "size": "10000"}]} for i in range(2)]


def test_benchmark_never_uses_post_start_or_stale_empty_book():
    meta = {"conditionId": "m", "tokens": ["0", "1"], "gameStartTime": NOW + 300, "rulesHash": "r"}
    cfg = config.get_config()["benchmark"]
    assert validate_books(meta, books(), NOW, cfg)["valid"]
    assert validate_books(meta, books(), NOW + 400, cfg) is None
    bad = books(); bad[0]["bids"] = []
    assert validate_books(meta, bad, NOW, cfg) is None
    bad = books(); bad[0]["timestamp"] -= 180000
    assert validate_books(meta, bad, NOW, cfg) is None


def test_baseline_is_immutable_and_enrolls_losing_wallets():
    async def run():
        db = AsyncMongoMockClient().test
        ev = {"asOf": NOW, "scopes": {"Counter-Strike": {"eventIds": ["e1"], "category": "ACTIVE_TRADER"}}, "automation": {}}
        profile = {"address": "a", "evidence": ev, "primary": "ACTIVE_TRADER", "smartScore": None, "configSignature": config.signature()}
        await record_observation(db, profile)
        ev["asOf"] += DAY
        ev["scopes"]["Counter-Strike"]["eventIds"].append("e2")
        await record_observation(db, profile)
        baseline = await baselines(db, "a")
        assert baseline["Counter-Strike"]["asOf"] == NOW
        assert baseline["Counter-Strike"]["eventIds"] == ["e1"]
        assert await db.validation_snapshots.count_documents({}) == 2
    asyncio.run(run())


def test_blank_wallet_is_unscored_without_erasing_valid_partial_results():
    client = SimpleNamespace(activity_paginated=AsyncMock(return_value=([], False)), positions_paginated=AsyncMock(return_value=([], False)), value=AsyncMock(return_value=[]))
    p = asyncio.run(analyze_wallet(client, "a"))
    assert p["smartScore"] is None
    assert p["primary"] == "INSUFFICIENT_DATA"
    assert p["stats"]["hold_ratio"] is None


def test_new_scope_can_enroll_after_empty_baseline():
    async def run():
        db = AsyncMongoMockClient().test
        ev = {"asOf": NOW, "scopes": {}, "automation": {}}
        p = {"address": "new", "evidence": ev, "primary": "INSUFFICIENT_DATA", "smartScore": None, "configSignature": config.signature()}
        await record_observation(db, p)
        ev["asOf"] += DAY
        ev["scopes"]["Counter-Strike"] = {"eventIds": ["new-event"], "category": "CONVICTION_HOLDER"}
        await record_observation(db, p)
        assert (await baselines(db, "new"))["Counter-Strike"]["asOf"] == NOW + DAY
    asyncio.run(run())


def test_post_resolution_buys_cannot_inflate_prematch_profit_evidence():
    early, late = trade(), trade(size=100, cash=90)
    late["timestamp"] = 300
    r = record([early, late], [position(size=200)], {"resolved":True, "resolvedAt":200, "gameStartTime":150, "payouts":[1,0]})
    assert r["postResultCost"] == 90
    assert not r["preMatchOnly"]


def test_unknown_sell_fees_do_not_become_known_from_buy_fees():
    buy, sell = dict(trade(), feeUsd=0), trade("SELL", size=5, cash=3)
    sell["timestamp"] = 110
    r = record([buy, sell], [position(size=95)], {"resolved":True, "resolvedAt":200, "gameStartTime":150, "payouts":[1,0], "feesEnabled":True})
    assert r["entryFeesKnown"]
    assert not r["feesKnown"]


def test_voids_are_neither_wins_nor_holding_samples():
    from classifier import compute_history, compute_category_breakdown
    perf = reconstruct_performance([trade()], [], {"m":{"resolved":True, "resolvedAt":200, "payouts":[.5,.5], "void":True}}, NOW)
    assert perf["settled_bets"] == 0
    assert perf["true_winrate"] is None
    assert perf["hold_ratio"] is None
    assert not perf["per_market"][0]["won"]
    assert compute_history(perf["per_market"])["totalRealized"] == perf["net_realized"]
    assert compute_category_breakdown(perf["per_market"]) == []



def test_automation_exclusion_applies_to_scope_labels(monkeypatch):
    import sharp_evidence
    monkeypatch.setattr(sharp_evidence, "retention_record", lambda events, positions, meta, row, as_of: row)
    monkeypatch.setattr(sharp_evidence, "automation_evidence", lambda *a: {"risk":"high", "groups":[{"reason":"timing"}, {"reason":"distribution"}], "marketMakerStyle":False})
    rows = good_rows()
    for r in rows:
        r.update(category="Counter-Strike", resolutionObserved=True, prematchCost=100, liveCost=0, postResultCost=0, unknownTimingCost=0)
    evidence = build_evidence([], [], {"per_market":rows}, {}, config.get_config(), NOW)
    assert evidence["category"] == "PROBABLE_BOT"
    assert evidence["scopes"]["Counter-Strike"]["category"] == "PROBABLE_BOT"
    assert evidence["scopes"]["Counter-Strike"]["score"] is None
    assert not evidence["qualifiedScopes"]


@pytest.mark.parametrize("price, benchmark_time, expected", [(.5,150,.1), (1,150,None), (.5,90,None), (.5,250,None)])
def test_clv_uses_only_valid_prestart_postentry_nonsettlement_reference(price, benchmark_time, expected):
    buy = dict(trade(), feeUsd=0)
    meta = {"resolved":True, "resolvedAt":500, "gameStartTime":200, "payouts":[1,0],
        "benchmark":{"valid":True, "timestamp":benchmark_time, "prices":[price,1-price], "depthUsd":1000}}
    r = record([buy], [position()], meta)
    if expected is None:
        assert r["clv"] is None
    else:
        assert r["clv"] == pytest.approx(expected)


def test_current_zero_fee_flag_cannot_establish_historical_fees():
    r = record([trade()], [position()], {"resolved":True, "resolvedAt":500, "gameStartTime":200, "feesEnabled":False, "payouts":[1,0]})
    assert not r["feesKnown"]


def test_legacy_comparator_does_not_change_new_holding_rules():
    from legacy_v2.classifier import compare
    events = [trade(cash=50), trade("SELL", size=99, cash=49.5)]
    old = compare("a", events, [position(size=1)], [])
    assert old["holdRatio"] == 1
    assert record(events, [position(size=1)])["retention"] == .01


def test_changed_rules_cannot_reuse_old_benchmark_quality_approval():
    from evidence_store import load_metadata, METADATA_SCHEMA
    import time
    async def run():
        db = AsyncMongoMockClient().test
        meta = {"conditionId":"benchmark-test", "metadataSchema":METADATA_SCHEMA, "fetchedAt":time.time(), "rulesHash":"r", "gameStartTime":200}
        await db.evidence_markets.insert_one(meta)
        await db.closing_benchmarks.insert_one({"conditionId":"benchmark-test", "rulesHash":"r", "startAt":200, "valid":True, "ruleVersion":RULE_VERSION, "configSignature":"old-config"})
        client = SimpleNamespace(market_metadata=AsyncMock())
        assert "benchmark" not in (await load_metadata(client, db, ["benchmark-test"]))["benchmark-test"]
        await db.closing_benchmarks.update_one({}, {"$set":{"configSignature":config.signature()}})
        assert (await load_metadata(client, db, ["benchmark-test"]))["benchmark-test"]["benchmark"]["valid"]
        client.market_metadata.assert_not_called()
    asyncio.run(run())

def test_global_net_loss_prevents_wallet_candidate_status():
    from sharp_evidence import evaluate_records, candidate_market_scope
    cfg = config.get_config()
    cfg["_signature"] = config.signature()
    auto = {"risk": "low_observed", "marketMakerStyle": False, "groups": [], "estimatedEpisodes": 10, "incentiveIncome": 0.0}

    dota_records = [
        {
            "conditionId": f"dota_{i}", "category": "Dota 2", "eventId": f"dota_ev_{i}",
            "settled": True, "resolutionObserved": True, "resolvedAt": NOW - i * 1000,
            "firstEntryAt": NOW - i * 1000 - 500, "invested": 100.0, "netPnl": 200.0,
            "won": True, "void": False, "prematchCost": 100.0, "liveCost": 0.0,
            "postResultCost": 0.0, "unknownTimingCost": 0.0, "eventGroupingKnown": True,
            "gameStartTime": NOW - i * 1000 - 100, "retention": 1.0, "capitalRetention": 1.0,
            "acquiredCost": 100.0, "retainedCost": 100.0,
            "cashflowClosedAt": NOW - i * 1000, "entryPrice": 0.5,
        }
        for i in range(6)
    ]
    cs_records = [
        {
            "conditionId": f"cs_{i}", "category": "Counter-Strike", "eventId": f"cs_ev_{i}",
            "settled": True, "resolutionObserved": True, "resolvedAt": NOW - i * 1000,
            "firstEntryAt": NOW - i * 1000 - 500, "invested": 500.0, "netPnl": -400.0,
            "won": False, "void": False, "prematchCost": 500.0, "liveCost": 0.0,
            "postResultCost": 0.0, "unknownTimingCost": 0.0, "eventGroupingKnown": True,
            "gameStartTime": NOW - i * 1000 - 100, "retention": 1.0, "capitalRetention": 1.0,
            "acquiredCost": 500.0, "retainedCost": 500.0,
            "cashflowClosedAt": NOW - i * 1000, "entryPrice": 0.5,
        }
        for i in range(6)
    ]
    records = dota_records + cs_records

    ev = evaluate_records(records, auto, cfg, NOW)
    assert ev["category"] == "RETAIL"
    assert "Dota 2" in ev["candidateScopes"]
    assert "Counter-Strike" not in ev["candidateScopes"]

    profile = {"primary": ev["category"], "labels": [ev["category"]], "evidence": ev}
    assert candidate_market_scope(profile, "NFL") is None
    assert candidate_market_scope(profile, "Counter-Strike") is None
    assert candidate_market_scope(profile, "Dota 2") == "Dota 2"


def test_global_net_profit_allows_wallet_candidate_status():
    from sharp_evidence import evaluate_records, candidate_market_scope
    cfg = config.get_config()
    cfg["_signature"] = config.signature()
    auto = {"risk": "low_observed", "marketMakerStyle": False, "groups": [], "estimatedEpisodes": 10, "incentiveIncome": 0.0}

    dota_records = [
        {
            "conditionId": f"dota_{i}", "category": "Dota 2", "eventId": f"dota_ev_{i}",
            "settled": True, "resolutionObserved": True, "resolvedAt": NOW - i * 1000,
            "firstEntryAt": NOW - i * 1000 - 500, "invested": 100.0, "netPnl": 400.0,
            "won": True, "void": False, "prematchCost": 100.0, "liveCost": 0.0,
            "postResultCost": 0.0, "unknownTimingCost": 0.0, "eventGroupingKnown": True,
            "gameStartTime": NOW - i * 1000 - 100, "retention": 1.0, "capitalRetention": 1.0,
            "acquiredCost": 100.0, "retainedCost": 100.0,
            "cashflowClosedAt": NOW - i * 1000, "entryPrice": 0.5,
        }
        for i in range(6)
    ]
    records = dota_records

    ev = evaluate_records(records, auto, cfg, NOW)
    assert ev["category"] == "CANDIDATE"
    assert "Sports" in ev["candidateScopes"]

    profile = {"primary": ev["category"], "labels": [ev["category"]], "evidence": ev}
    assert candidate_market_scope(profile, "NFL") == "Sports"
    assert candidate_market_scope(profile, "Dota 2") == "Sports"
