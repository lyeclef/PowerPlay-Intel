"""Runtime-tunable classifier config (score weights + category cutoffs).

Held in a module global so classification always reads the latest values; persisted
to Mongo (settings collection) and reloaded on startup. Tuned live from the UI.
"""
import copy
import math
import hashlib
import json

DEFAULTS = {
    "weights": {"winrate": 0.30, "roi": 0.25, "hold": 0.20, "pnl": 0.25},
    "sharp": {"minClosed": 5, "minWinrate": 0.55, "minRoi": 0.10, "minHold": 0.40},
    "elite": {"minWinrate": 0.60, "minRoi": 0.25, "minHold": 0.50},
    "insider": {"maxEntry": 0.45, "minWinrate": 0.65, "minRoi": 0.60},
    "whale": {
        "maxTradeUsd": 20000,
        "totalVolume": 150000,
        "portfolioValue": 75000,
        "avgTradeUsd": 8000,
    },
    "bot": {
        "minOrders": 50,
        "maxRoi": 0.25,
        "maxMedianInterval": 30,
        "minTradesPerDay": 60,
        "minFastRatio": 0.40,
        "maxCv": 1.1,
    },
    "mm": {
        "minTwoSided": 0.50,
        "minOrders": 40,
        "buyLo": 0.35,
        "buyHi": 0.65,
        "minMarkets": 8,
        "maxRoi": 0.25,
    },
    "scalper": {"minOrders": 15, "maxHold": 0.30},
    "automation": {
        "minTradesPerDay": 80,
        "minSample": 200,
        "mmTwoSided": 0.30,
        "buyLo": 0.35,
        "buyHi": 0.65,
    },
    "insufficient": {"maxTrades": 4, "maxVolume": 300, "maxClosed": 2},
}

_current = copy.deepcopy(DEFAULTS)


def get_config():
    return copy.deepcopy(_current)


def signature():
    return hashlib.sha256(json.dumps(_current, sort_keys=True).encode()).hexdigest()[:16]


def set_config(cfg):
    global _current
    _current = copy.deepcopy(cfg)


def _num(v, default):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def merge_config(partial):
    """Deep-merge a partial config over defaults, coercing to numbers and
    normalizing the score weights to sum to 1.0."""
    if partial is None:
        partial = {}
    if not isinstance(partial, dict):
        raise ValueError("Config must be an object")
    if set(partial) - set(DEFAULTS):
        raise ValueError("Unknown configuration group")
    out = copy.deepcopy(DEFAULTS)
    for group, defaults in DEFAULTS.items():
        pg = partial.get(group, {})
        if not isinstance(pg, dict) or set(pg) - set(defaults):
            raise ValueError(f"Invalid fields for {group}")
        for k, dv in defaults.items():
            if k in pg:
                v = pg[k]
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                    raise ValueError(f"{group}.{k} must be a finite number")
                ratio_fields = {"minWinrate", "minHold", "maxEntry", "minFastRatio", "minTwoSided", "buyLo", "buyHi", "mmTwoSided", "maxHold"}
                upper = 1 if k in ratio_fields else (100 if group == "weights" or k in {"minRoi", "maxRoi", "maxCv"} else 1e9)
                if not 0 <= v <= upper:
                    raise ValueError(f"{group}.{k} must be between 0 and {upper:g}")
                if isinstance(dv, int) and not float(v).is_integer():
                    raise ValueError(f"{group}.{k} must be a whole number")
                out[group][k] = v
    for group in ("mm", "automation"):
        if out[group]["buyLo"] > out[group]["buyHi"]:
            raise ValueError(f"{group}.buyLo cannot exceed buyHi")
    w = out["weights"]
    total = sum(w.values())
    if total <= 0:
        raise ValueError("At least one score weight must be positive")
    for k in w:
        w[k] /= total
    return out
