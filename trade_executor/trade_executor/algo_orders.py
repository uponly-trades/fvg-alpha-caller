from __future__ import annotations

import logging
import math

log = logging.getLogger("algo_orders")

_MIN_MARK_BUFFER_PCT = 0.0015  # 0.15% min distance from mark price


def _safe_price(p) -> float | None:
    try:
        v = float(p)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v) or v <= 0:
        return None
    return v


async def fetch_mark_price(ex, symbol: str) -> float | None:
    try:
        ticker = await ex.fapiPublicGetPremiumIndex({"symbol": symbol})
        return _safe_price(ticker.get("markPrice"))
    except Exception as e:
        log.warning("fetch_mark_price failed %s: %s", symbol, e)
        return None


def adjust_sl_for_mark(*, side: str, sl_price: float, mark: float) -> float:
    """If SL is on the wrong side of mark (would trigger immediately), nudge it
    to mark ± _MIN_MARK_BUFFER_PCT. side = 'long' or 'short' (entry side).
    """
    buf = mark * _MIN_MARK_BUFFER_PCT
    if side == "long":
        # SL must be BELOW mark
        if sl_price >= mark - buf:
            return mark - buf
    else:
        if sl_price <= mark + buf:
            return mark + buf
    return sl_price


def adjust_tp_for_mark(*, side: str, tp_price: float, mark: float) -> float:
    """Mirror of adjust_sl_for_mark for take-profit."""
    buf = mark * _MIN_MARK_BUFFER_PCT
    if side == "long":
        # TP must be ABOVE mark
        if tp_price <= mark + buf:
            return mark + buf
    else:
        if tp_price >= mark - buf:
            return mark - buf
    return tp_price


async def place_algo_stop(
    ex,
    *,
    symbol: str,
    close_side: str,
    trigger_price: float,
    order_type: str = "STOP_MARKET",
    position_side: str | None = None,
) -> dict:
    """Place a CONDITIONAL algo order via fapi/v1/algo/futures/newOrderAlgo.

    Raises ValueError if trigger_price is not a positive finite number — caller
    must validate upstream because Binance returns -1102 for empty triggerPrice.

    position_side: pass "LONG"/"SHORT" when account is in hedge mode (-4061).
    """
    p = _safe_price(trigger_price)
    if p is None:
        raise ValueError(f"invalid trigger_price={trigger_price!r}")
    params: dict = {
        "symbol": symbol,
        "side": close_side,
        "type": order_type,
        "algoType": "CONDITIONAL",
        "triggerPrice": ex.price_to_precision(symbol, p),
        "closePosition": "true",
        "workingType": "MARK_PRICE",
    }
    if position_side:
        params["positionSide"] = position_side
    return await ex.fapiPrivatePostAlgoOrder(params)


def algo_id_of(resp: dict) -> str:
    return str(resp.get("algoId") or resp.get("orderId") or resp.get("id"))


async def cancel_algo(ex, *, symbol: str, algo_id: str) -> None:
    """Cancel a CONDITIONAL algo order. Suppresses 'unknown order' (already
    cancelled / triggered)."""
    try:
        await ex.fapiPrivateDeleteAlgoOrder({"symbol": symbol, "algoId": algo_id})
    except Exception as e:
        msg = str(e)
        if "-2011" in msg or "Unknown order" in msg:
            log.debug("cancel_algo: already gone %s/%s", symbol, algo_id)
            return
        log.warning("cancel_algo failed %s/%s: %s", symbol, algo_id, e)
