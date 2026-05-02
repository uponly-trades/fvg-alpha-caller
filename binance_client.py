import asyncio
import logging
from dataclasses import dataclass
from typing import List, Optional

import aiohttp

from config import BASE_URL, KLINES_LIMIT

logger = logging.getLogger(__name__)

# Rate limiting
MAX_CONCURRENT = 8
REQUEST_DELAY_SEC = 0.15


@dataclass(frozen=True, slots=True)
class Bar:
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class BinanceClient:
    def __init__(self) -> None:
        self.session: Optional[aiohttp.ClientSession] = None
        self.semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    async def __aenter__(self):
        connector = aiohttp.TCPConnector(
            limit=20,
            limit_per_host=10,
            ttl_dns_cache=300,
        )
        self.session = aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=20, connect=10),
        )
        return self

    async def __aexit__(self, *args):
        if self.session:
            await self.session.close()

    async def fetch_klines(self, symbol: str, interval: str, limit: int = KLINES_LIMIT) -> List[Bar]:
        async with self.semaphore:
            url = f"{BASE_URL}/fapi/v1/klines"
            params = {"symbol": symbol, "interval": interval, "limit": limit}

            for attempt in range(3):
                try:
                    async with self.session.get(url, params=params) as resp:
                        if resp.status == 429:
                            logger.warning("Rate limited on %s %s, retry in 2s", symbol, interval)
                            await asyncio.sleep(2)
                            continue
                        resp.raise_for_status()
                        data = await resp.json()
                        bars = []
                        for row in data:
                            bars.append(Bar(
                                open_time=int(row[0]),
                                open=float(row[1]),
                                high=float(row[2]),
                                low=float(row[3]),
                                close=float(row[4]),
                                volume=float(row[5]),
                            ))
                        return bars
                except Exception as e:
                    if attempt < 2:
                        await asyncio.sleep(0.5 * (attempt + 1))
                        continue
                    logger.error("Binance fetch error %s %s: %s", symbol, interval, e)
                    return []

            await asyncio.sleep(REQUEST_DELAY_SEC)
            return []
