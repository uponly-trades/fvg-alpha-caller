from __future__ import annotations

import time
from typing import Any

OPEN_STATUSES = ("opening", "open", "tp1_trailed")
CLOSED_STATUSES = ("closed_tp2", "closed_sl", "closed_breakeven", "manual_close")


def now_ms() -> int:
    return int(time.time() * 1000)


async def upsert_user(conn, *, telegram_id: int, username: str | None, first_name: str | None) -> None:
    await conn.execute(
        """
        INSERT INTO users (telegram_id, telegram_username, first_name, created_at, updated_at)
        VALUES ($1, $2, $3, $4, $4)
        ON CONFLICT (telegram_id) DO UPDATE SET
          telegram_username=EXCLUDED.telegram_username,
          first_name=EXCLUDED.first_name,
          updated_at=EXCLUDED.updated_at
        """,
        telegram_id,
        username,
        first_name,
        now_ms(),
    )


async def user_row(conn, *, telegram_id: int):
    return await conn.fetchrow(
        """
        SELECT id, enabled, paused_until, pause_reason, api_key_tail, risk_pct,
               leverage, max_concurrent, daily_loss_cap_pct
        FROM users WHERE telegram_id=$1
        """,
        telegram_id,
    )


async def set_enabled(conn, *, telegram_id: int, enabled: bool) -> None:
    await conn.execute(
        """
        UPDATE users SET enabled=$1, paused_until=NULL, pause_reason=NULL, updated_at=$2
        WHERE telegram_id=$3
        """,
        enabled,
        now_ms(),
        telegram_id,
    )


async def update_setting(conn, *, telegram_id: int, field: str, value: float | int) -> None:
    allowed = {"risk_pct", "leverage", "max_concurrent", "daily_loss_cap_pct"}
    if field not in allowed:
        raise ValueError("invalid setting")
    await conn.execute(
        f"UPDATE users SET {field}=$1, updated_at=$2 WHERE telegram_id=$3",
        value,
        now_ms(),
        telegram_id,
    )


async def list_trades(conn, *, telegram_id: int, closed: bool) -> list[dict[str, Any]]:
    statuses = CLOSED_STATUSES if closed else OPEN_STATUSES
    rows = await conn.fetch(
        """
        SELECT t.id, t.symbol, t.tf, t.direction, t.entry, t.sl_current, t.tp1, t.tp2,
               t.qty, t.leverage, t.margin_usdt, t.pnl_usdt, t.pnl_pct, t.status,
               t.opened_at, t.closed_at
        FROM user_trades t
        JOIN users u ON u.id=t.user_id
        WHERE u.telegram_id=$1 AND t.status = ANY($2::text[])
        ORDER BY COALESCE(t.closed_at, t.opened_at) DESC
        LIMIT 10
        """,
        telegram_id,
        list(statuses),
    )
    return [dict(r) for r in rows]


async def stats(conn, *, telegram_id: int) -> dict[str, Any]:
    row = await conn.fetchrow(
        """
        SELECT u.id,
               COALESCE(d.realized_pnl_usdt, 0) AS today_pnl_usdt,
               COALESCE(d.realized_pnl_pct, 0) AS today_pnl_pct,
               COALESCE(d.trades_count, 0) AS today_trades,
               COALESCE(d.wins_count, 0) AS today_wins
        FROM users u
        LEFT JOIN user_daily_pnl d ON d.user_id=u.id AND d.day=CURRENT_DATE
        WHERE u.telegram_id=$1
        """,
        telegram_id,
    )
    if not row:
        return {"registered": False}
    all_time = await conn.fetchrow(
        """
        SELECT COUNT(*)::int AS closed_trades,
               COUNT(*) FILTER (WHERE COALESCE(pnl_usdt, 0) > 0)::int AS wins,
               COALESCE(SUM(pnl_usdt), 0)::float AS pnl_usdt
        FROM user_trades
        WHERE user_id=$1 AND status IN ('closed_tp2','closed_sl','closed_breakeven','manual_close')
        """,
        row["id"],
    )
    closed = int(all_time["closed_trades"] or 0)
    wins = int(all_time["wins"] or 0)
    return {
        "registered": True,
        "today_pnl_usdt": float(row["today_pnl_usdt"] or 0),
        "today_pnl_pct": float(row["today_pnl_pct"] or 0),
        "today_trades": int(row["today_trades"] or 0),
        "today_wins": int(row["today_wins"] or 0),
        "closed_trades": closed,
        "wins": wins,
        "winrate": (wins / closed * 100) if closed else 0.0,
        "pnl_usdt": float(all_time["pnl_usdt"] or 0),
    }
