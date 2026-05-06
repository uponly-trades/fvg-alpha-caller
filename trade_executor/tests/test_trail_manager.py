import os

import pytest

from trade_executor import db
from trade_executor.trail_manager import maybe_trail

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


class FakeEx:
    def __init__(self, fail_cancel=False):
        self.cancelled = []
        self.placed = []
        self.fail_cancel = fail_cancel

    async def cancel_order(self, order_id, symbol):
        if self.fail_cancel:
            raise Exception("not found")
        self.cancelled.append((order_id, symbol))
        return {"id": order_id, "status": "CANCELED"}

    async def create_order(self, symbol, type_, side, amount, price=None, params=None):
        self.placed.append((type_, side, params))
        return {"id": "new-sl"}


@pytest.mark.asyncio
async def test_long_trails_when_price_crosses_tp1(monkeypatch):
    pool = await db.create_pool(os.environ["TEST_DATABASE_URL"])
    try:
        async with pool.acquire() as conn:
            await conn.execute("INSERT INTO users (telegram_id, created_at, updated_at) VALUES (888, 0, 0) ON CONFLICT DO NOTHING")
            uid = await conn.fetchval("SELECT id FROM users WHERE telegram_id=888")
            await conn.execute("DELETE FROM user_trades WHERE id=$1", f"{uid}-tr-1")
            await conn.execute(
                """
                INSERT INTO user_trades (id, user_id, decision_id, symbol, tf, direction,
                  leverage, margin_usdt, notional_usdt, qty, entry, sl, sl_current, tp1, tp2,
                  status, sl_order_id, opened_at)
                VALUES ($1,$2,'tr-1','BTCUSDT','1h','long',5,10,50,0.001,100,95,95,105,110,'open','sl-old',0)
                """,
                f"{uid}-tr-1", uid,
            )

        ex = FakeEx()
        trailed = await maybe_trail(pool, ex=ex, symbol="BTCUSDT", price=105.5)
        assert trailed is True
        assert ex.cancelled == [("sl-old", "BTCUSDT")]
        assert ex.placed[0][0] == "STOP_MARKET"
        assert ex.placed[0][2]["stopPrice"] == 105.0

        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT status, sl_current, sl_order_id FROM user_trades WHERE id=$1",
                                       f"{uid}-tr-1")
        assert row["status"] == "tp1_trailed"
        assert row["sl_current"] == pytest.approx(105.0)
        assert row["sl_order_id"] == "new-sl"
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_short_trails_when_price_crosses_tp1():
    pool = await db.create_pool(os.environ["TEST_DATABASE_URL"])
    try:
        async with pool.acquire() as conn:
            await conn.execute("INSERT INTO users (telegram_id, created_at, updated_at) VALUES (889, 0, 0) ON CONFLICT DO NOTHING")
            uid = await conn.fetchval("SELECT id FROM users WHERE telegram_id=889")
            await conn.execute("DELETE FROM user_trades WHERE id=$1", f"{uid}-tr-2")
            await conn.execute(
                """
                INSERT INTO user_trades (id, user_id, decision_id, symbol, tf, direction,
                  leverage, margin_usdt, notional_usdt, qty, entry, sl, sl_current, tp1, tp2,
                  status, sl_order_id, opened_at)
                VALUES ($1,$2,'tr-2','BTCUSDT','1h','short',5,10,50,0.001,100,105,105,95,90,'open','sl-old2',0)
                """,
                f"{uid}-tr-2", uid,
            )
        ex = FakeEx()
        trailed = await maybe_trail(pool, ex=ex, symbol="BTCUSDT", price=94.0)
        assert trailed is True
        assert ex.placed[0][2]["stopPrice"] == 95.0
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_no_trail_when_price_below_tp1_long():
    pool = await db.create_pool(os.environ["TEST_DATABASE_URL"])
    try:
        async with pool.acquire() as conn:
            await conn.execute("INSERT INTO users (telegram_id, created_at, updated_at) VALUES (890, 0, 0) ON CONFLICT DO NOTHING")
            uid = await conn.fetchval("SELECT id FROM users WHERE telegram_id=890")
            await conn.execute("DELETE FROM user_trades WHERE id=$1", f"{uid}-tr-3")
            await conn.execute(
                """
                INSERT INTO user_trades (id, user_id, decision_id, symbol, tf, direction,
                  leverage, margin_usdt, notional_usdt, qty, entry, sl, sl_current, tp1, tp2,
                  status, sl_order_id, opened_at)
                VALUES ($1,$2,'tr-3','BTCUSDT','1h','long',5,10,50,0.001,100,95,95,105,110,'open','sl-3',0)
                """,
                f"{uid}-tr-3", uid,
            )
        ex = FakeEx()
        trailed = await maybe_trail(pool, ex=ex, symbol="BTCUSDT", price=104.0)
        assert trailed is False
    finally:
        await pool.close()
