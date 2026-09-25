"""Wallet classification engine + Smart Money Score (0-100).

Sports win rate, ROI, track-record depth, holding and automation screening.
Signals are derived from real Polymarket Data API responses (activity, closed &
open positions, portfolio value).
"""
import asyncio
import math
import re
import statistics
from datetime import datetime, timezone

from config import get_config, signature


def _f(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


RELIABLE_SETTLED = 10  # min reconstructed settled bets before we trust the true winrate

WALLET_SCHEMA = 23  # bump to force re-analysis of cached wallets after an engine change


# Market category (sport/game) derived from the Polymarket event slug prefix, e.g.
# "cs2-mouz-nrg-2026-09-17" -> Counter-Strike. Slugs are clean & hyphen-delimited so a segment
# match is reliable; the free-text title is only a fallback.
CATEGORY_RULES = [
    ("Counter-Strike", ["cs2", "csgo", "cs-go", "counterstrike", "counter-strike"]),
    ("League of Legends", ["lol", "league-of-legends", "lck", "lec", "lpl", "lcs", "worlds"]),
    ("Dota 2", ["dota", "dota2", "the-international"]),
    ("Valorant", ["valorant", "vct"]),
    ("Rocket League", ["rocket-league", "rlcs"]),
    ("Overwatch", ["overwatch", "owl"]),
    ("Rainbow Six", ["rainbow-six", "r6", "r6s"]),
    ("Call of Duty", ["call-of-duty", "cod"]),
    ("Mobile Legends", ["mobile-legends", "mlbb"]),
    ("StarCraft", ["starcraft", "sc2"]),
    ("NBA", ["nba"]),
    ("NFL", ["nfl"]),
    ("MLB", ["mlb"]),
    ("NHL", ["nhl"]),
    ("Soccer", ["soccer", "epl", "efl", "laliga", "la-liga", "lal", "ucl", "uel", "uecl",
                "uefa", "champions-league", "europa", "premier-league", "seriea", "serie-a",
                "bundesliga", "bl1", "bl2", "ligue-1", "ligue1", "fr1", "fr2", "eredivisie",
                "ere", "primeira", "por", "spl", "mls", "world-cup", "worldcup", "fifa", "copa",
                "jpl", "jap", "jleague", "j-league", "kleague", "k-league", "csl", "brasileirao",
                "brasileiro", "bra", "ligamx", "liga-mx", "mex", "superlig", "tur", "spfl", "sco",
                "jupiler", "bel", "eliteserien", "nor", "allsvenskan", "swe", "bol1", "uzb1",
                "chi", "arg", "per", "col", "ecu", "uru", "par", "gre", "aut", "sui", "den",
                "rus", "ukr", "pol", "cze", "cro", "srb", "rou", "clf", "chi2", "kor"]),
    ("Weather", ["temperature", "rainfall", "snowfall", "highest-temperature",
                 "lowest-temperature"]),
    ("UFC / MMA", ["ufc", "mma", "bellator", "pfl"]),
    ("Tennis", ["tennis", "atp", "wta", "wimbledon", "roland-garros", "australian-open"]),
    ("Cricket", ["cricket", "ipl", "t20", "bbl"]),
    ("Formula 1", ["f1", "formula-1", "formula1", "grand-prix"]),
    ("Boxing", ["boxing"]),
    ("Golf", ["golf", "pga", "liv-golf"]),
    ("eSports (other)", ["esports", "esport"]),
]


# Safe, unambiguous full-word/phrase keywords for the TITLE fallback (used only when a market
# has no slug). Short league codes (e.g. "den", "por", "arg") are deliberately EXCLUDED here —
# on free text they false-match inside unrelated words (e.g. "den" inside "presi-den-tial").
CATEGORY_TITLE_KEYWORDS = {
    "Counter-Strike": ["counter-strike", "counterstrike", "cs2", "cs:go"],
    "League of Legends": ["league of legends"],
    "Dota 2": ["dota"],
    "Valorant": ["valorant"],
    "Rocket League": ["rocket league"],
    "Overwatch": ["overwatch"],
    "Rainbow Six": ["rainbow six"],
    "Call of Duty": ["call of duty"],
    "NBA": ["nba"],
    "NFL": ["nfl"],
    "MLB": ["mlb"],
    "NHL": ["nhl"],
    "UFC / MMA": ["ufc", "mma"],
    "Tennis": ["tennis", "atp", "wta", "wimbledon"],
    "Cricket": ["cricket", "ipl"],
    "Formula 1": ["formula 1", "grand prix"],
    "Boxing": ["boxing"],
    "Golf": ["golf", "pga"],
    "Weather": ["temperature", "rainfall", "snowfall"],
    "Soccer": ["soccer", "premier league", "la liga", "bundesliga", "serie a",
               "champions league", "ligue 1", "europa league"],
}


def _title_has(text, phrase):
    return re.search(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", text) is not None


def categorize_market(slug, title=None):
    """Bucket a market into a sport/game. The event slug (e.g. "mlb-nyy-min-2026-09-15") is the
    reliable signal — matched on hyphen-delimited segments. The title is only a fallback and uses
    strict word-boundary matching so short codes can't false-match inside unrelated words."""
    s = (slug or "").lower().strip()
    if s:
        padded = f"-{s}-"
        for label, kws in CATEGORY_RULES:
            for kw in kws:
                if f"-{kw}-" in padded or s.startswith(f"{kw}-"):
                    return label
    t = (title or "").lower()
    if t:
        for label, phrases in CATEGORY_TITLE_KEYWORDS.items():
            for p in phrases:
                if _title_has(t, p):
                    return label
    return "Other"


def reconstruct_performance(activity, positions=None, metadata=None, as_of=None):
    from performance import reconstruct
    return reconstruct(activity, positions, categorize_market, metadata, as_of)


def _effective_roi(s):
    """(roi, reliable) — prefer reconstructed netted ROI once the sample is big enough;
    otherwise fall back to the (biased) closed-positions ROI marked unreliable."""
    tr = s.get("true_roi")
    sb = s.get("settled_bets", 0)
    if tr is not None and sb >= RELIABLE_SETTLED and s.get("performance_reliable", True):
        return tr, True
    return s.get("roi", 0.0), False


def _effective_winrate(s):
    """(winrate, reliable) — prefer reconstructed true winrate once the sample is big
    enough; otherwise fall back to the (biased) closed-positions winrate marked unreliable."""
    tw = s.get("true_winrate")
    sb = s.get("settled_bets", 0)
    if tw is not None and sb >= RELIABLE_SETTLED and s.get("performance_reliable", True):
        return tw, True
    return s.get("winrate", 0.0), False


def compute_wallet_stats(address, activity, open_positions, value, perf, very_high_volume=False):
    """Behavioral + performance stats derived from the wallet's FULL /activity ledger.
    `perf` is the output of reconstruct_performance (computed once by the caller)."""
    activity = activity or []
    open_positions = open_positions or []
    user_trades = [a for a in activity if a.get("type") == "TRADE"]
    redeems = [a for a in activity if a.get("type") in ("REDEEM", "CLAIM")]

    n_trades = len(user_trades)
    # Collapse same-timestamp partial fills into "logical orders" so cadence isn't inflated.
    logical_keys = set()
    for a in user_trades:
        logical_keys.add((int(_f(a.get("timestamp"))), a.get("conditionId"), a.get("side")))
    n_logical = len(logical_keys)
    lts = sorted({int(_f(a.get("timestamp"))) for a in user_trades if a.get("timestamp")})
    intervals = [b - a for a, b in zip(lts, lts[1:]) if b >= a]
    median_interval = statistics.median(intervals) if intervals else None
    reg_cv = None
    if len(intervals) >= 5 and statistics.mean(intervals) > 0:
        reg_cv = statistics.pstdev(intervals) / statistics.mean(intervals)
    span_days = ((lts[-1] - lts[0]) / 86400.0) if len(lts) >= 2 else 0.0
    trades_per_day = (n_logical / span_days) if span_days > 0.5 else float(n_logical)
    fast = sum(1 for iv in intervals if iv <= 3)
    fast_ratio = (fast / len(intervals)) if intervals else 0.0

    vols = [_f(a.get("usdcSize")) or _f(a.get("size")) * _f(a.get("price")) for a in user_trades]
    total_volume = sum(vols)
    avg_trade_usd = (total_volume / n_logical) if n_logical else 0.0
    max_trade_usd = max(vols) if vols else 0.0

    buys = sum(1 for a in user_trades if a.get("side") == "BUY")
    sells = sum(1 for a in user_trades if a.get("side") == "SELL")
    buy_ratio = (buys / n_trades) if n_trades else 0.0

    by_market = {}
    for a in user_trades:
        cid = a.get("conditionId")
        if cid is None:
            continue
        by_market.setdefault(cid, set()).add(a.get("outcomeIndex"))
    distinct_markets = len(by_market)
    two_sided = sum(1 for s in by_market.values() if len(s) >= 2)
    two_sided_ratio = (two_sided / distinct_markets) if distinct_markets else 0.0

    # A price near 0 or 1 does not establish resolution; require explicit redeemability.
    open_live = [p for p in open_positions if not (p.get("redeemable") is True and _f(p.get("curPrice")) in (0.0, 1.0))]
    unrealized_pnl = sum(_f(p.get("cashPnl")) for p in open_live)
    open_value = sum(_f(p.get("currentValue")) for p in open_live)
    portfolio_value = _f(value[0].get("value")) if value else open_value

    # Honest, unbiased performance (full-ledger reconstruction) — see reconstruct_performance.
    settled = perf["settled_bets"]
    true_wr = perf["true_winrate"]
    true_roi = perf["true_roi"]
    net_real = perf["net_realized"]
    hold_ratio = perf["hold_ratio"]

    return {
        "n_trades": n_trades,
        "n_logical": n_logical,
        "n_trades_sample": n_trades,
        "very_high_volume": bool(very_high_volume),
        "n_redeem": len(redeems),
        "n_sell": sells,
        "n_closed": settled,  # true resolved count (drives skill gates & confidence)
        "median_interval": median_interval,
        "reg_cv": reg_cv,
        "trades_per_day": round(trades_per_day, 2),
        "fast_ratio": round(fast_ratio, 3),
        "total_volume": round(total_volume, 2),
        "avg_trade_usd": round(avg_trade_usd, 2),
        "max_trade_usd": round(max_trade_usd, 2),
        "buy_ratio": round(buy_ratio, 3),
        "distinct_markets": distinct_markets,
        "two_sided_ratio": round(two_sided_ratio, 3),
        "winrate": round(true_wr, 4) if true_wr is not None else 0.0,
        "true_winrate": true_wr,
        "settled_bets": settled,
        "net_realized": net_real,
        "true_roi": true_roi,
        "invested_settled": perf["invested_settled"],
        "roi": round(true_roi, 4) if true_roi is not None else 0.0,
        "realized_pnl": net_real,
        "unrealized_pnl": round(unrealized_pnl, 2),
        "total_pnl": round(net_real + unrealized_pnl, 2),
        "invested": perf["invested_settled"],
        "avg_entry_price": perf["avg_entry_price"],
        "hold_ratio": round(hold_ratio, 3) if hold_ratio is not None else None,
        "portfolio_value": round(portfolio_value, 2),
    }


def classify(s, cfg=None):
    from sharp_evidence import QUALIFIED
    cfg = cfg or get_config()
    ev = s.get("evidence") or {}
    primary = ev.get("category", "INSUFFICIENT_DATA")
    whale = any(s.get(k, 0) >= cfg["whale"][c] for k, c in [
        ("max_trade_usd", "maxTradeUsd"), ("total_volume", "totalVolume"),
        ("portfolio_value", "portfolioValue"), ("avg_trade_usd", "avgTradeUsd")])
    labels = [primary] + (["WHALE"] if whale else [])
    auto = ev.get("automation", {})
    return {"labels": labels, "primary": primary,
        "skill": primary if primary in QUALIFIED else "UNQUALIFIED",
        "behaviors": [primary] if primary in {"PROBABLE_BOT", "AUTOMATION_UNCERTAIN", "HEDGED_STYLE", "ACTIVE_TRADER"} else [],
        "isWhale": whale, "automation": auto.get("risk") in {"high", "uncertain"},
        "confidence": round((ev.get("holding") or {}).get("coverage", 0), 3),
        "reasons": ev.get("reasons") or ["Insufficient qualification evidence"]}


def smart_score(s, cfg=None, unreliable=False):
    from sharp_evidence import QUALIFIED
    ev = s.get("evidence") or {}
    eligible = ev.get("category") in QUALIFIED
    return {"score": ev.get("score") if eligible else None,
        "components": ev.get("scoreComponents") if eligible else {k: None for k in (cfg or get_config())["weights"]}}


def compute_history(per_market):
    """Trade-history series for the wallet charts, from the reconstructed per-market P&L
    (honest — includes losses), NOT the winner-biased closed-positions API.

    - pnlSeries: cumulative realized PnL over resolution time (equity curve).
    - entryTiming: per-bet entry price vs realized PnL (edge scatter).
    """
    settled = [m for m in (per_market or []) if m.get("settled") and not m.get("void")]
    settled_by_time = sorted(settled, key=lambda m: m.get("resolvedAt") or m.get("cashflowClosedAt") or 0)

    series = []
    running = 0.0
    for m in settled_by_time:
        running += _f(m.get("netPnl"))
        series.append(
            {
                "t": int(m.get("resolvedAt") or m.get("cashflowClosedAt") or 0),
                "cum": round(running, 2),
                "pnl": round(_f(m.get("netPnl")), 2),
                "entry": _f(m.get("entryPrice")),
                "title": m.get("title"),
            }
        )
    if len(series) > 120:
        step = len(series) / 120.0
        idxs = sorted(set([int(i * step) for i in range(120)] + [len(series) - 1]))
        series = [series[i] for i in idxs]

    entry = []
    for m in settled:
        entry.append(
            {
                "entry": _f(m.get("entryPrice")),
                "pnl": round(_f(m.get("netPnl")), 2),
                "invested": _f(m.get("invested")),
                "resolved": bool(m.get("heldToResolution")),
                "won": bool(m.get("won")),
                "title": m.get("title"),
            }
        )
    entry.sort(key=lambda e: e["invested"], reverse=True)
    entry = entry[:80]

    wins = sum(1 for m in settled if m.get("won"))
    return {
        "pnlSeries": series,
        "entryTiming": entry,
        "totalRealized": round(running, 2),
        "resolvedCount": len(settled),
        "avgEntry": round(sum(e["entry"] for e in entry) / len(entry), 4) if entry else 0.0,
        "winners": wins,
        "losers": len(settled) - wins,
    }


def compute_category_breakdown(per_market):
    """Group a wallet's SETTLED bets by sport/game so users see where its edge really is
    (and where it bleeds). Returns rows sorted by activity, richest bucket first."""
    buckets = {}
    for m in per_market or []:
        if not m.get("settled") or m.get("void"):
            continue
        cat = m.get("category") or "Other"
        b = buckets.setdefault(cat, {"bets": 0, "wins": 0, "invested": 0.0, "netPnl": 0.0})
        b["bets"] += 1
        if m.get("won"):
            b["wins"] += 1
        b["invested"] += _f(m.get("invested"))
        b["netPnl"] += _f(m.get("netPnl"))
    out = []
    for cat, b in buckets.items():
        out.append(
            {
                "category": cat,
                "bets": b["bets"],
                "wins": b["wins"],
                "losses": b["bets"] - b["wins"],
                "winrate": round(b["wins"] / b["bets"], 4) if b["bets"] else 0.0,
                "invested": round(b["invested"], 2),
                "netPnl": round(b["netPnl"], 2),
                "roi": round(b["netPnl"] / b["invested"], 4) if b["invested"] > 0 else 0.0,
            }
        )
    out.sort(key=lambda x: (x["bets"], x["invested"]), reverse=True)
    return out


def _reliability(stats, capped, positions_capped):
    settled = stats.get("settled_bets") or 0
    incomplete = bool(capped or positions_capped or stats.get("unreconciled_markets", 0))
    return {"tier": "partial" if incomplete else ("verified" if settled else "thin"),
        "label": "Partial measured sample" if incomplete else ("Fetched balances reconciled" if settled else "No settled performance"),
        "note": "Valid market measurements are retained. Missing records affect coverage and qualification separately. "
                "Cashflows include reported fees only. Missing fee or closing-price evidence does not block Sharp qualification."}


def _identity(activity):
    for a in activity:
        if a.get("name") or a.get("pseudonym"):
            return {
                "name": a.get("name"),
                "pseudonym": a.get("pseudonym"),
                "profileImage": a.get("profileImage") or None,
            }
    return {"name": None, "pseudonym": None, "profileImage": None}


async def analyze_wallet(client, address, db=None):
    from evidence_store import load_metadata, baselines
    from sharp_evidence import build_evidence, deduplicate, sports_record
    address = address.lower()
    activity_res, positions_res, value = await asyncio.gather(
        client.activity_paginated(address, pages=12, size=500),
        client.positions_paginated(address, pages=4, size=500),
        client.value(address),
    )
    activity, capped = activity_res
    activity = deduplicate(activity)
    positions, positions_capped = positions_res
    as_of = datetime.now(timezone.utc).timestamp()
    ids = {r["conditionId"] for r in activity + positions if r.get("conditionId")}
    metadata = await load_metadata(client, db, ids)
    perf = reconstruct_performance(activity, positions, metadata, as_of)
    cfg = get_config()
    cfg["_signature"] = signature()
    frozen = await baselines(db, address) if db is not None else {}
    evidence = build_evidence(activity, positions, perf, metadata, cfg, as_of, capped, positions_capped, frozen)
    stats = compute_wallet_stats(address, activity, positions, value, perf)
    stats.update(activity_capped=bool(capped), positions_capped=bool(positions_capped),
        n_logical=evidence["automation"]["estimatedEpisodes"],
        performance_reliable=bool(perf["settled_bets"]),
        ledger_reconciled=perf["reconciled"], unreconciled_markets=len(perf["issues"]),
        hold_ratio=evidence["holding"]["positionRate"], capital_hold_ratio=evidence["holding"]["capitalRate"],
        incentive_income=evidence["automation"]["incentiveIncome"], evidence=evidence)
    classification = classify(stats)
    score = smart_score(stats)
    del stats["evidence"]
    ident = _identity(activity)
    records = evidence.pop("records")
    from legacy_v2.classifier import compare
    previous_model = compare(address, activity, positions, value, capped, positions_capped)
    market_entries = {r["conditionId"]: {"entryPrice": r.get("entryPrice"), "won": r.get("won"), "invested": r.get("invested")} for r in records if r.get("conditionId")}
    profile = {"address": address, **ident, **classification,
        "category": classification["primary"],
        "subTags": [l for l in classification["labels"] if l != classification["primary"]],
        "smartScore": score["score"], "scoreComponents": score["components"],
        "scoreStatus": evidence["scoreStatus"], "scoreNote": evidence["scoreNote"],
        "scoreWeights": get_config()["weights"], "configSignature": signature(),
        "stats": stats, "evidence": evidence, "sportsRecord": sports_record(evidence), "previousModel": previous_model,
        "marketEntries": market_entries,
        "history": compute_history(records), "categoryBreakdown": compute_category_breakdown(records),
        "reliability": _reliability(stats, capped, positions_capped),
        "schemaVersion": WALLET_SCHEMA, "updatedAt": datetime.now(timezone.utc).isoformat(),
        "audit": {"issues": perf["issues"], "markets": records,
            "performance": {"settledBets": perf["settled_bets"], "trueWinrate": perf["true_winrate"],
                "trueRoi": perf["true_roi"], "netRealized": perf["net_realized"],
                "invested": perf["invested_settled"], "sampleTrades": stats["n_trades"]},
            "trades": [{"timestamp": int(_f(t.get("timestamp"))), "side": t.get("side"),
                "outcome": t.get("outcome"), "price": _f(t.get("price")),
                "sizeUsd": _f(t.get("usdcSize")), "shares": _f(t.get("size")),
                "question": t.get("title"), "conditionId": t.get("conditionId")}
                for t in sorted((r for r in activity if r.get("type") == "TRADE"), key=lambda t: _f(t.get("timestamp")), reverse=True)[:150]]}}
    return profile
