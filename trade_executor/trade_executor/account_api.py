from __future__ import annotations

import base64
from typing import Any

import asyncpg

from trade_executor.config import settings
from trade_executor.crypto import decrypt, encrypt
from trade_executor.db import now_ms
from trade_executor.exchange import build_exchange


def _master_key() -> bytes:
    return base64.b64decode(settings.MASTER_ENCRYPTION_KEY)


async def _close_exchange(ex: Any) -> None:
    close = getattr(ex, "close", None)
    if close:
        await close()


async def _build_exchange_for_keys(api_key: str, api_secret: str):
    return build_exchange(api_key, api_secret, proxy_url=settings.BINANCE_PROXY_URL)


async def _validate_futures_keys(ex: Any) -> None:
    """Validate keys via futures-only endpoint — avoids spot /sapi/ calls."""
    await ex.fapiPrivateV2GetBalance()


async def _fetch_usdt_balance(ex: Any) -> dict[str, float]:
    """Fetch USDT balance from futures endpoint only."""
    items = await ex.fapiPrivateV2GetBalance()
    for item in items:
        if item.get("asset") == "USDT":
            total = float(item.get("balance", 0))
            free = float(item.get("availableBalance", 0))
            return {"free": free, "used": max(total - free, 0.0), "total": total}
    return {"free": 0.0, "used": 0.0, "total": 0.0}


async def save_user_keys(
    pool: asyncpg.Pool,
    *,
    telegram_id: int,
    api_key: str,
    api_secret: str,
) -> dict[str, str]:
    ex = await _build_exchange_for_keys(api_key, api_secret)
    try:
        await _validate_futures_keys(ex)
    finally:
        await _close_exchange(ex)

    key_blob = encrypt(api_key, _master_key())
    secret_blob = encrypt(api_secret, _master_key())
    tail = api_key[-4:]
    now = now_ms()

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO users (
                telegram_id, binance_api_key_enc, binance_api_secret_enc,
                api_key_tail, created_at, updated_at
            )
            VALUES ($1, $2, $3, $4, $5, $5)
            ON CONFLICT (telegram_id) DO UPDATE SET
                binance_api_key_enc    = EXCLUDED.binance_api_key_enc,
                binance_api_secret_enc = EXCLUDED.binance_api_secret_enc,
                api_key_tail           = EXCLUDED.api_key_tail,
                updated_at             = EXCLUDED.updated_at
            RETURNING id
            """,
            telegram_id,
            key_blob,
            secret_blob,
            tail,
            now,
        )
        await conn.execute(
            """
            INSERT INTO user_audit_log (user_id, action, payload, created_at)
            VALUES ($1, 'keys_rotated_telegram', jsonb_build_object('tail', $2::text), $3)
            """,
            row["id"],
            tail,
            now,
        )
    return {"api_key_tail": tail}


async def account_summary(pool: asyncpg.Pool, *, telegram_id: int) -> dict[str, Any]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, enabled, api_key_tail, binance_api_key_enc,
                   binance_api_secret_enc, risk_pct, leverage, max_concurrent,
                   daily_loss_cap_pct, rr_ratio, margin_mode, fixed_notional_usdt,
                   paused_until, pause_reason
            FROM users WHERE telegram_id=$1
            """,
            telegram_id,
        )
    if not row:
        return {"registered": False, "has_keys": False}

    result: dict[str, Any] = {
        "registered": True,
        "has_keys": bool(row["binance_api_key_enc"] and row["binance_api_secret_enc"]),
        "enabled": bool(row["enabled"]),
        "api_key_tail": row["api_key_tail"],
        "risk_pct": float(row["risk_pct"]),
        "leverage": int(row["leverage"]),
        "max_concurrent": int(row["max_concurrent"]),
        "daily_loss_cap_pct": float(row["daily_loss_cap_pct"]),
        "rr_ratio": float(row["rr_ratio"] or 1.0),
        "margin_mode": row["margin_mode"] or "ISOLATED",
        "fixed_notional_usdt": (
            float(row["fixed_notional_usdt"])
            if row["fixed_notional_usdt"] is not None else None
        ),
        "paused_until": row["paused_until"],
        "pause_reason": row["pause_reason"],
    }
    if not result["has_keys"]:
        return result

    api_key = decrypt(bytes(row["binance_api_key_enc"]), _master_key())
    api_secret = decrypt(bytes(row["binance_api_secret_enc"]), _master_key())
    ex = await _build_exchange_for_keys(api_key, api_secret)
    try:
        bal = await _fetch_usdt_balance(ex)
    finally:
        await _close_exchange(ex)

    result["balance"] = bal
    return result
