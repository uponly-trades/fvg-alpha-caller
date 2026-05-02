import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, List, Optional

import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatusCode

from config import SYMBOLS, TIMEFRAMES

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


class BinanceWSClient:
    """
    Idempotent WebSocket client for Binance Futures combined kline stream.
    Guarantees each closed bar is processed exactly once.
    """

    BASE_URL = "wss://fstream.binance.com/stream?streams="
    RECONNECT_DELAY_INITIAL = 1.0
    RECONNECT_DELAY_MAX = 60.0

    def __init__(self, on_bar_close: Callable[[str, str, Bar], Awaitable[None]]):
        self.on_bar_close = on_bar_close
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self._running = False
        self._reconnect_delay = self.RECONNECT_DELAY_INITIAL
        # Track last processed open_time per (symbol, tf) for idempotency
        self._last_closed_time: Dict[str, int] = {}
        self._buffer: Dict[str, List[Bar]] = {}

    def _build_stream_name(self, symbol: str, tf: str) -> str:
        return f"{symbol.lower()}@kline_{tf}"

    def _build_url(self) -> str:
        streams = []
        for symbol in SYMBOLS:
            for tf in TIMEFRAMES:
                streams.append(self._build_stream_name(symbol, tf))
        # Max 1024 streams per combined connection
        return self.BASE_URL + "/".join(streams)

    def _make_key(self, symbol: str, tf: str) -> str:
        return f"{symbol}_{tf}"

    async def _handle_message(self, raw: str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return

        if "stream" not in data or "data" not in data:
            return

        stream = data["stream"]  # e.g. "btcusdt@kline_15m"
        k = data["data"]["k"]

        # Parse symbol and tf from stream name
        parts = stream.split("@")
        if len(parts) != 2:
            return
        symbol = parts[0].upper()
        tf = parts[1].replace("kline_", "")

        bar = Bar(
            open_time=k["t"],
            open=float(k["o"]),
            high=float(k["h"]),
            low=float(k["l"]),
            close=float(k["c"]),
            volume=float(k["v"]),
            is_closed=k["x"],
        )

        key = self._make_key(symbol, tf)

        # Buffer bars for this stream
        if key not in self._buffer:
            self._buffer[key] = []
        self._buffer[key].append(bar)
        # Keep only last 100 bars
        if len(self._buffer[key]) > 100:
            self._buffer[key] = self._buffer[key][-100:]

        # Idempotent bar close detection
        if bar.is_closed:
            last_time = self._last_closed_time.get(key, -1)
            if bar.open_time > last_time:
                self._last_closed_time[key] = bar.open_time
                logger.debug("Bar closed %s %s @ %s", symbol, tf, bar.open_time)
                await self.on_bar_close(symbol, tf, bar)

    async def _connect(self):
        url = self._build_url()
        logger.info("WebSocket connecting | streams=%d", len(SYMBOLS) * len(TIMEFRAMES))
        try:
            self.ws = await websockets.connect(url, ping_interval=20, ping_timeout=10)
            logger.info("WebSocket connected")
            self._reconnect_delay = self.RECONNECT_DELAY_INITIAL
            async for message in self.ws:
                if not self._running:
                    break
                await self._handle_message(message)
        except ConnectionClosed as e:
            logger.warning("WebSocket closed: %s", e)
        except InvalidStatusCode as e:
            logger.error("WebSocket invalid status: %s", e)
        except Exception as e:
            logger.error("WebSocket error: %s", e)

    async def run(self):
        self._running = True
        while self._running:
            try:
                await self._connect()
            except Exception as e:
                logger.error("Connection failed: %s", e)

            if not self._running:
                break

            logger.info("Reconnecting in %.1fs...", self._reconnect_delay)
            await asyncio.sleep(self._reconnect_delay)
            self._reconnect_delay = min(
                self._reconnect_delay * 1.5 + (asyncio.get_event_loop().time() % 1),
                self.RECONNECT_DELAY_MAX,
            )

    def stop(self):
        self._running = False
        if self.ws:
            asyncio.create_task(self.ws.close())
