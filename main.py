import asyncio
import logging
import sys

from chart_generator import generate_chart
from config import TIMEFRAMES
from fvg_engine import FVGTracker
from telegram import send_mitigated_alert, send_new_fvg_alert
from websocket_client import BinanceWSClient

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("alpha")


class AlphaCaller:
    def __init__(self):
        self.tracker = FVGTracker()
        self.ws_client = BinanceWSClient(on_bar_close=self._on_bar_close)

    async def _on_bar_close(self, symbol: str, tf: str, bar):
        # Build buffer from WebSocket bar history
        key = f"{symbol}_{tf}"
        bars = self.ws_client._buffer.get(key, [])
        if len(bars) < 3:
            return

        # Convert websocket Bar dataclass to format fvg_engine expects
        bar_objects = bars
        self.tracker.update_buffer(symbol, tf, bar_objects)

        # Check mitigation
        mitigated = self.tracker.check_mitigation(symbol, tf, bar_objects)
        for zone in mitigated:
            send_mitigated_alert(zone)

        # Check new FVG
        new_zone = self.tracker.check_new_fvg(symbol, tf)
        if new_zone and not new_zone.alerted:
            new_zone.alerted = True

            chart_png = generate_chart(
                bars=bar_objects,
                zone_top=new_zone.top,
                zone_bottom=new_zone.bottom,
                zone_direction=new_zone.direction,
                symbol=new_zone.symbol,
                tf=new_zone.tf,
                rsi_value=new_zone.rsi,
            )

            send_new_fvg_alert(new_zone, chart_png=chart_png)
            logger.info(
                "Alert sent %s %s | strength=%d rsi=%s",
                symbol, tf, new_zone.main_strength, new_zone.rsi,
            )

    async def run(self):
        logger.info(
            "Alpha Caller (WebSocket) | symbols=%d tfs=%d total=%d",
            len(self.ws_client._buffer),
            len(TIMEFRAMES),
            len(self.ws_client._buffer) * len(TIMEFRAMES),
        )
        await self.ws_client.run()


async def main():
    caller = AlphaCaller()
    try:
        await caller.run()
    except KeyboardInterrupt:
        caller.ws_client.stop()
        logger.info("Shutdown by user.")


if __name__ == "__main__":
    asyncio.run(main())
