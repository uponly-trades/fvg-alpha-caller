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

DIRECTION_THRESHOLD = 0.003   # ±0.3% to determine LONG/SHORT vs RANGING
ATR_MIN_MULT = 0.5
ATR_MAX_MULT = 5.0
PREDICT_STEPS = 10


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


def _run_kronos(bars: List[Dict]) -> List[Dict]:
    """Run Kronos inference on OHLCV bars list. Returns predicted bars list."""
    import pandas as pd
    n = len(bars)
    # Generate monotonic 1-minute timestamps (relative — model only uses cyclical features)
    base = pd.Timestamp("2024-01-01")
    x_ts = pd.Series(pd.date_range(base, periods=n, freq="1min"))
    y_ts = pd.Series(pd.date_range(x_ts.iloc[-1] + pd.Timedelta("1min"), periods=PREDICT_STEPS, freq="1min"))
    df = pd.DataFrame(bars)[["open", "high", "low", "close", "volume"]]
    df["amount"] = df["close"] * df["volume"]  # required by Kronos tokenizer
    df.index = x_ts
    pred_df = _predictor.predict(df, x_timestamp=x_ts, y_timestamp=y_ts, pred_len=PREDICT_STEPS, verbose=False)
    return pred_df[["open", "high", "low", "close", "volume"]].to_dict(orient="records")


def _classify_timeframe(predicted: List[Dict], direction: str) -> str:
    """Determine SCALPING/INTRADAY/SWING from how many candles until predicted peak/trough."""
    closes = [b["close"] for b in predicted]
    if direction == "LONG":
        peak_idx = int(np.argmax(closes))
    elif direction == "SHORT":
        peak_idx = int(np.argmin(closes))
    else:
        return "INTRADAY"

    # 1-indexed candle number
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
    Returns dict with: direction, timeframe, entry, sl, tp1, tp2, confidence.
    """
    if not predicted:
        raise ValueError("predicted bars list is empty")

    closes = [b["close"] for b in predicted]
    highs = [b["high"] for b in predicted]
    lows = [b["low"] for b in predicted]

    trend_pct = (closes[-1] - closes[0]) / closes[0] if closes[0] != 0 else 0.0

    if trend_pct > DIRECTION_THRESHOLD:
        direction = "LONG"
    elif trend_pct < -DIRECTION_THRESHOLD:
        direction = "SHORT"
    else:
        direction = "RANGING"

    timeframe = _classify_timeframe(predicted, direction)

    # Confidence: combination of trend strength and prediction consistency
    std_dev = float(np.std(np.diff(closes))) if len(closes) > 1 else 0.0
    trend_strength = min(abs(trend_pct) / DIRECTION_THRESHOLD, 3.0) / 3.0  # 0-1
    noise_penalty = min(std_dev / (atr + 1e-9), 1.0)
    raw_confidence = trend_strength * (1 - noise_penalty * 0.5)
    confidence = int(round(raw_confidence * 100))

    # TP/SL from predicted high/low, clamped to [0.5×ATR, 5×ATR]
    effective_atr = atr if atr > 0 else abs(entry * 0.001)
    min_risk = effective_atr * ATR_MIN_MULT
    max_risk = effective_atr * ATR_MAX_MULT

    if direction == "LONG":
        raw_tp2_dist = max(highs) - entry
    elif direction == "SHORT":
        raw_tp2_dist = entry - min(lows)
    else:
        # RANGING: use 1x ATR as default
        raw_tp2_dist = effective_atr

    tp2_dist = float(np.clip(raw_tp2_dist, min_risk, max_risk))
    tp1_dist = tp2_dist / 2.0
    sl_dist = tp2_dist / 2.0  # RR 1:2 — risk = half of tp2_dist

    if direction == "LONG" or (direction == "RANGING" and zone_direction >= 0):
        sl = entry - sl_dist
        tp1 = entry + tp1_dist
        tp2 = entry + tp2_dist
    else:  # SHORT or RANGING with bearish zone
        sl = entry + sl_dist
        tp1 = entry - tp1_dist
        tp2 = entry - tp2_dist

    return {
        "direction": direction,
        "timeframe": timeframe,
        "entry": round(entry, 8),
        "sl": round(sl, 8),
        "tp1": round(tp1, 8),
        "tp2": round(tp2, 8),
        "confidence": confidence,
    }


def predict(bars: List[Dict], current_price: float, atr: float, zone_direction: int) -> Dict:
    """Full pipeline: run Kronos → derive decision. Raises if model not loaded."""
    predicted = _run_kronos(bars)
    return derive_decision(predicted, current_price, atr, zone_direction, entry=current_price)
