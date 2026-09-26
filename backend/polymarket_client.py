"""Async client + helpers for Polymarket public APIs (Gamma + Data API). No keys required."""
import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

logger = logging.getLogger("polymarket")


class UpstreamError(RuntimeError):
    """A failed fetch must never masquerade as a valid empty ledger."""

GAMMA = "https://gamma-api.polymarket.com"
DATA = "https://data-api.polymarket.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; SharpMarketTerminal/1.0)",
    "Accept": "application/json",
}

# Verified live tag slugs (probed against Gamma API).
CATEGORY_DEFS = [
    {"id": "esports", "label": "Esports", "tag_slug": "esports", "group": "esports", "primary": True},
    {"id": "lol", "label": "League of Legends", "tag_slug": "league-of-legends", "group": "esports"},
    {"id": "cs2", "label": "Counter-Strike", "tag_slug": "counter-strike", "group": "esports"},
    {"id": "dota", "label": "Dota 2", "tag_slug": "dota-2", "group": "esports"},
    {"id": "valorant", "label": "Valorant", "tag_slug": "valorant", "group": "esports"},
    {"id": "all", "label": "All Sports", "tag_slug": "sports", "group": "sports"},
    {"id": "soccer", "label": "Soccer", "tag_slug": "soccer", "group": "sports"},
    {"id": "nba", "label": "NBA", "tag_slug": "nba", "group": "sports"},
    {"id": "nfl", "label": "NFL", "tag_slug": "nfl", "group": "sports"},
    {"id": "cfb", "label": "CFB", "title": "College Football", "tag_slug": "cfb", "group": "sports"},
    {"id": "mlb", "label": "MLB", "tag_slug": "mlb", "group": "sports"},
    {"id": "mma", "label": "UFC / MMA", "tag_slug": "mma", "group": "sports"},
    {"id": "tennis", "label": "Tennis", "tag_slug": "tennis", "group": "sports"},
    {"id": "cricket", "label": "Cricket", "tag_slug": "cricket", "group": "sports"},
]
CATEGORY_BY_ID = {c["id"]: c for c in CATEGORY_DEFS}

MARKET_TYPES = [
    {"id": "moneyline", "label": "Moneyline"},
    {"id": "spread", "label": "Spread"},
    {"id": "total", "label": "Totals"},
    {"id": "prop", "label": "Props"},
]

# Drop near-decided / longshot markets (e.g. a 0.25c outright) — no analytical value.
LONGSHOT_MAX_PRICE = 0.95

_SPREAD_RE = re.compile(r"\([+-]?\d+(\.\d+)?\)")


def _to_float(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def parse_json_field(value, default):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return default
    return value if value is not None else default


def classify_market_type(question: str, outcomes=None, group_item_title=None) -> str:
    s = (question or "").lower()
    git = (group_item_title or "").lower()
    blob = f"{s} {git}"
    if any(k in blob for k in ["total", "over/under", "o/u", "over ", "under ", "team total"]):
        return "total"
    if "handicap" in blob or "spread" in blob or _SPREAD_RE.search(blob):
        return "spread"
    # Binary yes/no questions (NRFI, extra innings, outright "Will ... ?") are props — check
    # BEFORE moneyline so a prop whose text embeds the matchup isn't misread as a head-to-head.
    outs = [str(o).strip().lower() for o in (outcomes or [])]
    is_binary = set(outs) == {"yes", "no"}
    if is_binary or s.startswith("will ") or "will there" in s:
        return "prop"
    # Head-to-head straight win: "Team A vs. Team B" (period optional), "winner", "to win",
    # a best-of tag, or simply two non-binary (team-name) outcomes.
    if re.search(r"\bvs\.?\b", s) or "winner" in s or "moneyline" in s or "to win" in s or "(bo" in s:
        return "moneyline"
    if outs and not is_binary and "?" not in s:
        return "moneyline"
    return "prop"


class PolymarketClient:
    def __init__(self, concurrency: int = 10):
        self._client: Optional[httpx.AsyncClient] = None
        self._sem = asyncio.Semaphore(concurrency)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers=HEADERS,
                timeout=httpx.Timeout(20.0, connect=10.0),
                limits=httpx.Limits(max_connections=50, max_keepalive_connections=25),
            )
        return self._client

    async def close(self):
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _get(self, base: str, path: str, params: dict | None = None, retries: int = 5) -> Any:
        client = await self._get_client()
        url = f"{base}{path}"
        for attempt in range(retries):
            exc = None
            resp = None
            async with self._sem:
                try:
                    resp = await client.get(url, params=params)
                except Exception as e:  # noqa: BLE001
                    exc = e
            if exc is not None:
                logger.warning("request error %s: %s", url, exc)
                await asyncio.sleep(0.4 * (attempt + 1))
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                retry_after = resp.headers.get("retry-after")
                try:
                    wait_sec = float(retry_after) if retry_after else 1.0 * (attempt + 1)
                except (ValueError, TypeError):
                    wait_sec = 1.0 * (attempt + 1)
                await asyncio.sleep(min(wait_sec, 8.0))
                continue
            if resp.status_code == 200:
                try:
                    return resp.json()
                except Exception:  # noqa: BLE001
                    raise UpstreamError(f"Invalid JSON from {path}")
            raise UpstreamError(f"Upstream {path} returned HTTP {resp.status_code}")
        raise UpstreamError(f"Upstream {path} unavailable after {retries} attempts")

    async def _list(self, base, path, params):
        data = await self._get(base, path, params)
        if data is None:
            return []
        if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
            raise UpstreamError(f"Unexpected response shape from {path}")
        return data

    # ---- Gamma (markets discovery) ----
    async def events(self, tag_slug: str, limit: int = 40, order: str = "volume24hr") -> list:
        data = await self._get(
            GAMMA,
            "/events",
            {
                "tag_slug": tag_slug,
                "active": "true",
                "closed": "false",
                "order": order,
                "ascending": "false",
                "limit": limit,
            },
        )
        return data or []

    async def search_events(self, q: str, limit: int = 100) -> list:
        # Fetch 2 pages of 50 concurrently to bypass Polymarket's heavy political/historical market clutter
        p1, p2 = await asyncio.gather(
            self._get(GAMMA, "/public-search", {"q": q, "limit_per_type": 50, "page": 1}),
            self._get(GAMMA, "/public-search", {"q": q, "limit_per_type": 50, "page": 2}),
            return_exceptions=True,
        )
        events = []
        if isinstance(p1, dict) and p1.get("events"):
            events.extend(p1["events"])
        if isinstance(p2, dict) and p2.get("events"):
            events.extend(p2["events"])
        seen = set()
        deduped = []
        for e in events:
            eid = e.get("id") or e.get("slug")
            if eid and eid not in seen:
                seen.add(eid)
                deduped.append(e)
            elif not eid:
                deduped.append(e)
        return deduped[:limit]

    async def market_metadata(self, condition_ids):
        # Gamma defaults to open markets, even with a condition_ids filter.
        # A wallet's resolved losses and early exits must be fetched as well.
        batches = await asyncio.gather(*[
            self._list(GAMMA, "/markets", {"condition_ids": list(condition_ids),
                "closed": closed, "limit": len(condition_ids)})
            for closed in ("true", "false")
        ])
        wanted = set(condition_ids)
        return list({r["conditionId"]: r for batch in batches for r in batch
            if r.get("conditionId") in wanted}.values())

    async def order_book(self, token):
        return await self._get("https://clob.polymarket.com", "/book", {"token_id": token}, retries=1)

    async def historical_price(self, token, as_of):
        return await self._get(DATA, "/v2/prices-history", {"token_id": token, "as_of": int(as_of)}, retries=1)

    # ---- Data API (wallet + market analytics) ----
    async def trades(self, market: str, limit: int = 100) -> list:
        return await self._list(DATA, "/trades", {"market": market, "limit": limit})

    async def holders(self, market: str, limit: int = 20) -> list:
        return await self._list(DATA, "/holders", {"market": market, "limit": min(limit, 20)})

    async def positions(self, user: str, limit: int = 200, offset: int = 0) -> list:
        return await self._list(DATA, "/positions", {
            "user": user, "limit": limit, "offset": offset,
            "sizeThreshold": 0, "includeArchived": "true",
        })

    async def positions_paginated(self, user: str, pages: int = 4, size: int = 500):
        out = []
        for page in range(pages):
            rows = await self.positions(user, size, page * size)
            out.extend(rows)
            if len(rows) < size:
                return out, False
        return out, True

    async def closed_positions(self, user: str, limit: int = 200) -> list:
        return await self._get(
            DATA, "/closed-positions", {"user": user, "limit": limit, "sortBy": "REALIZEDPNL"}
        ) or []

    async def activity(self, user: str, limit: int = 300, offset: int = 0, end: int | None = None) -> list:
        params: dict = {"user": user, "limit": limit}
        if end is not None:
            params["end"] = int(end)
        elif offset:
            params["offset"] = offset
        return await self._list(
            DATA, "/activity", params
        )

    async def activity_paginated(self, user: str, pages: int = 12, size: int = 500, min_days: float = 65.0):
        """Pull the wallet's activity ledger (TRADE + REDEEM + ...) via timestamp-based pagination.
        Using ?end=<timestamp-1> bypasses Polymarket's 5000 offset cap and allows high-volume traders
        to be tracked back 60+ days.
        Returns (events, capped) where capped=True means more exist beyond the sampled pages."""
        out: list = []
        end_ts = None
        now_ts = time.time()
        for p in range(pages):
            arr = await self.activity(user, size, offset=p * size if end_ts is None else 0, end=end_ts)
            out.extend(arr)
            if len(arr) < size:
                return out, False
            ts = arr[-1].get("timestamp")
            if ts is not None:
                end_ts = int(ts) - 1
                if (now_ts - float(ts)) / 86400.0 >= min_days:
                    return out, True
            else:
                end_ts = None
        return out, True

    async def value(self, user: str) -> list:
        return await self._list(DATA, "/value", {"user": user})

    async def user_trades(self, user: str, limit: int = 500, offset: int = 0) -> list:
        return await self._get(DATA, "/trades", {"user": user, "limit": limit, "offset": offset}) or []

    async def user_trades_paginated(self, user: str, pages: int = 2, size: int = 500):
        """Pull a rich sample of a wallet's own trade history (offset-paginated).
        Returns (trades, capped) where capped=True means more trades exist beyond the sample."""
        out: list = []
        for p in range(pages):
            arr = await self.user_trades(user, size, p * size)
            out.extend(arr)
            if len(arr) < size:
                return out, False
        return out, True

    async def has_trades_beyond(self, user: str, offset: int) -> bool:
        """Cheap deep-probe: does the wallet have a trade at this offset? (volume signal)"""
        arr = await self.user_trades(user, 1, offset)
        return bool(arr)


def normalize_markets_from_events(events: list, category_id: str) -> list:
    """Flatten Gamma events into tradeable 2-outcome market rows (skips longshots)."""
    out = []
    seen = set()
    for event in events:
        event_title = event.get("title", "")
        event_slug = event.get("slug", "")
        event_icon = event.get("icon") or event.get("image")
        event_end = event.get("endDate")
        for m in event.get("markets", []) or []:
            cond = m.get("conditionId")
            if not cond or cond in seen or m.get("closed"):
                continue
            outcomes = parse_json_field(m.get("outcomes"), [])
            prices = parse_json_field(m.get("outcomePrices"), [])
            tokens = parse_json_field(m.get("clobTokenIds"), [])
            if len(outcomes) != 2 or len(tokens) != 2:
                continue
            volume = _to_float(m.get("volumeNum") or m.get("volume"))
            if volume <= 0:
                continue
            prices_f = [_to_float(p) for p in prices] if prices else [0.5, 0.5]
            if len(prices_f) < 2:
                prices_f = [0.5, 0.5]
            # skip near-decided / longshot markets
            if max(prices_f) > LONGSHOT_MAX_PRICE:
                continue
            question = m.get("question") or m.get("groupItemTitle") or event_title
            seen.add(cond)
            out.append(
                {
                    "id": cond,
                    "conditionId": cond,
                    "question": question,
                    "marketType": classify_market_type(
                        question, outcomes, m.get("groupItemTitle")
                    ),
                    "eventTitle": event_title,
                    "eventSlug": event_slug or cond,
                    "eventIcon": event_icon,
                    "slug": m.get("slug"),
                    "category": category_id,
                    "outcomes": outcomes,
                    "prices": prices_f,
                    "tokens": [str(t) for t in tokens],
                    "volume": volume,
                    "liquidity": _to_float(m.get("liquidity")),
                    "endDate": m.get("endDate") or event_end,
                    "startDate": m.get("startDate"),
                    "gameStartTime": _norm_dt(m.get("gameStartTime")),
                    "icon": m.get("icon") or event_icon,
                }
            )
    out.sort(key=lambda x: x["volume"], reverse=True)
    return out


_TYPE_ORDER = {"moneyline": 0, "spread": 1, "total": 2, "prop": 3}

_FAR_FUTURE = float("inf")


def _parse_dt(iso: Optional[str]) -> Optional[datetime]:
    """Parse an ISO/Polymarket date string (handles space separator and short tz offsets)."""
    if not iso:
        return None
    try:
        s = iso.strip().replace(" ", "T").replace("Z", "+00:00")
        if re.search(r"[+-]\d{2}$", s):
            s += ":00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _end_ts(iso: Optional[str]) -> float:
    """Epoch timestamp for sorting; missing/invalid sorts last."""
    dt = _parse_dt(iso)
    return dt.timestamp() if dt else _FAR_FUTURE


def _norm_dt(iso: Optional[str]) -> Optional[str]:
    """Normalize a date string to ISO 8601 so browsers parse it reliably."""
    dt = _parse_dt(iso)
    return dt.isoformat() if dt else None


def _extract_market_line(q: str) -> Optional[float]:
    """Extract numeric line from a spread or total question string."""
    m = re.search(r"\(([+-]?\d+(?:\.\d+)?)\)", q or "")
    if m:
        try:
            return float(m.group(1))
        except (ValueError, TypeError):
            pass
    m = re.search(r"(?:o/u|over/under|total)\s*(\d+(?:\.\d+)?)", q or "", re.I)
    if m:
        try:
            return float(m.group(1))
        except (ValueError, TypeError):
            pass
    return None


def _is_derivative_market(q: str) -> bool:
    """Detect quarters, halves, team totals, player props, or derivative lines."""
    s = (q or "").lower()
    return any(
        k in s
        for k in [
            "1h",
            "2h",
            "1q",
            "2q",
            "3q",
            "4q",
            "quarter",
            "half",
            "team total",
            "touchdown",
        ]
    )


def prune_event_markets(markets: list) -> list:
    """
    Select the main market and 1-2 adjacent alternative markets per type.
    Keeps core betting types focused and uncluttered (especially for NFL/NBA).
    """
    by_type: dict[str, list] = {}
    for m in markets:
        by_type.setdefault(m.get("marketType") or "prop", []).append(m)

    has_core = bool(by_type.get("moneyline") or by_type.get("spread") or by_type.get("total"))
    out = []

    # 1. Moneyline: full game first, then top derivative (e.g. 1H), max 2-3
    mls = by_type.get("moneyline", [])
    if mls:
        full = [m for m in mls if not _is_derivative_market(m.get("question", ""))]
        deriv = [m for m in mls if _is_derivative_market(m.get("question", ""))]
        full.sort(key=lambda x: -x.get("volume", 0))
        deriv.sort(key=lambda x: -x.get("volume", 0))
        chosen_ml = (full[:2] + deriv[:1]) if full else mls[:3]
        out.extend(chosen_ml[:3])

    # 2. Spread: main spread (highest volume) + 1-2 closest alternative lines
    spreads = by_type.get("spread", [])
    if spreads:
        full = [m for m in spreads if not _is_derivative_market(m.get("question", ""))]
        pool = full if full else spreads
        pool.sort(key=lambda x: -x.get("volume", 0))
        main = pool[0]
        main_line = _extract_market_line(main.get("question", ""))
        alts = pool[1:]
        if main_line is not None:
            def alt_spread_key(m):
                line = _extract_market_line(m.get("question", ""))
                dist = abs(line - main_line) if line is not None else 999.0
                return (dist, -m.get("volume", 0))

            alts.sort(key=alt_spread_key)
        else:
            alts.sort(key=lambda x: -x.get("volume", 0))
        out.extend([main] + alts[:2])

    # 3. Total: main total (highest volume) + 1-2 closest alternative lines
    totals = by_type.get("total", [])
    if totals:
        full = [m for m in totals if not _is_derivative_market(m.get("question", ""))]
        pool = full if full else totals
        pool.sort(key=lambda x: -x.get("volume", 0))
        main = pool[0]
        main_line = _extract_market_line(main.get("question", ""))
        alts = pool[1:]
        if main_line is not None:
            def alt_total_key(m):
                line = _extract_market_line(m.get("question", ""))
                dist = abs(line - main_line) if line is not None else 999.0
                return (dist, -m.get("volume", 0))

            alts.sort(key=alt_total_key)
        else:
            alts.sort(key=lambda x: -x.get("volume", 0))
        out.extend([main] + alts[:2])

    # 4. Prop: if core game lines exist, keep top 3 props by volume; otherwise keep up to 8 (e.g. champion futures)
    props = by_type.get("prop", [])
    if props:
        props.sort(key=lambda x: -x.get("volume", 0))
        limit = 3 if has_core else 8
        out.extend(props[:limit])

    return out


def group_by_event(markets: list) -> list:
    """Group flat markets into events, each carrying its categorized submarkets."""
    events: dict[str, dict] = {}
    for m in markets:
        slug = m.get("eventSlug") or m["id"]
        e = events.setdefault(
            slug,
            {
                "eventSlug": slug,
                "title": m.get("eventTitle") or m["question"],
                "icon": m.get("eventIcon") or m.get("icon"),
                "category": m.get("category"),
                "endDate": m.get("endDate"),
                "gameStartTime": m.get("gameStartTime"),
                "volume": 0.0,
                "markets": [],
            },
        )
        e["markets"].append(m)
        e["volume"] += m.get("volume", 0)
    out = []
    for e in events.values():
        e["markets"] = prune_event_markets(e["markets"])
        counts: dict[str, int] = {}
        for m in e["markets"]:
            counts[m["marketType"]] = counts.get(m["marketType"], 0) + 1
        e["typeCounts"] = counts
        e["markets"].sort(
            key=lambda x: (_TYPE_ORDER.get(x["marketType"], 9), -x.get("volume", 0))
        )
        out.append(e)
    now = time.time()

    def _event_sort_key(e: dict):
        ts = _end_ts(e.get("gameStartTime") or e.get("endDate"))
        if ts == _FAR_FUTURE:
            return (2, ts, -e.get("volume", 0))
        diff = ts - now
        if diff < -4 * 3600:
            return (3, -diff, -e.get("volume", 0))
        elif diff < 0:
            return (0, -diff, -e.get("volume", 0))
        else:
            return (1, diff, -e.get("volume", 0))

    active_events = []
    for e in out:
        ts = _end_ts(e.get("endDate") or e.get("gameStartTime"))
        if ts != _FAR_FUTURE and (now - ts) > 6 * 3600:
            continue
        active_events.append(e)

    active_events.sort(key=_event_sort_key)
    return active_events


def pick_display_market(event: dict, market_type: Optional[str]) -> Optional[dict]:
    markets = event.get("markets") or []
    if not markets:
        return None
    if market_type:
        matches = [m for m in markets if m["marketType"] == market_type]
        if matches:
            return matches[0]
    return markets[0]
