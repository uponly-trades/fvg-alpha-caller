import os
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
os.environ.setdefault("TELEGRAM_CHAT_ID", "x")

import indicator_context


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
