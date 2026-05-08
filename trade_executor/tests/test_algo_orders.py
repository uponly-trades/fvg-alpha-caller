import pytest

from trade_executor.algo_orders import (
    adjust_sl_for_mark,
    adjust_tp_for_mark,
    algo_id_of,
    cancel_algo,
    fetch_mark_price,
    place_algo_stop,
)


# ────────────────────────── adjust_sl_for_mark ──────────────────────────

def test_adjust_sl_long_safe_sl_unchanged():
    # SL well below mark → no change
    assert adjust_sl_for_mark(side="long", sl_price=95.0, mark=100.0) == 95.0


def test_adjust_sl_long_too_close_nudged_down():
    # SL above mark would trigger immediately. Force below mark - 0.15%.
    out = adjust_sl_for_mark(side="long", sl_price=100.5, mark=100.0)
    assert out < 100.0
    assert out == pytest.approx(100.0 - 100.0 * 0.0015)


def test_adjust_sl_short_safe_sl_unchanged():
    assert adjust_sl_for_mark(side="short", sl_price=105.0, mark=100.0) == 105.0


def test_adjust_sl_short_too_close_nudged_up():
    out = adjust_sl_for_mark(side="short", sl_price=99.5, mark=100.0)
    assert out > 100.0
    assert out == pytest.approx(100.0 + 100.0 * 0.0015)


# ────────────────────────── adjust_tp_for_mark ──────────────────────────

def test_adjust_tp_long_safe_tp_unchanged():
    assert adjust_tp_for_mark(side="long", tp_price=110.0, mark=100.0) == 110.0


def test_adjust_tp_long_too_close_nudged_up():
    out = adjust_tp_for_mark(side="long", tp_price=99.5, mark=100.0)
    assert out > 100.0


def test_adjust_tp_short_too_close_nudged_down():
    out = adjust_tp_for_mark(side="short", tp_price=100.5, mark=100.0)
    assert out < 100.0


# ────────────────────────── place_algo_stop ─────────────────────────────

class FakeEx:
    def __init__(self, response=None):
        self.response = response or {"algoId": "algo-1"}
        self.calls = []

    def price_to_precision(self, symbol, price):
        return f"{price:.4f}"

    async def fapiPrivatePostAlgoOrder(self, params):
        self.calls.append(params)
        return self.response

    async def fapiPrivateDeleteAlgoOrder(self, params):
        self.calls.append(("delete", params))
        return {}

    async def fapiPublicGetPremiumIndex(self, params):
        return {"markPrice": "100.5"}


@pytest.mark.asyncio
async def test_place_algo_stop_uses_conditional_algo_type():
    ex = FakeEx()
    await place_algo_stop(ex, symbol="BTCUSDT", close_side="SELL", trigger_price=95.0)
    assert ex.calls[0]["algoType"] == "CONDITIONAL"
    assert ex.calls[0]["type"] == "STOP_MARKET"
    assert ex.calls[0]["closePosition"] == "true"
    assert ex.calls[0]["triggerPrice"] == "95.0000"


@pytest.mark.asyncio
async def test_place_algo_stop_rejects_invalid_trigger():
    ex = FakeEx()
    with pytest.raises(ValueError):
        await place_algo_stop(ex, symbol="BTCUSDT", close_side="SELL", trigger_price=0)
    with pytest.raises(ValueError):
        await place_algo_stop(ex, symbol="BTCUSDT", close_side="SELL", trigger_price=None)


@pytest.mark.asyncio
async def test_place_algo_stop_take_profit_type():
    ex = FakeEx()
    await place_algo_stop(
        ex, symbol="BTCUSDT", close_side="SELL", trigger_price=110.0,
        order_type="TAKE_PROFIT_MARKET",
    )
    assert ex.calls[0]["type"] == "TAKE_PROFIT_MARKET"


# ────────────────────────── algo_id_of ──────────────────────────────────

def test_algo_id_prefers_algoId():
    assert algo_id_of({"algoId": "a", "orderId": "b", "id": "c"}) == "a"


def test_algo_id_fallback_orderId():
    assert algo_id_of({"orderId": "b", "id": "c"}) == "b"


def test_algo_id_fallback_id():
    assert algo_id_of({"id": "c"}) == "c"


# ────────────────────────── cancel_algo ─────────────────────────────────

@pytest.mark.asyncio
async def test_cancel_algo_swallows_unknown_order():
    class FakeUnknown:
        async def fapiPrivateDeleteAlgoOrder(self, params):
            raise Exception("binanceusdm {\"code\":-2011,\"msg\":\"Unknown order sent.\"}")
    # Should not raise
    await cancel_algo(FakeUnknown(), symbol="BTCUSDT", algo_id="x")


# ────────────────────────── fetch_mark_price ────────────────────────────

@pytest.mark.asyncio
async def test_fetch_mark_price_returns_float():
    ex = FakeEx()
    out = await fetch_mark_price(ex, "BTCUSDT")
    assert out == 100.5


@pytest.mark.asyncio
async def test_fetch_mark_price_returns_none_on_error():
    class FakeErr:
        async def fapiPublicGetPremiumIndex(self, params):
            raise Exception("network down")
    out = await fetch_mark_price(FakeErr(), "BTCUSDT")
    assert out is None
