import time
from typing import Dict, Tuple


class CooldownStore:
    """Per-(symbol, direction) signal throttle for v2 to mitigate alert spam."""

    def __init__(self, window_sec: int):
        self.window_sec = window_sec
        self._last_emit: Dict[Tuple[str, str], float] = {}

    def allow(self, symbol: str, direction: str) -> bool:
        """Return True if signal allowed (and record emission)."""
        key = (symbol, direction)
        now = time.time()
        last = self._last_emit.get(key)
        if last is not None and (now - last) < self.window_sec:
            return False
        self._last_emit[key] = now
        return True
