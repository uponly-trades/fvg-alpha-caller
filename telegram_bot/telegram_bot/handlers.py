from __future__ import annotations

import logging

from aiogram import Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from telegram_bot.executor_client import ExecutorClientError, account_summary, set_keys
from telegram_bot.queries import (
    list_trades,
    set_enabled,
    stats,
    update_setting,
    upsert_user,
    user_row,
)


async def _dashboard_text(pool, telegram_id: int) -> str:
    """Fetch summary + stats and render rich dashboard. Fallback to fmt_help on error."""
    try:
        summary = await account_summary(telegram_id)
    except ExecutorClientError:
        summary = {}
    async with pool.acquire() as conn:
        s = await stats(conn, telegram_id=telegram_id)
    return fmt_dashboard(summary, s)
from telegram_bot.templates import (
    fmt_balance,
    fmt_dashboard,
    fmt_help,
    fmt_key_saved,
    fmt_settings,
    fmt_stats,
    fmt_trade_list,
)

log = logging.getLogger("handlers")


class KeySetup(StatesGroup):
    waiting_for_keys = State()


class NumericSetup(StatesGroup):
    waiting_for_value = State()


# ── keyboards ────────────────────────────────────────────────────────────────

def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💰 Balance", callback_data="balance"),
            InlineKeyboardButton(text="📊 Stats", callback_data="stats"),
        ],
        [
            InlineKeyboardButton(text="📗 Active Trades", callback_data="trades"),
            InlineKeyboardButton(text="📕 Closed Trades", callback_data="closed"),
        ],
        [
            InlineKeyboardButton(text="⚙️ Settings", callback_data="settings"),
            InlineKeyboardButton(text="🔑 Set API Keys", callback_data="setkeys"),
        ],
        [
            InlineKeyboardButton(text="⏸ Pause", callback_data="pause"),
            InlineKeyboardButton(text="▶️ Resume", callback_data="resume"),
        ],
        [
            InlineKeyboardButton(text="📉 Set Risk %", callback_data="setrisk"),
            InlineKeyboardButton(text="⚡ Set Leverage", callback_data="setlev"),
        ],
        [
            InlineKeyboardButton(text="🔢 Max Trades", callback_data="setmax"),
            InlineKeyboardButton(text="🛑 Daily Loss Cap", callback_data="setloss"),
        ],
    ])


def back_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="« Back", callback_data="menu")]
    ])


# ── helpers ───────────────────────────────────────────────────────────────────

def _sender(m: Message) -> tuple[int, str | None, str | None]:
    return m.from_user.id, m.from_user.username, m.from_user.first_name


async def _ensure_user(pool, tid: int, username: str | None, first_name: str | None) -> None:
    async with pool.acquire() as conn:
        await upsert_user(conn, telegram_id=tid, username=username, first_name=first_name)


# ── register ──────────────────────────────────────────────────────────────────

def register_handlers(dp: Dispatcher, pool) -> None:

    # ── /start + menu callback ────────────────────────────────────────────────

    @dp.message(Command("start"))
    async def on_start(m: Message):
        await _ensure_user(pool, m.from_user.id, m.from_user.username, m.from_user.first_name)
        text = await _dashboard_text(pool, m.from_user.id)
        await m.answer(text, reply_markup=main_menu(), parse_mode="HTML")

    @dp.callback_query(F.data == "menu")
    async def cb_menu(cb: CallbackQuery):
        await cb.answer("Refreshing…")
        await _ensure_user(pool, cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
        text = await _dashboard_text(pool, cb.from_user.id)
        await cb.message.edit_text(text, reply_markup=main_menu(), parse_mode="HTML")

    # ── balance ───────────────────────────────────────────────────────────────

    @dp.message(Command("balance"))
    async def on_balance_cmd(m: Message):
        await _do_balance(m.from_user.id, m.from_user.username, m.from_user.first_name, reply=m)

    @dp.callback_query(F.data == "balance")
    async def cb_balance(cb: CallbackQuery):
        await cb.answer("Fetching balance…")
        await _ensure_user(pool, cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
        try:
            summary = await account_summary(cb.from_user.id)
        except ExecutorClientError as e:
            await cb.message.edit_text(f"❌ Balance unavailable: {e}", reply_markup=back_button())
            return
        await cb.message.edit_text(fmt_balance(summary), reply_markup=back_button(), parse_mode="HTML")

    async def _do_balance(tid, username, first_name, *, reply: Message):
        await _ensure_user(pool, tid, username, first_name)
        try:
            summary = await account_summary(tid)
        except ExecutorClientError as e:
            await reply.answer(f"❌ Balance unavailable: {e}")
            return
        await reply.answer(fmt_balance(summary), parse_mode="HTML")

    # ── trades ────────────────────────────────────────────────────────────────

    @dp.message(Command("trades"))
    async def on_trades_cmd(m: Message):
        await _ensure_user(pool, m.from_user.id, m.from_user.username, m.from_user.first_name)
        async with pool.acquire() as conn:
            rows = await list_trades(conn, telegram_id=m.from_user.id, closed=False)
        await m.answer(fmt_trade_list(rows, closed=False), parse_mode="HTML")

    @dp.callback_query(F.data == "trades")
    async def cb_trades(cb: CallbackQuery):
        await cb.answer()
        await _ensure_user(pool, cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
        async with pool.acquire() as conn:
            rows = await list_trades(conn, telegram_id=cb.from_user.id, closed=False)
        await cb.message.edit_text(fmt_trade_list(rows, closed=False), reply_markup=back_button(), parse_mode="HTML")

    @dp.message(Command("closed"))
    async def on_closed_cmd(m: Message):
        await _ensure_user(pool, m.from_user.id, m.from_user.username, m.from_user.first_name)
        async with pool.acquire() as conn:
            rows = await list_trades(conn, telegram_id=m.from_user.id, closed=True)
        await m.answer(fmt_trade_list(rows, closed=True), parse_mode="HTML")

    @dp.callback_query(F.data == "closed")
    async def cb_closed(cb: CallbackQuery):
        await cb.answer()
        await _ensure_user(pool, cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
        async with pool.acquire() as conn:
            rows = await list_trades(conn, telegram_id=cb.from_user.id, closed=True)
        await cb.message.edit_text(fmt_trade_list(rows, closed=True), reply_markup=back_button(), parse_mode="HTML")

    # ── stats ─────────────────────────────────────────────────────────────────

    @dp.message(Command("stats"))
    async def on_stats_cmd(m: Message):
        await _ensure_user(pool, m.from_user.id, m.from_user.username, m.from_user.first_name)
        async with pool.acquire() as conn:
            row = await stats(conn, telegram_id=m.from_user.id)
        await m.answer(fmt_stats(row), parse_mode="HTML")

    @dp.callback_query(F.data == "stats")
    async def cb_stats(cb: CallbackQuery):
        await cb.answer()
        await _ensure_user(pool, cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
        async with pool.acquire() as conn:
            row = await stats(conn, telegram_id=cb.from_user.id)
        await cb.message.edit_text(fmt_stats(row), reply_markup=back_button(), parse_mode="HTML")

    # ── settings ──────────────────────────────────────────────────────────────

    @dp.message(Command("settings"))
    async def on_settings_cmd(m: Message):
        await _ensure_user(pool, m.from_user.id, m.from_user.username, m.from_user.first_name)
        async with pool.acquire() as conn:
            row = await user_row(conn, telegram_id=m.from_user.id)
        await m.answer(fmt_settings(row), parse_mode="HTML")

    @dp.callback_query(F.data == "settings")
    async def cb_settings(cb: CallbackQuery):
        await cb.answer()
        await _ensure_user(pool, cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
        async with pool.acquire() as conn:
            row = await user_row(conn, telegram_id=cb.from_user.id)
        await cb.message.edit_text(fmt_settings(row), reply_markup=back_button(), parse_mode="HTML")

    # ── pause / resume ────────────────────────────────────────────────────────

    @dp.message(Command("pause"))
    async def on_pause_cmd(m: Message):
        await _ensure_user(pool, m.from_user.id, m.from_user.username, m.from_user.first_name)
        async with pool.acquire() as conn:
            await set_enabled(conn, telegram_id=m.from_user.id, enabled=False)
        await m.answer("⏸ Paused. No new live trades.")

    @dp.callback_query(F.data == "pause")
    async def cb_pause(cb: CallbackQuery):
        await cb.answer("Paused ⏸")
        await _ensure_user(pool, cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
        async with pool.acquire() as conn:
            await set_enabled(conn, telegram_id=cb.from_user.id, enabled=False)
        await cb.message.edit_text("⏸ Paused. No new live trades.", reply_markup=back_button())

    @dp.message(Command("resume"))
    async def on_resume_cmd(m: Message):
        await _ensure_user(pool, m.from_user.id, m.from_user.username, m.from_user.first_name)
        async with pool.acquire() as conn:
            await set_enabled(conn, telegram_id=m.from_user.id, enabled=True)
        await m.answer("✅ Live trading enabled.")

    @dp.callback_query(F.data == "resume")
    async def cb_resume(cb: CallbackQuery):
        await cb.answer("Resumed ▶️")
        await _ensure_user(pool, cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
        async with pool.acquire() as conn:
            await set_enabled(conn, telegram_id=cb.from_user.id, enabled=True)
        await cb.message.edit_text("✅ Live trading enabled.", reply_markup=back_button())

    # ── set API keys ──────────────────────────────────────────────────────────

    @dp.message(Command("setkeys"))
    async def on_setkeys_cmd(m: Message, state: FSMContext):
        await _ensure_user(pool, m.from_user.id, m.from_user.username, m.from_user.first_name)
        await state.set_state(KeySetup.waiting_for_keys)
        await m.answer(
            "🔑 Send Binance API key + secret — 2 lines:\n"
            "<code>API_KEY\nAPI_SECRET</code>\n\n"
            "Permissions needed: Futures Trading + Read. Never enable Withdraw.",
            parse_mode="HTML",
        )

    @dp.callback_query(F.data == "setkeys")
    async def cb_setkeys(cb: CallbackQuery, state: FSMContext):
        await cb.answer()
        await _ensure_user(pool, cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
        await state.set_state(KeySetup.waiting_for_keys)
        await cb.message.edit_text(
            "🔑 Send Binance API key + secret — 2 lines:\n"
            "<code>API_KEY\nAPI_SECRET</code>\n\n"
            "Permissions needed: Futures Trading + Read. Never enable Withdraw.",
            parse_mode="HTML",
            reply_markup=back_button(),
        )

    @dp.message(KeySetup.waiting_for_keys)
    async def on_keys_message(m: Message, state: FSMContext):
        lines = [x.strip() for x in (m.text or "").splitlines() if x.strip()]
        if len(lines) != 2:
            await m.answer("❌ Send exactly 2 lines: API key, then API secret.")
            return
        api_key, api_secret = lines
        try:
            result = await set_keys(m.from_user.id, api_key, api_secret)
        except ExecutorClientError as e:
            log.warning("setkeys failed telegram_id=%s: %s", m.from_user.id, e)
            await m.answer(f"❌ Binance key check failed: {e}")
            return
        finally:
            try:
                await m.delete()
            except Exception:
                pass
        await state.clear()
        tail = str(result.get("api_key_tail") or api_key[-4:])
        await m.answer(fmt_key_saved(tail), parse_mode="HTML")
        text = await _dashboard_text(pool, m.from_user.id)
        await m.answer(text, reply_markup=main_menu(), parse_mode="HTML")

    # ── numeric settings (FSM) ────────────────────────────────────────────────

    _NUMERIC_CONFIG = {
        "setrisk":  dict(field="risk_pct",           min_v=0.1,  max_v=10,  integer=False, label="Risk %",          hint="0.1–10"),
        "setlev":   dict(field="leverage",            min_v=5,    max_v=20,  integer=True,  label="Leverage",        hint="5–20"),
        "setmax":   dict(field="max_concurrent",      min_v=1,    max_v=10,  integer=True,  label="Max trades",      hint="1–10"),
        "setloss":  dict(field="daily_loss_cap_pct",  min_v=1,    max_v=50,  integer=False, label="Daily loss cap %", hint="1–50"),
    }

    async def _ask_numeric(target, state: FSMContext, key: str):
        cfg = _NUMERIC_CONFIG[key]
        await state.set_state(NumericSetup.waiting_for_value)
        await state.update_data(numeric_key=key)
        text = f"Enter {cfg['label']} ({cfg['hint']}):"
        if isinstance(target, CallbackQuery):
            await target.answer()
            await target.message.edit_text(text, reply_markup=back_button())
        else:
            await target.answer(text)

    @dp.message(Command("setrisk"))
    async def on_setrisk_cmd(m: Message, state: FSMContext): await _ask_numeric(m, state, "setrisk")

    @dp.message(Command("setlev"))
    async def on_setlev_cmd(m: Message, state: FSMContext): await _ask_numeric(m, state, "setlev")

    @dp.message(Command("setmax"))
    async def on_setmax_cmd(m: Message, state: FSMContext): await _ask_numeric(m, state, "setmax")

    @dp.message(Command("setloss"))
    async def on_setloss_cmd(m: Message, state: FSMContext): await _ask_numeric(m, state, "setloss")

    @dp.callback_query(F.data.in_({"setrisk", "setlev", "setmax", "setloss"}))
    async def cb_numeric(cb: CallbackQuery, state: FSMContext):
        await _ask_numeric(cb, state, cb.data)

    @dp.message(NumericSetup.waiting_for_value)
    async def on_numeric_value(m: Message, state: FSMContext):
        data = await state.get_data()
        key = data.get("numeric_key", "setrisk")
        cfg = _NUMERIC_CONFIG[key]
        try:
            raw = float((m.text or "").strip())
            if raw < cfg["min_v"] or raw > cfg["max_v"]:
                raise ValueError(f"must be {cfg['hint']}")
            value = int(raw) if cfg["integer"] else raw
        except (ValueError, TypeError) as e:
            await m.answer(f"❌ Invalid: {e}")
            return
        await state.clear()
        await _ensure_user(pool, m.from_user.id, m.from_user.username, m.from_user.first_name)
        async with pool.acquire() as conn:
            await update_setting(conn, telegram_id=m.from_user.id, field=cfg["field"], value=value)
        suffix = "x" if cfg["field"] == "leverage" else "%"
        await m.answer(f"✅ {cfg['label']} set to {value}{suffix}", reply_markup=main_menu())
