from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from trade_executor.audit import insert_audit
from trade_executor.notify import notify

log = logging.getLogger("pnl_aggregator")

DAILY_CAP_REACHED = 9_999_999_999_999


def classify_close(*, direction: str, filled_at_tp_id: str | None,
                   filled_at_sl_id: str | None, status_before: str) -> str:
    if filled_at_tp_id:
        return "closed_tp2"
    if filled_at_sl_id:
        return "closed_breakeven" if status_before == "tp1_trailed" else "closed_sl"
    return status_before


def _today_start_ms() -> int:
    now = datetime.now(timezone.utc)
    midnight = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    return int(midnight.timestamp() * 1000)


async def reconcile_user(pool, *, ex, user_id: int) -> None:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, symbol, direction, qty, entry, status,
                   sl_order_id, tp_order_id, opened_at
            FROM user_trades
            WHERE user_id=$1 AND status IN ('open','tp1_trailed')
            """,
            user_id,
        )
    if not rows:
        return

    by_symbol: dict[str, list[dict]] = {}
    for r in rows:
        by_symbol.setdefault(r["symbol"], []).append(dict(r))

    for symbol, trades in by_symbol.items():
        try:
            fills = await ex.fetch_my_trades(symbol, since=_today_start_ms())
        except Exception as e:
            log.warning("fetch_my_trades %s failed: %s", symbol, e)
            continue
        for t in trades:
            tp_fills = [f for f in fills if str(f.get("order")) == str(t["tp_order_id"])]
            sl_fills = [f for f in fills if str(f.get("order")) == str(t["sl_order_id"])]
            if not tp_fills and not sl_fills:
                continue
            close_fill = (tp_fills + sl_fills)[0]
            close_px = float(close_fill["price"])
            qty = float(t["qty"])
            entry_px = float(t["entry"])
            sign = 1 if t["direction"] == "long" else -1
            gross = sign * (close_px - entry_px) * qty
            fee_close = float(close_fill.get("fee", {}).get("cost", 0) or 0)
            entry_fills = [f for f in fills if str(f.get("order")) == "e_skip"]
            fee_open = float(entry_fills[0]["fee"]["cost"]) if entry_fills else 0.0
            fees = fee_open + fee_close
            pnl_usdt = gross - fees
            pnl_pct = (pnl_usdt / (qty * entry_px)) * 100

            new_status = classify_close(
                direction=t["direction"],
                filled_at_tp_id=str(close_fill.get("order")) if tp_fills else None,
                filled_at_sl_id=str(close_fill.get("order")) if sl_fills else None,
                status_before=t["status"],
            )

            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE user_trades
                    SET status=$1, pnl_usdt=$2, pnl_pct=$3, fees_usdt=$4, closed_at=$5
                    WHERE id=$6
                    """,
                    new_status, pnl_usdt, pnl_pct, fees,
                    int(close_fill.get("timestamp") or time.time() * 1000),
                    t["id"],
                )
                await _upsert_daily(conn, user_id, pnl_usdt, won=(pnl_usdt > 0))
                await notify(conn, "trade_closed",
                             {"user_id": user_id, "trade_id": t["id"], "status": new_status,
                              "pnl_usdt": pnl_usdt, "pnl_pct": pnl_pct})

    async with pool.acquire() as conn:
        cap_row = await conn.fetchrow(
            """
            SELECT u.daily_loss_cap_pct, COALESCE(d.realized_pnl_pct, 0) AS pct
            FROM users u
            LEFT JOIN user_daily_pnl d ON d.user_id=u.id AND d.day=CURRENT_DATE
            WHERE u.id=$1
            """,
            user_id,
        )
        if cap_row and cap_row["pct"] <= -float(cap_row["daily_loss_cap_pct"]):
            await conn.execute(
                "UPDATE users SET paused_until=$1, pause_reason='daily_cap', updated_at=$2 WHERE id=$3",
                DAILY_CAP_REACHED, int(time.time() * 1000), user_id,
            )
            await insert_audit(conn, user_id, "paused", {"reason": "daily_cap"})
            await notify(conn, "daily_summary", {"user_id": user_id, "paused": True})


async def _upsert_daily(conn, user_id: int, pnl_usdt: float, *, won: bool) -> None:
    await conn.execute(
        """
        INSERT INTO user_daily_pnl (user_id, day, realized_pnl_usdt, realized_pnl_pct,
                                    trades_count, wins_count, day_start_balance_usdt)
        VALUES ($1, CURRENT_DATE, $2, $3, 1, $4, 100.0)
        ON CONFLICT (user_id, day) DO UPDATE SET
          realized_pnl_usdt = user_daily_pnl.realized_pnl_usdt + EXCLUDED.realized_pnl_usdt,
          realized_pnl_pct  = (user_daily_pnl.realized_pnl_usdt + EXCLUDED.realized_pnl_usdt)
                              / COALESCE(user_daily_pnl.day_start_balance_usdt, 100.0) * 100,
          trades_count      = user_daily_pnl.trades_count + 1,
          wins_count        = user_daily_pnl.wins_count + EXCLUDED.wins_count
        """,
        user_id, pnl_usdt,
        (pnl_usdt / 100.0) * 100,
        1 if won else 0,
    )
