from __future__ import annotations

import time
from typing import Any

KEY = "signal_poller_last_seen_ms"


async def load_last_seen(conn) -> int:
    row = await conn.fetchrow("SELECT value FROM executor_state WHERE key=$1", KEY)
    return int(row["value"]) if row else 0


async def save_last_seen(conn, value: int) -> None:
    await conn.execute(
        """
        INSERT INTO executor_state (key, value, updated_at) VALUES ($1, $2, $3)
        ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=EXCLUDED.updated_at
        """,
        KEY,
        str(value),
        int(time.time() * 1000),
    )


async def poll_once(conn, *, last_seen_ms: int) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT id, symbol, tf, direction, entry, sl, tp1, tp2, event_type, created_at
        FROM kronos_decisions
        WHERE valid = true AND created_at > $1
        ORDER BY created_at ASC
        """,
        last_seen_ms,
    )
    return [dict(r) for r in rows]


async def list_enabled_users(conn) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT id, telegram_id, binance_api_key_enc, binance_api_secret_enc,
               risk_pct, leverage, margin_mode, max_concurrent, daily_loss_cap_pct, paused_until,
               rr_ratio
        FROM users
        WHERE enabled = true
          AND binance_api_key_enc IS NOT NULL
          AND binance_api_secret_enc IS NOT NULL
        """
    )
    return [dict(r) for r in rows]
