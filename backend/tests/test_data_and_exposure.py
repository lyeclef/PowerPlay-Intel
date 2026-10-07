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
    client._get = AsyncMock(return_value=[{"timestamp": 100}, {"timestamp": 99}])
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
