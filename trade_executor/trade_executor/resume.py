from __future__ import annotations

import logging
import time
from typing import Awaitable, Callable

from trade_executor.algo_orders import (
    adjust_sl_for_mark,
    adjust_tp_for_mark,
    algo_id_of,
    fetch_mark_price,
    place_algo_stop,
)

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
        sl_price = float(r["sl"]) if r["sl"] else 0.0
        tp_price = float(r["tp2"]) if r["tp2"] else 0.0
        if sl_price <= 0 or tp_price <= 0:
            log.error("resume %s: invalid sl=%s tp=%s", r["id"], sl_price, tp_price)
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE user_trades SET status='error_no_sl', error_msg=$1 WHERE id=$2",
                    f"invalid sl/tp sl={sl_price} tp={tp_price}", r["id"],
                )
            continue

        mark = await fetch_mark_price(ex, r["symbol"])
        if mark:
            sl_price = adjust_sl_for_mark(side=r["direction"], sl_price=sl_price, mark=mark)
            tp_price = adjust_tp_for_mark(side=r["direction"], tp_price=tp_price, mark=mark)

        pos_side = None
        if getattr(ex, "_is_hedge_mode", False):
            pos_side = "LONG" if r["direction"] == "long" else "SHORT"
        try:
            sl = await place_algo_stop(
                ex, symbol=r["symbol"], close_side=close_side,
                trigger_price=sl_price, order_type="STOP_MARKET",
                position_side=pos_side,
            )
            tp = await place_algo_stop(
                ex, symbol=r["symbol"], close_side=close_side,
                trigger_price=tp_price, order_type="TAKE_PROFIT_MARKET",
                position_side=pos_side,
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
                avg, sl_price, algo_id_of(sl), algo_id_of(tp), r["id"],
            )
