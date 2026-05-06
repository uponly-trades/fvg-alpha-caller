import json
import os

import pytest

from trade_executor.audit import insert_audit
from trade_executor.notify import notify
from trade_executor import db

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


@pytest.mark.asyncio
async def test_insert_audit_writes_row():
    pool = await db.create_pool(os.environ["TEST_DATABASE_URL"])
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO users (telegram_id, created_at, updated_at) VALUES (999, 0, 0) ON CONFLICT DO NOTHING"
            )
            uid = await conn.fetchval("SELECT id FROM users WHERE telegram_id=999")
            await insert_audit(conn, uid, "test_action", {"foo": "bar"})
            row = await conn.fetchrow(
                "SELECT action, payload FROM user_audit_log WHERE user_id=$1 ORDER BY id DESC LIMIT 1",
                uid,
            )
        assert row["action"] == "test_action"
        assert json.loads(row["payload"]) == {"foo": "bar"}
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_notify_sends_payload():
    pool = await db.create_pool(os.environ["TEST_DATABASE_URL"])
    try:
        listener = await pool.acquire()
        received = []

        async def cb(conn, pid, channel, payload):
            received.append((channel, payload))

        await listener.add_listener("test_chan", cb)
        async with pool.acquire() as conn:
            await notify(conn, "test_chan", {"x": 1})
        import asyncio
        await asyncio.sleep(0.5)
        await listener.remove_listener("test_chan", cb)
        await pool.release(listener)
        assert len(received) >= 1
        assert received[0][0] == "test_chan"
    finally:
        await pool.close()
