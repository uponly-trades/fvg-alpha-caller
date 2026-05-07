"""
Async HTTP client for Kronos prediction service.
Returns None on any network/service error — caller uses fallback.

Concurrency: Kronos service is single-GPU (MPS) so it can only run one
prediction at a time. Bot bursts on bar-close, so we cap concurrent calls
with a semaphore and retry once with backoff before giving up.
"""
from __future__ import annotations
import asyncio
import logging
import os
from typing import Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

KRONOS_URL = os.environ.get("KRONOS_URL", "http://192.168.70.192:8012")

# Generous timeouts: GPU prediction ~1s sequential but queues under burst.
# connect 15s tolerates TCP+TLS during saturation, total 45s tolerates queue depth.
_TIMEOUT = httpx.Timeout(45.0, connect=15.0)

# Cap concurrent /predict calls — service serializes on GPU anyway,
# so >2 in-flight just queue at the server and waste timeout budget.
_MAX_CONCURRENT = int(os.environ.get("KRONOS_MAX_CONCURRENT", "2"))
_SEMAPHORE: Optional[asyncio.Semaphore] = None

_RETRIES = 1  # one retry on transient timeout/5xx
_BACKOFF_S = 1.5


def _get_sem() -> asyncio.Semaphore:
    global _SEMAPHORE
    if _SEMAPHORE is None:
        _SEMAPHORE = asyncio.Semaphore(_MAX_CONCURRENT)
    return _SEMAPHORE


async def _post_once(payload: dict) -> Optional[Dict]:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(f"{KRONOS_URL}/predict", json=payload)
        resp.raise_for_status()
        return resp.json()


async def predict(
    bars: List[Dict],
    current_price: float,
    atr: float,
    zone_direction: int,
    symbol: str = "",
    tf: str = "",
    htf_bars: Optional[List[Dict]] = None,
) -> Optional[Dict]:
    """
    Call Kronos service. Returns decision dict or None if unreachable/error.
    Decision dict keys: direction, timeframe, entry, sl, tp1, tp2, confidence.
    htf_bars: optional 4h OHLCV bars for HTF RSI7 gate inside Kronos.
    """
    payload = {
        "bars": bars[-512:],
        "current_price": current_price,
        "atr": float(atr),
        "zone_direction": int(zone_direction),
        "symbol": symbol,
        "tf": tf,
    }
    if htf_bars:
        payload["htf_bars"] = htf_bars[-50:]  # 50 × 4h = 200h context, enough for RSI7

    sem = _get_sem()
    last_err: Optional[Exception] = None
    async with sem:
        for attempt in range(_RETRIES + 1):
            try:
                return await _post_once(payload)
            except (httpx.TimeoutException, httpx.ConnectError) as e:
                last_err = e
                if attempt < _RETRIES:
                    await asyncio.sleep(_BACKOFF_S)
                    continue
            except httpx.HTTPStatusError as e:
                # Retry only on 5xx; 4xx means bad payload, no point retrying
                last_err = e
                if 500 <= e.response.status_code < 600 and attempt < _RETRIES:
                    await asyncio.sleep(_BACKOFF_S)
                    continue
                break
            except Exception as e:
                last_err = e
                break

    err_name = type(last_err).__name__ if last_err else "Unknown"
    err_msg = str(last_err) if last_err else ""
    logger.warning(
        "Kronos service unavailable (%s %s): %s: %s — using fallback",
        symbol, tf, err_name, err_msg,
    )
    return None
