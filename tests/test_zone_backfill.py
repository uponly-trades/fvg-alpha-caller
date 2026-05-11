"""Zone backfill from warm-up buffers.

Without this: HTF zones (1h/2h/4h) only populate when their bar closes — so
post-restart we wait up to 4h before evaluate_v2_signal can trigger. That kills
the freshness window and the v2 confluence gate.

With this: scan each (symbol, tf) buffer with a sliding 3-bar window after
warm-up; any FVG already present in history gets registered in tracker.zones
(idempotent — re-running must not duplicate zones).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
os.environ.setdefault("TELEGRAM_CHAT_ID", "x")
os.environ.setdefault("STRATEGY_VERSION", "v2")
os.environ.setdefault("MODEL_ENABLED", "0")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rest_client import Bar  # noqa: E402


def _bar(t: int, o: float, h: float, l: float, c: float, v: float = 100.0) -> Bar:
    return Bar(open_time=t, open=o, high=h, low=l, close=c, volume=v, is_closed=True)


def _bull_fvg_window(t0: int) -> list:
    """3 bars where bars[2].low > bars[0].high (bull FVG between bars 0/2)."""
    return [
        _bar(t0,         100.0, 100.5,  99.5, 100.2, 100),  # high=100.5
        _bar(t0 + 60,    100.2, 102.0,  99.8, 101.8, 500),  # displacement
        _bar(t0 + 120,   101.9, 102.5, 101.0, 102.2, 200),  # low=101.0 > 100.5
    ]


def _flat_bars(start_t: int, n: int, t_step: int = 60) -> list:
    """N bars with no FVG — overlapping ranges."""
    return [_bar(start_t + i * t_step, 50.0, 50.5, 49.5, 50.0, 100) for i in range(n)]


@pytest.fixture()
def caller():
    import main
    return main.AlphaCaller()


def test_backfill_finds_fvg_in_buffer_history(caller):
    """A bull FVG embedded in the buffer 5 bars from the end must be captured."""
    symbol = "TESTUSDT"
    tf = "1h"
    bars = _flat_bars(1_700_000_000_000, 20)
    # Splice a bull FVG at index 10..12
    fvg_window = _bull_fvg_window(bars[10].open_time)
    bars[10:13] = fvg_window
    # Re-flatten times after splice (FVG window uses tight 60s; doesn't matter for detection)

    caller._v2_backfill_zones(symbol, tf, bars)

    bull_zones = [z for z in caller.tracker.zones.values()
                  if z.symbol == symbol and z.tf == tf and z.direction == 1]
    assert len(bull_zones) >= 1, "backfill missed embedded bull FVG"


def test_backfill_uses_offline_strength_context(caller, monkeypatch):
    """Backfill must stay deterministic and never block on live HTTP context."""
    import fvg_engine

    def fail_network(*_args, **_kwargs):
        raise AssertionError("backfill attempted live context fetch")

    monkeypatch.setattr(fvg_engine, "_fetch_closes", fail_network)
    monkeypatch.setattr(fvg_engine, "get_24h_price_change_pct", fail_network)

    bars = _flat_bars(1_700_000_000_000, 20)
    bars[10:13] = _bull_fvg_window(bars[10].open_time)

    caller._v2_backfill_zones("TESTUSDT", "1h", bars)

    assert any(z.symbol == "TESTUSDT" and z.tf == "1h" for z in caller.tracker.zones.values())

def test_backfill_idempotent(caller):
    """Calling backfill twice must not create duplicate zones."""
    symbol = "TESTUSDT"
    tf = "2h"
    bars = _flat_bars(1_700_000_000_000, 20)
    bars[10:13] = _bull_fvg_window(bars[10].open_time)

    caller._v2_backfill_zones(symbol, tf, bars)
    first_count = len(caller.tracker.zones)
    caller._v2_backfill_zones(symbol, tf, bars)
    second_count = len(caller.tracker.zones)

    assert first_count == second_count, (
        f"backfill not idempotent: {first_count} -> {second_count} zones"
    )


def test_backfill_does_not_clobber_last_bar_time(caller):
    """Backfill must NOT advance tracker.last_bar_time — that would make the
    next live bar close skip its capture/signal evaluation."""
    symbol = "TESTUSDT"
    tf = "4h"
    bars = _flat_bars(1_700_000_000_000, 20)
    bars[10:13] = _bull_fvg_window(bars[10].open_time)

    assert (symbol, tf) not in caller.tracker.last_bar_time
    caller._v2_backfill_zones(symbol, tf, bars)
    # Either unset, or set to a value <= last bar's open_time so the live
    # close still gets through the dedupe check.
    last_t = caller.tracker.last_bar_time.get((symbol, tf))
    assert last_t is None or last_t < bars[-1].open_time, (
        f"backfill clobbered last_bar_time={last_t}, will block next live close"
    )


def test_backfill_handles_empty_and_short_buffers(caller):
    """Backfill must not crash on empty or sub-3-bar buffers."""
    caller._v2_backfill_zones("X", "15m", [])
    caller._v2_backfill_zones("X", "15m", _flat_bars(1, 2))
    assert caller.tracker.zones == {}


def test_backfill_all_runs_per_buffer(caller, monkeypatch):
    """_v2_backfill_all must iterate ALL keys in poller._buffers and call
    _v2_backfill_zones for each."""
    caller.poller._buffers = {
        "BTCUSDT_15m": _flat_bars(1, 5),
        "BTCUSDT_1h":  _flat_bars(1, 5),
        "ETHUSDT_4h":  _flat_bars(1, 5),
    }
    seen: list = []
    monkeypatch.setattr(caller, "_v2_backfill_zones", lambda s, t, b: seen.append((s, t)))
    caller._v2_backfill_all()
    assert sorted(seen) == sorted([("BTCUSDT", "15m"), ("BTCUSDT", "1h"), ("ETHUSDT", "4h")])


@pytest.mark.asyncio
async def test_backfill_when_warm_loops_until_full_coverage(caller, monkeypatch):
    """The polling wrapper must re-invoke _v2_backfill_all as buffers grow
    (no fixed deadline that misses late warm-ups). Once coverage hits the
    full threshold, it must terminate."""
    import asyncio as _asyncio
    import main as _main

    monkeypatch.setattr(_main, "STRATEGY_VERSION", "v2")
    monkeypatch.setattr(_main, "TIMEFRAMES", ["15m", "1h"])
    import config as _config
    monkeypatch.setattr(_config, "SYMBOLS", ["A", "B"])

    # Patch the asyncio module that main.py actually uses
    async def _no_sleep(_s):
        return None
    monkeypatch.setattr(_main.asyncio, "sleep", _no_sleep)

    calls = {"n": 0}
    sequence = [0, 1, 2, 4]  # 4/4 = 100% >= 95% threshold → terminate

    def fake_all():
        idx = min(calls["n"], len(sequence) - 1)
        n = sequence[idx]
        caller.poller._buffers = {f"K{i}": [] for i in range(n)}
        calls["n"] += 1

    monkeypatch.setattr(caller, "_v2_backfill_all", fake_all)
    # Pre-populate one buffer so the initial "wait for any buffer" loop exits
    caller.poller._buffers = {"seed": []}

    await _asyncio.wait_for(caller._v2_backfill_when_warm(), timeout=5)
    assert calls["n"] >= 3, f"backfill loop only ran {calls['n']} times"


@pytest.mark.asyncio
async def test_backfill_when_warm_returns_when_strategy_not_v2(caller, monkeypatch):
    """v1 must skip backfill entirely — no buffer poll, no calls."""
    import main as _main
    monkeypatch.setattr(_main, "STRATEGY_VERSION", "v1")
    called = {"n": 0}
    monkeypatch.setattr(caller, "_v2_backfill_all", lambda: called.update(n=called["n"] + 1))
    await caller._v2_backfill_when_warm()
    assert called["n"] == 0
