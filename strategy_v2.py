from dataclasses import dataclass, field
from typing import Dict, Optional, List

from config import (
    V2_TRIGGER_TFS, V2_HTF_TFS, V2_HTF_WEIGHTS,
    V2_HTF_TOUCH_LOOKBACK, ATR_BUFFER_V2,
)
from fvg_engine import FVGZone, detect_fvg, atr as compute_atr


@dataclass
class V2Signal:
    symbol: str
    direction: int                    # 1 long, -1 short
    trigger_tf: str                   # "15m" or "30m"
    zone_top: float
    zone_bottom: float
    zone_born_time: int
    entry: float
    sl: float
    atr: float
    confluence_score: int             # 1-6
    htf_touches: Dict[str, bool]      # {"1h": bool, "2h": bool, "4h": bool}
    indicators: Dict[str, float] = field(default_factory=dict)

    @property
    def direction_str(self) -> str:
        return "long" if self.direction == 1 else "short"


def _htf_active_and_touched(
    zone: Optional[FVGZone],
    bars: List,
    lookback: int = 1,
) -> bool:
    """True if zone exists, is not fully mitigated, and price overlapped zone
    on any of the last `lookback` closed bars."""
    if zone is None:
        return False
    if zone.mitigation >= 1.0:
        return False
    if not bars or len(bars) < lookback:
        return False
    recent = bars[-lookback:]
    for b in recent:
        if b.high >= zone.bottom and b.low <= zone.top:
            return True
    return False


def _latest_active_zone(
    zones: Dict[str, FVGZone],
    symbol: str,
    tf: str,
    direction: int,
) -> Optional[FVGZone]:
    """Return the most recently born, not-fully-mitigated zone for (symbol, tf, direction)."""
    candidates = [
        z for z in zones.values()
        if z.symbol == symbol
        and z.tf == tf
        and z.direction == direction
        and z.mitigation < 1.0
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda z: z.born_time)


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
