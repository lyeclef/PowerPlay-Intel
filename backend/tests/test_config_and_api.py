import asyncio
import secrets

import httpx
import pytest
from mongomock_motor import AsyncMongoMockClient

import config
import server
from classifier import WALLET_SCHEMA
from polymarket_client import UpstreamError
from unittest.mock import AsyncMock


@pytest.mark.parametrize("payload", [
    [], {"weights":None}, {"weights":{"winrate":-1}},
    {"weights":{"winrate":float("nan")}}, {"weights":{"winrate":float("inf")}},
    {"weights":{"winrate":True}}, {"weights":{"winrate":".5"}},
    {"weights":{"winrate":0, "roi":0, "depth":0}},
    {"sharp":{"minWinrate":1.1}}, {"sharp":{"minEvents":1.5}},
    {"mm":{"buyLo":.8, "buyHi":.2}}, {"unknown":{}},
])
def test_invalid_config_rejected(payload):
    with pytest.raises(ValueError):
        config.merge_config(payload)


def test_valid_weights_normalized():
    cfg = config.merge_config({"weights":{"winrate":2, "roi":1, "depth":0}})
    assert cfg["weights"] == {"winrate":2/3, "roi":1/3, "depth":0}


def test_config_cannot_be_mutated_through_getter():
    config.get_config()["sharp"]["minEvents"] = 999
    assert config.get_config()["sharp"]["minEvents"] == 100


@pytest.fixture
def api(monkeypatch):
    token = secrets.token_urlsafe(32)
    monkeypatch.setattr(server, "ADMIN_TOKEN", token)
    monkeypatch.setattr(server, "db", AsyncMongoMockClient().test_database)
    monkeypatch.setattr(server, "market_jobs", server.MarketJobs(server._run_market_job, config.signature))
    monkeypatch.setattr(server, "_market_index", {})
    server._rl_hits.clear()
    async def request(method, path, **kwargs):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://test") as client:
            return await client.request(method, path, **kwargs)
    return request, {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("method,path", [("PUT","/api/config/thresholds"),
    ("POST","/api/config/thresholds/reset"), ("POST","/api/refresh"),
    ("GET","/api/admin/heal"), ("POST","/api/admin/heal")])
def test_admin_routes_require_token(api, method, path):
    request, _ = api
    assert asyncio.run(request(method, path, json={})).status_code == 401


def test_bad_thresholds_leave_database_and_config_unchanged(api):
    request, auth = api
    async def run():
        before = config.get_config()
        response = await request("PUT", "/api/config/thresholds", headers=auth,
                                 json={"config":{"weights":{"winrate":-1}}})
        assert response.status_code == 422
        assert config.get_config() == before
        assert await server.db.settings.count_documents({}) == 0
    asyncio.run(run())


def test_threshold_change_invalidates_market_analyses(api):
    request, auth = api
    async def run():
        await server.db.markets_analysis.insert_one({"conditionId":"old", "strengthYes":99})
        response = await request("PUT", "/api/config/thresholds", headers=auth,
                                 json={"config":{"weights":{"winrate":2}}})
        assert response.status_code == 200, response.text
        assert sum(response.json()["config"]["weights"].values()) == pytest.approx(1)
        assert await server.db.markets_analysis.count_documents({}) == 0
        response = await request("POST", "/api/config/thresholds/reset", headers=auth)
        assert response.status_code == 200
        assert config.get_config() == config.DEFAULTS
    asyncio.run(run())


def test_spoofed_forwarded_header_cannot_bypass_limiter(api):
    request, _ = api
    async def run():
        statuses = []
        for i in range(61):
            response = await request("GET", "/api/wallets/invalid", headers={"X-Forwarded-For":f"203.0.113.{i}"})
            statuses.append(response.status_code)
        assert statuses[:60] == [400]*60
        assert statuses[-1] == 429
    asyncio.run(run())


def test_upstream_failure_returns_502(api, monkeypatch):
    request, _ = api
    monkeypatch.setattr(server, "get_or_classify_wallet", AsyncMock(side_effect=UpstreamError("failure")))
    response = asyncio.run(request("GET", "/api/wallets/0x" + "1"*40))
    assert response.status_code == 502
    assert "temporarily unavailable" in response.json()["detail"]


@pytest.mark.parametrize("path", ["/api/markets?limit=-1", "/api/leaderboard?limit=0", "/api/tape?limit=-1"])
def test_invalid_limits_rejected(api, path):
    assert asyncio.run(api[0]("GET", path)).status_code == 422


def test_old_schema_and_unreliable_wallets_not_ranked(api):
    request, _ = api
    async def run():
        base = dict(primary="SHARP", smartScore=90, confidence=.9, scoreStatus="qualified", configSignature=config.signature(),
                    stats={"n_closed":20, "performance_reliable":True})
        await server.db.wallets.insert_many([
            dict(base, address="old", schemaVersion=WALLET_SCHEMA-1),
            dict(base, address="new", schemaVersion=WALLET_SCHEMA),
            dict(base, address="partial", schemaVersion=WALLET_SCHEMA, scoreStatus="not_qualified",
                 stats={"n_closed":20, "performance_reliable":False}),
        ])
        response = await request("GET", "/api/leaderboard")
        assert [w["address"] for w in response.json()["wallets"]] == ["new"]
    asyncio.run(run())


def test_previous_market_analysis_schema_is_not_served(api, monkeypatch):
    request, _ = api
    monkeypatch.setattr(server, "_fetch_flat_markets", AsyncMock(return_value=[]))
    async def run():
        await server.db.markets_analysis.insert_one({"conditionId":"obsolete", "schemaVersion":1,
                                                     "updatedAt":server._now_iso()})
        response = await request("GET", "/api/markets/obsolete")
        assert response.status_code == 404
    asyncio.run(run())


def test_lifespan_cancels_workers_and_closes_clients(api, monkeypatch):
    monkeypatch.setattr(server, "client", AsyncMongoMockClient())
    monkeypatch.setattr(server.poly, "close", AsyncMock())
    async def run():
        async with server.app.router.lifespan_context(server.app):
            assert len(server._background_tasks) == 6
            assert len(server.market_jobs.workers) == 3
        assert not server._background_tasks
        assert not server.market_jobs.workers
        server.poly.close.assert_awaited_once()
    asyncio.run(run())


def test_stats_exclude_obsolete_analytics(api):
    request, _ = api
    async def run():
        await server.db.wallets.insert_one({"address":"old", "schemaVersion":9, "primary":"SHARP", "stats":{"winrate":1}})
        await server.db.markets_analysis.insert_one({"conditionId":"old", "smartCapitalYes":50000})
        response = await request("GET", "/api/stats")
        assert response.json()["trackedWallets"] == 0
        assert response.json()["sharpWallets"] == 0
        assert response.json()["smartCapital"] == 0
    asyncio.run(run())


def test_sharp_average_uses_qualifying_sports_record(api):
    request, _ = api
    async def run():
        await server.db.wallets.insert_one({"address":"sports-sharp", "schemaVersion":WALLET_SCHEMA,
            "configSignature":config.signature(), "primary":"SHARP", "scoreStatus":"qualified",
            "stats":{"winrate":.2}, "sportsRecord":{"scope":"NBA", "winrate":.7}})
        response = await request("GET", "/api/stats")
        assert response.json()["sharpWallets"] == 1
        assert response.json()["sharpAvgWinrate"] == 70
    asyncio.run(run())
