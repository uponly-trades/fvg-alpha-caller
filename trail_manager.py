from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from config import V2_TRAIL_ATR_BUFFER


@dataclass
class TrailState:
    signal_id: str
    symbol: str
    trigger_tf: str
    direction: int                # 1 long, -1 short
    entry: float
    current_sl: float
    atr: float
    last_update_time: int = 0
    closed: bool = False
    close_reason: str = ""


class TrailManager:
    """In-memory wick-by-wick trail manager. Persisted state lives in DB layer
    (sim_trades / executor_state) — this class only manages live ratchet logic."""

    def __init__(self):
        self._states: Dict[str, TrailState] = {}

    def register(
        self, signal_id: str, symbol: str, trigger_tf: str,
        direction: int, entry: float, sl: float, atr: float,
    ) -> Optional[TrailState]:
        if signal_id in self._states:
            return None
        state = TrailState(
            signal_id=signal_id, symbol=symbol, trigger_tf=trigger_tf,
            direction=direction, entry=entry, current_sl=sl, atr=atr,
        )
        self._states[signal_id] = state
        return state

    def snapshot(self) -> List[TrailState]:
        return list(self._states.values())

    def get(self, signal_id: str) -> Optional[TrailState]:
        return self._states.get(signal_id)
