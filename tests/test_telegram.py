"""Tests for telegram.send_v2_alert title and confluence rendering."""
from unittest.mock import patch

import telegram as tg
from strategy_v2 import V2Signal


def _make_signal(direction: int = 1, bull: float = 80.0, bear: float = 40.0) -> V2Signal:
    return V2Signal(
        symbol="BTCUSDT",
        direction=direction,
        trigger_tf="15m",
        zone_top=100.0,
        zone_bottom=99.0,
        zone_born_time=0,
        entry=100.0,
        sl=99.0,
        tp=102.0,
        atr=0.5,
        confluence_score=3,
        htf_touches={"30m": True, "1h": False, "2h": False, "4h": False},
        indicators={"bull_strength": bull, "bear_strength": bear},
    )


def _captured_text(direction: int = 1, bull: float = 80.0, bear: float = 40.0) -> str:
    captured = {}

    def fake_post(url, *args, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        captured["data"] = kwargs.get("data")

        class _Resp:
            status_code = 200
            text = "ok"

            def json(self):
                return {"ok": True}

        return _Resp()

    sig = _make_signal(direction=direction, bull=bull, bear=bear)
    with patch.object(tg.requests, "post", side_effect=fake_post):
        tg.send_v2_alert(sig, timeframe_bars={})

    payload = captured.get("json") or {}
    return payload.get("text", "")


def test_send_v2_alert_long_title_and_rating():
    text = _captured_text(direction=1, bull=80.0, bear=10.0)
    # New title: symbol-first, no SIM prefix.
    assert "(BTCUSDT | LONG | 15m)" in text
    assert "SIM" not in text
    # Star-rating line replaces Confluence line.
    assert "BUY Retest" in text
    assert "★" in text
    # ceil(80 / 20) = 4
    assert "BUY Retest 4★" in text
    # Confluence line is gone.
    assert "Confluence:" not in text


def test_send_v2_alert_short_title_and_rating():
    text = _captured_text(direction=-1, bull=10.0, bear=55.0)
    assert "(BTCUSDT | SHORT | 15m)" in text
    assert "SIM" not in text
    # ceil(55 / 20) = 3
    assert "SELL Retest 3★" in text
    assert "Confluence:" not in text


def test_send_v2_alert_rating_clamped_low_and_high():
    # Zero strength clamps to 1★.
    text_low = _captured_text(direction=1, bull=0.0, bear=0.0)
    assert "BUY Retest 1★" in text_low
    # Over-strength clamps to 5★.
    text_high = _captured_text(direction=1, bull=999.0, bear=0.0)
    assert "BUY Retest 5★" in text_high
