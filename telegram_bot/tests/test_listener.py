import json
import os

import pytest

from telegram_bot.db import create_pool


pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text, kwargs))


@pytest.mark.asyncio
async def test_handle_payload_trade_opened_dispatches_message():
    from telegram_bot.listener import handle_payload
    pool = await create_pool(os.environ["TEST_DATABASE_URL"])
    try:
        async with pool.acquire() as conn:
            await conn.execute("INSERT INTO users (telegram_id, created_at, updated_at) VALUES (12345, 0, 0) ON CONFLICT DO NOTHING")
            uid = await conn.fetchval("SELECT id FROM users WHERE telegram_id=12345")
            await conn.execute("DELETE FROM user_trades WHERE id=$1", f"{uid}-d-1")
            await conn.execute(
                """INSERT INTO user_trades (id, user_id, decision_id, symbol, tf, direction,
                     leverage, margin_usdt, notional_usdt, qty, entry, sl, sl_current, tp1, tp2,
                     status, opened_at)
                   VALUES ($1,$2,'d-1','BTCUSDT','1h','long',5,10,50,0.001,100,95,95,105,110,'open',0)
                """,
                f"{uid}-d-1", uid,
            )

        bot = FakeBot()
        await handle_payload(pool, bot, "trade_opened", {"trade_id": f"{uid}-d-1", "user_id": uid})
        assert len(bot.sent) == 1
        assert bot.sent[0][0] == 12345
        assert "OPENED" in bot.sent[0][1]
        assert "TradingView" in bot.sent[0][1]
        assert bot.sent[0][2].get("parse_mode") == "HTML"
    finally:
        await pool.close()
