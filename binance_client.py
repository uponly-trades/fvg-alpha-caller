import asyncio
import logging
from dataclasses import dataclass
from typing import List, Optional

import aiohttp

from config import BASE_URL, KLINES_LIMIT

logger = logging.getLogger(__name__)


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

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        return self

    async def __aexit__(self, *args):
        if self.session:
            await self.session.close()

    async def fetch_klines(self, symbol: str, interval: str, limit: int = KLINES_LIMIT) -> List[Bar]:
        url = f"{BASE_URL}/fapi/v1/klines"
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        try:
            async with self.session.get(url, params=params) as resp:
                if resp.status == 429:
                    logger.warning("Rate limited on %s %s", symbol, interval)
                    await asyncio.sleep(2)
                    return []
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
            logger.error("Binance fetch error %s %s: %s", symbol, interval, e)
            return []
