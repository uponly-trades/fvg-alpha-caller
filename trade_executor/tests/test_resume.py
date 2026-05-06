import os

import pytest

from trade_executor import db
from trade_executor.resume import resume_in_flight


pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


class FakeEx:
    def __init__(self, entry_filled=True):
        self.entry_filled = entry_filled
        self.calls = []

    async def fetch_order(self, order_id, symbol):
        self.calls.append(("fetch_order", order_id))
        return {"id": order_id, "status": "FILLED" if self.entry_filled else "NEW",
                "average": 100.5}

    async def cancel_order(self, order_id, symbol):
        self.calls.append(("cancel", order_id))
        return {"id": order_id}

    async def create_order(self, symbol, type_, side, amount, price=None, params=None):
        self.calls.append(("create", type_, params))
        if type_ == "STOP_MARKET": return {"id": "sl-new"}
        if type_ == "TAKE_PROFIT_MARKET": return {"id": "tp-new"}


@pytest.mark.asyncio
async def test_resume_opening_with_filled_entry_places_sl_tp():
    pool = await db.create_pool(os.environ["TEST_DATABASE_URL"])
    try:
        async with pool.acquire() as conn:
            await conn.execute("INSERT INTO users (telegram_id, created_at, updated_at) VALUES (910, 0, 0) ON CONFLICT DO NOTHING")
            uid = await conn.fetchval("SELECT id FROM users WHERE telegram_id=910")
            await conn.execute("DELETE FROM user_trades WHERE user_id=$1", uid)
            await conn.execute(
                """
                INSERT INTO user_trades (id, user_id, decision_id, symbol, tf, direction,
                  leverage, margin_usdt, notional_usdt, qty, entry, sl, sl_current, tp1, tp2,
                  status, entry_order_id, opened_at)
                VALUES ($1,$2,'r-1','BTCUSDT','1h','long',5,10,50,0.001,100,95,95,105,110,'opening','e1',0)
                """,
                f"{uid}-r-1", uid,
            )

        await resume_in_flight(pool, ex_factory=lambda u: FakeEx(entry_filled=True))

        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT status, sl_order_id, tp_order_id FROM user_trades WHERE id=$1",
                                       f"{uid}-r-1")
        assert row["status"] == "open"
        assert row["sl_order_id"] == "sl-new"
        assert row["tp_order_id"] == "tp-new"
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_resume_opening_with_unfilled_entry_marks_error():
    pool = await db.create_pool(os.environ["TEST_DATABASE_URL"])
    try:
        async with pool.acquire() as conn:
            await conn.execute("INSERT INTO users (telegram_id, created_at, updated_at) VALUES (911, 0, 0) ON CONFLICT DO NOTHING")
            uid = await conn.fetchval("SELECT id FROM users WHERE telegram_id=911")
            await conn.execute("DELETE FROM user_trades WHERE user_id=$1", uid)
            await conn.execute(
                """
                INSERT INTO user_trades (id, user_id, decision_id, symbol, tf, direction,
                  leverage, margin_usdt, notional_usdt, qty, entry, sl, sl_current, tp1, tp2,
                  status, entry_order_id, opened_at)
                VALUES ($1,$2,'r-2','BTCUSDT','1h','long',5,10,50,0.001,100,95,95,105,110,'opening','e2',0)
                """,
                f"{uid}-r-2", uid,
            )

        await resume_in_flight(pool, ex_factory=lambda u: FakeEx(entry_filled=False))

        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT status FROM user_trades WHERE id=$1", f"{uid}-r-2")
        assert row["status"] == "error_restart"
    finally:
        await pool.close()
