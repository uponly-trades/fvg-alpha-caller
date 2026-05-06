import time

from aiogram import Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message

from telegram_bot.config import settings
from telegram_bot.templates import onboarding_intro


def register_handlers(dp: Dispatcher, pool) -> None:

    @dp.message(Command("start"))
    async def on_start(m: Message):
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO users (telegram_id, telegram_username, first_name,
                                   created_at, updated_at)
                VALUES ($1, $2, $3, $4, $4)
                ON CONFLICT (telegram_id) DO UPDATE SET
                  telegram_username=EXCLUDED.telegram_username,
                  first_name=EXCLUDED.first_name,
                  updated_at=EXCLUDED.updated_at
                """,
                m.from_user.id, m.from_user.username, m.from_user.first_name,
                int(time.time() * 1000),
            )
        await m.answer(onboarding_intro(settings.DASHBOARD_URL, "<PROXY_IP>"))

    @dp.message(Command("status"))
    async def on_status(m: Message):
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT enabled, paused_until, pause_reason FROM users WHERE telegram_id=$1",
                m.from_user.id,
            )
        if not row:
            await m.answer("Not registered. Send /start first.")
            return
        if row["paused_until"] and row["paused_until"] > int(time.time() * 1000):
            await m.answer(f"⏸ paused (reason: {row['pause_reason']})")
        elif row["enabled"]:
            await m.answer("✅ enabled — trading live signals")
        else:
            await m.answer("❌ disabled — toggle in dashboard /settings")

    @dp.message(Command("pause"))
    async def on_pause(m: Message):
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET enabled=false, updated_at=$1 WHERE telegram_id=$2",
                int(time.time() * 1000), m.from_user.id,
            )
        await m.answer("Paused. Send /resume to re-enable.")

    @dp.message(Command("resume"))
    async def on_resume(m: Message):
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET enabled=true, paused_until=NULL, pause_reason=NULL, updated_at=$1 WHERE telegram_id=$2",
                int(time.time() * 1000), m.from_user.id,
            )
        await m.answer("Resumed.")
