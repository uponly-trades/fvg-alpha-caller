import asyncio
import logging
import sys

import uvicorn

from trade_executor.config import settings
from trade_executor.http_api import app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("trade_executor")


async def run():
    config = uvicorn.Config(app, host="0.0.0.0", port=settings.HTTP_PORT, log_level="info")
    server = uvicorn.Server(config)
    log.info("trade_executor starting on :%d", settings.HTTP_PORT)
    await server.serve()


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
