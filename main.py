import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from chart_generator import generate_chart
from config import TIMEFRAMES
from fvg_engine import FVGTracker
from sim_trades import SimTradeStore
from trade_combo import evaluate_trade_setup, build_trade_from_kronos
from feature_extractor import extract_multi_tf, btc_regime
import kronos_client
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

CHART_DIR = Path("/app/data/charts")


class AlphaCaller:
    def __init__(self):
        self.tracker = FVGTracker()
        self.poller = BinanceKlineWS(on_bar_close=self._on_bar_close)
        self.sim_store = SimTradeStore()

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

    def _btc_bars_by_tf(self) -> dict:
        return self._timeframe_bars("BTCUSDT")

    def _log_features(self, zone, current_price: float, event_type: str) -> None:
        """Read-only ML logger: snapshot all-TF indicator state at decision time."""
        try:
            decision_id = self.sim_store.make_decision_id(zone, current_price, event_type)
            bars_by_tf = self._timeframe_bars(zone.symbol)
            features = extract_multi_tf(bars_by_tf, symbol=zone.symbol, with_ls_ratio=True)
            btc_ctx = btc_regime(self._btc_bars_by_tf())
            self.sim_store.add_signal_features(decision_id, zone, features, btc_ctx)
        except Exception as e:
            logger.warning("log_features failed (%s %s): %s", zone.symbol, zone.tf, e)

    async def _evaluate_setup_async(self, zone, current_price: float):
        """Try Kronos first; fall back to StochRSI combo on failure."""
        bars_by_tf = self._timeframe_bars(zone.symbol)
        tf_bars = bars_by_tf.get(zone.tf, [])
        ohlcv = [
            {"open": float(b.open), "high": float(b.high), "low": float(b.low),
             "close": float(b.close), "volume": float(b.volume)}
            for b in tf_bars
        ]
        atr = float(getattr(zone, "atr", 0.0) or 0.0)

        kronos = await kronos_client.predict(
            bars=ohlcv,
            current_price=float(current_price),
            atr=atr,
            zone_direction=int(zone.direction),
            symbol=zone.symbol,
            tf=zone.tf,
        )
        if kronos is not None:
            kronos_setup = build_trade_from_kronos(kronos, int(zone.direction))
            # Bearish FVG: combo path applies reversal filter (Kronos has no bar-context).
            if int(zone.direction) != 1 and kronos_setup.status == "SKIP: SHORT VIA COMBO":
                return evaluate_trade_setup(zone, current_price, bars_by_tf)
            return kronos_setup
        return evaluate_trade_setup(zone, current_price, bars_by_tf)

    def _save_chart_png(self, zone, chart_png: bytes) -> str:
        """Persist chart PNG to disk, return path string."""
        try:
            CHART_DIR.mkdir(parents=True, exist_ok=True)
            fname = f"{zone.symbol}_{zone.tf}_{int(zone.born_time)}.png"
            path = CHART_DIR / fname
            path.write_bytes(chart_png)
            return str(path)
        except Exception as e:
            logger.error("chart save failed: %s", e)
            return ""

    async def _store_fvg_all_modes(self, zone, chart_path: str, current_price: float) -> None:
        """Save FVG record + simulate trade via Kronos (fallback: StochRSI combo)."""
        self.sim_store.add_fvg(zone, chart_path=chart_path or None)
        bars_by_tf = self._timeframe_bars(zone.symbol)
        buf_summary = {tf: len(b) for tf, b in bars_by_tf.items()}
        logger.info("store_fvg_all_modes %s | bars=%s", zone.symbol, buf_summary)
        # Use Kronos for sim trade (same path as alert)
        setup = await self._evaluate_setup_async(zone, current_price)
        logger.info("  kronos/combo status=%s valid=%s", setup.status, setup.valid)
        self.sim_store.add_kronos_decision(zone, setup, current_price, "new_fvg")
        self._log_features(zone, current_price, "new_fvg")
        if setup.valid:
            self.sim_store.add_sim_trade(zone, setup, zone.born_time)

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
                if self.sim_store.mark_recap_sent(key):
                    send_trade_recap(name, self.sim_store.daily_recap(now.date().isoformat()))
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
            trade_setup = await self._evaluate_setup_async(zone, price)
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
                predicted_bars=getattr(trade_setup, "predicted_bars", None),
            )
            if event["type"] == "approaching":
                self.sim_store.add_kronos_decision(zone, trade_setup, price, "approach")
                self._log_features(zone, price, "approach")
                send_approach_alert(zone, price, chart_png=chart_png, trade_setup=trade_setup, timeframe_bars=self._timeframe_bars(zone.symbol))
                logger.info("Approach alert %s %s | price=%s", symbol, tf, price)
            elif event["type"] == "touch":
                self.sim_store.add_kronos_decision(zone, trade_setup, price, "touch")
                self._log_features(zone, price, "touch")
                send_touch_alert(zone, price, chart_png=chart_png, trade_setup=trade_setup, timeframe_bars=self._timeframe_bars(zone.symbol))
                logger.info("Touch alert %s %s | price=%s", symbol, tf, price)

        # Check new FVG
        new_zone = self.tracker.check_new_fvg(symbol, tf)
        if new_zone and not new_zone.alerted:
            new_zone.alerted = True
            price = bars[-1].close
            trade_setup = await self._evaluate_setup_async(new_zone, price)

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
                predicted_bars=getattr(trade_setup, "predicted_bars", None),
            )

            chart_path = self._save_chart_png(new_zone, chart_png) if chart_png else ""
            await self._store_fvg_all_modes(new_zone, chart_path, price)

            send_new_fvg_alert(new_zone, chart_png=chart_png, trade_setup=trade_setup, timeframe_bars=self._timeframe_bars(new_zone.symbol))
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
