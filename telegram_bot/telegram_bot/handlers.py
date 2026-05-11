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


# ── hybrid UX helpers ─────────────────────────────────────────────────────────

async def _edit_or_reply(
    bot,
    *,
    chat_id: int,
    prompt_msg_id: int | None,
    text: str,
    reply_markup=None,
    parse_mode: str | None = None,
) -> None:
    """Edit the stored prompt message in place; on any failure send a new one.

    Why: keeps multi-step flows in a single bubble so the chat doesn't fill
    with stale prompts and orphaned confirmations.
    """
    if prompt_msg_id is not None:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=prompt_msg_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
            return
        except Exception as e:
            log.debug("edit_message_text fallback (chat=%s msg=%s): %s", chat_id, prompt_msg_id, e)
    await bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=reply_markup,
        parse_mode=parse_mode,
    )


async def _safe_delete(bot, *, chat_id: int, message_id: int) -> None:
    """Delete a message; ignore failures (already gone, too old, no perms)."""
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        log.debug("delete_message ignored (chat=%s msg=%s): %s", chat_id, message_id, e)


# ── keyboards ────────────────────────────────────────────────────────────────

def main_menu(enabled: bool = True) -> InlineKeyboardMarkup:
    toggle_label = "⏸ Pause Bot" if enabled else "▶️ Resume Bot"
    toggle_data = "pause" if enabled else "resume"
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
            InlineKeyboardButton(text=toggle_label, callback_data=toggle_data),
        ],
        [
            InlineKeyboardButton(text="📉 Risk % Equity", callback_data="setrisk"),
            InlineKeyboardButton(text="⚡ Set Leverage", callback_data="setlev"),
        ],
        [
            InlineKeyboardButton(text="🔢 Max Trades", callback_data="setmax"),
            InlineKeyboardButton(text="🛑 Daily Loss Cap", callback_data="setloss"),
        ],
        [
            InlineKeyboardButton(text="🎯 Set RR", callback_data="setrr"),
            InlineKeyboardButton(text="🧱 Margin Mode", callback_data="setmargin"),
        ],
        [
            InlineKeyboardButton(text="💵 Risk Mode", callback_data="setriskmode"),
            InlineKeyboardButton(text="🛡 SL Settings", callback_data="setslmenu"),
        ],
    ])


def back_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="« Back", callback_data="menu")]
    ])

async def _menu_for(pool, telegram_id: int) -> InlineKeyboardMarkup:
    """Return a main_menu reflecting the user's current enabled state."""
    try:
        async with pool.acquire() as conn:
            row = await user_row(conn, telegram_id=telegram_id)
        enabled = bool(row["enabled"]) if row else True
    except Exception:
        enabled = True
    return main_menu(enabled=enabled)


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
        await m.answer(text, reply_markup=await _menu_for(pool, m.from_user.id), parse_mode="HTML")

    @dp.callback_query(F.data == "menu")
    async def cb_menu(cb: CallbackQuery):
        await cb.answer("Refreshing…")
        await _ensure_user(pool, cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
        text = await _dashboard_text(pool, cb.from_user.id)
        await cb.message.edit_text(text, reply_markup=await _menu_for(pool, cb.from_user.id), parse_mode="HTML")

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
        await m.answer("⏸ Paused. No new live trades.", reply_markup=await _menu_for(pool, m.from_user.id))

    @dp.callback_query(F.data == "pause")
    async def cb_pause(cb: CallbackQuery):
        await cb.answer("Paused ⏸")
        await _ensure_user(pool, cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
        async with pool.acquire() as conn:
            await set_enabled(conn, telegram_id=cb.from_user.id, enabled=False)
        await cb.message.edit_text(
            "⏸ Paused. No new live trades.",
            reply_markup=await _menu_for(pool, cb.from_user.id),
        )

    @dp.message(Command("resume"))
    async def on_resume_cmd(m: Message):
        await _ensure_user(pool, m.from_user.id, m.from_user.username, m.from_user.first_name)
        async with pool.acquire() as conn:
            await set_enabled(conn, telegram_id=m.from_user.id, enabled=True)
        await m.answer("✅ Live trading enabled.", reply_markup=await _menu_for(pool, m.from_user.id))

    @dp.callback_query(F.data == "resume")
    async def cb_resume(cb: CallbackQuery):
        await cb.answer("Resumed ▶️")
        await _ensure_user(pool, cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
        async with pool.acquire() as conn:
            await set_enabled(conn, telegram_id=cb.from_user.id, enabled=True)
        await cb.message.edit_text(
            "✅ Live trading enabled.",
            reply_markup=await _menu_for(pool, cb.from_user.id),
        )

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
        await state.update_data(prompt_msg_id=cb.message.message_id)

    @dp.message(KeySetup.waiting_for_keys)
    async def on_keys_message(m: Message, state: FSMContext):
        data = await state.get_data()
        prompt_msg_id = data.get("prompt_msg_id")
        # Always wipe the user's own message — secrets in chat history.
        await _safe_delete(m.bot, chat_id=m.chat.id, message_id=m.message_id)

        lines = [x.strip() for x in (m.text or "").splitlines() if x.strip()]
        if len(lines) != 2:
            await _edit_or_reply(
                m.bot, chat_id=m.chat.id, prompt_msg_id=prompt_msg_id,
                text="❌ Send exactly 2 lines: API key, then API secret.",
                reply_markup=back_button(),
            )
            return

        api_key, api_secret = lines
        try:
            result = await set_keys(m.from_user.id, api_key, api_secret)
        except ExecutorClientError as e:
            log.warning("setkeys failed telegram_id=%s: %s", m.from_user.id, e)
            await _edit_or_reply(
                m.bot, chat_id=m.chat.id, prompt_msg_id=prompt_msg_id,
                text=f"❌ Binance key check failed: {e}",
                reply_markup=back_button(),
            )
            return

        await state.clear()
        tail = str(result.get("api_key_tail") or api_key[-4:])
        text = await _dashboard_text(pool, m.from_user.id)
        await _edit_or_reply(
            m.bot, chat_id=m.chat.id, prompt_msg_id=prompt_msg_id,
            text=f"{fmt_key_saved(tail)}\n\n{text}",
            reply_markup=await _menu_for(pool, m.from_user.id), parse_mode="HTML",
        )

    # ── numeric settings (FSM) ────────────────────────────────────────────────

    _NUMERIC_CONFIG = {
        "setrisk":  dict(
            field="risk_pct", min_v=0.1, max_v=20, integer=False,
            label="Risk % equity", hint="0.1–20%", suffix="%",
            howto=(
                "📉 <b>Risk % Equity</b> — target win/loss per trade dari total equity Binance.\n"
                "Contoh: equity $100 dan risk 3% berarti SL ≈ -$3, TP 1:1 ≈ +$3.\n"
                "Bot otomatis hitung qty dari jarak Entry → SL; notional/margin tidak perlu dikira-kira."
            ),
        ),
        "setlev":   dict(
            field="leverage", min_v=10, max_v=15, integer=True,
            label="Leverage", hint="10–15", suffix="x",
            howto=(
                "⚡ <b>Leverage</b> — multiplier margin di Binance Futures.\n"
                "Bot pakai margin mode yang kamu pilih: <b>ISOLATED</b> atau <b>CROSS</b>.\n"
                "Tidak mengubah risk per trade (risk % yang nentuin loss). "
                "Lev tinggi = margin lebih kecil, liquidation lebih dekat."
            ),
        ),
        "setmax":   dict(
            field="max_concurrent", min_v=1, max_v=10, integer=True,
            label="Max trades", hint="1–10", suffix="",
            howto=(
                "🔢 <b>Max concurrent trades</b> — batas posisi terbuka bareng.\n"
                "Kalau sudah penuh, signal baru di-skip (di-log, tidak entry).\n"
                "Saran: 2–4 buat akun kecil biar margin gak ke-spread tipis."
            ),
        ),
        "setloss":  dict(
            field="daily_loss_cap_pct", min_v=1, max_v=50, integer=False,
            label="Daily loss cap %", hint="1–50", suffix="%",
            howto=(
                "🛑 <b>Daily loss cap</b> — kalau realized PnL hari ini ≤ −X% balance, "
                "bot auto-pause sampai besok (UTC).\n"
                "Saran: 3–5%. Resume manual via ▶️ Resume."
            ),
        ),
        "setrr": dict(
            field="rr_ratio", min_v=1, max_v=10, integer=False,
            label="RR ratio", hint="1–10", suffix=":1",
            howto=(
                "🎯 <b>RR ratio</b> — target TP berdasarkan jarak Entry → SL.\n"
                "1 = TP 1:1, 1.5 = TP 1.5:1, 2 = TP 2:1.\n"
                "Minimal 1 supaya reward tidak lebih kecil dari risk."
            ),
        ),
        "setslmult": dict(
            field="sl_mult", min_v=0.5, max_v=5.0, integer=False,
            label="SL multiplier", hint="0.5–5.0", suffix="x",
            howto=(
                "🛡 <b>SL multiplier</b> — pelebar jarak Stop Loss dari Entry.\n"
                "1.0 = pakai SL dari signal (default).\n"
                "1.5 = SL 50% lebih jauh (lebih aman dari wick, TP juga jadi lebih jauh).\n"
                "2.0 = SL 2x lebih jauh (swing-mini)."
            ),
        ),
    }

    async def _current_value(telegram_id: int, field: str) -> str:
        async with pool.acquire() as conn:
            row = await user_row(conn, telegram_id=telegram_id)
        if not row or row.get(field) is None:
            return "—"
        v = row[field]
        if field == "leverage" or field == "max_concurrent":
            return str(int(v))
        if field == "fixed_notional_usdt" and v is None:
            return "OFF"
        return f"{float(v):.2f}"

    async def _ask_numeric(target, state: FSMContext, key: str):
        cfg = _NUMERIC_CONFIG[key]
        await state.set_state(NumericSetup.waiting_for_value)
        tid = target.from_user.id
        cur = await _current_value(tid, cfg["field"])
        suffix = cfg.get("suffix", "")
        text = (
            f"{cfg['howto']}\n\n"
            f"Current <b>{cfg['label']}</b>: <b>{cur}{suffix}</b>\n"
            f"Masukkan nilai baru ({cfg['hint']}):"
        )
        if isinstance(target, CallbackQuery):
            await target.answer()
            await target.message.edit_text(text, reply_markup=back_button(), parse_mode="HTML")
            await state.update_data(numeric_key=key, prompt_msg_id=target.message.message_id)
        else:
            sent = await target.answer(text, reply_markup=back_button(), parse_mode="HTML")
            await state.update_data(numeric_key=key, prompt_msg_id=sent.message_id)

    @dp.message(Command("setrisk"))
    async def on_setrisk_cmd(m: Message, state: FSMContext): await _ask_numeric(m, state, "setrisk")

    @dp.message(Command("setlev"))
    async def on_setlev_cmd(m: Message, state: FSMContext): await _ask_numeric(m, state, "setlev")

    @dp.message(Command("setmax"))
    async def on_setmax_cmd(m: Message, state: FSMContext): await _ask_numeric(m, state, "setmax")

    @dp.message(Command("setloss"))
    async def on_setloss_cmd(m: Message, state: FSMContext): await _ask_numeric(m, state, "setloss")

    @dp.message(Command("setrr"))
    async def on_setrr_cmd(m: Message, state: FSMContext): await _ask_numeric(m, state, "setrr")

    @dp.message(Command("setslmult"))
    async def on_setslmult_cmd(m: Message, state: FSMContext): await _ask_numeric(m, state, "setslmult")

    @dp.message(Command("setnotional"))
    async def on_setnotional_cmd(m: Message, state: FSMContext):
        await m.answer("Notional cap sudah dihapus. Pakai /setrisk untuk Risk % Equity.")

    async def _margin_mode_text(telegram_id: int) -> str:
        async with pool.acquire() as conn:
            row = await user_row(conn, telegram_id=telegram_id)
        cur = (row["margin_mode"] if row else "ISOLATED") or "ISOLATED"
        return (
            "🧱 <b>Margin Mode</b>\n\n"
            f"Current: <b>{cur}</b>\n\n"
            "<b>ISOLATED</b>: margin per symbol terpisah, liquidation per posisi lebih mudah dikontrol.\n"
            "<b>CROSS</b>: posisi pakai shared futures wallet margin, bisa tahan floating lebih jauh tapi risk wallet lebih nyambung."
        )

    def margin_mode_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="ISOLATED", callback_data="margin:ISOLATED"),
                InlineKeyboardButton(text="CROSS", callback_data="margin:CROSSED"),
            ],
            [InlineKeyboardButton(text="« Back", callback_data="menu")],
        ])

    @dp.message(Command("setmargin"))
    async def on_setmargin_cmd(m: Message):
        await _ensure_user(pool, m.from_user.id, m.from_user.username, m.from_user.first_name)
        await m.answer(await _margin_mode_text(m.from_user.id), reply_markup=margin_mode_keyboard(), parse_mode="HTML")

    @dp.callback_query(F.data == "setmargin")
    async def cb_setmargin(cb: CallbackQuery):
        await cb.answer()
        await _ensure_user(pool, cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
        await cb.message.edit_text(await _margin_mode_text(cb.from_user.id), reply_markup=margin_mode_keyboard(), parse_mode="HTML")

    @dp.callback_query(F.data.startswith("margin:"))
    async def cb_margin_mode(cb: CallbackQuery):
        margin_mode = cb.data.split(":", 1)[1]
        if margin_mode not in {"ISOLATED", "CROSSED"}:
            await cb.answer("Invalid margin mode", show_alert=True)
            return
        await _ensure_user(pool, cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
        async with pool.acquire() as conn:
            await update_setting(conn, telegram_id=cb.from_user.id, field="margin_mode", value=margin_mode)
        await cb.answer(f"Margin mode set to {'CROSS' if margin_mode == 'CROSSED' else margin_mode}")
        dashboard = await _dashboard_text(pool, cb.from_user.id)
        await cb.message.edit_text(
            f"✅ Margin mode set to <b>{'CROSS' if margin_mode == 'CROSSED' else margin_mode}</b>\n\n{dashboard}",
            reply_markup=await _menu_for(pool, cb.from_user.id),
            parse_mode="HTML",
        )

    # ── risk mode (percent vs fixed $) ───────────────────────────────────────

    async def _risk_mode_text(telegram_id: int) -> str:
        async with pool.acquire() as conn:
            row = await user_row(conn, telegram_id=telegram_id)
        cur = (row["risk_mode"] if row else "percent") or "percent"
        risk_pct = float(row["risk_pct"] or 2.0) if row else 2.0
        fixed_risk = float(row["fixed_risk_usdt"] or 3.0) if row else 3.0
        return (
            "💵 <b>Risk Mode</b>\n\n"
            f"Current: <b>{cur}</b>\n"
            f"• percent: {risk_pct:.2f}% of equity per trade (atur via /setrisk)\n"
            f"• fixed: ${fixed_risk:.2f} per trade (atur via /setfixedrisk)\n\n"
            "Pakai <b>percent</b> kalau mau risk skala dengan equity (saran).\n"
            "Pakai <b>fixed</b> kalau target risk USD tetap apapun balance-nya."
        )

    def risk_mode_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="% equity", callback_data="riskmode:percent"),
                InlineKeyboardButton(text="$ fixed", callback_data="riskmode:fixed"),
            ],
            [InlineKeyboardButton(text="« Back", callback_data="menu")],
        ])

    @dp.message(Command("setriskmode"))
    async def on_setriskmode_cmd(m: Message):
        await _ensure_user(pool, m.from_user.id, m.from_user.username, m.from_user.first_name)
        await m.answer(await _risk_mode_text(m.from_user.id), reply_markup=risk_mode_keyboard(), parse_mode="HTML")

    @dp.callback_query(F.data == "setriskmode")
    async def cb_setriskmode(cb: CallbackQuery):
        await cb.answer()
        await _ensure_user(pool, cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
        await cb.message.edit_text(await _risk_mode_text(cb.from_user.id), reply_markup=risk_mode_keyboard(), parse_mode="HTML")

    @dp.callback_query(F.data.startswith("riskmode:"))
    async def cb_risk_mode_pick(cb: CallbackQuery):
        choice = cb.data.split(":", 1)[1]
        if choice not in {"percent", "fixed"}:
            await cb.answer("Invalid risk mode", show_alert=True)
            return
        await _ensure_user(pool, cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
        async with pool.acquire() as conn:
            await update_setting(conn, telegram_id=cb.from_user.id, field="risk_mode", value=choice)
        await cb.answer(f"Risk mode → {choice}")
        dashboard = await _dashboard_text(pool, cb.from_user.id)
        await cb.message.edit_text(
            f"✅ Risk mode set to <b>{choice}</b>\n\n{dashboard}",
            reply_markup=await _menu_for(pool, cb.from_user.id),
            parse_mode="HTML",
        )

    # ── SL menu (toggle on/off + multiplier) ─────────────────────────────────

    async def _sl_menu_text(telegram_id: int) -> str:
        async with pool.acquire() as conn:
            row = await user_row(conn, telegram_id=telegram_id)
        sl_enabled = bool(row["sl_enabled"]) if row and row["sl_enabled"] is not None else True
        sl_mult = float(row["sl_mult"] or 1.0) if row else 1.0
        margin_mode = (row["margin_mode"] if row else "ISOLATED") or "ISOLATED"
        state = "ON" if sl_enabled else "OFF"
        guard_line = (
            "" if sl_enabled else
            f"\n⚠️ SL OFF aktif. Margin mode wajib ISOLATED (sekarang: <b>{margin_mode}</b>)."
        )
        return (
            "🛡 <b>SL Settings</b>\n\n"
            f"Status: <b>{state}</b>\n"
            f"Multiplier: <b>{sl_mult:.2f}x</b> (lebar SL relatif ke signal)\n"
            f"{guard_line}\n\n"
            "<b>SL ON</b>: stop loss dipasang di Binance + trail TP1→BE setelah TP1.\n"
            "<b>SL OFF</b>: tidak pasang stop di exchange. Posisi hanya ditutup oleh TP, BE trail setelah TP1, atau likuidasi ISOLATED.\n"
            "Multiplier <b>1.0</b> = pakai SL dari signal. <b>1.5</b>+ = lebih aman dari wick."
        )

    def sl_menu_keyboard(sl_enabled: bool) -> InlineKeyboardMarkup:
        toggle_label = "❌ Turn SL OFF" if sl_enabled else "✅ Turn SL ON"
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=toggle_label, callback_data="sltoggle")],
            [InlineKeyboardButton(text="📏 Set Multiplier", callback_data="setslmult")],
            [InlineKeyboardButton(text="« Back", callback_data="menu")],
        ])

    async def _send_sl_menu(target):
        tid = target.from_user.id
        async with pool.acquire() as conn:
            row = await user_row(conn, telegram_id=tid)
        sl_enabled = bool(row["sl_enabled"]) if row and row["sl_enabled"] is not None else True
        text = await _sl_menu_text(tid)
        kb = sl_menu_keyboard(sl_enabled)
        if isinstance(target, CallbackQuery):
            await target.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        else:
            await target.answer(text, reply_markup=kb, parse_mode="HTML")

    @dp.message(Command("setsl"))
    async def on_setsl_cmd(m: Message):
        await _ensure_user(pool, m.from_user.id, m.from_user.username, m.from_user.first_name)
        await _send_sl_menu(m)

    @dp.callback_query(F.data == "setslmenu")
    async def cb_setslmenu(cb: CallbackQuery):
        await cb.answer()
        await _ensure_user(pool, cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
        await _send_sl_menu(cb)

    @dp.callback_query(F.data == "sltoggle")
    async def cb_sl_toggle(cb: CallbackQuery):
        await _ensure_user(pool, cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
        async with pool.acquire() as conn:
            row = await user_row(conn, telegram_id=cb.from_user.id)
            cur = bool(row["sl_enabled"]) if row and row["sl_enabled"] is not None else True
            new_val = not cur
            margin_mode = (row["margin_mode"] if row else "ISOLATED") or "ISOLATED"
            # Going SL OFF requires ISOLATED. We do NOT auto-switch — that would
            # silently change user's margin setting. Instead we refuse the toggle
            # so the user has to fix margin mode first, mirroring executor reject.
            if not new_val and margin_mode != "ISOLATED":
                await cb.answer(
                    "SL OFF butuh ISOLATED. Ubah margin mode dulu via 🧱 Margin Mode.",
                    show_alert=True,
                )
                await _send_sl_menu(cb)
                return
            await cb.answer(f"SL → {'OFF' if not new_val else 'ON'}")
            await update_setting(conn, telegram_id=cb.from_user.id, field="sl_enabled", value=new_val)
        await _send_sl_menu(cb)

    @dp.callback_query(F.data.in_({"setrisk", "setlev", "setmax", "setloss", "setrr", "setslmult"}))
    async def cb_numeric(cb: CallbackQuery, state: FSMContext):
        await _ask_numeric(cb, state, cb.data)

    @dp.message(NumericSetup.waiting_for_value)
    async def on_numeric_value(m: Message, state: FSMContext):
        data = await state.get_data()
        key = data.get("numeric_key", "setrisk")
        prompt_msg_id = data.get("prompt_msg_id")
        cfg = _NUMERIC_CONFIG[key]
        # Wipe user's input — keeps flow in single bubble.
        await _safe_delete(m.bot, chat_id=m.chat.id, message_id=m.message_id)
        try:
            raw = float((m.text or "").strip())
            if raw < cfg["min_v"] or raw > cfg["max_v"]:
                raise ValueError(f"must be {cfg['hint']}")
            value = int(raw) if cfg["integer"] else raw
        except (ValueError, TypeError) as e:
            await _edit_or_reply(
                m.bot, chat_id=m.chat.id, prompt_msg_id=prompt_msg_id,
                text=f"❌ Invalid: {e}\n\nEnter {cfg['label']} ({cfg['hint']}):",
                reply_markup=back_button(),
            )
            return
        await state.clear()
        await _ensure_user(pool, m.from_user.id, m.from_user.username, m.from_user.first_name)
        async with pool.acquire() as conn:
            await update_setting(conn, telegram_id=m.from_user.id, field=cfg["field"], value=value)
        suffix = cfg.get("suffix", "")
        shown_value = "OFF" if value is None else f"{value}{suffix}"
        dashboard = await _dashboard_text(pool, m.from_user.id)
        await _edit_or_reply(
            m.bot, chat_id=m.chat.id, prompt_msg_id=prompt_msg_id,
            text=f"✅ {cfg['label']} set to {shown_value}\n\n{dashboard}",
            reply_markup=await _menu_for(pool, m.from_user.id), parse_mode="HTML",
        )
