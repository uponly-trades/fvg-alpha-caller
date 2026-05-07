"""executor_client must not crash on non-JSON responses or HTTP errors with text body."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("INTERNAL_TOKEN", "test-token")
os.environ.setdefault("EXECUTOR_URL", "http://stub")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "1:stub")
os.environ.setdefault("DATABASE_URL", "postgresql://x")


class FakeResp:
    def __init__(self, status: int, body: str, content_type: str = "application/json"):
        self.status = status
        self._body = body
        self.headers = {"content-type": content_type}

    async def text(self) -> str:
        return self._body

    async def json(self, content_type=None):
        import json
        return json.loads(self._body)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class FakeSession:
    def __init__(self, resp: FakeResp):
        self._resp = resp

    def request(self, *_, **__):
        return self._resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


@pytest.mark.asyncio
async def test_non_json_500_response_raises_executor_client_error_not_decode_error(
    monkeypatch,
):
    """Server returning 'Internal Server Error' plaintext must surface as ExecutorClientError, not JSONDecodeError."""
    from telegram_bot import executor_client

    fake = FakeSession(FakeResp(500, "Internal Server Error", content_type="text/plain"))
    monkeypatch.setattr(executor_client.aiohttp, "ClientSession", lambda *a, **k: fake)

    with pytest.raises(executor_client.ExecutorClientError) as excinfo:
        await executor_client.set_keys(123, "k", "s")
    msg = str(excinfo.value)
    assert "500" in msg or "Internal Server Error" in msg


@pytest.mark.asyncio
async def test_json_400_with_detail_surfaces_detail_message(monkeypatch):
    """A proper 400 JSON {detail: "..."} must surface its detail string."""
    from telegram_bot import executor_client

    fake = FakeSession(FakeResp(400, '{"detail": "Invalid API-key (-2015)"}'))
    monkeypatch.setattr(executor_client.aiohttp, "ClientSession", lambda *a, **k: fake)

    with pytest.raises(executor_client.ExecutorClientError) as excinfo:
        await executor_client.set_keys(123, "k", "s")
    assert "-2015" in str(excinfo.value) or "Invalid API-key" in str(excinfo.value)


@pytest.mark.asyncio
async def test_empty_body_does_not_crash(monkeypatch):
    """Empty 502 body must surface as a usable error message, not JSONDecodeError."""
    from telegram_bot import executor_client

    fake = FakeSession(FakeResp(502, "", content_type="text/html"))
    monkeypatch.setattr(executor_client.aiohttp, "ClientSession", lambda *a, **k: fake)

    with pytest.raises(executor_client.ExecutorClientError) as excinfo:
        await executor_client.set_keys(123, "k", "s")
    assert "502" in str(excinfo.value)
