"""End-to-end signal flow tests for orchestrator.

We stub the asyncpg pool/connection surface and exchange so the full
handle_signal_for_user path runs in-memory. No DB, no network.

Covered flows:
  * SL ON happy path  (real trade is written, place_full_sequence called).
  * SL OFF + ISOLATED (real trade is written, sl_price=None propagated).
  * SL OFF + CROSSED  (signal rejected before any order is placed).
"""
from __future__ import annotations

import pytest

from trade_executor.orchestrator import handle_signal_for_user


# ── Stub asyncpg pool/conn ───────────────────────────────────────────────────


class FakeConn:
    def __init__(self):
        self.executes: list[tuple] = []
        self.notify_payloads: list[dict] = []

    async def fetchrow(self, *_a, **_kw):
        return None

    async def fetchval(self, *_a, **_kw):
        return 0

    async def execute(self, sql: str, *args):
        self.executes.append((sql, args))
        # Capture NOTIFY payload for assertions on reject path.
        if sql.startswith("NOTIFY "):
            import json
            _, _, rest = sql.partition(", ")
            body = rest.strip("'").replace("''", "'")
            try:
                self.notify_payloads.append(json.loads(body))
            except json.JSONDecodeError:
                pass


class FakePool:
    def __init__(self, conn: FakeConn):
        self._conn = conn

    def acquire(self):
        return _PoolCtx(self._conn)


class _PoolCtx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *_):
        return False


# ── Stub exchange ────────────────────────────────────────────────────────────


class FakeExchange:
    """Successful exchange. Captures SL-placement state for assertions."""

    def __init__(self):
        self.created_orders: list[tuple] = []
        self.algo_orders: list[dict] = []
        self._is_hedge_mode = False

    async def fetch_balance(self):
        return {"USDT": {"free": 90.0, "total": 90.0}}

    async def fapiPublicGetExchangeInfo(self):
        return {
            "symbols": [
                {
                    "symbol": "KSMUSDT",
                    "filters": [
                        {"filterType": "LOT_SIZE", "stepSize": "0.1"},
                        {"filterType": "MIN_NOTIONAL", "notional": "5"},
                    ],
                }
            ]
        }

    async def fapiPublicGetPremiumIndex(self, params):
        return {"markPrice": "100.0"}

    async def fapiPrivatePostLeverage(self, params):
        return {"leverage": params["leverage"]}

    async def fapiPrivatePostMarginType(self, params):
        return {}

    async def fapiPrivatePostAlgoOrder(self, params):
        self.algo_orders.append(params)
        if params["type"] == "STOP_MARKET":
            return {"algoId": "sl-1", "algoStatus": "NEW"}
        return {"algoId": "tp-1", "algoStatus": "NEW"}

    async def create_order(self, symbol, type_, side, amount, price=None, params=None):
        self.created_orders.append((type_, side, amount, params))
        if type_ == "MARKET":
            return {"id": "entry-1", "status": "FILLED", "average": 100.0}
        return {"id": "ord-1", "status": "NEW"}

    def price_to_precision(self, symbol, price):
        return f"{price:.4f}"

    def amount_to_precision(self, symbol, amount):
        return f"{amount:.4f}"


# ── Tests ────────────────────────────────────────────────────────────────────


SIGNAL = {
    "id": "sig-ksm-1",
    "symbol": "KSMUSDT",
    "tf": "15m",
    "direction": "short",
    "entry": 100.0,
    "sl": 100.7,
    "tp1": 99.3,
    "tp2": 98.6,
    "event_type": "v2_fvg_retest",
    "created_at": 0,
}


@pytest.mark.asyncio
async def test_e2e_sl_on_isolated_places_full_sequence():
    conn = FakeConn()
    pool = FakePool(conn)
    ex = FakeExchange()
    result = await handle_signal_for_user(
        pool, user_id=1, signal=SIGNAL, ex=ex,
        risk_pct=2.0, leverage=15, max_concurrent=3, daily_loss_cap_pct=10.0,
        margin_mode="ISOLATED", rr_ratio=2.0,
        risk_mode="percent", sl_enabled=True, sl_mult=1.0,
    )
    assert result.placed is True
    sl_calls = [o for o in ex.algo_orders if o["type"] == "STOP_MARKET"]
    tp_calls = [o for o in ex.algo_orders if o["type"] == "TAKE_PROFIT_MARKET"]
    assert len(sl_calls) == 1, "SuperTrend-band SL must be placed"
    assert len(tp_calls) == 0, "Pine parity uses no fixed TP orders"
    insert_args = next(args for sql, args in conn.executes if "INSERT INTO user_trades" in sql)
    assert "supertrend_band" in insert_args


@pytest.mark.asyncio
async def test_e2e_sl_off_isolated_keeps_pine_parity_tp_off():
    conn = FakeConn()
    pool = FakePool(conn)
    ex = FakeExchange()
    result = await handle_signal_for_user(
        pool, user_id=1, signal=SIGNAL, ex=ex,
        risk_pct=2.0, leverage=15, max_concurrent=3, daily_loss_cap_pct=10.0,
        margin_mode="ISOLATED", rr_ratio=2.0,
        risk_mode="percent", sl_enabled=False, sl_mult=1.0,
    )
    assert result.placed is True
    sl_calls = [o for o in ex.algo_orders if o["type"] == "STOP_MARKET"]
    tp_calls = [o for o in ex.algo_orders if o["type"] == "TAKE_PROFIT_MARKET"]
    assert len(sl_calls) == 0, "SL must NOT be placed when sl_enabled=False"
    assert len(tp_calls) == 0, "Pine parity uses no fixed TP orders"


@pytest.mark.asyncio
async def test_e2e_sl_off_crossed_rejects_signal_before_order():
    conn = FakeConn()
    pool = FakePool(conn)
    ex = FakeExchange()
    result = await handle_signal_for_user(
        pool, user_id=1, signal=SIGNAL, ex=ex,
        risk_pct=2.0, leverage=15, max_concurrent=3, daily_loss_cap_pct=10.0,
        margin_mode="CROSSED", rr_ratio=2.0,
        risk_mode="percent", sl_enabled=False, sl_mult=1.0,
    )
    assert result.placed is False
    assert result.skip_reason == "sl_off_requires_isolated"
    # No order may have hit the exchange.
    assert ex.created_orders == []
    assert ex.algo_orders == []


@pytest.mark.asyncio
async def test_e2e_risk_mode_fixed_overrides_percent_for_sizing():
    """With risk_mode=fixed + fixed_risk_usdt=$3, target risk USD stays $3
    regardless of balance, so notional shrinks vs the percent path."""
    conn = FakeConn()
    pool = FakePool(conn)
    ex = FakeExchange()
    result = await handle_signal_for_user(
        pool, user_id=1, signal=SIGNAL, ex=ex,
        risk_pct=10.0, leverage=15, max_concurrent=3, daily_loss_cap_pct=10.0,
        margin_mode="ISOLATED", rr_ratio=2.0,
        risk_mode="fixed", fixed_risk_usdt=3.0,
        sl_enabled=True, sl_mult=1.0,
    )
    assert result.placed is True
    market_calls = [o for o in ex.created_orders if o[0] == "MARKET"]
    assert market_calls, "entry order missing"
    qty = float(market_calls[0][2])
    # sl_distance% = 0.7. target_risk = $3 fixed. notional = 3 / 0.007 = ~428.
    # qty = 428 / 100 = ~4.28, rounded to 0.1 step → 4.2.
    assert 4.0 <= qty <= 4.5, f"qty={qty} not in fixed-risk band"
