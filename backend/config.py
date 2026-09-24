"""Runtime-tunable classifier config (score weights + category cutoffs).

Held in a module global so classification always reads the latest values; persisted
to Mongo (settings collection) and reloaded on startup. Tuned live from the UI.
"""
import copy
import math
import hashlib
import json

DEFAULTS = {
    "weights": {"winrate": 0.40, "roi": 0.40, "depth": 0.20},
    "sharp": {"minEvents": 100, "minDays": 60, "minHold": 0.90,
              "windowDays": 180, "extendedDays": 365, "recentEvents": 50,
              "minWinrate": 0.50, "minRoi": 0.0, "minDataCoverage": 0.70},
    "proven": {"minEvents": 300, "minDays": 180, "confidence": 0.95, "futureEvents": 50},
    "candidate": {"minEvents": 5},
    "tailing": {"primeSlippageCents": 0.03, "maxChaseSlippageCents": 0.08},
    "benchmark": {"maxAgeSeconds": 600, "maxSpread": 0.04, "minDepthUsd": 1000,
                  "startBufferSeconds": 60, "pollSeconds": 60},
    "whale": {
        "maxTradeUsd": 20000,
        "totalVolume": 150000,
        "portfolioValue": 75000,
        "avgTradeUsd": 8000,
    },
    "automation": {
        "minDays": 5, "minDailyEpisodes": 40, "rapidSeconds": 3,
        "rapidFraction": 0.50, "intenseEpisodes": 200, "intenseMarkets": 20,
        "hedgedFraction": 0.50, "minHedgedMarkets": 10,
    },
}

_current = copy.deepcopy(DEFAULTS)


def get_config():
    return copy.deepcopy(_current)


def signature():
    from sharp_evidence import RULE_VERSION
    return hashlib.sha256(json.dumps({"rules": RULE_VERSION, "config": _current}, sort_keys=True).encode()).hexdigest()[:16]


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
                ratio_fields = {"minHold", "minWinrate", "minDataCoverage", "confidence", "maxSpread", "rapidFraction", "hedgedFraction"}
                upper = 1 if k in ratio_fields else (100 if group == "weights" or k in {"minRoi", "maxRoi", "maxCv"} else 1e9)
                if not 0 <= v <= upper:
                    raise ValueError(f"{group}.{k} must be between 0 and {upper:g}")
                if isinstance(dv, int) and not float(v).is_integer():
                    raise ValueError(f"{group}.{k} must be a whole number")
                out[group][k] = v
    if out["sharp"]["minHold"] < .9:
        raise ValueError("Sharp holding requirement cannot be below 90%")
    for group, keys in {"sharp": ["minEvents", "minDays", "windowDays", "extendedDays", "recentEvents"], "proven": ["minEvents", "minDays", "futureEvents"], "candidate": ["minEvents"], "automation": ["minDays", "minDailyEpisodes", "intenseEpisodes", "intenseMarkets", "minHedgedMarkets"], "benchmark": ["maxAgeSeconds", "minDepthUsd", "pollSeconds"]}.items():
        if any(out[group][k] < 1 for k in keys):
            raise ValueError(f"{group} counts and durations must be positive")
    if not .8 <= out["proven"]["confidence"] < 1:
        raise ValueError("Diagnostic confidence must be at least .8 and below 1")
    if out["sharp"]["extendedDays"] < out["sharp"]["windowDays"] or out["proven"]["minEvents"] < out["sharp"]["minEvents"] or out["proven"]["minDays"] < out["sharp"]["minDays"]:
        raise ValueError("Extended windows and Proven evidence must be at least as large as Sharp requirements")
    w = out["weights"]
    total = sum(w.values())
    if total <= 0:
        raise ValueError("At least one score weight must be positive")
    for k in w:
        w[k] /= total
    return out
