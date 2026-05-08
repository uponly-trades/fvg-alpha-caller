from __future__ import annotations

import asyncio
import json
import logging

import websockets

from trade_executor.algo_orders import (
    adjust_sl_for_mark,
    algo_id_of,
    cancel_algo,
    fetch_mark_price,
    place_algo_stop,
)
from trade_executor.notify import notify

log = logging.getLogger("trail_manager")


async def _open_in_symbol(conn, symbol: str) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT id, user_id, direction, qty, tp1, sl_order_id, sl_current
        FROM user_trades
        WHERE symbol=$1 AND status='open'
        """,
        symbol,
    )
    return [dict(r) for r in rows]


async def maybe_trail(pool, *, ex, symbol: str, price: float) -> bool:
    """For each open trade in symbol, if price has crossed TP1, trail SL to TP1.

    Returns True if at least one trade was trailed.
    """
    trailed_any = False
    async with pool.acquire() as conn:
        trades = await _open_in_symbol(conn, symbol)

    for t in trades:
        is_long = t["direction"] == "long"
        crossed = (is_long and price >= float(t["tp1"])) or (
            not is_long and price <= float(t["tp1"])
        )
        if not crossed:
            continue

        if t["sl_order_id"]:
            await cancel_algo(ex, symbol=symbol, algo_id=t["sl_order_id"])

        close_side = "SELL" if is_long else "BUY"
        side = "long" if is_long else "short"
        trail_price = float(t["tp1"])
        mark = await fetch_mark_price(ex, symbol)
        if mark:
            trail_price = adjust_sl_for_mark(side=side, sl_price=trail_price, mark=mark)
        pos_side = None
        if getattr(ex, "_is_hedge_mode", False):
            pos_side = "LONG" if is_long else "SHORT"
        try:
            new_sl = await place_algo_stop(
                ex, symbol=symbol, close_side=close_side,
                trigger_price=trail_price, order_type="STOP_MARKET",
                position_side=pos_side,
            )
        except Exception as e:
            log.error("trail SL placement failed for %s/%s: %s", symbol, t["id"], e)
            continue
        new_sl_id = algo_id_of(new_sl)

        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE user_trades
                SET status='tp1_trailed', sl_current=$1, sl_order_id=$2
                WHERE id=$3 AND status='open'
                """,
                trail_price, new_sl_id, t["id"],
            )
            await notify(conn, "trade_tp1_trailed",
                         {"user_id": t["user_id"], "trade_id": t["id"]})
        trailed_any = True

    return trailed_any


async def run_mark_price_ws(pool, *, ex_factory, get_active_symbols, proxy_url: str | None = None):
    """Long-running task: subscribe to mark-price for all symbols with open trades.

    Reconciles symbol set every 30s. Reconnects on disconnect.
    """
    while True:
        try:
            symbols = await get_active_symbols()
            if not symbols:
                await asyncio.sleep(5)
                continue
            streams = "/".join(f"{s.lower()}@markPrice@1s" for s in symbols)
            url = f"wss://fstream.binance.com/stream?streams={streams}"
            log.info("connecting mark-price WS: %d symbols", len(symbols))
            async with websockets.connect(url, ping_interval=20) as ws:
                deadline = asyncio.get_event_loop().time() + 30.0
                async for msg in ws:
                    data = json.loads(msg).get("data", {})
                    sym = data.get("s")
                    price = float(data.get("p", 0))
                    if sym and price > 0:
                        ex = await ex_factory(sym)
                        try:
                            await maybe_trail(pool, ex=ex, symbol=sym, price=price)
                        except Exception as e:
                            log.exception("maybe_trail failed: %s", e)
                    if asyncio.get_event_loop().time() >= deadline:
                        break
        except Exception as e:
            log.warning("mark-price WS error: %s — retrying in 5s", e)
            await asyncio.sleep(5)
