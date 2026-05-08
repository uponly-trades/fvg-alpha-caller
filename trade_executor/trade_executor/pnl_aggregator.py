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


async def reconcile_user(pool, *, ex, user_id: int) -> None:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, symbol, direction, qty, entry, status,
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
            close_px = cum_notional / cum_qty
            gross = sign_dir * (close_px - entry_px) * qty
            pnl_usdt = gross - cum_fee
            pnl_pct = (pnl_usdt / (qty * entry_px)) * 100 if entry_px else 0.0

            # Decide TP vs SL by direction: long closed below entry = SL,
            # above = TP. Mirror for short. tp1_trailed → SL hit = breakeven.
            if t["direction"] == "long":
                hit_tp = close_px > entry_px
            else:
                hit_tp = close_px < entry_px
            if hit_tp:
                new_status = "closed_tp2"
            else:
                new_status = "closed_breakeven" if t["status"] == "tp1_trailed" else "closed_sl"

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
                await _upsert_daily(conn, user_id, pnl_usdt, won=(pnl_usdt > 0))
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


async def _upsert_daily(conn, user_id: int, pnl_usdt: float, *, won: bool) -> None:
    await conn.execute(
        """
        INSERT INTO user_daily_pnl (user_id, day, realized_pnl_usdt, realized_pnl_pct,
                                    trades_count, wins_count, day_start_balance_usdt)
        VALUES ($1, CURRENT_DATE, $2, $3, 1, $4, 100.0)
        ON CONFLICT (user_id, day) DO UPDATE SET
          realized_pnl_usdt = user_daily_pnl.realized_pnl_usdt + EXCLUDED.realized_pnl_usdt,
          realized_pnl_pct  = (user_daily_pnl.realized_pnl_usdt + EXCLUDED.realized_pnl_usdt)
                              / COALESCE(user_daily_pnl.day_start_balance_usdt, 100.0) * 100,
          trades_count      = user_daily_pnl.trades_count + 1,
          wins_count        = user_daily_pnl.wins_count + EXCLUDED.wins_count
        """,
        user_id, pnl_usdt,
        (pnl_usdt / 100.0) * 100,
        1 if won else 0,
    )
