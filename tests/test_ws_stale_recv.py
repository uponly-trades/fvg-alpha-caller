"""Per-connection stale-recv watchdog: zombie WS that 'connected' but never
delivers a message must time out, close, and reconnect — the recv() call
itself can hang forever after a half-broken keepalive cycle.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
os.environ.setdefault("TELEGRAM_CHAT_ID", "x")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import websocket_client  # noqa: E402


class _ZombieWS:
    """Pretend ws: enters context, blocks forever on recv, never closes itself."""
    def __init__(self):
        self.recv_calls = 0
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.closed = True
        return False

    async def recv(self):
        self.recv_calls += 1
        await asyncio.sleep(3600)  # block forever

    def __aiter__(self):
        return self

    async def __anext__(self):
        await self.recv()
        raise StopAsyncIteration


@pytest.mark.asyncio
async def test_run_connection_breaks_out_of_zombie_recv(monkeypatch):
    """If no message arrives within PER_CONN_STALE_SEC, the loop must abort
    the inner recv and proceed to the reconnect branch — proven by observing
    the connect attempt count grow past 1 within a small wall-clock budget.
    """
    monkeypatch.setattr(websocket_client, "SYMBOLS", ["BTCUSDT"])
    monkeypatch.setattr(websocket_client, "TIMEFRAMES", ["1m"])

    client = websocket_client.BinanceKlineWS(lambda *_: None)
    # Tight watchdog so the test is fast.
    client.PER_CONN_STALE_SEC = 0.3
    client.RECONNECT_DELAY_INITIAL = 0.05

    connect_calls = 0
    zombies: list[_ZombieWS] = []

    def fake_connect(*_a, **_kw):
        nonlocal connect_calls
        connect_calls += 1
        z = _ZombieWS()
        zombies.append(z)
        return z

    monkeypatch.setattr(websocket_client.websockets, "connect", fake_connect)

    client._running = True
    task = asyncio.create_task(client._run_connection(0, ["btcusdt@kline_1m"]))
    try:
        # Give two stale windows + a reconnect: 0.3 * 2 + 0.05 + slack.
        await asyncio.sleep(1.2)
    finally:
        client._running = False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert connect_calls >= 2, (
        f"zombie recv never timed out: only {connect_calls} connect attempt(s) "
        f"in 1.2s with PER_CONN_STALE_SEC=0.3"
    )
    # First zombie must be marked closed (context exited) before reconnect.
    assert zombies[0].closed, "first zombie was not closed before reconnect"
