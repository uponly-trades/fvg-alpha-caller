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


@dataclass(frozen=True)
class TradeSetupResult:
    status: str
    valid: bool
    mode: Optional[str]
    reason: str
    trade: Optional[TradeLevels]
    combo_states: Dict[str, str]


def classify_mode(tf: str) -> Optional[str]:
    for mode, timeframes in MODE_TIMEFRAMES.items():
        if tf in timeframes:
            return mode
    return None


def _latest_stoch_state(bars, direction: int) -> Optional[str]:
    closes = [float(bar.close) for bar in bars]
    k_values, d_values = stochrsi_series(closes)
    pairs = [(k, d) for k, d in zip(k_values, d_values) if k is not None and d is not None]
    if len(pairs) < 2:
        return None

    prev_k, prev_d = pairs[-2]
    k, d = pairs[-1]
    if direction == 1:
        if k <= 30 and d <= 30:
            return "long"
        if prev_k <= prev_d and k > d and min(prev_k, prev_d, k, d) <= 40:
            return "long"
        if k >= 70 and d >= 70:
            return "short"
    else:
        if k >= 70 and d >= 70:
            return "short"
        if prev_k >= prev_d and k < d and max(prev_k, prev_d, k, d) >= 60:
            return "short"
        if k <= 30 and d <= 30:
            return "long"
    return "neutral"


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


def evaluate_trade_setup(zone, current_price: float, bars_by_tf: Dict[str, List]) -> TradeSetupResult:
    mode = classify_mode(zone.tf)
    if mode is None:
        return TradeSetupResult("SKIP: MISSING DATA", False, None, "unsupported timeframe", None, {})

    if int(getattr(zone, "main_strength", 0)) < MIN_STRENGTH_TO_ALERT:
        return TradeSetupResult("SKIP: WEAK FVG", False, mode, "FVG strength below alert threshold", None, {})

    required_tfs = COMBO_TIMEFRAMES[mode]
    combo_states = {}
    for tf in required_tfs:
        state = _latest_stoch_state(bars_by_tf.get(tf, []), int(zone.direction))
        if state is None:
            return TradeSetupResult(
                "SKIP: MISSING DATA",
                False,
                mode,
                f"missing StochRSI data for {tf}",
                None,
                combo_states,
            )
        combo_states[tf] = state

    desired = "long" if int(zone.direction) == 1 else "short"
    matches = sum(1 for state in combo_states.values() if state == desired)
    conflicts = sum(1 for state in combo_states.values() if state not in {desired, "neutral"})
    if conflicts or matches < max(2, len(required_tfs) - 1):
        return TradeSetupResult("SKIP: MIXED COMBO", False, mode, "combo timeframes are mixed", None, combo_states)

    if _price_too_far(zone, current_price):
        return TradeSetupResult("SKIP: FAR FROM FVG", False, mode, "price is too far from FVG zone", None, combo_states)

    trade = _build_trade_levels(zone, current_price)
    if trade is None:
        return TradeSetupResult("SKIP: INVALID RISK", False, mode, "risk is zero or invalid", None, combo_states)

    direction_text = "LONG" if int(zone.direction) == 1 else "SHORT"
    reason = f"{desired} FVG with aligned StochRSI combo"
    return TradeSetupResult(f"{direction_text} VALID", True, mode, reason, trade, combo_states)
