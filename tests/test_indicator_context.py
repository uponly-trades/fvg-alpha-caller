import os
import sys

import pytest
from dataclasses import dataclass
from types import SimpleNamespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
os.environ.setdefault("TELEGRAM_CHAT_ID", "x")
os.environ.setdefault("DATABASE_URL", "postgresql://x:x@localhost/x")

import indicator_context
import chart_generator
import main


@dataclass(frozen=True)
class Bar:
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_closed: bool = True


def make_bars(closes):
    bars = []
    for i, close in enumerate(closes):
        bars.append(Bar(
            open_time=i,
            open=close - 0.2,
            high=close + 1.0,
            low=close - 1.0,
            close=close,
            volume=100 + i,
        ))
    return bars


def test_rsi7_returns_latest_value_for_trending_data():
    bars = make_bars([10, 11, 12, 13, 12, 14, 15, 16, 17, 18, 17, 19, 20, 21, 22])

    ctx = indicator_context.calculate_indicator_context("15m", bars, ls_ratio=None)

    assert ctx.rsi7 > 70
    assert ctx.tf == "15m"


def test_stochrsi_and_kdj_return_cross_state():
    bars = make_bars([10, 11, 12, 13, 14, 13, 12, 11, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 20, 19, 18, 19, 20, 21, 22, 23, 24, 23, 22, 24, 25, 26, 27, 28, 29, 28, 30, 31])

    ctx = indicator_context.calculate_indicator_context("1h", bars, ls_ratio=(72.2, 27.8))

    assert ctx.stoch_k is not None
    assert ctx.stoch_d is not None
    assert ctx.stoch_state in {"bull_cross", "bear_cross", "bull", "bear", "neutral"}
    assert ctx.kdj_k is not None
    assert ctx.kdj_d is not None
    assert ctx.kdj_j is not None
    assert ctx.kdj_state in {"bull_cross", "bear_cross", "bull", "bear", "neutral"}
    assert ctx.long_pct == 72.2
    assert ctx.short_pct == 27.8


def test_build_indicator_context_formats_three_timeframes(monkeypatch):
    bars = make_bars([10, 11, 12, 13, 12, 14, 15, 16, 17, 18, 17, 19, 20, 21, 22, 23, 22, 24, 25, 26])
    buffers = {
        ("BTCUSDT", "15m"): bars,
        ("BTCUSDT", "1h"): bars,
    }

    monkeypatch.setattr(indicator_context, "fetch_long_short_ratio", lambda symbol, tf: (60.0, 40.0))

    text = indicator_context.format_indicator_context("BTCUSDT", buffers)

    assert "📊 Indicator Context" in text
    assert "15m:" in text
    assert "1h :" in text
    assert "4h : n/a" in text
    assert "StochRSI" in text
    assert "RSI7" in text
    assert "KDJ" in text
    assert "div=" in text
    assert "LS L60.0/S40.0" in text


def test_long_short_ratio_uses_binance_response_and_cache(monkeypatch):
    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return [{"longAccount": "0.7221", "shortAccount": "0.2779"}]

    def fake_get(url, params, timeout):
        calls.append((url, params, timeout))
        return Response()

    indicator_context._LS_CACHE.clear()
    monkeypatch.setattr(indicator_context.requests, "get", fake_get)

    first = indicator_context.fetch_long_short_ratio("BTCUSDT", "15m")
    second = indicator_context.fetch_long_short_ratio("BTCUSDT", "15m")

    assert first == (72.21, 27.79)
    assert second == (72.21, 27.79)
    assert len(calls) == 1
    assert calls[0][1]["period"] == "15m"



def test_chart_generator_renders_30m_1h_2h_4h_stochrsi_without_divergence(monkeypatch):
    bars = make_bars([10, 11, 12, 13, 14, 13, 12, 11, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 20, 19, 18, 19, 20, 21, 22, 23, 24, 23, 22, 24, 25, 26, 27, 28, 29, 28, 30, 31])
    divergence_calls = []

    monkeypatch.setattr(chart_generator, "_draw_divergence", lambda *args, **kwargs: divergence_calls.append(args))

    png = chart_generator.generate_chart(
        bars=bars,
        zone_top=24.0,
        zone_bottom=22.0,
        zone_direction=1,
        symbol="BTCUSDT",
        tf="15m",
        rsi_value=55.0,
        timeframe_bars={"15m": bars, "30m": bars, "1h": bars, "2h": bars, "4h": bars},
    )

    assert png is not None
    assert png.startswith(b"\x89PNG")
    # Divergence drawn once per TF RSI7 axis (5 TFs)
    assert len(divergence_calls) == 5
    assert divergence_calls[0][0].get_ylabel() == "RSI7"



def test_chart_generator_renders_indicator_panels():
    bars = make_bars([10, 11, 12, 13, 14, 13, 12, 11, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 20, 19, 18, 19, 20, 21, 22, 23, 24, 23, 22, 24, 25, 26, 27, 28, 29, 28, 30, 31])

    png = chart_generator.generate_chart(
        bars=bars,
        zone_top=24.0,
        zone_bottom=22.0,
        zone_direction=1,
        symbol="BTCUSDT",
        tf="15m",
        rsi_value=55.0,
        timeframe_bars={"15m": bars, "1h": bars, "4h": bars},
    )

    assert png is not None
    assert png.startswith(b"\x89PNG")



def test_alpha_caller_uses_ws_warmup_buffers_for_missing_timeframes():
    caller = object.__new__(main.AlphaCaller)
    fifteen = make_bars([10, 11, 12])
    one_hour = make_bars([20, 21, 22])
    four_hour = make_bars([30, 31, 32])
    caller.tracker = SimpleNamespace(buffers={("BTCUSDT", "15m"): fifteen})
    caller.poller = SimpleNamespace(_buffers={"BTCUSDT_1h": one_hour, "BTCUSDT_4h": four_hour})

    bars_by_tf = caller._timeframe_bars("BTCUSDT")
    assert bars_by_tf["15m"] == fifteen
    assert bars_by_tf["1h"] == one_hour
    assert bars_by_tf["4h"] == four_hour



def test_align_series_to_index_interpolates_higher_timeframe_values():
    target = make_bars([10, 11, 12, 13, 14, 15])
    source = [target[0], target[3]]
    target_index = chart_generator.pd.to_datetime([b.open_time for b in target], unit="ms")

    aligned = chart_generator._align_series_to_index([10.0, 80.0], source, target_index)

    assert aligned == pytest.approx([10.0, 33.33333333333333, 56.666666666666664, 80.0, 80.0, 80.0])



def test_chart_generator_renders_when_higher_timeframes_lack_stochrsi_data():
    bars = make_bars([10, 11, 12, 13, 14, 13, 12, 11, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 20, 19, 18, 19, 20, 21, 22, 23, 24, 23, 22, 24, 25, 26, 27, 28, 29, 28, 30, 31])
    short_bars = make_bars([10, 11, 12])

    png = chart_generator.generate_chart(
        bars=bars,
        zone_top=24.0,
        zone_bottom=22.0,
        zone_direction=1,
        symbol="INJUSDT",
        tf="15m",
        rsi_value=61.0,
        timeframe_bars={"15m": bars, "1h": short_bars, "4h": short_bars},
    )

    assert png is not None
    assert png.startswith(b"\x89PNG")



def test_zone_indicator_context_text_is_not_rendered_in_alert(monkeypatch):
    import telegram

    class Zone:
        direction = 1
        label = "Strong Bullish Imbalance"
        symbol = "BTCUSDT"
        tf = "15m"
        price = 100.0
        bottom = 99.0
        top = 101.0
        size = 2.0
        main_strength = 80
        bull_strength = 80
        bear_strength = 20
        rsi = 55.0
        atr = 1.2
        vol_change_pct = 10.0
        price_change_pct = 1.0
        price_change_24h_pct = 2.0
        candle_body_pct = 70.0
        dist_to_zone = 0.1
        dominance_state = "ALT"
        btc_state = "UP"
        dominance_bias = -0.01
        btc_trend = 0.01
        confirm_score = 80
        confirm_label = "A+"
        volume_spike_ratio = 2.0
        confluence_tf_count = 2
        displacement_ok = True
        btc_alignment_ok = True
        invalidated = False
        invalid_reason = ""
        sl = 98.0
        tp1 = 103.0
        tp2 = 105.0
        indicator_context = (
            "📊 Indicator Context\n"
            "15m: StochRSI 15.0/10.0 bull | RSI7 55.0 | "
            "KDJ K50.0 D45.0 J60.0 bull | LS L60.0/S40.0"
        )

    sent = {}
    monkeypatch.setattr(telegram, "_send", lambda text: sent.setdefault("text", text) or True)

    telegram.send_new_fvg_alert(Zone())

    assert "📊 Indicator Context" not in sent["text"]
    assert "StochRSI" not in sent["text"]
    assert "KDJ" not in sent["text"]
    assert "LS L60.0/S40.0" not in sent["text"]


def test_trade_plan_alert_uses_reduced_trade_content(monkeypatch):
    import telegram

    class Zone:
        direction = 1
        label = "Strong Bullish Imbalance"
        symbol = "BTCUSDT"
        tf = "30m"
        price = 100.0
        bottom = 99.0
        top = 101.0
        main_strength = 80
        atr = 1.2
        vol_change_pct = 10.0
        price_change_pct = 1.0
        price_change_24h_pct = 2.0
        dominance_state = "ALT"
        btc_state = "UP"
        dominance_bias = -0.01
        btc_trend = 0.01
        confirm_score = 80
        confirm_label = "A+"
        indicator_context = "StochRSI should not render"

    setup = SimpleNamespace(
        status="LONG VALID",
        valid=True,
        mode="scalping",
        reason="long FVG with aligned StochRSI combo",
        trade=SimpleNamespace(direction="long", entry=100.0, sl=98.9, tp1=101.1, tp2=102.2, rr=2.0),
    )
    sent = {}
    monkeypatch.setattr(telegram, "_send", lambda text: sent.setdefault("text", text) or True)

    telegram.send_new_fvg_alert(Zone(), trade_setup=setup)

    text = sent["text"]
    assert "NEW FVG | BULLISH FVG | BTCUSDT | 30m" in text
    assert "100.0000" in text
    assert "98.9" in text
    assert "101.1" in text
    assert "102.2" in text
    assert "RR" in text
    assert "Mode: scalping" in text
    assert "Zone: 99.0000 — 101.0000" in text
    assert "Confidence:" in text
    assert "80%" in text
    assert "Reason: long FVG with aligned StochRSI combo" in text
    assert "interval=30" in text
    assert "StochRSI should not render" not in text
    assert "Vol Change" not in text
    assert "BTCDOM" not in text


def test_skipped_trade_alert_renders_skip_reason(monkeypatch):
    import telegram

    zone = SimpleNamespace(direction=-1, symbol="ETHUSDT", tf="2h", bottom=99.0, top=101.0, main_strength=80)
    setup = SimpleNamespace(status="SKIP: MIXED COMBO", valid=False, mode="intraday", reason="combo timeframes are mixed", trade=None)
    sent = {}
    monkeypatch.setattr(telegram, "_send", lambda text: sent.setdefault("text", text) or True)

    telegram.send_touch_alert(zone, 100.0, trade_setup=setup)

    text = sent["text"]
    assert "SKIP: MIXED COMBO | BEARISH FVG | ETHUSDT | 2h" in text
    assert "Entry:" not in text
    assert "Skip Reason: combo timeframes are mixed" in text
    assert "interval=120" in text


def test_send_trade_recap_formats_daily_summary(monkeypatch):
    import telegram

    sent = {}
    monkeypatch.setattr(telegram, "_send", lambda text: sent.setdefault("text", text) or True)

    telegram.send_trade_recap("Siang", {
        "open": 4,
        "tp1": 2,
        "win": 1,
        "loss": 1,
        "closed_winrate": 50.0,
        "recent": [
            {"direction": "long", "symbol": "BTCUSDT", "tf": "15m", "entry": 100.0, "sl": 98.0, "tp1": 102.0, "tp2": 104.0, "status": "tp1_hit"}
        ],
    })

    text = sent["text"]
    assert "Trade Recap — Siang" in text
    assert "Open" in text and "4" in text
    assert "50.0%" in text
    assert "BTCUSDT" in text
    assert "15m" in text
    assert "TP1 HIT" in text


def test_chart_generator_draws_trade_plan_overlays(monkeypatch):
    bars = make_bars([10, 11, 12, 13, 14, 13, 12, 11, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 20, 19, 18, 19, 20, 21, 22, 23, 24, 23, 22, 24, 25, 26, 27, 28, 29, 28, 30, 31])
    hlines = []
    original_axhline = chart_generator.matplotlib.axes.Axes.axhline

    def spy_axhline(self, y=0, *args, **kwargs):
        hlines.append((y, kwargs.get("color")))
        return original_axhline(self, y=y, *args, **kwargs)

    monkeypatch.setattr(chart_generator.matplotlib.axes.Axes, "axhline", spy_axhline)

    png = chart_generator.generate_chart(
        bars=bars,
        zone_top=24.0,
        zone_bottom=22.0,
        zone_direction=1,
        symbol="BTCUSDT",
        tf="15m",
        rsi_value=55.0,
        timeframe_bars={"15m": bars, "30m": bars, "1h": bars, "2h": bars, "4h": bars},
        trade_plan=SimpleNamespace(entry=25.0, sl=21.0, tp1=27.0, tp2=29.0),
    )

    assert png is not None
    assert png.startswith(b"\x89PNG")
    assert (25.0, "#1f77b4") in hlines
    assert (21.0, "#d62728") in hlines
    assert (27.0, "#2ca02c") in hlines
    assert (29.0, "#006400") in hlines


@pytest.mark.asyncio
async def test_alpha_caller_evaluates_and_saves_valid_new_fvg_trade(monkeypatch):
    caller = object.__new__(main.AlphaCaller)
    bars = make_bars([10, 11, 12, 13])
    zone = SimpleNamespace(
        symbol="BTCUSDT",
        tf="15m",
        direction=1,
        top=12.0,
        bottom=11.0,
        rsi=55.0,
        main_strength=80,
        price=13.0,
        born_time=1777899600000,
        alerted=False,
    )
    caller.tracker = SimpleNamespace(
        buffers={},
        update_buffer=lambda symbol, tf, bars: None,
        check_mitigation=lambda symbol, tf, bars: [],
        check_interaction=lambda symbol, tf, bars: [],
        check_new_fvg=lambda symbol, tf: zone,
    )
    caller.poller = SimpleNamespace(_buffers={})
    fvg_saved = []
    sim_saved = []
    caller.sim_store = SimpleNamespace(
        update_open_trades=lambda symbol, bar: 0,
        add_fvg=lambda z, chart_path=None: fvg_saved.append(z) or True,
        add_sim_trade=lambda z, setup, created_at: sim_saved.append((z, setup, created_at)) or True,
        daily_recap=lambda date: {"open": 0, "tp1": 0, "win": 0, "loss": 0, "closed_winrate": 0.0, "recent": []},
    )
    caller._last_recap_key = None

    setup = SimpleNamespace(
        status="LONG VALID",
        valid=True,
        mode="scalping",
        reason="aligned combo",
        trade=SimpleNamespace(entry=13.0, sl=10.9, tp1=15.1, tp2=17.2),
    )
    calls = {}
    monkeypatch.setattr(main, "evaluate_trade_setup", lambda zone, current_price, bars_by_tf: calls.setdefault("setup", setup))
    monkeypatch.setattr(main, "evaluate_for_mode", lambda zone, mode, price, bars_by_tf: setup)
    monkeypatch.setattr(main, "generate_chart", lambda **kwargs: calls.setdefault("trade_plan", kwargs.get("trade_plan")) or b"png")
    monkeypatch.setattr(main, "send_new_fvg_alert", lambda zone, chart_png=None, trade_setup=None, **kwargs: calls.setdefault("sent_setup", trade_setup) or True)
    monkeypatch.setattr(main, "send_trade_recap", lambda session, recap: True)
    monkeypatch.setattr(main.AlphaCaller, "_save_chart_png", lambda self, zone, png: "/app/data/charts/test.png")

    await caller._on_bar_close("BTCUSDT", "15m", bars)

    assert zone.alerted is True
    assert calls["setup"] is setup
    assert calls["trade_plan"] is setup.trade
    assert calls["sent_setup"] is setup
    assert fvg_saved == [zone]
    # 3 modes × valid setup → 3 sim_trade saves
    assert len(sim_saved) == 3
    assert all(s[0] is zone for s in sim_saved)


def test_trade_alert_text_has_no_ascii_sparkline(monkeypatch):
    import telegram

    class Zone:
        direction = 1
        symbol = "SOLUSDT"
        tf = "15m"
        bottom = 99.0
        top = 101.0
        main_strength = 80

    setup = SimpleNamespace(
        status="LONG VALID",
        valid=True,
        mode="scalping",
        reason="aligned combo",
        trade=SimpleNamespace(direction="long", entry=100.0, sl=98.9, tp1=101.1, tp2=102.2, rr=2.0),
        sparklines={"15m": "▁▂▃▄▅▆▇▆▅▄"},
    )
    sent = {}
    monkeypatch.setattr(telegram, "_send", lambda text: sent.setdefault("text", text) or True)

    telegram.send_new_fvg_alert(Zone(), trade_setup=setup)

    text = sent["text"]
    # sparklines now only in PNG chart, not in text
    assert "<code>" not in text
    assert "▁▂▃▄▅▆▇▆▅▄" not in text
    assert "NEW FVG | BULLISH FVG | SOLUSDT | 15m" in text
    assert "100.0000" in text


def test_alpha_caller_sends_each_session_recap_once(monkeypatch):
    caller = object.__new__(main.AlphaCaller)
    caller._last_recap_key = None
    caller.sim_store = SimpleNamespace(daily_recap=lambda date: {"open": 0, "tp1": 0, "win": 0, "loss": 0, "closed_winrate": 0.0, "recent": []})
    sent = []
    monkeypatch.setattr(main, "send_trade_recap", lambda session, recap: sent.append(session) or True)

    now = main.datetime(2026, 5, 4, 12, 5, tzinfo=main.timezone.utc)
    caller._maybe_send_recap(now)
    caller._maybe_send_recap(now)

    assert sent == ["Siang"]


def test_kronos_client_fallback_on_timeout(monkeypatch):
    """When Kronos service unreachable, client returns None without raising."""
    import kronos_client
    import httpx

    async def fake_post(*a, **kw):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    import asyncio
    result = asyncio.get_event_loop().run_until_complete(
        kronos_client.predict(
            bars=[{"open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000}] * 20,
            current_price=100.0,
            atr=1.0,
            zone_direction=1,
            symbol="BTCUSDT",
            tf="15m",
        )
    )
    assert result is None


def test_kronos_client_returns_decision_on_success(monkeypatch):
    """When service returns valid JSON, client returns dict with all fields."""
    import kronos_client
    import httpx
    from unittest.mock import MagicMock

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value={
        "direction": "LONG", "timeframe": "INTRADAY",
        "entry": 100.0, "sl": 99.0, "tp1": 101.0, "tp2": 102.0, "confidence": 72,
    })

    async def fake_post(*a, **kw):
        return mock_response

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    import asyncio
    result = asyncio.get_event_loop().run_until_complete(
        kronos_client.predict(
            bars=[{"open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000}] * 20,
            current_price=100.0, atr=1.0, zone_direction=1, symbol="BTCUSDT", tf="15m",
        )
    )
    assert result is not None
    assert result["direction"] == "LONG"
    assert result["confidence"] == 72


def test_build_trade_from_kronos_long():
    from trade_combo import build_trade_from_kronos
    kronos = {"direction": "LONG", "timeframe": "INTRADAY", "entry": 100.0,
              "sl": 99.0, "tp1": 101.0, "tp2": 102.0, "confidence": 75}
    result = build_trade_from_kronos(kronos)
    assert result.status == "LONG VALID"
    assert result.valid is True
    assert result.mode == "intraday"
    assert result.trade.entry == 100.0
    assert result.trade.sl == 99.0
    assert result.trade.tp1 == 101.0
    assert result.trade.tp2 == 102.0
    assert result.trade.rr == 2.0


def test_build_trade_from_kronos_ranging():
    from trade_combo import build_trade_from_kronos
    kronos = {"direction": "RANGING", "timeframe": "SCALPING", "entry": 100.0,
              "sl": 99.5, "tp1": 100.5, "tp2": 101.0, "confidence": 30}
    result = build_trade_from_kronos(kronos)
    assert result.status == "SKIP: RANGING"
    assert result.valid is False
    assert result.trade is None


def test_new_fvg_alert_always_uses_format_trade_alert(monkeypatch):
    """send_new_fvg_alert without trade_setup uses _format_trade_alert (no old caption block)."""
    import telegram
    sent = {}
    monkeypatch.setattr(telegram, "_send", lambda text: sent.setdefault("text", text) or True)

    class Zone:
        symbol = "ETHUSDT"; tf = "1h"; direction = 1; main_strength = 65
        rsi = 48.0; price = 2500.0; top = 2510.0; bottom = 2490.0
        alerted = False; born_time = 1000000

    telegram.send_new_fvg_alert(Zone(), trade_setup=None)
    text = sent["text"]
    assert "Vol Change" not in text
    assert "BTCDOM" not in text
    assert "Confluence" not in text
    assert "ETHUSDT" in text
    assert "1h" in text


def test_alert_contains_rev_top_bottom(monkeypatch):
    """Alert includes Rev. Top / Rev. Bottom lines when pivots found."""
    import telegram
    sent = {}
    monkeypatch.setattr(telegram, "_send", lambda text: sent.setdefault("text", text) or True)
    monkeypatch.setattr(telegram, "_send_photo", lambda msg, png: sent.setdefault("text", msg) or True)

    # Bars: go up 100→120 then back down 120→100 — clear swing high at peak
    prices_up = list(range(100, 121))
    prices_down = list(range(120, 99, -1))
    prices = prices_up + prices_down
    bars = make_bars(prices)

    class Zone:
        symbol = "BTCUSDT"; tf = "1h"; direction = 1; main_strength = 80
        rsi = 45.0; price = 100.0; top = 105.0; bottom = 95.0
        alerted = False; born_time = 1000000

    from types import SimpleNamespace
    trade_setup = SimpleNamespace(
        status="LONG VALID", valid=True, mode="intraday",
        reason="Kronos long signal",
        trade=SimpleNamespace(direction="long", entry=100.0, sl=98.0, tp1=102.0, tp2=104.0, rr=2.0),
        combo_states={}, sparklines={},
    )
    timeframe_bars = {"1h": bars, "4h": bars}

    telegram.send_new_fvg_alert(Zone(), trade_setup=trade_setup, timeframe_bars=timeframe_bars)
    text = sent["text"]
    assert "Rev." in text
