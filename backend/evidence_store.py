"""Public-source metadata, pre-event quote capture and immutable observation cohorts.

Historical last prices remain diagnostics without historical spread/depth evidence.
No reference is synthesized from settlement prices or a later information state.
"""
import asyncio
import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from pymongo import UpdateOne

from config import get_config, signature
from sharp_evidence import RULE_VERSION, num, timestamp
from polymarket_client import parse_json_field, UpstreamError

log = logging.getLogger("evidence")
_metadata_lock = asyncio.Lock()
_metadata_cache = {}
_metadata_inflight = {}
METADATA_SCHEMA = 2
COLLECTOR_STATE = {"status": "starting", "lastRun": None, "captured": 0, "errors": 0}


def normalize_metadata(raw, observed_at, previous=None):
    previous = previous or {}
    event = (raw.get("events") or [{}])[0]
    payouts = [num(p, None) for p in parse_json_field(raw.get("outcomePrices"), [])]
    settled = raw.get("closed") is True and str(raw.get("umaResolutionStatus", "")).lower() == "resolved"
    payouts_valid = len(payouts) == 2 and all(p in {0., .5, 1.} for p in payouts) and abs(sum(payouts) - 1) < 1e-6
    resolved = settled and payouts_valid
    # Gamma closedTime is not documented as the oracle settlement instant. Use
    # a recorded upper bound for holding, explicitly labelled in every record.
    first_resolved = (previous.get("firstObservedResolvedAt") or observed_at) if resolved else None
    return {"conditionId": raw["conditionId"], "eventId": str(event["id"]) if event.get("id") is not None else None,
        "eventSlug": event.get("slug"), "title": raw.get("question"),
        "gameStartTime": raw.get("gameStartTime") or raw.get("eventStartTime") or event.get("startTime"),
        "startTimeSource": "Polymarket reported start; rescheduling invalidates saved benchmark",
        "resultKnownAt": event.get("finishedTimestamp"),
        "resolved": resolved, "resolvedAt": first_resolved,
        "firstObservedResolvedAt": first_resolved, "reportedClosedAt": raw.get("closedTime"),
        "resolutionTimeSource": "first observed resolved status (upper bound), not exact oracle timestamp",
        "void": resolved and payouts == [.5, .5], "payouts": payouts if resolved else [],
        "tokens": [str(t) for t in parse_json_field(raw.get("clobTokenIds"), [])],
        "rulesHash": hashlib.sha256((raw.get("description") or "").encode()).hexdigest(),
        "feesEnabled": raw.get("feesEnabled"), "feeSchedule": raw.get("feeSchedule"),
        "feeEvidence": "Current market configuration; per-fill all-in fees require explicit evidence",
        "live": event.get("live") is True, "ended": event.get("ended") is True,
        "fetchedAt": observed_at, "metadataSchema": METADATA_SCHEMA, "source": "Gamma /markets"}


def _metadata_fresh(row, now):
    ttl = 86400 if row.get("resolved") else 300
    return now - row.get("fetchedAt", 0) < ttl


async def _fetch_metadata_batch(client, db, batch):
    now = time.time()
    try:
        raw = await asyncio.wait_for(client.market_metadata(batch), timeout=30)
    except (UpstreamError, TimeoutError):
        # Back off failed fetches without erasing previously measured resolution.
        for cid in batch:
            old = _metadata_cache.get(cid)
            if old:
                _metadata_cache[cid] = {**old, "retryAfter":now + 30}
            else:
                _metadata_cache[cid] = {"conditionId":cid, "fetchedAt":now, "unavailable":True}
        return
    found = {r.get("conditionId"):r for r in raw if r.get("conditionId") in batch}
    updates = []
    for cid in batch:
        old = _metadata_cache.get(cid)
        if cid in found:
            data = normalize_metadata(found[cid], now, old)
        elif old and old.get("resolved"):
            data = {**old, "retryAfter":now + 30}
        else:
            data = {"conditionId":cid, "fetchedAt":now, "unavailable":True}
        _metadata_cache[cid] = data
        updates.append(UpdateOne({"conditionId":cid}, {"$set":data}, upsert=True))
    if db is not None and updates:
        await db.evidence_markets.bulk_write(updates, ordered=False)


def _metadata_finished(task, batch):
    for cid in batch:
        if _metadata_inflight.get(cid) is task:
            del _metadata_inflight[cid]
    if not task.cancelled() and task.exception():
        log.warning("Metadata batch failed: %s", task.exception())


async def close_metadata_tasks():
    tasks = set(_metadata_inflight.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    _metadata_inflight.clear()


async def load_metadata(client, db, ids):
    ids = sorted(set(ids))
    if not ids or not hasattr(client, "market_metadata"):
        return {}
    now = time.time()
    missing = [cid for cid in ids if cid not in _metadata_cache or not _metadata_fresh(_metadata_cache[cid], now)]
    if missing and db is not None:
        async for row in db.evidence_markets.find({"conditionId":{"$in":missing}, "metadataSchema":METADATA_SCHEMA}, {"_id":0}):
            old = _metadata_cache.get(row["conditionId"], {})
            if row.get("fetchedAt", 0) > old.get("fetchedAt", 0):
                _metadata_cache[row["conditionId"]] = row
    # The lock protects only registration. Network/DB work for unrelated wallets
    # runs concurrently; overlapping condition IDs share the same fetch task.
    async with _metadata_lock:
        missing = [cid for cid in ids if cid not in _metadata_cache or (
            not _metadata_fresh(_metadata_cache[cid], now) and _metadata_cache[cid].get("retryAfter", 0) <= now)]
        new = [cid for cid in missing if cid not in _metadata_inflight]
        for offset in range(0, len(new), 60):
            batch = new[offset:offset + 60]
            task = asyncio.create_task(_fetch_metadata_batch(client, db, batch))
            for cid in batch:
                _metadata_inflight[cid] = task
            task.add_done_callback(lambda done, batch=batch: _metadata_finished(done, batch))
        tasks = {_metadata_inflight[cid] for cid in missing if cid in _metadata_inflight}
    await asyncio.gather(*[asyncio.shield(task) for task in tasks])
    output = {cid:dict(_metadata_cache[cid]) for cid in ids if cid in _metadata_cache}
    if db is not None:
        async for benchmark in db.closing_benchmarks.find({"conditionId":{"$in":ids}, "ruleVersion":RULE_VERSION, "configSignature":signature()}, {"_id":0}):
            m = output.get(benchmark["conditionId"])
            if m and benchmark.get("rulesHash") == m.get("rulesHash") and benchmark.get("startAt") == timestamp(m.get("gameStartTime")):
                m["benchmark"] = benchmark
    return output


def validate_books(meta, books, observed_at, cfg):
    start = timestamp(meta.get("gameStartTime"))
    if not start or meta.get("live") or meta.get("ended") or not start - cfg["maxAgeSeconds"] <= observed_at <= start - cfg["startBufferSeconds"]:
        return None
    if len(books) != 2 or len(meta.get("tokens", [])) != 2:
        return None
    prices, depths, spreads, times = [], [], [], []
    for idx, b in enumerate(books):
        if str(b.get("asset_id")) != meta["tokens"][idx] or b.get("market") != meta["conditionId"]:
            return None
        bids, asks = b.get("bids") or [], b.get("asks") or []
        if not bids or not asks:
            return None
        bid = max(num(l.get("price")) for l in bids)
        ask = min(num(l.get("price"), 1) for l in asks)
        t = num(b.get("timestamp")) / 1000
        if not 0 < bid <= ask < 1 or ask - bid > cfg["maxSpread"] or not 0 <= observed_at - t <= 120:
            return None
        buy_depth = sum(num(l.get("size")) * num(l.get("price")) for l in asks if num(l.get("price")) <= ask + .02)
        sell_depth = sum(num(l.get("size")) * num(l.get("price")) for l in bids if num(l.get("price")) >= bid - .02)
        if min(buy_depth, sell_depth) < cfg["minDepthUsd"]:
            return None
        prices.append((bid + ask) / 2)
        depths.append(min(buy_depth, sell_depth))
        spreads.append(ask - bid)
        times.append(t)
    if abs(sum(prices) - 1) > .03 or max(times) - min(times) > 60:
        return None
    # Binary coherent midpoint normalized once, with the raw mids retained.
    normalized = [p / sum(prices) for p in prices]
    return {"conditionId": meta["conditionId"], "valid": True, "timestamp": min(times),
        "observedAt": observed_at, "startAt": start, "prices": normalized, "rawMidpoints": prices,
        "spreads": spreads, "depthUsd": min(depths), "rulesHash": meta["rulesHash"],
        "source": "locally observed CLOB /book", "quality": "pre-reported-start spread/depth validated", "ruleVersion": RULE_VERSION, "configSignature": signature()}


async def capture_benchmarks(client, db, markets):
    now = time.time()
    cfg = get_config()["benchmark"]
    # Market discovery only registers IDs; no score-dependent selection.
    ids = [m["id"] for m in markets if timestamp(m.get("gameStartTime")) and now < timestamp(m["gameStartTime"]) <= now + 1200]
    metas = await load_metadata(client, db, ids)
    captured = 0
    for m in metas.values():
        start = timestamp(m.get("gameStartTime"))
        if not start or not now < start <= now + cfg["maxAgeSeconds"] or len(m.get("tokens", [])) != 2:
            continue
        try:
            books = await asyncio.gather(*[client.order_book(t) for t in m["tokens"]])
            record = validate_books(m, books, time.time(), cfg)
            if record:
                # Keep the latest valid pre-event observation only; never replace
                # it with a post-start/final price.
                await db.closing_benchmarks.update_one({"conditionId": m["conditionId"]}, {"$set": record}, upsert=True)
                captured += 1
        except UpstreamError:
            COLLECTOR_STATE["errors"] += 1
    COLLECTOR_STATE.update(status="running", lastRun=datetime.now(timezone.utc).isoformat(), captured=COLLECTOR_STATE["captured"] + captured)


async def baselines(db, address):
    if db is None:
        return {}
    doc = await db.validation_baselines.find_one({"address": address, "ruleVersion": RULE_VERSION, "configSignature": signature()}, {"_id": 0})
    return (doc or {}).get("scopes", {})


async def record_observation(db, profile):
    ev = profile["evidence"]
    scopes = {key: {"asOf": ev["asOf"], "eventIds": scope["eventIds"], "category": scope["category"],
                    "legacyClass": profile.get("previousModel", {}).get("category"),
                    "ruleVersion": RULE_VERSION, "configSignature": profile["configSignature"]} for key, scope in ev["scopes"].items()}
    key = {"address": profile["address"], "ruleVersion": RULE_VERSION, "configSignature": profile["configSignature"]}
    # Enroll every class; each newly observed scope gets its own frozen baseline.
    # An empty initial profile must not prevent later scope enrollment.
    await db.validation_baselines.update_one(key, {"$setOnInsert": {**key, "asOf": ev["asOf"], "primary": profile["primary"], "previousModel": profile.get("previousModel"), "scopes": {}}}, upsert=True)
    for scope, baseline in scopes.items():
        await db.validation_baselines.update_one({**key, f"scopes.{scope}": {"$exists": False}},
            {"$set": {f"scopes.{scope}": baseline}})
    snapshot_key = {**key, "day": int(ev["asOf"] // 86400)}
    await db.validation_snapshots.update_one(snapshot_key, {"$setOnInsert": {**snapshot_key, "asOf": ev["asOf"], "primary": profile["primary"], "score": profile["smartScore"], "scopes": ev["scopes"], "automation": ev["automation"]}}, upsert=True)


async def validation_status(db):
    current = {"ruleVersion": RULE_VERSION, "configSignature": signature()}
    baselines_count = await db.validation_baselines.count_documents(current)
    latest = await db.wallets.find({"evidence.ruleVersion": RULE_VERSION, "configSignature": signature()}, {"_id": 0, "evidence.scopes": 1, "primary": 1}).to_list(length=5000)
    future = [s["forward"] for p in latest for s in p.get("evidence", {}).get("scopes", {}).values()]
    comparison = {"currentBaselineClass": {}, "legacyBaselineClass": {}}
    for f in future:
        for field in comparison:
            label = f.get(field)
            if label is None:
                continue
            group = comparison[field].setdefault(label, {"walletScopes": 0, "futureEvents": 0, "observedFuturePnl": 0, "validClvEvents": 0, "passedScopes": 0})
            group["walletScopes"] += 1
            group["futureEvents"] += f["events"]
            group["observedFuturePnl"] += f.get("pnl") or 0
            group["validClvEvents"] += f.get("clvEvents") or 0
            group["passedScopes"] += int(f["passed"])
    return {"ruleVersion": RULE_VERSION, "configSignature": signature(), "status": "collecting prospective evidence",
        "enrolledWallets": baselines_count, "savedDailySnapshots": await db.validation_snapshots.count_documents(current),
        "validBenchmarks": await db.closing_benchmarks.count_documents({"valid": True}),
        "futureScopeSamples": sum(f["events"] for f in future), "passedFutureScopes": sum(f["passed"] for f in future),
        "collector": dict(COLLECTOR_STATE), "validatedPredictor": False, "baselineComparison": comparison,
        "comparisonNote": "Observed future results grouped by frozen baseline class. Wallet scopes may share events; these totals do not establish independent samples or superiority. Legacy comparison uses uploaded v2 default settings.",
        "limitations": ["No completed prospective validation at launch", "No verified bot ground-truth labels: false-positive/negative rates unavailable", "Sampled wallets are holders of discovered markets, not the full population", "No cross-wallet hedges, private order cancellations or copying-profit claims", "Classification uses observed sports WR/ROI; price and fee evidence do not gate it"]}
