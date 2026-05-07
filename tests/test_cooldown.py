import time
from cooldown import CooldownStore


def test_first_signal_passes():
    cd = CooldownStore(window_sec=60)
    assert cd.allow("BTCUSDT", "long") is True


def test_second_signal_within_window_blocked():
    cd = CooldownStore(window_sec=60)
    assert cd.allow("BTCUSDT", "long") is True
    assert cd.allow("BTCUSDT", "long") is False


def test_signal_after_window_passes():
    cd = CooldownStore(window_sec=1)
    assert cd.allow("BTCUSDT", "long") is True
    time.sleep(1.1)
    assert cd.allow("BTCUSDT", "long") is True


def test_different_direction_independent():
    cd = CooldownStore(window_sec=60)
    assert cd.allow("BTCUSDT", "long") is True
    assert cd.allow("BTCUSDT", "short") is True


def test_different_symbol_independent():
    cd = CooldownStore(window_sec=60)
    assert cd.allow("BTCUSDT", "long") is True
    assert cd.allow("ETHUSDT", "long") is True
