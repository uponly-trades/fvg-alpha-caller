from dataclasses import dataclass, field
from typing import Dict, Optional, List

from config import (
    V2_TRIGGER_TFS, V2_HTF_TFS, V2_HTF_WEIGHTS, V2_HTF_MIN_SCORE, V2_RR,
    V2_HTF_TOUCH_LOOKBACK, ATR_BUFFER_V2,
)
from fvg_engine import FVGZone, detect_fvg, atr as compute_atr


@dataclass
class V2Signal:
    symbol: str
    direction: int                    # 1 long, -1 short
    trigger_tf: str                   # "15m"
    zone_top: float
    zone_bottom: float
    zone_born_time: int
    entry: float
    sl: float
    tp: float                         # entry ± R × V2_RR (display + initial executor TP2)
    atr: float
    confluence_score: int             # 1-4 (flat weights)
    htf_touches: Dict[str, bool]      # {"30m": bool, "1h": bool, "2h": bool, "4h": bool}
    fvg_buy_volume: float = 0.0       # taker buy volume of the 3-bar FVG formation
    fvg_sell_volume: float = 0.0
    indicators: Dict[str, float] = field(default_factory=dict)

    @property
    def direction_str(self) -> str:
        return "long" if self.direction == 1 else "short"


def _htf_active_and_touched(
    zone: Optional[FVGZone],
    bars: List,
    lookback: int = 1,
) -> bool:
    """Pine-parity touch: bull → low <= zone.top, bear → high >= zone.bottom.
    Single-edge probe over last `lookback` closed bars."""
    if zone is None:
        return False
    if zone.mitigation >= 1.0:
        return False
    if not bars:
        return False
    n = min(lookback, len(bars))
    recent = bars[-n:]
    if zone.direction == 1:
        for b in recent:
            if b.low <= zone.top:
                return True
    else:
        for b in recent:
            if b.high >= zone.bottom:
                return True
    return False


_TF_SECONDS = {"15m": 900, "30m": 1800, "1h": 3600, "2h": 7200, "4h": 14400}


def _zone_live_quality(z: FVGZone, now_ms: int) -> float:
    """Zeiierman-parity live quality score with mitigation + age decay.

    qualityScore = fvgSize*100 + volScore*10 + trendScore*20 - mit*50 - age*0.1
    age = bars elapsed since born_time (approximated via tf_seconds).
    """
    base = z.size * 100 + z.volume_score * 10 + z.trend_score * 20
    tf_sec = _TF_SECONDS.get(z.tf, 900)
    age_bars = max(0, (now_ms - z.born_time) // (tf_sec * 1000))
    return base - z.mitigation * 50 - age_bars * 0.1


# Top-N zones the chart actually displays. Pine default maxZones=10.
_VISIBLE_TOP_N = 10


def _visible_top_zones(
    zones: Dict[str, FVGZone],
    symbol: str,
    tf: str,
    direction: int,
) -> List[FVGZone]:
    """Top-N zones the chart displays — Pine indicator default maxZones=10.
    Sorted by live quality_score descending."""
    import time
    now_ms = int(time.time() * 1000)
    candidates = [
        z for z in zones.values()
        if z.symbol == symbol
        and z.tf == tf
        and z.direction == direction
        and z.mitigation < 1.0
    ]
    if not candidates:
        return []
    candidates.sort(key=lambda z: _zone_live_quality(z, now_ms), reverse=True)
    return candidates[:_VISIBLE_TOP_N]


def _latest_active_zone(
    zones: Dict[str, FVGZone],
    symbol: str,
    tf: str,
    direction: int,
) -> Optional[FVGZone]:
    """Pine alert behaviour: of the top-N visible zones (the ones the chart
    actually shows), return the one most recently born. This preserves v1
    'fresh signal' behaviour while filtering out micro low-quality zones the
    user can't see on their chart."""
    visible = _visible_top_zones(zones, symbol, tf, direction)
    if not visible:
        return None
    return max(visible, key=lambda z: z.born_time)


def _compute_htf_confluence(
    zones: Dict[str, FVGZone],
    symbol: str,
    direction: int,
    bars_by_tf: Dict[str, List],
) -> tuple:
    """Returns (score, touches_dict). Score sums weights for HTFs that have an
    active same-direction zone touched within V2_HTF_TOUCH_LOOKBACK."""
    touches: Dict[str, bool] = {}
    score = 0
    for tf in V2_HTF_TFS:
        zone = _latest_active_zone(zones, symbol, tf, direction)
        bars = bars_by_tf.get(tf, [])
        touched = _htf_active_and_touched(zone, bars, lookback=V2_HTF_TOUCH_LOOKBACK)
        touches[tf] = touched
        if touched:
            score += V2_HTF_WEIGHTS[tf]
    return score, touches


_TRIGGER_TOUCH_LOOKBACK = 1  # Pine alert fires on current bar only — match parity


def _trigger_zone_touched(zone: Optional[FVGZone], bars: List) -> Optional[FVGZone]:
    """Pine-parity touch on last `_TRIGGER_TOUCH_LOOKBACK` closed bars.
    Bull: low <= zone.top. Bear: high >= zone.bottom."""
    if zone is None or zone.mitigation >= 1.0:
        return None
    if not bars:
        return None
    n = min(_TRIGGER_TOUCH_LOOKBACK, len(bars))
    recent = bars[-n:]
    if zone.direction == 1:
        for b in recent:
            if b.low <= zone.top:
                return zone
    else:
        for b in recent:
            if b.high >= zone.bottom:
                return zone
    return None


def _compute_sl(zone: FVGZone, atr_val: float) -> float:
    """SL = zone edge ± ATR*buffer. Below FVG bottom for long, above top for short."""
    buf = atr_val * ATR_BUFFER_V2
    if zone.direction == 1:
        return zone.bottom - buf
    return zone.top + buf


def evaluate_v2_signal(
    symbol: str,
    zones: Dict[str, FVGZone],
    bars_by_tf: Dict[str, List],
) -> Optional[V2Signal]:
    """Multi-TF FVG touch confluence detector.

    Returns V2Signal if:
      1. 15m FVG (any strength, any direction) is touched on latest bar.
      2. HTF confluence score ≥ V2_HTF_MIN_SCORE across {30m, 1h, 2h, 4h}.
    """
    for direction in (1, -1):
        for trigger_tf in V2_TRIGGER_TFS:
            # Walk visible top-N zones (chart-displayed). Newest born first
            # to favor recent setups, but only zones that actually appear on
            # the user's Zeiierman chart (top-10 by quality).
            visible = _visible_top_zones(zones, symbol, trigger_tf, direction)
            if not visible:
                continue
            visible.sort(key=lambda z: z.born_time, reverse=True)
            triggered = None
            bars = bars_by_tf.get(trigger_tf, [])
            for z in visible:
                hit = _trigger_zone_touched(z, bars)
                if hit is not None:
                    triggered = hit
                    break
            if triggered is None:
                continue
            score, touches = _compute_htf_confluence(zones, symbol, direction, bars_by_tf)
            if score < V2_HTF_MIN_SCORE:
                continue

            atr_val = float(triggered.atr) if triggered.atr else 0.0
            if atr_val <= 0:
                bars = bars_by_tf.get(trigger_tf, [])
                if len(bars) >= 15:
                    highs = [b.high for b in bars]
                    lows = [b.low for b in bars]
                    closes = [b.close for b in bars]
                    atr_val = compute_atr(highs, lows, closes, 14) or triggered.size
                else:
                    atr_val = triggered.size

            sl = _compute_sl(triggered, atr_val)
            entry = float(bars_by_tf[trigger_tf][-1].close)
            r = abs(entry - sl)
            tp = entry + r * V2_RR if direction == 1 else entry - r * V2_RR

            return V2Signal(
                symbol=symbol,
                direction=direction,
                trigger_tf=trigger_tf,
                zone_top=triggered.top,
                zone_bottom=triggered.bottom,
                zone_born_time=triggered.born_time,
                entry=entry,
                sl=sl,
                tp=tp,
                atr=atr_val,
                confluence_score=score,
                htf_touches=touches,
                fvg_buy_volume=getattr(triggered, "fvg_buy_volume", 0.0),
                fvg_sell_volume=getattr(triggered, "fvg_sell_volume", 0.0),
                indicators={},
            )
    return None
