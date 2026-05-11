"""POST /users/{tid}/keys must surface exchange auth errors as JSON 400, not crash 500."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("INTERNAL_TOKEN", "test-internal-token")
os.environ.setdefault("MASTER_ENCRYPTION_KEY", "MDEyMzQ1Njc4OTAxMjM0NTY3ODkwMTIzNDU2Nzg5MDE=")
os.environ.setdefault("DATABASE_URL", "postgresql://x")

from trade_executor.http_api import app  # noqa: E402


@pytest.mark.asyncio
async def test_invalid_binance_keys_returns_400_json():
    """ccxt AuthenticationError must become {"detail": "..."} 400, not 500 plain."""
    from ccxt.base.errors import AuthenticationError

    auth_err = AuthenticationError(
        'binanceusdm {"code":-2015,"msg":"Invalid API-key, IP, or permissions for action"}'
    )
    with (
        patch("trade_executor.http_api._pool_or_create", return_value=None),
        patch("trade_executor.http_api.save_user_keys", side_effect=auth_err),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(
                "/users/123/keys",
                json={"api_key": "k", "api_secret": "s"},
                headers={"X-Internal-Token": "test-internal-token"},
            )

    assert r.status_code == 400, f"got {r.status_code}: {r.text}"
    assert r.headers["content-type"].startswith("application/json")
    body = r.json()
    assert "detail" in body
    assert "-2015" in body["detail"] or "Invalid API-key" in body["detail"]


@pytest.mark.asyncio
async def test_network_error_returns_502_json():
    """ccxt NetworkError (timeout/dns) must become 502 JSON, not 500 plain."""
    from ccxt.base.errors import NetworkError

    net_err = NetworkError("connection timeout")
    with (
        patch("trade_executor.http_api._pool_or_create", return_value=None),
        patch("trade_executor.http_api.save_user_keys", side_effect=net_err),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(
                "/users/123/keys",
                json={"api_key": "k", "api_secret": "s"},
                headers={"X-Internal-Token": "test-internal-token"},
            )

    assert r.status_code == 502, f"got {r.status_code}: {r.text}"
    body = r.json()
    assert "detail" in body
    assert "timeout" in body["detail"].lower() or "network" in body["detail"].lower()
