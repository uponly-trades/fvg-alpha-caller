"""
Async HTTP client for Kronos prediction service.
Returns None on any network/service error — caller uses fallback.
"""
from __future__ import annotations
import logging
import os
from typing import Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

KRONOS_URL = os.environ.get("KRONOS_URL", "http://192.168.70.192:8012")
_TIMEOUT = httpx.Timeout(10.0, connect=3.0)


async def predict(
    bars: List[Dict],
    current_price: float,
    atr: float,
    zone_direction: int,
    symbol: str = "",
    tf: str = "",
) -> Optional[Dict]:
    """
    Call Kronos service. Returns decision dict or None if unreachable/error.
    Decision dict keys: direction, timeframe, entry, sl, tp1, tp2, confidence.
    """
    payload = {
        "bars": bars[-512:],   # max context
        "current_price": current_price,
        "atr": float(atr),
        "zone_direction": int(zone_direction),
        "symbol": symbol,
        "tf": tf,
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(f"{KRONOS_URL}/predict", json=payload)
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.warning("Kronos service unavailable (%s %s): %s — using fallback", symbol, tf, e)
        return None
