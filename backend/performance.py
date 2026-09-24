"""Conservative, outcome-level accounting for the public activity/positions APIs.

Markets contribute to performance only after every outcome has closed or is
explicitly redeemable and balances reconcile. Missing records are not losses.
Cashflows are as reported by the API; unreported fees/gas are not inferred.
"""
import math
from collections import defaultdict

EPS = 1e-6


def number(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Non-finite ledger value")
    return result


def outcome_key(row, assets):
    asset = str(row.get("asset") or "")
    if asset and asset in assets:
        return assets[asset]
    index = row.get("outcomeIndex")
    if index in (0, 1, "0", "1"):
        return str(index)
    return f"asset:{asset}" if asset else None


def reconstruct(activity, positions, categorize, metadata=None, as_of=None):
    from sharp_evidence import deduplicate, timestamp
    from datetime import datetime, timezone
    metadata = metadata or {}
    as_of = as_of or datetime.now(timezone.utc).timestamp()
    activity = deduplicate([e for e in (activity or []) if float(e.get("timestamp") or 0) <= as_of])
    activity, positions = activity or [], positions or []
    grouped, snapshots = defaultdict(list), defaultdict(list)
    for row in activity:
        if row.get("conditionId"):
            grouped[row["conditionId"]].append(row)
    for row in positions:
        if row.get("conditionId"):
            snapshots[row["conditionId"]].append(row)

    rows, issues = [], []
    total_buys = total_shares = 0.0
    # Include snapshot-only positions: absent acquisition history is incomplete.
    for cid in sorted(set(grouped) | set(snapshots)):
        events, current = grouped[cid], snapshots[cid]
        market_meta = metadata.get(cid, {})
        resolution_time = timestamp(market_meta.get("resolvedAt"))
        resolved = market_meta.get("resolved") and resolution_time and resolution_time <= as_of
        payouts = market_meta.get("payouts") or []
        assets = {str(r["asset"]): str(r["outcomeIndex"])
                  for r in events + current if r.get("asset")
                  and r.get("outcomeIndex") in (0, 1, "0", "1")}
        balances, pos = defaultdict(float), {}
        problems = set()
        spent = received = buy_shares = buys_usd = 0.0
        redeemed = False
        relevant = False
        for p in current:
            key = outcome_key(p, assets)
            if key is None or key in pos:
                problems.add("ambiguous_position")
            else:
                pos[key] = p
        # Preserve upstream order for events with identical timestamps. Balance
        # validation happens after netting, so tied partial fills are harmless.
        for event in sorted(events, key=lambda e: e.get("timestamp") or 0):
            kind = event.get("type")
            if kind in ("REWARD", "MAKER_REBATE", "TAKER_REBATE"):
                continue  # wallet incentives are not bet outcomes
            if kind not in ("TRADE", "REDEEM", "CLAIM", "SPLIT", "MERGE"):
                problems.add("unsupported_activity")
                continue
            relevant = True
            try:
                shares = number(event.get("size", 0))
                cash = number(event["usdcSize"]) if event.get("usdcSize") is not None else (
                    shares * number(event.get("price", 0)) if kind == "TRADE" else None)
                if shares < 0 or cash is None or cash < 0:
                    raise ValueError("Missing or negative cashflow")
            except (ValueError, TypeError):
                problems.add("invalid_cashflow")
                continue
            if kind in ("SPLIT", "MERGE"):
                direction = 1 if kind == "SPLIT" else -1
                balances["0"] += direction * shares
                balances["1"] += direction * shares
                if kind == "SPLIT":
                    spent += cash
                else:
                    received += cash
                continue
            key = outcome_key(event, assets)
            if kind in ("REDEEM", "CLAIM"):
                redeemed = True
                received += cash
                if key is None:
                    held = [k for k, v in balances.items() if v > EPS]
                    # A single held token is unambiguous. Never guess how a
                    # market-level redemption consumed several outcome tokens.
                    key = held[0] if len(held) == 1 else None
                if key is None:
                    problems.add("ambiguous_redemption")
                else:
                    balances[key] -= shares
                continue
            if key is None or event.get("side") not in ("BUY", "SELL"):
                problems.add("ambiguous_trade")
                continue
            if event["side"] == "BUY":
                balances[key] += shares
                spent += cash
                buys_usd += cash
                buy_shares += shares
            else:
                balances[key] -= shares
                received += cash
            # Only explicitly separate cash-denominated fees are adjusted. Never
            # subtract a fee already included in cashflow a second time.
            if event.get("feeUsd") is not None and event.get("feeIncludedInCashflow") is not True:
                try:
                    fee = number(event["feeUsd"])
                    if fee < 0:
                        raise ValueError("Negative fee")
                    spent += fee
                except (ValueError, TypeError):
                    problems.add("invalid_fee")

        total_buys += buys_usd
        total_shares += buy_shares
        final_value = 0.0
        settled = True
        held_to_resolution = redeemed
        for key in set(balances) | set(pos):
            balance, snapshot = balances[key], pos.get(key)
            if balance < -EPS:
                problems.add("missing_acquisition")
            if snapshot is not None:
                try:
                    size = number(snapshot["size"])
                    price = number(snapshot["curPrice"])
                    if size < 0 or not 0 <= price <= 1:
                        raise ValueError("Invalid position")
                    if abs(balance - size) > max(1e-4 + EPS, abs(size) * 1e-6):
                        problems.add("position_balance_mismatch")
                except (KeyError, ValueError, TypeError):
                    problems.add("invalid_position")
                    continue
            if balance <= EPS:
                continue
            if resolved and key in {"0", "1"} and len(payouts) == 2:
                final_value += balance * payouts[int(key)]
                held_to_resolution = True
            elif snapshot is None:
                problems.add("missing_position")
                settled = False
            elif snapshot.get("redeemable") is True and price in (0.0, 1.0):
                final_value += balance * price
                held_to_resolution = True
            else:
                settled = False

        if not relevant and not current and not problems:
            continue
        if spent <= EPS:
            problems.add("missing_cost_basis")
        if problems:
            settled = False
            issues.append({"conditionId": cid, "reasons": sorted(problems)})
        row_metadata = next((e for e in events if e.get("title") or e.get("slug")),
                        current[0] if current else {})
        pnl = received + final_value - spent
        slug = row_metadata.get("eventSlug") or row_metadata.get("slug")
        rows.append({
            "conditionId": cid, "title": row_metadata.get("title"), "icon": row_metadata.get("icon"),
            "slug": slug, "category": categorize(slug, row_metadata.get("title")),
            "invested": round(spent, 2), "proceeds": round(received + final_value, 2),
            "netPnl": round(pnl, 2) if settled else None,
            "entryPrice": round(buys_usd / buy_shares, 4) if buy_shares else 0.0,
            "settled": settled, "heldToResolution": settled and held_to_resolution,
            "won": settled and pnl > EPS, "reconciled": not problems,
            "issues": sorted(problems),
            "resolvedAt": resolution_time if resolved else None,
            "cashflowClosedAt": max((int(e.get("timestamp") or 0) for e in events), default=0),
        })

    from sharp_evidence import retention_record
    rows = [retention_record(grouped[r["conditionId"]], snapshots[r["conditionId"]],
             metadata.get(r["conditionId"], {}), r, as_of) for r in rows]
    for r in rows:
        if r.get("void"):
            r["won"] = False
    closed = [r for r in rows if r["settled"] and not r.get("void")]
    wins = sum(r["won"] for r in closed)
    hold_known = [r for r in rows if r.get("retention") is not None and not r.get("void")]
    held = sum(r["heldToResolution"] for r in hold_known)
    invested = sum(r["invested"] for r in closed)
    profit = sum(r["netPnl"] for r in closed)
    rows.sort(key=lambda r: (-abs(r["netPnl"] or 0), r["conditionId"]))
    return {
        "settled_bets": len(closed), "true_winrate": round(wins / len(closed), 4) if closed else None,
        "true_roi": round(profit / invested, 4) if invested > EPS else None,
        "net_realized": round(profit, 2), "invested_settled": round(invested, 2),
        "hold_ratio": round(held / len(hold_known), 4) if hold_known else None,
        "avg_entry_price": round(total_buys / total_shares, 4) if total_shares else 0.0,
        "held": held, "exited": sum(not r.get("heldToResolution") for r in closed), "per_market": rows,
        "reconciled": not issues, "issues": issues,
    }
