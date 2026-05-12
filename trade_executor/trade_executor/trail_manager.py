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
        SELECT id, user_id, direction, qty, entry, sl, sl_order_id, sl_current,
               tp1, tp2, tp1_order_id, tp1_qty, tp2_qty
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


import os

# Trail mode: "structural" trips earlier (move to BE at 1R, then to +1R at 2R,
# then +2R at 3R) which suits the new magnet-anchored TP2 (typically 1.2-2.5R).
# Legacy "percent" mode keeps the 1.5R/2.5R/3.5R ladder used by fixed-RR TP=2.
_V2_TRAIL_MODE = os.environ.get("V2_TRAIL_MODE", "structural").lower()


def trail_sl_for_progress(*, direction: str, entry: float, sl: float, sl_current: float, price: float) -> float | None:
    progress = r_progress(direction=direction, entry=entry, sl=sl, price=price)

    if _V2_TRAIL_MODE == "structural":
        # Earlier breakeven + tighter ladder for magnet-mode trades.
        if progress >= 3.0:
            locked_r = 2.0
        elif progress >= 2.0:
            locked_r = 1.0
        elif progress >= 1.0:
            locked_r = 0.0  # breakeven
        else:
            return None
    else:
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


_BE_BUFFER_PCT = float(os.environ.get("V2_BE_BUFFER_PCT", "0.0005"))  # 0.05% fee cushion


def _compute_be_price(*, direction: str, entry: float, mark: float, buffer_pct: float) -> float:
    """Protective SL = entry + tiny fee buffer. Ensures runner cannot go worse than BE."""
    buf = float(entry) * float(buffer_pct)
    if direction == "long":
        return float(entry) + buf
    return float(entry) - buf


async def _check_tp1_filled(ex, *, symbol: str, trade: dict) -> bool:
    """Return True if TP1 has filled (inferred from qty reduction on Binance).

    We infer TP1 fill by comparing actual remaining position qty against
    tp2_qty stored in DB. If position size dropped to ≈ tp2_qty, TP1 filled.
    Falls back to False on any API error so we never trigger falsely.
    """
    tp1_order_id = trade.get("tp1_order_id")
    tp1_qty = float(trade.get("tp1_qty") or 0)
    tp2_qty = float(trade.get("tp2_qty") or 0)
    if not tp1_order_id or tp1_qty <= 0 or tp2_qty <= 0:
        return False  # no tiered TP for this trade
    try:
        positions = await ex.fapiPrivateV2GetPositionRisk({"symbol": symbol})
        for pos in positions if isinstance(positions, list) else [positions]:
            if (pos.get("symbol") or "").upper() == symbol.upper():
                remaining_qty = abs(float(pos.get("positionAmt") or 0))
                # TP1 filled if remaining ≈ tp2_qty (within 10% of step noise)
                if remaining_qty <= tp2_qty * 1.05 and remaining_qty > 0:
                    return True
    except Exception as e:
        log.debug("_check_tp1_filled error %s: %s", symbol, e)
    return False


async def _arm_protective_sl(pool, ex, *, symbol: str, trade: dict, mark: float | None) -> None:
    """After TP1 fills, place a breakeven+ SL order protecting the runner (tp2_qty).

    If SL was OFF (sl_order_id empty), this is the FIRST SL ever placed for the
    trade. If SL was already on, we cancel and replace to ensure correct qty.
    """
    is_long = trade["direction"] == "long"
    side = "long" if is_long else "short"
    close_side = "SELL" if is_long else "BUY"
    pos_side = None
    if getattr(ex, "_is_hedge_mode", False):
        pos_side = "LONG" if is_long else "SHORT"

    entry = float(trade["entry"])
    tp2_qty = float(trade.get("tp2_qty") or trade["qty"])
    be_price = _compute_be_price(direction=side, entry=entry, mark=mark or entry, buffer_pct=_BE_BUFFER_PCT)
    if mark:
        be_price = adjust_sl_for_mark(side=side, sl_price=be_price, mark=mark)

    # Cancel existing SL if any
    old_sl_id = trade.get("sl_order_id")
    if old_sl_id:
        await cancel_algo(ex, symbol=symbol, algo_id=old_sl_id)

    try:
        new_sl_resp = await place_algo_stop(
            ex, symbol=symbol, close_side=close_side, quantity=tp2_qty,
            trigger_price=be_price, order_type="STOP_MARKET",
            position_side=pos_side,
        )
        new_sl_id = algo_id_of(new_sl_resp)
    except Exception as e:
        log.error("protective SL arm failed %s/%s: %s", symbol, trade["id"], e)
        return

    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE user_trades
            SET status='tp1_trailed', sl_current=$1, sl_order_id=$2
            WHERE id=$3 AND status IN ('open','tp1_trailed')
            """,
            be_price, new_sl_id, trade["id"],
        )
        await notify(conn, "trade_tp1_trailed", {
            "user_id": trade["user_id"],
            "trade_id": trade["id"],
            "protective_sl": be_price,
        })
    log.info(
        "protective SL armed %s/%s direction=%s be=%.6f qty=%.6f sl_id=%s",
        symbol, trade["id"], side, be_price, tp2_qty, new_sl_id,
    )


async def maybe_trail(pool, *, ex, symbol: str, price: float) -> bool:
    trailed_any = False
    async with pool.acquire() as conn:
        trades = await _open_in_symbol(conn, symbol)

    for t in trades:
        is_long = t["direction"] == "long"
        side = "long" if is_long else "short"
        close_side = "SELL" if is_long else "BUY"

        # ── Phase 1: detect TP1 fill → arm protective SL ──────────────────
        if t["status"] == "open" and t.get("tp1_order_id") and t.get("tp1_qty"):
            tp1_filled = await _check_tp1_filled(ex, symbol=symbol, trade=t)
            if tp1_filled:
                mark = await fetch_mark_price(ex, symbol)
                await _arm_protective_sl(pool, ex, symbol=symbol, trade=t, mark=mark)
                trailed_any = True
                continue  # skip ladder trail this tick; SL already updated

        # ── Phase 2: regular progress trail (ladder) ───────────────────────
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
