from dataclasses import dataclass
from typing import Dict, List, Optional

from config import MIN_STRENGTH_TO_ALERT
from indicator_context import stochrsi_series
from feature_extractor import extract_tf_features


HTF_TREND_MIN = 4.0   # 4h_ema20_dist_pct (LONG)
LTF_VOL_MIN   = 2.0   # 15m_vol_z (LONG)
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
    """v2 LONG: vol_change_15m >= 50 AND (4h_ema20 >= 4 OR 1.35-1.77)."""
    bars_4h  = bars_by_tf.get("4h",  [])
    bars_15m = bars_by_tf.get("15m", [])
    if len(bars_4h) < 30 or len(bars_15m) < 30:
        return {"valid": False, "status": "v2 SKIP", "reason": "insufficient bars"}
    f4 = extract_tf_features(bars_4h, "4h")
    f15 = extract_tf_features(bars_15m, "15m")
    e20 = f4.get("ema20_dist_pct")
    vc = f15.get("vol_change_pct")
    if vc is None or vc < 50:
        return {"valid": False, "status": "v2 SKIP",
                "reason": f"15m_vol_change<50 (got {vc})"}
    if e20 is None:
        return {"valid": False, "status": "v2 SKIP", "reason": "4h_ema20 missing"}
    if e20 >= 4.0 or (1.35 <= e20 <= 1.77):
        return {"valid": True, "status": "v2 LONG VALID",
                "reason": f"4h_ema20={e20:.2f} | 15m_vc={vc:.0f}"}
    return {"valid": False, "status": "v2 SKIP",
            "reason": f"4h_ema20={e20:.2f} not in [4+ or 1.35-1.77]"}


def _v2_short_decision(bars_by_tf: Dict[str, List]) -> Dict:
    """v2 SHORT: 1h_ema20<0 AND vol_change_15m>=100 AND oi_change_15m>=0."""
    bars_1h  = bars_by_tf.get("1h",  [])
    bars_15m = bars_by_tf.get("15m", [])
    if len(bars_1h) < 30 or len(bars_15m) < 30:
        return {"valid": False, "status": "v2 SKIP", "reason": "insufficient bars"}
    f1 = extract_tf_features(bars_1h, "1h")
    f15 = extract_tf_features(bars_15m, "15m", with_ls_ratio=False)
    e20 = f1.get("ema20_dist_pct")
    vc = f15.get("vol_change_pct")
    oi = f15.get("oi_change_pct")
    if e20 is None or vc is None:
        return {"valid": False, "status": "v2 SKIP", "reason": "missing 1h_ema20 or vol_change"}
    if e20 >= 0:
        return {"valid": False, "status": "v2 SKIP",
                "reason": f"1h_ema20={e20:.2f} not bearish"}
    if vc < 100:
        return {"valid": False, "status": "v2 SKIP",
                "reason": f"15m_vol_change={vc:.0f}<100"}
    if oi is None or oi < 0:
        return {"valid": False, "status": "v2 SKIP",
                "reason": f"15m_oi_change={oi} <0 or missing"}
    return {"valid": True, "status": "v2 SHORT VALID",
            "reason": f"1h_ema20={e20:.2f} vc={vc:.0f} oi={oi:.2f}"}


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
    source: str = "combo"          # "kronos" | "combo"
    kronos_raw: Optional[dict] = None  # raw Kronos response for ML logging
    predicted_bars: Optional[list] = None  # Kronos forecast candles for chart
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
        return atr * 0.1
    return zone_size * 0.1


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


def build_trade_from_kronos(kronos: dict, zone_direction: int) -> TradeSetupResult:
    """
    Convert Kronos prediction response into TradeSetupResult.
    RANGING → SKIP. Direction must align with zone_direction, else SKIP.
    """
    direction = kronos.get("direction", "RANGING")
    timeframe = kronos.get("timeframe", "INTRADAY")
    confidence = kronos.get("confidence", 0)
    mode = _TIMEFRAME_MAP.get(timeframe, "intraday")

    if direction == "RANGING":
        return TradeSetupResult(
            "SKIP: RANGING", False, mode,
            f"Kronos ranging (conf {confidence}%)",
            None, {}, {}, source="kronos", kronos_raw=kronos,
        )

    # SHORT side: filter handled inside evaluate_for_mode (bars-aware).
    # Kronos path can't see bars — leave SHORT to combo path so filter applies.
    if zone_direction != 1:
        return TradeSetupResult(
            "SKIP: SHORT VIA COMBO", False, mode,
            "bearish FVG routed to combo path for reversal filter",
            None, {}, {}, source="kronos", kronos_raw=kronos,
        )

    # Must align with FVG zone direction
    expected = "LONG" if zone_direction == 1 else "SHORT"
    if direction != expected:
        return TradeSetupResult(
            "SKIP: KRONOS CONFLICT", False, mode,
            f"Kronos {direction} conflicts with {expected} FVG zone (conf {confidence}%)",
            None, {}, {}, source="kronos", kronos_raw=kronos,
        )

    trade = TradeLevels(
        direction=direction.lower(),
        entry=float(kronos["entry"]),
        sl=float(kronos["sl"]),
        tp1=float(kronos["tp1"]),
        tp2=float(kronos["tp2"]),
        rr=2.0,
    )
    status = f"{direction} VALID"
    reason = f"Kronos {direction.lower()} — {timeframe.lower()} (conf {confidence}%)"
    return TradeSetupResult(
        status, True, mode, reason, trade, {}, {},
        source="kronos", kronos_raw=kronos,
        predicted_bars=kronos.get("predicted_bars"),
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
