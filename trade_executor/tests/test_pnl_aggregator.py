import os
from datetime import date

import pytest

from trade_executor import db
from trade_executor.pnl_aggregator import reconcile_user, classify_close


def test_classify_close_tp2_long():
    assert classify_close(direction="long", filled_at_tp_id="tp", filled_at_sl_id=None,
                          status_before="open") == "closed_tp2"


def test_classify_close_sl_trailed_breakeven():
    assert classify_close(direction="long", filled_at_tp_id=None, filled_at_sl_id="sl",
                          status_before="tp1_trailed") == "closed_breakeven"


def test_classify_close_sl_open_loss():
    assert classify_close(direction="long", filled_at_tp_id=None, filled_at_sl_id="sl",
                          status_before="open") == "closed_sl"


pytestmark_db = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


class FakeEx:
    def __init__(self, fills):
        self._fills = fills

    async def fetch_my_trades(self, symbol, since=None):
        return self._fills.get(symbol, [])

    async def fetch_balance(self):
        return {"USDT": {"free": 100.0}}


@pytestmark_db
@pytest.mark.asyncio
async def test_reconcile_marks_tp2_close_and_updates_daily():
    pool = await db.create_pool(os.environ["TEST_DATABASE_URL"])
    try:
        async with pool.acquire() as conn:
            await conn.execute("INSERT INTO users (telegram_id, created_at, updated_at) VALUES (901, 0, 0) ON CONFLICT DO NOTHING")
            uid = await conn.fetchval("SELECT id FROM users WHERE telegram_id=901")
            await conn.execute("DELETE FROM user_trades WHERE user_id=$1", uid)
            await conn.execute(
                """
                INSERT INTO user_trades (id, user_id, decision_id, symbol, tf, direction,
                  leverage, margin_usdt, notional_usdt, qty, entry, sl, sl_current, tp1, tp2,
                  status, entry_order_id, sl_order_id, tp_order_id, opened_at)
                VALUES ($1,$2,'p-1','BTCUSDT','1h','long',5,20,100,1.0,100,95,95,105,110,'open','e1','s1','t1',0)
                """,
                f"{uid}-p-1", uid,
            )

        ex = FakeEx(fills={
            "BTCUSDT": [
                {"order": "t1", "side": "sell", "price": 110.0, "amount": 1.0, "fee": {"cost": 0.05, "currency": "USDT"}, "timestamp": 1000},
            ]
        })
        await reconcile_user(pool, ex=ex, user_id=uid)

        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT status, pnl_usdt, fees_usdt FROM user_trades WHERE id=$1",
                                       f"{uid}-p-1")
            day = await conn.fetchrow("SELECT realized_pnl_usdt, trades_count, wins_count FROM user_daily_pnl WHERE user_id=$1 AND day=CURRENT_DATE", uid)
        assert row["status"] == "closed_tp2"
        assert row["pnl_usdt"] == pytest.approx(10.0, rel=1e-2)
        assert day["trades_count"] == 1
        assert day["wins_count"] == 1
    finally:
        await pool.close()
