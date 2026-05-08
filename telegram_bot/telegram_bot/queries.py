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
        SELECT u.id, u.enabled, u.risk_pct, u.leverage, u.max_concurrent,
               u.daily_loss_cap_pct, u.api_key_tail,
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
        SELECT
            COUNT(*)::int                                                    AS closed_trades,
            COUNT(*) FILTER (WHERE COALESCE(pnl_usdt, 0) > 0)::int         AS wins,
            COUNT(*) FILTER (WHERE status = 'closed_tp2')::int              AS tp2_hits,
            COUNT(*) FILTER (WHERE status = 'closed_sl')::int               AS sl_hits,
            COUNT(*) FILTER (WHERE status = 'closed_breakeven')::int        AS be_hits,
            COALESCE(SUM(pnl_usdt), 0)::float                               AS pnl_usdt,
            COALESCE(AVG(pnl_usdt) FILTER (WHERE COALESCE(pnl_usdt,0) > 0), 0)::float  AS avg_win,
            COALESCE(AVG(pnl_usdt) FILTER (WHERE COALESCE(pnl_usdt,0) <= 0), 0)::float AS avg_loss,
            COALESCE(MAX(pnl_usdt), 0)::float                               AS best_trade,
            COALESCE(MIN(pnl_usdt), 0)::float                               AS worst_trade,
            COALESCE(SUM(pnl_usdt) FILTER (WHERE COALESCE(pnl_usdt,0) > 0), 0)::float  AS gross_win,
            COALESCE(ABS(SUM(pnl_usdt) FILTER (WHERE COALESCE(pnl_usdt,0) <= 0)), 0)::float AS gross_loss
        FROM user_trades
        WHERE user_id=$1
          AND status IN ('closed_tp2','closed_sl','closed_breakeven','manual_close')
        """,
        row["id"],
    )
    closed   = int(all_time["closed_trades"] or 0)
    wins     = int(all_time["wins"] or 0)
    gw       = float(all_time["gross_win"] or 0)
    gl       = float(all_time["gross_loss"] or 0)
    return {
        "registered":     True,
        "enabled":        bool(row["enabled"]),
        "risk_pct":       float(row["risk_pct"] or 0),
        "leverage":       int(row["leverage"] or 0),
        "max_concurrent": int(row["max_concurrent"] or 0),
        "daily_loss_cap_pct": float(row["daily_loss_cap_pct"] or 0),
        "api_key_tail":   row["api_key_tail"] or "",
        "today_pnl_usdt": float(row["today_pnl_usdt"] or 0),
        "today_pnl_pct":  float(row["today_pnl_pct"] or 0),
        "today_trades":   int(row["today_trades"] or 0),
        "today_wins":     int(row["today_wins"] or 0),
        "closed_trades":  closed,
        "wins":           wins,
        "winrate":        (wins / closed * 100) if closed else 0.0,
        "pnl_usdt":       float(all_time["pnl_usdt"] or 0),
        "avg_win":        float(all_time["avg_win"] or 0),
        "avg_loss":       float(all_time["avg_loss"] or 0),
        "best_trade":     float(all_time["best_trade"] or 0),
        "worst_trade":    float(all_time["worst_trade"] or 0),
        "tp2_hits":       int(all_time["tp2_hits"] or 0),
        "sl_hits":        int(all_time["sl_hits"] or 0),
        "be_hits":        int(all_time["be_hits"] or 0),
        "profit_factor":  round(gw / gl, 2) if gl else 0.0,
    }
