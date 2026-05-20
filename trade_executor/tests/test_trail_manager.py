import os

# Legacy tests assert the percent-ladder trail behavior. Must be set BEFORE
# importing trail_manager so module-level _V2_TRAIL_MODE captures it.
os.environ.setdefault("V2_TRAIL_MODE", "percent")

import pytest

from trade_executor import db
from trade_executor.trail_manager import (
    maybe_trail,
    r_progress,
    should_replace_supertrend_band,
    trail_sl_for_progress,
)

def test_r_progress_long_and_short():
    assert r_progress(direction="long", entry=100, sl=95, price=107.5) == pytest.approx(1.5)
    assert r_progress(direction="short", entry=100, sl=105, price=92.5) == pytest.approx(1.5)


def test_trail_sl_for_progress_locks_profit_stages():
    assert trail_sl_for_progress(direction="long", entry=100, sl=95, sl_current=95, price=107.5) == pytest.approx(105.0)
    assert trail_sl_for_progress(direction="long", entry=100, sl=95, sl_current=105, price=112.5) == pytest.approx(107.5)
    assert trail_sl_for_progress(direction="short", entry=100, sl=105, sl_current=105, price=92.5) == pytest.approx(95.0)
    assert trail_sl_for_progress(direction="short", entry=100, sl=105, sl_current=95, price=87.5) == pytest.approx(92.5)


def test_trail_sl_for_progress_does_not_loosen_or_repeat_same_stage():
    assert trail_sl_for_progress(direction="long", entry=100, sl=95, sl_current=105, price=107.5) is None
    assert trail_sl_for_progress(direction="short", entry=100, sl=105, sl_current=95, price=92.5) is None
    assert trail_sl_for_progress(direction="long", entry=100, sl=95, sl_current=95, price=104.9) is None


class FakeEx:
    def __init__(self, fail_cancel=False):
        self.cancelled = []
        self.placed = []
        self.orders = []
        self.fail_cancel = fail_cancel

    async def fapiPrivateDeleteAlgoOrder(self, params):
        if self.fail_cancel:
            raise Exception("not found")
        self.cancelled.append((params["algoId"], params["symbol"]))
        return {"algoId": params["algoId"], "algoStatus": "CANCELED"}

    async def fapiPublicGetPremiumIndex(self, params):
        return {"markPrice": "100.0"}

    def price_to_precision(self, symbol, price):
        return f"{float(price):.4f}"

    def amount_to_precision(self, symbol, amount):
        return f"{float(amount):.4f}"

    async def fapiPrivatePostAlgoOrder(self, params):
        self.placed.append((params["type"], params["side"], params))
        return {"algoId": "new-sl"}

    async def create_order(self, symbol, type_, side, amount, price=None, params=None):
        self.orders.append((symbol, type_, side, amount, price, params or {}))
        return {"id": "auto-tp-market", "status": "FILLED", "average": "101.0"}


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not set")
@pytest.mark.asyncio
async def test_long_trails_when_price_reaches_1_5r(monkeypatch):
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


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not set")
@pytest.mark.asyncio
async def test_short_trails_when_price_reaches_1_5r():
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


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not set")
@pytest.mark.asyncio
async def test_no_trail_when_price_below_1_5r_long():
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
        trailed = await maybe_trail(pool, ex=ex, symbol="BTCUSDT", price=107.4)
        assert trailed is False
    finally:
        await pool.close()


# ---------------------------------------------------------------------------
# Structural trail mode (V2_TRAIL_MODE=structural)
# Spec: .specify/specs/dynamic-sltp.md R7
# ---------------------------------------------------------------------------
def test_structural_trail_moves_to_breakeven_at_1r(monkeypatch):
    import importlib
    import trade_executor.trail_manager as tm
    monkeypatch.setenv("V2_TRAIL_MODE", "structural")
    importlib.reload(tm)
    # long: entry 100, sl 95, progress=1.0R at price 105 → BE = 100
    assert tm.trail_sl_for_progress(direction="long", entry=100, sl=95, sl_current=95, price=105) == pytest.approx(100.0)
    # short: entry 100, sl 105, progress=1.0R at price 95 → BE = 100
    assert tm.trail_sl_for_progress(direction="short", entry=100, sl=105, sl_current=105, price=95) == pytest.approx(100.0)
    monkeypatch.setenv("V2_TRAIL_MODE", "percent")
    importlib.reload(tm)


def test_structural_trail_ladder_stages(monkeypatch):
    import importlib
    import trade_executor.trail_manager as tm
    monkeypatch.setenv("V2_TRAIL_MODE", "structural")
    importlib.reload(tm)
    # progress 2.0R locks +1R: long entry 100 sl 95 price 110 → SL 105
    assert tm.trail_sl_for_progress(direction="long", entry=100, sl=95, sl_current=100, price=110) == pytest.approx(105.0)
    # progress 3.0R locks +2R: long entry 100 sl 95 price 115 → SL 110
    assert tm.trail_sl_for_progress(direction="long", entry=100, sl=95, sl_current=105, price=115) == pytest.approx(110.0)
    monkeypatch.setenv("V2_TRAIL_MODE", "percent")
    importlib.reload(tm)


def test_structural_trail_never_loosens(monkeypatch):
    import importlib
    import trade_executor.trail_manager as tm
    monkeypatch.setenv("V2_TRAIL_MODE", "structural")
    importlib.reload(tm)
    # sl_current already above BE → no loosen
    assert tm.trail_sl_for_progress(direction="long", entry=100, sl=95, sl_current=105, price=105) is None


def test_supertrend_band_replacement_only_tightens():
    assert should_replace_supertrend_band(direction="long", sl_current=95, st_band=96) is True
    assert should_replace_supertrend_band(direction="long", sl_current=96, st_band=95) is False
    assert should_replace_supertrend_band(direction="short", sl_current=105, st_band=104) is True
    assert should_replace_supertrend_band(direction="short", sl_current=104, st_band=105) is False


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not set")
@pytest.mark.asyncio
async def test_auto_tp_waits_15m_before_green_partial_close(monkeypatch):
    monkeypatch.setenv("AUTO_TP_MIN_AGE_SEC", "900")
    import importlib
    import trade_executor.trail_manager as tm
    importlib.reload(tm)
    pool = await db.create_pool(os.environ["TEST_DATABASE_URL"])
    try:
        async with pool.acquire() as conn:
            await conn.execute("INSERT INTO users (telegram_id, created_at, updated_at) VALUES (892, 0, 0) ON CONFLICT DO NOTHING")
            uid = await conn.fetchval("SELECT id FROM users WHERE telegram_id=892")
            tid = f"{uid}-auto-wait"
            await conn.execute("DELETE FROM user_trades WHERE id=$1", tid)
            await conn.execute(
                """
                INSERT INTO user_trades (id, user_id, decision_id, symbol, tf, direction,
                  leverage, margin_usdt, notional_usdt, qty, entry, sl, sl_current, tp1, tp2,
                  status, sl_order_id, opened_at)
                VALUES ($1,$2,'auto-wait','BTCUSDT','15m','long',5,10,50,0.004,100,95,95,100,100,'open','sl-auto',CAST(EXTRACT(EPOCH FROM clock_timestamp()) * 1000 AS BIGINT))
                """,
                tid, uid,
            )
        ex = FakeEx()
        trailed = await tm.maybe_trail(pool, ex=ex, symbol="BTCUSDT", price=101.0)
        assert trailed is False
        assert ex.orders == []
    finally:
        await pool.close()
        monkeypatch.setenv("V2_TRAIL_MODE", "percent")
        importlib.reload(tm)


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not set")
@pytest.mark.asyncio
async def test_auto_tp_green_trade_closes_half_and_moves_sl_to_entry(monkeypatch):
    monkeypatch.setenv("AUTO_TP_MIN_AGE_SEC", "900")
    import importlib
    import trade_executor.trail_manager as tm
    importlib.reload(tm)
    pool = await db.create_pool(os.environ["TEST_DATABASE_URL"])
    try:
        async with pool.acquire() as conn:
            await conn.execute("INSERT INTO users (telegram_id, created_at, updated_at) VALUES (893, 0, 0) ON CONFLICT DO NOTHING")
            uid = await conn.fetchval("SELECT id FROM users WHERE telegram_id=893")
            tid = f"{uid}-auto-green"
            await conn.execute("DELETE FROM user_trades WHERE id=$1", tid)
            await conn.execute(
                """
                INSERT INTO user_trades (id, user_id, decision_id, symbol, tf, direction,
                  leverage, margin_usdt, notional_usdt, qty, entry, sl, sl_current, tp1, tp2,
                  status, sl_order_id, opened_at)
                VALUES ($1,$2,'auto-green','BTCUSDT','15m','long',5,10,50,0.004,100,95,95,100,100,'open','sl-auto-old',CAST(EXTRACT(EPOCH FROM clock_timestamp()) * 1000 AS BIGINT) - 901000)
                """,
                tid, uid,
            )
        ex = FakeEx()
        trailed = await tm.maybe_trail(pool, ex=ex, symbol="BTCUSDT", price=101.0)
        assert trailed is True
        assert ex.orders[0][1] == "MARKET"
        assert ex.orders[0][2] == "SELL"
        assert ex.orders[0][3] == pytest.approx(0.002)
        assert ex.placed[0][0] == "STOP_MARKET"
        assert float(ex.placed[0][2]["triggerPrice"]) >= 100.0
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT status, tp1_qty, tp2_qty, sl_current FROM user_trades WHERE id=$1", tid)
        assert row["status"] == "tp1_trailed"
        assert row["tp1_qty"] == pytest.approx(0.002)
        assert row["tp2_qty"] == pytest.approx(0.002)
        assert row["sl_current"] >= 100.0
    finally:
        await pool.close()
        monkeypatch.setenv("V2_TRAIL_MODE", "percent")
        importlib.reload(tm)


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not set")
@pytest.mark.asyncio
async def test_pine_retest_uses_persisted_supertrend_band_not_mark_price():
    pool = await db.create_pool(os.environ["TEST_DATABASE_URL"])
    try:
        async with pool.acquire() as conn:
            await conn.execute("INSERT INTO users (telegram_id, created_at, updated_at) VALUES (891, 0, 0) ON CONFLICT DO NOTHING")
            uid = await conn.fetchval("SELECT id FROM users WHERE telegram_id=891")
            await conn.execute("DELETE FROM user_trades WHERE id=$1", f"{uid}-pine-1")
            await conn.execute(
                """
                INSERT INTO user_trades (id, user_id, decision_id, exit_mode, symbol, tf, direction,
                  leverage, margin_usdt, notional_usdt, qty, entry, sl, sl_current, tp1, tp2,
                  status, sl_order_id, opened_at)
                VALUES ($1,$2,'BTCUSDT_15m_1_1','supertrend_band','BTCUSDT','15m','long',5,10,50,0.001,100,95,95,100,100,'open','sl-old-st',0)
                """,
                f"{uid}-pine-1", uid,
            )
            await conn.execute(
                """
                INSERT INTO supertrend_state (symbol, tf, trend, band, switch_price, bar_time, updated_at)
                VALUES ('BTCUSDT','15m',1,96.25,101,1,CAST(EXTRACT(EPOCH FROM clock_timestamp()) * 1000 AS BIGINT))
                ON CONFLICT (symbol, tf) DO UPDATE SET band=EXCLUDED.band, updated_at=EXCLUDED.updated_at
                """
            )

        ex = FakeEx()
        trailed = await maybe_trail(pool, ex=ex, symbol="BTCUSDT", price=120.0)
        assert trailed is True
        assert ex.cancelled == [("sl-old-st", "BTCUSDT")]
        assert ex.placed[0][2]["triggerPrice"] == "96.2500"

        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT sl_current, sl_order_id FROM user_trades WHERE id=$1", f"{uid}-pine-1")
        assert row["sl_current"] == pytest.approx(96.25)
        assert row["sl_order_id"] == "new-sl"
    finally:
        await pool.close()
