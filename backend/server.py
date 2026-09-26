import asyncio
import hmac
import logging
import os
import re
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import UpdateOne
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

import config as tuning_config
from polymarket_client import (
    CATEGORY_BY_ID,
    CATEGORY_DEFS,
    MARKET_TYPES,
    PolymarketClient,
    UpstreamError,
    group_by_event,
    normalize_markets_from_events,
    pick_display_market,
)
from classifier import classify, smart_score, reconstruct_performance, WALLET_SCHEMA, _reliability
from sharp_evidence import RULE_VERSION, QUALIFIED, qualified_market_scope
from evidence_store import capture_benchmarks, validation_status, COLLECTOR_STATE
from classifier import categorize_market
from analyzer import analyze_market, analysis_summary, get_or_classify_wallet, _fresh, ANALYSIS_SCHEMA, compute_pick_from_doc
from market_jobs import MarketJobs

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("server")
logging.getLogger("httpx").setLevel(logging.WARNING)

client = AsyncIOMotorClient(os.environ["MONGO_URL"])
db = client[os.environ["DB_NAME"]]
poly = PolymarketClient()

@asynccontextmanager
async def lifespan(app):
    await _startup()
    try:
        yield
    finally:
        await _shutdown()


app = FastAPI(title="SharpMarket Terminal API", lifespan=lifespan)
_background_tasks = set()


def _spawn(coro):
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    def finished(done):
        _background_tasks.discard(done)
        if not done.cancelled() and done.exception():
            logger.warning("Background task failed: %s", done.exception())
    task.add_done_callback(finished)
    return task


@app.exception_handler(UpstreamError)
async def upstream_error_handler(request, exc):
    logger.warning("Upstream data unavailable: %s", exc)
    return JSONResponse(status_code=502, content={"detail": "Market data is temporarily unavailable. Please retry."})


api_router = APIRouter(prefix="/api")

MARKETS_CACHE_TTL = 30
ANALYSIS_TTL = 300
_markets_cache: dict[str, dict] = {}
async def _run_market_job(market, progress):
    return await analyze_market(poly, db, market, on_progress=progress)


market_jobs = MarketJobs(_run_market_job, tuning_config.signature)
_market_index: dict[str, dict] = {}

TAPE_MAX = 160
TAPE_REFRESH = 45
HEAL_INTERVAL = 600  # background top-up cadence (s) to keep the leaderboard on the latest schema
_tape: list[dict] = []
_tape_updated: str | None = None
_tape_lock = asyncio.Lock()


def _to_f(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


# ---------------- SECURITY ----------------
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
_bearer = HTTPBearer(auto_error=False)


def require_admin(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)):
    """Gate state-changing admin routes behind a shared bearer token (constant-time compare)."""
    if not ADMIN_TOKEN:
        raise HTTPException(status_code=503, detail="Admin controls are not configured")
    ok = (
        credentials is not None
        and credentials.scheme.lower() == "bearer"
        and hmac.compare_digest(credentials.credentials, ADMIN_TOKEN)
    )
    if not ok:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing admin token",
            headers={"WWW-Authenticate": "Bearer"},
        )


_ADDR_RE = re.compile(r"^0x[0-9a-f]{40}$")


def _valid_address(address: str) -> str:
    address = (address or "").strip().lower()
    if not _ADDR_RE.match(address):
        raise HTTPException(status_code=400, detail="Invalid wallet address")
    return address


_rl_hits: dict[str, deque] = defaultdict(deque)
_rl_last_cleanup = 0.0


def _client_ip(request: Request) -> str:
    # Proxy trust belongs to the ASGI server's forwarded-allow-ips setting.
    # Arbitrary request headers must never change the limiter identity here.
    return request.client.host if request.client else "unknown"


def rate_limit(bucket: str, max_hits: int, window: int):
    """Per-IP sliding-window limiter to bound costly outbound/DB work (429 when exceeded)."""

    async def _dep(request: Request):
        global _rl_last_cleanup
        key = f"{bucket}:{_client_ip(request)}"
        now = time.monotonic()
        if now - _rl_last_cleanup >= 60 or len(_rl_hits) >= 10000:
            for old_key, hits in list(_rl_hits.items()):
                if not hits or hits[-1] <= now - 60:
                    del _rl_hits[old_key]
            _rl_last_cleanup = now
        if key not in _rl_hits and len(_rl_hits) >= 10000:
            raise HTTPException(status_code=429, detail="Rate limit capacity reached; retry later")
        dq = _rl_hits[key]
        while dq and dq[0] < now - window:
            dq.popleft()
        if len(dq) >= max_hits:
            raise HTTPException(
                status_code=429, detail="Rate limit exceeded — slow down and retry shortly"
            )
        dq.append(now)

    return _dep


async def _fetch_flat_markets(category_id: str, n_events: int) -> list:
    cat = CATEGORY_BY_ID.get(category_id)
    if not cat:
        raise HTTPException(status_code=404, detail="Unknown category")
    entry = _markets_cache.get(category_id)
    if entry and (time.time() - entry["ts"]) < MARKETS_CACHE_TTL:
        markets = entry["markets"]
    else:
        events = await poly.events(cat["tag_slug"], limit=max(n_events, 40))
        markets = normalize_markets_from_events(events, category_id)
        _markets_cache[category_id] = {"ts": time.time(), "markets": markets}
    for m in markets:
        _market_index[m["id"]] = m
    return markets


def _analysis_fresh(doc):
    if not doc:
        return False
    ttl = 60 if doc.get("isPartial") or doc.get("cachedWallets") else ANALYSIS_TTL
    return _fresh(doc.get("updatedAt"), ttl)


def _available_analysis(cond, cached):
    latest = market_jobs.latest(cond)
    res = latest if (latest and (latest.get("participantCount") or not latest.get("pendingWallets") or not cached)) else cached
    if res and res.get("topWallets"):
        if not res.get("sharpPick"):
            res["sharpPick"] = compute_pick_from_doc(res, mode="sharp")
        if not res.get("pick"):
            res["pick"] = res.get("sharpPick") or compute_pick_from_doc(res, mode="combined")
        if res.get("combined") and not res["combined"].get("pick"):
            res["combined"]["pick"] = compute_pick_from_doc(res, mode="combined")
    return res


async def _attach_analyses(events: list):
    conds = [m["id"] for ev in events for m in ev["markets"]]
    analyses = {}
    if conds:
        async for doc in db.markets_analysis.find({"conditionId":{"$in":conds},
                "schemaVersion":ANALYSIS_SCHEMA, "configSignature":tuning_config.signature()}, {"_id":0}):
            analyses[doc["conditionId"]] = doc
    for ev in events:
        for m in ev["markets"]:
            doc = _available_analysis(m["id"], analyses.get(m["id"]))
            m["analysis"] = analysis_summary(doc)
            m["profiling"] = market_jobs.status(m["id"], doc)


async def _warm_events(events: list, market_type: str | None, cap=None):
    # Queue every displayed event, not just the first six. Secondary submarkets
    # remain explicitly "not started" until selected.
    for ev in events if cap is None else events[:cap]:
        display = pick_display_market(ev, market_type)
        if display is not None and (market_type is None or display.get("marketType") == market_type):
            await _enqueue_analysis(display)
    for ev in events:
        for m in ev["markets"]:
            m["profiling"] = market_jobs.status(m["id"], m.get("analysis"))


async def _enqueue_analysis(market: dict, foreground=False, retry=False):
    cond = market["id"]
    _market_index[cond] = market
    existing = market.get("analysis")
    if existing is None:
        existing = await db.markets_analysis.find_one({"conditionId":cond,
            "schemaVersion":ANALYSIS_SCHEMA, "configSignature":tuning_config.signature()}, {"_id":0})
    compatible = existing and existing.get("schemaVersion") == ANALYSIS_SCHEMA and existing.get("configSignature") == tuning_config.signature()
    if retry or not compatible or not _analysis_fresh(existing):
        market_jobs.enqueue(market, foreground=foreground, retry=retry)


async def _refresh_loop():
    await asyncio.sleep(3)
    while True:
        try:
            for cat in CATEGORY_DEFS:
                if cat["id"] not in ("esports", "all"):
                    continue
                try:
                    flat = await _fetch_flat_markets(cat["id"], 30)
                except Exception:  # noqa: BLE001
                    continue
                events = group_by_event(flat)[:3]
                await _warm_events(events, "moneyline", cap=3)
                await asyncio.sleep(1)
        except Exception as exc:  # noqa: BLE001
            logger.warning("refresh loop error: %s", exc)
        await asyncio.sleep(20 * 60)


async def _build_tape():
    """Poll recent trades across tracked esports+sports markets and tag each with the
    trader's cached classification to surface a live smart-money trade tape."""
    global _tape, _tape_updated
    cond_markets: dict[str, dict] = {}
    for cat in ("esports", "all", "soccer", "nba"):
        try:
            flat = await _fetch_flat_markets(cat, 30)
        except Exception:  # noqa: BLE001
            continue
        for m in flat[:6]:
            cond_markets.setdefault(m["id"], m)
    conds = list(cond_markets.keys())[:16]
    if not conds:
        return

    results = await asyncio.gather(
        *[poly.trades(c, 25) for c in conds], return_exceptions=True
    )
    rows = []
    for c, res in zip(conds, results):
        if isinstance(res, Exception) or not res:
            continue
        m = cond_markets[c]
        for t in res:
            rows.append((m, t))
    rows.sort(key=lambda mt: _to_f(mt[1].get("timestamp")), reverse=True)
    rows = rows[:TAPE_MAX]

    addrs = list({(t.get("proxyWallet") or "").lower() for _, t in rows if t.get("proxyWallet")})
    wallet_map: dict[str, dict] = {}
    if addrs:
        async for w in db.wallets.find(
            {"address": {"$in": addrs}, "schemaVersion": WALLET_SCHEMA, "configSignature": tuning_config.signature()},
            {"_id": 0, "address": 1, "labels": 1, "primary": 1, "smartScore": 1, "name": 1, "pseudonym": 1, "evidence.qualifiedScopes": 1, "evidence.scopes": 1},
        ):
            wallet_map[w["address"]] = w

    tape = []
    enqueue = []
    for m, t in rows:
        try:
            addr = (t.get("proxyWallet") or "").lower()
            size = _to_f(t.get("size"))
            price = _to_f(t.get("price"))
            usd = size * price
            prof = wallet_map.get(addr)
            labels = (prof or {}).get("labels") or []
            score = (prof or {}).get("smartScore")
            primary = (prof or {}).get("primary")
            is_sharp = bool(
                prof
                and (primary in QUALIFIED or bool(set(labels or []) & QUALIFIED))
                and not (prof or {}).get("isBot", False)
            )

            prices = m.get("prices") or []
            tokens = m.get("tokens") or []
            cur_yes = 0.5
            cur_no = 0.5
            if prices and len(prices) > 0:
                cur_yes = _to_f(prices[0])
                cur_no = _to_f(prices[1]) if len(prices) > 1 else max(0.0, min(1.0, 1.0 - cur_yes))
            elif tokens and len(tokens) > 0 and isinstance(tokens[0], dict):
                cur_yes = _to_f(tokens[0].get("price"))
                cur_no = _to_f(tokens[1].get("price")) if len(tokens) > 1 else max(0.0, min(1.0, 1.0 - cur_yes))

            outcome_str = str(t.get("outcome") or "").lower()
            idx = t.get("outcomeIndex")
            is_no = idx == 1 or outcome_str in ("no", "1")
            cur_price = cur_no if is_no else cur_yes
            slip = cur_price - price
            slip_cents = round(slip * 100.0, 1)

            if slip <= 0.0:
                tail_status = "BETTER_PRICE"
            elif slip <= 0.03:
                tail_status = "PRIME_TAIL"
            elif slip <= 0.07:
                tail_status = "ACCEPTABLE"
            else:
                tail_status = "LINE_MOVED"

            tape.append(
                {
                    "id": f"{t.get('transactionHash', '')}-{t.get('asset', '')}-{t.get('side', '')}",
                    "timestamp": int(_to_f(t.get("timestamp"))),
                    "side": t.get("side"),
                    "outcome": t.get("outcome"),
                    "outcomeIndex": t.get("outcomeIndex", 0),
                    "price": round(price, 4),
                    "currentPrice": round(cur_price, 4),
                    "slippageCents": slip_cents,
                    "tailStatus": tail_status,
                    "sizeUsd": round(usd, 2),
                    "shares": round(size, 2),
                    "conditionId": m.get("id", ""),
                    "question": m.get("question") or t.get("title"),
                    "eventTitle": m.get("eventTitle"),
                    "marketType": m.get("marketType"),
                    "category": m.get("category"),
                    "icon": t.get("icon") or m.get("icon"),
                    "address": addr,
                    "name": (prof or {}).get("name") or t.get("name"),
                    "pseudonym": (prof or {}).get("pseudonym") or t.get("pseudonym"),
                    "labels": labels,
                    "primary": (prof or {}).get("primary"),
                    "smartScore": score,
                    "sharp": is_sharp,
                    "classified": prof is not None,
                }
            )
            if prof is None and usd >= 400 and addr:
                enqueue.append(addr)
        except Exception as row_exc:
            logger.debug("Failed to process tape row: %s", row_exc)
            continue

    for addr in list(dict.fromkeys(enqueue))[:2]:
        _spawn(get_or_classify_wallet(poly, db, addr))

    _tape = tape
    _tape_updated = _now_iso()


async def _tape_loop():
    await asyncio.sleep(5)
    while True:
        try:
            async with _tape_lock:
                await _build_tape()
        except Exception as exc:  # noqa: BLE001
            logger.warning("tape loop error: %s", exc)
        await asyncio.sleep(TAPE_REFRESH)


_heal_state = {"running": False, "healed": 0, "target": 0, "startedAt": None}


async def _heal_wallets(limit=2000, batch=10, pause=0.5):
    """Re-analyze stale cached wallets (highest-score first) to the honest full-ledger
    classifier so the leaderboard stays consistent. Single-flight + idempotent."""
    if _heal_state["running"]:
        return {"skipped": True, "reason": "already running"}
    try:
        cursor = (
            db.wallets.find({"$or": [{"schemaVersion": {"$ne": WALLET_SCHEMA}}, {"configSignature": {"$ne": tuning_config.signature()}}]}, {"_id": 0, "address": 1})
            .sort("smartScore", -1)
            .limit(limit)
        )
        addrs = [w["address"] async for w in cursor if w.get("address")]
    except Exception as exc:  # noqa: BLE001
        logger.warning("wallet heal query failed: %s", exc)
        return {"error": str(exc)}
    _heal_state.update(
        {"running": True, "healed": 0, "target": len(addrs), "startedAt": _now_iso()}
    )
    logger.info("healing %d stale wallets to schema v%d", len(addrs), WALLET_SCHEMA)
    try:
        for i in range(0, len(addrs), batch):
            await asyncio.gather(
                *[get_or_classify_wallet(poly, db, a) for a in addrs[i : i + batch]],
                return_exceptions=True,
            )
            _heal_state["healed"] = min(i + batch, len(addrs))
            await asyncio.sleep(pause)
        logger.info("wallet heal complete (%d)", len(addrs))
        return {"healed": len(addrs)}
    finally:
        _heal_state["running"] = False


async def _migrate_stale_wallets():
    await asyncio.sleep(10)
    try:
        await _heal_wallets()
    except Exception as exc:  # noqa: BLE001
        logger.warning("startup heal failed: %s", exc)


async def _heal_loop():
    """Keep the leaderboard consistent over time — periodically top up any wallets that fell
    behind the current schema (e.g. discovered by market analysis before this deploy)."""
    while True:
        await asyncio.sleep(HEAL_INTERVAL)
        try:
            stale = await db.wallets.count_documents({"$or": [{"schemaVersion": {"$ne": WALLET_SCHEMA}}, {"configSignature": {"$ne": tuning_config.signature()}}]})
            if stale and not _heal_state["running"]:
                await _heal_wallets(limit=300)
        except Exception as exc:  # noqa: BLE001
            logger.warning("heal loop error: %s", exc)


async def _reclassify_all():
    global _tape, _tape_updated
    _tape, _tape_updated = [], None
    await market_jobs.close()
    market_jobs.start()
    count = await db.wallets.count_documents({})
    _spawn(_heal_wallets(limit=2000, batch=4, pause=.5))
    return {"reclassified": 0, "queued": count, "distribution": {},
            "note": "Rebuilding qualification with the new rules; obsolete profiles are excluded meanwhile."}


async def _benchmark_loop():
    while True:
        try:
            await capture_benchmarks(poly, db, list(_market_index.values()))
        except Exception as exc:
            COLLECTOR_STATE["status"] = "retrying"
            logger.warning("benchmark collection failed: %s", exc)
        await asyncio.sleep(tuning_config.get_config()["benchmark"]["pollSeconds"])


async def _forward_loop():
    # Revisit all previously enrolled styles, rather than only today's winners.
    while True:
        await asyncio.sleep(600)
        try:
            enrolled = await db.wallets.find({}, {"address": 1}).sort("updatedAt", 1).limit(20).to_list(length=20)
            for w in enrolled:
                await get_or_classify_wallet(poly, db, w["address"])
        except Exception as exc:
            logger.warning("forward observation refresh failed: %s", exc)


# ---------------- ROUTES ----------------
@api_router.get("/")
async def root():
    return {"service": "SharpMarket Terminal", "status": "live"}


@api_router.get("/categories")
async def categories():
    return {"categories": CATEGORY_DEFS}


@api_router.get("/market-types")
async def market_types():
    return {"types": MARKET_TYPES}


@api_router.get("/markets")
async def markets(
    category: str = "esports",
    type: str | None = None,
    limit: int = Query(24, ge=1, le=60),
):
    flat = await _fetch_flat_markets(category, max(limit, 30))
    events = group_by_event(flat)[:limit]
    await _attach_analyses(events)
    await _warm_events(events, type, cap=6)
    return {"category": category, "type": type, "count": len(events), "events": events}


@api_router.get("/search")
async def search(q: str = Query(..., max_length=200), limit: int = Query(20, ge=1, le=40)):
    q = (q or "").strip()
    if len(q) < 2:
        return {"query": q, "events": []}
    raw = await poly.search_events(q, limit=100)
    allowed = {c["tag_slug"] for c in CATEGORY_DEFS} | {
        "sports", "esports", "games", "football", "college-football", "ncaaf",
        "basketball", "baseball", "soccer", "tennis", "fighting", "boxing", "hockey",
        "cfb", "cfb-gameday", "nfl", "nba", "mlb", "mma", "ufc"
    }
    sports_slug_prefixes = ("cfb-", "nfl-", "nba-", "mlb-", "cbb-", "epl-", "ucl-", "atp-", "wta-", "ufc-", "cs2-", "dota-", "lol-", "val-")
    scoped = []
    for e in raw:
        if e.get("closed") is True:
            continue
        tags = {t.get("slug") if isinstance(t, dict) else str(t) for t in (e.get("tags") or [])}
        is_sports = bool(tags & allowed) or any(str(e.get("slug") or "").lower().startswith(p) for p in sports_slug_prefixes)
        if not is_sports and tags:
            continue
        scoped.append(e)

    flat = normalize_markets_from_events(scoped, "search")

    # Also search local memory cache so active live markets already loaded are never missed
    q_lower = q.lower()
    existing_conds = {m["id"] for m in flat}
    for cat_id, entry in _markets_cache.items():
        for m in entry.get("markets", []):
            if m["id"] in existing_conds:
                continue
            title = (m.get("eventTitle") or "").lower()
            question = (m.get("question") or "").lower()
            slug = (m.get("slug") or "").lower()
            if q_lower in title or q_lower in question or q_lower in slug:
                flat.append(m)
                existing_conds.add(m["id"])

    for m in flat:
        _market_index[m["id"]] = m

    events = group_by_event(flat)

    # Rank events by relevance to query (exact word match in title > title substring > question match)
    def _search_rank(ev):
        title = (ev.get("title") or "").lower()
        if re.search(r"\b" + re.escape(q_lower) + r"\b", title):
            return (0, -ev.get("volume", 0))
        if q_lower in title:
            return (1, -ev.get("volume", 0))
        if any(q_lower in (m.get("question") or "").lower() for m in ev.get("markets", [])):
            return (2, -ev.get("volume", 0))
        return (3, -ev.get("volume", 0))

    events.sort(key=_search_rank)
    events = events[:limit]

    await _attach_analyses(events)
    await _warm_events(events, None, cap=4)
    return {"query": q, "count": len(events), "events": events}


@api_router.get("/markets/{condition_id}", dependencies=[Depends(rate_limit("markets", 60, 60))])
async def market_detail(condition_id: str, retry: bool = False):
    doc = await db.markets_analysis.find_one({"conditionId":condition_id,
        "schemaVersion":ANALYSIS_SCHEMA, "configSignature":tuning_config.signature()}, {"_id":0})
    market = _market_index.get(condition_id)
    if market is None and doc is None:
        for cat in ("esports", "all"):
            await _fetch_flat_markets(cat, 40)
            market = _market_index.get(condition_id)
            if market:
                break
    if market is None:
        if doc:
            return {"market":None, "analysis":doc, "profiling":market_jobs.status(condition_id, doc)}
        raise HTTPException(status_code=404, detail="Market not found or not tradeable")
    if retry or not _analysis_fresh(doc):
        market_jobs.enqueue(market, foreground=True, retry=retry)
    available = _available_analysis(condition_id, doc)
    return {"market":market, "analysis":available, "profiling":market_jobs.status(condition_id, available)}


@api_router.get("/wallets/{address}", dependencies=[Depends(rate_limit("wallets", 60, 60))])
async def wallet_profile(address: str):
    address = _valid_address(address)
    return await get_or_classify_wallet(poly, db, address)


@api_router.get(
    "/wallets/{address}/audit", dependencies=[Depends(rate_limit("wallets", 60, 60))]
)
async def wallet_audit(address: str, limit: int = Query(60, ge=1, le=150)):
    address = _valid_address(address)
    profile = await get_or_classify_wallet(poly, db, address)
    audit = profile.get("audit", {})
    return {"address": address, "reliability": profile.get("reliability"),
            "evidence": profile.get("evidence"), "asOf": profile.get("updatedAt"),
            "issues": audit.get("issues", []), "performance": audit.get("performance", {}),
            "markets": audit.get("markets", [])[:150], "trades": audit.get("trades", [])[:limit]}


@api_router.get("/leaderboard")
async def leaderboard(limit: int = Query(60, ge=1, le=100), min_score: float = Query(0.0, ge=0, le=100), view: str = Query("sharp", pattern="^(sharp|proven|candidate|all)$")):
    query = {"schemaVersion": WALLET_SCHEMA, "configSignature": tuning_config.signature()}
    if view in {"sharp", "proven"}:
        query.update({"primary": {"$in": ["PROVEN_SHARP"] if view == "proven" else list(QUALIFIED)}, "scoreStatus": "qualified", "smartScore": {"$gte": min_score}})
    elif view == "candidate":
        query["primary"] = "CANDIDATE"
    wallets = await db.wallets.find(query, {"_id": 0, "audit": 0}).sort([("smartScore", -1), ("stats.settled_bets", -1)]).limit(limit).to_list(length=limit)
    return {"count": len(wallets), "wallets": wallets, "view": view,
            "note": "Only qualified, scope-specific Sharps receive a ranking score. Candidates and all-wallet views retain measured metrics."}


@api_router.get("/validation")
async def validation():
    return await validation_status(db)


@api_router.get("/stats")
async def stats():
    current = {"schemaVersion": WALLET_SCHEMA, "configSignature": tuning_config.signature()}
    sharp_filter = {**current, "primary": {"$in": list(QUALIFIED)}, "scoreStatus": "qualified"}
    tracked = await db.wallets.count_documents(current)
    sharp = await db.wallets.count_documents(sharp_filter)
    cat_counts = {}
    async for row in db.wallets.aggregate([{"$match": current}, {"$group": {"_id": "$primary", "n": {"$sum": 1}}}]):
        if row["_id"]:
            cat_counts[row["_id"]] = row["n"]

    analyses = await db.markets_analysis.find(
        {"schemaVersion": ANALYSIS_SCHEMA, "configSignature": tuning_config.signature()}, {"_id": 0, "netLean": 1, "smartCapitalYes": 1, "smartCapitalNo": 1}
    ).to_list(length=500)
    analyzed = len(analyses)
    yes_lean = sum(1 for a in analyses if (a.get("netLean") or 0) > 0)
    smart_capital = sum(
        (a.get("smartCapitalYes") or 0) + (a.get("smartCapitalNo") or 0) for a in analyses
    )

    avg_winrate = None
    agg = await db.wallets.aggregate(
        [
            {"$match": sharp_filter},
            {"$group": {"_id": None, "wr": {"$avg": "$sportsRecord.winrate"}}},
        ]
    ).to_list(length=1)
    if agg and agg[0].get("wr") is not None:
        avg_winrate = round(agg[0]["wr"] * 100, 1)

    return {
        "trackedWallets": tracked,
        "sharpWallets": sharp,
        "categoryCounts": cat_counts,
        "analyzedMarkets": analyzed,
        "smartCapital": round(smart_capital, 2),
        "yesLeanPct": round(yes_lean / sum(a.get("netLean") is not None for a in analyses) * 100, 1) if any(a.get("netLean") is not None for a in analyses) else None,
        "sharpAvgWinrate": avg_winrate,
        "updatedAt": _now_iso(),
    }


@api_router.post("/refresh", dependencies=[Depends(require_admin)])
async def refresh(category: str = "esports", limit: int = Query(10, ge=1, le=60)):
    flat = await _fetch_flat_markets(category, 30)
    events = group_by_event(flat)[:limit]
    await _warm_events(events, "moneyline", cap=limit)
    return {"queued": len(events), "queueSize": market_jobs.pending_count()}


async def _heal_counts():
    total = await db.wallets.count_documents({})
    fresh = await db.wallets.count_documents({"schemaVersion": WALLET_SCHEMA})
    return {"total": total, "fresh": fresh, "stale": total - fresh, "schema": WALLET_SCHEMA}


@api_router.get("/admin/heal", dependencies=[Depends(require_admin)])
async def heal_status():
    counts = await _heal_counts()
    return {**counts, **_heal_state}


@api_router.post("/admin/heal", dependencies=[Depends(require_admin)])
async def heal_now(limit: int = Query(2000, ge=1, le=5000)):
    """Kick off an on-demand re-analysis of stale wallets so the leaderboard heals immediately."""
    if _heal_state["running"]:
        counts = await _heal_counts()
        return {"started": False, "reason": "already running", **counts, **_heal_state}
    _spawn(_heal_wallets(limit=limit, batch=12, pause=0.4))
    counts = await _heal_counts()
    return {"started": True, **counts}


@api_router.get("/config/thresholds")
async def get_thresholds():
    return {"config": tuning_config.get_config(), "defaults": tuning_config.DEFAULTS, "ruleVersion": RULE_VERSION}


@api_router.put("/config/thresholds", dependencies=[Depends(require_admin)])
async def put_thresholds(payload: dict = Body(...)):
    raw = payload.get("config") if isinstance(payload, dict) and "config" in payload else payload
    if not isinstance(raw, dict):
        raise HTTPException(status_code=422, detail="Config must be an object")
    try:
        merged = tuning_config.merge_config(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await db.settings.update_one(
        {"_id": "thresholds"}, {"$set": {"config": merged, "updatedAt": _now_iso()}}, upsert=True
    )
    tuning_config.set_config(merged)
    result = await _reclassify_all()
    await db.markets_analysis.delete_many({})
    return {"config": merged, **result}


@api_router.post("/config/thresholds/reset", dependencies=[Depends(require_admin)])
async def reset_thresholds():
    merged = tuning_config.merge_config({})
    await db.settings.update_one(
        {"_id": "thresholds"}, {"$set": {"config": merged, "updatedAt": _now_iso()}}, upsert=True
    )
    tuning_config.set_config(merged)
    result = await _reclassify_all()
    await db.markets_analysis.delete_many({})
    return {"config": merged, **result}


@api_router.get("/tape")
async def tape(
    sharp_only: bool = False,
    min_size: float = Query(0.0, ge=0, le=1e12),
    limit: int = Query(80, ge=1, le=160),
):
    if not _tape:
        async with _tape_lock:
            if not _tape:
                try:
                    await _build_tape()
                except Exception as exc:
                    logger.warning("Error building tape on demand: %s", exc)
    items = _tape or []
    if sharp_only:
        items = [x for x in items if x.get("sharp")]
    if min_size > 0:
        items = [x for x in items if x.get("sizeUsd", 0) >= min_size]
    return {"count": len(items[:limit]), "trades": items[:limit], "updatedAt": _tape_updated}


app.include_router(api_router)
app.add_middleware(
    CORSMiddleware,
    allow_credentials=False,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


async def _startup():
    await db.wallets.create_index("address", unique=True)
    await db.wallets.create_index([("smartScore", -1)])
    await db.markets_analysis.create_index("conditionId", unique=True)
    settings_doc = await db.settings.find_one({"_id": "thresholds"})
    if settings_doc and settings_doc.get("config"):
        try:
            tuning_config.set_config(tuning_config.merge_config(settings_doc["config"]))
        except ValueError:
            logger.warning("Legacy tuning is incompatible with this rule version; using new defaults")
    await db.evidence_markets.create_index("conditionId", unique=True)
    await db.closing_benchmarks.create_index("conditionId", unique=True)
    await db.validation_baselines.create_index([("address", 1), ("ruleVersion", 1), ("configSignature", 1)], unique=True)
    await db.validation_snapshots.create_index([("address", 1), ("ruleVersion", 1), ("configSignature", 1), ("day", 1)], unique=True)
    market_jobs.start()
    _spawn(_refresh_loop())
    _spawn(_tape_loop())
    _spawn(_migrate_stale_wallets())
    _spawn(_heal_loop())
    _spawn(_benchmark_loop())
    _spawn(_forward_loop())
    logger.info("SharpMarket Terminal API started")


async def _shutdown():
    tasks = list(_background_tasks)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await market_jobs.close()
    from evidence_store import close_metadata_tasks
    await close_metadata_tasks()
    await poly.close()
    client.close()
