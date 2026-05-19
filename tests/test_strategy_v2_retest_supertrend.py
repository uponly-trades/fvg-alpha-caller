import os

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
os.environ.setdefault("TELEGRAM_CHAT_ID", "x")
import pytest

import strategy_v2
from fvg_engine import FVGZone
from rest_client import Bar
from strategy_v2 import (
    _fvg_retest_decision,
    _pine_zone_quality,
    _supertrend_recovery_state,
    evaluate_v2_signal,
)


def bar(t, o, h, l, c, v=100.0):
    return Bar(open_time=t, open=o, high=h, low=l, close=c, volume=v, is_closed=True)


def zone(direction=1, top=100.0, bottom=98.0, tf="15m"):
    z = FVGZone(
        symbol="BTCUSDT", tf=tf, direction=direction,
        top=top, bottom=bottom, size=top-bottom, born_time=1, atr=1.0,
    )
    z.volume_score = 1.2
    z.main_strength = 70
    if direction == 1:
        z.bull_strength = 70
        z.fvg_buy_volume = 120
        z.fvg_sell_volume = 80
    else:
        z.bear_strength = 70
        z.fvg_buy_volume = 80
        z.fvg_sell_volume = 120
    return z


def bullish_retest_bars():
    # Close sequence builds bullish SuperTrend; penultimate bar is prior touch,
    # latest bar retests inside zone and closes above top.
    bars = [bar(i, 100+i*0.1, 101+i*0.1, 99+i*0.1, 100.5+i*0.1) for i in range(1, 24)]
    bars.append(bar(24, 101.0, 101.5, 99.2, 100.4))   # first/prior touch
    bars.append(bar(25, 100.6, 101.2, 99.0, 100.8))   # retest + reclaim
    return bars


def bearish_retest_bars():
    bars = [bar(i, 105-i*0.1, 106-i*0.1, 104-i*0.1, 104.5-i*0.1) for i in range(1, 24)]
    bars.append(bar(24, 97.0, 98.8, 96.0, 97.8))      # first/prior touch
    bars.append(bar(25, 97.4, 99.0, 96.8, 97.2))      # retest + reject below bottom
    return bars


def test_retest_requires_prior_touch():
    z = zone(direction=1)
    # latest bar reclaims, but no prior touch exists
    bars = [bar(i, 105, 106, 104, 105) for i in range(1, 24)]
    bars.append(bar(24, 100.6, 101.2, 99.0, 100.8))
    d = _fvg_retest_decision(z, bars)
    assert d.valid is False
    assert d.reason == "no_prior_touch"


def test_retest_prior_touch_must_be_after_fvg_birth():
    z = zone(direction=1)
    z.born_time = 20
    bars = [bar(i, 100.6, 101.2, 99.0, 100.8) for i in range(1, 20)]
    bars.append(bar(21, 105, 106, 104, 105))
    bars.append(bar(22, 100.6, 101.2, 99.0, 100.8))

    d = _fvg_retest_decision(z, bars)

    assert d.valid is False
    assert d.reason == "no_prior_touch"


def test_retest_accepts_after_prior_touch_bullish():
    d = _fvg_retest_decision(zone(direction=1), bullish_retest_bars())
    assert d.valid is True
    assert d.reason == "valid"
    assert d.confirmation_close == pytest.approx(100.8)


def test_retest_rejects_when_mitigation_too_deep_for_pine_rule():
    z = zone(direction=1, top=100.0, bottom=98.0)
    bars = bullish_retest_bars()
    bars[-1] = bar(25, 100.6, 101.2, 98.3, 100.8)  # depth=0.85 > max 0.75
    d = _fvg_retest_decision(z, bars, max_depth=0.75)
    assert d.valid is False
    assert d.reason == "retest_too_deep"


def test_supertrend_recovery_state_identifies_basic_trend():
    up = [bar(i, 100+i, 101+i, 99+i, 100.5+i) for i in range(1, 40)]
    down = [bar(i, 140-i, 141-i, 139-i, 139.5-i) for i in range(1, 40)]
    assert _supertrend_recovery_state(up).trend == 1
    assert _supertrend_recovery_state(down).trend == -1


def test_evaluate_signal_uses_15m_retest_without_htf_confluence(monkeypatch):
    monkeypatch.setattr(strategy_v2, "V2_HTF_OBSTACLE_FILTER_ENABLED", False)
    monkeypatch.setattr(strategy_v2, "V2_REQUIRE_SUPERTREND_FILTER", True)

    sig = evaluate_v2_signal(
        "BTCUSDT",
        {"z15": zone(direction=1), "z4h": zone(direction=1, tf="4h")},
        {"15m": bullish_retest_bars(), "1h": [], "4h": []},
    )
    assert sig is not None
    assert sig.direction == 1
    assert sig.trigger_tf == "15m"
    assert sig.indicators["entry_trigger"] == "retest"
    assert sig.indicators["supertrend_trend"] == 1


def test_evaluate_signal_rejects_supertrend_misalignment(monkeypatch):
    monkeypatch.setattr(strategy_v2, "V2_HTF_OBSTACLE_FILTER_ENABLED", False)
    monkeypatch.setattr(strategy_v2, "V2_REQUIRE_SUPERTREND_FILTER", True)
    monkeypatch.setattr(strategy_v2, "_supertrend_recovery_state", lambda bars: strategy_v2.SuperTrendState(trend=-1, band=99.0, switch_price=101.0))

    sig = evaluate_v2_signal(
        "BTCUSDT",
        {"z15": zone(direction=1)},
        {"15m": bullish_retest_bars()},
    )
    assert sig is None


def test_evaluate_signal_rejects_short_when_supertrend_is_bullish(monkeypatch):
    monkeypatch.setattr(strategy_v2, "V2_HTF_OBSTACLE_FILTER_ENABLED", False)
    monkeypatch.setattr(strategy_v2, "V2_REQUIRE_SUPERTREND_FILTER", True)
    monkeypatch.setattr(
        strategy_v2,
        "_supertrend_recovery_state",
        lambda bars: strategy_v2.SuperTrendState(trend=1, band=98.0, switch_price=97.0),
    )

    sig = evaluate_v2_signal(
        "BTCUSDT",
        {"z15": zone(direction=-1, top=100.0, bottom=98.0)},
        {"15m": bearish_retest_bars()},
    )
    assert sig is None


def test_evaluate_signal_accepts_short_when_supertrend_is_bearish(monkeypatch):
    monkeypatch.setattr(strategy_v2, "V2_HTF_OBSTACLE_FILTER_ENABLED", False)
    monkeypatch.setattr(strategy_v2, "V2_REQUIRE_SUPERTREND_FILTER", True)
    monkeypatch.setattr(
        strategy_v2,
        "_supertrend_recovery_state",
        lambda bars: strategy_v2.SuperTrendState(trend=-1, band=100.5, switch_price=97.0),
    )

    sig = evaluate_v2_signal(
        "BTCUSDT",
        {"z15": zone(direction=-1, top=100.0, bottom=98.0)},
        {"15m": bearish_retest_bars()},
    )
    assert sig is not None
    assert sig.direction == -1
    assert sig.indicators["supertrend_trend"] == -1


def test_evaluate_signal_supertrend_exit_uses_entry_placeholders(monkeypatch):
    monkeypatch.setattr(strategy_v2, "V2_HTF_OBSTACLE_FILTER_ENABLED", False)
    monkeypatch.setattr(strategy_v2, "V2_REQUIRE_SUPERTREND_FILTER", True)
    monkeypatch.setattr(
        strategy_v2,
        "_supertrend_recovery_state",
        lambda bars: strategy_v2.SuperTrendState(trend=1, band=97.5, switch_price=101.0),
    )

    sig = evaluate_v2_signal(
        "BTCUSDT",
        {"z15": zone(direction=1)},
        {"15m": bullish_retest_bars()},
    )

    assert sig is not None
    assert sig.sl == pytest.approx(97.5)
    assert sig.tp == pytest.approx(sig.entry)
    assert sig.indicators["tp1"] == pytest.approx(sig.entry)
    assert sig.indicators["sl_mode"] == "supertrend_band"
    assert sig.indicators["tp_mode"] == "supertrend_exit"


def test_visible_zone_quality_matches_pine_score_not_gap_over_atr():
    older_micro = zone(direction=1, top=0.0590, bottom=0.0589)
    older_micro.born_time = 1_000_000
    older_micro.size = 0.0001
    older_micro.atr = 0.0001
    older_micro.volume_score = 1.0
    older_micro.trend_score = 0.5
    older_micro.mitigation = 0.0

    recent_volume_gap = zone(direction=-1, top=0.0602, bottom=0.0599)
    recent_volume_gap.born_time = 1_000_000 + 6 * 900_000
    recent_volume_gap.size = 0.0003
    recent_volume_gap.atr = 0.0007
    recent_volume_gap.volume_score = 6.15
    recent_volume_gap.trend_score = 1.0
    recent_volume_gap.mitigation = 0.0

    now_ms = 1_000_000 + 7 * 900_000
    assert _pine_zone_quality(recent_volume_gap, now_ms) > _pine_zone_quality(older_micro, now_ms)


def test_evaluate_signal_uses_pine_visible_rank_before_birth_time(monkeypatch):
    monkeypatch.setattr(strategy_v2, "V2_HTF_OBSTACLE_FILTER_ENABLED", False)
    monkeypatch.setattr(strategy_v2, "V2_REQUIRE_SUPERTREND_FILTER", True)
    monkeypatch.setattr(
        strategy_v2,
        "_supertrend_recovery_state",
        lambda bars: strategy_v2.SuperTrendState(trend=1, band=97.5, switch_price=101.0),
    )

    high_rank = zone(direction=1, top=100.0, bottom=98.0)
    high_rank.born_time = 1_000
    high_rank.volume_score = 6.0
    high_rank.trend_score = 1.0
    high_rank.size = 2.0
    high_rank.atr = 1.0

    newer_low_rank = zone(direction=1, top=101.0, bottom=100.9)
    newer_low_rank.born_time = 2_000
    newer_low_rank.volume_score = 0.1
    newer_low_rank.trend_score = 0.0
    newer_low_rank.size = 0.1
    newer_low_rank.atr = 1.0

    bars = bullish_retest_bars()
    sig = evaluate_v2_signal(
        "BTCUSDT",
        {"high_rank": high_rank, "newer_low_rank": newer_low_rank},
        {"15m": bars},
    )

    assert sig is not None
    assert sig.zone_top == pytest.approx(high_rank.top)
    assert sig.zone_bottom == pytest.approx(high_rank.bottom)
