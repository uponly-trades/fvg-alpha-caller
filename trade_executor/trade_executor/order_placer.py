from __future__ import annotations

import logging
import math
import os
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

# Tiered TP: fraction of qty closed at TP1 (rest runs to TP2). Set to 0 to
# disable tiered TP and fall back to single TP at tp2.
TP1_FRACTION = float(os.environ.get("V2_TP1_FRACTION", "0.5"))


class OrderError(Exception):
    def __init__(self, stage: str, message: str):
        super().__init__(message)
        self.stage = stage


@dataclass
class PlacedOrders:
    entry_order_id: str
    sl_order_id: str | None
    tp_order_id: str | None        # TP2 (runner) order id — kept for legacy
    avg_price: float
    sl_price: float
    tp_price: float                 # TP2 price after mark-adjust
    tp1_order_id: str | None = None
    tp1_price: float = 0.0
    tp1_qty: float = 0.0
    tp2_qty: float = 0.0


async def place_full_sequence(
    ex,
    *,
    symbol: str,
    side: str,
    qty: float,
    sl_price: float | None,
    tp_price: float,
    leverage: int,
    margin_mode: str = "ISOLATED",
    rr_ratio: float = 1.0,
    tp1_price: float | None = None,
) -> PlacedOrders:
    sl_off = sl_price is None or (isinstance(sl_price, (int, float)) and sl_price <= 0)
    if sl_off:
        if str(margin_mode).upper() != "ISOLATED":
            raise OrderError(
                "sl_off_guard",
                "SL OFF requires ISOLATED margin mode; refusing CROSSED",
            )
        sl_price = None
    if not (tp_price and tp_price > 0):
        raise OrderError("tp", f"invalid tp_price={tp_price}")

    try:
        await set_isolated_and_leverage(ex, symbol, leverage, margin_mode)
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
    rr_ratio = max(1.0, float(rr_ratio or 1.0))
    if sl_price is not None:
        # Recompute TP from actual fill price + SL distance * RR (handles slippage)
        planned_risk_distance = abs(avg - sl_price)
        if side == "BUY":
            tp_price = avg + planned_risk_distance * rr_ratio
        else:
            tp_price = avg - planned_risk_distance * rr_ratio
    # When SL is OFF, use the TP price passed in as-is. Do NOT derive a
    # synthetic risk from (tp - avg) and multiply by rr_ratio — that double-
    # extends the target and ignores the orchestrator's structural TP2.

    # Re-validate SL vs current mark — entry slippage may have moved mark past
    # planned SL, which would trigger -2021 "Order would immediately trigger".
    mark = await fetch_mark_price(ex, symbol)
    if mark and sl_price is not None:
        sl_price = adjust_sl_for_mark(side=entry_side, sl_price=sl_price, mark=mark)
    if mark:
        tp_price = adjust_tp_for_mark(side=entry_side, tp_price=tp_price, mark=mark)

    # In hedge mode, position_side on the original (not flipped) side closes it.
    pos_side_for_close = pos_side

    sl_id: str | None = None
    if sl_price is not None:
        try:
            sl = await place_algo_stop(
                ex, symbol=symbol, close_side=close_side, quantity=qty,
                trigger_price=sl_price, order_type="STOP_MARKET",
                position_side=pos_side_for_close,
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

    # Tiered TP: split qty into TP1 + TP2 when tp1_price provided and fraction>0.
    # TP1 = reduceOnly partial close at the nearer magnet; remaining runner sits
    # on TP2. After TP1 fills, trail_manager auto-arms protective SL to BE+.
    tp1_qty = 0.0
    tp2_qty = qty
    tp1_id: str | None = None
    tp1_actual: float = 0.0

    if (
        tp1_price is not None
        and tp1_price > 0
        and TP1_FRACTION > 0.0
        and TP1_FRACTION < 1.0
    ):
        # Adjust TP1 vs mark before precision so the order survives -2021.
        adj_tp1 = adjust_tp_for_mark(side=entry_side, tp_price=float(tp1_price), mark=mark) if mark else float(tp1_price)
        # Step-size aware split
        try:
            raw_tp1_qty = qty * TP1_FRACTION
            tp1_qty_str = ex.amount_to_precision(symbol, raw_tp1_qty)
            tp1_qty_p = float(tp1_qty_str)
        except Exception:
            tp1_qty_p = 0.0
        # Avoid placing a zero-qty or oversize order
        if tp1_qty_p > 0 and tp1_qty_p < qty:
            tp2_qty_candidate = qty - tp1_qty_p
            try:
                tp2_qty_p = float(ex.amount_to_precision(symbol, tp2_qty_candidate))
            except Exception:
                tp2_qty_p = tp2_qty_candidate
            if tp2_qty_p > 0:
                try:
                    tp1_resp = await place_algo_stop(
                        ex, symbol=symbol, close_side=close_side, quantity=tp1_qty_p,
                        trigger_price=adj_tp1, order_type="TAKE_PROFIT_MARKET",
                        position_side=pos_side_for_close,
                    )
                    tp1_id = algo_id_of(tp1_resp)
                    tp1_actual = adj_tp1
                    tp1_qty = tp1_qty_p
                    tp2_qty = tp2_qty_p
                except Exception as e:
                    log.error("TP1 placement failed for %s: %s — falling back to single TP", symbol, e)
                    tp1_qty = 0.0
                    tp2_qty = qty

    tp_id: str | None = None
    try:
        tp = await place_algo_stop(
            ex, symbol=symbol, close_side=close_side, quantity=tp2_qty,
            trigger_price=tp_price, order_type="TAKE_PROFIT_MARKET",
            position_side=pos_side_for_close,
        )
        tp_id = algo_id_of(tp)
    except Exception as e:
        log.error("TP placement failed for %s: %s — SL still active", symbol, e)

    return PlacedOrders(
        entry_order_id=entry_id,
        sl_order_id=sl_id,
        tp_order_id=tp_id,
        avg_price=avg,
        sl_price=sl_price if sl_price is not None else 0.0,
        tp_price=tp_price,
        tp1_order_id=tp1_id,
        tp1_price=tp1_actual,
        tp1_qty=tp1_qty,
        tp2_qty=tp2_qty,
    )
