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
