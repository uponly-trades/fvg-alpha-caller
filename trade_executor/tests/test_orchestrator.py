import os

import pytest

from trade_executor import db
from trade_executor.orchestrator import handle_signal_for_user

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


class FakeEx:
    """Successful path."""

    def __init__(self):
        self.balance = {"USDT": {"free": 100.0}}
        self.markets = {"BTCUSDT": {"limits": {"amount": {"min": 0.001}}, "precision": {"amount": 3}}}

    async def fetch_balance(self):
        return self.balance

    async def load_markets(self):
        return self.markets

    async def fapiPublic_get_exchangeinfo(self):
        return {"symbols": [{"symbol": "BTCUSDT", "filters": [
            {"filterType": "LOT_SIZE", "stepSize": "0.001"},
            {"filterType": "MIN_NOTIONAL", "notional": "5"},
        ]}]}

    async def fapiPrivatePostLeverage(self, params): return {}
    async def fapiPrivatePostMarginType(self, params): return {}

    async def create_order(self, symbol, type_, side, amount, price=None, params=None):
        if type_ == "MARKET":
            return {"id": "e1", "status": "FILLED", "average": 100.5}
        if type_ == "STOP_MARKET":
            return {"id": "s1", "status": "NEW"}
        if type_ == "TAKE_PROFIT_MARKET":
            return {"id": "t1", "status": "NEW"}


@pytest.mark.asyncio
async def test_orchestrator_writes_open_trade(monkeypatch):
    pool = await db.create_pool(os.environ["TEST_DATABASE_URL"])
    try:
        async with pool.acquire() as conn:
            await conn.execute("INSERT INTO users (telegram_id, enabled, binance_api_key_enc, binance_api_secret_enc, created_at, updated_at) VALUES (777, true, '\\x00', '\\x00', 0, 0) ON CONFLICT DO NOTHING")
            uid = await conn.fetchval("SELECT id FROM users WHERE telegram_id=777")

        signal = {
            "id": "dec-1", "symbol": "BTCUSDT", "tf": "1h", "direction": "long",
            "entry": 100.0, "sl": 95.0, "tp1": 105.0, "tp2": 110.0,
        }
        result = await handle_signal_for_user(
            pool, user_id=uid, signal=signal, ex=FakeEx(),
            risk_pct=2.0, leverage=5, max_concurrent=3, daily_loss_cap_pct=6.0,
        )
        assert result.placed is True

        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM user_trades WHERE id=$1", f"{uid}-dec-1")
        assert row["status"] == "open"
        assert row["entry"] == pytest.approx(100.5)
        assert row["sl_order_id"] == "s1"
        assert row["tp_order_id"] == "t1"
    finally:
        await pool.close()
