import pytest
from strategy_v2 import V2Signal


def test_v2signal_fields_present():
    sig = V2Signal(
        symbol="BTCUSDT",
        direction=1,
        trigger_tf="15m",
        zone_top=67500.0,
        zone_bottom=67200.0,
        zone_born_time=1714915200000,
        entry=67250.0,
        sl=66890.0,
        tp=67970.0,
        atr=120.0,
        confluence_score=3,
        htf_touches={"30m": False, "1h": False, "2h": False, "4h": True},
        indicators={"stoch_rsi_15m": 23.0, "vol_change_pct": 18.0},
    )
    assert sig.direction == 1
    assert sig.trigger_tf == "15m"
    assert sig.confluence_score == 3
    assert sig.htf_touches["4h"] is True
    assert sig.tp == 67970.0


from rest_client import Bar
from fvg_engine import FVGZone


def make_bar(open_time: int, o: float, h: float, l: float, c: float, v: float = 100.0) -> Bar:
    return Bar(
        open_time=open_time, open=o, high=h, low=l, close=c, volume=v, is_closed=True,
    )


def make_zone(
    symbol: str = "BTCUSDT",
    tf: str = "15m",
    direction: int = 1,
    top: float = 100.5,
    bottom: float = 99.5,
    born_time: int = 1700000000000,
    atr_val: float = 1.0,
) -> FVGZone:
    zone = FVGZone(
        symbol=symbol, tf=tf, direction=direction,
        top=top, bottom=bottom, size=top - bottom,
        born_time=born_time, atr=atr_val,
    )
    zone.volume_score = 1.2
    if direction == 1:
        zone.fvg_buy_volume = 120.0
        zone.fvg_sell_volume = 80.0
    else:
        zone.fvg_buy_volume = 80.0
        zone.fvg_sell_volume = 120.0
    return zone


def make_bull_fvg_bars(zone_low: float = 99.5, zone_high: float = 100.5) -> list:
    """Build 3 bars where the third bar's low > first bar's high (bull FVG)."""
    return [
        make_bar(1, 98.0, 99.0, 97.5, zone_low - 0.5, 200.0),  # prev2: high=99.0
        make_bar(2, zone_low - 0.4, 102.0, zone_low - 0.6, 101.5, 500.0),  # prev1: displacement
        make_bar(3, 101.6, 102.5, zone_high + 0.1, 102.0, 300.0),  # curr: low=zone_high+0.1 > prev2.high=99
    ]


def make_bear_fvg_bars(zone_low: float = 99.5, zone_high: float = 100.5) -> list:
    """3 bars where curr.high < prev2.low (bear FVG)."""
    return [
        make_bar(1, 102.0, 102.5, zone_high + 0.5, 102.3, 200.0),  # prev2: low=zone_high+0.5
        make_bar(2, 102.0, 102.0, 99.0, 99.2, 500.0),               # prev1: displacement
        make_bar(3, 99.0, zone_low - 0.1, 98.0, 98.5, 300.0),       # curr: high=zone_low-0.1 < prev2.low
    ]


from strategy_v2 import _htf_active_and_touched


def test_htf_no_zone_returns_false():
    bars = [make_bar(i, 100, 101, 99, 100.5) for i in range(1, 25)]
    result = _htf_active_and_touched(zone=None, bars=bars, lookback=1)
    assert result is False


def test_htf_zone_present_but_not_touched_returns_false():
    bars = [make_bar(i, 200, 201, 199, 200.5) for i in range(1, 25)]
    zone = make_zone(top=100.5, bottom=99.5)
    result = _htf_active_and_touched(zone=zone, bars=bars, lookback=1)
    assert result is False


def test_htf_zone_touched_within_lookback_returns_true():
    bars = [make_bar(i, 100, 101, 99, 100.5) for i in range(1, 24)]
    bars.append(make_bar(24, 100, 100.6, 99.4, 100.0))
    zone = make_zone(top=100.5, bottom=99.5)
    result = _htf_active_and_touched(zone=zone, bars=bars, lookback=1)
    assert result is True


def test_htf_zone_touched_only_outside_lookback_returns_false():
    bars = []
    bars.append(make_bar(1, 100, 100.6, 99.4, 100.0))
    for i in range(2, 25):
        bars.append(make_bar(i, 200, 201, 199, 200.5))
    zone = make_zone(top=100.5, bottom=99.5)
    result = _htf_active_and_touched(zone=zone, bars=bars, lookback=1)
    assert result is False


def test_htf_zone_fully_mitigated_returns_false():
    """Zone fully mitigated = bottom (long) breached -> not active."""
    zone = make_zone(top=100.5, bottom=99.5, direction=1)
    zone.mitigation = 1.0
    bars = [make_bar(i, 100, 101, 99, 100.0) for i in range(1, 25)]
    result = _htf_active_and_touched(zone=zone, bars=bars, lookback=1)
    assert result is False


from strategy_v2 import _latest_active_zone


def test_latest_active_zone_returns_none_when_empty():
    assert _latest_active_zone(zones={}, symbol="BTCUSDT", tf="1h", direction=1) is None


def test_latest_active_zone_filters_by_symbol_tf_direction():
    z_match = make_zone(symbol="BTCUSDT", tf="1h", direction=1, born_time=2000)
    z_wrong_symbol = make_zone(symbol="ETHUSDT", tf="1h", direction=1, born_time=3000)
    z_wrong_tf = make_zone(symbol="BTCUSDT", tf="2h", direction=1, born_time=3000)
    z_wrong_dir = make_zone(symbol="BTCUSDT", tf="1h", direction=-1, born_time=3000)
    zones = {"a": z_match, "b": z_wrong_symbol, "c": z_wrong_tf, "d": z_wrong_dir}
    result = _latest_active_zone(zones=zones, symbol="BTCUSDT", tf="1h", direction=1)
    assert result is z_match


def test_latest_active_zone_picks_youngest():
    z_old = make_zone(symbol="BTCUSDT", tf="1h", direction=1, born_time=1000)
    z_new = make_zone(symbol="BTCUSDT", tf="1h", direction=1, born_time=2000)
    zones = {"a": z_old, "b": z_new}
    result = _latest_active_zone(zones=zones, symbol="BTCUSDT", tf="1h", direction=1)
    assert result is z_new


def test_latest_active_zone_skips_fully_mitigated():
    z_mit = make_zone(symbol="BTCUSDT", tf="1h", direction=1, born_time=2000)
    z_mit.mitigation = 1.0
    z_active = make_zone(symbol="BTCUSDT", tf="1h", direction=1, born_time=1500)
    zones = {"a": z_mit, "b": z_active}
    result = _latest_active_zone(zones=zones, symbol="BTCUSDT", tf="1h", direction=1)
    assert result is z_active


from strategy_v2 import _compute_htf_confluence


def test_confluence_no_htf_zones_returns_zero():
    zones = {}
    bars_by_tf = {"30m": [], "1h": [], "2h": [], "4h": []}
    score, touches = _compute_htf_confluence(zones, "BTCUSDT", direction=1, bars_by_tf=bars_by_tf)
    assert score == 0
    assert touches == {"30m": False, "1h": False, "2h": False, "4h": False}


def test_confluence_only_4h_touched_returns_score_1():
    z_4h = make_zone(tf="4h", direction=1, top=100.5, bottom=99.5)
    zones = {"a": z_4h}
    bars_4h = [make_bar(i, 100, 101, 99, 100.0) for i in range(1, 25)]
    bars_4h.append(make_bar(25, 100, 100.6, 99.4, 100.0))
    bars_by_tf = {"30m": [], "1h": [], "2h": [], "4h": bars_4h}
    score, touches = _compute_htf_confluence(zones, "BTCUSDT", direction=1, bars_by_tf=bars_by_tf)
    # Weights: 30m=1, 1h=1, 2h=2, 4h=3 → only 4h touched = 3.
    assert score == 3
    assert touches == {"30m": False, "1h": False, "2h": False, "4h": True}


def test_confluence_all_four_touched_returns_score_7():
    bars_touch = [make_bar(i, 100, 101, 99, 100.0) for i in range(1, 24)]
    bars_touch.append(make_bar(24, 100, 100.6, 99.4, 100.0))
    z_30m = make_zone(tf="30m", direction=1, top=100.5, bottom=99.5, born_time=50)
    z_1h = make_zone(tf="1h", direction=1, top=100.5, bottom=99.5, born_time=100)
    z_2h = make_zone(tf="2h", direction=1, top=100.5, bottom=99.5, born_time=200)
    z_4h = make_zone(tf="4h", direction=1, top=100.5, bottom=99.5, born_time=300)
    zones = {"a": z_1h, "b": z_2h, "c": z_4h, "d": z_30m}
    bars_by_tf = {"30m": bars_touch, "1h": bars_touch, "2h": bars_touch, "4h": bars_touch}
    score, touches = _compute_htf_confluence(zones, "BTCUSDT", direction=1, bars_by_tf=bars_by_tf)
    # Weighted max = 1+1+2+3 = 7.
    assert score == 7
    assert touches == {"30m": True, "1h": True, "2h": True, "4h": True}


def test_confluence_direction_filters():
    bars_touch = [make_bar(i, 100, 101, 99, 100.0) for i in range(1, 24)]
    bars_touch.append(make_bar(24, 100, 100.6, 99.4, 100.0))
    z = make_zone(tf="1h", direction=1, top=100.5, bottom=99.5)
    zones = {"a": z}
    bars_by_tf = {"30m": [], "1h": bars_touch, "2h": [], "4h": []}
    score, touches = _compute_htf_confluence(zones, "BTCUSDT", direction=-1, bars_by_tf=bars_by_tf)
    assert score == 0


from strategy_v2 import _trigger_zone_touched


def test_trigger_no_zone_returns_none():
    bars = [make_bar(i, 100, 101, 99, 100.0) for i in range(1, 5)]
    assert _trigger_zone_touched(zone=None, bars=bars) is None


def test_trigger_zone_not_touched_returns_none():
    z = make_zone(top=100.5, bottom=99.5)
    bars = [make_bar(i, 200, 201, 199, 200.5) for i in range(1, 5)]
    assert _trigger_zone_touched(zone=z, bars=bars) is None


def test_trigger_zone_touched_returns_zone():
    z = make_zone(top=100.5, bottom=99.5)
    bars = [make_bar(i, 100, 101, 99, 100.0) for i in range(1, 5)]
    assert _trigger_zone_touched(zone=z, bars=bars) is z


def test_trigger_zone_fully_mitigated_returns_none():
    z = make_zone(top=100.5, bottom=99.5)
    z.mitigation = 1.0
    bars = [make_bar(i, 100, 101, 99, 100.0) for i in range(1, 5)]
    assert _trigger_zone_touched(zone=z, bars=bars) is None


from strategy_v2 import _compute_sl, _volume_confirmation, _zeiierman_quality


def test_sl_long_below_zone_bottom():
    z = make_zone(top=100.5, bottom=99.5, direction=1, atr_val=2.0)
    sl = _compute_sl(zone=z, atr_val=2.0)
    assert abs(sl - 98.9) < 1e-9


def test_sl_short_above_zone_top():
    z = make_zone(top=100.5, bottom=99.5, direction=-1, atr_val=2.0)
    sl = _compute_sl(zone=z, atr_val=2.0)
    assert abs(sl - 101.1) < 1e-9


def test_sl_uses_zone_bottom_not_wick():
    z = make_zone(top=100.5, bottom=99.5, direction=1, atr_val=2.0)
    sl = _compute_sl(zone=z, atr_val=2.0)
    assert sl < z.bottom
    assert sl > z.bottom - 5.0


def test_zeiierman_quality_uses_abs_gap_over_atr_only():
    z = make_zone(top=100.5, bottom=99.5, direction=1, atr_val=2.0)
    z.volume_score = 9.0
    z.trend_score = 1.0
    z.quality_score = 9999.0
    assert _zeiierman_quality(z) == pytest.approx(0.5)


def test_zeiierman_quality_zero_when_atr_missing():
    z = make_zone(top=100.5, bottom=99.5, direction=1, atr_val=0.0)
    assert _zeiierman_quality(z) == 0.0


def test_volume_confirmation_long_requires_spike_imbalance_and_buy_alignment():
    z = make_zone(direction=1)
    z.volume_score = 1.2
    z.fvg_buy_volume = 120.0
    z.fvg_sell_volume = 80.0
    ok, metrics = _volume_confirmation(z)
    assert ok is True
    assert metrics["fvg_volume_imbalance"] == pytest.approx(0.2)
    assert metrics["fvg_volume_aligned"] is True


def test_volume_confirmation_rejects_low_volume_score_even_when_aligned():
    z = make_zone(direction=1)
    z.volume_score = 0.9
    z.fvg_buy_volume = 140.0
    z.fvg_sell_volume = 60.0
    ok, metrics = _volume_confirmation(z)
    assert ok is False
    assert metrics["fvg_volume_aligned"] is True


def test_volume_confirmation_rejects_low_imbalance_churn():
    z = make_zone(direction=-1)
    z.volume_score = 2.0
    z.fvg_buy_volume = 101.0
    z.fvg_sell_volume = 99.0
    ok, metrics = _volume_confirmation(z)
    assert ok is False
    assert metrics["fvg_volume_imbalance"] == pytest.approx(0.01)


def test_volume_confirmation_short_requires_sell_alignment():
    z = make_zone(direction=-1)
    z.volume_score = 1.5
    z.fvg_buy_volume = 140.0
    z.fvg_sell_volume = 60.0
    ok, metrics = _volume_confirmation(z)
    assert ok is False
    assert metrics["fvg_volume_aligned"] is False


from strategy_v2 import evaluate_v2_signal


def _bars_at_zone(zone, n=25):
    """Build n closed bars all overlapping a zone."""
    bars = []
    for i in range(1, n + 1):
        bars.append(make_bar(i, zone.bottom + 0.1, zone.top + 0.1, zone.bottom - 0.1, zone.bottom + 0.2, 100.0))
    return bars


def _bars_far_from_zone(zone, n=25):
    """Build n bars far above zone (no overlap)."""
    far = zone.top + 50.0
    return [make_bar(i, far, far + 1, far - 1, far + 0.5, 100.0) for i in range(1, n + 1)]


def _bars_retest_long(zone, n=25):
    bars = [make_bar(i, 95.0 + i * 0.2, 96.0 + i * 0.2, 94.0 + i * 0.2, 95.5 + i * 0.2, 100.0) for i in range(1, n - 1)]
    bars.append(make_bar(n - 1, zone.top + 0.3, zone.top + 0.6, zone.top - zone.size * 0.25, zone.top + 0.1, 100.0))
    bars.append(make_bar(n, zone.top - 0.4, zone.top + 0.5, zone.top - zone.size * 0.5, zone.top + 0.3, 100.0))
    return bars


def _bars_retest_short(zone, n=25):
    bars = [make_bar(i, 105.0 - i * 0.2, 106.0 - i * 0.2, 104.0 - i * 0.2, 104.5 - i * 0.2, 100.0) for i in range(1, n - 1)]
    bars.append(make_bar(n - 1, zone.bottom - 0.3, zone.bottom + zone.size * 0.25, zone.bottom - 0.6, zone.bottom - 0.1, 100.0))
    bars.append(make_bar(n, zone.bottom + 0.4, zone.bottom + zone.size * 0.5, zone.bottom - 0.5, zone.bottom - 0.3, 100.0))
    return bars


def test_eval_no_15m_no_30m_zones_returns_none():
    zones = {}
    bars_by_tf = {tf: [make_bar(i, 100, 101, 99, 100.0) for i in range(1, 25)] for tf in ("15m", "30m", "1h", "2h", "4h")}
    sig = evaluate_v2_signal("BTCUSDT", zones, bars_by_tf)
    assert sig is None


def test_eval_15m_retest_triggers_without_htf_confluence():
    z_15m = make_zone(tf="15m", direction=1, top=100.5, bottom=99.5, atr_val=1.0)
    zones = {"a": z_15m}
    bars_by_tf = {
        "15m": _bars_retest_long(z_15m),
        "30m": _bars_far_from_zone(z_15m),
        "1h": _bars_far_from_zone(z_15m),
        "2h": _bars_far_from_zone(z_15m),
        "4h": _bars_far_from_zone(z_15m),
    }
    sig = evaluate_v2_signal("BTCUSDT", zones, bars_by_tf)
    assert sig is not None
    assert sig.indicators["entry_trigger"] == "retest"


def test_eval_retest_does_not_gate_by_volume_confirmation():
    z_15m = make_zone(tf="15m", direction=1, top=100.5, bottom=99.5, atr_val=1.0)
    z_15m.volume_score = 0.8
    z_15m.fvg_buy_volume = 120.0
    z_15m.fvg_sell_volume = 80.0
    z_4h = make_zone(tf="4h", direction=1, top=100.5, bottom=99.5, atr_val=1.0)
    zones = {"a": z_15m, "b": z_4h}
    bars_at = _bars_retest_long(z_15m)
    bars_by_tf = {
        "15m": bars_at,
        "30m": _bars_far_from_zone(z_15m),
        "1h": _bars_far_from_zone(z_15m),
        "2h": _bars_far_from_zone(z_15m),
        "4h": bars_at,
    }
    sig = evaluate_v2_signal("BTCUSDT", zones, bars_by_tf)
    assert sig is not None
    assert sig.indicators["entry_trigger"] == "retest"


def test_eval_15m_retest_returns_long_signal_without_htf_gate():
    z_15m = make_zone(tf="15m", direction=1, top=100.5, bottom=99.5, atr_val=1.0)
    z_4h = make_zone(tf="4h", direction=1, top=100.5, bottom=99.5, atr_val=1.0)
    zones = {"a": z_15m, "b": z_4h}
    bars_at = _bars_retest_long(z_15m)
    bars_by_tf = {
        "15m": bars_at,
        "30m": _bars_far_from_zone(z_15m),
        "1h": _bars_far_from_zone(z_15m),
        "2h": _bars_far_from_zone(z_15m),
        "4h": bars_at,
    }
    sig = evaluate_v2_signal("BTCUSDT", zones, bars_by_tf)
    assert sig is not None
    assert sig.direction == 1
    assert sig.trigger_tf == "15m"
    # HTF confluence is no longer an entry gate in retest.txt parity mode.
    assert sig.confluence_score == 0
    assert sig.htf_touches["4h"] is False
    assert sig.htf_touches["1h"] is False
    assert sig.indicators["quality_score"] == pytest.approx(z_15m.size / z_15m.atr)
    assert sig.indicators["quality_score_formula_live"] == "zeiierman_gap_atr"
    assert sig.indicators["retest_score"] >= 60
    assert sig.indicators["retest_reason"] == "valid"
    assert sig.entry == pytest.approx(bars_at[-1].close)
    # tp = entry + 2R for long
    r = abs(sig.entry - sig.sl)
    assert abs(sig.tp - (sig.entry + 2 * r)) < 1e-9


def test_eval_no_15m_zone_returns_none_even_with_30m_zone():
    """30m is HTF in v2, not a trigger TF. Only 15m bears trigger zones."""
    z_30m = make_zone(tf="30m", direction=1, top=100.5, bottom=99.5, atr_val=1.0)
    z_1h = make_zone(tf="1h", direction=1, top=100.5, bottom=99.5, atr_val=1.0)
    zones = {"a": z_30m, "b": z_1h}
    bars_at = _bars_at_zone(z_30m)
    bars_by_tf = {
        "15m": _bars_far_from_zone(z_30m),
        "30m": bars_at,
        "1h": bars_at,
        "2h": _bars_far_from_zone(z_30m),
        "4h": _bars_far_from_zone(z_30m),
    }
    sig = evaluate_v2_signal("BTCUSDT", zones, bars_by_tf)
    assert sig is None


def test_eval_short_mirror():
    z_15m = make_zone(tf="15m", direction=-1, top=100.5, bottom=99.5, atr_val=1.0)
    z_4h = make_zone(tf="4h", direction=-1, top=100.5, bottom=99.5, atr_val=1.0)
    zones = {"a": z_15m, "b": z_4h}
    bars_at = _bars_retest_short(z_15m)
    bars_by_tf = {
        "15m": bars_at, "30m": _bars_far_from_zone(z_15m),
        "1h": _bars_far_from_zone(z_15m), "2h": _bars_far_from_zone(z_15m),
        "4h": bars_at,
    }
    sig = evaluate_v2_signal("BTCUSDT", zones, bars_by_tf)
    assert sig is not None
    assert sig.direction == -1
    assert sig.sl > sig.zone_top


def test_eval_sl_below_fvg_bottom_long():
    z_15m = make_zone(tf="15m", direction=1, top=100.5, bottom=99.5, atr_val=1.0)
    z_4h = make_zone(tf="4h", direction=1, top=100.5, bottom=99.5, atr_val=1.0)
    zones = {"a": z_15m, "b": z_4h}
    bars_at = _bars_retest_long(z_15m)
    bars_by_tf = {tf: bars_at if tf in ("15m", "4h") else _bars_far_from_zone(z_15m)
                  for tf in ("15m", "30m", "1h", "2h", "4h")}
    sig = evaluate_v2_signal("BTCUSDT", zones, bars_by_tf)
    assert sig is not None
    assert sig.sl < z_15m.bottom
    assert abs(sig.sl - 99.2) < 1e-9


def test_v2_new_safety_config_imports():
    import config

    assert isinstance(config.V2_HTF_OBSTACLE_FILTER_ENABLED, bool)
    assert config.V2_HTF_OBSTACLE_TFS == ["1h", "2h", "4h"]
    assert config.V2_HTF_OBSTACLE_ATR_BUFFER >= 0
    assert config.V2_ENTRY_MODE in {"close", "touch"}
    assert isinstance(config.V2_RETEST_ENABLED, bool)
    assert 0 <= config.V2_RETEST_MAX_DEPTH <= 1
    assert config.V2_ENTRY_TRIGGER == "retest_only"
    assert config.V2_REQUIRE_PRIOR_TOUCH is True
    assert config.V2_STRONG_VOLUME_SCORE >= config.V2_NORMAL_VOLUME_SCORE
    assert 0 <= config.V2_NORMAL_VOLUME_IMBALANCE <= 1
    assert config.V2_STRONG_VOLUME_IMBALANCE >= config.V2_NORMAL_VOLUME_IMBALANCE
    assert 0 < config.TRADE_MARGIN_USAGE_CAP <= 1


from strategy_v2 import _htf_obstacle_decision


def test_long_blocked_when_bearish_htf_fvg_contains_entry():
    zones = {"bear_1h": make_zone(tf="1h", direction=-1, top=105.0, bottom=100.0)}

    decision = _htf_obstacle_decision(
        symbol="BTCUSDT", direction=1, entry=102.0, tp=108.0,
        zones=zones, obstacle_tfs=["1h", "2h", "4h"], atr_buffer_mult=0.0,
    )

    assert decision.blocked is True
    assert decision.reason == "entry_inside_opposite_htf_fvg"
    assert decision.blocking_tf == "1h"
    assert decision.blocking_direction == -1


def test_long_blocked_when_bearish_htf_fvg_intersects_path_to_tp():
    zones = {"bear_4h": make_zone(tf="4h", direction=-1, top=106.0, bottom=104.0)}

    decision = _htf_obstacle_decision(
        symbol="BTCUSDT", direction=1, entry=100.0, tp=108.0,
        zones=zones, obstacle_tfs=["1h", "2h", "4h"], atr_buffer_mult=0.0,
    )

    assert decision.blocked is True
    assert decision.reason == "tp_path_blocked_by_opposite_htf_fvg"
    assert decision.blocking_tf == "4h"


def test_short_blocked_when_bullish_htf_fvg_intersects_path_to_tp():
    zones = {"bull_2h": make_zone(tf="2h", direction=1, top=96.0, bottom=94.0)}

    decision = _htf_obstacle_decision(
        symbol="BTCUSDT", direction=-1, entry=100.0, tp=92.0,
        zones=zones, obstacle_tfs=["1h", "2h", "4h"], atr_buffer_mult=0.0,
    )

    assert decision.blocked is True
    assert decision.reason == "tp_path_blocked_by_opposite_htf_fvg"
    assert decision.blocking_tf == "2h"
    assert decision.blocking_direction == 1


def test_same_direction_htf_fvg_does_not_block():
    zones = {"bull_1h": make_zone(tf="1h", direction=1, top=105.0, bottom=100.0)}

    decision = _htf_obstacle_decision(
        symbol="BTCUSDT", direction=1, entry=100.0, tp=108.0,
        zones=zones, obstacle_tfs=["1h", "2h", "4h"], atr_buffer_mult=0.0,
    )

    assert decision.blocked is False
    assert decision.reason == "clear"


def test_obstacle_ignores_mitigated_or_wrong_symbol_zones():
    mitigated = make_zone(symbol="BTCUSDT", tf="1h", direction=-1, top=105.0, bottom=100.0)
    mitigated.mitigation = 1.0
    wrong_symbol = make_zone(symbol="ETHUSDT", tf="1h", direction=-1, top=105.0, bottom=100.0)
    zones = {"mitigated": mitigated, "wrong_symbol": wrong_symbol}

    decision = _htf_obstacle_decision(
        symbol="BTCUSDT", direction=1, entry=102.0, tp=108.0,
        zones=zones, obstacle_tfs=["1h", "2h", "4h"], atr_buffer_mult=0.0,
    )

    assert decision.blocked is False


from strategy_v2 import _zone_touch_depth, _touch_depth_ok, _live_touch_qualifies, _fvg_strength_tier, _fvg_retest_decision


def test_zone_touch_depth_for_long_zone():
    z = make_zone(direction=1, top=100.0, bottom=98.0)
    assert _zone_touch_depth(z, 100.0) == 0.0
    assert _zone_touch_depth(z, 99.5) == 0.25
    assert _zone_touch_depth(z, 99.0) == 0.5
    assert _zone_touch_depth(z, 97.0) == 1.0


def test_zone_touch_depth_for_short_zone():
    z = make_zone(direction=-1, top=100.0, bottom=98.0)
    assert _zone_touch_depth(z, 98.0) == 0.0
    assert _zone_touch_depth(z, 98.5) == 0.25
    assert _zone_touch_depth(z, 99.0) == 0.5
    assert _zone_touch_depth(z, 101.0) == 1.0


def test_touch_depth_ok_respects_min_depth():
    long_zone = make_zone(direction=1, top=100.0, bottom=98.0)
    assert _touch_depth_ok(long_zone, touch_price=99.5, min_depth=0.25) is True
    assert _touch_depth_ok(long_zone, touch_price=99.8, min_depth=0.25) is False


def test_live_touch_qualifies_long_only_after_min_depth():
    z = make_zone(direction=1, top=100.0, bottom=98.0)
    assert _live_touch_qualifies(z, live_price=99.5, min_depth=0.25) is True
    assert _live_touch_qualifies(z, live_price=99.75, min_depth=0.25) is False
    assert _live_touch_qualifies(z, live_price=101.0, min_depth=0.25) is False


def test_live_touch_qualifies_short_only_after_min_depth():
    z = make_zone(direction=-1, top=100.0, bottom=98.0)
    assert _live_touch_qualifies(z, live_price=98.5, min_depth=0.25) is True
    assert _live_touch_qualifies(z, live_price=98.25, min_depth=0.25) is False
    assert _live_touch_qualifies(z, live_price=97.0, min_depth=0.25) is False


def test_fvg_strength_tier_strong_when_volume_and_strength_are_high():
    z = make_zone(direction=1)
    z.volume_score = 1.8
    z.fvg_buy_volume = 150.0
    z.fvg_sell_volume = 50.0
    z.main_strength = 80
    tier, metrics = _fvg_strength_tier(z)
    assert tier == "strong"
    assert metrics["fvg_volume_imbalance"] == pytest.approx(0.5)
    assert metrics["fvg_volume_aligned"] is True


def test_fvg_strength_tier_weak_when_directional_volume_is_wrong():
    z = make_zone(direction=1)
    z.volume_score = 2.0
    z.fvg_buy_volume = 40.0
    z.fvg_sell_volume = 160.0
    z.main_strength = 90
    tier, metrics = _fvg_strength_tier(z)
    assert tier == "weak"
    assert metrics["fvg_volume_aligned"] is False


def test_fvg_retest_decision_accepts_bullish_reclaim():
    z = make_zone(direction=1, top=100.0, bottom=98.0)
    bars = _bars_retest_long(z)
    decision = _fvg_retest_decision(z, bars)
    assert decision.valid is True
    assert decision.reason == "valid"
    assert decision.touch_depth == pytest.approx(0.5)
    assert decision.confirmation_close == pytest.approx(bars[-1].close)


def test_fvg_retest_decision_rejects_plain_touch_without_reclaim():
    z = make_zone(direction=1, top=100.0, bottom=98.0)
    bars = _bars_at_zone(z)
    decision = _fvg_retest_decision(z, bars)
    assert decision.valid is False
    assert decision.reason in {"retest_too_deep", "no_bullish_reclaim"}


def test_fvg_retest_decision_accepts_bearish_reject():
    z = make_zone(direction=-1, top=100.0, bottom=98.0)
    bars = _bars_retest_short(z)
    decision = _fvg_retest_decision(z, bars)
    assert decision.valid is True
    assert decision.reason == "valid"
    assert decision.touch_depth == pytest.approx(0.5)


def test_evaluate_v2_signal_ignores_htf_obstacle_as_entry_gate(monkeypatch):
    import strategy_v2

    monkeypatch.setattr(strategy_v2, "V2_HTF_OBSTACLE_FILTER_ENABLED", True)
    monkeypatch.setattr(strategy_v2, "V2_HTF_OBSTACLE_TFS", ["1h", "2h", "4h"])
    monkeypatch.setattr(strategy_v2, "V2_HTF_OBSTACLE_ATR_BUFFER", 0.0)

    trigger = make_zone(tf="15m", direction=1, top=100.0, bottom=99.0, born_time=1000, atr_val=1.0)
    trigger.quality_score = 100.0
    trigger.volume_score = 1.8
    trigger.fvg_buy_volume = 150.0
    trigger.fvg_sell_volume = 50.0
    trigger.main_strength = 80

    same_dir_htf = make_zone(tf="1h", direction=1, top=99.5, bottom=98.5, born_time=900, atr_val=1.0)
    blocker = make_zone(tf="4h", direction=-1, top=102.0, bottom=101.0, born_time=800, atr_val=1.0)

    zones = {"trigger": trigger, "same_dir_htf": same_dir_htf, "blocker": blocker}
    bars_by_tf = {
        "15m": _bars_retest_long(trigger),
        "1h": _bars_at_zone(same_dir_htf),
        "2h": _bars_far_from_zone(trigger),
        "4h": _bars_far_from_zone(trigger),
    }

    sig = strategy_v2.evaluate_v2_signal("BTCUSDT", zones, bars_by_tf)
    assert sig is not None
    assert sig.indicators["entry_trigger"] == "retest"
