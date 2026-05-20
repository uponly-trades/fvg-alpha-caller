from __future__ import annotations

import asyncio
import json
import logging
import os
import time

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
        SELECT id, user_id, decision_id, exit_mode, status, direction, qty, entry, sl, sl_order_id, sl_current,
               tf, tp1, tp2, tp1_order_id, tp1_qty, tp2_qty
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


def is_pine_retest_trade(trade: dict) -> bool:
    return str(trade.get("exit_mode") or "") == "supertrend_band"


_ST_STATE_MAX_AGE_MS = int(os.environ.get("ST_STATE_MAX_AGE_MS", str(45 * 60 * 1000)))


async def _latest_supertrend_band(conn, *, symbol: str, tf: str = "15m") -> float | None:
    row = await conn.fetchrow(
        """
        SELECT band, updated_at
        FROM supertrend_state
        WHERE symbol=$1 AND tf=$2
        """,
        symbol, tf,
    )
    if not row:
        return None
    updated_at = int(row["updated_at"] or 0)
    if updated_at and int(time.time() * 1000) - updated_at > _ST_STATE_MAX_AGE_MS:
        return None
    band = float(row["band"] or 0.0)
    return band if band > 0 else None


def should_replace_supertrend_band(*, direction: str, sl_current: float, st_band: float) -> bool:
    if st_band <= 0:
        return False
    if sl_current <= 0:
        return True
    if direction == "long":
        return st_band > sl_current
    return st_band < sl_current


# Legacy non-Pine trail mode. Pine-retets trades bypass this path and follow
# the persisted SuperTrend band instead.
_V2_TRAIL_MODE = os.environ.get("V2_TRAIL_MODE", "structural").lower()


def trail_sl_for_progress(*, direction: str, entry: float, sl: float, sl_current: float, price: float) -> float | None:
    progress = r_progress(direction=direction, entry=entry, sl=sl, price=price)

    if _V2_TRAIL_MODE == "structural":
        # Earlier breakeven + tighter ladder for legacy non-Pine trades.
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
_AUTO_TP_ENABLED = os.environ.get("AUTO_TP_15M_ENABLED", "1") == "1"
_AUTO_TP_MIN_AGE_SEC = int(os.environ.get("AUTO_TP_MIN_AGE_SEC", str(15 * 60)))
_AUTO_TP_FRACTION = float(os.environ.get("AUTO_TP_FRACTION", "0.3"))
_AUTO_BE_FIRST_ENABLED = os.environ.get("AUTO_BE_FIRST_15M_ENABLED", "1") == "1"
_AUTO_TIMEOUT_CLOSE_SEC = int(os.environ.get("AUTO_CLOSE_AFTER_SEC", str(2 * 60 * 60)))


def _compute_be_price(*, direction: str, entry: float, mark: float, buffer_pct: float) -> float:
    """Protective SL = entry + tiny fee buffer. Ensures runner cannot go worse than BE."""
    buf = float(entry) * float(buffer_pct)
    if direction == "long":
        return float(entry) + buf
    return float(entry) - buf


def _is_green(*, direction: str, entry: float, price: float) -> bool:
    if direction == "long":
        return float(price) > float(entry)
    return float(price) < float(entry)


def _tp_fraction_qty(ex, *, symbol: str, qty: float, fraction: float) -> float:
    fraction = max(0.0, min(1.0, float(fraction)))
    if fraction <= 0.0:
        return 0.0
    try:
        return float(ex.amount_to_precision(symbol, float(qty) * fraction))
    except Exception:
        return float(qty) * fraction


async def _market_close_qty(ex, *, symbol: str, direction: str, qty: float, close_side: str) -> dict:
    pos_side = None
    if getattr(ex, "_is_hedge_mode", False):
        pos_side = "LONG" if direction == "long" else "SHORT"
    params: dict = {"reduceOnly": True}
    if pos_side:
        params = {"positionSide": pos_side}
    return await ex.create_order(symbol, "MARKET", close_side, qty, None, params)


def _remaining_qty(trade: dict) -> float:
    return float(trade.get("tp2_qty") or trade.get("qty") or 0.0)


async def _replace_sl_at_entry(pool, ex, *, symbol: str, trade: dict, price: float) -> bool:
    """At +15m, move active trade SL to entry/BE+ without taking profit yet."""
    if not _AUTO_BE_FIRST_ENABLED:
        return False
    if trade.get("status") != "open":
        return False
    if float(trade.get("sl_current") or 0.0) > 0:
        direction = str(trade["direction"])
        entry = float(trade["entry"])
        current = float(trade["sl_current"])
        if direction == "long" and current >= entry:
            return False
        if direction == "short" and current <= entry:
            return False
    opened_at = int(trade.get("opened_at") or 0)
    if int(time.time() * 1000) - opened_at < _AUTO_TP_MIN_AGE_SEC * 1000:
        return False

    direction = str(trade["direction"])
    is_long = direction == "long"
    close_side = "SELL" if is_long else "BUY"
    qty = _remaining_qty(trade)
    if qty <= 0:
        return False
    old_sl_id = trade.get("sl_order_id")
    if old_sl_id:
        await cancel_algo(ex, symbol=symbol, algo_id=old_sl_id)
    mark = await fetch_mark_price(ex, symbol) or float(price)
    entry = float(trade["entry"])
    be_price = _compute_be_price(direction=direction, entry=entry, mark=mark, buffer_pct=_BE_BUFFER_PCT)
    be_price = adjust_sl_for_mark(side=direction, sl_price=be_price, mark=mark)
    pos_side = None
    if getattr(ex, "_is_hedge_mode", False):
        pos_side = "LONG" if is_long else "SHORT"
    try:
        sl_resp = await place_algo_stop(
            ex, symbol=symbol, close_side=close_side, quantity=qty,
            trigger_price=be_price, order_type="STOP_MARKET",
            position_side=pos_side,
        )
    except Exception as e:
        log.error("auto BE SL placement failed %s/%s: %s", symbol, trade["id"], e)
        return False
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE user_trades
            SET sl_current=$1, sl_order_id=$2
            WHERE id=$3 AND status='open'
            """,
            be_price, algo_id_of(sl_resp), trade["id"],
        )
        await notify(conn, "trade_tp1_trailed", {
            "user_id": trade["user_id"],
            "trade_id": trade["id"],
            "protective_sl": be_price,
            "reason": "auto_be_15m",
        })
    log.info("auto BE SL armed %s/%s direction=%s be=%.6f qty=%.6f", symbol, trade["id"], direction, be_price, qty)
    return True


async def _timeout_close(pool, ex, *, symbol: str, trade: dict) -> bool:
    """Close any still-active trade after 2h to avoid stale/anomaly positions."""
    if _AUTO_TIMEOUT_CLOSE_SEC <= 0:
        return False
    if trade.get("status") not in {"open", "tp1_trailed"}:
        return False
    opened_at = int(trade.get("opened_at") or 0)
    if int(time.time() * 1000) - opened_at < _AUTO_TIMEOUT_CLOSE_SEC * 1000:
        return False
    direction = str(trade["direction"])
    is_long = direction == "long"
    close_side = "SELL" if is_long else "BUY"
    qty = _remaining_qty(trade) if trade.get("status") == "tp1_trailed" else float(trade.get("qty") or 0.0)
    if qty <= 0:
        return False
    for order_key in ("sl_order_id", "tp_order_id"):
        oid = trade.get(order_key)
        if oid:
            await cancel_algo(ex, symbol=symbol, algo_id=oid)
    try:
        resp = await _market_close_qty(ex, symbol=symbol, direction=direction, qty=qty, close_side=close_side)
    except Exception as e:
        log.error("timeout close failed %s/%s: %s", symbol, trade["id"], e)
        return False
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE user_trades
            SET tp_order_id=$1
            WHERE id=$2 AND status IN ('open','tp1_trailed')
            """,
            str(resp.get("id") or resp.get("orderId") or "timeout_market_close"), trade["id"],
        )
    log.info("timeout close submitted %s/%s direction=%s qty=%.6f", symbol, trade["id"], direction, qty)
    return True


async def _auto_tp_half_and_be(pool, ex, *, symbol: str, trade: dict, price: float) -> bool:
    """Every 15m loop: if trade is green, close a partial and move SL to entry.

    Guardrails:
    - min age defaults to 15m, so a fresh entry never TP's immediately.
    - only status='open' and tp1_qty empty/zero, so it runs once per trade.
    - runner qty is protected by SL at entry/BE+ after partial TP.
    """
    if not _AUTO_TP_ENABLED:
        return False
    if trade.get("status") != "open":
        return False
    if float(trade.get("tp1_qty") or 0.0) > 0.0:
        return False
    opened_at = int(trade.get("opened_at") or 0)
    age_ms = int(time.time() * 1000) - opened_at
    if age_ms < _AUTO_TP_MIN_AGE_SEC * 1000:
        return False

    direction = str(trade["direction"])
    entry = float(trade["entry"])
    if not _is_green(direction=direction, entry=entry, price=price):
        return False

    qty = float(trade["qty"])
    close_qty = _tp_fraction_qty(ex, symbol=symbol, qty=qty, fraction=_AUTO_TP_FRACTION)
    if close_qty <= 0 or close_qty >= qty:
        return False
    runner_qty = qty - close_qty
    try:
        runner_qty = float(ex.amount_to_precision(symbol, runner_qty))
    except Exception:
        pass
    if runner_qty <= 0:
        return False

    is_long = direction == "long"
    close_side = "SELL" if is_long else "BUY"
    if trade.get("tp_order_id"):
        await cancel_algo(ex, symbol=symbol, algo_id=trade["tp_order_id"])
    old_sl_id = trade.get("sl_order_id")
    if old_sl_id:
        await cancel_algo(ex, symbol=symbol, algo_id=old_sl_id)

    try:
        tp_resp = await _market_close_qty(
            ex, symbol=symbol, direction=direction, qty=close_qty, close_side=close_side,
        )
    except Exception as e:
        log.error("auto TP market close failed %s/%s: %s", symbol, trade["id"], e)
        return False

    mark = await fetch_mark_price(ex, symbol) or float(price)
    be_price = _compute_be_price(direction=direction, entry=entry, mark=mark, buffer_pct=_BE_BUFFER_PCT)
    be_price = adjust_sl_for_mark(side=direction, sl_price=be_price, mark=mark)
    pos_side = None
    if getattr(ex, "_is_hedge_mode", False):
        pos_side = "LONG" if is_long else "SHORT"
    try:
        sl_resp = await place_algo_stop(
            ex, symbol=symbol, close_side=close_side, quantity=runner_qty,
            trigger_price=be_price, order_type="STOP_MARKET",
            position_side=pos_side,
        )
    except Exception as e:
        log.error("auto TP BE SL placement failed %s/%s: %s", symbol, trade["id"], e)
        return False

    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE user_trades
            SET status='tp1_trailed', sl_current=$1, sl_order_id=$2,
                tp1_qty=$3, tp2_qty=$4, tp1_order_id=$5, tp_order_id=NULL
            WHERE id=$6 AND status='open'
            """,
            be_price, algo_id_of(sl_resp), close_qty, runner_qty,
            str(tp_resp.get("id") or tp_resp.get("orderId") or "auto_tp_market"),
            trade["id"],
        )
        await notify(conn, "trade_tp1_trailed", {
            "user_id": trade["user_id"],
            "trade_id": trade["id"],
            "auto_tp_qty": close_qty,
            "runner_qty": runner_qty,
            "protective_sl": be_price,
        })
    log.info(
        "auto TP %.0f%% + BE SL armed %s/%s direction=%s close_qty=%.6f runner=%.6f be=%.6f",
        _AUTO_TP_FRACTION * 100,
        symbol, trade["id"], direction, close_qty, runner_qty, be_price,
    )
    return True


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

        if await _timeout_close(pool, ex, symbol=symbol, trade=t):
            trailed_any = True
            continue

        if await _replace_sl_at_entry(pool, ex, symbol=symbol, trade=t, price=price):
            trailed_any = True
            continue

        if is_pine_retest_trade(t):
            async with pool.acquire() as conn:
                st_band = await _latest_supertrend_band(conn, symbol=symbol, tf=t.get("tf") or "15m")
            if st_band is None:
                log.debug("supertrend SL update skipped %s/%s: no fresh band", symbol, t["id"])
                continue
            if not should_replace_supertrend_band(
                direction=side,
                sl_current=float(t.get("sl_current") or 0.0),
                st_band=float(st_band),
            ):
                continue
            trail_price = float(st_band)
            mark = await fetch_mark_price(ex, symbol)
            if mark:
                trail_price = adjust_sl_for_mark(side=side, sl_price=trail_price, mark=mark)
            if t["sl_order_id"]:
                await cancel_algo(ex, symbol=symbol, algo_id=t["sl_order_id"])
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
                log.error("supertrend SL update failed for %s/%s: %s", symbol, t["id"], e)
                continue
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE user_trades
                    SET sl=$1, sl_current=$1, sl_order_id=$2
                    WHERE id=$3 AND status='open'
                    """,
                    trail_price, algo_id_of(new_sl), t["id"],
                )
            trailed_any = True
            log.info("supertrend SL updated %s/%s direction=%s band=%.6f trigger=%.6f", symbol, t["id"], side, st_band, trail_price)
            continue

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
            active = await get_active_symbols()
            if not active:
                await asyncio.sleep(5)
                continue
            symbol_users: dict[str, int | None] = {}
            for item in active:
                if isinstance(item, (tuple, list)) and len(item) >= 2:
                    symbol_users[str(item[0]).upper()] = int(item[1])
                else:
                    symbol_users[str(item).upper()] = None
            streams = "/".join(f"{s.lower()}@markPrice@1s" for s in symbol_users)
            url = f"wss://fstream.binance.com/stream?streams={streams}"
            log.info("connecting mark-price WS: %d symbols", len(symbol_users))
            async with websockets.connect(url, ping_interval=20) as ws:
                deadline = asyncio.get_event_loop().time() + 30.0
                async for msg in ws:
                    data = json.loads(msg).get("data", {})
                    sym = data.get("s")
                    price = float(data.get("p", 0))
                    if sym and price > 0:
                        uid = symbol_users.get(sym.upper())
                        if uid is None:
                            log.warning("mark-price WS cannot trail %s: no user id", sym)
                            continue
                        ex = await ex_factory(uid)
                        try:
                            await maybe_trail(pool, ex=ex, symbol=sym, price=price)
                        except Exception as e:
                            log.exception("maybe_trail failed: %s", e)
                        finally:
                            close = getattr(ex, "close", None)
                            if close:
                                await close()
                    if asyncio.get_event_loop().time() >= deadline:
                        break
        except Exception as e:
            log.warning("mark-price WS error: %s — retrying in 5s", e)
            await asyncio.sleep(5)
