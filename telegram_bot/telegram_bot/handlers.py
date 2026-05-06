from __future__ import annotations

import logging

from aiogram import Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from telegram_bot.executor_client import ExecutorClientError, account_summary, set_keys
from telegram_bot.queries import (
    list_trades,
    set_enabled,
    stats,
    update_setting,
    upsert_user,
    user_row,
)
from telegram_bot.templates import (
    fmt_balance,
    fmt_help,
    fmt_key_saved,
    fmt_settings,
    fmt_stats,
    fmt_trade_list,
)

log = logging.getLogger("handlers")


class KeySetup(StatesGroup):
    waiting_for_keys = State()


def _sender(m: Message) -> tuple[int, str | None, str | None]:
    return m.from_user.id, m.from_user.username, m.from_user.first_name


async def _ensure_user(pool, m: Message) -> None:
    tid, username, first_name = _sender(m)
    async with pool.acquire() as conn:
        await upsert_user(conn, telegram_id=tid, username=username, first_name=first_name)


def _parse_float_arg(cmd: CommandObject, *, min_v: float, max_v: float) -> float:
    if not cmd.args:
        raise ValueError(f"missing value ({min_v:g}-{max_v:g})")
    value = float(cmd.args.strip())
    if value < min_v or value > max_v:
        raise ValueError(f"value must be {min_v:g}-{max_v:g}")
    return value


def _parse_int_arg(cmd: CommandObject, *, min_v: int, max_v: int) -> int:
    value = int(_parse_float_arg(cmd, min_v=min_v, max_v=max_v))
    if value < min_v or value > max_v:
        raise ValueError(f"value must be {min_v}-{max_v}")
    return value


def register_handlers(dp: Dispatcher, pool) -> None:

    @dp.message(Command("start"))
    async def on_start(m: Message):
        await _ensure_user(pool, m)
        await m.answer(fmt_help())

    @dp.message(Command("help"))
    async def on_help(m: Message):
        await m.answer(fmt_help())

    @dp.message(Command("setkeys"))
    async def on_setkeys(m: Message, state: FSMContext):
        await _ensure_user(pool, m)
        await state.set_state(KeySetup.waiting_for_keys)
        await m.answer(
            "Send Binance API key + secret in 2 lines.\n"
            "Permissions: Futures Trading + Read. Never enable Withdraw."
        )

    @dp.message(KeySetup.waiting_for_keys)
    async def on_keys_message(m: Message, state: FSMContext):
        lines = [x.strip() for x in (m.text or "").splitlines() if x.strip()]
        if len(lines) != 2:
            await m.answer("Format must be exactly 2 lines: API key, then API secret.")
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
        await m.answer(fmt_key_saved(str(result.get("api_key_tail") or api_key[-4:])))

    @dp.message(Command("balance"))
    async def on_balance(m: Message):
        await _ensure_user(pool, m)
        try:
            summary = await account_summary(m.from_user.id)
        except ExecutorClientError as e:
            await m.answer(f"❌ Balance unavailable: {e}")
            return
        await m.answer(fmt_balance(summary))

    @dp.message(Command("trades"))
    async def on_trades(m: Message):
        await _ensure_user(pool, m)
        async with pool.acquire() as conn:
            rows = await list_trades(conn, telegram_id=m.from_user.id, closed=False)
        await m.answer(fmt_trade_list(rows, closed=False))

    @dp.message(Command("closed"))
    async def on_closed(m: Message):
        await _ensure_user(pool, m)
        async with pool.acquire() as conn:
            rows = await list_trades(conn, telegram_id=m.from_user.id, closed=True)
        await m.answer(fmt_trade_list(rows, closed=True))

    @dp.message(Command("stats"))
    async def on_stats(m: Message):
        await _ensure_user(pool, m)
        async with pool.acquire() as conn:
            row = await stats(conn, telegram_id=m.from_user.id)
        await m.answer(fmt_stats(row))

    @dp.message(Command("settings"))
    async def on_settings(m: Message):
        await _ensure_user(pool, m)
        async with pool.acquire() as conn:
            row = await user_row(conn, telegram_id=m.from_user.id)
        await m.answer(fmt_settings(row))

    @dp.message(Command("status"))
    async def on_status(m: Message):
        await _ensure_user(pool, m)
        async with pool.acquire() as conn:
            row = await user_row(conn, telegram_id=m.from_user.id)
        await m.answer(fmt_settings(row))

    @dp.message(Command("pause"))
    async def on_pause(m: Message):
        await _ensure_user(pool, m)
        async with pool.acquire() as conn:
            await set_enabled(conn, telegram_id=m.from_user.id, enabled=False)
        await m.answer("⏸ Paused. No new live trades. Send /resume to re-enable.")

    @dp.message(Command("resume"))
    async def on_resume(m: Message):
        await _ensure_user(pool, m)
        async with pool.acquire() as conn:
            await set_enabled(conn, telegram_id=m.from_user.id, enabled=True)
        await m.answer("✅ Live trading enabled.")

    @dp.message(Command("setrisk"))
    async def on_setrisk(m: Message, command: CommandObject):
        await _set_numeric(m, command, field="risk_pct", min_v=0.1, max_v=10, integer=False, label="Risk")

    @dp.message(Command("setlev"))
    async def on_setlev(m: Message, command: CommandObject):
        await _set_numeric(m, command, field="leverage", min_v=5, max_v=20, integer=True, label="Leverage")

    @dp.message(Command("setmax"))
    async def on_setmax(m: Message, command: CommandObject):
        await _set_numeric(m, command, field="max_concurrent", min_v=1, max_v=10, integer=True, label="Max trades")

    @dp.message(Command("setloss"))
    async def on_setloss(m: Message, command: CommandObject):
        await _set_numeric(m, command, field="daily_loss_cap_pct", min_v=1, max_v=50, integer=False, label="Daily loss cap")

    async def _set_numeric(
        m: Message,
        command: CommandObject,
        *,
        field: str,
        min_v: float,
        max_v: float,
        integer: bool,
        label: str,
    ) -> None:
        await _ensure_user(pool, m)
        try:
            value = _parse_int_arg(command, min_v=int(min_v), max_v=int(max_v)) if integer else _parse_float_arg(command, min_v=min_v, max_v=max_v)
        except ValueError as e:
            await m.answer(f"❌ {e}")
            return
        async with pool.acquire() as conn:
            await update_setting(conn, telegram_id=m.from_user.id, field=field, value=value)
        suffix = "x" if field == "leverage" else "%" if field in {"risk_pct", "daily_loss_cap_pct"} else ""
        await m.answer(f"✅ {label} set to {value}{suffix}")
