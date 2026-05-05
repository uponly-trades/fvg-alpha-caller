from dataclasses import dataclass
from typing import Dict, List, Optional

from config import MIN_STRENGTH_TO_ALERT
from indicator_context import stochrsi_series


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


def build_trade_from_kronos(kronos: dict) -> TradeSetupResult:
    """
    Convert Kronos prediction response into TradeSetupResult.
    RANGING → SKIP. LONG/SHORT → valid trade with Kronos levels.
    """
    direction = kronos.get("direction", "RANGING")
    timeframe = kronos.get("timeframe", "INTRADAY")
    confidence = kronos.get("confidence", 0)
    mode = _TIMEFRAME_MAP.get(timeframe, "intraday")

    if direction == "RANGING":
        return TradeSetupResult(
            f"SKIP: RANGING", False, mode,
            f"Kronos predicts ranging market (confidence {confidence}%)",
            None, {}, {},
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
    reason = f"Kronos {direction.lower()} signal — {timeframe.lower()} (confidence {confidence}%)"
    return TradeSetupResult(status, True, mode, reason, trade, {}, {})


def evaluate_for_mode(zone, mode: str, current_price: float, bars_by_tf: Dict[str, List]) -> TradeSetupResult:
    """Evaluate a specific trade mode regardless of zone.tf. Used to simulate all modes per FVG."""
    if mode not in COMBO_TIMEFRAMES:
        return TradeSetupResult("SKIP: MISSING DATA", False, mode, "unsupported mode", None, {}, {})

    if int(getattr(zone, "main_strength", 0)) < MIN_STRENGTH_TO_ALERT:
        return TradeSetupResult("SKIP: WEAK FVG", False, mode, "FVG strength below alert threshold", None, {}, {})

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
        return TradeSetupResult("SKIP: MIXED COMBO", False, mode, "combo timeframes are mixed", None, combo_states, sparklines)

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
