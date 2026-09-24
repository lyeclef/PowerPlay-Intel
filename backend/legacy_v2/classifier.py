"""Wallet classification engine + Smart Money Score (0-100).

Categories: SHARP, WHALE, INSIDER_EARLY, MARKET_MAKER, BOT, SCALPER, RETAIL_NOISE.
Signals are derived from real Polymarket Data API responses (activity, closed &
open positions, portfolio value).
"""
import asyncio
import math
import re
import statistics
from datetime import datetime, timezone

from .config import get_config, signature


def _f(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


RELIABLE_SETTLED = 10  # min reconstructed settled bets before we trust the true winrate

WALLET_SCHEMA = 11  # bump to force re-analysis of cached wallets after an engine change


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


def reconstruct_performance(activity, positions=None):
    from .performance import reconstruct
    return reconstruct(activity, positions, categorize_market)


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
    hold_ratio = perf["hold_ratio"] if perf["hold_ratio"] is not None else 0.5

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
        "hold_ratio": round(hold_ratio, 3),
        "portfolio_value": round(portfolio_value, 2),
    }


def _pnl_score(pnl):
    if pnl >= 0:
        return _clamp(50 + math.log10(pnl + 1) * 11)
    return _clamp(50 - math.log10(-pnl + 1) * 11)


def smart_score(s, cfg=None, unreliable=False):
    cfg = cfg or get_config()
    w = cfg["weights"]
    unreliable = unreliable or not s.get("performance_reliable", True)
    # winrate & hold-to-resolution are winner-biased/unreliable for high-volume automation
    # wallets (the API only returns their biggest resolved winners) — neutralize them.
    # Winrate is trusted only when reconstructed from a reliable sample (see _effective_winrate).
    wr, wr_reliable = _effective_winrate(s)
    roi_eff, roi_reliable = _effective_roi(s)
    winrate_score = _clamp(wr * 100) if (wr_reliable and not unreliable) else 50.0
    roi_score = _clamp(40 + roi_eff * 40) if (roi_reliable and not unreliable) else 50.0
    hold_score = 50.0 if unreliable else _clamp(s["hold_ratio"] * 100)
    # PnL component uses the wallet's NET realized cashflow (entries vs exits + payouts) —
    # an honest, netted number — rather than the API's winner-biased realized total. Open marks
    # only nudge it within a capped band so a big open position can't erase a proven edge.
    net_real = s.get("net_realized")
    pnl_base = _pnl_score(net_real if net_real is not None else s["realized_pnl"])
    unreal = s["unrealized_pnl"]
    if unreal >= 0:
        nudge = min(8.0, math.log10(unreal + 1) * 2.0)
    else:
        nudge = -min(12.0, math.log10(-unreal + 1) * 2.5)
    pnl_score = 50.0 if not s.get("performance_reliable", True) else _clamp(pnl_base + nudge)
    score = (
        winrate_score * w["winrate"]
        + roi_score * w["roi"]
        + hold_score * w["hold"]
        + pnl_score * w["pnl"]
    )
    return {
        "score": round(score, 1),
        "components": {
            "winrate": round(winrate_score, 1),
            "roi": round(roi_score, 1),
            "hold": round(hold_score, 1),
            "pnl": round(pnl_score, 1),
        },
    }


def _confidence(s):
    return round(
        _clamp(
            0.30
            + min(s["n_trades"], 60) / 60 * 0.40
            + min(s["n_closed"], 20) / 20 * 0.30,
            0,
            1,
        ),
        2,
    )


def _sub_tags(s, primary):
    tags = []
    if primary != "WHALE" and (
        s["max_trade_usd"] >= 20000 or s["portfolio_value"] >= 75000 or s["total_volume"] >= 150000
    ):
        tags.append("WHALE")
    if primary != "SHARP" and s["n_closed"] >= 5 and s["winrate"] >= 0.6 and s["roi"] > 0.1:
        tags.append("SHARP")
    if primary != "BOT" and s["fast_ratio"] >= 0.3 and s["n_trades"] >= 30:
        tags.append("BOT")
    return tags


LABEL_PRIORITY = [
    "INSIDER_EARLY",
    "SHARP",
    "MARKET_MAKER",
    "BOT",
    "SCALPER",
    "WHALE",
    "RETAIL_NOISE",
]


def classify(s, cfg=None):
    """Multi-label, cohesive classification.

    - Skill tier is mutually exclusive: SHARP / INSIDER_EARLY *or* RETAIL.
    - Behaviors can stack (BOT + MARKET_MAKER, BOT + SCALPER) but a proven skilled
      wallet (holds to resolution, +EV) can never also be a bot/scalper/MM.
    - WHALE (size) stacks with anything.
    Returns a dict with the full cohesive label set + primary + reasons.
    """
    cfg = cfg or get_config()
    ins, sh, el, ins_c = cfg["insufficient"], cfg["sharp"], cfg["elite"], cfg["insider"]
    wh, bo, mmc, scc = cfg["whale"], cfg["bot"], cfg["mm"], cfg["scalper"]
    auto = cfg["automation"]
    n = s["n_trades"]
    nl = s.get("n_logical", n)
    vol = s["total_volume"]
    mi = s["median_interval"]
    cv = s["reg_cv"]

    insufficient = (
        n < ins["maxTrades"] and vol < ins["maxVolume"] and s["n_closed"] < ins["maxClosed"]
    )

    is_whale = (
        s["max_trade_usd"] >= wh["maxTradeUsd"]
        or s["total_volume"] >= wh["totalVolume"]
        or s["portfolio_value"] >= wh["portfolioValue"]
        or s["avg_trade_usd"] >= wh["avgTradeUsd"]
    )

    # ---- automation override (highest priority) ----
    # Extreme trade volume / cadence = machinery, NOT smart directional money. This must
    # override the (winner-biased) skill signals so a bot can't masquerade as an elite SHARP.
    very_high = bool(s.get("very_high_volume"))
    automation = very_high or (
        s["trades_per_day"] >= auto["minTradesPerDay"] and n >= auto["minSample"]
    )
    if automation:
        is_mm_auto = (
            s["two_sided_ratio"] >= auto["mmTwoSided"]
            and auto["buyLo"] <= s["buy_ratio"] <= auto["buyHi"]
        )
        labels, reasons = [], []
        if is_mm_auto:
            labels.append("MARKET_MAKER")
            reasons.append(
                f"Two-sided market-making across {s['distinct_markets']}+ markets at "
                f"~{int(s['trades_per_day'])} trades/day"
            )
        else:
            labels.append("BOT")
            vol_txt = ">5,000" if very_high else f"{n}+"
            reasons.append(
                f"{vol_txt} trades across {s['distinct_markets']}+ markets at "
                f"~{int(s['trades_per_day'])}/day — automated, non-directional flow"
            )
        if is_whale:
            labels.append("WHALE")
            reasons.append(
                f"Size — avg ${s['avg_trade_usd']:,.0f}/order, max ${s['max_trade_usd']:,.0f}"
            )
        seen = set()
        labels = [x for x in labels if not (x in seen or seen.add(x))]
        primary = sorted(
            labels, key=lambda x: LABEL_PRIORITY.index(x) if x in LABEL_PRIORITY else 99
        )[0]
        return {
            "labels": labels,
            "primary": primary,
            "skill": "RETAIL",
            "behaviors": [l for l in labels if l != "WHALE"],
            "isWhale": is_whale,
            "automation": True,
            "confidence": _confidence(s),
            "reasons": reasons,
        }

    # ---- skill tier (mutually exclusive) ----
    wr, wr_reliable = _effective_winrate(s)
    roi_eff, roi_reliable = _effective_roi(s)
    settled = s.get("settled_bets", 0)
    net_real = s.get("net_realized")
    profit = net_real if net_real is not None else s.get("realized_pnl", 0)
    if insufficient or not (wr_reliable and roi_reliable):
        skill = "RETAIL"
    else:
        elite = (
            s["n_closed"] >= sh["minClosed"]
            and wr >= el["minWinrate"]
            and roi_eff >= el["minRoi"]
            and s["hold_ratio"] >= el["minHold"]
            and profit > 0
        )
        sharp = (
            s["n_closed"] >= sh["minClosed"]
            and wr >= sh["minWinrate"]
            and roi_eff > sh["minRoi"]
            and s["hold_ratio"] >= sh["minHold"]
            and profit > 0
        )
        insider = (
            elite
            and s["avg_entry_price"] <= ins_c["maxEntry"]
            and wr >= ins_c["minWinrate"]
            and roi_eff >= ins_c["minRoi"]
        )
        if insider:
            skill = "INSIDER"
        elif elite or sharp:
            skill = "SHARP"
        else:
            skill = "RETAIL"

    # ---- behavioral traits (can stack) ----
    is_bot = nl >= bo["minOrders"] and roi_eff < bo["maxRoi"] and (
        (mi is not None and mi <= bo["maxMedianInterval"])
        or s["trades_per_day"] >= bo["minTradesPerDay"]
        or (s["fast_ratio"] >= bo["minFastRatio"] and cv is not None and cv < bo["maxCv"])
    )
    is_mm = (
        s["two_sided_ratio"] >= mmc["minTwoSided"]
        and nl >= mmc["minOrders"]
        and mmc["buyLo"] <= s["buy_ratio"] <= mmc["buyHi"]
        and s["distinct_markets"] >= mmc["minMarkets"]
        and roi_eff < mmc["maxRoi"]
    )
    is_scalper = nl >= scc["minOrders"] and s["hold_ratio"] <= scc["maxHold"]

    behaviors = []
    if is_bot:
        behaviors.append("BOT")
    if is_mm:
        behaviors.append("MARKET_MAKER")
    if is_scalper and not is_mm:  # MM (balanced two-sided) & scalper contradict; MM wins
        behaviors.append("SCALPER")

    # cohesion: a proven skilled wallet holds & is directional -> not bot/mm/scalper
    if skill in ("SHARP", "INSIDER"):
        behaviors = []

    labels = []
    reasons = []
    if skill == "INSIDER":
        labels.append("INSIDER_EARLY")
        if wr_reliable:
            reasons.append(
                f"Enters cheap (avg {s['avg_entry_price']:.2f}); {int(wr*100)}% real winrate "
                f"over {settled} settled bets at {int(roi_eff*100)}% net ROI"
            )
        else:
            reasons.append(
                f"Enters cheap (avg {s['avg_entry_price']:.2f}); "
                f"${s.get('net_realized', 0):,.0f} net realized"
            )
    elif skill == "SHARP":
        labels.append("SHARP")
        if wr_reliable:
            reasons.append(
                f"{int(wr*100)}% real winrate over {settled} settled bets at {int(roi_eff*100)}% net ROI"
            )
        else:
            reasons.append(
                f"${s.get('net_realized', 0):,.0f} net realized, holds to resolution "
                f"(winrate sample too small to verify)"
            )
    elif not behaviors:
        labels.append("RETAIL_NOISE")
        if wr_reliable:
            reasons.append(
                f"Sentiment-driven — {int(wr*100)}% real winrate over {settled} settled bets"
            )
        else:
            reasons.append(
                f"Sentiment-driven — ${s.get('net_realized', 0):,.0f} net realized over {s['n_closed']} resolved bets"
            )

    if "BOT" in behaviors:
        reasons.append(f"{nl} orders at machine cadence ({int(s['fast_ratio']*100)}% <3s apart)")
    if "MARKET_MAKER" in behaviors:
        reasons.append(
            f"Two-sided liquidity in {int(s['two_sided_ratio']*100)}% of {s['distinct_markets']} markets"
        )
    if "SCALPER" in behaviors:
        reasons.append(f"Exits early {int((1 - s['hold_ratio']) * 100)}% of the time")
    labels.extend(behaviors)

    if is_whale:
        labels.append("WHALE")
        reasons.append(
            f"Size — avg ${s['avg_trade_usd']:,.0f}/order, max ${s['max_trade_usd']:,.0f}"
        )

    if not labels:
        labels.append("RETAIL_NOISE")

    seen = set()
    labels = [x for x in labels if not (x in seen or seen.add(x))]
    primary = sorted(
        labels, key=lambda x: LABEL_PRIORITY.index(x) if x in LABEL_PRIORITY else 99
    )[0]

    return {
        "labels": labels,
        "primary": primary,
        "skill": skill,
        "behaviors": behaviors,
        "isWhale": is_whale,
        "automation": False,
        "confidence": _confidence(s),
        "reasons": reasons,
    }



def compare(address, activity, positions, value, capped=False, positions_capped=False):
    """Evaluate the uploaded v2 rules on this same observed data snapshot."""
    perf = reconstruct_performance(activity, positions)
    stats = compute_wallet_stats(address, activity, positions, value, perf)
    stats.update(activity_capped=capped, positions_capped=positions_capped,
        performance_reliable=perf['true_winrate'] is not None and perf['settled_bets'] >= RELIABLE_SETTLED,
        unreconciled_markets=len(perf['issues']))
    result = classify(stats)
    score = smart_score(stats, unreliable=result.get('automation', False))
    return {'model': 'uploaded-v2-defaults', 'configSignature': signature(),
        'category': result['primary'], 'score': score['score'], 'holdRatio': stats['hold_ratio'],
        'note': 'Frozen historical comparator only; its score and labels are not current eligibility.'}
