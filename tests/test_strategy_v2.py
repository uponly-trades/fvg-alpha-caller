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
