import os
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
os.environ.setdefault("TELEGRAM_CHAT_ID", "x")
"""Unit tests for dynamic SL/TP helpers in strategy_v2.

Spec: .specify/specs/dynamic-sltp.md
"""
from dataclasses import replace
import pytest

from rest_client import Bar
from fvg_engine import FVGZone
from strategy_v2 import (
    _swings,
    _structural_sl,
    _tp_magnets,
    _structural_rr,
)


def _bar(t: int, o: float, h: float, l: float, c: float, v: float = 100.0) -> Bar:
    return Bar(open_time=t, open=o, high=h, low=l, close=c, volume=v, is_closed=True)


def _zone(top: float, bottom: float, direction: int, born_time: int = 0, atr: float = 1.0) -> FVGZone:
    return FVGZone(
        symbol="TEST", tf="15m", direction=direction,
        top=top, bottom=bottom, size=top - bottom,
        born_time=born_time, atr=atr,
    )


# -----------------------------------------------------------------------------
# R1: swing detection
# -----------------------------------------------------------------------------
def test_swings_finds_clear_pivot_high():
    # ascending then peak then descending → swing high at idx=4
    bars = [
        _bar(1, 100, 101, 99, 100),
        _bar(2, 100, 102, 100, 101),
        _bar(3, 101, 103, 101, 102),
        _bar(4, 102, 104, 102, 103),
        _bar(5, 103, 110, 103, 104),  # peak (idx=4)
        _bar(6, 104, 105, 103, 104),
        _bar(7, 104, 104, 102, 103),
    ]
    highs = _swings(bars, "high", lookback=10, fractal=2)
    assert (4, 110.0) in highs


def test_swings_finds_swing_low():
    bars = [
        _bar(1, 100, 101, 99, 100),
        _bar(2, 100, 100, 98, 99),
        _bar(3, 99, 100, 97, 98),
        _bar(4, 98, 99, 90, 97),  # bottom (idx=3)
        _bar(5, 97, 99, 96, 98),
        _bar(6, 98, 100, 97, 99),
        _bar(7, 99, 101, 98, 100),
    ]
    lows = _swings(bars, "low", lookback=10, fractal=2)
    assert (3, 90.0) in lows


def test_swings_returns_empty_for_short_bars():
    bars = [_bar(i, 100, 101, 99, 100) for i in range(3)]
    assert _swings(bars, "high", lookback=10, fractal=2) == []


# -----------------------------------------------------------------------------
# R2: structural SL
# -----------------------------------------------------------------------------
def test_structural_sl_long_uses_swing_low_when_lower_than_zone_bottom():
    # zone bottom = 100; swing low at idx=3 (low=95) confirmed by 2 left/2 right with strictly higher lows
    zone = _zone(top=110, bottom=100, direction=1, born_time=0, atr=1.0)
    bars = [
        _bar(1, 106, 107, 105, 106),
        _bar(2, 105, 106, 104, 105),
        _bar(3, 104, 105, 102, 103),
        _bar(4, 103, 104, 95, 96),    # swing low (idx=3): low=95
        _bar(5, 96, 99, 96, 98),
        _bar(6, 98, 100, 97, 99),
        _bar(7, 99, 102, 98, 100),
    ]
    sl = _structural_sl(zone, bars, atr_val=1.0, side="long", lookback=10, fractal=2, buffer_atr=0.25)
    assert sl == pytest.approx(95.0 - 0.25)


def test_structural_sl_long_falls_back_to_zone_bottom_when_no_swing_below():
    # all swings ABOVE zone bottom → SL = zone_bottom - 0.25
    zone = _zone(top=110, bottom=100, direction=1, born_time=0, atr=1.0)
    bars = [
        _bar(1, 105, 108, 104, 105),
        _bar(2, 105, 108, 103, 106),
        _bar(3, 106, 109, 102, 108),
        _bar(4, 108, 109, 105, 107),
        _bar(5, 107, 108, 105, 106),
    ]
    sl = _structural_sl(zone, bars, atr_val=1.0, side="long", lookback=10, fractal=2, buffer_atr=0.25)
    assert sl == pytest.approx(100.0 - 0.25)


def test_structural_sl_short_uses_swing_high_above_zone_top():
    zone = _zone(top=100, bottom=90, direction=-1, born_time=0, atr=1.0)
    bars = [
        _bar(1, 95, 96, 94, 95),
        _bar(2, 95, 97, 94, 96),
        _bar(3, 96, 99, 95, 97),
        _bar(4, 97, 105, 96, 98),    # swing high (idx=3): high=105
        _bar(5, 98, 99, 95, 96),
        _bar(6, 96, 98, 95, 97),
        _bar(7, 97, 99, 96, 98),
    ]
    sl = _structural_sl(zone, bars, atr_val=1.0, side="short", lookback=10, fractal=2, buffer_atr=0.25)
    assert sl == pytest.approx(105.0 + 0.25)


# -----------------------------------------------------------------------------
# R3: TP magnets
# -----------------------------------------------------------------------------
def test_tp_magnets_long_finds_swing_high_above_entry():
    # entry=100 risk=2; need fractal-2 swings: high strictly higher than 2 left + 2 right.
    # Swing high at idx=3 → 100.5 (drop, dist 0.5 < 1.0=0.5R)
    # Swing high at idx=7 → 105 (tp1, dist 5)
    # Swing high at idx=11 → 110 (tp2, dist 10 cap=8 → out, fallback rr_cap)
    # We want both 105 and 110 inside cap; raise rr_cap test side: use rr_cap=10.
    bars = [
        _bar(1, 95, 96, 94, 95),
        _bar(2, 95, 97, 94, 96),
        _bar(3, 96, 100.5, 95, 97),    # idx=2 candidate? need idx 2 high>idx0,1 high and >idx3,4 high
        _bar(4, 97, 99, 96, 98),
        _bar(5, 98, 100, 97, 99),
        _bar(6, 99, 101, 98, 100),
        _bar(7, 100, 105, 99, 104),    # swing high idx=6 candidate
        _bar(8, 104, 104, 102, 103),
        _bar(9, 103, 103, 101, 102),
        _bar(10, 102, 105, 101, 104),
        _bar(11, 104, 110, 103, 109),  # swing high idx=10
        _bar(12, 109, 109, 107, 108),
        _bar(13, 108, 108, 106, 107),
    ]
    tp1, tp1_kind, tp2, tp2_kind = _tp_magnets(
        entry=100.0, side="long", risk=2.0,
        bars_15m=bars, htf_zones={},
        min_dist_r=0.5, rr_cap=10.0,
    )
    assert tp1 == pytest.approx(105.0)
    assert tp1_kind == "swing"
    assert tp2 == pytest.approx(110.0)
    assert tp2_kind == "swing"


def test_tp_magnets_long_uses_htf_fvg_as_magnet():
    # No swing highs above entry, but a 1h bear FVG above provides magnet at its near edge (bottom)
    bars = [_bar(i, 100, 101, 99, 100) for i in range(10)]
    bear_fvg_1h = _zone(top=120, bottom=108, direction=-1)  # near edge for long target = bottom
    tp1, tp1_kind, tp2, tp2_kind = _tp_magnets(
        entry=100.0, side="long", risk=2.0,
        bars_15m=bars, htf_zones={"1h": [bear_fvg_1h]},
        min_dist_r=0.5, rr_cap=4.0,
    )
    assert tp1 == pytest.approx(108.0)
    assert tp1_kind == "fvg_1h"
    # tp2 falls back to rr_cap
    assert tp2 == pytest.approx(100.0 + 2.0 * 4.0)
    assert tp2_kind == "cap"


def test_tp_magnets_returns_none_when_nothing_in_path():
    bars = [_bar(i, 100, 101, 99, 100) for i in range(10)]
    tp1, tp1_kind, tp2, tp2_kind = _tp_magnets(
        entry=100.0, side="long", risk=2.0,
        bars_15m=bars, htf_zones={},
        min_dist_r=0.5, rr_cap=4.0,
    )
    assert tp1 is None
    assert tp1_kind == "none"


# -----------------------------------------------------------------------------
# R4: RR gate
# -----------------------------------------------------------------------------
def test_structural_rr_passes_above_threshold():
    rr, ok = _structural_rr(entry=100, sl=98, tp1=103.0, min_rr=1.2)  # rr=1.5
    assert ok is True
    assert rr == pytest.approx(1.5)


def test_structural_rr_fails_below_threshold():
    rr, ok = _structural_rr(entry=100, sl=98, tp1=102.0, min_rr=1.2)  # rr=1.0
    assert ok is False
    assert rr == pytest.approx(1.0)


def test_structural_rr_returns_zero_when_no_tp1():
    rr, ok = _structural_rr(entry=100, sl=98, tp1=None, min_rr=1.2)
    assert ok is False
    assert rr == 0.0
