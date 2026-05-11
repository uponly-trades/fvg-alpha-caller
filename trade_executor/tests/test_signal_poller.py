import os

import pytest

from trade_executor import db
from trade_executor.signal_poller import poll_once, load_last_seen, save_last_seen

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


@pytest.mark.asyncio
async def test_poll_only_returns_valid_decisions_after_last_seen():
    pool = await db.create_pool(os.environ["TEST_DATABASE_URL"])
    try:
        async with pool.acquire() as conn:
            await conn.execute("""
              CREATE TABLE IF NOT EXISTS signal_decisions (
                id TEXT PRIMARY KEY, symbol TEXT, tf TEXT, direction TEXT,
                entry DOUBLE PRECISION, sl DOUBLE PRECISION,
                tp1 DOUBLE PRECISION, tp2 DOUBLE PRECISION,
                valid BOOLEAN, event_type TEXT, created_at BIGINT
              )
            """)
            await conn.execute("DELETE FROM signal_decisions WHERE id LIKE 'sp-%'")
            await conn.execute("""
              INSERT INTO signal_decisions (id, symbol, tf, direction, entry, sl, tp1, tp2, valid, event_type, created_at) VALUES
                ('sp-1', 'BTCUSDT', '1h', 'long', 100, 95, 105, 110, true, 'touch', 1000),
                ('sp-2', 'BTCUSDT', '1h', 'long', 100, 95, 105, 110, false, 'touch', 2000),
                ('sp-3', 'ETHUSDT', '1h', 'short', 200, 210, 190, 180, true, 'touch', 3000)
            """)

            rows = await poll_once(conn, last_seen_ms=500)
        assert [r["id"] for r in rows] == ["sp-1", "sp-3"]
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_last_seen_persists():
    pool = await db.create_pool(os.environ["TEST_DATABASE_URL"])
    try:
        async with pool.acquire() as conn:
            await save_last_seen(conn, 12345)
            assert await load_last_seen(conn) == 12345
            await save_last_seen(conn, 99999)
            assert await load_last_seen(conn) == 99999
    finally:
        await pool.close()
