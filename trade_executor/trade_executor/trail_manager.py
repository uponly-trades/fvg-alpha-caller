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
        SELECT id, user_id, direction, qty, entry, sl, sl_order_id, sl_current
        FROM user_trades
        WHERE symbol=$1 AND status IN ('open','tp1_trailed')
        """,
        symbol,
    )
    return [dict(r) for r in rows]


def r_progress(*, direction: str, entry: float, sl: float, price: float) -> float:
    r = abs(float(entry) - float(sl))
    if r <= 0:
        return 0.0
    if direction == "long":
        return (float(price) - float(entry)) / r
    return (float(entry) - float(price)) / r


def trail_sl_for_progress(*, direction: str, entry: float, sl: float, sl_current: float, price: float) -> float | None:
    progress = r_progress(direction=direction, entry=entry, sl=sl, price=price)
    if progress >= 3.5:
        locked_r = 2.5
    elif progress >= 2.5:
        locked_r = 1.5
    elif progress >= 1.5:
        locked_r = 1.0
    else:
        return None

    r = abs(float(entry) - float(sl))
    if direction == "long":
        next_sl = float(entry) + r * locked_r
        if next_sl <= float(sl_current):
            return None
    else:
        next_sl = float(entry) - r * locked_r
        if next_sl >= float(sl_current):
            return None
    return next_sl


async def maybe_trail(pool, *, ex, symbol: str, price: float) -> bool:
    trailed_any = False
    async with pool.acquire() as conn:
        trades = await _open_in_symbol(conn, symbol)

    for t in trades:
        is_long = t["direction"] == "long"
        side = "long" if is_long else "short"
        trail_price = trail_sl_for_progress(
            direction=side,
            entry=float(t["entry"]),
            sl=float(t["sl"]),
            sl_current=float(t["sl_current"]),
            price=price,
        )
        if trail_price is None:
            continue

        if t["sl_order_id"]:
            await cancel_algo(ex, symbol=symbol, algo_id=t["sl_order_id"])

        close_side = "SELL" if is_long else "BUY"
        mark = await fetch_mark_price(ex, symbol)
        if mark:
            trail_price = adjust_sl_for_mark(side=side, sl_price=trail_price, mark=mark)
        pos_side = None
        if getattr(ex, "_is_hedge_mode", False):
            pos_side = "LONG" if is_long else "SHORT"
        try:
            new_sl = await place_algo_stop(
                ex, symbol=symbol, close_side=close_side, quantity=float(t["qty"]),
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
                WHERE id=$3 AND status IN ('open','tp1_trailed')
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
