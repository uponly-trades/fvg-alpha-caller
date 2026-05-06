import asyncio
import logging
import sys

from aiogram import Dispatcher

from telegram_bot.client import make_bot
from telegram_bot.config import settings
from telegram_bot.db import create_pool
from telegram_bot.handlers import register_handlers
from telegram_bot.listener import run_listener

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("telegram_bot")


async def run():
    pool = await create_pool(settings.DATABASE_URL)
    bot = make_bot()
    dp = Dispatcher()
    register_handlers(dp, pool)
    log.info("telegram_bot starting")
    await asyncio.gather(
        dp.start_polling(bot),
        run_listener(pool, bot),
    )


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
