"""
Random search filter optimizer for FVG/Kronos trade outcomes.

Each "config" = one combination of filter values. Score by WR x sample-size.
Toggleable filters via FILTER_CONFIG dict — set 'enabled': False to skip.

Run: python scripts/ml_optimizer.py [--n 5000] [--config filters.json]
Output: ranked top configs + feature importance.
"""
from __future__ import annotations
import argparse
import json
import math
import os
import random
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg2
import psycopg2.extras

DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://fvg:f18bbdd5a18785da8d18d8d92965defc@localhost:5432/fvg",
)

# ---- TOGGLEABLE FILTER UNIVERSE ----
# Edit 'enabled' to skip a filter from random search. Add new filters as needed.
DEFAULT_FILTER_CONFIG = {
    "kronos_conf_min":   {"enabled": True,  "values": [None, 40, 50, 60, 70]},
    "rsi14_long_max":    {"enabled": True,  "values": [None, 65, 70, 75, 80]},
    "rsi14_short_min":   {"enabled": True,  "values": [None, 20, 25, 30, 35]},
    "ema_stack_align":   {"enabled": True,  "values": [True, False]},
    "btc_trend_align":   {"enabled": True,  "values": [True, False]},
    "macd_hist_sign":    {"enabled": True,  "values": [True, False]},
    "bb_pos_long_max":   {"enabled": False, "values": [None, 0.6, 0.7, 0.8, 0.9]},
    "bb_pos_short_min":  {"enabled": False, "values": [None, 0.1, 0.2, 0.3, 0.4]},
    "vol_z_min":         {"enabled": True,  "values": [None, -1.0, 0.0, 0.5, 1.0]},
    "atr_pct_min":       {"enabled": False, "values": [None, 0.3, 0.5, 0.8]},
    "atr_pct_max":       {"enabled": False, "values": [None, 2.0, 3.0, 5.0]},
    "tf_blocklist":      {"enabled": True,  "values": [[], ["15m"], ["15m", "30m"]]},
    "mode_blocklist":    {"enabled": True,  "values": [[], ["scalping"]]},
}

MIN_SAMPLE = 8  # configs with fewer trades disqualified


def load_data(min_trades: int = 1) -> List[Dict]:
    """Load trades with features + outcome."""
    conn = psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute("""
              SELECT
                sf.decision_id, sf.symbol, sf.tf, sf.features, sf.btc_context,
                sf.outcome, sf.pnl_pct,
                k.mode, k.direction, k.kronos_raw, k.zone_dir
              FROM signal_features sf
              JOIN kronos_decisions k ON k.id = sf.decision_id
              WHERE sf.outcome IS NOT NULL
              ORDER BY sf.created_at ASC
            """)
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def passes(trade: Dict, cfg: Dict) -> bool:
    """Apply filter config to one trade. Returns True if trade survives all filters."""
    feats = trade["features"] or {}
    tf = trade["tf"]
    direction = trade["direction"]
    kr = trade["kronos_raw"] or {}
    if isinstance(kr, str):
        kr = json.loads(kr)
    btc = trade["btc_context"] or {}

    # Per-trade-TF features (use trade's own TF)
    f_tf = feats.get(tf, {}) if isinstance(feats, dict) else {}
    f_1h = feats.get("1h", {}) if isinstance(feats, dict) else {}

    # Kronos confidence
    if cfg.get("kronos_conf_min") is not None:
        c = kr.get("confidence")
        if c is None or c < cfg["kronos_conf_min"]:
            return False

    # RSI14 extreme block
    rsi = f_tf.get("rsi14")
    if direction == "long" and cfg.get("rsi14_long_max") is not None:
        if rsi is not None and rsi > cfg["rsi14_long_max"]:
            return False
    if direction == "short" and cfg.get("rsi14_short_min") is not None:
        if rsi is not None and rsi < cfg["rsi14_short_min"]:
            return False

    # EMA stack align with direction (1h)
    if cfg.get("ema_stack_align"):
        stack = f_1h.get("ema_stack")
        if direction == "long" and stack != "bull":
            return False
        if direction == "short" and stack != "bear":
            return False

    # BTC trend align
    if cfg.get("btc_trend_align"):
        bt = btc.get("btc_trend")
        if direction == "long" and bt != "bull":
            return False
        if direction == "short" and bt != "bear":
            return False

    # MACD hist sign align
    if cfg.get("macd_hist_sign"):
        hist = f_tf.get("macd_hist")
        if hist is None:
            return False
        if direction == "long" and hist <= 0:
            return False
        if direction == "short" and hist >= 0:
            return False

    # BB position
    bb = f_tf.get("bb_pos")
    if direction == "long" and cfg.get("bb_pos_long_max") is not None:
        if bb is not None and bb > cfg["bb_pos_long_max"]:
            return False
    if direction == "short" and cfg.get("bb_pos_short_min") is not None:
        if bb is not None and bb < cfg["bb_pos_short_min"]:
            return False

    # Volume z-score
    vz = f_tf.get("vol_z")
    if cfg.get("vol_z_min") is not None:
        if vz is None or vz < cfg["vol_z_min"]:
            return False

    # ATR pct range
    atr_pct = f_tf.get("atr_pct")
    if cfg.get("atr_pct_min") is not None and (atr_pct is None or atr_pct < cfg["atr_pct_min"]):
        return False
    if cfg.get("atr_pct_max") is not None and atr_pct is not None and atr_pct > cfg["atr_pct_max"]:
        return False

    # TF / mode blocklist
    if tf in cfg.get("tf_blocklist", []):
        return False
    if trade["mode"] in cfg.get("mode_blocklist", []):
        return False

    return True


def evaluate(trades: List[Dict], cfg: Dict) -> Dict:
    kept = [t for t in trades if passes(t, cfg)]
    n = len(kept)
    if n == 0:
        return {"n": 0, "wr": 0.0, "avg_pnl": 0.0, "expectancy": 0.0, "score": -999}
    wins = sum(1 for t in kept if t["outcome"] == "win")
    losses = sum(1 for t in kept if t["outcome"] == "loss")
    tp1s = sum(1 for t in kept if t["outcome"] == "tp1")
    closed_pnl = [float(t["pnl_pct"] or 0) for t in kept]
    total_pnl = sum(closed_pnl)
    avg_pnl = total_pnl / n
    decisive = wins + losses  # exclude tp1 from WR denominator
    wr = (wins / decisive * 100) if decisive else 0
    score = wr * math.sqrt(n) * (avg_pnl if avg_pnl > 0 else 0.01)
    return {
        "n": n, "wins": wins, "losses": losses, "tp1": tp1s,
        "wr": round(wr, 1), "avg_pnl": round(avg_pnl, 3),
        "total_pnl": round(total_pnl, 2),
        "expectancy": round(avg_pnl, 3),
        "score": round(score, 2),
    }


def random_config(filter_config: Dict) -> Dict:
    cfg = {}
    for name, spec in filter_config.items():
        if not spec.get("enabled", True):
            continue
        cfg[name] = random.choice(spec["values"])
    return cfg


def cfg_str(cfg: Dict) -> str:
    parts = []
    for k, v in cfg.items():
        if v is None or v is False or v == [] or v == "":
            continue
        if isinstance(v, list):
            parts.append(f"{k}={','.join(map(str, v))}")
        else:
            parts.append(f"{k}={v}")
    return " | ".join(parts) or "<no filters>"


def split_train_holdout(trades: List[Dict], holdout_frac: float = 0.3):
    """Split by date order: oldest=train, newest=holdout."""
    n = len(trades)
    cut = int(n * (1 - holdout_frac))
    return trades[:cut], trades[cut:]


def feature_importance(trades: List[Dict], baseline: Dict, filter_config: Dict, n_per_filter: int = 200):
    """For each filter, measure how much disabling vs enabling moves WR."""
    importance = []
    for name, spec in filter_config.items():
        if not spec.get("enabled"):
            continue
        wr_with, wr_without = [], []
        for _ in range(n_per_filter):
            cfg = random_config(filter_config)
            res_with = evaluate(trades, cfg)
            cfg2 = dict(cfg)
            cfg2[name] = None if name not in ("ema_stack_align", "btc_trend_align", "macd_hist_sign") else False
            cfg2[name] = [] if name in ("tf_blocklist", "mode_blocklist") else cfg2[name]
            res_without = evaluate(trades, cfg2)
            if res_with["n"] >= MIN_SAMPLE:
                wr_with.append(res_with["wr"])
            if res_without["n"] >= MIN_SAMPLE:
                wr_without.append(res_without["wr"])
        if wr_with and wr_without:
            importance.append({
                "filter": name,
                "wr_on": round(sum(wr_with) / len(wr_with), 1),
                "wr_off": round(sum(wr_without) / len(wr_without), 1),
                "delta": round(sum(wr_with) / len(wr_with) - sum(wr_without) / len(wr_without), 1),
            })
    importance.sort(key=lambda x: abs(x["delta"]), reverse=True)
    return importance


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=5000, help="random search iterations")
    ap.add_argument("--top", type=int, default=15, help="show top-N configs")
    ap.add_argument("--config", type=str, help="path to JSON filter config (overrides defaults)")
    ap.add_argument("--holdout", type=float, default=0.3, help="holdout fraction (0=disable)")
    args = ap.parse_args()

    filter_config = DEFAULT_FILTER_CONFIG
    if args.config and os.path.exists(args.config):
        with open(args.config) as f:
            filter_config = json.load(f)

    trades = load_data()
    print(f"\n=== DATA ===")
    print(f"Total labeled trades: {len(trades)}")
    if not trades:
        print("No labeled trades — run fill_outcomes.py first.")
        return
    by_outcome = {}
    for t in trades:
        by_outcome[t["outcome"]] = by_outcome.get(t["outcome"], 0) + 1
    print(f"Outcomes: {by_outcome}")
    base = evaluate(trades, {})
    print(f"Baseline (no filters): n={base['n']} WR={base['wr']}% avg_pnl={base['avg_pnl']}% total_pnl={base['total_pnl']}%")

    if args.holdout > 0 and len(trades) >= 20:
        train, holdout = split_train_holdout(trades, args.holdout)
        print(f"\nSplit: train={len(train)} holdout={len(holdout)}")
    else:
        train = trades
        holdout = []

    # Random search
    print(f"\n=== RANDOM SEARCH ({args.n} iters) ===")
    seen = set()
    results = []
    enabled_filters = [k for k, v in filter_config.items() if v.get("enabled", True)]
    print(f"Active filters: {enabled_filters}")

    for _ in range(args.n):
        cfg = random_config(filter_config)
        key = json.dumps(cfg, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        res = evaluate(train, cfg)
        if res["n"] >= MIN_SAMPLE:
            res["cfg"] = cfg
            if holdout:
                ho = evaluate(holdout, cfg)
                res["holdout_wr"] = ho["wr"]
                res["holdout_n"] = ho["n"]
            results.append(res)

    results.sort(key=lambda r: r["score"], reverse=True)

    print(f"\n=== TOP {args.top} CONFIGS (train) ===")
    print(f"{'rank':<5}{'n':<5}{'WR%':<7}{'avgPnL%':<9}{'totalPnL':<10}{'score':<8}{'holdoutWR':<10}filters")
    for i, r in enumerate(results[:args.top], 1):
        ho = f"{r.get('holdout_wr','-')}%/{r.get('holdout_n','-')}n" if holdout else "-"
        print(f"{i:<5}{r['n']:<5}{r['wr']:<7}{r['avg_pnl']:<9}{r['total_pnl']:<10}{r['score']:<8}{ho:<10}{cfg_str(r['cfg'])}")

    # Feature importance
    print(f"\n=== FEATURE IMPORTANCE (avg WR delta when filter enabled) ===")
    imp = feature_importance(train, base, filter_config, n_per_filter=300)
    for x in imp:
        arrow = "↑" if x["delta"] > 0 else "↓"
        print(f"  {x['filter']:<22} {arrow} {x['delta']:+.1f}%  (on={x['wr_on']}% off={x['wr_off']}%)")

    print(f"\n=== USAGE ===")
    print(f"To toggle: edit DEFAULT_FILTER_CONFIG in this file or pass --config filters.json")
    print(f"Disable noisy filter: set 'enabled': False")


if __name__ == "__main__":
    main()
