import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Awaitable, Callable, Dict, List, Optional

import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatusCode

from config import KLINES_LIMIT, SYMBOLS, TIMEFRAMES
from rest_client import Bar, fetch_klines

logger = logging.getLogger(__name__)

# Buffer cache persisted to disk so restarts skip REST warm-up
_CACHE_PATH = Path(os.environ.get("BUFFER_CACHE_PATH", "/tmp/fvg_buffer_cache.json"))
# Cache is stale if last bar is older than this (seconds) — 2 candles of smallest TF (15m)
_CACHE_MAX_AGE_SEC = 30 * 60  # legacy fallback, not used when per-TF table covers the key

# Per-TF cache TTL: ~2× the bar duration. Stale-by-1-bar cache is still useful
# because the next WS close overwrites the tail. Avoids rejecting HTF caches
# (e.g. 4h bar naturally has last_close up to 4h old between closes).
_CACHE_MAX_AGE_BY_TF = {
    "15m": 30 * 60,        # 30m
    "30m": 60 * 60,        # 1h
    "1h":  2 * 60 * 60,    # 2h
    "2h":  4 * 60 * 60,    # 4h
    "4h":  8 * 60 * 60,    # 8h
}


def _key_max_age_sec(key: str) -> int:
    # key format: "SYMBOL_TF" — split from the right since SYMBOL itself never
    # contains underscore in Binance Futures (e.g. BTCUSDT_15m → tf="15m").
    if "_" in key:
        tf = key.rsplit("_", 1)[1]
        return _CACHE_MAX_AGE_BY_TF.get(tf, _CACHE_MAX_AGE_SEC)
    return _CACHE_MAX_AGE_SEC


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

    # ------------------------------------------------------------------
    # Disk cache helpers
    # ------------------------------------------------------------------

    def _save_cache(self) -> None:
        """Persist current buffers to disk for fast restart."""
        try:
            data = {}
            for key, bars in self._buffers.items():
                data[key] = [
                    {
                        "t": b.open_time, "o": b.open, "h": b.high,
                        "l": b.low, "c": b.close, "v": b.volume,
                    }
                    for b in bars
                ]
            _CACHE_PATH.write_text(json.dumps(data))
        except Exception as e:
            logger.warning("Buffer cache save failed: %s", e)

    def _load_cache(self) -> int:
        """
        Load buffers from disk cache. Returns number of keys restored.
        Skips cache if file is missing, corrupt, or any key's last bar
        is older than _CACHE_MAX_AGE_SEC (stale data → must REST-fetch).
        """
        if not _CACHE_PATH.exists():
            return 0
        try:
            raw = json.loads(_CACHE_PATH.read_text())
            now_ms = int(time.time() * 1000)
            loaded = 0
            for key, bar_dicts in raw.items():
                if not bar_dicts:
                    continue
                last_bar_ms = bar_dicts[-1]["t"]
                age_sec = (now_ms - last_bar_ms) / 1000
                if age_sec > _key_max_age_sec(key):
                    continue  # stale beyond per-TF tolerance — will REST-fetch this key
                bars = [
                    Bar(open_time=b["t"], open=b["o"], high=b["h"],
                        low=b["l"], close=b["c"], volume=b["v"], is_closed=True)
                    for b in bar_dicts
                ]
                self._buffers[key] = bars
                self._last_closed_time[key] = bars[-1].open_time
                loaded += 1
            return loaded
        except Exception as e:
            logger.warning("Buffer cache load failed (will REST warm-up): %s", e)
            return 0

    # ------------------------------------------------------------------
    # Warm-up
    # ------------------------------------------------------------------

    def _warmup_one(self, symbol: str, tf: str) -> None:
        key = self._make_key(symbol, tf)
        bars = fetch_klines(symbol, tf, limit=KLINES_LIMIT)
        if len(bars) < 3:
            return
        self._buffers[key] = bars[-KLINES_LIMIT:]
        self._last_closed_time[key] = bars[-1].open_time
        logger.info("WS warm-up %s %s | buf=%d last_time=%s", symbol, tf, len(bars), bars[-1].open_time)

    async def _warmup_all(self) -> None:
        # Try disk cache first
        cached = self._load_cache()
        total = len(SYMBOLS) * len(TIMEFRAMES)
        if cached > 0:
            logger.info("Buffer cache restored %d/%d keys — skipping REST fetch for cached symbols", cached, total)

        # Collect keys that still need REST fetch
        missing = [
            (symbol, tf)
            for symbol in SYMBOLS
            for tf in TIMEFRAMES
            if self._make_key(symbol, tf) not in self._buffers
        ]
        if not missing:
            logger.info("WS warm-up complete | all %d keys loaded from cache", total)
            return

        # Concurrent fetch: 3 parallel — proxy SOCKS5 chokes above this
        sem = asyncio.Semaphore(3)

        async def _fetch_one(symbol: str, tf: str) -> None:
            async with sem:
                await asyncio.to_thread(self._warmup_one, symbol, tf)

        logger.info("WS warm-up starting | cached=%d rest_needed=%d concurrent=3", cached, len(missing))
        await asyncio.gather(*[_fetch_one(s, t) for s, t in missing])
        logger.info("WS warm-up complete | cached=%d rest_fetched=%d total=%d", cached, len(missing), total)
        self._save_cache()

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

            # Persist cache after every bar close so restarts are instant
            self._save_cache()
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
        # Start WS connections immediately — don't block on warm-up.
        # Bars arriving before buffer is ready trigger on-demand warmup_one() per key.
        self._fallback_task = asyncio.create_task(self._fallback_loop())
        self._connection_tasks = [asyncio.create_task(self._run_connection(i, chunk)) for i, chunk in enumerate(chunks)]
        # Warm-up runs concurrently so signals flow as soon as each symbol's buffer is ready
        warmup_task = asyncio.create_task(self._warmup_all())
        await asyncio.gather(*self._connection_tasks, warmup_task)

    def stop(self) -> None:
        self._running = False
        for task in self._connection_tasks:
            task.cancel()
        if self._fallback_task:
            self._fallback_task.cancel()
