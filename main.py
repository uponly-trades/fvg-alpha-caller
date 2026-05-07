import asyncio
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# trigger-TF duration in seconds (used by v2 freshness guard)
_TF_SECONDS = {"15m": 15 * 60, "30m": 30 * 60, "1h": 60 * 60, "2h": 2 * 60 * 60, "4h": 4 * 60 * 60}

from chart_generator import generate_chart
from config import (
    TIMEFRAMES, STRATEGY_VERSION, KRONOS_ENABLED, V2_COOLDOWN_SEC,
    V2_TRIGGER_TFS, V2_MAX_SIGNAL_AGE_SEC,
)
from fvg_engine import FVGTracker, detect_fvg, calc_strength
from sim_trades import SimTradeStore
from trade_combo import (
    evaluate_trade_setup,
    build_trade_from_kronos,
    v2_decision,
    build_mitigated_breakout,
    build_mitigated_reversal,
)
from feature_extractor import extract_multi_tf, btc_regime
if STRATEGY_VERSION == "v1" and KRONOS_ENABLED:
    import kronos_client
else:
    kronos_client = None  # disabled in v2
from websocket_client import BinanceKlineWS
from strategy_v2 import evaluate_v2_signal
from trail_manager import TrailManager
from cooldown import CooldownStore
from telegram import (
    send_approach_alert,
    send_mitigated_alert,
    send_new_fvg_alert,
    send_touch_alert,
    send_trade_recap,
    send_snipe_alert,
    send_v2_alert,
    send_v2_trail_update,
    send_v2_stopped,
)
from snipe import RetestTracker, build_long_snipe, build_retest_short, gate_retest_short, build_htf_fade_short
import alert_settings

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
        self.retest_tracker = RetestTracker()
        # v2 components (no-op in v1 mode)
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
        """Kronos-only path in v1; in v2 this is unused."""
        if STRATEGY_VERSION == "v2" or not KRONOS_ENABLED or kronos_client is None:
            from trade_combo import TradeSetupResult
            return TradeSetupResult(
                "SKIP: V2_MODE", False, None,
                "v2 mode — kronos disabled", None, {}, {}, source="v2_disabled",
            )
        bars_by_tf = self._timeframe_bars(zone.symbol)
        tf_bars = bars_by_tf.get(zone.tf, [])
        ohlcv = [
            {"open": float(b.open), "high": float(b.high), "low": float(b.low),
             "close": float(b.close), "volume": float(b.volume)}
            for b in tf_bars
        ]
        atr = float(getattr(zone, "atr", 0.0) or 0.0)

        htf_raw = bars_by_tf.get("4h", [])
        htf_bars = [
            {"open": float(b.open), "high": float(b.high), "low": float(b.low),
             "close": float(b.close), "volume": float(b.volume)}
            for b in htf_raw
        ] if htf_raw else None

        kronos = await kronos_client.predict(
            bars=ohlcv,
            current_price=float(current_price),
            atr=atr,
            zone_direction=int(zone.direction),
            symbol=zone.symbol,
            tf=zone.tf,
            htf_bars=htf_bars,
        )
        if kronos is not None:
            setup = build_trade_from_kronos(kronos, zone)
            # HTF fade short: Kronos forced RANGING by 4h hard gate on bullish FVG
            # → exploit the same OB signal as a SHORT fade setup
            htf_note = kronos.get("htf_note", "")
            htf_rsi7 = kronos.get("htf_rsi7")
            if (
                not setup.valid
                and kronos.get("direction") == "RANGING"
                and "hard_gate" in htf_note
                and int(zone.direction) == 1
                and htf_rsi7 is not None
            ):
                fade = build_htf_fade_short(zone, float(current_price), htf_rsi7)
                if fade is not None:
                    setup = fade
                    logger.info(
                        "HTF fade short %s %s | 4h_rsi7=%.1f | %s",
                        zone.symbol, zone.tf, htf_rsi7, htf_note,
                    )
        else:
            # Kronos unavailable — skip entirely rather than fall back to combo
            from trade_combo import TradeSetupResult
            setup = TradeSetupResult(
                "SKIP: KRONOS UNAVAILABLE", False, None,
                "Kronos offline, combo path disabled",
                None, {}, {}, source="kronos",
            )

        # v2 filter — hard gate: if v2 says skip, override Kronos valid signal
        try:
            v2 = v2_decision(zone, bars_by_tf)
        except Exception as e:
            logger.warning("v2_decision failed: %s", e)
            v2 = None

        if v2 and not v2["valid"] and setup.valid:
            logger.info("v2 gate blocked %s %s | %s", zone.symbol, zone.tf, v2["reason"])
            from trade_combo import TradeSetupResult
            setup = TradeSetupResult(
                f"SKIP: {v2['reason']}", False, setup.mode,
                v2["reason"], None, {}, {}, source="v2_gate",
            )

        return setup.__class__(
            status=setup.status, valid=setup.valid, mode=setup.mode, reason=setup.reason,
            trade=setup.trade, combo_states=setup.combo_states, sparklines=setup.sparklines,
            source=setup.source, kronos_raw=getattr(setup, "kronos_raw", None),
            predicted_bars=getattr(setup, "predicted_bars", None), v2_decision=v2,
        )

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
                s = calc_strength(window, fvg, symbol=symbol, existing_zones=self.tracker.zones)
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
        )
        zone_id = f"{symbol}_{tf}_{zone.born_time}_{zone.direction}"
        self.tracker.zones[zone_id] = zone
        self.tracker._save_zones()

    def _v2_handle_trail(self, symbol: str, tf: str, bars) -> None:
        updates = self.v2_trail.on_bar_close(symbol, tf, bars)
        for u in updates:
            send_v2_trail_update(
                symbol=u.symbol, trigger_tf=u.trigger_tf,
                previous_sl=u.previous_sl, new_sl=u.new_sl, direction=u.direction,
                entry=u.entry, initial_sl=u.initial_sl,
            )
            logger.info(
                "v2 trail %s %s %s | %g -> %g",
                u.symbol, u.trigger_tf, "long" if u.direction == 1 else "short",
                u.previous_sl, u.new_sl,
            )
        # Touch-based stop on latest closed bar (use both wicks for conservative check)
        last = bars[-1]
        for probe_price in (last.low, last.high):
            stops = self.v2_trail.check_stop_hit(symbol, last_price=probe_price)
            for st in stops:
                state = self.v2_trail.get(st.signal_id)
                send_v2_stopped(
                    symbol=st.symbol, trigger_tf=state.trigger_tf if state else tf,
                    direction=st.direction, entry=state.entry if state else 0.0,
                    sl_at_stop=st.sl_at_stop, last_price=st.last_price,
                )
                logger.info("v2 stopped %s %s | sl=%g price=%g",
                            st.symbol, st.signal_id, st.sl_at_stop, st.last_price)

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
                chart_png = generate_chart(
                    bars=trigger_bars[-100:],
                    zone_top=sig.zone_top,
                    zone_bottom=sig.zone_bottom,
                    zone_direction=sig.direction,
                    symbol=symbol,
                    tf=sig.trigger_tf,
                    timeframe_bars=bars_by_tf,
                )
        except Exception as e:
            logger.warning("v2 chart render failed %s %s: %s", symbol, sig.trigger_tf, e)
        send_v2_alert(sig, timeframe_bars=bars_by_tf, chart_png=chart_png)
        logger.info(
            "v2 signal %s %s %s | score=%d entry=%g sl=%g chart=%s",
            symbol, sig.trigger_tf, sig.direction_str,
            sig.confluence_score, sig.entry, sig.sl,
            "yes" if chart_png else "no",
        )

    async def _on_bar_close(self, symbol: str, tf: str, bars):
        if len(bars) < 3:
            return

        # =====================================================
        # v2 Strategy Path (multi-TF touch confluence)
        # =====================================================
        if STRATEGY_VERSION == "v2":
            self.tracker.update_buffer(symbol, tf, bars)
            self.sim_store.update_open_trades(symbol, bars[-1])
            self._maybe_send_recap()
            # 1. Mitigation pass — drops fully-mitigated zones so HTF/trigger checks
            # don't include stale ones. Fixes "active forever" bug.
            self.tracker.check_mitigation(symbol, tf, bars)
            # 2. Detect & store any FVG (any strength) on this bar
            self._v2_capture_fvg(symbol, tf, bars)
            # 3. Trail bookkeeping for any open v2 trades on this trigger TF
            if tf in V2_TRIGGER_TFS:
                self._v2_handle_trail(symbol, tf, bars)
            # 4. Evaluate signal only on trigger TFs (15m / 30m)
            if tf in V2_TRIGGER_TFS:
                self._v2_try_emit_signal(symbol, tf, bars)
            return

        self.tracker.update_buffer(symbol, tf, bars)
        self.sim_store.update_open_trades(symbol, bars[-1])
        self._maybe_send_recap()

        # Check mitigation
        mitigated = self.tracker.check_mitigation(symbol, tf, bars)
        for zone in mitigated:
            send_mitigated_alert(zone)
            # Log dual shadow hypotheses (continuation vs reversal) for WR study.
            price = bars[-1].close
            breakout = build_mitigated_breakout(zone, price)
            reversal = build_mitigated_reversal(zone, price)
            self.sim_store.add_kronos_decision(zone, breakout, price, "mitigated_breakout")
            self.sim_store.add_kronos_decision(zone, reversal, price, "mitigated_reversal")
            # Features attach to the breakout decision (one snapshot per mitigation —
            # state is identical across both hypotheses; FK requires an existing
            # kronos_decisions.id, so reuse event_type='mitigated_breakout').
            self._log_features(zone, price, "mitigated_breakout")
            # Register bullish FVG for retest-short monitoring
            if int(zone.direction) == 1:
                self.retest_tracker.add(zone)

        # Check retest short on active retest zones
        retest_hit = self.retest_tracker.check(symbol, tf, bars[-1])
        if retest_hit is not None:
            price = bars[-1].close
            bars_by_tf = self._timeframe_bars(symbol)
            passed, gate_reason = gate_retest_short(bars_by_tf)
            if passed:
                snipe_setup = build_retest_short(retest_hit, price)
                if snipe_setup is not None and snipe_setup.trade is not None:
                    self.sim_store.add_sim_trade_raw(
                        symbol=symbol, tf=tf, mode="snipe",
                        direction="short", entry=snipe_setup.trade.entry,
                        sl=snipe_setup.trade.sl, tp1=snipe_setup.trade.tp1,
                        tp2=snipe_setup.trade.tp2, reason=snipe_setup.reason,
                        born_time=retest_hit.born_time,
                    )
                    if alert_settings.is_enabled("snipe_short"):
                        retest_chart = generate_chart(
                            bars=bars,
                            zone_top=retest_hit.zone_top,
                            zone_bottom=retest_hit.resistance,
                            zone_direction=-1,
                            symbol=symbol, tf=tf,
                            timeframe_bars=bars_by_tf,
                            trade_plan=snipe_setup.trade,
                        )
                        send_snipe_alert(
                            snipe_type="retest_short",
                            symbol=symbol, tf=tf,
                            trade_setup=snipe_setup,
                            chart_png=retest_chart,
                            timeframe_bars=bars_by_tf,
                        )
                    logger.info("Retest SHORT snipe %s %s | entry=%.6g", symbol, tf, price)
            else:
                logger.info("Retest SHORT snipe SKIP %s %s | %s", symbol, tf, gate_reason)

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
                if trade_setup.valid and trade_setup.source == "snipe_htf_fade":
                    self.sim_store.add_sim_trade_raw(
                        symbol=symbol, tf=tf, mode="snipe_htf_fade",
                        direction="short", entry=trade_setup.trade.entry,
                        sl=trade_setup.trade.sl, tp1=trade_setup.trade.tp1,
                        tp2=trade_setup.trade.tp2, reason=trade_setup.reason,
                        born_time=int(zone.born_time),
                    )
                    if alert_settings.is_enabled("snipe_htf_fade") and not getattr(zone, "_htf_fade_sent", False):
                        zone._htf_fade_sent = True
                        send_snipe_alert(
                            snipe_type="htf_fade", symbol=symbol, tf=tf,
                            trade_setup=trade_setup, chart_png=chart_png,
                            zone=zone, timeframe_bars=self._timeframe_bars(zone.symbol),
                        )
                        logger.info("HTF fade SHORT approach %s %s | entry=%.6g", symbol, tf, price)
                elif trade_setup.valid and alert_settings.is_enabled("approach"):
                    send_approach_alert(zone, price, chart_png=chart_png, trade_setup=trade_setup, timeframe_bars=self._timeframe_bars(zone.symbol))
                    logger.info("Approach alert %s %s | price=%s", symbol, tf, price)
                else:
                    logger.info("Approach SKIP %s %s | %s", symbol, tf, trade_setup.status)
                # Snipe long: emit limit entry at zone.bottom (one alert per zone)
                if int(zone.direction) == 1 and not getattr(zone, "_snipe_long_sent", False) and trade_setup.source != "snipe_htf_fade":
                    zone._snipe_long_sent = True
                    snipe_setup = build_long_snipe(zone)
                    if snipe_setup is not None:
                        self.sim_store.add_sim_trade_raw(
                            symbol=symbol, tf=tf, mode="snipe_long",
                            direction="long", entry=snipe_setup.trade.entry,
                            sl=snipe_setup.trade.sl, tp1=snipe_setup.trade.tp1,
                            tp2=snipe_setup.trade.tp2, reason=snipe_setup.reason,
                            born_time=int(zone.born_time),
                        )
                        if alert_settings.is_enabled("snipe_long"):
                            snipe_chart = generate_chart(
                                bars=bars,
                                zone_top=zone.top, zone_bottom=zone.bottom,
                                zone_direction=zone.direction,
                                symbol=zone.symbol, tf=zone.tf,
                                rsi_value=zone.rsi,
                                timeframe_bars=self._timeframe_bars(zone.symbol),
                                trade_plan=snipe_setup.trade,
                                predicted_bars=getattr(trade_setup, "predicted_bars", None),
                            )
                            send_snipe_alert(
                                snipe_type="long_limit", symbol=symbol, tf=tf,
                                trade_setup=snipe_setup, chart_png=snipe_chart,
                                zone=zone, timeframe_bars=self._timeframe_bars(zone.symbol),
                            )
                            logger.info("Snipe LONG approach %s %s | limit=%.6g", symbol, tf, snipe_setup.trade.entry)
            elif event["type"] == "touch":
                self.sim_store.add_kronos_decision(zone, trade_setup, price, "touch")
                self._log_features(zone, price, "touch")
                if trade_setup.valid and trade_setup.source == "snipe_htf_fade":
                    self.sim_store.add_sim_trade_raw(
                        symbol=symbol, tf=tf, mode="snipe_htf_fade",
                        direction="short", entry=trade_setup.trade.entry,
                        sl=trade_setup.trade.sl, tp1=trade_setup.trade.tp1,
                        tp2=trade_setup.trade.tp2, reason=trade_setup.reason,
                        born_time=int(zone.born_time),
                    )
                    if alert_settings.is_enabled("snipe_htf_fade") and not getattr(zone, "_htf_fade_sent", False):
                        zone._htf_fade_sent = True
                        send_snipe_alert(
                            snipe_type="htf_fade", symbol=symbol, tf=tf,
                            trade_setup=trade_setup, chart_png=chart_png,
                            zone=zone, timeframe_bars=self._timeframe_bars(zone.symbol),
                        )
                        logger.info("HTF fade SHORT touch %s %s | entry=%.6g", symbol, tf, price)
                elif trade_setup.valid and alert_settings.is_enabled("touch"):
                    send_touch_alert(zone, price, chart_png=chart_png, trade_setup=trade_setup, timeframe_bars=self._timeframe_bars(zone.symbol))
                    logger.info("Touch alert %s %s | price=%s", symbol, tf, price)
                else:
                    logger.info("Touch SKIP %s %s | %s", symbol, tf, trade_setup.status)
                # Snipe long on touch (price entering zone — refine to zone.bottom limit)
                if int(zone.direction) == 1 and not getattr(zone, "_snipe_long_sent", False) and trade_setup.source != "snipe_htf_fade":
                    zone._snipe_long_sent = True
                    snipe_setup = build_long_snipe(zone)
                    if snipe_setup is not None:
                        self.sim_store.add_sim_trade_raw(
                            symbol=symbol, tf=tf, mode="snipe_long",
                            direction="long", entry=snipe_setup.trade.entry,
                            sl=snipe_setup.trade.sl, tp1=snipe_setup.trade.tp1,
                            tp2=snipe_setup.trade.tp2, reason=snipe_setup.reason,
                            born_time=int(zone.born_time),
                        )
                        if alert_settings.is_enabled("snipe_long"):
                            snipe_chart = generate_chart(
                                bars=bars,
                                zone_top=zone.top, zone_bottom=zone.bottom,
                                zone_direction=zone.direction,
                                symbol=zone.symbol, tf=zone.tf,
                                rsi_value=zone.rsi,
                                timeframe_bars=self._timeframe_bars(zone.symbol),
                                trade_plan=snipe_setup.trade,
                                predicted_bars=getattr(trade_setup, "predicted_bars", None),
                            )
                            send_snipe_alert(
                                snipe_type="long_limit", symbol=symbol, tf=tf,
                                trade_setup=snipe_setup, chart_png=snipe_chart,
                                zone=zone, timeframe_bars=self._timeframe_bars(zone.symbol),
                            )
                            logger.info("Snipe LONG touch %s %s | limit=%.6g", symbol, tf, snipe_setup.trade.entry)

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

            if trade_setup.valid and trade_setup.source == "snipe_htf_fade":
                self.sim_store.add_sim_trade_raw(
                    symbol=symbol, tf=tf, mode="snipe_htf_fade",
                    direction="short", entry=trade_setup.trade.entry,
                    sl=trade_setup.trade.sl, tp1=trade_setup.trade.tp1,
                    tp2=trade_setup.trade.tp2, reason=trade_setup.reason,
                    born_time=int(new_zone.born_time),
                )
                if alert_settings.is_enabled("snipe_htf_fade"):
                    send_snipe_alert(
                        snipe_type="htf_fade", symbol=symbol, tf=tf,
                        trade_setup=trade_setup, chart_png=chart_png,
                        zone=new_zone, timeframe_bars=self._timeframe_bars(new_zone.symbol),
                    )
                    logger.info("HTF fade SHORT new_fvg %s %s | entry=%.6g", symbol, tf, price)
                else:
                    logger.info("HTF fade SKIP (disabled) %s %s", symbol, tf)
            elif trade_setup.valid and alert_settings.is_enabled("new_fvg"):
                send_new_fvg_alert(new_zone, chart_png=chart_png, trade_setup=trade_setup, timeframe_bars=self._timeframe_bars(new_zone.symbol))
                logger.info(
                    "New FVG alert %s %s | strength=%d rsi=%s",
                    symbol, tf, new_zone.main_strength, new_zone.rsi,
                )
            else:
                logger.info("New FVG SKIP %s %s | %s", symbol, tf, trade_setup.status)

    async def _v2_backfill_when_warm(self) -> None:
        """Periodically backfill zones from WS warm-up buffers until coverage
        stabilises. Idempotent (zone_id dedup) so safe to re-fire.

        Why a loop: cold-start REST may be 418-banned for many minutes per
        symbol — a single fixed deadline hits empty/partial buffers and never
        retries. Continuous polling keeps registering newly-warmed keys as the
        REST fetch grinds through the fleet.
        """
        if STRATEGY_VERSION != "v2":
            return
        from config import SYMBOLS
        target = len(SYMBOLS) * len(TIMEFRAMES)
        full_threshold = max(1, int(target * 0.95))
        hard_deadline = time.time() + 60 * 60  # 60 min absolute cap
        last_count = -1
        stable_since: float | None = None
        # Wait briefly for any buffers to populate before first scan
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
        # start_command_poller() — disabled: dedicated telegram_bot handles all commands
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
