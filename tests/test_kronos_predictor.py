import os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
os.environ.setdefault("TELEGRAM_CHAT_ID", "x")
os.environ.setdefault("DATABASE_URL", "postgresql://x:x@localhost/x")

import pytest


def test_derive_direction_long():
    from kronos_service.predictor import derive_decision
    predicted = [{"open": 100, "high": 101, "low": 99, "close": c, "volume": 1000}
                 for c in [100.1, 100.3, 100.5, 100.8, 101.0, 101.2, 101.3, 101.5, 101.6, 101.8]]
    result = derive_decision(predicted, current_price=100.0, atr=1.0, zone_direction=1, entry=100.0)
    assert result["direction"] == "LONG"
    assert result["timeframe"] in ("SCALPING", "INTRADAY", "SWING")
    assert result["sl"] < result["entry"]
    assert result["tp1"] > result["entry"]
    assert result["tp2"] > result["tp1"]
    assert abs((result["tp2"] - result["entry"]) / (result["entry"] - result["sl"]) - 2.0) < 0.01
    assert 0 <= result["confidence"] <= 100


def test_derive_direction_short():
    from kronos_service.predictor import derive_decision
    predicted = [{"open": 100, "high": 101, "low": 99, "close": c, "volume": 1000}
                 for c in [99.9, 99.7, 99.4, 99.1, 98.8, 98.6, 98.5, 98.3, 98.2, 98.0]]
    result = derive_decision(predicted, current_price=100.0, atr=1.0, zone_direction=-1, entry=100.0)
    assert result["direction"] == "SHORT"
    assert result["sl"] > result["entry"]
    assert result["tp1"] < result["entry"]
    assert result["tp2"] < result["tp1"]


def test_derive_direction_ranging():
    from kronos_service.predictor import derive_decision
    predicted = [{"open": 100, "high": 101, "low": 99, "close": c, "volume": 1000}
                 for c in [100.1, 99.9, 100.0, 100.1, 99.9, 100.0, 100.1, 99.9, 100.0, 100.1]]
    result = derive_decision(predicted, current_price=100.0, atr=1.0, zone_direction=1, entry=100.0)
    assert result["direction"] == "RANGING"


def test_tp_sl_clamped_to_atr():
    from kronos_service.predictor import derive_decision
    # Huge predicted move — should clamp to 5x ATR
    predicted = [{"open": 100, "high": 200, "low": 99, "close": c, "volume": 1000}
                 for c in [101, 110, 120, 130, 140, 150, 160, 170, 180, 190]]
    result = derive_decision(predicted, current_price=100.0, atr=1.0, zone_direction=1, entry=100.0)
    risk = result["tp2"] - result["entry"]
    assert risk <= 5.0 * 1.0  # max 5x ATR


def test_tp_sl_minimum_atr():
    from kronos_service.predictor import derive_decision
    # Tiny predicted move — should floor to 0.5x ATR; direction=LONG (trend_pct > 0.3%)
    predicted = [{"open": 100, "high": 100.01, "low": 99.99, "close": c, "volume": 1000}
                 for c in [100.001, 100.002, 100.003, 100.004, 100.005, 100.006, 100.007, 100.008, 100.009, 100.5]]
    result = derive_decision(predicted, current_price=100.0, atr=1.0, zone_direction=1, entry=100.0)
    risk = result["tp2"] - result["entry"]
    assert risk >= 0.5 * 1.0  # min 0.5x ATR
