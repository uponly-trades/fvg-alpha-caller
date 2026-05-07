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
    initial_sl: float = 0.0
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
            initial_sl=sl,
        )
        self._states[signal_id] = state
        return state

    def snapshot(self) -> List[TrailState]:
        return list(self._states.values())

    def get(self, signal_id: str) -> Optional[TrailState]:
        return self._states.get(signal_id)


@dataclass
class TrailUpdate:
    signal_id: str
    symbol: str
    trigger_tf: str
    direction: int
    previous_sl: float
    new_sl: float
    entry: float = 0.0
    initial_sl: float = 0.0


def _new_sl_long(prev_low: float, atr_val: float) -> float:
    return prev_low - atr_val * V2_TRAIL_ATR_BUFFER


def _new_sl_short(prev_high: float, atr_val: float) -> float:
    return prev_high + atr_val * V2_TRAIL_ATR_BUFFER


def _on_bar_close(self, symbol: str, tf: str, bars) -> List[TrailUpdate]:
    if len(bars) < 2:
        return []
    prev = bars[-2]
    updates: List[TrailUpdate] = []
    for state in list(self._states.values()):
        if state.closed:
            continue
        if state.symbol != symbol or state.trigger_tf != tf:
            continue
        if state.direction == 1:
            candidate = _new_sl_long(prev.low, state.atr)
            if candidate > state.current_sl:
                prev_sl = state.current_sl
                state.current_sl = candidate
                state.last_update_time = prev.open_time
                updates.append(TrailUpdate(
                    signal_id=state.signal_id, symbol=symbol, trigger_tf=tf,
                    direction=1, previous_sl=prev_sl, new_sl=candidate,
                    entry=state.entry, initial_sl=state.initial_sl,
                ))
        else:
            candidate = _new_sl_short(prev.high, state.atr)
            if candidate < state.current_sl:
                prev_sl = state.current_sl
                state.current_sl = candidate
                state.last_update_time = prev.open_time
                updates.append(TrailUpdate(
                    signal_id=state.signal_id, symbol=symbol, trigger_tf=tf,
                    direction=-1, previous_sl=prev_sl, new_sl=candidate,
                    entry=state.entry, initial_sl=state.initial_sl,
                ))
    return updates


TrailManager.on_bar_close = _on_bar_close


@dataclass
class TrailStop:
    signal_id: str
    symbol: str
    direction: int
    sl_at_stop: float
    last_price: float


def _check_stop_hit(self, symbol: str, last_price: float) -> List[TrailStop]:
    stops: List[TrailStop] = []
    for state in self._states.values():
        if state.closed:
            continue
        if state.symbol != symbol:
            continue
        hit = (
            (state.direction == 1 and last_price <= state.current_sl)
            or (state.direction == -1 and last_price >= state.current_sl)
        )
        if hit:
            state.closed = True
            state.close_reason = "trail_stop"
            stops.append(TrailStop(
                signal_id=state.signal_id, symbol=symbol,
                direction=state.direction, sl_at_stop=state.current_sl,
                last_price=last_price,
            ))
    return stops


TrailManager.check_stop_hit = _check_stop_hit
