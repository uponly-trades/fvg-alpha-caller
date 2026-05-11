from dataclasses import dataclass
from typing import Dict, List, Optional

from config import MIN_STRENGTH_TO_ALERT
from indicator_context import stochrsi_series
from feature_extractor import extract_tf_features


HTF_TREND_MIN = 4.0   # 4h_ema20_dist_pct (LONG)
LTF_VOL_MIN   = 2.0   # 15m_vol_z (LONG)
LTF_VOL_SPIKE_RATIO_MIN = 1.5  # last closed 15m volume vs prior 20 closed candles
VOL_Z_MAX = 20.0  # guard against partial/misaligned candle spikes
LONG_RSI7_15M_MAX = 75.0   # block long entries: 15m RSI7 overbought → mostly losses
LONG_RSI7_4H_MAX  = 75.0   # block long entries: 4h RSI7 overbought → extended, overdue pullback
LONG_E20_4H_MIN = 4.0      # require strong 4h momentum (1.35-1.77 weak zone removed)
SHORT_E20D_1H_MAX = 0.0   # 1h_ema20_dist_pct must be < 0 (downtrend)
SHORT_BB15_MAX    = 0.4   # 15m_bb_pos must be < 0.4 (lower band)


def _long_filter_pass(bars_by_tf: Dict[str, List]) -> bool:
    """Shadow-backtested LONG filter: HTF trend + LTF volume burst (WR 72% on n=43)."""
    bars_4h  = bars_by_tf.get("4h",  [])
    bars_15m = bars_by_tf.get("15m", [])
    if len(bars_4h) < 30 or len(bars_15m) < 30:
        return False
    f4 = extract_tf_features(bars_4h,  "4h")
    f15 = extract_tf_features(bars_15m, "15m")
    e20d = f4.get("ema20_dist_pct")
    vz   = f15.get("vol_z")
    if e20d is None or vz is None:
        return False
    return e20d >= HTF_TREND_MIN and vz >= LTF_VOL_MIN


def _v2_long_decision(bars_by_tf: Dict[str, List]) -> Dict:
    """
    v2 LONG filter gates (data-driven, n=48 trades):
      1. 15m RSI7 <= 75 — overbought entries lose 88% of the time
      2. 4h RSI7 <= 75 — extended HTF overbought = overdue pullback
      3. 4h_ema20_dist_pct >= 4.0 — strong HTF momentum only
      4. 15m vol_spike_ratio >= 1.5x stable (not partial candle noise)
    """
    bars_4h  = bars_by_tf.get("4h",  [])
    bars_15m = bars_by_tf.get("15m", [])
    if len(bars_4h) < 30 or len(bars_15m) < 30:
        return {"valid": False, "status": "v2 SKIP", "reason": "insufficient bars"}
    f4 = extract_tf_features(bars_4h, "4h")
    f15 = extract_tf_features(bars_15m, "15m")
    e20 = f4.get("ema20_dist_pct")
    rsi7_4h = f4.get("rsi7")
    vol_spike = f15.get("vol_spike_ratio")
    vol_z = f15.get("vol_z")
    rsi7_15m = f15.get("rsi7")
    # Gate 1: 15m RSI7 overbought
    if rsi7_15m is not None and rsi7_15m > LONG_RSI7_15M_MAX:
        return {"valid": False, "status": "v2 SKIP",
                "reason": f"15m_rsi7={rsi7_15m:.1f}>{LONG_RSI7_15M_MAX:g} overbought"}
    # Gate 2: 4h RSI7 overbought — extended HTF = bad long entry
    if rsi7_4h is not None and rsi7_4h > LONG_RSI7_4H_MAX:
        return {"valid": False, "status": "v2 SKIP",
                "reason": f"4h_rsi7={rsi7_4h:.1f}>{LONG_RSI7_4H_MAX:g} HTF overbought"}
    # Gate 3: volume spike quality
    if vol_spike is None or vol_spike < LTF_VOL_SPIKE_RATIO_MIN:
        return {"valid": False, "status": "v2 SKIP",
                "reason": f"15m_vol_spike<{LTF_VOL_SPIKE_RATIO_MIN:g}x (got {vol_spike})"}
    if vol_z is not None and vol_z > VOL_Z_MAX:
        return {"valid": False, "status": "v2 SKIP",
                "reason": f"15m_vol_z>{VOL_Z_MAX:g} unstable (got {vol_z})"}
    # Gate 4: 4h momentum must be strong
    if e20 is None:
        return {"valid": False, "status": "v2 SKIP", "reason": "4h_ema20 missing"}
    if e20 < LONG_E20_4H_MIN:
        return {"valid": False, "status": "v2 SKIP",
                "reason": f"4h_ema20={e20:.2f}<{LONG_E20_4H_MIN:g} weak HTF momentum"}
    return {"valid": True, "status": "v2 LONG VALID",
            "reason": f"15m_rsi7={rsi7_15m:.1f} 4h_rsi7={rsi7_4h:.1f} e20={e20:.2f} spike={vol_spike:.2f}x"}


def _v2_short_decision(bars_by_tf: Dict[str, List]) -> Dict:
    """v2 SHORT: 1h_ema20<0 AND stable 15m volume spike AND oi_change_15m>=0."""
    bars_1h  = bars_by_tf.get("1h",  [])
    bars_15m = bars_by_tf.get("15m", [])
    if len(bars_1h) < 30 or len(bars_15m) < 30:
        return {"valid": False, "status": "v2 SKIP", "reason": "insufficient bars"}
    f1 = extract_tf_features(bars_1h, "1h")
    f15 = extract_tf_features(bars_15m, "15m", with_ls_ratio=False)
    e20 = f1.get("ema20_dist_pct")
    vol_spike = f15.get("vol_spike_ratio")
    vol_z = f15.get("vol_z")
    oi = f15.get("oi_change_pct")
    if e20 is None or vol_spike is None:
        return {"valid": False, "status": "v2 SKIP", "reason": "missing 1h_ema20 or vol_spike"}
    if e20 >= 0:
        return {"valid": False, "status": "v2 SKIP",
                "reason": f"1h_ema20={e20:.2f} not bearish"}
    if vol_spike < LTF_VOL_SPIKE_RATIO_MIN:
        return {"valid": False, "status": "v2 SKIP",
                "reason": f"15m_vol_spike={vol_spike:.2f}x<{LTF_VOL_SPIKE_RATIO_MIN:g}x"}
    if vol_z is not None and vol_z > VOL_Z_MAX:
        return {"valid": False, "status": "v2 SKIP",
                "reason": f"15m_vol_z>{VOL_Z_MAX:g} unstable (got {vol_z})"}
    if oi is None or oi < 0:
        return {"valid": False, "status": "v2 SKIP",
                "reason": f"15m_oi_change={oi} <0 or missing"}
    return {"valid": True, "status": "v2 SHORT VALID",
            "reason": f"1h_ema20={e20:.2f} vol_spike={vol_spike:.2f}x oi={oi:.2f}"}


def v2_decision(zone, bars_by_tf: Dict[str, List]) -> Dict:
    """Shadow v2 filter. Returns {valid, status, reason} dict for parallel logging."""
    if int(zone.direction) == 1:
        return _v2_long_decision(bars_by_tf)
    return _v2_short_decision(bars_by_tf)


def _short_filter_pass(bars_by_tf: Dict[str, List]) -> bool:
    """Shadow-backtested SHORT filter: 1h downtrend + 15m near lower BB (WR 57% on n=14)."""
    bars_1h  = bars_by_tf.get("1h",  [])
    bars_15m = bars_by_tf.get("15m", [])
    if len(bars_1h) < 30 or len(bars_15m) < 30:
        return False
    f1 = extract_tf_features(bars_1h,  "1h")
    f15 = extract_tf_features(bars_15m, "15m")
    e20d_1h = f1.get("ema20_dist_pct")
    bb15    = f15.get("bb_pos")
    if e20d_1h is None or bb15 is None:
        return False
    return e20d_1h < SHORT_E20D_1H_MAX and bb15 < SHORT_BB15_MAX


MODE_TIMEFRAMES = {
    "scalping": ("15m", "30m"),
    "intraday": ("1h", "2h"),
    "swing": ("4h",),
}

COMBO_TIMEFRAMES = {
    "scalping": ("15m", "30m", "1h"),
    "intraday": ("30m", "1h", "2h", "4h"),
    "swing": ("1h", "2h", "4h"),
}


@dataclass(frozen=True)
class TradeLevels:
    direction: str
    entry: float
    sl: float
    tp1: float
    tp2: float
    rr: float


_SPARK_CHARS = " ▁▂▃▄▅▆▇█"

def _sparkline(values: List[Optional[float]], n: int = 10) -> str:
    vals = [v for v in values if v is not None][-n:]
    if not vals:
        return "─" * n
    lo, hi = min(vals), max(vals)
    span = hi - lo or 1.0
    chars = [_SPARK_CHARS[round((v - lo) / span * (len(_SPARK_CHARS) - 1))] for v in vals]
    return "".join(chars).ljust(n)


@dataclass(frozen=True)
class TradeSetupResult:
    status: str
    valid: bool
    mode: Optional[str]
    reason: str
    trade: Optional[TradeLevels]
    combo_states: Dict[str, str]
    sparklines: Dict[str, str] = None
    source: str = "combo"          # "model" | "combo"
    model_raw: Optional[dict] = None  # raw model response for ML logging
    predicted_bars: Optional[list] = None  # model forecast candles for chart
    v2_decision: Optional[dict] = None  # shadow v2 filter result for compare logging


def classify_mode(tf: str) -> Optional[str]:
    for mode, timeframes in MODE_TIMEFRAMES.items():
        if tf in timeframes:
            return mode
    return None


def _latest_stoch_state(bars, direction: int):
    closes = [float(bar.close) for bar in bars]
    k_values, d_values = stochrsi_series(closes)
    pairs = [(k, d) for k, d in zip(k_values, d_values) if k is not None and d is not None]
    if len(pairs) < 2:
        return None, k_values

    prev_k, prev_d = pairs[-2]
    k, d = pairs[-1]
    if direction == 1:
        if k <= 30 and d <= 30:
            return "long", k_values
        if prev_k <= prev_d and k > d and min(prev_k, prev_d, k, d) <= 40:
            return "long", k_values
        if k >= 70 and d >= 70:
            return "short", k_values
    else:
        if k >= 70 and d >= 70:
            return "short", k_values
        if prev_k >= prev_d and k < d and max(prev_k, prev_d, k, d) >= 60:
            return "short", k_values
        if k <= 30 and d <= 30:
            return "long", k_values
    return "neutral", k_values


def _price_too_far(zone, current_price: float) -> bool:
    top = float(zone.top)
    bottom = float(zone.bottom)
    zone_size = abs(top - bottom)
    if zone_size <= 0:
        return True
    if bottom <= current_price <= top:
        return False
    if current_price > top:
        distance = current_price - top
    else:
        distance = bottom - current_price
    return distance > zone_size


def _risk_buffer(zone) -> float:
    zone_size = abs(float(zone.top) - float(zone.bottom))
    atr = float(getattr(zone, "atr", 0.0) or 0.0)
    if atr > 0:
        return atr * 0.8
    return zone_size * 0.8


def _build_trade_levels(zone, current_price: float) -> Optional[TradeLevels]:
    entry = float(current_price)
    buffer = _risk_buffer(zone)

    if int(zone.direction) == 1:
        sl = float(zone.bottom) - buffer
        risk = entry - sl
        if risk <= 0:
            return None
        return TradeLevels(
            direction="long",
            entry=entry,
            sl=sl,
            tp1=entry + risk,
            tp2=entry + risk * 2,
            rr=2.0,
        )

    sl = float(zone.top) + buffer
    risk = sl - entry
    if risk <= 0:
        return None
    return TradeLevels(
        direction="short",
        entry=entry,
        sl=sl,
        tp1=entry - risk,
        tp2=entry - risk * 2,
        rr=2.0,
    )


_TIMEFRAME_MAP = {"SCALPING": "scalping", "INTRADAY": "intraday", "SWING": "swing"}


def build_trade_from_model(model: dict, zone) -> TradeSetupResult:
    """
    Convert model prediction response into TradeSetupResult.
    RANGING → SKIP. Direction must align with zone.direction, else SKIP.
    SL anchored to FVG zone geometry. TP1/TP2 always recomputed from actual
    risk (1R and 2R) so RR is always exactly 1:2 regardless of model's
    raw TP values (which can be arbitrarily small vs a wide zone SL).
    """
    zone_direction = int(zone.direction)
    direction = model.get("direction", "RANGING")
    timeframe = model.get("timeframe", "INTRADAY")
    confidence = model.get("confidence", 0)
    mode = _TIMEFRAME_MAP.get(timeframe, "intraday")

    if direction == "RANGING":
        return TradeSetupResult(
            "SKIP: RANGING", False, mode,
            f"Model ranging (conf {confidence}%)",
            None, {}, {}, source="model", model_raw=model,
        )

    # SHORT side: combo path disabled — skip
    if zone_direction != 1:
        return TradeSetupResult(
            "SKIP: SHORT DISABLED", False, mode,
            "short WR 25% negative EV, disabled",
            None, {}, {}, source="model", model_raw=model,
        )

    # Must align with FVG zone direction
    expected = "LONG" if zone_direction == 1 else "SHORT"
    if direction != expected:
        return TradeSetupResult(
            "SKIP: MODEL CONFLICT", False, mode,
            f"Model {direction} conflicts with {expected} FVG zone (conf {confidence}%)",
            None, {}, {}, source="model", model_raw=model,
        )

    entry = float(model["entry"])
    model_sl = float(model["sl"])
    buffer = _risk_buffer(zone)
    # SL anchored to FVG zone bottom (never inside zone)
    zone_sl = float(zone.bottom) - buffer
    sl = min(model_sl, zone_sl)

    risk = abs(entry - sl)
    if risk <= 0:
        return TradeSetupResult(
            "SKIP: INVALID RISK", False, mode,
            "entry == sl after zone anchor",
            None, {}, {}, source="model", model_raw=model,
        )

    # TP always recomputed from actual risk → guaranteed 1:2 RR
    tp1 = entry + risk        # 1R
    tp2 = entry + risk * 2   # 2R

    trade = TradeLevels(
        direction=direction.lower(),
        entry=entry,
        sl=sl,
        tp1=tp1,
        tp2=tp2,
        rr=2.0,
    )
    status = f"{direction} VALID"
    reason = f"Model {direction.lower()} — {timeframe.lower()} (conf {confidence}%)"
    return TradeSetupResult(
        status, True, mode, reason, trade, {}, {},
        source="model", model_raw=model,
        predicted_bars=model.get("predicted_bars"),
    )


def evaluate_for_mode(zone, mode: str, current_price: float, bars_by_tf: Dict[str, List]) -> TradeSetupResult:
    """Evaluate a specific trade mode regardless of zone.tf. Used to simulate all modes per FVG."""
    if mode not in COMBO_TIMEFRAMES:
        return TradeSetupResult("SKIP: MISSING DATA", False, mode, "unsupported mode", None, {}, {})

    if int(getattr(zone, "main_strength", 0)) < MIN_STRENGTH_TO_ALERT:
        return TradeSetupResult("SKIP: WEAK FVG", False, mode, "FVG strength below alert threshold", None, {}, {})

    # SHORT side: blanket WR 10%, but reversal filter (1h downtrend + 15m near lower BB)
    # rescues WR 57% on n=14. Gate strictly.
    if int(zone.direction) != 1:
        if not _short_filter_pass(bars_by_tf):
            return TradeSetupResult(
                "SKIP: SHORT FILTER", False, mode,
                "bearish FVG without reversal context (1h_ema20_dist_pct<0 AND 15m_bb_pos<0.4)",
                None, {}, {},
            )

    required_tfs = COMBO_TIMEFRAMES[mode]
    all_tfs = ("15m", "30m", "1h", "2h", "4h")
    combo_states = {}
    sparklines = {}
    for tf in all_tfs:
        state, k_vals = _latest_stoch_state(bars_by_tf.get(tf, []), int(zone.direction))
        sparklines[tf] = _sparkline(k_vals)
        if tf in required_tfs:
            if state is None:
                return TradeSetupResult(
                    "SKIP: MISSING DATA", False, mode,
                    f"missing StochRSI data for {tf}", None, combo_states, sparklines,
                )
            combo_states[tf] = state

    desired = "long" if int(zone.direction) == 1 else "short"
    matches = sum(1 for state in combo_states.values() if state == desired)
    conflicts = sum(1 for state in combo_states.values() if state not in {desired, "neutral"})
    if conflicts or matches < max(2, len(required_tfs) - 1):
        # MIXED COMBO bypass — only for LONG (filter validated on bullish FVG only).
        # Bearish FVG that reaches here already passed _short_filter_pass above.
        is_long = int(zone.direction) == 1
        if is_long and not _long_filter_pass(bars_by_tf):
            return TradeSetupResult(
                "SKIP: MIXED COMBO", False, mode,
                "combo mixed and HTF filter failed", None, combo_states, sparklines,
            )

    if _price_too_far(zone, current_price):
        return TradeSetupResult("SKIP: FAR FROM FVG", False, mode, "price is too far from FVG zone", None, combo_states, sparklines)

    trade = _build_trade_levels(zone, current_price)
    if trade is None:
        return TradeSetupResult("SKIP: INVALID RISK", False, mode, "risk is zero or invalid", None, combo_states, sparklines)

    direction_text = "LONG" if int(zone.direction) == 1 else "SHORT"
    reason = f"{desired} FVG with aligned StochRSI combo"
    return TradeSetupResult(f"{direction_text} VALID", True, mode, reason, trade, combo_states, sparklines)


def evaluate_trade_setup(zone, current_price: float, bars_by_tf: Dict[str, List]) -> TradeSetupResult:
    mode = classify_mode(zone.tf)
    if mode is None:
        return TradeSetupResult("SKIP: MISSING DATA", False, None, "unsupported timeframe", None, {}, {})
    return evaluate_for_mode(zone, mode, current_price, bars_by_tf)


# ---------------------------------------------------------------------------
# Mitigated FVG shadow builders.
#
# A mitigation = price has fully traversed the FVG zone:
#   bullish FVG mitigated = price closed BELOW zone.bottom (support broke).
#   bearish FVG mitigated = price closed ABOVE zone.top    (resistance broke).
#
# Two competing hypotheses are logged per mitigation event:
#   BREAKOUT (continuation): bullish->SHORT, bearish->LONG.
#     SL anchored on the OPPOSITE side of the zone from breakout direction
#     (zone.top + buffer for short, zone.bottom - buffer for long).
#   REVERSAL (mean revert): bullish->LONG, bearish->SHORT.
#     SL anchored just past current_price (overshoot side); allow more room
#     because the bounce takes time. TP back into the FVG.
#
# Both use RR = 1:2 (tp1 = 1R, tp2 = 2R) for simulator parity.
# ---------------------------------------------------------------------------

_MITIG_REVERSAL_BUFFER_ATR = 0.5  # wider than entry-style 0.1; bounce needs room
_MITIG_REVERSAL_BUFFER_FRAC = 0.3  # fallback if no atr


def _mitig_buffer(zone) -> float:
    """Standard buffer used for breakout SL (same as entry-style trades)."""
    return _risk_buffer(zone)


def _mitig_reversal_buffer(zone) -> float:
    zone_size = abs(float(zone.top) - float(zone.bottom))
    atr = float(getattr(zone, "atr", 0.0) or 0.0)
    if atr > 0:
        return atr * _MITIG_REVERSAL_BUFFER_ATR
    return zone_size * _MITIG_REVERSAL_BUFFER_FRAC


def build_mitigated_breakout(zone, current_price: float) -> TradeSetupResult:
    """
    Continuation trade after FVG mitigation. Direction OPPOSITE of zone.
    Bullish mitigated -> SHORT (broke support). SL above zone.top.
    Bearish mitigated -> LONG  (broke resistance). SL below zone.bottom.
    """
    entry = float(current_price)
    buffer = _mitig_buffer(zone)
    zone_dir = int(zone.direction)

    if zone_dir == 1:
        # Bullish FVG mitigated -> SHORT continuation
        sl = float(zone.top) + buffer
        risk = sl - entry
        if risk <= 0:
            return TradeSetupResult(
                "SKIP: MITIG BREAKOUT INVALID RISK", False, None,
                "breakout short SL not above entry", None, {}, {},
                source="mitigated_breakout",
            )
        trade = TradeLevels(
            direction="short", entry=entry, sl=sl,
            tp1=entry - risk, tp2=entry - risk * 2, rr=2.0,
        )
        reason = "bullish FVG mitigated -> short continuation"
    else:
        # Bearish FVG mitigated -> LONG continuation
        sl = float(zone.bottom) - buffer
        risk = entry - sl
        if risk <= 0:
            return TradeSetupResult(
                "SKIP: MITIG BREAKOUT INVALID RISK", False, None,
                "breakout long SL not below entry", None, {}, {},
                source="mitigated_breakout",
            )
        trade = TradeLevels(
            direction="long", entry=entry, sl=sl,
            tp1=entry + risk, tp2=entry + risk * 2, rr=2.0,
        )
        reason = "bearish FVG mitigated -> long continuation"

    return TradeSetupResult(
        "MITIG BREAKOUT VALID", True, None, reason, trade, {}, {},
        source="mitigated_breakout",
    )


def build_mitigated_reversal(zone, current_price: float) -> TradeSetupResult:
    """
    Mean-revert trade after FVG mitigation. Direction SAME as zone.
    Bullish mitigated -> LONG (price overshot down, expect bounce back up).
        SL = current_price - reversal_buffer (give bounce room).
    Bearish mitigated -> SHORT (price overshot up, expect rejection).
        SL = current_price + reversal_buffer.
    RR fixed 1:2.
    """
    entry = float(current_price)
    buffer = _mitig_reversal_buffer(zone)
    zone_dir = int(zone.direction)

    if zone_dir == 1:
        # Bullish FVG mitigated -> LONG reversal
        sl = entry - buffer
        risk = entry - sl
        if risk <= 0:
            return TradeSetupResult(
                "SKIP: MITIG REVERSAL INVALID RISK", False, None,
                "reversal long buffer is zero", None, {}, {},
                source="mitigated_reversal",
            )
        trade = TradeLevels(
            direction="long", entry=entry, sl=sl,
            tp1=entry + risk, tp2=entry + risk * 2, rr=2.0,
        )
        reason = "bullish FVG mitigated -> long reversal"
    else:
        # Bearish FVG mitigated -> SHORT reversal
        sl = entry + buffer
        risk = sl - entry
        if risk <= 0:
            return TradeSetupResult(
                "SKIP: MITIG REVERSAL INVALID RISK", False, None,
                "reversal short buffer is zero", None, {}, {},
                source="mitigated_reversal",
            )
        trade = TradeLevels(
            direction="short", entry=entry, sl=sl,
            tp1=entry - risk, tp2=entry - risk * 2, rr=2.0,
        )
        reason = "bearish FVG mitigated -> short reversal"

    return TradeSetupResult(
        "MITIG REVERSAL VALID", True, None, reason, trade, {}, {},
        source="mitigated_reversal",
    )
