import os
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
os.environ.setdefault("TELEGRAM_CHAT_ID", "x")
os.environ.setdefault("DATABASE_URL", "postgresql://x:x@localhost/x")

import pytest

import trade_combo


@dataclass(frozen=True)
class Bar:
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_closed: bool = True


def bars_from_closes(closes):
    return [
        Bar(
            open_time=i * 60_000,
            open=close - 0.2,
            high=close + 1.0,
            low=close - 1.0,
            close=close,
            volume=100 + i,
        )
        for i, close in enumerate(closes)
    ]


def zone(**overrides):
    data = dict(
        symbol="BTCUSDT",
        tf="15m",
        direction=1,
        top=101.0,
        bottom=99.0,
        main_strength=80,
        atr=1.0,
        born_time=123,
    )
    data.update(overrides)
    return SimpleNamespace(**data)


def aligned_bars():
    return bars_from_closes([
        100, 99, 98, 97, 96, 95, 94, 93, 92, 91,
        90, 89, 88, 87, 86, 85, 84, 83, 82, 81,
        80, 79, 78, 77, 76, 75, 76, 77, 78, 79,
        80, 81, 82, 83, 84, 85, 86, 87, 88, 89,
    ])


def overbought_bars():
    return bars_from_closes([
        50, 51, 52, 53, 54, 55, 56, 57, 58, 59,
        60, 61, 62, 63, 64, 65, 66, 67, 68, 69,
        70, 71, 72, 73, 74, 75, 74, 73, 72, 71,
        70, 69, 68, 67, 66, 65, 64, 63, 62, 61,
    ])


def test_classifies_trade_mode_by_setup_timeframe():
    assert trade_combo.classify_mode("15m") == "scalping"
    assert trade_combo.classify_mode("30m") == "scalping"
    assert trade_combo.classify_mode("1h") == "intraday"
    assert trade_combo.classify_mode("2h") == "intraday"
    assert trade_combo.classify_mode("4h") == "swing"
    assert trade_combo.classify_mode("12h") is None


def test_missing_required_indicator_data_skips_trade_setup():
    result = trade_combo.evaluate_trade_setup(
        zone(tf="15m", direction=1),
        current_price=100.0,
        bars_by_tf={"15m": bars_from_closes([1, 2, 3])},
    )

    assert result.status == "SKIP: MISSING DATA"
    assert result.valid is False
    assert result.mode == "scalping"
    assert result.trade is None


def test_bullish_fvg_with_aligned_combo_builds_long_risk_plan(monkeypatch):
    monkeypatch.setattr(trade_combo, "_latest_stoch_state", lambda bars, direction: ("long", []))

    result = trade_combo.evaluate_trade_setup(
        zone(tf="15m", direction=1, top=101.0, bottom=99.0, atr=1.0),
        current_price=100.0,
        bars_by_tf={"15m": aligned_bars(), "30m": aligned_bars(), "1h": aligned_bars()},
    )

    assert result.status == "LONG VALID"
    assert result.valid is True
    assert result.mode == "scalping"
    assert result.trade.direction == "long"
    assert result.trade.entry == 100.0
    assert result.trade.sl == pytest.approx(98.9)
    assert result.trade.tp1 == pytest.approx(101.1)
    assert result.trade.tp2 == pytest.approx(102.2)
    assert result.trade.rr == 2.0


def test_bearish_fvg_with_aligned_combo_builds_short_risk_plan(monkeypatch):
    monkeypatch.setattr(trade_combo, "_latest_stoch_state", lambda bars, direction: ("short", []))

    result = trade_combo.evaluate_trade_setup(
        zone(tf="1h", direction=-1, top=101.0, bottom=99.0, atr=1.0),
        current_price=100.0,
        bars_by_tf={
            "15m": overbought_bars(),
            "30m": overbought_bars(),
            "1h": overbought_bars(),
            "2h": overbought_bars(),
            "4h": overbought_bars(),
        },
    )

    assert result.status == "SHORT VALID"
    assert result.valid is True
    assert result.mode == "intraday"
    assert result.trade.direction == "short"
    assert result.trade.entry == 100.0
    assert result.trade.sl == pytest.approx(101.1)
    assert result.trade.tp1 == pytest.approx(98.9)
    assert result.trade.tp2 == pytest.approx(97.8)


def test_mixed_combo_skips_trade(monkeypatch):
    # 5 TFs now computed (15m/30m/1h/2h/4h); scalping required = 15m/30m/1h
    states = iter(["long", "short", "neutral", "neutral", "neutral"])
    monkeypatch.setattr(trade_combo, "_latest_stoch_state", lambda bars, direction: (next(states), []))

    result = trade_combo.evaluate_trade_setup(
        zone(tf="15m", direction=1),
        current_price=100.0,
        bars_by_tf={"15m": aligned_bars(), "30m": aligned_bars(), "1h": aligned_bars()},
    )

    assert result.status == "SKIP: MIXED COMBO"
    assert result.valid is False


def test_far_from_fvg_skips_trade(monkeypatch):
    monkeypatch.setattr(trade_combo, "_latest_stoch_state", lambda bars, direction: ("long", []))

    result = trade_combo.evaluate_trade_setup(
        zone(tf="15m", direction=1, top=101.0, bottom=99.0),
        current_price=104.5,
        bars_by_tf={"15m": aligned_bars(), "30m": aligned_bars(), "1h": aligned_bars()},
    )

    assert result.status == "SKIP: FAR FROM FVG"


def test_weak_fvg_skips_before_combo_validation():
    result = trade_combo.evaluate_trade_setup(
        zone(tf="15m", direction=1, main_strength=20),
        current_price=100.0,
        bars_by_tf={},
    )

    assert result.status == "SKIP: WEAK FVG"


def test_model_long_re_anchors_sl_below_zone_bottom():
    # Regression: ZECUSDT-style. Model predicted SL is tighter (closer to entry)
    # than zone-anchored SL because predictor has no zone awareness. SL must widen
    # to (zone.bottom - 0.1*atr); TPs preserved verbatim → RR stretches.
    model = {
        "direction": "LONG", "timeframe": "SCALPING",
        "entry": 580.06, "sl": 572.053, "tp1": 584.06, "tp2": 588.06,
        "confidence": 70,
    }
    z = zone(direction=1, top=575.0, bottom=565.0, atr=2.0)
    result = trade_combo.build_trade_from_model(model, z)

    assert result.status == "LONG VALID"
    assert result.valid is True
    # zone_sl = 565.0 - 0.1*2.0 = 564.8; model_sl 572.053 sits inside zone → widen.
    assert result.trade.sl == pytest.approx(564.8)
    assert result.trade.tp1 == pytest.approx(584.06)
    assert result.trade.tp2 == pytest.approx(588.06)
    # RR no longer 2.0 — model TP preserved on widened risk.
    expected_rr = (588.06 - 580.06) / (580.06 - 564.8)
    assert result.trade.rr == pytest.approx(expected_rr)


def test_model_long_keeps_tighter_model_sl_when_already_below_zone():
    # When model SL is already below the zone-anchored SL, keep model SL.
    # min(model_sl=98.0, zone_sl=98.9) → 98.0.
    model = {
        "direction": "LONG", "timeframe": "INTRADAY",
        "entry": 100.0, "sl": 98.0, "tp1": 102.0, "tp2": 104.0,
        "confidence": 80,
    }
    z = zone(direction=1, top=100.5, bottom=99.0, atr=1.0)
    result = trade_combo.build_trade_from_model(model, z)
    assert result.trade.sl == pytest.approx(98.0)
    assert result.trade.rr == pytest.approx(2.0)


def test_model_short_routes_to_combo_path():
    # SHORT model signal must defer to combo (bars-aware filter), regardless of SL.
    model = {
        "direction": "SHORT", "timeframe": "SCALPING",
        "entry": 100.0, "sl": 101.0, "tp1": 99.0, "tp2": 98.0,
        "confidence": 70,
    }
    z = zone(direction=-1, top=101.0, bottom=99.0, atr=1.0)
    result = trade_combo.build_trade_from_model(model, z)
    assert result.status == "SKIP: SHORT VIA COMBO"
    assert result.valid is False


def test_invalid_risk_skips_trade(monkeypatch):
    monkeypatch.setattr(trade_combo, "_latest_stoch_state", lambda bars, direction: ("long", []))

    result = trade_combo.evaluate_trade_setup(
        zone(tf="15m", direction=1, top=101.0, bottom=100.0, atr=0.0),
        current_price=99.0,
        bars_by_tf={"15m": aligned_bars(), "30m": aligned_bars(), "1h": aligned_bars()},
    )

    assert result.status == "SKIP: INVALID RISK"
