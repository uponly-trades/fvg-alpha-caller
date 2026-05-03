import asyncio
import json
import logging
import time
from typing import Awaitable, Callable, Dict, List, Optional

import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatusCode

from config import KLINES_LIMIT, SYMBOLS, TIMEFRAMES
from rest_client import Bar, fetch_klines

logger = logging.getLogger(__name__)


class BinanceKlineWS:
    """
    Binance Futures kline WebSocket client.

    REST is used for initial warm-up and stale fallback. WebSocket closed candle
    events then update the same buffers consumed by the FVG engine.
    """

    BASE_URL = "wss://fstream.binancefuture.com/stream?streams="
    MAX_STREAMS_PER_CONN = 100
    STALE_AFTER_SEC = 120
    FALLBACK_INTERVAL_SEC = 30
    RECONNECT_DELAY_INITIAL = 1.0
    RECONNECT_DELAY_MAX = 60.0

    def __init__(self, on_bar_close: Callable[[str, str, List[Bar]], Awaitable[None]]):
        self.on_bar_close = on_bar_close
        self._running = False
        self._buffers: Dict[str, List[Bar]] = {}
        self._last_closed_time: Dict[str, int] = {}
        self._last_message_at: Dict[int, float] = {}
        self._expected_connections = 0
        self._fallback_task: Optional[asyncio.Task] = None
        self._connection_tasks: List[asyncio.Task] = []

    def _make_key(self, symbol: str, tf: str) -> str:
        return f"{symbol}_{tf}"

    def _stream_name(self, symbol: str, tf: str) -> str:
        return f"{symbol.lower()}@kline_{tf}"

    def _stream_chunks(self) -> List[List[str]]:
        streams = [self._stream_name(symbol, tf) for symbol in SYMBOLS for tf in TIMEFRAMES]
        return [streams[i:i + self.MAX_STREAMS_PER_CONN] for i in range(0, len(streams), self.MAX_STREAMS_PER_CONN)]

    def _build_url(self, streams: List[str]) -> str:
        return self.BASE_URL + "/".join(streams)

    def _warmup_one(self, symbol: str, tf: str) -> None:
        key = self._make_key(symbol, tf)
        bars = fetch_klines(symbol, tf, limit=KLINES_LIMIT)
        if len(bars) < 3:
            return
        self._buffers[key] = bars[-KLINES_LIMIT:]
        self._last_closed_time[key] = bars[-1].open_time
        logger.info("WS warm-up %s %s | buf=%d last_time=%s", symbol, tf, len(bars), bars[-1].open_time)

    async def _warmup_all(self) -> None:
        logger.info("WS warm-up starting | symbols=%d tfs=%d", len(SYMBOLS), len(TIMEFRAMES))
        for symbol in SYMBOLS:
            for tf in TIMEFRAMES:
                await asyncio.to_thread(self._warmup_one, symbol, tf)
                await asyncio.sleep(0.05)
        logger.info("WS warm-up complete | buffers=%d", len(self._buffers))

    def _bar_from_kline(self, kline: dict) -> Bar:
        return Bar(
            open_time=int(kline["t"]),
            open=float(kline["o"]),
            high=float(kline["h"]),
            low=float(kline["l"]),
            close=float(kline["c"]),
            volume=float(kline["v"]),
            is_closed=bool(kline["x"]),
        )

    async def _handle_message(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
            data = msg.get("data", {})
            kline = data.get("k")
            if not kline or not kline.get("x"):
                return

            symbol = str(kline["s"]).upper()
            tf = str(kline["i"])
            bar = self._bar_from_kline(kline)
            key = self._make_key(symbol, tf)
            last_time = self._last_closed_time.get(key, -1)
            if bar.open_time <= last_time:
                return

            buf = self._buffers.get(key)
            if not buf:
                await asyncio.to_thread(self._warmup_one, symbol, tf)
                buf = self._buffers.get(key, [])

            if buf and buf[-1].open_time == bar.open_time:
                buf[-1] = bar
            else:
                buf.append(bar)
            self._buffers[key] = buf[-KLINES_LIMIT:]
            self._last_closed_time[key] = bar.open_time

            logger.info("WS bar closed %s %s @ %s | buf=%d", symbol, tf, bar.open_time, len(self._buffers[key]))
            await self.on_bar_close(symbol, tf, list(self._buffers[key]))
        except Exception as e:
            logger.warning("WS message handling failed: %s", e)

    async def _run_connection(self, index: int, streams: List[str]) -> None:
        delay = self.RECONNECT_DELAY_INITIAL
        url = self._build_url(streams)
        while self._running:
            try:
                logger.info("WS connecting | conn=%d streams=%d", index, len(streams))
                async with websockets.connect(url, ping_interval=20, ping_timeout=10, close_timeout=10) as ws:
                    logger.info("WS connected | conn=%d streams=%d", index, len(streams))
                    delay = self.RECONNECT_DELAY_INITIAL
                    self._last_message_at[index] = time.time()
                    async for message in ws:
                        if not self._running:
                            break
                        self._last_message_at[index] = time.time()
                        await self._handle_message(message)
            except (ConnectionClosed, InvalidStatusCode) as e:
                logger.warning("WS connection closed | conn=%d err=%s", index, e)
            except Exception as e:
                logger.warning("WS connection error | conn=%d err=%s", index, e)

            if self._running:
                logger.info("WS reconnecting | conn=%d delay=%.1fs", index, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 1.5, self.RECONNECT_DELAY_MAX)

    async def _fallback_loop(self) -> None:
        while self._running:
            await asyncio.sleep(self.FALLBACK_INTERVAL_SEC)
            now = time.time()
            live_connections = len(self._last_message_at) == self._expected_connections
            all_fresh = live_connections and all(now - ts < self.STALE_AFTER_SEC for ts in self._last_message_at.values())
            if all_fresh:
                continue

            logger.warning("WS stale/disconnected; running REST fallback poll")
            for symbol in SYMBOLS:
                for tf in TIMEFRAMES:
                    key = self._make_key(symbol, tf)
                    bars = await asyncio.to_thread(fetch_klines, symbol, tf, KLINES_LIMIT)
                    if len(bars) < 3:
                        await asyncio.sleep(0.05)
                        continue
                    last_bar = bars[-1]
                    last_time = self._last_closed_time.get(key, -1)
                    if last_bar.open_time > last_time:
                        self._buffers[key] = bars[-KLINES_LIMIT:]
                        self._last_closed_time[key] = last_bar.open_time
                        logger.info("REST fallback bar closed %s %s @ %s | buf=%d", symbol, tf, last_bar.open_time, len(bars))
                        await self.on_bar_close(symbol, tf, bars)
                    await asyncio.sleep(0.05)

    async def run(self) -> None:
        self._running = True
        chunks = self._stream_chunks()
        self._expected_connections = len(chunks)
        logger.info(
            "BinanceKlineWS starting | symbols=%d tfs=%d streams=%d conns=%d fallback=%ds stale=%ds",
            len(SYMBOLS), len(TIMEFRAMES), len(SYMBOLS) * len(TIMEFRAMES), len(chunks),
            self.FALLBACK_INTERVAL_SEC, self.STALE_AFTER_SEC,
        )
        await self._warmup_all()
        self._fallback_task = asyncio.create_task(self._fallback_loop())
        self._connection_tasks = [asyncio.create_task(self._run_connection(i, chunk)) for i, chunk in enumerate(chunks)]
        await asyncio.gather(*self._connection_tasks)

    def stop(self) -> None:
        self._running = False
        for task in self._connection_tasks:
            task.cancel()
        if self._fallback_task:
            self._fallback_task.cancel()
