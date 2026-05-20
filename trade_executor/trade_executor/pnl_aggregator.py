from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from trade_executor.audit import insert_audit
from trade_executor.notify import notify

log = logging.getLogger("pnl_aggregator")

DAILY_CAP_REACHED = 9_999_999_999_999


def classify_close(*, direction: str, filled_at_tp_id: str | None,
                   filled_at_sl_id: str | None, status_before: str) -> str:
    if filled_at_tp_id:
        return "closed_tp2"
    if filled_at_sl_id:
        return "closed_breakeven" if status_before == "tp1_trailed" else "closed_sl"
    return status_before


def _today_start_ms() -> int:
    now = datetime.now(timezone.utc)
    midnight = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    return int(midnight.timestamp() * 1000)


async def _fetch_positions(ex) -> dict[str, float]:
    """Return {symbol: positionAmt} for every non-zero position.

    Uses fapiPrivateV2GetPositionRisk (more reliable than fetchPositions for
    one-way mode). Symbols missing from the result map have zero position.
    """
    try:
        rows = await ex.fapiPrivateV2GetPositionRisk({})
    except Exception as e:
        log.warning("fetch positions failed: %s", e)
        return {}
    out: dict[str, float] = {}
    for p in rows:
        amt = float(p.get("positionAmt") or 0)
        if amt != 0:
            out[p["symbol"]] = amt
    return out


async def _fetch_total_usdt_balance(ex) -> float:
    """Return current futures total USDT equity, or 0 if unavailable."""
    try:
        rows = await ex.fapiPrivateV2GetBalance()
        for item in rows:
            if item.get("asset") == "USDT":
                return float(item.get("balance") or 0)
    except Exception as e:
        log.warning("fetch futures balance failed: %s", e)

    try:
        usdt = (await ex.fetch_balance()).get("USDT", {})
        return float(usdt.get("total") or usdt.get("free") or 0)
    except Exception as e:
        log.warning("fetch balance fallback failed: %s", e)
        return 0.0


def _pct_of_day_start(pnl_usdt: float, day_start_balance_usdt: float) -> float:
    if day_start_balance_usdt <= 0:
        return 0.0
    return pnl_usdt / day_start_balance_usdt * 100


def _infer_day_start_balance(current_balance_usdt: float, first_close_pnl_usdt: float) -> float:
    """Estimate day-start equity when inserting today's first realized close.

    Reconciliation runs after Binance has already applied the close PnL, so for
    the first close of the day: day_start = current equity - first realized pnl.
    Existing daily rows keep their original stored day_start_balance_usdt.
    """
    inferred = current_balance_usdt - first_close_pnl_usdt
    return inferred if inferred > 0 else current_balance_usdt


_CLOSE_PROXIMITY_PCT = 0.005  # 0.5% — close fill within this band of tp/sl = that exit


def _classify_close(*, close_px: float, status_before: str, sl: float,
                    sl_current: float, tp1: float, tp2: float, direction: str) -> str:
    """Decide closed_tp2 / closed_sl / closed_breakeven / manual_close from
    proximity of close_px to stored levels. Manual closes (e.g. user clicks
    close on Binance UI) land between the bands and get tagged accurately."""
    def near(a: float, b: float) -> bool:
        return b > 0 and abs(a - b) / b <= _CLOSE_PROXIMITY_PCT

    if near(close_px, tp2) or (direction == "long" and close_px >= tp2 > 0) \
            or (direction == "short" and 0 < close_px <= tp2):
        return "closed_tp2"
    # SL band — sl_current trumps original sl for tp1_trailed flow
    sl_check = sl_current if status_before == "tp1_trailed" and sl_current else sl
    if near(close_px, sl_check) or (direction == "long" and close_px <= sl_check) \
            or (direction == "short" and close_px >= sl_check > 0):
        return "closed_breakeven" if status_before == "tp1_trailed" else "closed_sl"
    return "manual_close"


def _is_pine_retest_trade(t: dict) -> bool:
    return str(t.get("exit_mode") or "") == "supertrend_band"


def _classify_pine_retest_close(t: dict, *, pnl_usdt: float) -> str:
    # After auto TP has closed a partial, the runner is protected at entry/BE+.
    # Classify a non-negative runner close as breakeven, not a fresh SL loss.
    if float(t.get("tp1_qty") or 0.0) > 0.0 and str(t.get("status") or "") == "tp1_trailed":
        return "closed_breakeven" if pnl_usdt >= 0 else "closed_sl"
    return "closed_sl"


async def reconcile_user(pool, *, ex, user_id: int) -> None:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, decision_id, exit_mode, symbol, direction, qty, entry, status,
                   sl, sl_current, tp1, tp2, tp1_qty,
                   sl_order_id, tp_order_id, opened_at
            FROM user_trades
            WHERE user_id=$1 AND status IN ('open','tp1_trailed')
            """,
            user_id,
        )
    if not rows:
        return

    # Position-based truth: any DB-open trade for symbol with zero Binance
    # position has been closed (by SL/TP algo, manual close, or liquidation).
    # We must reconcile via positions because algo SL/TP fills carry CHILD
    # market order IDs that don't match our stored algoId.
    positions = await _fetch_positions(ex)
    current_balance_usdt = await _fetch_total_usdt_balance(ex)

    by_symbol: dict[str, list[dict]] = {}
    for r in rows:
        by_symbol.setdefault(r["symbol"], []).append(dict(r))

    for symbol, trades in by_symbol.items():
        pos_amt = positions.get(symbol, 0.0)
        try:
            fills = await ex.fetch_my_trades(symbol, since=_today_start_ms())
        except Exception as e:
            log.warning("fetch_my_trades %s failed: %s", symbol, e)
            continue

        for t in trades:
            qty = float(t["qty"])
            entry_px = float(t["entry"])
            sign_dir = 1 if t["direction"] == "long" else -1

            # Find close fills: opposite-side fills after open ts. Algo trigger
            # creates child market order with reduceOnly=True; close_side is
            # SELL for long / BUY for short.
            close_side = "sell" if t["direction"] == "long" else "buy"
            after = int(t["opened_at"])
            close_fills = [
                f for f in fills
                if (f.get("side") or "").lower() == close_side
                and int(f.get("timestamp") or 0) >= after
            ]

            position_closed = (pos_amt == 0.0)
            if not close_fills and not position_closed:
                # Position still alive on Binance, no close fills — leave alone.
                continue

            if not close_fills:
                # Position is zero but no matching close fills found in window —
                # rare edge case (manual close before today, or fills outside
                # since=today). Skip safely; log for visibility.
                log.warning(
                    "reconcile %s: position=0 but no close fills found in window — skipping",
                    t["id"],
                )
                continue

            # Sum close fills until they cover qty (handles partial fills).
            cum_qty = 0.0
            cum_notional = 0.0
            cum_fee = 0.0
            close_ts = 0
            for f in close_fills:
                fq = float(f.get("amount") or 0)
                fp = float(f.get("price") or 0)
                cum_notional += fq * fp
                cum_qty += fq
                fee_cost = float((f.get("fee") or {}).get("cost") or 0)
                cum_fee += fee_cost
                close_ts = int(f.get("timestamp") or close_ts)
                if cum_qty >= qty - 1e-9:
                    break
            if cum_qty <= 0:
                continue
            # Partial close (e.g. TP1 fill of tiered TP): position still alive
            # on Binance. Skip reconcile so we do not mark the trade closed
            # prematurely. trail_manager will arm protective SL on next tick.
            if pos_amt != 0.0 and cum_qty < qty - 1e-9:
                log.info(
                    "reconcile %s: partial close detected (cum=%.6f / qty=%.6f) — leaving open",
                    t["id"], cum_qty, qty,
                )
                continue
            close_px = cum_notional / cum_qty
            gross = sign_dir * (close_px - entry_px) * qty
            pnl_usdt = gross - cum_fee
            pnl_pct = (pnl_usdt / (qty * entry_px)) * 100 if entry_px else 0.0

            if _is_pine_retest_trade(t):
                new_status = _classify_pine_retest_close(t, pnl_usdt=pnl_usdt)
            else:
                new_status = _classify_close(
                    close_px=close_px,
                    status_before=t["status"],
                    sl=float(t["sl"] or 0),
                    sl_current=float(t["sl_current"] or 0),
                    tp1=float(t["tp1"] or 0),
                    tp2=float(t["tp2"] or 0),
                    direction=t["direction"],
                )

            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE user_trades
                    SET status=$1, pnl_usdt=$2, pnl_pct=$3, fees_usdt=$4, closed_at=$5
                    WHERE id=$6 AND status IN ('open','tp1_trailed')
                    """,
                    new_status, pnl_usdt, pnl_pct, cum_fee,
                    close_ts or int(time.time() * 1000),
                    t["id"],
                )
                await _upsert_daily(
                    conn,
                    user_id,
                    pnl_usdt,
                    won=(pnl_usdt > 0),
                    current_balance_usdt=current_balance_usdt,
                )
                await notify(conn, "trade_closed",
                             {"user_id": user_id, "trade_id": t["id"], "status": new_status,
                              "pnl_usdt": pnl_usdt, "pnl_pct": pnl_pct})

    async with pool.acquire() as conn:
        cap_row = await conn.fetchrow(
            """
            SELECT u.daily_loss_cap_pct, COALESCE(d.realized_pnl_pct, 0) AS pct
            FROM users u
            LEFT JOIN user_daily_pnl d ON d.user_id=u.id AND d.day=CURRENT_DATE
            WHERE u.id=$1
            """,
            user_id,
        )
        if cap_row and cap_row["pct"] <= -float(cap_row["daily_loss_cap_pct"]):
            await conn.execute(
                "UPDATE users SET paused_until=$1, pause_reason='daily_cap', updated_at=$2 WHERE id=$3",
                DAILY_CAP_REACHED, int(time.time() * 1000), user_id,
            )
            await insert_audit(conn, user_id, "paused", {"reason": "daily_cap"})
            await notify(conn, "daily_summary", {"user_id": user_id, "paused": True})


async def _upsert_daily(
    conn,
    user_id: int,
    pnl_usdt: float,
    *,
    won: bool,
    current_balance_usdt: float,
) -> None:
    day_start_balance = _infer_day_start_balance(current_balance_usdt, pnl_usdt)
    await conn.execute(
        """
        INSERT INTO user_daily_pnl (user_id, day, realized_pnl_usdt, realized_pnl_pct,
                                    trades_count, wins_count, day_start_balance_usdt)
        VALUES ($1, CURRENT_DATE, $2, $3, 1, $4, $5)
        ON CONFLICT (user_id, day) DO UPDATE SET
          realized_pnl_usdt = user_daily_pnl.realized_pnl_usdt + EXCLUDED.realized_pnl_usdt,
          realized_pnl_pct  = (user_daily_pnl.realized_pnl_usdt + EXCLUDED.realized_pnl_usdt)
                              / NULLIF(user_daily_pnl.day_start_balance_usdt, 0) * 100,
          trades_count      = user_daily_pnl.trades_count + 1,
          wins_count        = user_daily_pnl.wins_count + EXCLUDED.wins_count
        """,
        user_id,
        pnl_usdt,
        _pct_of_day_start(pnl_usdt, day_start_balance),
        1 if won else 0,
        day_start_balance,
    )
