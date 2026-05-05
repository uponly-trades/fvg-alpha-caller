"""
Kronos-base inference + decision logic.
derive_decision() processes pre-predicted OHLCV bars (no model needed).
load_model() and predict() load/run the actual Kronos model (Mac Studio only).
"""
from __future__ import annotations
import logging
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

# Lazy-loaded globals — set by load_model()
_predictor = None
_tokenizer = None

# Direction: use max-excursion from entry, not just last-close drift
# Lower threshold = more LONG/SHORT signals, fewer RANGING
DIRECTION_THRESHOLD = 0.0015  # ±0.15% (was 0.3% — too many RANGING)
ATR_MIN_MULT = 0.5
ATR_MAX_MULT = 5.0
PREDICT_STEPS = 10

# TF → pandas freq for realistic timestamp encoding
_TF_FREQ = {
    "15m": "15min", "30m": "30min",
    "1h": "1h", "2h": "2h", "4h": "4h",
}


def load_model(device: str = "mps"):
    """Load Kronos-base and tokenizer once at startup."""
    global _predictor, _tokenizer
    if _predictor is not None:
        return
    try:
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "Kronos"))
        from model import Kronos, KronosTokenizer, KronosPredictor
        _tokenizer = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
        model = Kronos.from_pretrained("NeoQuasar/Kronos-base")
        _predictor = KronosPredictor(model=model, tokenizer=_tokenizer, device=device, max_context=512)
        logger.info("Kronos-base loaded on device=%s", device)
    except Exception as e:
        logger.error("Kronos load failed: %s", e)
        raise


def _run_kronos(bars: List[Dict], tf: str = "15m") -> List[Dict]:
    """Run Kronos inference on OHLCV bars. Uses correct TF freq for timestamps."""
    import pandas as pd
    n = len(bars)
    freq = _TF_FREQ.get(tf, "15min")
    base = pd.Timestamp("2024-01-01")
    x_ts = pd.Series(pd.date_range(base, periods=n, freq=freq))
    y_ts = pd.Series(pd.date_range(
        x_ts.iloc[-1] + pd.Timedelta(freq), periods=PREDICT_STEPS, freq=freq
    ))
    df = pd.DataFrame(bars)[["open", "high", "low", "close", "volume"]]
    df["amount"] = df["close"] * df["volume"]
    df.index = x_ts
    pred_df = _predictor.predict(
        df, x_timestamp=x_ts, y_timestamp=y_ts,
        pred_len=PREDICT_STEPS, verbose=False,
    )
    return pred_df[["open", "high", "low", "close", "volume"]].to_dict(orient="records")


def _classify_timeframe(predicted: List[Dict], direction: str) -> str:
    """SCALPING/INTRADAY/SWING from candle index of predicted peak/trough."""
    closes = [b["close"] for b in predicted]
    if direction == "LONG":
        peak_idx = int(np.argmax(closes))
    elif direction == "SHORT":
        peak_idx = int(np.argmin(closes))
    else:
        return "INTRADAY"
    candle = peak_idx + 1
    if candle <= 3:
        return "SCALPING"
    if candle <= 6:
        return "INTRADAY"
    return "SWING"


def derive_decision(
    predicted: List[Dict],
    current_price: float,
    atr: float,
    zone_direction: int,
    entry: float,
) -> Dict:
    """
    From Kronos predicted bars, derive trading decision.
    Direction based on max-excursion (peak/trough) not just last-close drift —
    more robust for trending moves that reverse before the last candle.
    """
    if not predicted:
        raise ValueError("predicted bars list is empty")

    closes = [b["close"] for b in predicted]
    highs  = [b["high"]  for b in predicted]
    lows   = [b["low"]   for b in predicted]

    # Use best excursion in predicted window vs entry
    max_up   = (max(highs)  - entry) / entry if entry > 0 else 0.0
    max_down = (entry - min(lows))   / entry if entry > 0 else 0.0
    last_pct = (closes[-1]  - entry) / entry if entry > 0 else 0.0

    # Primary: max excursion wins if dominant; secondary: last-close drift
    if max_up > max_down and max_up > DIRECTION_THRESHOLD:
        direction = "LONG"
    elif max_down > max_up and max_down > DIRECTION_THRESHOLD:
        direction = "SHORT"
    elif abs(last_pct) > DIRECTION_THRESHOLD:
        direction = "LONG" if last_pct > 0 else "SHORT"
    else:
        direction = "RANGING"

    timeframe = _classify_timeframe(predicted, direction)

    # Confidence: trend strength vs prediction noise
    # Use relative std of close changes, normalised to entry price
    effective_atr = atr if atr > 0 else abs(entry * 0.005)
    diffs = np.diff(closes)
    noise_ratio = float(np.std(diffs)) / effective_atr if effective_atr > 0 else 1.0

    excursion = max(max_up, max_down)
    trend_strength = min(excursion / (DIRECTION_THRESHOLD * 3), 1.0)  # saturates at 3×threshold
    noise_penalty  = min(noise_ratio * 0.3, 0.5)                       # capped at 50% penalty
    raw_confidence = trend_strength * (1.0 - noise_penalty)
    confidence = int(round(raw_confidence * 100))

    # TP/SL from predicted range, clamped to ATR bounds
    min_risk = effective_atr * ATR_MIN_MULT
    max_risk = effective_atr * ATR_MAX_MULT

    if direction == "LONG":
        raw_tp2_dist = max(highs) - entry
    elif direction == "SHORT":
        raw_tp2_dist = entry - min(lows)
    else:
        raw_tp2_dist = effective_atr

    tp2_dist = float(np.clip(raw_tp2_dist, min_risk, max_risk))
    sl_dist  = tp2_dist / 2.0   # RR 1:2
    tp1_dist = tp2_dist / 2.0

    if direction == "LONG" or (direction == "RANGING" and zone_direction >= 0):
        sl  = entry - sl_dist
        tp1 = entry + tp1_dist
        tp2 = entry + tp2_dist
    else:
        sl  = entry + sl_dist
        tp1 = entry - tp1_dist
        tp2 = entry - tp2_dist

    return {
        "direction":  direction,
        "timeframe":  timeframe,
        "entry":      round(entry, 8),
        "sl":         round(sl,    8),
        "tp1":        round(tp1,   8),
        "tp2":        round(tp2,   8),
        "confidence": confidence,
    }


def predict(
    bars: List[Dict],
    current_price: float,
    atr: float,
    zone_direction: int,
    tf: str = "15m",
) -> Dict:
    """Full pipeline: run Kronos → derive decision. Raises if model not loaded."""
    predicted = _run_kronos(bars, tf=tf)
    return derive_decision(predicted, current_price, atr, zone_direction, entry=current_price)
