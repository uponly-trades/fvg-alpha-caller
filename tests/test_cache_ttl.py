"""Tests for per-TF cache TTL acceptance."""
import os
import time

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
os.environ.setdefault("TELEGRAM_CHAT_ID", "x")

import websocket_client as wc


def test_per_tf_age_4h_accepts_old_cache():
    # 4h key should accept cache up to 8h old
    assert wc._key_max_age_sec("BTCUSDT_4h") == 8 * 60 * 60


def test_per_tf_age_15m_strict():
    assert wc._key_max_age_sec("BTCUSDT_15m") == 30 * 60


def test_per_tf_age_unknown_tf_falls_back():
    # Unknown TF → fallback constant
    assert wc._key_max_age_sec("BTCUSDT_unknown") == wc._CACHE_MAX_AGE_SEC


def test_per_tf_age_no_underscore():
    assert wc._key_max_age_sec("UNRECOGNIZED") == wc._CACHE_MAX_AGE_SEC


def test_4h_cache_5h_old_still_loaded(tmp_path, monkeypatch):
    """4h bar with last_close 5h old must load (within 8h tolerance)."""
    cache_path = tmp_path / "cache.json"
    monkeypatch.setattr(wc, "_CACHE_PATH", cache_path)

    five_hours_ago_ms = int((time.time() - 5 * 3600) * 1000)
    one_hour_ago_ms = int((time.time() - 1 * 3600) * 1000)
    cache_data = {
        "BTCUSDT_4h": [
            {"t": five_hours_ago_ms - 4 * 3600 * 1000, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10},
            {"t": five_hours_ago_ms, "o": 1.5, "h": 3, "l": 1, "c": 2, "v": 20},
        ],
        "BTCUSDT_15m": [
            {"t": one_hour_ago_ms, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10},
        ],
    }
    import json
    cache_path.write_text(json.dumps(cache_data))

    ws = wc.BinanceKlineWS(on_bar_close=lambda *a, **k: None)
    loaded = ws._load_cache()
    # 4h accepts 5h-old → loaded. 15m rejects 1h-old (>30min) → skipped.
    assert "BTCUSDT_4h" in ws._buffers
    assert "BTCUSDT_15m" not in ws._buffers
    assert loaded == 1


def test_15m_cache_45min_old_rejected(tmp_path, monkeypatch):
    cache_path = tmp_path / "cache.json"
    monkeypatch.setattr(wc, "_CACHE_PATH", cache_path)

    forty_five_min_ago_ms = int((time.time() - 45 * 60) * 1000)
    cache_data = {
        "BTCUSDT_15m": [
            {"t": forty_five_min_ago_ms, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10},
        ],
    }
    import json
    cache_path.write_text(json.dumps(cache_data))

    ws = wc.BinanceKlineWS(on_bar_close=lambda *a, **k: None)
    loaded = ws._load_cache()
    assert loaded == 0
    assert "BTCUSDT_15m" not in ws._buffers
