import asyncio
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("telegram_bot")


async def run():
    log.info("telegram_bot starting (skeleton)")
    while True:
        await asyncio.sleep(60)


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
