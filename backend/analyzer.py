"""Market-level smart-money analysis.

Strength = SHARE OF SMART-MONEY CAPITAL on each side (capital weighted by wallet
quality), NOT the average wallet quality — so a near-worthless longshot side
correctly reads ~0 even if a couple of sharp wallets hold a tiny amount.
"""
import asyncio
from weakref import WeakValueDictionary
from datetime import datetime, timezone

from classifier import analyze_wallet, WALLET_SCHEMA, categorize_market
from sharp_evidence import QUALIFIED, qualified_market_scope, candidate_market_scope
from evidence_store import record_observation

ANALYSIS_SCHEMA = 23
from ai_narrative import generate_intel, _fallback
from config import signature

WALLET_TTL_SECONDS = 3600
MAX_WALLETS_PER_MARKET = 26
WALLET_ANALYSIS_TIMEOUT = 75
NEUTRAL_BAND = 4.0

_wallet_locks = WeakValueDictionary()
_wallet_classify_sem = asyncio.Semaphore(5)


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
            analysis["intel"] = await asyncio.wait_for(generate_intel(_intel_summary(analysis)), timeout=5)
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
    comb_smart_yes = comb_smart_no = 0.0  # quality-weighted capital (Sharps + Candidates)
    comb_cap_yes = comb_cap_no = 0.0      # raw capital (Sharps + Candidates)
    candidate_cap_yes = candidate_cap_no = 0.0
    candidate_count = 0
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
        qualified = ranking_scope is not None or (prof.get("primary") in QUALIFIED and not prof.get("isBot"))
        cand_scope = candidate_market_scope(prof, scope)
        is_candidate = cand_scope is not None and not qualified
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
        primary = prof.get("primary") or prof.get("category", "INSUFFICIENT_DATA")
        if primary in QUALIFIED and not qualified:
            primary = scoped.get("category", "INSUFFICIENT_DATA")
        labels = [primary] + (["WHALE"] if prof.get("isWhale") else [])
        stats = prof.get("stats", {})
        record = scoped if qualified else prof.get("evidence", {}).get("scopes", {}).get("Sports", {})
        cat_capital[primary] = cat_capital.get(primary, 0.0) + total_cap
        observed_hold = stats.get("capital_hold_ratio")
        if observed_hold is not None:
            hold_wsum += observed_hold * total_cap
            hold_total += total_cap

        # Tailability & Entry price tracking
        market_entry = (prof.get("marketEntries") or {}).get(cond, {}).get("entryPrice")
        if market_entry is None:
            for m_rec in (prof.get("audit", {}).get("markets") or []):
                if m_rec.get("conditionId") == cond and m_rec.get("entryPrice"):
                    market_entry = _f(m_rec.get("entryPrice"))
                    break
        if market_entry is None:
            denom_sh = cap["yesShares"] if yc >= nc else cap["noShares"]
            if denom_sh and denom_sh > 0:
                market_entry = round((yc if yc >= nc else nc) / denom_sh, 4)

        wallet_side = "YES" if directional_yes > directional_no else ("NO" if directional_no > directional_yes else ("YES" if yc >= nc else "NO"))
        cur_px = p0 if wallet_side == "YES" else p1
        
        slippage_cents = None
        tail_status = None
        if (qualified or is_candidate) and market_entry is not None and cur_px is not None:
            slippage_cents = round(cur_px - market_entry, 3)
            if slippage_cents <= 0.0:
                tail_status = "BETTER_PRICE"
            elif slippage_cents <= 0.03:
                tail_status = "PRIME_TAIL"
            elif slippage_cents <= 0.07:
                tail_status = "ACCEPTABLE"
            else:
                tail_status = "LINE_MOVED"

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
                "signalNote": "Qualified sports track record; paired shares excluded" if qualified else ((f"Disciplined profitable bettor on watch ({cand_scope})" if cand_scope else "Disciplined profitable bettor on watch") if is_candidate else "Sports track record, holding or bot requirements not met"),
                "scope": ranking_scope or cand_scope or scope,
                "scoreNote": prof.get("scoreNote"),
                "profileUpdatedAt": prof.get("updatedAt"),
                "profileStale": i in stale,
                "holdingCoverage": prof.get("evidence", {}).get("holding", {}).get("coverage"),
                "capitalHoldRatio": stats.get("capital_hold_ratio"),
                "confidence": prof.get("confidence"),
                "side": wallet_side,
                "capital": round(total_cap, 2),
                "directionalCapital": round(directional_yes if wallet_side == "YES" else directional_no, 2),
                "entryPrice": round(market_entry, 3) if market_entry is not None else None,
                "currentPrice": round(cur_px, 3) if cur_px is not None else None,
                "slippageCents": slippage_cents,
                "tailStatus": tail_status,
                "performanceScope": ranking_scope if qualified else "Sports",
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

    # Tailing Consensus Calculation (Sharps + Candidates as Smart Money)
    sharp_wallets_yes = [w for w in top_wallets if w["qualified"] and w["side"] == "YES" and w["directionalCapital"] > 0]
    sharp_wallets_no = [w for w in top_wallets if w["qualified"] and w["side"] == "NO" and w["directionalCapital"] > 0]
    cand_wallets_yes = [w for w in top_wallets if w.get("isCandidate") and w["side"] == "YES" and w["directionalCapital"] > 0]
    cand_wallets_no = [w for w in top_wallets if w.get("isCandidate") and w["side"] == "NO" and w["directionalCapital"] > 0]

    smart_wallets_yes = sharp_wallets_yes + cand_wallets_yes
    smart_wallets_no = sharp_wallets_no + cand_wallets_no

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
    avg_entry_yes = round(sum(s_entry_yes_list) / sum(s_cap_yes_list), 3) if s_cap_yes_list and sum(s_cap_yes_list) > 0 else avg_smart_entry_yes

    s_entry_no_list = [w["entryPrice"] * w["directionalCapital"] for w in sharp_wallets_no if w["entryPrice"] is not None]
    s_cap_no_list = [w["directionalCapital"] for w in sharp_wallets_no if w["entryPrice"] is not None]
    avg_entry_no = round(sum(s_entry_no_list) / sum(s_cap_no_list), 3) if s_cap_no_list and sum(s_cap_no_list) > 0 else avg_smart_entry_no

    tail_wallets = [w for w in top_wallets if (w["qualified"] or w.get("isCandidate")) and w.get("tailStatus") in ("BETTER_PRICE", "PRIME_TAIL", "ACCEPTABLE")]

    outcomes = market.get("outcomes") or ["Yes", "No"]
    name_yes = outcomes[0] if outcomes else "Yes"
    name_no = outcomes[1] if len(outcomes) > 1 else "No"

    # Check whether net smart lean switches sides between Sharps Only and Sharps + Candidates
    sides_switched = (
        lean_side in ("YES", "NO")
        and comb_lean_side in ("YES", "NO")
        and lean_side != comb_lean_side
    )

    # 1. SHARP-ONLY PICK
    sharp_pick_side = None
    if (lean_side == "YES" or lean_side is None) and sharp_cap_yes > sharp_cap_no and sharp_cap_yes > 0:
        sharp_pick_side = "YES"
    elif (lean_side == "NO" or lean_side is None) and sharp_cap_no > sharp_cap_yes and sharp_cap_no > 0:
        sharp_pick_side = "NO"

    sharp_pick = None
    if sharp_pick_side:
        s_slip = (p0 - (avg_entry_yes or p0)) if sharp_pick_side == "YES" else (p1 - (avg_entry_no or p1))
        s_slip_cents = round(s_slip * 100, 1)
        s_outcome = name_yes if sharp_pick_side == "YES" else name_no
        s_cap = sharp_cap_yes if sharp_pick_side == "YES" else sharp_cap_no
        s_cnt = sharp_count_yes if sharp_pick_side == "YES" else sharp_count_no

        if sides_switched:
            s_conviction = "CONFLICT"
            s_verdict = f"SPLIT CONSENSUS: Sharps favor {s_outcome} ({strength_yes if sharp_pick_side == 'YES' else strength_no}%), but net smart lean switches to {comb_lean_side} when Candidates are included ({comb_net_lean}%). No solid pick."
        elif s_slip > 0.07:
            s_conviction = "CAUTION"
            s_verdict = f"CAUTION: Sharps entered {s_outcome} at {round((avg_entry_yes if sharp_pick_side == 'YES' else avg_entry_no)*100)}¢, but line already moved to {round((p0 if sharp_pick_side == 'YES' else p1)*100)}¢. Do not chase."
        elif s_cnt >= 2 or s_cap >= 2000:
            s_conviction = "HIGH"
            s_verdict = f"{s_cnt} Sharps backing {s_outcome} (${round(s_cap):,})"
        elif s_cnt >= 1:
            s_conviction = "MODERATE"
            s_verdict = f"{s_cnt} Sharp backing {s_outcome} (${round(s_cap):,})"
        else:
            s_conviction = "LEAN"
            s_verdict = f"Sharp lean on {s_outcome}"

        sharp_pick = {
            "side": sharp_pick_side,
            "outcome": s_outcome,
            "conviction": s_conviction,
            "isConflict": sides_switched,
            "conflictReason": f"Net smart lean switches sides between Sharps ({lean_side}) and Sharps + Candidates ({comb_lean_side})" if sides_switched else None,
            "sharpCount": s_cnt,
            "candidateCount": 0,
            "smartCount": s_cnt,
            "smartCapital": round(s_cap, 2),
            "avgEntry": round((avg_entry_yes if sharp_pick_side == "YES" else avg_entry_no) or 0, 3),
            "currentPrice": round(p0 if sharp_pick_side == "YES" else p1, 3),
            "slippageCents": s_slip_cents,
            "verdict": s_verdict,
        }

    # 2. COMBINED PICK (Sharps + Candidates)
    comb_pick_side = None
    if (comb_lean_side == "YES" or comb_lean_side is None) and smart_cap_yes > smart_cap_no and smart_cap_yes > 0:
        comb_pick_side = "YES"
    elif (comb_lean_side == "NO" or comb_lean_side is None) and smart_cap_no > smart_cap_yes and smart_cap_no > 0:
        comb_pick_side = "NO"

    comb_pick = None
    if comb_pick_side:
        c_slip = (p0 - (avg_smart_entry_yes or p0)) if comb_pick_side == "YES" else (p1 - (avg_smart_entry_no or p1))
        c_slip_cents = round(c_slip * 100, 1)
        c_outcome = name_yes if comb_pick_side == "YES" else name_no
        c_cap = smart_cap_yes if comb_pick_side == "YES" else smart_cap_no
        c_cnt = smart_count_yes if comb_pick_side == "YES" else smart_count_no
        c_sharp_cnt = sharp_count_yes if comb_pick_side == "YES" else sharp_count_no
        c_cand_cnt = cand_count_yes if comb_pick_side == "YES" else cand_count_no

        if sides_switched:
            c_conviction = "CONFLICT"
            c_verdict = f"SPLIT CONSENSUS: Candidates pull to {c_outcome} ({comb_strength_yes if comb_pick_side == 'YES' else comb_strength_no}%), but verified Sharps lean {lean_side} ({strength_yes if lean_side == 'YES' else strength_no}%). No solid pick."
        elif c_slip > 0.07:
            c_conviction = "CAUTION"
            c_verdict = f"CAUTION: Smart money entered {c_outcome} at {round((avg_smart_entry_yes if comb_pick_side == 'YES' else avg_smart_entry_no)*100)}¢, but line already moved to {round((p0 if comb_pick_side == 'YES' else p1)*100)}¢. Do not chase."
        elif c_sharp_cnt >= 1 and (c_cnt >= 2 or c_cap >= 1500):
            c_conviction = "HIGH"
            c_verdict = f"Smart Money ({c_sharp_cnt} Sharp, {c_cand_cnt} Candidate) backing {c_outcome} (${round(c_cap):,})"
        elif c_cnt >= 1:
            c_conviction = "MODERATE"
            c_verdict = f"Smart Money ({c_sharp_cnt} Sharp, {c_cand_cnt} Candidate) backing {c_outcome} (${round(c_cap):,})"
        else:
            c_conviction = "LEAN"
            c_verdict = f"Smart lean on {c_outcome}"

        comb_pick = {
            "side": comb_pick_side,
            "outcome": c_outcome,
            "conviction": c_conviction,
            "isConflict": sides_switched,
            "conflictReason": f"Net smart lean switches sides between Sharps ({lean_side}) and Sharps + Candidates ({comb_lean_side})" if sides_switched else None,
            "sharpCount": c_sharp_cnt,
            "candidateCount": c_cand_cnt,
            "smartCount": c_cnt,
            "smartCapital": round(c_cap, 2),
            "avgEntry": round((avg_smart_entry_yes if comb_pick_side == "YES" else avg_smart_entry_no) or 0, 3),
            "currentPrice": round(p0 if comb_pick_side == "YES" else p1, 3),
            "slippageCents": c_slip_cents,
            "verdict": c_verdict,
        }

    # Primary default pick
    pick = sharp_pick or comb_pick

    tail_verdict = (pick or {}).get("verdict") or "NO SMART MONEY DETECTED: Order book currently led by casuals / market makers."
    tail_verdict_key = "SPLIT_CONSENSUS" if sides_switched else ("SMART_PICK" if pick else "NO_SMART_CONSENSUS")
    slip_yes_cents = round((p0 - (avg_smart_entry_yes or p0)) * 100, 1) if avg_smart_entry_yes is not None else None
    slip_no_cents = round((p1 - (avg_smart_entry_no or p1)) * 100, 1) if avg_smart_entry_no is not None else None

    tail_intelligence = {
        "pick": pick,
        "sharpCountYes": sharp_count_yes,
        "sharpCountNo": sharp_count_no,
        "candidateCountYes": cand_count_yes,
        "candidateCountNo": cand_count_no,
        "smartCountYes": smart_count_yes,
        "smartCountNo": smart_count_no,
        "sharpCapitalYes": round(sharp_cap_yes, 2),
        "sharpCapitalNo": round(sharp_cap_no, 2),
        "smartCapitalYes": round(smart_cap_yes, 2),
        "smartCapitalNo": round(smart_cap_no, 2),
        "avgSharpEntryYes": avg_entry_yes,
        "avgSharpEntryNo": avg_entry_no,
        "avgSmartEntryYes": avg_smart_entry_yes,
        "avgSmartEntryNo": avg_smart_entry_no,
        "currentPriceYes": round(p0, 3),
        "currentPriceNo": round(p1, 3),
        "slippageYesCents": slip_yes_cents,
        "slippageNoCents": slip_no_cents,
        "tailVerdict": tail_verdict_key,
        "verdict": tail_verdict,
        "tailableCount": len(tail_wallets),
        "tailableWallets": tail_wallets,
    }

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
        "smartCount": sharp_count + candidate_count,
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

    return analysis


def _hold_label(ratio):
    if ratio is None:
        return "Unknown holding ratio"
    pct = int(round(ratio * 100))
    if ratio >= 0.90:
        return f"High conviction sharp · {pct}% holds to resolution"
    if ratio >= 0.60:
        return f"Moderate holding · {pct}% holds to resolution"
    return f"Active trader · exits early {100 - pct}% of the time"


def compute_pick_from_doc(doc, market=None, mode="combined"):
    if not doc or not isinstance(doc, dict):
        return None
    wallets = doc.get("topWallets") or []
    if not wallets:
        if mode == "sharp":
            return doc.get("sharpPick") or doc.get("pick")
        return (doc.get("combined") or {}).get("pick") or doc.get("pick") or (doc.get("tailIntelligence") or {}).get("pick")

    prices = doc.get("prices") or (market.get("prices") if market else [0.5, 0.5])
    p0 = prices[0] if len(prices) > 0 else 0.5
    p1 = prices[1] if len(prices) > 1 else (1.0 - p0)
    outcomes = doc.get("outcomes") or (market.get("outcomes") if market else ["Yes", "No"])
    name_yes = outcomes[0] if outcomes else "Yes"
    name_no = outcomes[1] if len(outcomes) > 1 else "No"

    lean_side = doc.get("leanSide")
    comb = doc.get("combined") or {}
    comb_lean_side = comb.get("leanSide")

    sides_switched = (
        lean_side in ("YES", "NO")
        and comb_lean_side in ("YES", "NO")
        and lean_side != comb_lean_side
    )

    sharp_yes = [w for w in wallets if w.get("qualified") and w.get("side") == "YES" and w.get("directionalCapital", 0) > 0]
    sharp_no = [w for w in wallets if w.get("qualified") and w.get("side") == "NO" and w.get("directionalCapital", 0) > 0]
    cand_yes = [w for w in wallets if w.get("isCandidate") and w.get("side") == "YES" and w.get("directionalCapital", 0) > 0]
    cand_no = [w for w in wallets if w.get("isCandidate") and w.get("side") == "NO" and w.get("directionalCapital", 0) > 0]

    if mode == "sharp":
        pick_wallets_yes = sharp_yes
        pick_wallets_no = sharp_no
        effective_lean = lean_side
    else:
        pick_wallets_yes = sharp_yes + cand_yes
        pick_wallets_no = sharp_no + cand_no
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
    elif cap_yes > cap_no and cap_yes > 0:
        pick_side = "YES"
    elif cap_no > cap_yes and cap_no > 0:
        pick_side = "NO"

    if not pick_side:
        return None

    slip = (p0 - avg_entry_yes) if pick_side == "YES" and avg_entry_yes else ((p1 - avg_entry_no) if avg_entry_no else 0)
    sharp_cnt = len(sharp_yes) if pick_side == "YES" else len(sharp_no)
    cand_cnt = len(cand_yes) if pick_side == "YES" else len(cand_no)
    smart_cnt = len(pick_wallets_yes) if pick_side == "YES" else len(pick_wallets_no)
    smart_cap = cap_yes if pick_side == "YES" else cap_no
    chosen_name = name_yes if pick_side == "YES" else name_no

    if sides_switched:
        conviction = "CONFLICT"
        verdict = f"SPLIT CONSENSUS: Net smart lean switches sides between Sharps ({lean_side}) and Sharps + Candidates ({comb_lean_side}). No solid pick."
    elif slip > 0.07:
        conviction = "CAUTION"
        verdict = f"CAUTION: Line moved to {round((p0 if pick_side == 'YES' else p1)*100)}¢"
    elif (sharp_cnt >= 1 and (smart_cnt >= 2 or smart_cap >= 1500)) or (mode == "sharp" and sharp_cnt >= 1 and smart_cap >= 1500):
        conviction = "HIGH"
        verdict = f"Smart Money ({sharp_cnt} Sharp, {cand_cnt} Candidate) backing {chosen_name} (${round(smart_cap):,})"
    elif smart_cnt >= 1:
        conviction = "MODERATE"
        verdict = f"Smart Money ({sharp_cnt} Sharp, {cand_cnt} Candidate) backing {chosen_name} (${round(smart_cap):,})"
    else:
        conviction = "LEAN"
        verdict = f"Smart lean on {chosen_name}"

    slip_cents = round(slip * 100, 1) if (avg_entry_yes if pick_side == "YES" else avg_entry_no) is not None else None

    return {
        "side": pick_side,
        "outcome": chosen_name,
        "conviction": conviction,
        "isConflict": sides_switched,
        "conflictReason": f"Net smart lean switches sides between Sharps ({lean_side}) and Sharps + Candidates ({comb_lean_side})" if sides_switched else None,
        "sharpCount": sharp_cnt,
        "candidateCount": cand_cnt,
        "smartCount": smart_cnt,
        "smartCapital": round(smart_cap, 2),
        "avgEntry": round((avg_entry_yes if pick_side == "YES" else avg_entry_no) or 0, 3),
        "currentPrice": round(p0 if pick_side == "YES" else p1, 3),
        "slippageCents": slip_cents,
        "verdict": verdict,
    }


def analysis_summary(doc):
    if not doc:
        return None
    sharp_pick = doc.get("sharpPick") or compute_pick_from_doc(doc, mode="sharp")
    comb_pick = (doc.get("combined") or {}).get("pick") or compute_pick_from_doc(doc, mode="combined")
    comb = dict(doc.get("combined") or {
        "strengthYes": doc.get("strengthYes"),
        "strengthNo": doc.get("strengthNo"),
        "netLean": doc.get("netLean"),
        "leanSide": doc.get("leanSide"),
        "sharpCount": doc.get("sharpCount", 0),
        "candidateCount": doc.get("candidateCount", 0),
        "smartCount": (doc.get("sharpCount") or 0) + (doc.get("candidateCount") or 0),
        "smartCapitalYes": doc.get("smartCapitalYes", 0),
        "smartCapitalNo": doc.get("smartCapitalNo", 0),
        "alpha": doc.get("alpha"),
    })
    comb["pick"] = comb_pick

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
        "tailVerdict": (doc.get("tailIntelligence") or {}).get("verdict") or doc.get("tailVerdict"),
        "smartCapitalYes": doc.get("smartCapitalYes"),
        "smartCapitalNo": doc.get("smartCapitalNo"),
        "updatedAt": doc.get("updatedAt"),
        "combined": comb,
    }
