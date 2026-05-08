from __future__ import annotations

import logging
from dataclasses import dataclass

from trade_executor.exchange import set_isolated_and_leverage

log = logging.getLogger("order_placer")


class OrderError(Exception):
    def __init__(self, stage: str, message: str):
        super().__init__(message)
        self.stage = stage


@dataclass
class PlacedOrders:
    entry_order_id: str
    sl_order_id: str | None
    tp_order_id: str | None
    avg_price: float


async def place_full_sequence(
    ex,
    *,
    symbol: str,
    side: str,
    qty: float,
    sl_price: float,
    tp_price: float,
    leverage: int,
) -> PlacedOrders:
    try:
        await set_isolated_and_leverage(ex, symbol, leverage)
    except Exception as e:
        raise OrderError("leverage", str(e))

    try:
        entry = await ex.create_order(symbol, "MARKET", side, qty, None, {})
    except Exception as e:
        raise OrderError("entry", str(e))
    entry_id = str(entry.get("id"))
    avg = float(entry.get("average") or entry.get("avgPrice") or 0)
    if not avg:
        raise OrderError("entry", "no avg price")

    close_side = "SELL" if side == "BUY" else "BUY"

    try:
        sl = await ex.fapiPrivatePostOrder({
            "symbol": symbol,
            "side": close_side,
            "type": "STOP_MARKET",
            "stopPrice": ex.price_to_precision(symbol, sl_price),
            "closePosition": "true",
            "workingType": "MARK_PRICE",
        })
        sl_id = str(sl.get("orderId") or sl.get("id"))
    except Exception as e:
        log.error("SL placement failed for %s: %s — emergency close", symbol, e)
        try:
            await ex.create_order(
                symbol, "MARKET", close_side, qty, None, {"reduceOnly": True},
            )
        except Exception as ee:
            log.critical("EMERGENCY CLOSE FAILED %s: %s", symbol, ee)
        raise OrderError("sl", str(e))

    tp_id: str | None = None
    try:
        tp = await ex.fapiPrivatePostOrder({
            "symbol": symbol,
            "side": close_side,
            "type": "TAKE_PROFIT_MARKET",
            "stopPrice": ex.price_to_precision(symbol, tp_price),
            "closePosition": "true",
            "workingType": "MARK_PRICE",
        })
        tp_id = str(tp.get("orderId") or tp.get("id"))
    except Exception as e:
        log.error("TP placement failed for %s: %s — SL still active", symbol, e)

    return PlacedOrders(entry_order_id=entry_id, sl_order_id=sl_id, tp_order_id=tp_id, avg_price=avg)
