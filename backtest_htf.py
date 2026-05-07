"""
Backtest: HTF fade short + cascade alignment score.

Fetches historical klines, detects FVGs, then for each FVG:
  1. HTF test  — compute 4h RSI7 at FVG born_time
                 if RSI7 >= 75 → simulate SHORT fade (entry=price, SL=zone.top+buf)
                 else           → simulate LONG (normal path)
  2. Cascade   — for each of (30m, 1h, 2h) check StochRSI direction at born_time
                 score = how many TFs agree with zone direction (0-3)
                 record outcome by score bucket

Results printed + written to backtest_htf_results.json
"""
from __future__ import annotations

import json
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np
import requests

from config import BASE_URL, SYMBOLS, MIN_STRENGTH_TO_ALERT
from fvg_engine import detect_fvg, calc_strength, FVGZone
from indicator_context import stochrsi_series

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("backtest_htf")

BARS_PER_TF  = 1500
LOOKAHEAD    = 100
SYMBOL_DELAY = 0.10

# Symbols to test — use top liquid ones for speed; set to SYMBOLS for full run
TEST_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "LINKUSDT", "AVAXUSDT", "DOTUSDT",
    "ARBUSDT", "OPUSDT", "INJUSDT", "SUIUSDT", "NEARUSDT",
    "UNIUSDT", "AAVEUSDT", "LDOUSDT", "RUNEUSDT", "ENSUSDT",
]

# HTF RSI7 thresholds (mirror predictor.py)
HTF_SOFT_OB  = 75.0
HTF_HARD_OB  = 80.0
HTF_SOFT_OS  = 25.0
HTF_HARD_OS  = 20.0

# Cascade TFs to check alignment for
CASCADE_TFS = ("30m", "1h", "2h")


# ── Helpers ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Bar:
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


def fetch_klines(symbol: str, interval: str, limit: int = 1500) -> List[Bar]:
    url = f"{BASE_URL}/fapi/v1/klines"
    all_bars: List[Bar] = []
    end_time = None
    remaining = limit

    while remaining > 0:
        fetch_n = min(remaining, 1499)
        params = {"symbol": symbol, "interval": interval, "limit": fetch_n + 1}
        if end_time:
            params["endTime"] = end_time
        try:
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            raw = resp.json()
        except Exception as e:
            logger.warning("fetch %s %s failed: %s", symbol, interval, e)
            break
        if not raw:
            break
        closed = raw[:-1]
        bars = [Bar(int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]))
                for k in closed]
        if not bars:
            break
        all_bars = bars + all_bars
        remaining -= len(bars)
        if len(bars) < fetch_n:
            break
        end_time = bars[0].open_time - 1
        time.sleep(0.05)
    return all_bars


def _rsi7(bars: List[Bar]) -> float:
    """Wilder RSI-7 on close prices. Returns 50.0 if insufficient data."""
    if len(bars) < 8:
        return 50.0
    vals = [b.close for b in bars]
    deltas = np.diff(vals)
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_g  = float(np.mean(gains[:7]))
    avg_l  = float(np.mean(losses[:7]))
    for i in range(7, len(gains)):
        avg_g = (avg_g * 6 + gains[i]) / 7
        avg_l = (avg_l * 6 + losses[i]) / 7
    if avg_l == 0:
        return 100.0
    return round(100.0 - 100.0 / (1.0 + avg_g / avg_l), 2)


def _stoch_direction(bars: List[Bar], zone_direction: int) -> Optional[str]:
    """
    Returns 'agree' if StochRSI aligns with zone_direction at these bars,
    'conflict' if it opposes, None if insufficient data.
    """
    if len(bars) < 20:
        return None
    closes = [b.close for b in bars]
    k_vals, d_vals = stochrsi_series(closes)
    pairs = [(k, d) for k, d in zip(k_vals, d_vals) if k is not None and d is not None]
    if len(pairs) < 2:
        return None
    k, d = pairs[-1]
    if zone_direction == 1:   # expecting long-aligned = oversold
        if k <= 30 and d <= 30:
            return "agree"
        if k >= 70 and d >= 70:
            return "conflict"
    else:                     # expecting short-aligned = overbought
        if k >= 70 and d >= 70:
            return "agree"
        if k <= 30 and d <= 30:
            return "conflict"
    return "neutral"


def simulate_outcome(direction: int, entry: float, sl: float,
                     tp1: float, tp2: float, forward: List[Bar]) -> str:
    for bar in forward:
        if direction == 1:
            if bar.low <= sl:   return "loss"
            if bar.high >= tp2: return "win"
        else:
            if bar.high >= sl:  return "loss"
            if bar.low <= tp2:  return "win"
    return "open"


def _risk_buf(zone_top: float, zone_bottom: float, atr: float) -> float:
    if atr > 0:
        return atr * 0.1
    return abs(zone_top - zone_bottom) * 0.1


# ── Per-FVG analysis ─────────────────────────────────────────────────────────

@dataclass
class FVGResult:
    symbol: str
    tf: str
    direction: int
    born_time: int
    entry: float
    # HTF result
    htf_rsi7_4h: float = 0.0
    htf_bucket: str = ""        # "normal_long","soft_ob_long","hard_ob_fade_short","normal_short",etc
    htf_outcome: str = ""
    # Cascade result
    cascade_score: int = 0      # 0-3: how many of (30m,1h,2h) agree with direction
    cascade_outcome: str = ""


def process_symbol(symbol: str, bars_by_tf: Dict[str, List[Bar]]) -> List[FVGResult]:
    results: List[FVGResult] = []
    bars_15m = bars_by_tf.get("15m", [])
    bars_4h  = bars_by_tf.get("4h",  [])

    if len(bars_15m) < 10:
        return results

    for i in range(3, len(bars_15m)):
        window = bars_15m[:i + 1]
        fvg = detect_fvg(window, symbol)
        if fvg is None:
            continue

        strength = calc_strength(window, fvg, symbol, {})
        if strength.get("main_strength", 0) < MIN_STRENGTH_TO_ALERT:
            continue

        born_time = fvg["born_time"]
        zone_dir  = fvg["direction"]
        zone_top  = float(fvg["top"])
        zone_bot  = float(fvg["bottom"])
        atr       = float(strength.get("atr", 0.0) or 0.0)
        entry_p   = float(strength.get("price", window[-1].close))
        buf       = _risk_buf(zone_top, zone_bot, atr)

        forward = bars_15m[i + 1: i + 1 + LOOKAHEAD]
        if not forward:
            continue

        # ── 4h RSI7 at born_time ────────────────────────────────────────────
        htf_slice = [b for b in bars_4h if b.open_time <= born_time]
        htf_rsi7  = _rsi7(htf_slice) if len(htf_slice) >= 8 else 50.0

        # ── HTF bucket + simulate ────────────────────────────────────────────
        if zone_dir == 1:  # bullish FVG
            if htf_rsi7 >= HTF_HARD_OB:
                # Hard gate: fade SHORT
                sl  = zone_top + buf
                risk = abs(sl - entry_p)
                if risk > 0 and entry_p < sl:
                    tp2 = entry_p - risk * 2
                    outcome = simulate_outcome(-1, entry_p, sl, entry_p - risk, tp2, forward)
                    htf_bucket = "hard_ob_fade_short"
                else:
                    outcome = "invalid_risk"
                    htf_bucket = "hard_ob_fade_short"
            elif htf_rsi7 >= HTF_SOFT_OB:
                # Soft OB: still LONG but penalized — simulate as LONG, label separately
                sl  = zone_bot - buf
                risk = entry_p - sl
                if risk > 0:
                    tp2 = entry_p + risk * 2
                    outcome = simulate_outcome(1, entry_p, sl, entry_p + risk, tp2, forward)
                else:
                    outcome = "invalid_risk"
                htf_bucket = "soft_ob_long"
            else:
                # Normal LONG
                sl  = zone_bot - buf
                risk = entry_p - sl
                if risk > 0:
                    tp2 = entry_p + risk * 2
                    outcome = simulate_outcome(1, entry_p, sl, entry_p + risk, tp2, forward)
                else:
                    outcome = "invalid_risk"
                htf_bucket = "normal_long"
        else:  # bearish FVG
            if htf_rsi7 <= HTF_HARD_OS:
                # Hard gate: fade LONG (mirror)
                sl  = zone_bot - buf
                risk = abs(entry_p - sl)
                if risk > 0 and entry_p > sl:
                    tp2 = entry_p + risk * 2
                    outcome = simulate_outcome(1, entry_p, sl, entry_p + risk, tp2, forward)
                else:
                    outcome = "invalid_risk"
                htf_bucket = "hard_os_fade_long"
            elif htf_rsi7 <= HTF_SOFT_OS:
                sl  = zone_top + buf
                risk = sl - entry_p
                if risk > 0:
                    tp2 = entry_p - risk * 2
                    outcome = simulate_outcome(-1, entry_p, sl, entry_p - risk, tp2, forward)
                else:
                    outcome = "invalid_risk"
                htf_bucket = "soft_os_short"
            else:
                sl  = zone_top + buf
                risk = sl - entry_p
                if risk > 0:
                    tp2 = entry_p - risk * 2
                    outcome = simulate_outcome(-1, entry_p, sl, entry_p - risk, tp2, forward)
                else:
                    outcome = "invalid_risk"
                htf_bucket = "normal_short"

        # ── Cascade score ────────────────────────────────────────────────────
        score = 0
        for ctf in CASCADE_TFS:
            ctf_bars = bars_by_tf.get(ctf, [])
            ctf_slice = [b for b in ctf_bars if b.open_time <= born_time]
            if not ctf_slice:
                continue
            align = _stoch_direction(ctf_slice, zone_dir)
            if align == "agree":
                score += 1

        # Cascade outcome: same direction as zone
        if zone_dir == 1:
            sl_c  = zone_bot - buf
            risk_c = entry_p - sl_c
            if risk_c > 0:
                cascade_outcome = simulate_outcome(1, entry_p, sl_c, entry_p + risk_c, entry_p + risk_c * 2, forward)
            else:
                cascade_outcome = "invalid_risk"
        else:
            sl_c  = zone_top + buf
            risk_c = sl_c - entry_p
            if risk_c > 0:
                cascade_outcome = simulate_outcome(-1, entry_p, sl_c, entry_p - risk_c, entry_p - risk_c * 2, forward)
            else:
                cascade_outcome = "invalid_risk"

        results.append(FVGResult(
            symbol=symbol, tf="15m", direction=zone_dir,
            born_time=born_time, entry=entry_p,
            htf_rsi7_4h=htf_rsi7, htf_bucket=htf_bucket, htf_outcome=outcome,
            cascade_score=score, cascade_outcome=cascade_outcome,
        ))

    return results


# ── Aggregate stats ──────────────────────────────────────────────────────────

def _wr(wins: int, losses: int) -> str:
    total = wins + losses
    if total == 0:
        return "n/a"
    return f"{100*wins/total:.1f}% (n={total})"


def print_report(all_results: List[FVGResult]) -> dict:
    from collections import defaultdict

    # HTF buckets
    htf: Dict[str, Dict[str, int]] = defaultdict(lambda: {"win": 0, "loss": 0, "open": 0})
    for r in all_results:
        if r.htf_outcome in ("win", "loss", "open"):
            htf[r.htf_bucket][r.htf_outcome] += 1

    # Cascade score buckets (long only for clarity)
    casc: Dict[int, Dict[str, int]] = defaultdict(lambda: {"win": 0, "loss": 0, "open": 0})
    casc_short: Dict[int, Dict[str, int]] = defaultdict(lambda: {"win": 0, "loss": 0, "open": 0})
    for r in all_results:
        bucket = casc[r.cascade_score] if r.direction == 1 else casc_short[r.cascade_score]
        if r.cascade_outcome in ("win", "loss", "open"):
            bucket[r.cascade_outcome] += 1

    print("\n" + "="*60)
    print("HTF RSI7 BACKTEST RESULTS (15m FVGs × 4h RSI7)")
    print("="*60)
    print(f"{'Bucket':<28} {'WR':>10}  wins  losses")
    for bucket in ["normal_long", "soft_ob_long", "hard_ob_fade_short",
                   "normal_short", "soft_os_short", "hard_os_fade_long"]:
        d = htf.get(bucket, {"win": 0, "loss": 0})
        print(f"  {bucket:<26} {_wr(d['win'], d['loss']):>10}  {d['win']:>5}  {d['loss']:>6}")

    print("\n" + "="*60)
    print("CASCADE SCORE BACKTEST (30m+1h+2h StochRSI agrees with FVG dir)")
    print("="*60)
    print("LONG FVGs:")
    for s in range(4):
        d = casc.get(s, {"win": 0, "loss": 0})
        print(f"  score={s}/3   {_wr(d['win'], d['loss']):>10}  wins={d['win']}  losses={d['loss']}")
    print("SHORT FVGs:")
    for s in range(4):
        d = casc_short.get(s, {"win": 0, "loss": 0})
        print(f"  score={s}/3   {_wr(d['win'], d['loss']):>10}  wins={d['win']}  losses={d['loss']}")

    summary = {
        "htf_buckets": {k: dict(v) for k, v in htf.items()},
        "cascade_long":  {str(k): dict(v) for k, v in casc.items()},
        "cascade_short": {str(k): dict(v) for k, v in casc_short.items()},
        "total_fvgs": len(all_results),
        "symbols": len({r.symbol for r in all_results}),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    return summary


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    symbols = TEST_SYMBOLS
    if "--all" in sys.argv:
        from config import SYMBOLS as ALL_SYMBOLS
        symbols = ALL_SYMBOLS
        logger.info("Full run: %d symbols", len(symbols))
    else:
        logger.info("Quick run: %d symbols (use --all for full)", len(symbols))

    all_tfs = ["15m", "30m", "1h", "2h", "4h"]
    all_results: List[FVGResult] = []

    for idx, symbol in enumerate(symbols):
        logger.info("[%d/%d] %s — fetching...", idx + 1, len(symbols), symbol)
        bars_by_tf: Dict[str, List[Bar]] = {}
        for tf in all_tfs:
            bars = fetch_klines(symbol, tf, BARS_PER_TF)
            bars_by_tf[tf] = bars
            time.sleep(SYMBOL_DELAY)

        results = process_symbol(symbol, bars_by_tf)
        all_results.extend(results)
        logger.info("  %s: %d FVGs", symbol, len(results))

    summary = print_report(all_results)

    out = "backtest_htf_results.json"
    with open(out, "w") as f:
        json.dump({"summary": summary, "records": [
            {
                "symbol": r.symbol, "tf": r.tf, "direction": r.direction,
                "born_time": r.born_time, "entry": r.entry,
                "htf_rsi7_4h": r.htf_rsi7_4h, "htf_bucket": r.htf_bucket,
                "htf_outcome": r.htf_outcome,
                "cascade_score": r.cascade_score, "cascade_outcome": r.cascade_outcome,
            } for r in all_results
        ]}, f, indent=2)
    logger.info("Saved to %s", out)


if __name__ == "__main__":
    main()
