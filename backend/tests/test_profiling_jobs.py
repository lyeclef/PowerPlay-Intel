import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mongomock_motor import AsyncMongoMockClient

import analyzer
import config
import evidence_store
import server
from classifier import WALLET_SCHEMA
from market_jobs import MarketJobs


def test_opened_market_uses_reserved_lane_and_promoted_job_runs_once():
    async def run():
        started = {k:asyncio.Event() for k in ("a", "b", "opened")}
        release = asyncio.Event(); calls = []
        async def runner(market, progress):
            calls.append(market["id"]); started[market["id"]].set()
            await release.wait()
            return {"conditionId":market["id"]}
        jobs = MarketJobs(runner, lambda:"rules")
        jobs.start()
        try:
            assert jobs.status("other")["state"] == "not_started"
            for key in started:
                jobs.enqueue({"id":key})
            await asyncio.wait_for(asyncio.gather(started["a"].wait(), started["b"].wait()), 1)
            assert jobs.status("opened")["state"] == "waiting"
            for _ in range(4):
                jobs.enqueue({"id":"opened"}, foreground=True)
            await asyncio.wait_for(started["opened"].wait(), 1)
            assert jobs.status("opened")["state"] == "profiling"
            release.set()
            await asyncio.wait_for(asyncio.gather(*(q.join() for q in jobs.queues)), 1)
            assert calls.count("opened") == 1
            assert jobs.status("opened")["state"] == "ready"
        finally:
            await jobs.close()
    asyncio.run(run())


def test_all_displayed_markets_are_queued_and_secondary_markets_are_not_started(monkeypatch):
    async def run():
        jobs = MarketJobs(AsyncMock(), config.signature)
        monkeypatch.setattr(server, "market_jobs", jobs)
        monkeypatch.setattr(server, "db", AsyncMongoMockClient().test)
        events = [{"markets":[{"id":str(i), "marketType":"moneyline"},
            {"id":f"secondary-{i}", "marketType":"prop"}]} for i in range(30)]
        events.append({"markets":[{"id":"hidden-prop", "marketType":"prop"}]})
        await server._warm_events(events, "moneyline")
        assert jobs.pending_count() == 30
        assert all(e["markets"][0]["profiling"]["state"] == "waiting" for e in events[:30])
        assert all(e["markets"][1]["profiling"]["state"] == "not_started" for e in events[:30])
        assert events[-1]["markets"][0]["profiling"]["state"] == "not_started"
    asyncio.run(run())


def test_market_endpoint_returns_saved_results_without_waiting_for_wallets(monkeypatch):
    async def run():
        db = AsyncMongoMockClient().test
        runner = AsyncMock()
        jobs = MarketJobs(runner, config.signature)
        monkeypatch.setattr(server, "market_jobs", jobs)
        monkeypatch.setattr(server, "db", db)
        monkeypatch.setattr(server, "_market_index", {"m":{"id":"m"}})
        saved = {"conditionId":"m", "schemaVersion":analyzer.ANALYSIS_SCHEMA,
            "configSignature":config.signature(), "participantCount":20,
            "updatedAt":(datetime.now(timezone.utc)-timedelta(hours=2)).isoformat()}
        await db.markets_analysis.insert_one(dict(saved))
        responses = await asyncio.wait_for(asyncio.gather(*(server.market_detail("m") for _ in range(5))), .5)
        assert all(r["analysis"] == saved for r in responses)
        assert all(r["profiling"]["state"] == "waiting" for r in responses)
        assert jobs.pending_count() == 1
        assert jobs.status("m")["priority"] == "opened_market"
        runner.assert_not_called()
    asyncio.run(run())


def test_progress_keeps_fast_and_cached_wallets_visible_while_slow_wallet_runs(monkeypatch):
    async def run():
        db = AsyncMongoMockClient().test
        saved = {"address":"cached", "schemaVersion":WALLET_SCHEMA,
            "configSignature":config.signature(), "updatedAt":datetime.now(timezone.utc).isoformat(),
            "primary":"ACTIVE_TRADER", "stats":{"hold_ratio":.5}}
        await db.wallets.insert_one(saved)
        client = SimpleNamespace(holders=AsyncMock(return_value=[{"token":"yes", "holders":[
            {"proxyWallet":k,"amount":100} for k in ("cached", "fast", "slow")]}]))
        release = asyncio.Event(); partial = asyncio.Event(); frames = []; calls = []
        async def profile(client, db, address):
            calls.append(address)
            if address == "slow":
                await release.wait()
            return {"primary":"ACTIVE_TRADER", "stats":{"hold_ratio":.5}}
        async def progress(analysis, status):
            frames.append((analysis, status))
            if analysis["participantCount"] == 2 and status["pendingWallets"] == 1:
                partial.set()
        monkeypatch.setattr(analyzer, "get_or_classify_wallet", profile)
        monkeypatch.setattr(analyzer, "generate_intel", AsyncMock(return_value={}))
        task = asyncio.create_task(analyzer.analyze_market(client, db,
            {"id":"m", "tokens":["yes","no"], "prices":[.5,.5]}, on_progress=progress))
        try:
            await asyncio.wait_for(partial.wait(), 1)
            assert not task.done()
            assert frames[0][0]["participantCount"] == 1
            assert frames[0][0]["coverageDetail"]["failedWallets"] == 0
            assert "cached" not in calls
            assert await db.markets_analysis.count_documents({}) == 0
            release.set()
            result = await asyncio.wait_for(task, 1)
            assert result["participantCount"] == 3
            assert not result["isPartial"]
            assert result["pendingWallets"] == 0
            assert result["coverageDetail"]["sampledCapital"] == 150
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(run())


def test_failed_refresh_retains_labelled_saved_profile_but_old_rules_are_excluded(monkeypatch):
    async def run():
        db = AsyncMongoMockClient().test
        for address, sig in [("saved",config.signature()), ("old-rule","obsolete"), ("too-old",config.signature())]:
            await db.wallets.insert_one({"address":address, "schemaVersion":WALLET_SCHEMA,
                "configSignature":sig, "updatedAt":(datetime.now(timezone.utc)-timedelta(hours=30 if address == "too-old" else 2)).isoformat(),
                "primary":"ACTIVE_TRADER"})
        client = SimpleNamespace(holders=AsyncMock(return_value=[{"token":"yes", "holders":[
            {"proxyWallet":a,"amount":100} for a in ("saved","old-rule","too-old")]}]))
        monkeypatch.setattr(analyzer, "get_or_classify_wallet", AsyncMock(side_effect=RuntimeError("offline")))
        result = await analyzer.analyze_market(client, db,
            {"id":"m", "tokens":["yes","no"], "prices":[.5,.5]}, on_progress=AsyncMock())
        assert [w["address"] for w in result["topWallets"]] == ["saved"]
        assert result["cachedWallets"] == 1
        assert result["coverageDetail"]["failedWallets"] == 3
        assert result["isPartial"]
    asyncio.run(run())


def test_foreground_makes_room_in_bounded_queue_and_rule_change_hides_old_progress():
    async def run():
        version = {"value":"old"}
        jobs = MarketJobs(AsyncMock(), lambda:version["value"], max_pending=2)
        jobs.enqueue({"id":"a"}); jobs.enqueue({"id":"b"})
        jobs.enqueue({"id":"opened"}, foreground=True)
        assert jobs.pending_count() == 2
        assert jobs.status("a")["state"] == "not_started"
        assert jobs.status("opened")["priority"] == "opened_market"
        jobs.jobs[("old","opened")]["analysis"] = {"smartScore":99}
        version["value"] = "new"
        assert jobs.latest("opened") is None
        assert jobs.status("opened")["state"] == "not_started"
        await jobs.close()
    asyncio.run(run())


def test_shutdown_cancels_active_work_and_preserves_partial_error_results():
    async def run():
        cancelled = asyncio.Event(); started = asyncio.Event()
        async def runner(market, progress):
            await progress({"participantCount":1}, {"completedWallets":1})
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        jobs = MarketJobs(runner, lambda:"rules", timeout=.03)
        jobs.start(); jobs.enqueue({"id":"m"})
        await started.wait()
        await asyncio.wait_for(jobs.queues[0].join(), 1)
        assert jobs.status("m")["state"] == "error"
        assert jobs.latest("m")["participantCount"] == 1
        assert cancelled.is_set()
        await jobs.close()
        assert not jobs.workers
    asyncio.run(run())


def test_metadata_overlap_shares_requests_without_blocking_unrelated_wallets(monkeypatch):
    async def run():
        monkeypatch.setattr(evidence_store, "_metadata_cache", {})
        monkeypatch.setattr(evidence_store, "_metadata_inflight", {})
        monkeypatch.setattr(evidence_store, "_metadata_lock", asyncio.Lock())
        started = {k:asyncio.Event() for k in ("shared","unrelated")}
        release = asyncio.Event(); calls = []
        async def fetch(ids):
            calls.extend(ids)
            for cid in ids: started[cid].set()
            await release.wait()
            return []
        client = SimpleNamespace(market_metadata=fetch)
        first = asyncio.create_task(evidence_store.load_metadata(client, None, ["shared"]))
        await started["shared"].wait()
        second = asyncio.create_task(evidence_store.load_metadata(client, None, ["shared","unrelated"]))
        try:
            await asyncio.wait_for(started["unrelated"].wait(), 1)
            first.cancel()
            await asyncio.gather(first, return_exceptions=True)
            release.set()
            result = await asyncio.wait_for(second, 1)
            assert set(result) == {"shared","unrelated"}
            assert calls.count("shared") == 1
        finally:
            second.cancel()
            await asyncio.gather(second, return_exceptions=True)
            await evidence_store.close_metadata_tasks()
    asyncio.run(run())
