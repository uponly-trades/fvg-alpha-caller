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
