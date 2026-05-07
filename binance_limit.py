"""Shared Binance fapi rate-limit defense.

Tracks `X-MBX-USED-WEIGHT-1m` to throttle preemptively, and writes a Postgres
circuit-breaker flag (`binance_rest_state.banned_until_ms`) so every callsite —
alpha-caller and trade_executor both — short-circuits while the IP is banned.

Concurrency model: in-process counters live in module globals; the durable
ban flag is in Postgres. We poll Postgres every BAN_CHECK_INTERVAL_SEC at most,
since hot paths call `await_capacity_*` per request.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Binance fapi cap is 2400 weight/min/IP. Throttle at 75% (1800).
WEIGHT_CAP = int(os.environ.get("BINANCE_WEIGHT_CAP", "2400"))
WEIGHT_THRESHOLD = int(os.environ.get("BINANCE_WEIGHT_THRESHOLD", str(int(WEIGHT_CAP * 0.75))))
WEIGHT_WINDOW_SEC = 60.0
BAN_CHECK_INTERVAL_SEC = float(os.environ.get("BINANCE_BAN_CHECK_INTERVAL_SEC", "3.0"))
DEFAULT_DB_URL_ENV = "DATABASE_URL"


class BinanceBannedError(RuntimeError):
    """Raised when caller passes raise_when_banned=True and IP is banned."""

    def __init__(self, ms_remaining: int):
        super().__init__(f"Binance REST circuit-open for {ms_remaining/1000:.0f}s")
        self.ms_remaining = ms_remaining


# ---- In-process state ---------------------------------------------------

_lock = threading.Lock()
_state = {
    "used_weight": 0,
    "last_seen_ts": 0.0,
    "banned_until_ms_cache": 0,
    "banned_cache_ts": 0.0,
}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _decay_weight(now: float) -> int:
    """Used-weight resets every minute on Binance side. If our last seen
    sample is older than the rolling window, treat counter as 0."""
    if now - _state["last_seen_ts"] > WEIGHT_WINDOW_SEC:
        return 0
    return int(_state["used_weight"])


# ---- Response recording -------------------------------------------------

def record_response(resp) -> None:
    """Parse `X-MBX-USED-WEIGHT-1m` from a `requests.Response`."""
    try:
        headers = resp.headers
    except AttributeError:
        return
    record_headers(headers)
    if getattr(resp, "status_code", None) == 418:
        retry_after = headers.get("Retry-After") or headers.get("retry-after")
        try:
            seconds = int(float(retry_after)) if retry_after else 120
        except ValueError:
            seconds = 120
        mark_banned(seconds + 1)


def record_headers(headers) -> None:
    """Generic header recorder — works with both dict and CIMultiDict
    (ccxt async stores headers as plain dict in `last_response_headers`)."""
    if not headers:
        return
    used = None
    for key in ("X-MBX-USED-WEIGHT-1m", "x-mbx-used-weight-1m", "X-Mbx-Used-Weight-1m"):
        if key in headers:
            used = headers[key]
            break
    if used is None:
        # generic case-insensitive scan
        for k, v in headers.items():
            if k.lower() == "x-mbx-used-weight-1m":
                used = v
                break
    if used is None:
        return
    try:
        used_int = int(used)
    except (TypeError, ValueError):
        return
    with _lock:
        _state["used_weight"] = used_int
        _state["last_seen_ts"] = time.time()


# ---- Capacity gating ----------------------------------------------------

def _required_sleep(weight_needed: int, now: float) -> float:
    """Return seconds to sleep so weight + weight_needed fits under threshold.
    Returns 0 if already under."""
    used = _decay_weight(now)
    if used + weight_needed <= WEIGHT_THRESHOLD:
        return 0.0
    elapsed = now - _state["last_seen_ts"]
    return max(WEIGHT_WINDOW_SEC - elapsed, 0.5)


def await_capacity_sync(weight_needed: int = 5, raise_when_banned: bool = False) -> None:
    """Block (time.sleep) until capacity is available. If banned, sleep the
    remaining ban duration unless raise_when_banned=True."""
    ban_ms = is_banned()
    if ban_ms > 0:
        if raise_when_banned:
            raise BinanceBannedError(ban_ms)
        sleep_s = ban_ms / 1000.0
        logger.warning("binance_limit: ban active, sleeping %.0fs", sleep_s)
        time.sleep(sleep_s)
    now = time.time()
    sleep_s = _required_sleep(weight_needed, now)
    if sleep_s > 0:
        logger.info(
            "binance_limit: weight=%d/%d threshold reached — sleeping %.1fs",
            _decay_weight(now), WEIGHT_CAP, sleep_s,
        )
        time.sleep(sleep_s)


async def await_capacity_async(weight_needed: int = 5, raise_when_banned: bool = False) -> None:
    """Async variant — uses asyncio.sleep."""
    import asyncio
    ban_ms = await is_banned_async()
    if ban_ms > 0:
        if raise_when_banned:
            raise BinanceBannedError(ban_ms)
        sleep_s = ban_ms / 1000.0
        logger.warning("binance_limit: ban active, sleeping %.0fs", sleep_s)
        await asyncio.sleep(sleep_s)
    now = time.time()
    sleep_s = _required_sleep(weight_needed, now)
    if sleep_s > 0:
        logger.info(
            "binance_limit: weight=%d/%d threshold reached — sleeping %.1fs",
            _decay_weight(now), WEIGHT_CAP, sleep_s,
        )
        await asyncio.sleep(sleep_s)


# ---- Postgres circuit breaker ------------------------------------------

_DB_URL = os.environ.get(DEFAULT_DB_URL_ENV)


def _ensure_table_sync() -> None:
    if not _DB_URL:
        return
    import psycopg2
    try:
        conn = psycopg2.connect(_DB_URL)
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS binance_rest_state (
                            key TEXT PRIMARY KEY,
                            banned_until_ms BIGINT NOT NULL DEFAULT 0,
                            used_weight_1m INT NOT NULL DEFAULT 0,
                            last_response_ts BIGINT NOT NULL DEFAULT 0
                        );
                        INSERT INTO binance_rest_state (key) VALUES ('global')
                        ON CONFLICT (key) DO NOTHING;
                        """
                    )
        finally:
            conn.close()
    except Exception as e:
        logger.warning("binance_limit: ensure_table failed: %s", e)


_TABLE_READY = False


def _ensure_ready() -> None:
    global _TABLE_READY
    if _TABLE_READY:
        return
    _ensure_table_sync()
    _TABLE_READY = True


def mark_banned(retry_after_s: int) -> None:
    """Persist the ban deadline so every service short-circuits."""
    if not _DB_URL:
        with _lock:
            _state["banned_until_ms_cache"] = _now_ms() + retry_after_s * 1000
            _state["banned_cache_ts"] = time.time()
        return
    until_ms = _now_ms() + retry_after_s * 1000
    import psycopg2
    try:
        _ensure_ready()
        conn = psycopg2.connect(_DB_URL)
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE binance_rest_state SET banned_until_ms=%s, last_response_ts=%s "
                        "WHERE key='global'",
                        (until_ms, _now_ms()),
                    )
        finally:
            conn.close()
        with _lock:
            _state["banned_until_ms_cache"] = until_ms
            _state["banned_cache_ts"] = time.time()
        logger.error("binance_limit: marked banned for %ds", retry_after_s)
    except Exception as e:
        logger.warning("binance_limit: mark_banned failed: %s", e)


def is_banned() -> int:
    """Returns ms remaining on ban, or 0 if not banned. Caches DB lookup."""
    now = time.time()
    cached_until = _state["banned_until_ms_cache"]
    cached_ts = _state["banned_cache_ts"]
    if now - cached_ts < BAN_CHECK_INTERVAL_SEC:
        remaining = cached_until - _now_ms()
        return max(remaining, 0)
    if not _DB_URL:
        remaining = cached_until - _now_ms()
        return max(remaining, 0)
    import psycopg2
    try:
        _ensure_ready()
        conn = psycopg2.connect(_DB_URL)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT banned_until_ms FROM binance_rest_state WHERE key='global'"
                )
                row = cur.fetchone()
        finally:
            conn.close()
        until_ms = int(row[0]) if row else 0
    except Exception as e:
        logger.warning("binance_limit: is_banned read failed: %s", e)
        until_ms = cached_until
    with _lock:
        _state["banned_until_ms_cache"] = until_ms
        _state["banned_cache_ts"] = now
    remaining = until_ms - _now_ms()
    return max(remaining, 0)


async def is_banned_async() -> int:
    """Async variant for asyncpg-based callers (trade_executor)."""
    now = time.time()
    cached_until = _state["banned_until_ms_cache"]
    cached_ts = _state["banned_cache_ts"]
    if now - cached_ts < BAN_CHECK_INTERVAL_SEC:
        remaining = cached_until - _now_ms()
        return max(remaining, 0)
    if not _DB_URL:
        remaining = cached_until - _now_ms()
        return max(remaining, 0)
    try:
        import asyncpg
        conn = await asyncpg.connect(_DB_URL)
        try:
            row = await conn.fetchrow(
                "SELECT banned_until_ms FROM binance_rest_state WHERE key='global'"
            )
        finally:
            await conn.close()
        until_ms = int(row["banned_until_ms"]) if row else 0
    except Exception as e:
        logger.warning("binance_limit: is_banned_async read failed: %s", e)
        until_ms = cached_until
    with _lock:
        _state["banned_until_ms_cache"] = until_ms
        _state["banned_cache_ts"] = now
    remaining = until_ms - _now_ms()
    return max(remaining, 0)


async def mark_banned_async(retry_after_s: int) -> None:
    """Async variant — for ccxt 418/429 paths in trade_executor."""
    until_ms = _now_ms() + retry_after_s * 1000
    if _DB_URL:
        try:
            import asyncpg
            conn = await asyncpg.connect(_DB_URL)
            try:
                await conn.execute(
                    "UPDATE binance_rest_state SET banned_until_ms=$1, last_response_ts=$2 "
                    "WHERE key='global'",
                    until_ms, _now_ms(),
                )
            finally:
                await conn.close()
        except Exception as e:
            logger.warning("binance_limit: mark_banned_async failed: %s", e)
    with _lock:
        _state["banned_until_ms_cache"] = until_ms
        _state["banned_cache_ts"] = time.time()
    logger.error("binance_limit: marked banned for %ds (async)", retry_after_s)


# ---- Test hooks --------------------------------------------------------

def _reset_for_tests() -> None:
    with _lock:
        _state["used_weight"] = 0
        _state["last_seen_ts"] = 0.0
        _state["banned_until_ms_cache"] = 0
        _state["banned_cache_ts"] = 0.0
