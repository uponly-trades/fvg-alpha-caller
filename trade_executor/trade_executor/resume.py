from __future__ import annotations

import logging
import time
from typing import Awaitable, Callable

log = logging.getLogger("resume")


async def resume_in_flight(pool, *, ex_factory: Callable[[int], object | Awaitable[object]]) -> None:
    """On boot, walk every trade in (opening, open, tp1_trailed) and reconcile.

    - opening: check entry order; if FILLED → place SL+TP now; else → cancel + error.
    - open / tp1_trailed: leave to running loops (mark-price WS + reconcile loop).
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, user_id, symbol, direction, qty, entry, sl, tp2,
                   entry_order_id, status
            FROM user_trades
            WHERE status IN ('opening','open','tp1_trailed')
            """
        )

    for r in rows:
        if r["status"] != "opening":
            continue
        ex = ex_factory(r["user_id"])
        if hasattr(ex, "__await__"):
            ex = await ex
        try:
            order = await ex.fetch_order(r["entry_order_id"], r["symbol"])
        except Exception as e:
            log.warning("fetch_order failed during resume %s: %s", r["id"], e)
            continue

        if order.get("status") != "FILLED":
            try:
                await ex.cancel_order(r["entry_order_id"], r["symbol"])
            except Exception:
                pass
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE user_trades SET status='error_restart', closed_at=$1 WHERE id=$2",
                    int(time.time() * 1000), r["id"],
                )
            continue

        avg = float(order.get("average") or order.get("avgPrice") or r["entry"])
        close_side = "SELL" if r["direction"] == "long" else "BUY"
        try:
            sl = await ex.create_order(
                r["symbol"], "STOP_MARKET", close_side, float(r["qty"]), None,
                {"stopPrice": float(r["sl"]), "closePosition": True, "workingType": "MARK_PRICE"},
            )
            tp = await ex.create_order(
                r["symbol"], "TAKE_PROFIT_MARKET", close_side, float(r["qty"]), None,
                {"stopPrice": float(r["tp2"]), "closePosition": True, "workingType": "MARK_PRICE"},
            )
        except Exception as e:
            log.error("resume SL/TP placement failed for %s: %s", r["id"], e)
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE user_trades SET status='error_no_sl', error_msg=$1 WHERE id=$2",
                    str(e), r["id"],
                )
            continue

        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE user_trades SET
                  status='open', entry=$1, sl_current=$2,
                  sl_order_id=$3, tp_order_id=$4
                WHERE id=$5
                """,
                avg, float(r["sl"]), str(sl["id"]), str(tp["id"]), r["id"],
            )
