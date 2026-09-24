import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from classifier import analyze_wallet, reconstruct_performance


def trade(side="BUY", size=100, cash=40, outcome=0, cid="m", **kwargs):
    return dict(type="TRADE", conditionId=cid, side=side, size=size,
                usdcSize=cash, price=cash/size, outcomeIndex=outcome,
                asset=f"{cid}-{outcome}", timestamp=100, **kwargs)


def position(outcome=0, size=100, price=1, redeemable=True, cid="m"):
    return dict(conditionId=cid, outcomeIndex=outcome, asset=f"{cid}-{outcome}",
                size=size, curPrice=price, redeemable=redeemable)


@pytest.mark.parametrize("reverse", [False, True])
def test_two_outcomes_have_order_independent_profit(reverse):
    events = [trade(cash=60), trade(cash=40, outcome=1)]
    positions = [position(), position(outcome=1, price=0)]
    perf = reconstruct_performance(events, positions[::-1] if reverse else positions)
    assert perf["net_realized"] == 0
    assert perf["settled_bets"] == 1
    assert perf["reconciled"]


def test_round_trip_sale():
    perf = reconstruct_performance([trade(), trade("SELL", cash=60)])
    assert perf["net_realized"] == 20
    assert perf["true_roi"] == .5
    assert perf["true_winrate"] == 1
    assert perf["exited"] == 1


@pytest.mark.parametrize("price", [0, .0005, .5, .9995, 1])
def test_price_does_not_prove_resolution(price):
    perf = reconstruct_performance([trade()], [position(price=price, redeemable=False)])
    assert perf["settled_bets"] == 0
    assert perf["per_market"][0]["netPnl"] is None


def test_missing_position_is_not_a_loss():
    perf = reconstruct_performance([trade()])
    assert perf["settled_bets"] == 0
    assert perf["net_realized"] == 0
    assert not perf["reconciled"]


def test_fractional_holding_is_not_flat():
    perf = reconstruct_performance([trade(size=.1, cash=.04)],
                                   [position(size=.1, price=.5, redeemable=False)])
    assert perf["settled_bets"] == 0


def test_partial_exit_then_resolution():
    events = [trade(), trade("SELL", size=40, cash=24)]
    perf = reconstruct_performance(events, [position(size=60)])
    assert perf["net_realized"] == 44
    assert perf["held"] == 0
    assert perf["per_market"][0]["retention"] == .6


def test_partial_exit_stays_out_of_settled_market_metrics():
    perf = reconstruct_performance([trade(), trade("SELL", size=40, cash=24)],
                                   [position(size=60, price=.6, redeemable=False)])
    assert perf["settled_bets"] == 0
    assert perf["net_realized"] == 0


@pytest.mark.parametrize("payout,expected", [(100, 60), (0, -40)])
def test_redemption_uses_cash_including_zero(payout, expected):
    redeem = dict(type="REDEEM", conditionId="m", size=100, usdcSize=payout,
                  outcomeIndex=0, timestamp=200)
    perf = reconstruct_performance([trade(), redeem])
    assert perf["net_realized"] == expected
    assert perf["settled_bets"] == 1


def test_single_outcome_anonymous_redemption():
    perf = reconstruct_performance([trade(), dict(type="REDEEM", conditionId="m",
        size=100, usdcSize=100, timestamp=200)])
    assert perf["net_realized"] == 60


def test_ambiguous_redemption_is_excluded():
    perf = reconstruct_performance([trade(), trade(outcome=1), dict(type="REDEEM",
        conditionId="m", size=100, usdcSize=100, timestamp=200)])
    assert not perf["reconciled"]
    assert perf["settled_bets"] == 0


def test_sell_without_purchase_cannot_invent_profit():
    perf = reconstruct_performance([trade("SELL", cash=60)])
    assert perf["settled_bets"] == 0
    assert not perf["reconciled"]


def test_snapshot_without_acquisition_marks_partial():
    perf = reconstruct_performance([], [position()])
    assert not perf["reconciled"]
    assert perf["settled_bets"] == 0


def test_balance_mismatch_marks_partial():
    perf = reconstruct_performance([trade()], [position(size=50)])
    assert not perf["reconciled"]
    assert perf["settled_bets"] == 0


def test_split_merge_cashflow():
    events = [dict(type=t, conditionId="m", size=100, usdcSize=100, timestamp=i)
              for i, t in enumerate(["SPLIT", "MERGE"])]
    perf = reconstruct_performance(events)
    assert perf["net_realized"] == 0
    assert perf["settled_bets"] == 1
    assert perf["reconciled"]


def test_split_and_sells():
    events = [dict(type="SPLIT", conditionId="m", size=100, usdcSize=100),
              trade("SELL", cash=65), trade("SELL", cash=45, outcome=1)]
    assert reconstruct_performance(events)["net_realized"] == 10


def test_unsupported_conversion_marks_partial():
    perf = reconstruct_performance([trade(), trade("SELL", cash=60),
        dict(type="CONVERSION", conditionId="m", size=100)])
    assert not perf["reconciled"]
    assert perf["settled_bets"] == 0


@pytest.mark.parametrize("cash", [float("nan"), float("inf"), -1])
def test_invalid_cashflow_is_excluded(cash):
    perf = reconstruct_performance([trade(cash=cash), trade("SELL", cash=60)])
    assert not perf["reconciled"]


def test_partial_history_preserves_results_without_fabricating_score_or_bot():
    events = []
    for n in range(20):
        events.extend([trade(cid=str(n)), trade("SELL", cash=80, cid=str(n))])
    client = SimpleNamespace(activity_paginated=AsyncMock(return_value=(events, True)),
        positions_paginated=AsyncMock(return_value=([], False)), value=AsyncMock(return_value=[]))
    profile = asyncio.run(analyze_wallet(client, "wallet"))
    assert profile["reliability"]["tier"] == "partial"
    assert profile["primary"] not in {"SHARP", "INSIDER_EARLY"}
    assert profile["smartScore"] is None
    assert profile["stats"]["net_realized"] == 800
    assert profile["stats"]["true_roi"] == 1
    assert not profile["automation"]
