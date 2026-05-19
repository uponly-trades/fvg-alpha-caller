import io
import json
from unittest.mock import patch, MagicMock

import pytest

from notify_bot_webhook import post_chart, _normalize_direction


# ---------- direction normalization ----------

@pytest.mark.parametrize("inp,expected", [
    (1, "long"),
    (-1, "short"),
    ("long", "long"),
    ("short", "short"),
    ("LONG", "long"),
    ("Short", "short"),
])
def test_normalize_direction_valid(inp, expected):
    assert _normalize_direction(inp) == expected


@pytest.mark.parametrize("inp", [0, 2, "sideways", None, object()])
def test_normalize_direction_invalid(inp):
    assert _normalize_direction(inp) is None


# ---------- silent no-ops ----------

def test_skips_when_url_missing(monkeypatch):
    monkeypatch.delenv("NOTIFY_BOT_WEBHOOK_URL", raising=False)
    monkeypatch.setenv("NOTIFY_BOT_WEBHOOK_TOKEN", "tkn")
    with patch("notify_bot_webhook.requests.post") as mock_post:
        post_chart("sid", "BTCUSDT", 1, "15m", 1.0, 0.9, 1.1, b"abc")
        mock_post.assert_not_called()


def test_skips_when_token_missing(monkeypatch):
    monkeypatch.setenv("NOTIFY_BOT_WEBHOOK_URL", "https://x/y")
    monkeypatch.delenv("NOTIFY_BOT_WEBHOOK_TOKEN", raising=False)
    with patch("notify_bot_webhook.requests.post") as mock_post:
        post_chart("sid", "BTCUSDT", 1, "15m", 1.0, 0.9, 1.1, b"abc")
        mock_post.assert_not_called()


def test_skips_when_png_missing(monkeypatch):
    monkeypatch.setenv("NOTIFY_BOT_WEBHOOK_URL", "https://x/y")
    monkeypatch.setenv("NOTIFY_BOT_WEBHOOK_TOKEN", "tkn")
    with patch("notify_bot_webhook.requests.post") as mock_post:
        post_chart("sid", "BTCUSDT", 1, "15m", 1.0, 0.9, 1.1, None)
        post_chart("sid", "BTCUSDT", 1, "15m", 1.0, 0.9, 1.1, b"")
        mock_post.assert_not_called()


def test_skips_when_url_blank(monkeypatch):
    monkeypatch.setenv("NOTIFY_BOT_WEBHOOK_URL", "   ")
    monkeypatch.setenv("NOTIFY_BOT_WEBHOOK_TOKEN", "tkn")
    with patch("notify_bot_webhook.requests.post") as mock_post:
        post_chart("sid", "BTCUSDT", 1, "15m", 1.0, 0.9, 1.1, b"abc")
        mock_post.assert_not_called()


# ---------- happy path: multipart shape ----------

def test_posts_multipart_shape(monkeypatch):
    monkeypatch.setenv("NOTIFY_BOT_WEBHOOK_URL", "https://notify.example/webhooks/fvg-chart")
    monkeypatch.setenv("NOTIFY_BOT_WEBHOOK_TOKEN", "secret-token")

    fake_resp = MagicMock(status_code=200, text="ok")
    with patch("notify_bot_webhook.requests.post", return_value=fake_resp) as mock_post:
        post_chart(
            signal_id="BTCUSDT_15m_1715000000_1",
            symbol="BTCUSDT",
            direction=1,
            tf="15m",
            entry=101350.0,
            sl=100400.0,
            tp1=102300.0,
            png_bytes=b"\x89PNG\r\n\x1a\nFAKEDATA",
        )

    assert mock_post.call_count == 1
    args, kwargs = mock_post.call_args
    # URL is positional
    assert args[0] == "https://notify.example/webhooks/fvg-chart"
    assert kwargs["timeout"] == 5
    assert kwargs["headers"]["Authorization"] == "Bearer secret-token"

    files = kwargs["files"]
    assert set(files.keys()) == {"meta", "chart"}

    meta_name, meta_body, meta_ct = files["meta"]
    assert meta_name == "meta.json"
    assert meta_ct == "application/json"
    meta = json.loads(meta_body)
    assert meta == {
        "signal_id": "BTCUSDT_15m_1715000000_1",
        "symbol": "BTCUSDT",
        "direction": "long",
        "tf": "15m",
        "entry": 101350.0,
        "sl": 100400.0,
        "tp1": 102300.0,
        "ts_iso": meta["ts_iso"],  # tolerated; format-checked below
        "source": "fvg-alpha-caller",
    }
    # ISO-Z timestamp ending in Z, length 20
    assert meta["ts_iso"].endswith("Z") and len(meta["ts_iso"]) == 20

    chart_name, chart_body, chart_ct = files["chart"]
    assert chart_name == "chart.png"
    assert chart_ct == "image/png"
    assert chart_body == b"\x89PNG\r\n\x1a\nFAKEDATA"


def test_short_direction_from_int(monkeypatch):
    monkeypatch.setenv("NOTIFY_BOT_WEBHOOK_URL", "https://x/y")
    monkeypatch.setenv("NOTIFY_BOT_WEBHOOK_TOKEN", "t")
    fake_resp = MagicMock(status_code=204, text="")
    with patch("notify_bot_webhook.requests.post", return_value=fake_resp) as mock_post:
        post_chart("sid", "ETHUSDT", -1, "1h", 2000, 2100, 1900, b"x")
    meta = json.loads(mock_post.call_args.kwargs["files"]["meta"][1])
    assert meta["direction"] == "short"
    assert meta["symbol"] == "ETHUSDT"
    assert meta["tf"] == "1h"


def test_string_direction_uppercase(monkeypatch):
    monkeypatch.setenv("NOTIFY_BOT_WEBHOOK_URL", "https://x/y")
    monkeypatch.setenv("NOTIFY_BOT_WEBHOOK_TOKEN", "t")
    fake_resp = MagicMock(status_code=200, text="ok")
    with patch("notify_bot_webhook.requests.post", return_value=fake_resp) as mock_post:
        post_chart("sid", "BTCUSDT", "LONG", "15m", 1, 2, 3, b"x")
    meta = json.loads(mock_post.call_args.kwargs["files"]["meta"][1])
    assert meta["direction"] == "long"


# ---------- never raises ----------

def test_swallows_connection_error(monkeypatch):
    monkeypatch.setenv("NOTIFY_BOT_WEBHOOK_URL", "https://x/y")
    monkeypatch.setenv("NOTIFY_BOT_WEBHOOK_TOKEN", "t")
    import requests as _rq
    with patch("notify_bot_webhook.requests.post", side_effect=_rq.ConnectionError("boom")):
        # Must not raise.
        post_chart("sid", "BTCUSDT", 1, "15m", 1, 2, 3, b"x")


def test_swallows_non_2xx(monkeypatch):
    monkeypatch.setenv("NOTIFY_BOT_WEBHOOK_URL", "https://x/y")
    monkeypatch.setenv("NOTIFY_BOT_WEBHOOK_TOKEN", "t")
    fake_resp = MagicMock(status_code=503, text="upstream busy")
    with patch("notify_bot_webhook.requests.post", return_value=fake_resp):
        post_chart("sid", "BTCUSDT", 1, "15m", 1, 2, 3, b"x")


def test_bad_direction_skips_post(monkeypatch):
    monkeypatch.setenv("NOTIFY_BOT_WEBHOOK_URL", "https://x/y")
    monkeypatch.setenv("NOTIFY_BOT_WEBHOOK_TOKEN", "t")
    with patch("notify_bot_webhook.requests.post") as mock_post:
        post_chart("sid", "BTCUSDT", "sideways", "15m", 1, 2, 3, b"x")
        mock_post.assert_not_called()
