"""Versioned, point-in-time qualification for directional sports bettors.

Scores rank qualified Sharps only. They are indices, never win probabilities.
Public fills cannot certify a human, reveal hidden hedges, or identify an insider.
"""
import hashlib
import math
import random
import statistics
from collections import defaultdict
from datetime import datetime, timezone

RULE_VERSION = "sports-record-2026-09-22.1"
QUALIFIED = {"SHARP", "PROVEN_SHARP"}
DAY = 86400
EPS = 1e-6


def num(value, default=0.0):
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def timestamp(value):
    if isinstance(value, (int, float)):
        return num(value)
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()
    except (ValueError, TypeError):
        return None


def deduplicate(rows):
    """Deduplicate identified source records, not merely equal-size partial fills."""
    seen, result = set(), []
    for row in rows or []:
        identity = row.get("id") or row.get("activityId")
        if identity is None and row.get("logIndex") is not None:
            identity = (row.get("transactionHash"), row["logIndex"])
        if identity is not None:
            key = (row.get("conditionId"), row.get("type"), str(identity))
            if key in seen:
                continue
            seen.add(key)
        result.append(row)
    return result


def retention_record(events, positions, meta, row, as_of):
    """FIFO acquisition-cost continuity; hedged lots never regain conviction credit.

    Acquiring the other outcome cancels equal shares of directional exposure on
    both sides. Sales consume FIFO lots. Re-entry creates a new denominator lot.
    Official resolution freezes exposure; redemption timing cannot improve it.
    """
    events = sorted(events, key=lambda e: num(e.get("timestamp")))
    resolved_at = timestamp(meta.get("resolvedAt"))
    start = timestamp(meta.get("gameStartTime"))
    result_at = timestamp(meta.get("resultKnownAt"))
    resolved = bool(meta.get("resolved") and resolved_at and resolved_at <= as_of)
    redemption = [e for e in events if e.get("type") in {"REDEEM", "CLAIM"}]
    resolved_observed = resolved or bool(redemption) or any(p.get("redeemable") is True for p in positions)
    lots = defaultdict(list)
    acquired = retained = hedged = 0.0
    acquired_shares = redeemed_cost = redeemed_shares = 0.0
    prematch_cost = live_cost = late_cost = unknown_cost = 0.0
    prematch_entries = []
    problems = []
    if not row.get("reconciled"):
        problems.extend(row.get("issues") or ["ledger_unreconciled"])
    # Unknown resolution time permits conservative observations, not a precise
    # dated-cohort claim. A REDEEM is evidence of resolution, not its timestamp.
    cutoff = resolved_at if resolved else as_of + 1
    post_resolution_activity = False
    for e in events:
        t = num(e.get("timestamp"))
        kind = e.get("type")
        if kind in {"REWARD", "MAKER_REBATE", "TAKER_REBATE"}:
            continue
        if t > as_of:
            continue
        if t >= cutoff:
            if kind == "TRADE" and e.get("side") == "BUY":
                late_cost += num(e.get("usdcSize"), num(e.get("size")) * num(e.get("price")))
                post_resolution_activity = True
            continue
        if kind in {"SPLIT", "MERGE", "CONVERSION", "TRANSFER"}:
            problems.append("non_directional_or_unattributed_transfer")
            continue
        if kind in {"REDEEM", "CLAIM"}:
            # With unknown resolution date, retain redeemed lots as a lower
            # bound. Never subtract redemption as an early exit.
            key = str(e.get("outcomeIndex"))
            if key not in {"0", "1"}:
                keys = [k for k, ls in lots.items() if sum(l["size"] for l in ls) > EPS]
                key = keys[0] if len(keys) == 1 else None
            left = num(e.get("size"))
            if key is not None:
                for lot in lots[key]:
                    taken = min(left, lot["size"])
                    kept = min(taken, lot["continuous"])
                    redeemed_shares += kept
                    redeemed_cost += kept * lot["unitCost"]
                    lot["size"] -= taken
                    lot["continuous"] -= kept
                    left -= taken
                    if left <= EPS:
                        break
            continue
        if kind != "TRADE":
            continue
        size, cash = num(e.get("size")), num(e.get("usdcSize"), None)
        if cash is None:
            cash = size * num(e.get("price"))
        key = str(e.get("outcomeIndex"))
        if key not in {"0", "1"} or size <= 0 or cash < 0:
            problems.append("invalid_directional_trade")
            continue
        if e.get("side") == "BUY":
            acquired += cash
            acquired_shares += size
            lot = {"size": size, "continuous": size, "unitCost": cash / size}
            opposite = "1" if key == "0" else "0"
            for other in lots[opposite]:
                matched = min(lot["continuous"], other["continuous"])
                other["continuous"] -= matched
                lot["continuous"] -= matched
                hedged += matched * (lot["unitCost"] + other["unitCost"])
                if lot["continuous"] <= EPS:
                    break
            lots[key].append(lot)
            if not t or not start:
                unknown_cost += cash
            elif result_at and t >= result_at:
                late_cost += cash
            elif t >= start:
                live_cost += cash
            else:
                prematch_cost += cash
                prematch_entries.append({"t": t, "outcome": int(key), "cost": cash, "shares": size,
                    "fee": num(e.get("feeUsd")), "feeIncluded": e.get("feeIncludedInCashflow") is True,
                    "feeKnown": e.get("feeUsd") is not None or e.get("feeIncludedInCashflow") is True})
        elif e.get("side") == "SELL":
            left = size
            for lot in lots[key]:
                taken = min(left, lot["size"])
                lot["size"] -= taken
                lot["continuous"] = max(0, lot["continuous"] - taken)
                left -= taken
                if left <= EPS:
                    break
            if left > 1e-4 + EPS:
                problems.append("missing_acquisition")
    retained = redeemed_cost + sum(l["continuous"] * l["unitCost"] for ls in lots.values() for l in ls)
    retained_shares = redeemed_shares + sum(l["continuous"] for ls in lots.values() for l in ls)
    fraction = min(1.0, retained_shares / acquired_shares) if acquired_shares > EPS else None
    if problems or not resolved_observed:
        fraction = None
    benchmark = meta.get("benchmark") or {}
    clv = None
    clv_note = "No validated pre-event closing benchmark"
    # A current feesEnabled flag is not historical per-fill fee evidence.
    fees_known = all(e["feeKnown"] for e in prematch_entries)
    all_trade_fees_known = all(
        e.get("feeIncludedInCashflow") is True or num(e.get("feeUsd"), -1) >= 0
        for e in events if e.get("type") == "TRADE" and num(e.get("timestamp")) <= as_of)
    pre_only = acquired > EPS and prematch_cost >= acquired - EPS and not problems and not post_resolution_activity
    if benchmark.get("valid") and pre_only and fees_known and prematch_entries:
        bt = num(benchmark.get("timestamp"))
        prices = benchmark.get("prices") or []
        # Benchmark must be after every entry and before the reported start.
        if len(prices) == 2 and all(0 < num(p) < 1 for p in prices) and start and bt < start and all(e["t"] <= bt for e in prematch_entries):
            near_cost = sum(e["cost"] for e in prematch_entries if bt - e["t"] < 300)
            depth = num(benchmark.get("depthUsd"))
            if depth > 0 and near_cost <= depth * .10:
                shares = sum(e["shares"] for e in prematch_entries)
                cost = sum(e["cost"] + (0 if e["feeIncluded"] else e["fee"]) for e in prematch_entries)
                clv = (sum(e["shares"] * prices[e["outcome"]] for e in prematch_entries) - cost) / shares
                clv_note = "Local pre-event order-book midpoint; conditional reference, not fair-value proof"
            else:
                clv_note = "Own recent trading is material relative to benchmark depth"
        else:
            clv_note = "Entry/benchmark/start timing is not a valid pre-match comparison"
    elif not pre_only:
        clv_note = "Live, post-result or unknown entry timing; excluded from pre-match edge"
    elif not fees_known:
        clv_note = "Entry fees are not established for an all-in price comparison"
    return {
        **row, "eventId": meta.get("eventId") or None,
        "resolvedAt": resolved_at, "resolutionObserved": resolved_observed,
        "resolutionTimeKnown": resolved, "gameStartTime": start,
        "resolutionTimeSource": meta.get("resolutionTimeSource", "unavailable"),
        "resolutionLowerAt": timestamp(meta.get("reportedClosedAt")) or (min((num(e.get("timestamp")) for e in redemption), default=0) or None),
        "void": bool(meta.get("void")), "acquiredCost": acquired,
        "acquiredShares": acquired_shares, "retainedShares": retained_shares if fraction is not None else None,
        "capitalRetention": min(1., retained / acquired) if acquired and fraction is not None else None,
        "retainedCost": retained if fraction is not None else None,
        "retention": fraction, "heldToResolution": fraction is not None and fraction >= .9 - EPS,
        "hedgedCost": hedged, "holdingIssues": sorted(set(problems)),
        "prematchCost": prematch_cost, "liveCost": live_cost,
        "postResultCost": late_cost, "unknownTimingCost": unknown_cost,
        "preMatchOnly": pre_only, "clv": clv, "clvNote": clv_note,
        "benchmark": benchmark if benchmark else None,
        "feesKnown": all_trade_fees_known and bool(prematch_entries),
        "entryFeesKnown": fees_known and bool(prematch_entries),
        "firstEntryAt": min((num(e.get("timestamp")) for e in events if e.get("type") == "TRADE" and e.get("side") == "BUY"), default=0),
        "lastActivityAt": max((num(e.get("timestamp")) for e in events), default=0),
        "eventGroupingKnown": bool(meta.get("eventId")),
        "accounting": "FIFO original entry cost; opposing inventory cancels continuity",
    }


def automation_evidence(activity, records, cfg):
    trades = [e for e in deduplicate(activity) if e.get("type") == "TRADE"]
    episodes = {}
    for e in trades:
        t = int(num(e.get("timestamp")))
        # No order IDs: same transaction/outcome/side is only an estimated episode.
        key = (e.get("transactionHash") or t, e.get("conditionId"), e.get("outcomeIndex"), e.get("side"))
        episodes.setdefault(key, {"t": t, "market": e.get("conditionId"), "side": e.get("side"), "size": 0})["size"] += num(e.get("size"))
    ordered = sorted(episodes.values(), key=lambda e: e["t"])
    by_day = defaultdict(list)
    for e in ordered:
        if e["t"]:
            by_day[e["t"] // DAY].append(e)
    rapid_days = mechanical_days = intense_days = 0
    for entries in by_day.values():
        times = sorted(set(e["t"] for e in entries))
        intervals = [b - a for a, b in zip(times, times[1:])]
        if len(intervals) >= cfg["minDailyEpisodes"] and sum(0 < i <= cfg["rapidSeconds"] for i in intervals) / len(intervals) >= cfg["rapidFraction"]:
            rapid_days += 1
        if len(intervals) >= cfg["minDailyEpisodes"] and statistics.mean(intervals) > 0 and statistics.pstdev(intervals) / statistics.mean(intervals) < .15:
            mechanical_days += 1
        if len(entries) >= cfg["intenseEpisodes"] and len({e["market"] for e in entries}) >= cfg["intenseMarkets"]:
            intense_days += 1
    acquired = sum(r.get("acquiredCost", 0) for r in records)
    hedge_ratio = sum(r.get("hedgedCost", 0) for r in records) / acquired if acquired else 0
    hedged_markets = sum(r.get("hedgedCost", 0) > r.get("acquiredCost", 0) * .5 for r in records)
    buys = sum(num(e.get("usdcSize")) for e in trades if e.get("side") == "BUY")
    sells = sum(num(e.get("usdcSize")) for e in trades if e.get("side") == "SELL")
    turnover = sells / buys if buys else 0
    groups = []
    if max(rapid_days, mechanical_days) >= cfg["minDays"]:
        groups.append({"group": "timing", "reason": f"Repeated rapid or regular episodes on {max(rapid_days, mechanical_days)} days"})
    if hedge_ratio >= cfg["hedgedFraction"] and hedged_markets >= cfg["minHedgedMarkets"] and turnover >= .5:
        groups.append({"group": "inventory", "reason": f"{hedge_ratio:.0%} paired acquisition cost with repeated two-sided turnover"})
    if intense_days >= cfg["minDays"]:
        groups.append({"group": "distribution", "reason": f"High episode intensity across many markets on {intense_days} days"})
    # Coordination is descriptive until repeated patterns span distinct days.
    bursts = defaultdict(set)
    for e in ordered:
        bursts[(e["t"] // DAY, e["t"] // 3, e["side"], round(e["size"], 2))].add(e["market"])
    coordinated_days = {key[0] for key, markets in bursts.items() if len(markets) >= 6}
    if len(coordinated_days) >= cfg["minDays"]:
        groups.append({"group": "coordination", "reason": "Repeated same-size cross-market bursts on multiple days; shared news remains a possible cause"})
    incentives = sum(num(e.get("usdcSize")) for e in activity if e.get("type") in {"REWARD", "MAKER_REBATE", "TAKER_REBATE"})
    risk = "high" if len(groups) >= 2 else ("uncertain" if groups else "low_observed")
    return {"risk": risk, "groups": groups, "estimatedEpisodes": len(episodes),
        "activeDays": len(by_day), "rapidDays": rapid_days, "intenseDays": intense_days,
        "hedgedCapitalRatio": round(hedge_ratio, 4), "turnoverRatio": round(turnover, 4),
        "marketMakerStyle": hedge_ratio >= cfg["hedgedFraction"] and hedged_markets >= cfg["minHedgedMarkets"],
        "incentiveIncome": round(incentives, 2),
        "note": "Public fills only. Partial fills are grouped into estimated episodes. Low observed risk does not verify a human; hidden hedges and private cancellations are unobserved."}


def lower_bound(values, confidence=.90, seed="", iterations=800):
    """Deterministic percentile bootstrap over independent event/day blocks."""
    if len(values) < 2:
        return None
    rng = random.Random(int(hashlib.sha256(seed.encode()).hexdigest()[:16], 16))
    means = sorted(sum(rng.choices(values, k=len(values))) / len(values) for _ in range(iterations))
    return means[max(0, int((1 - confidence) * iterations) - 1)]


def holding_bounds(records, cohort_complete=True):
    known = [r for r in records if r.get("retention") is not None]
    total, held = len(records), sum(r["retention"] >= .9 - EPS for r in known)
    cost = sum(num(r.get("acquiredCost")) for r in records)
    kept = sum(num(r.get("retainedCost")) for r in known)
    unknown_cost = sum(num(r.get("acquiredCost")) for r in records if r.get("retention") is None)
    # Unknown acquisition cost prevents a defensible capital bound.
    basis_known = all(num(r.get("acquiredCost")) > EPS for r in records)
    return {"positions": total, "measuredPositions": len(known), "heldPositions": held,
        "positionRate": held / len(known) if known else None,
        "positionLower": held / total if total and cohort_complete else None,
        "positionUpper": (held + total - len(known)) / total if total and cohort_complete else None,
        "capitalRate": kept / (cost - unknown_cost) if cost > unknown_cost else None,
        "capitalLower": kept / cost if cost and cohort_complete and basis_known else None,
        "capitalUpper": (kept + unknown_cost) / cost if cost and cohort_complete and basis_known else None,
        "acquiredCost": round(cost, 2), "retainedCost": round(kept, 2),
        "coverage": len(known) / total if total else 0, "cohortComplete": cohort_complete}


def cohort_time(row):
    """Reported market close, else observed resolution/activity; never exact-oracle proof."""
    return row.get("resolutionLowerAt") or row.get("resolvedAt") or row.get("lastActivityAt") or 0


def holding_passes(hold, cfg):
    return (hold["coverage"] >= cfg["minDataCoverage"]
        and all(hold[k] is not None and hold[k] >= cfg["minHold"] - EPS for k in ("positionRate", "capitalRate")))


def assess_scope(records, scope, as_of, complete, cfg, frozen=None):
    sh, proven = cfg["sharp"], cfg["proven"]
    resolved = [r for r in records if (r.get("resolutionObserved") or r.get("resolvedAt"))
        and not r.get("void") and cohort_time(r) <= as_of]
    recent = [r for r in resolved if cohort_time(r) >= as_of - sh["windowDays"] * DAY]
    extended = [r for r in resolved if cohort_time(r) >= as_of - sh["extendedDays"] * DAY]
    event_count = lambda rows: len({r.get("eventId") for r in rows if r.get("eventId") and r.get("settled")})
    window = sh["windowDays"]
    if event_count(recent) < sh["minEvents"] or event_count(extended) >= proven["minEvents"]:
        recent, window = extended, sh["extendedDays"]
    hold = holding_bounds(recent, complete)
    grouped = defaultdict(list)
    for r in recent:
        grouped[str(r.get("eventId") or "unknown:" + r["conditionId"])].append(r)
    events = []
    for eid, rows in grouped.items():
        usable = [r for r in rows if r.get("settled") and r.get("netPnl") is not None]
        invested = sum(r["invested"] for r in usable)
        pnl = sum(r["netPnl"] for r in usable)
        measured = len(usable) == len(rows) and invested > EPS
        events.append({"id":eid, "t":max(cohort_time(r) for r in rows),
            "firstEntryAt":min((r.get("firstEntryAt") or cohort_time(r)) for r in rows),
            "roi":pnl / invested if measured else None, "pnl":pnl, "invested":invested, "rows":rows})
    events.sort(key=lambda e:(e["t"], e["id"]))
    measured = [e for e in events if e["roi"] is not None and not e["id"].startswith("unknown:")]
    n = len(measured)
    first_entries = [e["firstEntryAt"] for e in measured]
    span = (max(first_entries) - min(first_entries)) / DAY if first_entries else 0
    invested = sum(e["invested"] for e in measured)
    profit = sum(e["pnl"] for e in measured)
    wins = sum(e["pnl"] > EPS for e in measured)
    winrate = wins / n if n else None
    roi = profit / invested if invested else None
    data_coverage = n / len(events) if events else 0
    recent_hold = holding_bounds([r for e in events[-sh["recentEvents"]:] for r in e["rows"]], complete)
    gates = [
        {"key":"holding", "label":"90% position and capital retention", "passed":holding_passes(hold, sh),
         "detail":f"{hold['measuredPositions']} measured resolved positions; {hold['coverage']:.0%} holding coverage. Retention uses measured sports exposure."},
        {"key":"recentHolding", "label":f"Last {sh['recentEvents']} events retain 90%", "passed":holding_passes(recent_hold, sh),
         "detail":f"{min(len(events), sh['recentEvents'])} recent observed events"},
        {"key":"sample", "label":f"{sh['minEvents']} resolved sports events / {sh['minDays']} days", "passed":n >= sh["minEvents"] and span >= sh["minDays"],
         "detail":f"{n} measured underlying events; entries span {span:.1f} days"},
        {"key":"winrate", "label":f"Win rate at least {sh['minWinrate']:.0%}", "passed":winrate is not None and winrate >= sh["minWinrate"] - EPS,
         "detail":f"{wins} profitable events out of {n}; related positions on one event are netted together"},
        {"key":"profit", "label":"Positive ROI and P&L", "passed":roi is not None and roi > sh["minRoi"] and profit > EPS,
         "detail":f"Observed P&L ${profit:,.2f} on ${invested:,.2f} invested. Missing fee records do not block qualification."},
        {"key":"coverage", "label":f"At least {sh['minDataCoverage']:.0%} usable observed event records", "passed":data_coverage >= sh["minDataCoverage"],
         "detail":f"{n}/{len(events)} observed events have grouped, reconciled cashflows. A capped history is a sample, not an automatic rejection."},
    ]
    qualified = all(g["passed"] for g in gates)
    holding_ok = holding_passes(hold, sh)
    holding_fail = hold["coverage"] >= sh["minDataCoverage"] and any(hold[k] is not None and hold[k] < sh["minHold"] - EPS for k in ("positionRate", "capitalRate"))
    category = "SHARP" if qualified else "ACTIVE_TRADER" if holding_fail else (
        "CANDIDATE" if holding_ok and n >= cfg["candidate"]["minEvents"] and profit > 0 else "RETAIL" if holding_ok else "INSUFFICIENT_DATA")
    if qualified and n >= proven["minEvents"] and span >= proven["minDays"]:
        category = "PROVEN_SHARP"
    halves = [measured[:n//2], measured[n//2:]]
    periods = [{"events":len(p), "pnl":round(sum(e["pnl"] for e in p),2),
        "winrate":sum(e["pnl"] > EPS for e in p)/len(p) if p else None} for p in halves]
    future = {"status":"awaiting_baseline", "events":0, "pnl":None, "passed":False}
    if frozen and frozen.get("ruleVersion") == RULE_VERSION and frozen.get("configSignature") == cfg.get("_signature"):
        later = [e for e in measured if e["firstEntryAt"] > frozen["asOf"] and e["id"] not in frozen.get("eventIds", [])]
        fpnl = sum(e["pnl"] for e in later)
        future = {"status":"collecting", "baselineAt":frozen["asOf"], "events":len(later), "pnl":round(fpnl,2),
            "passed":len(later) >= proven["futureEvents"] and fpnl > 0,
            "currentBaselineClass":frozen.get("category"), "legacyBaselineClass":frozen.get("legacyClass")}
        if future["passed"]: future["status"] = "passed"
    components = {k:None for k in cfg["weights"]}
    score = None
    if category in QUALIFIED:
        components = {"winrate":winrate * 100, "roi":min(100, 50 + max(0, roi) * 200),
            "depth":min(100, 50 * n / proven["minEvents"] + 50 * span / proven["minDays"])}
        score = round(sum(components[k] * cfg["weights"][k] for k in components),1)
    return {"scope":scope, "category":category, "score":score, "scoreComponents":components,
        "gates":gates, "holding":hold, "recentHolding":recent_hold, "events":n, "wins":wins,
        "winrate":winrate, "elapsedDays":round(span,1), "windowDays":window, "windowStart":as_of-window*DAY,
        "windowEnd":as_of, "profit":round(profit,2), "roi":roi,
        "standardizedRoi":statistics.mean(e["roi"] for e in measured) if measured else None,
        "profitWithoutBestEvent":round(profit-max((e["pnl"] for e in measured),default=0),2),
        "dataCoverage":data_coverage, "sampledHistory":not complete, "periods":periods,
        "forward":future, "eventIds":[e["id"] for e in events], "historicalCategory":frozen.get("category") if frozen else None,
        "note":"Sports track record: win rate, observed ROI, sample size and retention. Price, fee evidence, entry timing and future validation are not eligibility gates."}


def qualified_market_scope(profile, category):
    if profile.get("primary") not in QUALIFIED or category in {"Other", "Weather"}:
        return None
    qualified = profile.get("evidence", {}).get("qualifiedScopes", [])
    return "Sports" if "Sports" in qualified else category if category in qualified else None


def candidate_market_scope(profile, category):
    """Check if a wallet qualifies as a CANDIDATE for the given market category.
    Candidate status is category-scoped unless the wallet is a candidate across overall Sports."""
    if not profile or profile.get("isBot"):
        return None
    ev = profile.get("evidence", {})
    cand_scopes = ev.get("candidateScopes")
    is_cand = profile.get("primary") == "CANDIDATE" or "CANDIDATE" in (profile.get("labels") or [])
    if not is_cand and not cand_scopes:
        return None
    # If candidateScopes is tracked and populated:
    if cand_scopes:
        if "Sports" in cand_scopes:
            return "Sports"
        if category and category in cand_scopes:
            return category
        best_key = ev.get("bestScopeKey")
        if is_cand and (best_key == "Sports" or best_key == category):
            return best_key
        return None
    # If is_cand is True but candidateScopes was not tracked (e.g. mock test or legacy profile):
    if is_cand:
        best_key = ev.get("bestScopeKey")
        if best_key and best_key != "Sports" and category and best_key != category:
            return None
        return "Sports"
    return None


def sports_record(evidence):
    """Display the exact qualifying scope; use combined sports for other wallets."""
    key = evidence.get("bestScopeKey") if evidence.get("category") in QUALIFIED else "Sports"
    scope = evidence.get("scopes", {}).get(key) or {}
    return {"scope": key, **{k: scope.get(k) for k in
        ("events", "winrate", "roi", "profit", "elapsedDays", "windowDays")}}


def build_evidence(activity, positions, perf, metadata, cfg, as_of, capped=False, positions_capped=False, frozen=None):
    grouped, snapshots = defaultdict(list), defaultdict(list)
    for e in activity:
        grouped[e.get("conditionId")].append(e)
    for p in positions:
        snapshots[p.get("conditionId")].append(p)
    records = [retention_record(grouped[r["conditionId"]], snapshots[r["conditionId"]], metadata.get(r["conditionId"], {}), r, as_of) for r in perf["per_market"]]
    auto = automation_evidence(activity, records, cfg["automation"])
    return evaluate_records(records, auto, cfg, as_of, capped, positions_capped, frozen)


def evaluate_records(records, auto, cfg, as_of, capped=False, positions_capped=False, frozen=None):
    """Re-evaluate saved observed records without inventing fresh market data."""
    scopes = {}
    by_category = defaultdict(list)
    sports = [r for r in records if r.get("category") not in {None, "Other", "Weather"}]
    if sports:
        by_category["Sports"] = sports
    for r in sports:
        by_category[r["category"]].append(r)
    all_recent = [r for r in sports if r.get("resolutionObserved") and not r.get("void") and cohort_time(r) >= as_of - cfg["sharp"]["extendedDays"] * DAY]
    global_hold = holding_bounds(all_recent, not (capped or positions_capped))
    global_pass = holding_passes(global_hold, cfg["sharp"])
    for category, rows in by_category.items():
        scopes[category] = assess_scope(rows, "All sports & esports" if category == "Sports" else category, as_of, not (capped or positions_capped), cfg, (frozen or {}).get(category))
        scope = scopes[category]
        scope["gates"].append({"key": "walletHolding", "label": "Sports-wide 90% retention", "passed": global_pass,
            "detail": "Measured sports position and capital retention in the extended window, including live trades; non-sports trading does not affect this requirement"})
        if scope["category"] in QUALIFIED and not global_pass:
            scope["category"] = "ACTIVE_TRADER" if global_hold["coverage"] >= cfg["sharp"]["minDataCoverage"] and any(global_hold[k] is not None and global_hold[k] < .9 - EPS for k in ("positionRate", "capitalRate")) else "CANDIDATE"
            scope["score"] = None
            scope["scoreComponents"] = {k: None for k in cfg["weights"]}
        auto_pass = auto["risk"] == "low_observed" and not auto["marketMakerStyle"]
        scope["gates"].append({"key": "automation", "label": "Low observed automation / directional style", "passed": auto_pass,
            "detail": "Multiple signal groups imply probable automation; one group remains uncertain. Paired inventory styles are excluded."})
        if not auto_pass:
            scope["category"] = "PROBABLE_BOT" if auto["risk"] == "high" else ("AUTOMATION_UNCERTAIN" if auto["risk"] == "uncertain" else "HEDGED_STYLE")
            scope["score"] = None
            scope["scoreComponents"] = {k: None for k in cfg["weights"]}
    ordered = sorted(scopes.values(), key=lambda s: ({"PROVEN_SHARP": 5, "SHARP": 4, "CANDIDATE": 3, "ACTIVE_TRADER": 1}.get(s["category"], 0), s["events"]), reverse=True)
    sports_scope = scopes.get("Sports")
    sports_profit = sports_scope.get("profit") if sports_scope else None

    # Sub-scope can promote wallet to SHARP if certified.
    # But a sub-scope can ONLY promote wallet to CANDIDATE if overall sports profit is > 0.
    if sports_scope and sports_scope.get("category") in QUALIFIED:
        best = sports_scope
    else:
        eligible_ordered = [
            s for s in ordered
            if s.get("category") in QUALIFIED or (s.get("category") == "CANDIDATE" and sports_profit is not None and sports_profit > 0)
        ]
        best = eligible_ordered[0] if eligible_ordered else (sports_scope or (ordered[0] if ordered else None))

    category = best["category"] if best else "INSUFFICIENT_DATA"
    if category == "CANDIDATE" and (sports_profit is None or sports_profit <= 0):
        category = "ACTIVE_TRADER" if global_hold["coverage"] >= cfg["sharp"]["minDataCoverage"] and any(global_hold[k] is not None and global_hold[k] < .9 - EPS for k in ("positionRate", "capitalRate")) else "RETAIL"

    if global_hold["coverage"] >= cfg["sharp"]["minDataCoverage"] and any(global_hold[k] is not None and global_hold[k] < .9 - EPS for k in ("positionRate", "capitalRate")):
        category = "ACTIVE_TRADER"
    elif not best and all(global_hold[k] is not None and global_hold[k] >= .9 - EPS for k in ("positionRate", "capitalRate")):
        category = "RETAIL"
    if auto["risk"] == "high":
        category = "PROBABLE_BOT"
    elif auto["risk"] == "uncertain":
        category = "AUTOMATION_UNCERTAIN"
    elif auto["marketMakerStyle"]:
        category = "HEDGED_STYLE"
    reasons = [g["reason"] for g in auto["groups"]]
    if best:
        reasons.extend(g["label"] + ": " + g["detail"] for g in best["gates"] if not g["passed"])
    if (sports_profit is None or sports_profit <= 0) and any(s.get("category") == "CANDIDATE" for s in scopes.values()):
        p_str = f"${sports_profit:,.2f}" if sports_profit is not None else "$0.00"
        reasons.append(f"Negative overall measured sports P&L ({p_str}); wallet-level Candidate status disallowed across portfolio.")
    if not reasons:
        reasons = ["All scope qualification requirements passed" if category in QUALIFIED else "No sufficient measured sports track record yet"]
    qualified_scopes = [k for k, s in scopes.items() if s["category"] in QUALIFIED] if category in QUALIFIED else []
    candidate_scopes = [k for k, s in scopes.items() if s.get("category") == "CANDIDATE"]
    return {"ruleVersion": RULE_VERSION, "asOf": as_of, "category": category,
        "qualifiedScopes": qualified_scopes, "candidateScopes": candidate_scopes,
        "scopes": scopes, "bestScope": best["scope"] if best else None,
        "bestScopeKey": next((k for k, s in scopes.items() if s is best), None),
        "score": best["score"] if best and category in QUALIFIED else None,
        "scoreComponents": best["scoreComponents"] if best and category in QUALIFIED else {k: None for k in cfg["weights"]},
        "holding": global_hold, "automation": auto, "reasons": reasons,
        "timing": {"prematchCost": sum(r["prematchCost"] for r in records), "liveCost": sum(r["liveCost"] for r in records), "postResultCost": sum(r["postResultCost"] for r in records), "unknownCost": sum(r["unknownTimingCost"] for r in records)},
        "records": records, "pendingPositions": sum(not r.get("resolutionObserved") for r in records),
        "voidPositions": sum(bool(r.get("void")) for r in records),
        "unknownMetadata": sum(not r.get("eventGroupingKnown") or not r.get("gameStartTime") for r in records),
        "scoreStatus": "qualified" if category in QUALIFIED else "not_qualified",
        "scoreNote": f"Sharp rank uses win rate ({cfg['weights']['winrate']:.0%}), ROI ({cfg['weights']['roi']:.0%}) and track-record depth ({cfg['weights']['depth']:.0%}) after sports sample, 90% holding and bot checks. No price or fee-evidence gate."}
