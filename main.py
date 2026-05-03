import asyncio
import logging
import sys

from chart_generator import generate_chart
from config import TIMEFRAMES
from fvg_engine import FVGTracker
from indicator_context import format_indicator_context
from websocket_client import BinanceKlineWS
from telegram import (
    send_approach_alert,
    send_mitigated_alert,
    send_new_fvg_alert,
    send_touch_alert,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("alpha")


class AlphaCaller:
    def __init__(self):
        self.tracker = FVGTracker()
        self.poller = BinanceKlineWS(on_bar_close=self._on_bar_close)

    async def _on_bar_close(self, symbol: str, tf: str, bars):
        if len(bars) < 3:
            return

        self.tracker.update_buffer(symbol, tf, bars)

        # Check mitigation
        mitigated = self.tracker.check_mitigation(symbol, tf, bars)
        for zone in mitigated:
            send_mitigated_alert(zone)

        # Check approaching + touch on strong zones
        interactions = self.tracker.check_interaction(symbol, tf, bars)
        for event in interactions:
            zone = event["zone"]
            price = bars[-1].close
            chart_png = generate_chart(
                bars=bars,
                zone_top=zone.top,
                zone_bottom=zone.bottom,
                zone_direction=zone.direction,
                symbol=zone.symbol,
                tf=zone.tf,
                rsi_value=zone.rsi,
            )
            zone.indicator_context = format_indicator_context(zone.symbol, self.tracker.buffers)
            if event["type"] == "approaching":
                send_approach_alert(zone, price, chart_png=chart_png)
                logger.info("Approach alert %s %s | price=%s", symbol, tf, price)
            elif event["type"] == "touch":
                send_touch_alert(zone, price, chart_png=chart_png)
                logger.info("Touch alert %s %s | price=%s", symbol, tf, price)

        # Check new FVG
        new_zone = self.tracker.check_new_fvg(symbol, tf)
        if new_zone and not new_zone.alerted:
            new_zone.alerted = True

            chart_png = generate_chart(
                bars=bars,
                zone_top=new_zone.top,
                zone_bottom=new_zone.bottom,
                zone_direction=new_zone.direction,
                symbol=new_zone.symbol,
                tf=new_zone.tf,
                rsi_value=new_zone.rsi,
            )

            new_zone.indicator_context = format_indicator_context(new_zone.symbol, self.tracker.buffers)
            send_new_fvg_alert(new_zone, chart_png=chart_png)
            logger.info(
                "New FVG alert %s %s | strength=%d rsi=%s",
                symbol, tf, new_zone.main_strength, new_zone.rsi,
            )

    async def run(self):
        logger.info(
            "Alpha Caller (Binance WS + REST fallback) | tfs=%d",
            len(TIMEFRAMES),
        )
        await self.poller.run()


async def main():
    caller = AlphaCaller()
    try:
        await caller.run()
    except KeyboardInterrupt:
        caller.poller.stop()
        logger.info("Shutdown by user.")


if __name__ == "__main__":
    asyncio.run(main())
