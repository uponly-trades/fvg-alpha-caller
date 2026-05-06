import os

import pytest

from trade_executor import db


pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


@pytest.mark.asyncio
async def test_pool_round_trip():
    pool = await db.create_pool(os.environ["TEST_DATABASE_URL"])
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT 1 AS one")
            assert row["one"] == 1
    finally:
        await pool.close()
