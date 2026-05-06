import base64
import os

import pytest
from httpx import AsyncClient, ASGITransport


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://x")
    monkeypatch.setenv("MASTER_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    monkeypatch.setenv("INTERNAL_TOKEN", "secret-token")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")


@pytest.mark.asyncio
async def test_encrypt_requires_token():
    from trade_executor.http_api import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/encrypt", json={"plaintext": "hi"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_encrypt_returns_blob_b64():
    from trade_executor.http_api import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/encrypt",
            json={"plaintext": "hello"},
            headers={"X-Internal-Token": "secret-token"},
        )
    assert r.status_code == 200
    body = r.json()
    assert "blob_b64" in body
    raw = base64.b64decode(body["blob_b64"])
    assert len(raw) >= 12 + 16
