import pytest
from httpx import AsyncClient, ASGITransport

from trade_executor.http_api import app


@pytest.mark.asyncio
async def test_healthz_returns_ok():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
