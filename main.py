import asyncio
import logging
import sys
from datetime import datetime, timezone

from chart_generator import generate_chart
from config import TIMEFRAMES
from fvg_engine import FVGTracker
from sim_trades import SimTradeStore
from trade_combo import evaluate_trade_setup
from websocket_client import BinanceKlineWS
from telegram import (
    send_approach_alert,
    send_mitigated_alert,
    send_new_fvg_alert,
    send_touch_alert,
    send_trade_recap,
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
        self.sim_store = SimTradeStore()
        self._last_recap_key = None

    def _timeframe_bars(self, symbol: str) -> dict:
        bars_by_tf = {}
        for tf in TIMEFRAMES:
            bars = self.tracker.buffers.get((symbol, tf), [])
            if not bars:
                bars = self.poller._buffers.get(f"{symbol}_{tf}", [])
            bars_by_tf[tf] = bars
        return bars_by_tf

    def _evaluate_setup(self, zone, current_price: float):
        return evaluate_trade_setup(zone, current_price, self._timeframe_bars(zone.symbol))

    def _maybe_save_trade(self, zone, setup, created_at: int) -> None:
        if setup.valid:
            self.sim_store.add_trade(zone, setup, created_at)

    def _maybe_send_recap(self, now=None) -> None:
        now = now or datetime.now(timezone.utc)
        sessions = {
            "Subuh": (4, 5),
            "Pagi": (8, 9),
            "Siang": (12, 13),
            "Sore": (16, 17),
            "Malam": (20, 21),
        }
        for name, (start_hour, end_hour) in sessions.items():
            if start_hour <= now.hour < end_hour:
                key = f"{now.date().isoformat()}-{name}"
                if self._last_recap_key != key:
                    send_trade_recap(name, self.sim_store.daily_recap(now.date().isoformat()))
                    self._last_recap_key = key
                return

    async def _on_bar_close(self, symbol: str, tf: str, bars):
        if len(bars) < 3:
            return

        self.tracker.update_buffer(symbol, tf, bars)
        self.sim_store.update_open_trades(symbol, bars[-1])
        self._maybe_send_recap()

        # Check mitigation
        mitigated = self.tracker.check_mitigation(symbol, tf, bars)
        for zone in mitigated:
            send_mitigated_alert(zone)

        # Check approaching + touch on strong zones
        interactions = self.tracker.check_interaction(symbol, tf, bars)
        for event in interactions:
            zone = event["zone"]
            price = bars[-1].close
            trade_setup = self._evaluate_setup(zone, price)
            chart_png = generate_chart(
                bars=bars,
                zone_top=zone.top,
                zone_bottom=zone.bottom,
                zone_direction=zone.direction,
                symbol=zone.symbol,
                tf=zone.tf,
                rsi_value=zone.rsi,
                timeframe_bars=self._timeframe_bars(zone.symbol),
                trade_plan=trade_setup.trade,
            )
            if event["type"] == "approaching":
                send_approach_alert(zone, price, chart_png=chart_png, trade_setup=trade_setup)
                logger.info("Approach alert %s %s | price=%s", symbol, tf, price)
            elif event["type"] == "touch":
                send_touch_alert(zone, price, chart_png=chart_png, trade_setup=trade_setup)
                logger.info("Touch alert %s %s | price=%s", symbol, tf, price)

        # Check new FVG
        new_zone = self.tracker.check_new_fvg(symbol, tf)
        if new_zone and not new_zone.alerted:
            new_zone.alerted = True
            price = bars[-1].close
            trade_setup = self._evaluate_setup(new_zone, price)
            self._maybe_save_trade(new_zone, trade_setup, new_zone.born_time)

            chart_png = generate_chart(
                bars=bars,
                zone_top=new_zone.top,
                zone_bottom=new_zone.bottom,
                zone_direction=new_zone.direction,
                symbol=new_zone.symbol,
                tf=new_zone.tf,
                rsi_value=new_zone.rsi,
                timeframe_bars=self._timeframe_bars(new_zone.symbol),
                trade_plan=trade_setup.trade,
            )

            send_new_fvg_alert(new_zone, chart_png=chart_png, trade_setup=trade_setup)
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
