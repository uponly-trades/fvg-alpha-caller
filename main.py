import asyncio
import logging
import sys

from binance_client import BinanceClient
from chart_generator import generate_chart
from config import POLL_INTERVAL_SEC, SYMBOLS, TIMEFRAMES
from fvg_engine import FVGTracker
from telegram import send_mitigated_alert, send_new_fvg_alert

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("alpha")


async def process_symbol_tf(client: BinanceClient, tracker: FVGTracker, symbol: str, tf: str):
    bars = await client.fetch_klines(symbol, tf)
    if not bars:
        return

    tracker.update_buffer(symbol, tf, bars)

    # Check mitigation first
    mitigated = tracker.check_mitigation(symbol, tf, bars)
    for zone in mitigated:
        send_mitigated_alert(zone)

    # Check new FVG
    new_zone = tracker.check_new_fvg(symbol, tf)
    if new_zone and not new_zone.alerted:
        new_zone.alerted = True

        # Generate chart
        chart_png = generate_chart(
            bars=bars,
            zone_top=new_zone.top,
            zone_bottom=new_zone.bottom,
            zone_direction=new_zone.direction,
            symbol=new_zone.symbol,
            tf=new_zone.tf,
            rsi_value=new_zone.rsi,
        )

        send_new_fvg_alert(new_zone, chart_png=chart_png)
        logger.info("Alert sent %s %s | strength=%d rsi=%s", symbol, tf, new_zone.main_strength, new_zone.rsi)


async def poll_cycle(client: BinanceClient, tracker: FVGTracker):
    tasks = []
    for symbol in SYMBOLS:
        for tf in TIMEFRAMES:
            tasks.append(process_symbol_tf(client, tracker, symbol, tf))
    await asyncio.gather(*tasks, return_exceptions=True)


async def main():
    tracker = FVGTracker()
    logger.info("Alpha Caller started | symbols=%d tfs=%d total=%d", len(SYMBOLS), len(TIMEFRAMES), len(SYMBOLS) * len(TIMEFRAMES))

    async with BinanceClient() as client:
        await poll_cycle(client, tracker)
        logger.info("Warm-up complete. Entering poll loop every %ds.", POLL_INTERVAL_SEC)

        while True:
            await asyncio.sleep(POLL_INTERVAL_SEC)
            await poll_cycle(client, tracker)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutdown by user.")
