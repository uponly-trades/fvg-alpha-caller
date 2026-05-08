from __future__ import annotations

import logging
from dataclasses import dataclass

from trade_executor.algo_orders import (
    adjust_sl_for_mark,
    adjust_tp_for_mark,
    algo_id_of,
    fetch_mark_price,
    place_algo_stop,
)
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
    if not (sl_price and sl_price > 0):
        raise OrderError("sl", f"invalid sl_price={sl_price}")
    if not (tp_price and tp_price > 0):
        raise OrderError("tp", f"invalid tp_price={tp_price}")

    try:
        await set_isolated_and_leverage(ex, symbol, leverage)
    except Exception as e:
        raise OrderError("leverage", str(e))

    pos_side: str | None = None
    if getattr(ex, "_is_hedge_mode", False):
        pos_side = "LONG" if side == "BUY" else "SHORT"

    entry_params: dict = {"positionSide": pos_side} if pos_side else {}
    try:
        entry = await ex.create_order(symbol, "MARKET", side, qty, None, entry_params)
    except Exception as e:
        # -4061: hedge mode mismatch — retry with positionSide (mode detection raced)
        if "4061" in str(e) and not pos_side:
            pos_side = "LONG" if side == "BUY" else "SHORT"
            try:
                entry = await ex.create_order(
                    symbol, "MARKET", side, qty, None, {"positionSide": pos_side},
                )
                ex._is_hedge_mode = True  # cache for SL/TP below
            except Exception as ee:
                raise OrderError("entry", str(ee))
        else:
            raise OrderError("entry", str(e))
    entry_id = str(entry.get("id"))
    avg = float(entry.get("average") or entry.get("avgPrice") or 0)
    if not avg:
        raise OrderError("entry", "no avg price")

    close_side = "SELL" if side == "BUY" else "BUY"
    entry_side = "long" if side == "BUY" else "short"

    # Re-validate SL vs current mark — entry slippage may have moved mark past
    # planned SL, which would trigger -2021 "Order would immediately trigger".
    mark = await fetch_mark_price(ex, symbol)
    if mark:
        sl_price = adjust_sl_for_mark(side=entry_side, sl_price=sl_price, mark=mark)
        tp_price = adjust_tp_for_mark(side=entry_side, tp_price=tp_price, mark=mark)

    # In hedge mode, position_side on the original (not flipped) side closes it.
    pos_side_for_close = pos_side

    try:
        sl = await place_algo_stop(
            ex, symbol=symbol, close_side=close_side, trigger_price=sl_price,
            order_type="STOP_MARKET", position_side=pos_side_for_close,
        )
        sl_id = algo_id_of(sl)
    except Exception as e:
        log.error("SL placement failed for %s: %s — emergency close", symbol, e)
        emerg_params: dict = {"reduceOnly": True}
        if pos_side_for_close:
            emerg_params = {"positionSide": pos_side_for_close}  # reduceOnly invalid in hedge
        try:
            await ex.create_order(symbol, "MARKET", close_side, qty, None, emerg_params)
        except Exception as ee:
            log.critical("EMERGENCY CLOSE FAILED %s: %s", symbol, ee)
        raise OrderError("sl", str(e))

    tp_id: str | None = None
    try:
        tp = await place_algo_stop(
            ex, symbol=symbol, close_side=close_side, trigger_price=tp_price,
            order_type="TAKE_PROFIT_MARKET", position_side=pos_side_for_close,
        )
        tp_id = algo_id_of(tp)
    except Exception as e:
        log.error("TP placement failed for %s: %s — SL still active", symbol, e)

    return PlacedOrders(entry_order_id=entry_id, sl_order_id=sl_id, tp_order_id=tp_id, avg_price=avg)
