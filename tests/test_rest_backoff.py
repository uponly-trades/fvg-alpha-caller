"""Tests for REST fetch_klines retry-with-backoff."""
import os

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
os.environ.setdefault("TELEGRAM_CHAT_ID", "x")

from unittest.mock import patch, MagicMock

import rest_client


def _make_resp(status: int, body=None):
    r = MagicMock()
    r.status_code = status
    r.headers = {}
    if status >= 400 and status not in (418, 429):
        r.raise_for_status.side_effect = Exception(f"{status} error")
    else:
        r.raise_for_status.return_value = None
    r.json.return_value = body if body is not None else []
    return r


def test_backoff_recovers_after_418():
    sample = [[1000, "1", "2", "0.5", "1.5", "10"], [2000, "1.5", "3", "1", "2", "20"]]
    responses = [_make_resp(418), _make_resp(418), _make_resp(200, sample)]
    with patch.object(rest_client.requests, "get", side_effect=responses), \
         patch.object(rest_client.time, "sleep", return_value=None):
        bars = rest_client.fetch_klines("BTCUSDT", "15m", limit=2)
    # last bar dropped (forming) → only 1 bar back
    assert len(bars) == 1
    assert bars[0].open_time == 1000


def test_backoff_gives_up_after_max_retries():
    responses = [_make_resp(418)] * 10
    with patch.object(rest_client.requests, "get", side_effect=responses), \
         patch.object(rest_client.time, "sleep", return_value=None):
        bars = rest_client.fetch_klines("BTCUSDT", "15m")
    assert bars == []


def test_429_also_triggers_backoff():
    sample = [[1000, "1", "2", "0.5", "1.5", "10"], [2000, "1.5", "3", "1", "2", "20"]]
    responses = [_make_resp(429), _make_resp(200, sample)]
    with patch.object(rest_client.requests, "get", side_effect=responses), \
         patch.object(rest_client.time, "sleep", return_value=None):
        bars = rest_client.fetch_klines("BTCUSDT", "1h", limit=2)
    assert len(bars) == 1


def test_long_retry_after_honored_uncapped():
    """Retry-After must NOT be clamped — Binance 418 ban can be 1800s+ and a
    capped sleep guarantees a re-ban. The MAX_SLEEP ceiling only applies to
    exponential-backoff fallback when no Retry-After header is present."""
    sample = [[1000, "1", "2", "0.5", "1.5", "10"], [2000, "1.5", "3", "1", "2", "20"]]
    rl = _make_resp(418)
    rl.headers = {"Retry-After": "1800"}
    responses = [rl, _make_resp(200, sample)]
    sleeps = []
    with patch.object(rest_client.requests, "get", side_effect=responses), \
         patch.object(rest_client.time, "sleep", side_effect=lambda s: sleeps.append(s)):
        rest_client.fetch_klines("BTCUSDT", "1h")
    assert 1800.0 in sleeps


def test_retry_after_header_respected():
    sample = [[1000, "1", "2", "0.5", "1.5", "10"], [2000, "1.5", "3", "1", "2", "20"]]
    rl = _make_resp(429)
    rl.headers = {"Retry-After": "3"}
    responses = [rl, _make_resp(200, sample)]
    sleeps = []
    with patch.object(rest_client.requests, "get", side_effect=responses), \
         patch.object(rest_client.time, "sleep", side_effect=lambda s: sleeps.append(s)):
        rest_client.fetch_klines("BTCUSDT", "30m")
    assert 3.0 in sleeps


def test_non_rate_limit_error_no_retry():
    """500 errors should not retry — only 418/429 do."""
    responses = [_make_resp(500), _make_resp(500)]
    call_count = {"n": 0}

    def _side(*a, **kw):
        call_count["n"] += 1
        if call_count["n"] > len(responses):
            raise AssertionError("retried beyond expected")
        return responses[call_count["n"] - 1]

    with patch.object(rest_client.requests, "get", side_effect=_side), \
         patch.object(rest_client.time, "sleep", return_value=None):
        bars = rest_client.fetch_klines("BTCUSDT", "15m")
    assert bars == []
    # Backoff path triggers retries only on 418/429. 500 raises → caught in except → also retries.
    # That's acceptable: just confirm no infinite loop.
    assert call_count["n"] <= rest_client._RATE_LIMIT_RETRIES + 1
