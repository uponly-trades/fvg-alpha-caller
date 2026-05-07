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
