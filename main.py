import asyncio
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

# trigger-TF duration in seconds (used by v2 freshness guard)
_TF_SECONDS = {"15m": 15 * 60, "30m": 30 * 60, "1h": 60 * 60, "2h": 2 * 60 * 60, "4h": 4 * 60 * 60}

from chart_generator import generate_chart
from config import (
    TIMEFRAMES, STRATEGY_VERSION, V2_COOLDOWN_SEC,
    V2_TRIGGER_TFS, V2_MAX_SIGNAL_AGE_SEC,
)
from fvg_engine import FVGTracker, detect_fvg, calc_strength
from sim_trades import SimTradeStore
from websocket_client import BinanceKlineWS
from strategy_v2 import _supertrend_recovery_state, evaluate_v2_signal
from trail_manager import TrailManager
from cooldown import CooldownStore
from telegram import send_trade_recap, send_v2_alert
from notify_bot_webhook import post_chart as _notify_bot_post_chart

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
        self.v2_trail = TrailManager()
        self.v2_cooldown = CooldownStore(window_sec=V2_COOLDOWN_SEC)

    def _timeframe_bars(self, symbol: str) -> dict:
        bars_by_tf = {}
        for tf in TIMEFRAMES:
            bars = self.tracker.buffers.get((symbol, tf), [])
            if not bars:
                bars = self.poller._buffers.get(f"{symbol}_{tf}", [])
            bars_by_tf[tf] = bars
        return bars_by_tf

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
                marker = getattr(self.sim_store, "mark_recap_sent", None)
                should_send = marker(key) if marker else (getattr(self, "_last_recap_key", None) != key)
                if should_send:
                    self._last_recap_key = key
                    send_trade_recap(name, self.sim_store.daily_recap(now.date().isoformat()))
                return

    def _v2_backfill_zones(self, symbol: str, tf: str, bars) -> None:
        """Scan a warm-up buffer with sliding 3-bar windows and register every
        FVG into tracker.zones. Idempotent — zone_id collision is the dedup key.

        Why: live capture only fires on bar close, so HTF zones (1h/2h/4h)
        wouldn't populate for hours after a restart, leaving evaluate_v2_signal
        confluence-blocked. Backfill closes that gap immediately.

        Must NOT touch tracker.last_bar_time — the next live close needs to
        proceed through _v2_capture_fvg unchanged.
        """
        if not bars or len(bars) < 3:
            return
        from fvg_engine import FVGZone
        for i in range(2, len(bars)):
            window = bars[i - 2:i + 1]
            fvg = detect_fvg(window, symbol=symbol)
            if not fvg:
                continue
            zone_id = f"{symbol}_{tf}_{fvg['born_time']}_{fvg['direction']}"
            if zone_id in self.tracker.zones:
                continue
            fvg["symbol"] = symbol
            try:
                s = calc_strength(
                    window,
                    fvg,
                    symbol=symbol,
                    existing_zones=self.tracker.zones,
                    use_live_context=False,
                )
            except Exception as e:
                logger.warning("backfill calc_strength fail %s %s: %s", symbol, tf, e)
                continue
            zone = FVGZone(
                symbol=symbol, tf=tf, direction=fvg["direction"],
                top=fvg["top"], bottom=fvg["bottom"], size=fvg["size"],
                born_time=fvg["born_time"],
                main_strength=s["main_strength"], bull_strength=s["bull_strength"],
                bear_strength=s["bear_strength"], label=s["label"],
                rsi=s["rsi"], atr=s["atr"], sl=s["sl"], tp1=s["tp1"], tp2=s["tp2"],
                price=s["price"],
                vol_change_pct=s["vol_change_pct"], price_change_pct=s["price_change_pct"],
                candle_body_pct=s["candle_body_pct"], dist_to_zone=s["dist_to_zone"],
                dominance_bias=s["dominance_bias"], btc_trend=s["btc_trend"],
                dominance_state=s["dominance_state"], btc_state=s["btc_state"],
                volume_spike_ratio=s["volume_spike_ratio"],
                displacement_ok=s["displacement_ok"],
                btc_alignment_ok=s["btc_alignment_ok"],
                confluence_tf_count=s["confluence_tf_count"],
                price_change_24h_pct=s["price_change_24h_pct"],
                confirm_score=s["confirm_score"], confirm_label=s["confirm_label"],
                fvg_buy_volume=s["fvg_buy_volume"],
                fvg_sell_volume=s["fvg_sell_volume"],
                volume_score=s["vol_score"],
                trend_score=s["trend_score"],
                quality_score=s["quality_score"],
            )
            self.tracker.zones[zone_id] = zone

    def _v2_backfill_all(self) -> None:
        """Walk all WS warm-up buffers and backfill zones once."""
        before = len(self.tracker.zones)
        for key, bars in list(self.poller._buffers.items()):
            if "_" not in key:
                continue
            symbol, tf = key.rsplit("_", 1)
            self.tracker.update_buffer(symbol, tf, bars)
            self._v2_backfill_zones(symbol, tf, bars)
        added = len(self.tracker.zones) - before
        try:
            self.tracker._save_zones()
        except Exception as e:
            logger.warning("backfill save_zones failed: %s", e)
        logger.info("v2 backfill complete | zones %d -> %d (+%d)",
                    before, len(self.tracker.zones), added)

    def _v2_capture_fvg(self, symbol: str, tf: str, bars) -> None:
        """v2 FVG ingest: bypass MIN_STRENGTH_TO_ALERT, store ANY detected FVG."""
        key = (symbol, tf)
        last_time = bars[-1].open_time
        if self.tracker.last_bar_time.get(key) == last_time:
            return
        if key not in self.tracker.last_bar_time:
            self.tracker.last_bar_time[key] = last_time
            return
        self.tracker.last_bar_time[key] = last_time
        fvg = detect_fvg(bars, symbol=symbol)
        if not fvg:
            return
        fvg["symbol"] = symbol
        s = calc_strength(bars, fvg, symbol=symbol, existing_zones=self.tracker.zones)
        from fvg_engine import FVGZone
        zone = FVGZone(
            symbol=symbol, tf=tf, direction=fvg["direction"],
            top=fvg["top"], bottom=fvg["bottom"], size=fvg["size"],
            born_time=fvg["born_time"],
            main_strength=s["main_strength"], bull_strength=s["bull_strength"],
            bear_strength=s["bear_strength"], label=s["label"],
            rsi=s["rsi"], atr=s["atr"], sl=s["sl"], tp1=s["tp1"], tp2=s["tp2"],
            price=s["price"],
            vol_change_pct=s["vol_change_pct"], price_change_pct=s["price_change_pct"],
            candle_body_pct=s["candle_body_pct"], dist_to_zone=s["dist_to_zone"],
            dominance_bias=s["dominance_bias"], btc_trend=s["btc_trend"],
            dominance_state=s["dominance_state"], btc_state=s["btc_state"],
            volume_spike_ratio=s["volume_spike_ratio"],
            displacement_ok=s["displacement_ok"],
            btc_alignment_ok=s["btc_alignment_ok"],
            confluence_tf_count=s["confluence_tf_count"],
            price_change_24h_pct=s["price_change_24h_pct"],
            confirm_score=s["confirm_score"], confirm_label=s["confirm_label"],
            fvg_buy_volume=s["fvg_buy_volume"],
            fvg_sell_volume=s["fvg_sell_volume"],
            volume_score=s["vol_score"],
            trend_score=s["trend_score"],
            quality_score=s["quality_score"],
        )
        zone_id = f"{symbol}_{tf}_{zone.born_time}_{zone.direction}"
        self.tracker.zones[zone_id] = zone
        self.tracker._save_zones()

    def _v2_handle_trail(self, symbol: str, tf: str, bars) -> None:
        updates = self.v2_trail.on_bar_close(symbol, tf, bars)
        for u in updates:
            logger.info(
                "v2 trail %s %s %s | %g -> %g",
                u.symbol, u.trigger_tf, "long" if u.direction == 1 else "short",
                u.previous_sl, u.new_sl,
            )
        # Touch-based stop on latest closed bar (use both wicks for conservative check).
        # STOPPED messages no longer broadcast to channel — per-trade close
        # outcomes (win/loss) belong in user trading bot, not public channel.
        last = bars[-1]
        for probe_price in (last.low, last.high):
            stops = self.v2_trail.check_stop_hit(symbol, last_price=probe_price)
            for st in stops:
                logger.info("v2 stopped %s %s | sl=%g price=%g",
                            st.symbol, st.signal_id, st.sl_at_stop, st.last_price)

    # Retest-entry flow:
    # - Current production path is bar-close based: _on_bar_close -> _v2_try_emit_signal.
    # - strategy_v2 now requires a valid FVG retest/rejection before alerting.
    # - The executor still enters with a market order after the decision is persisted.
    def _v2_try_emit_signal(self, symbol: str, tf: str, bars) -> None:
        bars_by_tf = self._timeframe_bars(symbol)
        sig = evaluate_v2_signal(symbol, self.tracker.zones, bars_by_tf)
        if sig is None:
            return
        if sig.trigger_tf != tf:
            return  # only emit on the originating TF's bar close
        # Freshness guard: refuse to alert/order on a stale trigger bar.
        if V2_MAX_SIGNAL_AGE_SEC > 0 and bars:
            tf_sec = _TF_SECONDS.get(sig.trigger_tf, 0)
            last_close_time_ms = bars[-1].open_time + tf_sec * 1000
            age_sec = time.time() - last_close_time_ms / 1000
            if age_sec > V2_MAX_SIGNAL_AGE_SEC:
                logger.warning(
                    "v2 stale signal skip %s %s | age=%.1fs > %ds",
                    symbol, sig.trigger_tf, age_sec, V2_MAX_SIGNAL_AGE_SEC,
                )
                return
        if not self.v2_cooldown.allow(symbol, sig.direction_str):
            logger.info("v2 cooldown skip %s %s", symbol, sig.direction_str)
            return
        signal_id = f"{symbol}_{sig.trigger_tf}_{sig.zone_born_time}_{sig.direction}"
        registered = self.v2_trail.register(
            signal_id=signal_id, symbol=symbol, trigger_tf=sig.trigger_tf,
            direction=sig.direction, entry=sig.entry, sl=sig.sl, atr=sig.atr,
        )
        if registered is None:
            logger.warning("v2 duplicate signal_id %s — alert suppressed", signal_id)
            return
        chart_png = None
        try:
            trigger_bars = bars_by_tf.get(sig.trigger_tf, []) or bars
            if trigger_bars and len(trigger_bars) >= 3:
                v2_plan = SimpleNamespace(
                    entry=float(sig.entry),
                    sl=float(sig.sl),
                    tp1=float(sig.entry),
                    tp2=float(sig.entry),
                    direction=sig.direction_str,
                )
                chart_png = generate_chart(
                    bars=trigger_bars[-100:],
                    zone_top=sig.zone_top,
                    zone_bottom=sig.zone_bottom,
                    zone_direction=sig.direction,
                    symbol=symbol,
                    tf=sig.trigger_tf,
                    timeframe_bars=bars_by_tf,
                    trade_plan=v2_plan,
                )
        except Exception as e:
            logger.warning("v2 chart render failed %s %s: %s", symbol, sig.trigger_tf, e)
        send_v2_alert(sig, timeframe_bars=bars_by_tf, chart_png=chart_png)
        # Persist as signal_decisions row → trade_executor.signal_poller picks it
        # up and places the order on each user with API keys + enabled.
        try:
            self.sim_store.add_v2_decision(sig, signal_id)
        except Exception as e:
            logger.error("v2 decision persist failed %s: %s", signal_id, e)
        # Best-effort: ship chart PNG + minimal metadata to notify-bot so it
        # can later attach the chart to its Signals · Exec Telegram card when
        # app-stable reports execution. Never blocks our own alert path.
        try:
            _notify_bot_post_chart(
                signal_id=signal_id,
                symbol=symbol,
                direction=sig.direction,
                tf=sig.trigger_tf,
                entry=sig.entry,
                sl=sig.sl,
                tp1=sig.entry,
                png_bytes=chart_png,
            )
        except Exception as e:
            logger.warning("notify-bot webhook dispatch failed %s: %s", signal_id, e)
        logger.info(
            "v2 signal %s %s %s | score=%d entry=%g sl=%g tp=%g chart=%s",
            symbol, sig.trigger_tf, sig.direction_str,
            sig.confluence_score, sig.entry, sig.sl, sig.tp,
            "yes" if chart_png else "no",
        )

    async def _on_bar_close(self, symbol: str, tf: str, bars):
        if len(bars) < 3:
            return

        if STRATEGY_VERSION != "v2":
            logger.warning("Only v2 strategy is supported. Current: %s", STRATEGY_VERSION)
            return

        self.tracker.update_buffer(symbol, tf, bars)
        if tf in V2_TRIGGER_TFS:
            st = _supertrend_recovery_state(bars)
            self.sim_store.upsert_supertrend_state(
                symbol=symbol,
                tf=tf,
                trend=st.trend,
                band=st.band,
                switch_price=st.switch_price,
                bar_time=bars[-1].open_time,
            )
        self.sim_store.update_open_trades(symbol, bars[-1])
        self._maybe_send_recap()
        # 1. Mitigation pass — drops fully-mitigated zones
        self.tracker.check_mitigation(symbol, tf, bars)
        # 2. Detect & store any FVG (any strength) on this bar
        self._v2_capture_fvg(symbol, tf, bars)
        # 3. Trail bookkeeping for any open v2 trades on this trigger TF
        if tf in V2_TRIGGER_TFS:
            self._v2_handle_trail(symbol, tf, bars)
        # 4. Evaluate signal only on trigger TFs (15m / 30m)
        if tf in V2_TRIGGER_TFS:
            self._v2_try_emit_signal(symbol, tf, bars)

    async def _v2_backfill_when_warm(self) -> None:
        """Periodically backfill zones from WS warm-up buffers until coverage
        stabilises. Idempotent (zone_id dedup) so safe to re-fire.
        """
        if STRATEGY_VERSION != "v2":
            return
        from config import SYMBOLS
        target = len(SYMBOLS) * len(TIMEFRAMES)
        full_threshold = max(1, int(target * 0.95))
        hard_deadline = time.time() + 60 * 60  # 60 min absolute cap
        last_count = -1
        stable_since: float | None = None
        while time.time() < hard_deadline:
            ready = len(self.poller._buffers)
            if ready > 0:
                break
            await asyncio.sleep(2)
        while time.time() < hard_deadline:
            ready = len(self.poller._buffers)
            try:
                self._v2_backfill_all()
            except Exception as e:
                logger.warning("v2 backfill failed: %s", e)
            if ready >= full_threshold:
                logger.info("v2 backfill done | coverage=%d/%d (>=95%%)", ready, target)
                return
            if ready == last_count:
                if stable_since is None:
                    stable_since = time.time()
                elif time.time() - stable_since >= 90:
                    logger.info(
                        "v2 backfill done | coverage stable at %d/%d for 90s",
                        ready, target,
                    )
                    return
            else:
                stable_since = None
                last_count = ready
            await asyncio.sleep(30)

    async def run(self):
        logger.info(
            "Alpha Caller (Binance WS + REST fallback) | tfs=%d",
            len(TIMEFRAMES),
        )
        backfill_task = asyncio.create_task(self._v2_backfill_when_warm())
        try:
            await self.poller.run()
        finally:
            backfill_task.cancel()


async def main():
    caller = AlphaCaller()
    try:
        await caller.run()
    except KeyboardInterrupt:
        caller.poller.stop()
        logger.info("Shutdown by user.")


if __name__ == "__main__":
    asyncio.run(main())
