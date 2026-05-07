from rest_client import Bar
from trail_manager import TrailManager, TrailState


def make_bar(t, o, h, l, c, v=100.0):
    return Bar(open_time=t, open=o, high=h, low=l, close=c, volume=v, is_closed=True)


def test_register_creates_state():
    tm = TrailManager()
    tm.register(
        signal_id="BTCUSDT_15m_1700_1",
        symbol="BTCUSDT", trigger_tf="15m", direction=1,
        entry=100.0, sl=99.0, atr=1.0,
    )
    states = tm.snapshot()
    assert len(states) == 1
    assert states[0].symbol == "BTCUSDT"
    assert states[0].current_sl == 99.0


def test_register_duplicate_signal_id_idempotent():
    tm = TrailManager()
    tm.register(signal_id="x", symbol="BTCUSDT", trigger_tf="15m", direction=1,
                entry=100.0, sl=99.0, atr=1.0)
    tm.register(signal_id="x", symbol="BTCUSDT", trigger_tf="15m", direction=1,
                entry=100.0, sl=99.0, atr=1.0)
    assert len(tm.snapshot()) == 1


def test_long_trail_ratchets_up_on_higher_low():
    tm = TrailManager()
    tm.register(signal_id="x", symbol="BTCUSDT", trigger_tf="15m", direction=1,
                entry=100.0, sl=98.0, atr=1.0)
    bars = [
        make_bar(1, 99.5, 100.5, 99.5, 100.2),
        make_bar(2, 100.2, 100.8, 100.0, 100.5),
    ]
    updates = tm.on_bar_close("BTCUSDT", "15m", bars)
    assert len(updates) == 1
    state = tm.get("x")
    assert abs(state.current_sl - 99.2) < 1e-9
    assert updates[0].previous_sl == 98.0
    assert abs(updates[0].new_sl - 99.2) < 1e-9


def test_long_trail_does_not_lower_sl():
    tm = TrailManager()
    tm.register(signal_id="x", symbol="BTCUSDT", trigger_tf="15m", direction=1,
                entry=100.0, sl=99.5, atr=1.0)
    bars = [
        make_bar(1, 99.0, 99.5, 99.0, 99.3),
        make_bar(2, 99.3, 99.4, 99.0, 99.2),
    ]
    updates = tm.on_bar_close("BTCUSDT", "15m", bars)
    assert updates == []
    state = tm.get("x")
    assert state.current_sl == 99.5


def test_short_trail_ratchets_down_on_lower_high():
    tm = TrailManager()
    tm.register(signal_id="x", symbol="BTCUSDT", trigger_tf="15m", direction=-1,
                entry=100.0, sl=102.0, atr=1.0)
    bars = [
        make_bar(1, 100.4, 100.5, 100.0, 100.2),
        make_bar(2, 100.2, 100.3, 99.5, 99.8),
    ]
    updates = tm.on_bar_close("BTCUSDT", "15m", bars)
    assert len(updates) == 1
    state = tm.get("x")
    assert abs(state.current_sl - 100.8) < 1e-9


def test_trail_ignores_states_for_other_symbols():
    tm = TrailManager()
    tm.register(signal_id="x", symbol="ETHUSDT", trigger_tf="15m", direction=1,
                entry=100.0, sl=98.0, atr=1.0)
    bars = [make_bar(1, 99.0, 100.0, 99.0, 99.5), make_bar(2, 99.5, 100.0, 99.0, 99.8)]
    updates = tm.on_bar_close("BTCUSDT", "15m", bars)
    assert updates == []


def test_trail_ignores_states_for_other_tf():
    tm = TrailManager()
    tm.register(signal_id="x", symbol="BTCUSDT", trigger_tf="30m", direction=1,
                entry=100.0, sl=98.0, atr=1.0)
    bars = [make_bar(1, 99.0, 100.0, 99.0, 99.5), make_bar(2, 99.5, 100.0, 99.0, 99.8)]
    updates = tm.on_bar_close("BTCUSDT", "15m", bars)
    assert updates == []


def test_trail_skips_when_only_one_bar():
    tm = TrailManager()
    tm.register(signal_id="x", symbol="BTCUSDT", trigger_tf="15m", direction=1,
                entry=100.0, sl=98.0, atr=1.0)
    bars = [make_bar(1, 99.0, 100.0, 99.0, 99.5)]
    updates = tm.on_bar_close("BTCUSDT", "15m", bars)
    assert updates == []


def test_long_stop_hit_when_price_touches_sl():
    tm = TrailManager()
    tm.register(signal_id="x", symbol="BTCUSDT", trigger_tf="15m", direction=1,
                entry=100.0, sl=99.0, atr=1.0)
    stops = tm.check_stop_hit("BTCUSDT", last_price=99.0)
    assert len(stops) == 1
    assert stops[0].signal_id == "x"
    state = tm.get("x")
    assert state.closed is True


def test_long_no_stop_when_price_above_sl():
    tm = TrailManager()
    tm.register(signal_id="x", symbol="BTCUSDT", trigger_tf="15m", direction=1,
                entry=100.0, sl=99.0, atr=1.0)
    stops = tm.check_stop_hit("BTCUSDT", last_price=99.5)
    assert stops == []
    state = tm.get("x")
    assert state.closed is False


def test_short_stop_hit_when_price_at_or_above_sl():
    tm = TrailManager()
    tm.register(signal_id="x", symbol="BTCUSDT", trigger_tf="15m", direction=-1,
                entry=100.0, sl=101.0, atr=1.0)
    stops = tm.check_stop_hit("BTCUSDT", last_price=101.0)
    assert len(stops) == 1


def test_check_stop_filters_by_symbol():
    tm = TrailManager()
    tm.register(signal_id="x", symbol="ETHUSDT", trigger_tf="15m", direction=1,
                entry=100.0, sl=99.0, atr=1.0)
    stops = tm.check_stop_hit("BTCUSDT", last_price=98.0)
    assert stops == []


def test_check_stop_skips_already_closed():
    tm = TrailManager()
    tm.register(signal_id="x", symbol="BTCUSDT", trigger_tf="15m", direction=1,
                entry=100.0, sl=99.0, atr=1.0)
    tm.check_stop_hit("BTCUSDT", last_price=99.0)
    stops = tm.check_stop_hit("BTCUSDT", last_price=99.0)
    assert stops == []
