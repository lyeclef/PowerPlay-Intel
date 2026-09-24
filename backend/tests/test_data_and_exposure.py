import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

import analyzer
import config
from ai_narrative import generate_intel
from classifier import WALLET_SCHEMA
from polymarket_client import PolymarketClient, UpstreamError


def test_failure_after_first_page_is_not_end_of_history():
    client = PolymarketClient()
    client._get = AsyncMock(side_effect=[[{}, {}], None])
    with pytest.raises(UpstreamError):
        asyncio.run(client.activity_paginated("wallet", pages=3, size=2))


def test_positions_pagination_includes_dust_and_archived():
    client = PolymarketClient()
    client._get = AsyncMock(side_effect=[[{}, {}], []])
    result = asyncio.run(client.positions_paginated("wallet", pages=3, size=2))
    assert result == ([{}, {}], False)
    assert client._get.call_args.args[2]["offset"] == 2
    assert client._get.call_args.args[2]["sizeThreshold"] == 0
    assert client._get.call_args.args[2]["includeArchived"] == "true"


@pytest.mark.parametrize("status,body", [(400, {}), (200, "not json")])
def test_http_failure_is_explicit(status, body):
    async def run():
        client = PolymarketClient()
        def respond(request):
            return httpx.Response(status, text=body) if isinstance(body, str) else httpx.Response(status, json=body)
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        try:
            with pytest.raises(UpstreamError):
                await client.activity("wallet")
        finally:
            await client.close()
    asyncio.run(run())


def test_activity_cap_is_explicit():
    client = PolymarketClient()
    client._get = AsyncMock(return_value=[{}, {}])
    assert asyncio.run(client.activity_paginated("wallet", pages=1, size=2))[1] is True


def test_holder_cap_matches_api():
    client = PolymarketClient()
    client._get = AsyncMock(return_value=[])
    asyncio.run(client.holders("m", 22))
    assert client._get.call_args.args[2]["limit"] == 20


def test_exposure_uses_holdings_not_recent_trades(monkeypatch):
    client = SimpleNamespace(holders=AsyncMock(return_value=[
        {"token":"yes", "holders":[{"proxyWallet":"a", "amount":100}]},
        {"token":"no", "holders":[{"proxyWallet":"b", "amount":100}]},
    ]), trades=AsyncMock(return_value=[{"proxyWallet":"a", "side":"SELL", "size":50000, "price":.5}]))
    monkeypatch.setattr(analyzer, "get_or_classify_wallet", AsyncMock(return_value={
        "smartScore":80, "primary":"SHARP", "stats":{"hold_ratio":.95}, "audit":{"markets":[{"conditionId":"m", "preMatchOnly":True}]}, "evidence":{"qualifiedScopes":["Counter-Strike"], "scopes":{"Counter-Strike":{"category":"SHARP", "score":80}}}}))
    monkeypatch.setattr(analyzer, "generate_intel", AsyncMock(return_value={}))
    db = SimpleNamespace(markets_analysis=SimpleNamespace(update_one=AsyncMock()))
    result = asyncio.run(analyzer.analyze_market(client, db,
        {"id":"m", "eventSlug":"cs2-test", "tokens":["yes", "no"], "prices":[.5,.5]}))
    assert result["smartCapitalYes"] == 50
    assert result["smartCapitalNo"] == 50
    assert result["strengthYes"] == result["strengthNo"] == 50
    client.trades.assert_not_called()


def test_no_holders_cannot_gain_exposure_from_sells(monkeypatch):
    client = SimpleNamespace(holders=AsyncMock(return_value=[]), trades=AsyncMock())
    monkeypatch.setattr(analyzer, "generate_intel", AsyncMock(return_value={}))
    db = SimpleNamespace(markets_analysis=SimpleNamespace(update_one=AsyncMock()))
    result = asyncio.run(analyzer.analyze_market(client, db, {"id":"m"}))
    assert result["smartCapitalYes"] == result["smartCapitalNo"] == 0
    client.trades.assert_not_called()


def test_failed_classification_does_not_write_a_valid_cache():
    db = SimpleNamespace(wallets=SimpleNamespace(find_one=AsyncMock(return_value=None), update_one=AsyncMock()))
    client = SimpleNamespace(activity_paginated=AsyncMock(side_effect=UpstreamError("failed")),
        positions_paginated=AsyncMock(return_value=([], False)), value=AsyncMock(return_value=[]))
    with pytest.raises(UpstreamError):
        asyncio.run(analyzer.get_or_classify_wallet(client, db, "wallet"))
    db.wallets.update_one.assert_not_called()


def test_current_schema_cache_avoids_fetch():
    doc = dict(address="wallet", schemaVersion=WALLET_SCHEMA, configSignature=config.signature(), updatedAt=datetime.now(timezone.utc).isoformat())
    db = SimpleNamespace(wallets=SimpleNamespace(find_one=AsyncMock(return_value=doc)))
    assert asyncio.run(analyzer.get_or_classify_wallet(object(), db, "wallet")) == doc


def test_config_change_refreshes_cached_wallet(monkeypatch):
    old = dict(address="wallet", schemaVersion=WALLET_SCHEMA,
               configSignature="old", updatedAt=datetime.now(timezone.utc).isoformat())
    db = SimpleNamespace(wallets=SimpleNamespace(find_one=AsyncMock(return_value=old), update_one=AsyncMock()))
    updated = dict(old, configSignature=config.signature())
    classify = AsyncMock(return_value=updated)
    monkeypatch.setattr(analyzer, "analyze_wallet", classify)
    result = asyncio.run(analyzer.get_or_classify_wallet(object(), db, "wallet"))
    assert result == updated
    classify.assert_awaited_once()


def test_optional_ai_fallback_without_package_or_key(monkeypatch):
    monkeypatch.delenv("EMERGENT_LLM_KEY", raising=False)
    result = asyncio.run(generate_intel({"outcomes":["Yes", "No"]}))
    assert result["verdict"]
    assert result["bullets"]


def test_metadata_fetches_closed_and_open_markets():
    client = PolymarketClient()
    client._list = AsyncMock(side_effect=[[{"conditionId": "closed"}], [{"conditionId": "open"}, {"conditionId": "unrelated"}]])
    result = asyncio.run(client.market_metadata(["closed", "open"]))
    assert {r["conditionId"] for r in result} == {"closed", "open"}
    assert {c.args[2]["closed"] for c in client._list.call_args_list} == {"true", "false"}


@pytest.mark.parametrize("pre_match, expected", [(True, 25), (False, 25)])
def test_market_signal_includes_live_entries_and_nets_opposing_shares(monkeypatch, pre_match, expected):
    client = SimpleNamespace(holders=AsyncMock(return_value=[
        {"token":"yes", "holders":[{"proxyWallet":"a", "amount":100}]},
        {"token":"no", "holders":[{"proxyWallet":"a", "amount":50}]},
    ]))
    monkeypatch.setattr(analyzer, "get_or_classify_wallet", AsyncMock(return_value={
        "primary":"SHARP", "audit":{"markets":[{"conditionId":"m", "preMatchOnly":pre_match}]},
        "evidence":{"qualifiedScopes":["Counter-Strike"], "scopes":{"Counter-Strike":{"category":"SHARP", "score":80}}}}))
    monkeypatch.setattr(analyzer, "generate_intel", AsyncMock(return_value={}))
    db = SimpleNamespace(markets_analysis=SimpleNamespace(update_one=AsyncMock()))
    result = asyncio.run(analyzer.analyze_market(client, db,
        {"id":"m", "eventSlug":"cs2-test", "tokens":["yes", "no"], "prices":[.5,.5]}))
    assert result["smartCapitalYes"] == expected
    assert result["smartCapitalNo"] == 0
    assert result["signalStatus"] == "measured"


def test_slow_wallet_does_not_block_other_market_measurements(monkeypatch):
    client = SimpleNamespace(holders=AsyncMock(return_value=[
        {"token":"yes", "holders":[{"proxyWallet":"fast", "amount":100}, {"proxyWallet":"slow", "amount":200}]}]))
    async def profile(client, db, address):
        if address == "slow":
            await asyncio.sleep(1)
        return {"primary":"ACTIVE_TRADER", "stats":{"hold_ratio":.5}}
    monkeypatch.setattr(analyzer, "get_or_classify_wallet", profile)
    monkeypatch.setattr(analyzer, "WALLET_ANALYSIS_TIMEOUT", .01)
    monkeypatch.setattr(analyzer, "generate_intel", AsyncMock(return_value={}))
    db = SimpleNamespace(markets_analysis=SimpleNamespace(update_one=AsyncMock()))
    result = asyncio.run(analyzer.analyze_market(client, db,
        {"id":"m", "tokens":["yes","no"], "prices":[.5,.5]}))
    assert [w["address"] for w in result["topWallets"]] == ["fast"]
    assert result["coverageDetail"]["failedWallets"] == 1
    assert result["coverageDetail"]["sampledCapital"] == 150


def test_dual_measurement_sharp_and_candidate_separation(monkeypatch):
    """Ensure candidate positions do not pollute strict sharp metrics, but properly
    populate the combined measurement metrics with appropriate candidate weighting."""
    client = SimpleNamespace(holders=AsyncMock(return_value=[
        {"token": "yes", "holders": [
            {"proxyWallet": "sharp_w", "amount": 100},   # Sharp on YES: $50
            {"proxyWallet": "cand_w", "amount": 40},     # Candidate on YES: $20 (half-weight $10)
        ]},
        {"token": "no", "holders": [
            {"proxyWallet": "cand_w2", "amount": 20},    # Candidate on NO: $10 (half-weight $5)
        ]},
    ]))

    async def mock_wallet(client, db, address):
        if address == "sharp_w":
            return {
                "address": address, "primary": "SHARP", "labels": ["sharp"],
                "evidence": {"qualifiedScopes": ["Counter-Strike"], "scopes": {"Counter-Strike": {"category": "SHARP", "score": 85}}},
                "audit": {"markets": []}, "stats": {"hold_ratio": 0.95},
            }
        elif address == "cand_w":
            return {
                "address": address, "primary": "CANDIDATE", "labels": ["candidate"],
                "evidence": {"qualifiedScopes": [], "scopes": {}},
                "audit": {"markets": []}, "stats": {"hold_ratio": 0.92},
            }
        else:
            return {
                "address": address, "primary": "CANDIDATE", "labels": ["candidate"],
                "evidence": {"qualifiedScopes": [], "scopes": {}},
                "audit": {"markets": []}, "stats": {"hold_ratio": 0.90},
            }

    monkeypatch.setattr(analyzer, "get_or_classify_wallet", mock_wallet)
    monkeypatch.setattr(analyzer, "generate_intel", AsyncMock(return_value={}))
    db = SimpleNamespace(markets_analysis=SimpleNamespace(update_one=AsyncMock()))
    result = asyncio.run(analyzer.analyze_market(client, db,
        {"id": "m", "eventSlug": "cs2-test", "tokens": ["yes", "no"], "prices": [0.5, 0.5]}))

    # 1. Sharps Only (strict gold standard):
    assert result["sharpCount"] == 1
    assert result["candidateCount"] == 2
    assert result["smartCapitalYes"] == 50.0  # 100 * 0.5
    assert result["smartCapitalNo"] == 0.0
    assert result["strengthYes"] == 100.0
    assert result["strengthNo"] == 0.0
    assert result["leanSide"] == "YES"
    assert result["netLean"] == 100.0

    # 2. Combined (Sharps + Candidates):
    comb = result["combined"]
    assert comb["sharpCount"] == 1
    assert comb["candidateCount"] == 2
    assert comb["smartCapitalYes"] == 70.0    # 50 + 20
    assert comb["smartCapitalNo"] == 10.0     # 10
    # Weighted calculation:
    # sharp_w score = 85 -> q = 0.85. Sharp YES = 50 * 0.85 = 42.5.
    # cand_w YES = 20 * 0.50 = 10.0. Total comb YES = 52.5.
    # cand_w2 NO = 10 * 0.50 = 5.0. Total comb NO = 5.0.
    # denom = 52.5 + 5.0 = 57.5.
    # strengthYes = round((52.5 / 57.5) * 100, 1) = 91.3
    # strengthNo = round((5.0 / 57.5) * 100, 1) = 8.7
    assert comb["strengthYes"] == 91.3
    assert comb["strengthNo"] == 8.7
    assert comb["leanSide"] == "YES"
    assert comb["netLean"] == 82.6  # round(91.3 - 8.7, 1)


def test_net_smart_lean_switches_sides_creates_conflict_pick(monkeypatch):
    client = SimpleNamespace(holders=AsyncMock(return_value=[
        {"token": "yes", "holders": [{"proxyWallet": "sharp_w", "amount": 100}]},
        {"token": "no", "holders": [{"proxyWallet": "cand_w", "amount": 400}]},
    ]))
    async def profile(client, db, address):
        if address == "sharp_w":
            return {
                "primary": "SHARP",
                "audit": {"markets": [{"conditionId": "m", "preMatchOnly": True}]},
                "evidence": {"qualifiedScopes": ["Counter-Strike"], "scopes": {"Counter-Strike": {"category": "SHARP", "score": 85}}}
            }
        else:
            return {
                "primary": "CANDIDATE",
                "is_candidate": True,
                "stats": {"hold_ratio": 0.95},
                "evidence": {"category": "CANDIDATE"}
            }
    monkeypatch.setattr(analyzer, "get_or_classify_wallet", profile)
    monkeypatch.setattr(analyzer, "generate_intel", AsyncMock(return_value={}))
    db = SimpleNamespace(markets_analysis=SimpleNamespace(update_one=AsyncMock()))
    result = asyncio.run(analyzer.analyze_market(client, db,
        {"id": "m", "eventSlug": "cs2-test", "tokens": ["yes", "no"], "prices": [0.5, 0.5]}))

    assert result["leanSide"] == "YES"
    assert result["combined"]["leanSide"] == "NO"
    assert result["sharpPick"]["isConflict"] is True
    assert result["sharpPick"]["conviction"] == "CONFLICT"
    assert result["combined"]["pick"]["isConflict"] is True
    assert result["combined"]["pick"]["conviction"] == "CONFLICT"
    assert "SPLIT CONSENSUS" in result["combined"]["pick"]["verdict"]




def test_candidate_in_dota_is_not_counted_in_nfl_market(monkeypatch):
    """Verify that a wallet with candidateScopes=['Dota 2'] is recognized as a Candidate
    in Dota 2 markets, but completely excluded as Candidate in NFL markets."""
    client = SimpleNamespace(holders=AsyncMock(return_value=[
        {"token": "yes", "holders": [{"proxyWallet": "dota_cand_wallet", "amount": 200}]},
        {"token": "no", "holders": []},
    ]))

    async def mock_wallet(client, db, address):
        return {
            "address": address, "primary": "RETAIL", "labels": ["RETAIL", "WHALE"],
            "evidence": {
                "category": "RETAIL",
                "candidateScopes": ["Dota 2"],
                "qualifiedScopes": [],
                "bestScopeKey": "Dota 2",
                "scopes": {
                    "Sports": {"category": "RETAIL", "profit": -500000},
                    "Dota 2": {"category": "CANDIDATE", "profit": 300000},
                    "NFL": {"category": "RETAIL", "profit": -400000},
                }
            },
            "stats": {"hold_ratio": 0.95, "capital_hold_ratio": 0.95},
        }

    monkeypatch.setattr(analyzer, "get_or_classify_wallet", mock_wallet)
    monkeypatch.setattr(analyzer, "generate_intel", AsyncMock(return_value={}))
    db = SimpleNamespace(markets_analysis=SimpleNamespace(update_one=AsyncMock()))

    # 1. Analyze NFL Market:
    nfl_res = asyncio.run(analyzer.analyze_market(client, db,
        {"id": "m_nfl", "eventSlug": "nfl-falcons-packers", "tokens": ["yes", "no"], "prices": [0.5, 0.5]}))
    assert nfl_res["candidateCount"] == 0
    assert nfl_res["tailIntelligence"]["candidateCountYes"] == 0
    assert nfl_res["topWallets"][0]["isCandidate"] is False

    # 2. Analyze Dota 2 Market:
    dota_res = asyncio.run(analyzer.analyze_market(client, db,
        {"id": "m_dota", "eventSlug": "dota2-navi-spirit", "tokens": ["yes", "no"], "prices": [0.5, 0.5]}))
    assert dota_res["candidateCount"] == 1
    assert dota_res["tailIntelligence"]["candidateCountYes"] == 1
    assert dota_res["topWallets"][0]["isCandidate"] is True
