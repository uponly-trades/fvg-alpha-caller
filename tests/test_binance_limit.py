"""Tests for binance_limit module — anti-ban rate-limit defense.

These tests run with no DATABASE_URL, so the Postgres circuit breaker
falls back to in-memory state. That keeps the suite fast and hermetic.
"""
from __future__ import annotations

import os
from unittest import mock

import pytest

# Ensure we hit the in-memory fallback for ban state.
os.environ.pop("DATABASE_URL", None)

import binance_limit  # noqa: E402


@pytest.fixture(autouse=True)
def _reset():
    binance_limit._reset_for_tests()
    binance_limit._DB_URL = None
    yield
    binance_limit._reset_for_tests()


class _FakeResp:
    def __init__(self, status_code=200, headers=None):
        self.status_code = status_code
        self.headers = headers or {}


def test_record_headers_parses_used_weight_canonical():
    binance_limit.record_headers({"X-MBX-USED-WEIGHT-1m": "1234"})
    assert binance_limit._state["used_weight"] == 1234
    assert binance_limit._state["last_seen_ts"] > 0


def test_record_headers_case_insensitive():
    binance_limit.record_headers({"x-mbx-used-weight-1m": "999"})
    assert binance_limit._state["used_weight"] == 999


def test_record_headers_ignores_missing():
    binance_limit.record_headers({"Content-Type": "application/json"})
    assert binance_limit._state["used_weight"] == 0


def test_record_headers_ignores_garbage():
    binance_limit.record_headers({"X-MBX-USED-WEIGHT-1m": "not-a-number"})
    assert binance_limit._state["used_weight"] == 0


def test_record_response_parses_headers():
    resp = _FakeResp(headers={"X-MBX-USED-WEIGHT-1m": "777"})
    binance_limit.record_response(resp)
    assert binance_limit._state["used_weight"] == 777


def test_await_capacity_sync_no_sleep_under_threshold():
    binance_limit.record_headers({"X-MBX-USED-WEIGHT-1m": "100"})
    with mock.patch.object(binance_limit.time, "sleep") as sleep_mock:
        binance_limit.await_capacity_sync(weight_needed=5)
    sleep_mock.assert_not_called()


def test_await_capacity_sync_sleeps_over_threshold():
    binance_limit.record_headers({"X-MBX-USED-WEIGHT-1m": "1900"})
    with mock.patch.object(binance_limit.time, "sleep") as sleep_mock:
        binance_limit.await_capacity_sync(weight_needed=5)
    sleep_mock.assert_called_once()
    sleep_arg = sleep_mock.call_args[0][0]
    assert sleep_arg > 0


def test_mark_banned_then_is_banned_returns_positive():
    binance_limit.mark_banned(1800)
    remaining = binance_limit.is_banned()
    # Should be roughly 1800s left (in ms), allow generous slack
    assert remaining > 1700_000
    assert remaining <= 1801_000


def test_is_banned_zero_when_not_marked():
    assert binance_limit.is_banned() == 0


def test_await_capacity_sync_raises_when_banned_flag():
    binance_limit.mark_banned(60)
    with pytest.raises(binance_limit.BinanceBannedError) as exc:
        binance_limit.await_capacity_sync(weight_needed=5, raise_when_banned=True)
    assert exc.value.ms_remaining > 0


def test_await_capacity_sync_sleeps_when_banned_no_raise():
    binance_limit.mark_banned(2)
    with mock.patch.object(binance_limit.time, "sleep") as sleep_mock:
        binance_limit.await_capacity_sync(weight_needed=5)
    # First call sleeps the ban; weight is 0 so no second sleep
    assert sleep_mock.call_count >= 1
    first_sleep = sleep_mock.call_args_list[0][0][0]
    assert first_sleep > 0  # ban sleep


def test_record_response_418_marks_banned():
    resp = _FakeResp(status_code=418, headers={"Retry-After": "30"})
    binance_limit.record_response(resp)
    remaining = binance_limit.is_banned()
    assert remaining > 25_000


def test_record_response_418_default_when_no_retry_after():
    resp = _FakeResp(status_code=418, headers={})
    binance_limit.record_response(resp)
    remaining = binance_limit.is_banned()
    # Default 120s
    assert remaining > 100_000


@pytest.mark.asyncio
async def test_await_capacity_async_raises_when_banned():
    binance_limit.mark_banned(60)
    with pytest.raises(binance_limit.BinanceBannedError):
        await binance_limit.await_capacity_async(weight_needed=5, raise_when_banned=True)


@pytest.mark.asyncio
async def test_mark_banned_async_then_is_banned_async():
    await binance_limit.mark_banned_async(1800)
    remaining = await binance_limit.is_banned_async()
    assert remaining > 1700_000


def test_decay_weight_resets_after_window():
    binance_limit.record_headers({"X-MBX-USED-WEIGHT-1m": "1900"})
    # Force last_seen_ts to be > 60s in the past
    binance_limit._state["last_seen_ts"] = binance_limit.time.time() - 120
    with mock.patch.object(binance_limit.time, "sleep") as sleep_mock:
        binance_limit.await_capacity_sync(weight_needed=5)
    sleep_mock.assert_not_called()
