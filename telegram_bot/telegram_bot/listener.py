from __future__ import annotations

import json
import logging

from telegram_bot.templates import (
    fmt_breakeven, fmt_error, fmt_manual_close, fmt_opened, fmt_sl, fmt_tp2,
    fmt_trade_skipped,
)

log = logging.getLogger("listener")

CHANNELS = ("trade_opened", "trade_closed", "trade_skipped", "error")


async def _user_chat(pool, user_id: int) -> int | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT telegram_id FROM users WHERE id=$1", user_id)
    return int(row["telegram_id"]) if row else None


async def _trade_row(pool, trade_id: str) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT user_id, symbol, tf, direction, entry, sl, tp1, tp2,
                      qty, leverage, notional_usdt, margin_usdt, sl_current,
                      pnl_usdt, pnl_pct, status
               FROM user_trades WHERE id=$1""",
            trade_id,
        )
    return dict(row) if row else None


async def handle_payload(pool, bot, channel: str, payload: dict) -> None:
    if channel == "trade_opened":
        t = await _trade_row(pool, payload["trade_id"])
        if not t: return
        chat = await _user_chat(pool, t["user_id"])
        if not chat: return
        await bot.send_message(chat, fmt_opened(
            symbol=t["symbol"], tf=t["tf"], direction=t["direction"],
            entry=float(t["entry"]), sl=float(t["sl"]),
            tp1=float(t["tp1"]), tp2=float(t["tp2"]),
            qty=float(t["qty"]), leverage=int(t["leverage"]),
            notional=float(t["notional_usdt"]), margin=float(t["margin_usdt"]),
        ))
    elif channel == "trade_closed":
        t = await _trade_row(pool, payload["trade_id"])
        if not t: return
        chat = await _user_chat(pool, t["user_id"])
        if not chat: return
        pnl_usdt = float(t["pnl_usdt"] or 0)
        pnl_pct = float(t["pnl_pct"] or 0)
        setup = dict(
            tf=t["tf"], direction=t["direction"],
            entry=float(t["entry"]), sl=float(t["sl"]),
            tp1=float(t["tp1"]), tp2=float(t["tp2"]),
            qty=float(t["qty"]), leverage=int(t["leverage"]),
            notional=float(t["notional_usdt"]),
        )
        if t["status"] == "closed_tp2":
            msg = fmt_tp2(symbol=t["symbol"], pnl_usdt=pnl_usdt, pnl_pct=pnl_pct, **setup)
        elif t["status"] == "closed_breakeven":
            msg = fmt_breakeven(symbol=t["symbol"], pnl_usdt=pnl_usdt, **setup)
        elif t["status"] == "manual_close":
            msg = fmt_manual_close(symbol=t["symbol"], pnl_usdt=pnl_usdt, pnl_pct=pnl_pct, **setup)
        else:
            msg = fmt_sl(symbol=t["symbol"], pnl_usdt=pnl_usdt, pnl_pct=pnl_pct, **setup)
        await bot.send_message(chat, msg, parse_mode="HTML")
    elif channel == "trade_skipped":
        chat = await _user_chat(pool, payload["user_id"])
        if not chat: return
        await bot.send_message(chat, fmt_trade_skipped(
            symbol=payload.get("symbol", ""),
            reason=payload.get("reason", "unknown"),
            decision_id=payload.get("decision_id", ""),
        ), parse_mode="HTML")
    elif channel == "error":
        chat = await _user_chat(pool, payload["user_id"])
        if not chat: return
        await bot.send_message(chat, fmt_error(symbol=payload.get("symbol", ""),
                                               reason=payload.get("stage", "unknown")))


async def run_listener(pool, bot):
    conn = await pool.acquire()
    try:
        async def cb(c, pid, channel, raw):
            try:
                payload = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                payload = {}
            try:
                await handle_payload(pool, bot, channel, payload)
            except Exception as e:
                log.exception("dispatch failed channel=%s: %s", channel, e)

        for ch in CHANNELS:
            await conn.add_listener(ch, cb)
        log.info("listening on %s", CHANNELS)
        import asyncio
        while True:
            await asyncio.sleep(60)
    finally:
        await pool.release(conn)
