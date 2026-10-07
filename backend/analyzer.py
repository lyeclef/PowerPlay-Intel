"""Market-level smart-money analysis.

Strength = SHARE OF SMART-MONEY CAPITAL on each side (capital weighted by wallet
quality), NOT the average wallet quality — so a near-worthless longshot side
correctly reads ~0 even if a couple of sharp wallets hold a tiny amount.
"""
import asyncio
import math
import copy
from weakref import WeakValueDictionary
from datetime import datetime, timezone

from classifier import analyze_wallet, WALLET_SCHEMA, categorize_market
from sharp_evidence import QUALIFIED, qualified_market_scope, candidate_market_scope, underdog_market_scope
from evidence_store import record_observation

ANALYSIS_SCHEMA = 24
from ai_narrative import generate_intel, _fallback
from config import signature

WALLET_TTL_SECONDS = 3600
MAX_WALLETS_PER_MARKET = 26
WALLET_ANALYSIS_TIMEOUT = 75
NEUTRAL_BAND = 4.0


def _entry_price(row, side):
    if not row:
        return None
    entries = row.get("entryPrices")
    value = entries.get("0" if side == "YES" else "1") if isinstance(entries, dict) else row.get("entryPrice")
    try:
        value = float(value)
        return value if math.isfinite(value) and 0 < value <= 1 else None
    except (TypeError, ValueError):
        return None


def _slippage(entry, current):
    return round((current - entry) * 100, 1) if entry is not None else None


def _tail_status(cents):
    if cents is None:
        return None
    return "BETTER_PRICE" if cents <= 0 else "PRIME_TAIL" if cents <= 3 else "ACCEPTABLE" if cents <= 7 else "LINE_MOVED"

_wallet_locks = WeakValueDictionary()
_wallet_classify_sem = asyncio.Semaphore(8)


async def _guarded_classify_wallet(client, db, address):
    async with _wallet_classify_sem:
        return await asyncio.wait_for(get_or_classify_wallet(client, db, address), WALLET_ANALYSIS_TIMEOUT)


def _f(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _fresh(iso_str, ttl):
    if not iso_str:
        return False
    try:
        dt = datetime.fromisoformat(iso_str)
    except (ValueError, TypeError):
        return False
    if dt.tzinfo is None:
        return False
    return (datetime.now(timezone.utc) - dt).total_seconds() < ttl


async def get_or_classify_wallet(client, db, address):
    address = address.lower()
    cached = await db.wallets.find_one({"address": address}, {"_id": 0})
    if cached and _fresh(cached.get("updatedAt"), WALLET_TTL_SECONDS) and cached.get("schemaVersion", 0) == WALLET_SCHEMA and cached.get("configSignature") == signature():
        return cached
    lock = _wallet_locks.setdefault(address, asyncio.Lock())
    async with lock:
        cached = await db.wallets.find_one({"address": address}, {"_id": 0})
        if cached and _fresh(cached.get("updatedAt"), WALLET_TTL_SECONDS) and cached.get("schemaVersion", 0) == WALLET_SCHEMA and cached.get("configSignature") == signature():
            return cached
        profile = await analyze_wallet(client, address, db=db)
        await db.wallets.update_one({"address": address}, {"$set": profile}, upsert=True)
        if profile.get("evidence"):
            await record_observation(db, profile)
        return profile


async def analyze_market(client, db, market, on_progress=None):
    config_signature = signature()
    cond = market["id"]
    tokens = market.get("tokens") or []
    prices = market.get("prices") or [0.5, 0.5]
    token_index = {str(t): i for i, t in enumerate(tokens)}
    scope = categorize_market(market.get("eventSlug") or market.get("slug"), market.get("question"))

    holders = await client.holders(cond, 20)

    # participant capital per side (USD = shares * current outcome price)
    participants: dict[str, dict] = {}

    for grp in holders or []:
        tok = str(grp.get("token"))
        gidx = token_index.get(tok)
        for h in grp.get("holders", []) or []:
            idx = gidx if gidx is not None else h.get("outcomeIndex", 0)
            addr = (h.get("proxyWallet") or "").lower()
            if not addr or idx not in (0, 1):
                continue
            px = _f(prices[idx]) if idx < len(prices) else 0.5
            cap = _f(h.get("amount")) * px
            p = participants.setdefault(
                addr, {"yes": 0.0, "no": 0.0, "yesShares": 0.0, "noShares": 0.0, "name": h.get("name"), "pseudonym": h.get("pseudonym")}
            )
            p["yes" if idx == 0 else "no"] += cap
            p["yesShares" if idx == 0 else "noShares"] += _f(h.get("amount"))

    # Current holder balances alone define exposure; trades belong in the tape.

    ranked = sorted(
        participants.items(), key=lambda kv: kv[1]["yes"] + kv[1]["no"], reverse=True
    )[:MAX_WALLETS_PER_MARKET]

    profiles = [None] * len(ranked)
    cached = {}
    # One small cache read supplies immediate results for the entire holder sample.
    if on_progress and ranked:
        async for row in db.wallets.find({"address":{"$in":[a for a, _ in ranked]},
                "schemaVersion":WALLET_SCHEMA, "configSignature":config_signature},
                {"_id":0, "audit":0, "history":0, "previousModel":0, "categoryBreakdown":0}):
            if _fresh(row.get("updatedAt"), 86400):
                cached[row["address"]] = row
    stale = set()
    tasks = {}
    failures = set()
    for i, (address, _) in enumerate(ranked):
        row = cached.get(address)
        if row:
            profiles[i] = row
        if row and _fresh(row.get("updatedAt"), WALLET_TTL_SECONDS):
            continue
        if row:
            stale.add(i)
        task = asyncio.create_task(_guarded_classify_wallet(client, db, address))
        tasks[task] = i

    async def publish(pending):
        analysis = _market_result(market, ranked, profiles, config_signature,
            pending=pending, failed=len(failures), stale=stale)
        if on_progress:
            await on_progress(analysis, {"stage":"wallets" if pending else "complete",
                "totalWallets":len(ranked), "completedWallets":len(ranked)-pending,
                "availableWallets":analysis["participantCount"], "pendingWallets":pending,
                "failedWallets":len(failures), "cachedWallets":len(stale)})
        return analysis

    pending_tasks = set(tasks)
    try:
        await publish(len(pending_tasks))
        while pending_tasks:
            done, pending_tasks = await asyncio.wait(pending_tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                i = tasks[task]
                try:
                    profiles[i] = task.result()
                    stale.discard(i)
                except Exception:
                    failures.add(i)
            await publish(len(pending_tasks))
        analysis = await publish(0)
        # A changed rubric must never publish an obsolete job over a new result.
        if signature() != config_signature:
            raise RuntimeError("Classification rules changed during profiling")
        # Incremental previews use the fast local narrative. Optional provider text
        # cannot keep the completed wallet table hidden while it is generated.
        try:
            analysis["intel"] = await asyncio.wait_for(generate_intel(analysis_summary(analysis)), timeout=5)
        except Exception:
            pass
        if signature() != config_signature:
            raise RuntimeError("Classification rules changed during profiling")
        await db.markets_analysis.update_one({"conditionId":cond}, {"$set":analysis}, upsert=True)
        return analysis
    finally:
        for task in pending_tasks:
            task.cancel()
        await asyncio.gather(*pending_tasks, return_exceptions=True)


def _market_result(market, ranked, profiles, config_signature, pending=0, failed=0, stale=None):
    stale = stale or set()
    cached = len(stale)
    cond = market["id"]
    prices = market.get("prices") or [0.5, 0.5]
    scope = categorize_market(market.get("eventSlug") or market.get("slug"), market.get("question"))
    # smart-capital = capital weighted by wallet quality (score/100)
    smart_yes = smart_no = 0.0          # quality-weighted capital (drives sharp strength)
    sharp_cap_yes = sharp_cap_no = 0.0  # raw capital from qualified sharp wallets (display)
    comb_smart_yes = comb_smart_no = 0.0  # quality-weighted capital (Sharps + Candidates + Underdogs)
    comb_cap_yes = comb_cap_no = 0.0      # raw capital (Sharps + Candidates + Underdogs)
    candidate_cap_yes = candidate_cap_no = 0.0
    candidate_count = 0
    underdog_count = 0
    cat_capital: dict[str, float] = {}
    hold_wsum = hold_total = 0.0
    top_wallets = []
    sampled_capital = sum(c["yes"] + c["no"] for _, c in ranked)
    failed_wallets = failed

    p0 = _f(prices[0]) if prices else 0.5
    p1 = _f(prices[1]) if prices and len(prices) > 1 else 0.5

    for i, ((addr, cap), prof) in enumerate(zip(ranked, profiles)):
        if isinstance(prof, Exception) or not prof:
            continue
        yc, nc = cap["yes"], cap["no"]
        total_cap = yc + nc
        if total_cap <= 0:
            continue
        ranking_scope = qualified_market_scope(prof, scope)
        scoped = (prof.get("evidence", {}).get("scopes") or {}).get(ranking_scope or scope, {})
        qualified = ranking_scope is not None and not prof.get("isBot")
        cand_scope = candidate_market_scope(prof, scope)
        is_candidate = cand_scope is not None and not qualified and not prof.get("isBot")
        underdog_scope = underdog_market_scope(prof, scope)
        is_underdog = underdog_scope is not None and not qualified and not is_candidate and not prof.get("isBot")
        # Directional shares net of paired YES/NO positions
        paired = min(cap["yesShares"], cap["noShares"])
        directional_yes = max(0., cap["yesShares"] - paired) * p0
        directional_no = max(0., cap["noShares"] - paired) * p1
        sc = scoped.get("score") if (qualified and scoped.get("score") is not None) else (prof.get("smartScore") if qualified else None)
        q = sc / 100.0 if isinstance(sc, (int, float)) else 0
        smart_yes += directional_yes * q
        smart_no += directional_no * q
        if qualified:
            sharp_cap_yes += directional_yes
            sharp_cap_no += directional_no
            comb_smart_yes += directional_yes * q
            comb_smart_no += directional_no * q
            comb_cap_yes += directional_yes
            comb_cap_no += directional_no
        elif is_candidate:
            cand_weight = 0.50
            comb_smart_yes += directional_yes * cand_weight
            comb_smart_no += directional_no * cand_weight
            comb_cap_yes += directional_yes
            comb_cap_no += directional_no
            candidate_cap_yes += directional_yes
            candidate_cap_no += directional_no
            if directional_yes > 0 or directional_no > 0:
                candidate_count += 1
        elif is_underdog:
            underdog_weight = 0.35
            comb_smart_yes += directional_yes * underdog_weight
            comb_smart_no += directional_no * underdog_weight
            comb_cap_yes += directional_yes
            comb_cap_no += directional_no
            candidate_cap_yes += directional_yes
            candidate_cap_no += directional_no
            if directional_yes > 0 or directional_no > 0:
                underdog_count += 1
        primary = prof.get("primary") or prof.get("category", "INSUFFICIENT_DATA")
        if qualified:
            primary = scoped.get("category") or "SHARP"
        elif is_candidate:
            primary = "CANDIDATE"
        elif is_underdog:
            primary = "UNDERDOG_TRADER"
        elif primary in QUALIFIED and not qualified:
            primary = scoped.get("category", "RETAIL")
        elif primary == "CANDIDATE" and not is_candidate:
            primary = scoped.get("category", "RETAIL")
        elif primary == "UNDERDOG_TRADER" and not is_underdog:
            primary = scoped.get("category", "RETAIL")
        labels = [primary] + (["WHALE"] if prof.get("isWhale") else [])
        stats = prof.get("stats", {})
        performance_scope = ranking_scope or cand_scope or underdog_scope or "Sports"
        record = prof.get("evidence", {}).get("scopes", {}).get(performance_scope, {})
        cat_capital[primary] = cat_capital.get(primary, 0.0) + total_cap
        observed_hold = stats.get("capital_hold_ratio")
        if observed_hold is not None:
            hold_wsum += observed_hold * total_cap
            hold_total += total_cap

        # Tailability & Entry price tracking
        wallet_side = "YES" if directional_yes > directional_no else ("NO" if directional_no > directional_yes else ("YES" if yc >= nc else "NO"))
        market_entry = _entry_price((prof.get("marketEntries") or {}).get(cond), wallet_side)
        if market_entry is None:
            for m_rec in (prof.get("audit", {}).get("markets") or []):
                if m_rec.get("conditionId") == cond:
                    market_entry = _entry_price(m_rec, wallet_side)
                    break
        cur_px = p0 if wallet_side == "YES" else p1
        
        slippage_cents = None
        tail_status = None
        if (qualified or is_candidate or is_underdog) and market_entry is not None and cur_px is not None:
            slippage_cents = _slippage(market_entry, cur_px)
            tail_status = _tail_status(slippage_cents)

        top_wallets.append(
            {
                "address": addr,
                "name": prof.get("name"),
                "pseudonym": prof.get("pseudonym"),
                "primary": primary,
                "category": primary,
                "labels": labels,
                "smartScore": sc,
                "qualified": qualified,
                "isSharp": qualified,
                "isCandidate": is_candidate,
                "isUnderdog": is_underdog,
                "signalNote": "Qualified sports track record; paired shares excluded" if qualified else ((f"Disciplined profitable bettor on watch ({cand_scope})" if cand_scope else "Disciplined profitable bettor on watch") if is_candidate else "Sports track record, holding or bot requirements not met"),
                "scope": ranking_scope or cand_scope or underdog_scope or scope,
                "scoreNote": prof.get("scoreNote"),
                "profileUpdatedAt": prof.get("updatedAt"),
                "profileStale": i in stale,
                "holdingCoverage": prof.get("evidence", {}).get("holding", {}).get("coverage"),
                "capitalHoldRatio": stats.get("capital_hold_ratio"),
                "confidence": prof.get("confidence"),
                "side": wallet_side,
                "capital": round(total_cap, 2),
                "directionalCapital": round(directional_yes if wallet_side == "YES" else directional_no, 2),
                "directionalShares": max(0, cap["yesShares"] - cap["noShares"]) if wallet_side == "YES" else max(0, cap["noShares"] - cap["yesShares"]),
                "yesShares": cap["yesShares"], "noShares": cap["noShares"],
                "signalWeight": q if qualified else .50 if is_candidate else .35 if is_underdog else 0,
                "entryPrice": round(market_entry, 3) if market_entry is not None else None,
                "currentPrice": round(cur_px, 3) if cur_px is not None else None,
                "slippageCents": slippage_cents,
                "tailStatus": tail_status,
                "performanceScope": performance_scope,
                "winrate": record.get("winrate"),
                "trueWinrate": record.get("winrate"),
                "settledBets": record.get("events", 0),
                "netRealized": record.get("profit"),
                "roi": record.get("roi"),
                "holdRatio": stats.get("hold_ratio", 0),
                "realizedPnl": record.get("profit"),
            }
        )

    top_wallets.sort(key=lambda w: w["capital"], reverse=True)

    denom = smart_yes + smart_no
    strength_yes = round(100 * smart_yes / denom, 1) if denom > 0 else None
    strength_no = round(100 * smart_no / denom, 1) if denom > 0 else None
    net_lean = round(strength_yes - strength_no, 1) if denom > 0 else None
    lean_side = "UNAVAILABLE" if net_lean is None else ("YES" if net_lean > NEUTRAL_BAND else ("NO" if net_lean < -NEUTRAL_BAND else "NEUTRAL"))

    comb_denom = comb_smart_yes + comb_smart_no
    comb_strength_yes = round(100 * comb_smart_yes / comb_denom, 1) if comb_denom > 0 else None
    comb_strength_no = round(100 * comb_smart_no / comb_denom, 1) if comb_denom > 0 else None
    comb_net_lean = round(comb_strength_yes - comb_strength_no, 1) if comb_denom > 0 else None
    comb_lean_side = "UNAVAILABLE" if comb_net_lean is None else ("YES" if comb_net_lean > NEUTRAL_BAND else ("NO" if comb_net_lean < -NEUTRAL_BAND else "NEUTRAL"))

    # Tailing Consensus Calculation (Sharps + Candidates + Underdogs as Smart Money)
    sharp_wallets_yes = [w for w in top_wallets if w["qualified"] and w["side"] == "YES" and w["directionalCapital"] > 0]
    sharp_wallets_no = [w for w in top_wallets if w["qualified"] and w["side"] == "NO" and w["directionalCapital"] > 0]
    cand_wallets_yes = [w for w in top_wallets if w.get("isCandidate") and w["side"] == "YES" and w["directionalCapital"] > 0]
    cand_wallets_no = [w for w in top_wallets if w.get("isCandidate") and w["side"] == "NO" and w["directionalCapital"] > 0]
    underdog_wallets_yes = [w for w in top_wallets if w.get("isUnderdog") and w["side"] == "YES" and w["directionalCapital"] > 0]
    underdog_wallets_no = [w for w in top_wallets if w.get("isUnderdog") and w["side"] == "NO" and w["directionalCapital"] > 0]

    smart_wallets_yes = sharp_wallets_yes + cand_wallets_yes + underdog_wallets_yes
    smart_wallets_no = sharp_wallets_no + cand_wallets_no + underdog_wallets_no

    sharp_count_yes = len(sharp_wallets_yes)
    sharp_count_no = len(sharp_wallets_no)
    cand_count_yes = len(cand_wallets_yes)
    cand_count_no = len(cand_wallets_no)
    smart_count_yes = len(smart_wallets_yes)
    smart_count_no = len(smart_wallets_no)

    smart_cap_yes = sum(w["directionalCapital"] for w in smart_wallets_yes)
    smart_cap_no = sum(w["directionalCapital"] for w in smart_wallets_no)

    entry_yes_list = [w["entryPrice"] * w["directionalCapital"] for w in smart_wallets_yes if w["entryPrice"] is not None]
    cap_yes_list = [w["directionalCapital"] for w in smart_wallets_yes if w["entryPrice"] is not None]
    avg_smart_entry_yes = round(sum(entry_yes_list) / sum(cap_yes_list), 3) if cap_yes_list and sum(cap_yes_list) > 0 else None

    entry_no_list = [w["entryPrice"] * w["directionalCapital"] for w in smart_wallets_no if w["entryPrice"] is not None]
    cap_no_list = [w["directionalCapital"] for w in smart_wallets_no if w["entryPrice"] is not None]
    avg_smart_entry_no = round(sum(entry_no_list) / sum(cap_no_list), 3) if cap_no_list and sum(cap_no_list) > 0 else None

    # Fallback to sharp-only if available
    s_entry_yes_list = [w["entryPrice"] * w["directionalCapital"] for w in sharp_wallets_yes if w["entryPrice"] is not None]
    s_cap_yes_list = [w["directionalCapital"] for w in sharp_wallets_yes if w["entryPrice"] is not None]
    avg_entry_yes = round(sum(s_entry_yes_list) / sum(s_cap_yes_list), 3) if s_cap_yes_list and sum(s_cap_yes_list) > 0 else None

    s_entry_no_list = [w["entryPrice"] * w["directionalCapital"] for w in sharp_wallets_no if w["entryPrice"] is not None]
    s_cap_no_list = [w["directionalCapital"] for w in sharp_wallets_no if w["entryPrice"] is not None]
    avg_entry_no = round(sum(s_entry_no_list) / sum(s_cap_no_list), 3) if s_cap_no_list and sum(s_cap_no_list) > 0 else None

    tail_wallets = [w for w in top_wallets if (w["qualified"] or w.get("isCandidate") or w.get("isUnderdog")) and w.get("tailStatus") in ("BETTER_PRICE", "PRIME_TAIL", "ACCEPTABLE")]

    outcomes = market.get("outcomes") or ["Yes", "No"]
    name_yes = outcomes[0] if outcomes else "Yes"
    name_no = outcomes[1] if len(outcomes) > 1 else "No"

    # Pick generation is shared by fresh results and cached responses.
    sharp_pick = comb_pick = pick = None
    tail_verdict = "No directional smart consensus in the available results"
    tail_intelligence = {}

    # Deep Alpha: smart-money lean disagrees with the market-implied favorite
    market_fav = "YES" if p0 > 0.5 else ("NO" if p0 < 0.5 else "NEUTRAL")
    fav_price = max(p0, 1 - p0)
    divergent = (
        lean_side in ("YES", "NO")
        and market_fav in ("YES", "NO")
        and lean_side != market_fav
        and fav_price >= 0.60
    )
    alpha = {
        "divergent": divergent,
        "side": lean_side if divergent else None,
        "marketFavorite": market_fav,
        "favPrice": round(fav_price, 3),
        "edge": round(abs(net_lean), 1) if divergent and net_lean is not None else 0.0,
    }

    comb_divergent = (
        comb_lean_side in ("YES", "NO")
        and market_fav in ("YES", "NO")
        and comb_lean_side != market_fav
        and fav_price >= 0.60
    )
    comb_alpha = {
        "divergent": comb_divergent,
        "side": comb_lean_side if comb_divergent else None,
        "marketFavorite": market_fav,
        "favPrice": round(fav_price, 3),
        "edge": round(abs(comb_net_lean), 1) if comb_divergent and comb_net_lean is not None else 0.0,
    }

    total_cat = sum(cat_capital.values()) or 1.0
    capital_distribution = sorted(
        [
            {"category": c, "capital": round(v, 2), "pct": round(v / total_cat * 100, 1)}
            for c, v in cat_capital.items()
        ],
        key=lambda x: x["capital"],
        reverse=True,
    )

    hold_ratio = round(hold_wsum / hold_total, 3) if hold_total > 0 else None
    sharp_count = sum(1 for w in top_wallets if w["qualified"])

    combined_data = {
        "leanSide": comb_lean_side,
        "netLean": comb_net_lean,
        "strengthYes": comb_strength_yes,
        "strengthNo": comb_strength_no,
        "sharpCount": sharp_count,
        "candidateCount": candidate_count,
        "underdogCount": underdog_count,
        "smartCount": sharp_count + candidate_count + underdog_count,
        "smartCapitalYes": round(comb_cap_yes, 2),
        "smartCapitalNo": round(comb_cap_no, 2),
        "alpha": comb_alpha,
        "pick": comb_pick,
    }

    coverage_detail = {"sampledCapital": round(sampled_capital, 2), "failedWallets": failed_wallets,
        "pendingWallets": pending, "cachedWallets": cached,
        "qualifiedCapital": round(sharp_cap_yes + sharp_cap_no, 2),
        "qualifiedCapitalPct": round((sharp_cap_yes + sharp_cap_no) / sampled_capital * 100, 1) if sampled_capital else 0,
        "holdingCapitalPct": round(hold_total / sampled_capital * 100, 1) if sampled_capital else 0}

    summary = {
        "conditionId": cond,
        "schemaVersion": ANALYSIS_SCHEMA,
        "configSignature": config_signature,
        "coverage": "Sample of top holders; not all market capital",
        "question": market.get("question"),
        "marketType": market.get("marketType"),
        "outcomes": market.get("outcomes"),
        "prices": prices,
        "leanSide": lean_side,
        "netLean": net_lean,
        "strengthYes": strength_yes,
        "strengthNo": strength_no,
        "participantCount": len(top_wallets),
        "sharpCount": sharp_count,
        "candidateCount": candidate_count,
        "sharpCapitalYes": round(sharp_cap_yes, 2),
        "sharpCapitalNo": round(sharp_cap_no, 2),
        "holdRatio": hold_ratio,
        "alpha": alpha,
        "topCategories": capital_distribution[:3],
        "pick": sharp_pick or comb_pick,
        "sharpPick": sharp_pick,
        "tailIntelligence": tail_intelligence,
        "tailVerdict": tail_verdict,
        "combined": combined_data,
    }

    intel = _fallback(summary)

    analysis = {
        "conditionId": cond,
        "schemaVersion": ANALYSIS_SCHEMA,
        "configSignature": config_signature,
        "coverage": "Sample of top holders; not all market capital",
        "coverageDetail": coverage_detail,
        "sampledHolderShares": [sum(cap["yesShares"] for _, cap in ranked), sum(cap["noShares"] for _, cap in ranked)],
        "isPartial": bool(pending or failed),
        "pendingWallets": pending,
        "cachedWallets": cached,
        "qualificationScope": "Sports track record (overall or this sport); net of observed paired shares",
        "signalStatus": "measured" if denom > 0 else "no_qualified_sharps",
        "question": market.get("question"),
        "marketType": market.get("marketType"),
        "eventTitle": market.get("eventTitle"),
        "category": market.get("category"),
        "outcomes": market.get("outcomes"),
        "prices": prices,
        "strengthYes": strength_yes,
        "strengthNo": strength_no,
        "netLean": net_lean,
        "leanSide": lean_side,
        "smartCapitalYes": round(sharp_cap_yes, 2),
        "smartCapitalNo": round(sharp_cap_no, 2),
        "capitalDistribution": capital_distribution,
        "holdToResolution": {
            "ratio": hold_ratio,
            "label": _hold_label(hold_ratio),
            "estimated": True,
        },
        "participantCount": len(top_wallets),
        "sharpCount": sharp_count,
        "candidateCount": candidate_count,
        "alpha": alpha,
        "topWallets": top_wallets,
        "intel": intel,
        "pick": sharp_pick or comb_pick,
        "sharpPick": sharp_pick,
        "tailIntelligence": tail_intelligence,
        "tailVerdict": tail_verdict,
        "combined": combined_data,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }

    return refresh_analysis_prices(analysis, market)


def _hold_label(ratio):
    if ratio is None:
        return "Unknown holding ratio"
    pct = int(round(ratio * 100))
    if ratio >= 0.90:
        return f"{pct}% held or cashed out at 97¢+"
    if ratio >= 0.60:
        return f"Moderate holding · {pct}% holds to resolution"
    return f"Active trader · exits early {100 - pct}% of the time"


def compute_pick_from_doc(doc, market=None, mode="combined"):
    if not doc or not isinstance(doc, dict):
        return None
    wallets = doc.get("topWallets") or []
    if not wallets:
        if mode == "sharp":
            return doc.get("sharpPick")
        return (doc.get("combined") or {}).get("pick") or doc.get("pick") or (doc.get("tailIntelligence") or {}).get("pick")

    prices = (market or {}).get("prices") or doc.get("prices") or [0.5, 0.5]
    p0 = prices[0] if len(prices) > 0 else 0.5
    p1 = prices[1] if len(prices) > 1 else (1.0 - p0)
    outcomes = (market or {}).get("outcomes") or doc.get("outcomes") or ["Yes", "No"]
    name_yes = outcomes[0] if outcomes else "Yes"
    name_no = outcomes[1] if len(outcomes) > 1 else "No"

    lean_side = doc.get("leanSide")
    comb = doc.get("combined") or {}
    comb_lean_side = comb.get("leanSide")

    sides_switched = (
        lean_side in ("YES", "NO")
        and (
            (comb_lean_side in ("YES", "NO") and lean_side != comb_lean_side)
            or comb_lean_side == "NEUTRAL"
        )
    )

    sharp_yes = [w for w in wallets if w.get("qualified") and w.get("side") == "YES" and w.get("directionalCapital", 0) > 0]
    sharp_no = [w for w in wallets if w.get("qualified") and w.get("side") == "NO" and w.get("directionalCapital", 0) > 0]
    cand_yes = [w for w in wallets if w.get("isCandidate") and w.get("side") == "YES" and w.get("directionalCapital", 0) > 0]
    cand_no = [w for w in wallets if w.get("isCandidate") and w.get("side") == "NO" and w.get("directionalCapital", 0) > 0]

    under_yes = [w for w in wallets if w.get("isUnderdog") and w.get("side") == "YES" and w.get("directionalCapital", 0) > 0]
    under_no = [w for w in wallets if w.get("isUnderdog") and w.get("side") == "NO" and w.get("directionalCapital", 0) > 0]

    if mode == "sharp":
        pick_wallets_yes = sharp_yes
        pick_wallets_no = sharp_no
        effective_lean = lean_side
    else:
        pick_wallets_yes = sharp_yes + cand_yes + under_yes
        pick_wallets_no = sharp_no + cand_no + under_no
        effective_lean = comb_lean_side

    cap_yes = sum(w["directionalCapital"] for w in pick_wallets_yes)
    cap_no = sum(w["directionalCapital"] for w in pick_wallets_no)

    entry_yes_list = [w["entryPrice"] * w["directionalCapital"] for w in pick_wallets_yes if w.get("entryPrice") is not None]
    c_yes_list = [w["directionalCapital"] for w in pick_wallets_yes if w.get("entryPrice") is not None]
    avg_entry_yes = round(sum(entry_yes_list) / sum(c_yes_list), 3) if c_yes_list and sum(c_yes_list) > 0 else None

    entry_no_list = [w["entryPrice"] * w["directionalCapital"] for w in pick_wallets_no if w.get("entryPrice") is not None]
    c_no_list = [w["directionalCapital"] for w in pick_wallets_no if w.get("entryPrice") is not None]
    avg_entry_no = round(sum(entry_no_list) / sum(c_no_list), 3) if c_no_list and sum(c_no_list) > 0 else None

    pick_side = None
    if effective_lean in ("YES", "NO"):
        if effective_lean == "YES" and cap_yes > 0:
            pick_side = "YES"
        elif effective_lean == "NO" and cap_no > 0:
            pick_side = "NO"
    elif effective_lean in (None, "UNAVAILABLE"):
        if cap_yes > cap_no and cap_yes > 0:
            pick_side = "YES"
        elif cap_no > cap_yes and cap_no > 0:
            pick_side = "NO"

    if not pick_side:
        return None

    entry = avg_entry_yes if pick_side == "YES" else avg_entry_no
    chosen_capital = cap_yes if pick_side == "YES" else cap_no
    known_capital = sum(c_yes_list) if pick_side == "YES" else sum(c_no_list)
    entry_coverage = min(1., known_capital / chosen_capital) if chosen_capital else 0
    slip = ((p0 if pick_side == "YES" else p1) - entry) if entry is not None and entry_coverage >= 1 - 1e-6 else None
    sharp_cnt = len(sharp_yes) if pick_side == "YES" else len(sharp_no)
    cand_cnt = len(cand_yes) if pick_side == "YES" else len(cand_no)
    under_cnt = (len(under_yes) if pick_side == "YES" else len(under_no)) if mode != "sharp" else 0
    smart_cnt = len(pick_wallets_yes) if pick_side == "YES" else len(pick_wallets_no)
    smart_cap = cap_yes if pick_side == "YES" else cap_no
    chosen_name = name_yes if pick_side == "YES" else name_no

    if sides_switched:
        conviction = "CONFLICT"
        if comb_lean_side == "NEUTRAL":
            verdict = f"SPLIT CONSENSUS: Sharps favor {chosen_name} ({round(doc.get('strengthYes' if pick_side == 'YES' else 'strengthNo') or 100)}%), but Candidates and Underdogs pull net smart lean into a dead heat ({comb.get('netLean')}%). No solid pick."
        else:
            verdict = f"SPLIT CONSENSUS: Net smart lean switches sides between Sharps ({lean_side}) and Sharps + Candidates + Underdogs ({comb_lean_side}). No solid pick."
    elif slip is not None and slip > 0.07:
        conviction = "CAUTION"
        verdict = f"CAUTION: Line moved to {round((p0 if pick_side == 'YES' else p1)*100)}¢"
    elif (sharp_cnt >= 1 and (smart_cnt >= 2 or smart_cap >= 1500)) or (mode == "sharp" and sharp_cnt >= 1 and smart_cap >= 1500):
        conviction = "HIGH"
        verdict = f"Smart Money ({sharp_cnt} Sharp, {cand_cnt if mode != 'sharp' else 0} Candidate, {under_cnt} Underdog) backing {chosen_name} (${round(smart_cap):,})"
    elif smart_cnt >= 1:
        conviction = "MODERATE"
        verdict = f"Smart Money ({sharp_cnt} Sharp, {cand_cnt if mode != 'sharp' else 0} Candidate, {under_cnt} Underdog) backing {chosen_name} (${round(smart_cap):,})"
    else:
        conviction = "LEAN"
        verdict = f"Smart lean on {chosen_name}"

    slip_cents = round(slip * 100, 1) if slip is not None else None

    conflict_reason = (
        f"Net smart lean deadlocks into a split ({comb.get('netLean')}%) when Candidates and Underdogs oppose Sharps ({lean_side})"
        if comb_lean_side == "NEUTRAL"
        else f"Net smart lean switches sides between Sharps ({lean_side}) and Sharps + Candidates + Underdogs ({comb_lean_side})"
    ) if sides_switched else None

    return {
        "side": pick_side,
        "outcome": chosen_name,
        "conviction": conviction,
        "isConflict": sides_switched,
        "conflictReason": conflict_reason,
        "sharpCount": sharp_cnt,
        "candidateCount": cand_cnt if mode != "sharp" else 0,
        "underdogCount": under_cnt,
        "smartCount": smart_cnt,
        "smartCapital": round(smart_cap, 2),
        "avgEntry": entry,
        "entryCoverage": round(entry_coverage, 4),
        "entryStatus": "known" if entry_coverage >= 1 - 1e-6 else "partial" if entry is not None else "unknown",
        "currentPrice": round(p0 if pick_side == "YES" else p1, 3),
        "slippageCents": slip_cents,
        "verdict": verdict,
    }


def refresh_analysis_prices(doc, market=None):
    """Revalue saved holder quantities using the available market snapshot.

    Qualification is preserved; quantities remain observations from updatedAt.
    Strength, counts, picks and tail comparisons share the same cohorts.
    """
    result = copy.deepcopy(doc)
    prices = (market or {}).get("prices") or result.get("prices") or [.5, .5]
    result["prices"] = prices
    if (market or {}).get("outcomes"):
        result["outcomes"] = market["outcomes"]
    rows = result.get("topWallets") or []
    for row in rows:
        idx = 0 if row.get("side") == "YES" else 1
        current = _f(prices[idx])
        if row.get("directionalShares") is not None:
            row["directionalCapital"] = round(row["directionalShares"] * current, 2)
        if "yesShares" in row and "noShares" in row:
            row["capital"] = round(row["yesShares"] * _f(prices[0]) + row["noShares"] * _f(prices[1]), 2)
        row["currentPrice"] = current
        row["entryPrice"] = _entry_price(row, row.get("side"))
        row["slippageCents"] = _slippage(row["entryPrice"], current)
        row["tailStatus"] = _tail_status(row["slippageCents"]) if any(row.get(k) for k in ("qualified", "isCandidate", "isUnderdog")) else None

    directional = [w for w in rows if w.get("directionalCapital", 0) > 0]
    sharps = [w for w in directional if w.get("qualified")]
    candidates = [w for w in directional if w.get("isCandidate") and not w.get("qualified")]
    underdogs = [w for w in directional if w.get("isUnderdog") and not w.get("qualified") and not w.get("isCandidate")]
    smart = sharps + candidates + underdogs

    def weight(w):
        return w.get("signalWeight", (_f(w.get("smartScore")) / 100 if w.get("qualified") else .5 if w.get("isCandidate") else .35))

    def metrics(cohort):
        raw = [sum(w["directionalCapital"] for w in cohort if w["side"] == side) for side in ("YES", "NO")]
        weighted = [sum(w["directionalCapital"] * weight(w) for w in cohort if w["side"] == side) for side in ("YES", "NO")]
        total = sum(weighted)
        strength = [round(100 * v / total, 1) if total else None for v in weighted]
        net = round(strength[0] - strength[1], 1) if total else None
        lean = "UNAVAILABLE" if net is None else "YES" if net > NEUTRAL_BAND else "NO" if net < -NEUTRAL_BAND else "NEUTRAL"
        fav = "YES" if prices[0] > .5 else "NO" if prices[0] < .5 else "NEUTRAL"
        divergent = lean in {"YES", "NO"} and fav in {"YES", "NO"} and lean != fav and max(prices) >= .6
        return dict(smartCapitalYes=round(raw[0], 2), smartCapitalNo=round(raw[1], 2),
            strengthYes=strength[0], strengthNo=strength[1], netLean=net, leanSide=lean,
            alpha=dict(divergent=divergent, side=lean if divergent else None,
                marketFavorite=fav, favPrice=round(max(prices), 3), edge=abs(net) if divergent else 0))

    result.update(metrics(sharps), sharpCount=len(sharps), candidateCount=len(candidates), underdogCount=len(underdogs))
    categories = {}
    for row in rows:
        categories[row["primary"]] = categories.get(row["primary"], 0) + row.get("capital", 0)
    total_capital = sum(categories.values())
    result["capitalDistribution"] = sorted([{"category":key, "capital":round(value, 2),
        "pct":round(value / total_capital * 100, 1) if total_capital else 0}
        for key, value in categories.items()], key=lambda row:row["capital"], reverse=True)
    hold_rows = [w for w in rows if w.get("capitalHoldRatio") is not None]
    hold_capital = sum(w.get("capital", 0) for w in hold_rows)
    hold_ratio = sum(w["capitalHoldRatio"] * w.get("capital", 0) for w in hold_rows) / hold_capital if hold_capital else None
    result["holdToResolution"] = {"ratio":round(hold_ratio, 3) if hold_ratio is not None else None,
        "label":_hold_label(hold_ratio), "estimated":True,
        "policy":"Held through resolution or qualifying cashouts at 97 cents or above"}
    coverage = result.get("coverageDetail") or {}
    qualified_capital = sum(w["directionalCapital"] for w in sharps)
    sampled_shares = result.get("sampledHolderShares")
    if sampled_shares:
        coverage["sampledCapital"] = round(sampled_shares[0] * prices[0] + sampled_shares[1] * prices[1], 2)
    sampled_capital = coverage.get("sampledCapital", 0)
    coverage.update(qualifiedCapital=round(qualified_capital, 2),
        qualifiedCapitalPct=round(qualified_capital / sampled_capital * 100, 1) if sampled_capital else 0,
        holdingCapitalPct=round(hold_capital / sampled_capital * 100, 1) if sampled_capital else 0)
    result["coverageDetail"] = coverage
    result["combined"] = {**(result.get("combined") or {}), **metrics(smart),
        "sharpCount":len(sharps), "candidateCount":len(candidates), "underdogCount":len(underdogs), "smartCount":len(smart)}
    result["sharpPick"] = compute_pick_from_doc(result, mode="sharp")
    result["combined"]["pick"] = compute_pick_from_doc(result, mode="combined")
    result["pick"] = result["sharpPick"] or result["combined"]["pick"]
    result["tailVerdict"] = (result["pick"] or {}).get("verdict") or "No directional smart consensus in the available results"
    tail = result["tailIntelligence"] = {}
    for side in ("YES", "NO"):
        suffix = side.title()
        side_smart = [w for w in smart if w["side"] == side]
        known = [w for w in side_smart if w.get("entryPrice") is not None]
        capital = sum(w["directionalCapital"] for w in side_smart)
        known_capital = sum(w["directionalCapital"] for w in known)
        avg = round(sum(w["entryPrice"] * w["directionalCapital"] for w in known) / known_capital, 3) if known_capital else None
        side_sharp = [w for w in sharps if w["side"] == side]
        sharp_known = [w for w in side_sharp if w.get("entryPrice") is not None]
        sharp_known_cap = sum(w["directionalCapital"] for w in sharp_known)
        sharp_avg = round(sum(w["entryPrice"] * w["directionalCapital"] for w in sharp_known) / sharp_known_cap, 3) if sharp_known_cap else None
        tail.update({"smartCount"+suffix:len(side_smart), "sharpCount"+suffix:len(side_sharp),
            "candidateCount"+suffix:sum(w in candidates for w in side_smart),
            "underdogCount"+suffix:sum(w in underdogs for w in side_smart),
            "smartCapital"+suffix:round(capital, 2), "sharpCapital"+suffix:round(sum(w["directionalCapital"] for w in side_sharp), 2),
            "avgSmartEntry"+suffix:avg, "avgSharpEntry"+suffix:sharp_avg,
            "currentPrice"+suffix:prices[0 if side == "YES" else 1],
            "slippage"+suffix+"Cents":_slippage(avg, prices[0 if side == "YES" else 1]) if capital and known_capital >= capital - 1e-6 else None})
    tailable = [w for w in smart if w.get("tailStatus") in {"BETTER_PRICE", "PRIME_TAIL", "ACCEPTABLE"}]
    tail.update(pick=result["pick"], verdict=result["tailVerdict"], tailableCount=len(tailable), tailableWallets=tailable)
    result["intel"] = _fallback({**result, "holdRatio":result.get("holdToResolution", {}).get("ratio"),
        "topCategories":result.get("capitalDistribution", [])[:3]})
    return result


def analysis_summary(doc):
    if not doc:
        return None
    sharp_pick = compute_pick_from_doc(doc, mode="sharp") if doc.get("topWallets") else doc.get("sharpPick")
    comb_pick = compute_pick_from_doc(doc, mode="combined") if doc.get("topWallets") else (doc.get("combined") or {}).get("pick")
    comb = dict(doc.get("combined") or {
        "strengthYes": doc.get("strengthYes"),
        "strengthNo": doc.get("strengthNo"),
        "netLean": doc.get("netLean"),
        "leanSide": doc.get("leanSide"),
        "sharpCount": doc.get("sharpCount", 0),
        "candidateCount": doc.get("candidateCount", 0),
        "underdogCount": doc.get("underdogCount", 0),
        "smartCount": (doc.get("sharpCount") or 0) + (doc.get("candidateCount") or 0),
        "smartCapitalYes": doc.get("smartCapitalYes", 0),
        "smartCapitalNo": doc.get("smartCapitalNo", 0),
        "alpha": doc.get("alpha"),
    })
    comb["pick"] = comb_pick

    tail_verdict = (doc.get("tailIntelligence") or {}).get("verdict") or doc.get("tailVerdict")
    if sharp_pick and sharp_pick.get("isConflict"):
        tail_verdict = sharp_pick.get("verdict")

    return {
        "schemaVersion": doc.get("schemaVersion"),
        "isPartial": doc.get("isPartial", False),
        "pendingWallets": doc.get("pendingWallets", 0),
        "cachedWallets": doc.get("cachedWallets", 0),
        "signalStatus": doc.get("signalStatus"),
        "coverageDetail": doc.get("coverageDetail"),
        "configSignature": doc.get("configSignature"),
        "strengthYes": doc.get("strengthYes"),
        "strengthNo": doc.get("strengthNo"),
        "netLean": doc.get("netLean"),
        "leanSide": doc.get("leanSide"),
        "participantCount": doc.get("participantCount"),
        "sharpCount": doc.get("sharpCount"),
        "candidateCount": doc.get("candidateCount", 0),
        "pick": sharp_pick or comb_pick,
        "sharpPick": sharp_pick,
        "alpha": doc.get("alpha"),
        "holdToResolution": doc.get("holdToResolution"),
        "topCategory": (doc.get("capitalDistribution") or [{}])[0].get("category"),
        "tailIntelligence": doc.get("tailIntelligence"),
        "tailVerdict": tail_verdict,
        "smartCapitalYes": doc.get("smartCapitalYes"),
        "smartCapitalNo": doc.get("smartCapitalNo"),
        "updatedAt": doc.get("updatedAt"),
        "combined": comb,
    }
