import asyncio
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

import requests

from config import BASE_URL, KLINES_LIMIT, SYMBOLS, TIMEFRAMES

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Bar:
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_closed: bool


def fetch_klines(symbol: str, tf: str, limit: int = KLINES_LIMIT) -> List[Bar]:
    """Fetch closed klines from Binance Futures REST API."""
    url = f"{BASE_URL}/fapi/v1/klines"
    params = {"symbol": symbol, "interval": tf, "limit": limit}
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        raw = resp.json()
    except Exception as e:
        logger.error("Fetch klines failed %s %s: %s", symbol, tf, e)
        return []

    bars = []
    for k in raw:
        bars.append(Bar(
            open_time=int(k[0]),
            open=float(k[1]),
            high=float(k[2]),
            low=float(k[3]),
            close=float(k[4]),
            volume=float(k[5]),
            is_closed=True,  # REST klines are all closed (last one is forming but we ignore it)
        ))
    # Drop the last bar — it's the currently forming (not closed) candle
    if bars:
        bars = bars[:-1]
    return bars


class KlinePoller:
    """
    Poll Binance Futures REST API for closed klines.
    Calls on_bar_close(symbol, tf, bars) when a new bar closes.
    """

    def __init__(self, on_bar_close, poll_interval: int = 30):
        self.on_bar_close = on_bar_close
        self.poll_interval = poll_interval
        self._last_close_time: Dict[str, int] = {}
        self._running = False

    def _make_key(self, symbol: str, tf: str) -> str:
        return f"{symbol}_{tf}"

    async def _poll_once(self, symbol: str, tf: str):
        key = self._make_key(symbol, tf)
        bars = fetch_klines(symbol, tf)
        if len(bars) < 3:
            return

        last_bar = bars[-1]
        last_time = self._last_close_time.get(key, -1)

        if last_bar.open_time <= last_time:
            return  # no new bar

        # Warm-up: first poll just records timestamp, skip detection
        if key not in self._last_close_time:
            self._last_close_time[key] = last_bar.open_time
            logger.info("Warm-up %s %s | buf=%d last_time=%s", symbol, tf, len(bars), last_bar.open_time)
            return

        self._last_close_time[key] = last_bar.open_time
        logger.info("Bar closed %s %s @ %s | buf=%d", symbol, tf, last_bar.open_time, len(bars))

        await self.on_bar_close(symbol, tf, bars)

    async def run(self):
        self._running = True
        streams = len(SYMBOLS) * len(TIMEFRAMES)
        # Stagger delay to stay under Binance rate limit (2400 req/5min = 8 req/s)
        # With 60s interval: 300 req / 60s = 5 req/s — safe
        # But burst 300 simultaneously could trigger limit, so stagger 0.15s
        stagger = 0.15
        logger.info(
            "KlinePoller starting | symbols=%d tfs=%d streams=%d interval=%ds stagger=%.2fs",
            len(SYMBOLS), len(TIMEFRAMES), streams, self.poll_interval, stagger,
        )

        while self._running:
            errors = 0
            for symbol in SYMBOLS:
                for tf in TIMEFRAMES:
                    try:
                        await self._poll_once(symbol, tf)
                    except Exception as e:
                        errors += 1
                        logger.warning("Poll error %s %s: %s", symbol, tf, e)
                    await asyncio.sleep(stagger)

            if errors:
                logger.warning("Poll errors: %d / %d", errors, streams)

            await asyncio.sleep(self.poll_interval)

    def stop(self):
        self._running = False
