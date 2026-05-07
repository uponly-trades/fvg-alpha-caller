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
        atr=120.0,
        confluence_score=3,
        htf_touches={"1h": False, "2h": False, "4h": True},
        indicators={"stoch_rsi_15m": 23.0, "vol_change_pct": 18.0},
    )
    assert sig.direction == 1
    assert sig.trigger_tf == "15m"
    assert sig.confluence_score == 3
    assert sig.htf_touches["4h"] is True


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
    return FVGZone(
        symbol=symbol, tf=tf, direction=direction,
        top=top, bottom=bottom, size=top - bottom,
        born_time=born_time, atr=atr_val,
    )


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
    bars_by_tf = {"1h": [], "2h": [], "4h": []}
    score, touches = _compute_htf_confluence(zones, "BTCUSDT", direction=1, bars_by_tf=bars_by_tf)
    assert score == 0
    assert touches == {"1h": False, "2h": False, "4h": False}


def test_confluence_only_4h_touched_returns_score_3():
    z_4h = make_zone(tf="4h", direction=1, top=100.5, bottom=99.5)
    zones = {"a": z_4h}
    bars_4h = [make_bar(i, 100, 101, 99, 100.0) for i in range(1, 25)]
    bars_4h.append(make_bar(25, 100, 100.6, 99.4, 100.0))
    bars_by_tf = {"1h": [], "2h": [], "4h": bars_4h}
    score, touches = _compute_htf_confluence(zones, "BTCUSDT", direction=1, bars_by_tf=bars_by_tf)
    assert score == 3
    assert touches == {"1h": False, "2h": False, "4h": True}


def test_confluence_all_three_touched_returns_score_6():
    bars_touch = [make_bar(i, 100, 101, 99, 100.0) for i in range(1, 24)]
    bars_touch.append(make_bar(24, 100, 100.6, 99.4, 100.0))
    z_1h = make_zone(tf="1h", direction=1, top=100.5, bottom=99.5, born_time=100)
    z_2h = make_zone(tf="2h", direction=1, top=100.5, bottom=99.5, born_time=200)
    z_4h = make_zone(tf="4h", direction=1, top=100.5, bottom=99.5, born_time=300)
    zones = {"a": z_1h, "b": z_2h, "c": z_4h}
    bars_by_tf = {"1h": bars_touch, "2h": bars_touch, "4h": bars_touch}
    score, touches = _compute_htf_confluence(zones, "BTCUSDT", direction=1, bars_by_tf=bars_by_tf)
    assert score == 6
    assert touches == {"1h": True, "2h": True, "4h": True}


def test_confluence_direction_filters():
    bars_touch = [make_bar(i, 100, 101, 99, 100.0) for i in range(1, 24)]
    bars_touch.append(make_bar(24, 100, 100.6, 99.4, 100.0))
    z = make_zone(tf="1h", direction=1, top=100.5, bottom=99.5)
    zones = {"a": z}
    bars_by_tf = {"1h": bars_touch, "2h": [], "4h": []}
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


from strategy_v2 import _compute_sl


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


def test_eval_no_15m_no_30m_zones_returns_none():
    zones = {}
    bars_by_tf = {tf: [make_bar(i, 100, 101, 99, 100.0) for i in range(1, 25)] for tf in ("15m", "30m", "1h", "2h", "4h")}
    sig = evaluate_v2_signal("BTCUSDT", zones, bars_by_tf)
    assert sig is None


def test_eval_15m_touched_no_htf_confluence_returns_none():
    z_15m = make_zone(tf="15m", direction=1, top=100.5, bottom=99.5, atr_val=1.0)
    zones = {"a": z_15m}
    bars_by_tf = {
        "15m": _bars_at_zone(z_15m),
        "30m": _bars_far_from_zone(z_15m),
        "1h": _bars_far_from_zone(z_15m),
        "2h": _bars_far_from_zone(z_15m),
        "4h": _bars_far_from_zone(z_15m),
    }
    sig = evaluate_v2_signal("BTCUSDT", zones, bars_by_tf)
    assert sig is None


def test_eval_15m_touched_with_4h_confluence_returns_long_signal():
    z_15m = make_zone(tf="15m", direction=1, top=100.5, bottom=99.5, atr_val=1.0)
    z_4h = make_zone(tf="4h", direction=1, top=100.5, bottom=99.5, atr_val=1.0)
    zones = {"a": z_15m, "b": z_4h}
    bars_at = _bars_at_zone(z_15m)
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
    assert sig.confluence_score == 3
    assert sig.htf_touches["4h"] is True
    assert sig.htf_touches["1h"] is False


def test_eval_30m_fallback_when_no_15m_zone():
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
    assert sig is not None
    assert sig.trigger_tf == "30m"
    assert sig.confluence_score == 1


def test_eval_short_mirror():
    z_15m = make_zone(tf="15m", direction=-1, top=100.5, bottom=99.5, atr_val=1.0)
    z_4h = make_zone(tf="4h", direction=-1, top=100.5, bottom=99.5, atr_val=1.0)
    zones = {"a": z_15m, "b": z_4h}
    bars_at = _bars_at_zone(z_15m)
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
    bars_at = _bars_at_zone(z_15m)
    bars_by_tf = {tf: bars_at if tf in ("15m", "4h") else _bars_far_from_zone(z_15m)
                  for tf in ("15m", "30m", "1h", "2h", "4h")}
    sig = evaluate_v2_signal("BTCUSDT", zones, bars_by_tf)
    assert sig is not None
    assert sig.sl < z_15m.bottom
    assert abs(sig.sl - 99.2) < 1e-9
