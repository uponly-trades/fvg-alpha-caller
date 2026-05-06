from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from trade_executor.audit import insert_audit
from trade_executor.gate import check_user_gate
from trade_executor.notify import notify
from trade_executor.order_placer import OrderError, place_full_sequence
from trade_executor.sizing import SymbolMeta, compute_size

log = logging.getLogger("orchestrator")


@dataclass
class OrchResult:
    placed: bool
    skip_reason: str | None = None


async def _today_pnl_pct(conn, user_id: int) -> float:
    row = await conn.fetchrow(
        "SELECT realized_pnl_pct FROM user_daily_pnl WHERE user_id=$1 AND day=CURRENT_DATE",
        user_id,
    )
    return float(row["realized_pnl_pct"]) if row else 0.0


async def _open_count(conn, user_id: int) -> int:
    return int(await conn.fetchval(
        "SELECT COUNT(*) FROM user_trades WHERE user_id=$1 AND status IN ('opening','open','tp1_trailed')",
        user_id,
    ))


async def _existing(conn, user_id: int, decision_id: str) -> bool:
    return bool(await conn.fetchval(
        "SELECT 1 FROM user_trades WHERE user_id=$1 AND decision_id=$2",
        user_id, decision_id,
    ))


async def _symbol_meta(ex, symbol: str) -> SymbolMeta:
    info = await ex.fapiPublic_get_exchangeinfo()
    for s in info["symbols"]:
        if s["symbol"] == symbol:
            step = 0.001
            min_n = 5.0
            for f in s.get("filters", []):
                if f["filterType"] == "LOT_SIZE":
                    step = float(f["stepSize"])
                elif f["filterType"] in ("MIN_NOTIONAL", "NOTIONAL"):
                    min_n = float(f.get("notional") or f.get("minNotional") or 5.0)
            return SymbolMeta(step_size=step, min_notional=min_n)
    return SymbolMeta(step_size=0.001, min_notional=5.0)


async def handle_signal_for_user(
    pool,
    *,
    user_id: int,
    signal: dict,
    ex,
    risk_pct: float,
    leverage: int,
    max_concurrent: int,
    daily_loss_cap_pct: float,
) -> OrchResult:
    decision_id = signal["id"]
    symbol = signal["symbol"]
    direction = signal["direction"]
    entry = float(signal["entry"])
    sl = float(signal["sl"])
    tp1 = float(signal["tp1"])
    tp2 = float(signal["tp2"])

    async with pool.acquire() as conn:
        balance = (await ex.fetch_balance()).get("USDT", {}).get("free", 0)
        gate = check_user_gate(
            user={
                "id": user_id, "enabled": True, "paused_until": None,
                "risk_pct": risk_pct, "leverage": leverage,
                "max_concurrent": max_concurrent, "daily_loss_cap_pct": daily_loss_cap_pct,
            },
            open_count=await _open_count(conn, user_id),
            today_pnl_pct=await _today_pnl_pct(conn, user_id),
            balance_usdt=float(balance),
            decision_id=decision_id,
            existing_trade=await _existing(conn, user_id, decision_id),
        )
        if gate.skip_reason:
            await insert_audit(conn, user_id, "trade_skipped",
                               {"decision_id": decision_id, "reason": gate.skip_reason})
            if gate.should_pause_forever:
                await conn.execute(
                    "UPDATE users SET paused_until=$1, pause_reason='daily_cap', updated_at=$2 WHERE id=$3",
                    9_999_999_999_999, int(time.time() * 1000), user_id,
                )
            return OrchResult(placed=False, skip_reason=gate.skip_reason)

        meta = await _symbol_meta(ex, symbol)
        size = compute_size(
            balance=float(balance), risk_pct=risk_pct, entry=entry, sl=sl,
            leverage=leverage, meta=meta,
        )
        if size.skip_reason:
            await insert_audit(conn, user_id, "trade_skipped",
                               {"decision_id": decision_id, "reason": size.skip_reason})
            return OrchResult(placed=False, skip_reason=size.skip_reason)

        trade_id = f"{user_id}-{decision_id}"
        now = int(time.time() * 1000)
        try:
            await conn.execute(
                """
                INSERT INTO user_trades (id, user_id, decision_id, symbol, tf, direction,
                  leverage, margin_usdt, notional_usdt, qty, entry, sl, sl_current, tp1, tp2,
                  status, opened_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,'opening',$16)
                """,
                trade_id, user_id, decision_id, symbol, signal["tf"], direction,
                leverage, size.margin_usdt, size.notional_usdt, size.qty,
                entry, sl, sl, tp1, tp2, now,
            )
        except Exception as e:
            log.info("user_trade insert collision for %s: %s", trade_id, e)
            return OrchResult(placed=False, skip_reason="duplicate")

    side = "BUY" if direction == "long" else "SELL"
    try:
        placed = await place_full_sequence(
            ex, symbol=symbol, side=side, qty=size.qty,
            sl_price=sl, tp_price=tp2, leverage=leverage,
        )
    except OrderError as e:
        async with pool.acquire() as conn:
            status = {"sl": "error_no_sl", "entry": "error_open"}.get(e.stage, "error_open")
            await conn.execute(
                "UPDATE user_trades SET status=$1, error_msg=$2, closed_at=$3 WHERE id=$4",
                status, str(e), int(time.time() * 1000), trade_id,
            )
            await notify(conn, "error", {"user_id": user_id, "trade_id": trade_id, "stage": e.stage})
        return OrchResult(placed=False, skip_reason=f"order_{e.stage}")

    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE user_trades SET
              status='open',
              entry=$1, sl_current=$2,
              entry_order_id=$3, sl_order_id=$4, tp_order_id=$5
            WHERE id=$6
            """,
            placed.avg_price, sl,
            placed.entry_order_id, placed.sl_order_id, placed.tp_order_id,
            trade_id,
        )
        await notify(conn, "trade_opened", {"user_id": user_id, "trade_id": trade_id})
        await insert_audit(conn, user_id, "trade_opened",
                           {"decision_id": decision_id, "symbol": symbol})

    return OrchResult(placed=True)
