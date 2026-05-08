import pytest

from trade_executor.order_placer import place_full_sequence, OrderError


class FakeOK:
    """Mock exchange returning success for all calls."""

    def __init__(self):
        self.calls = []

    async def fapiPrivatePostMultiAssetsMargin(self, params):
        self.calls.append(("multiAssets", params))
        return {}

    async def fapiPrivatePostPositionSideDual(self, params):
        self.calls.append(("posSide", params))
        return {}

    async def fapiPrivatePostLeverage(self, params):
        self.calls.append(("leverage", params))
        return {"leverage": params["leverage"]}

    async def fapiPrivatePostMarginType(self, params):
        self.calls.append(("marginType", params))
        return {}

    async def fapiPrivatePostAlgoOrder(self, params):
        self.calls.append(("algoOrder", params))
        assert params.get("algoType") == "CONDITIONAL", "algoType must be CONDITIONAL"
        if params["type"] == "STOP_MARKET":
            return {"algoId": "sl-1", "algoStatus": "NEW"}
        if params["type"] == "TAKE_PROFIT_MARKET":
            return {"algoId": "tp-1", "algoStatus": "NEW"}
        raise AssertionError(f"unexpected type {params['type']}")

    def price_to_precision(self, symbol, price):
        return f"{price:.4f}"

    async def create_order(self, symbol, type_, side, amount, price=None, params=None):
        self.calls.append(("create", type_, side, amount, params))
        if type_ == "MARKET":
            return {"id": "entry-1", "status": "FILLED", "average": 100.5}
        raise AssertionError(f"unexpected type {type_}")


class FakeSLFails(FakeOK):
    async def fapiPrivatePostAlgoOrder(self, params):
        if params["type"] == "STOP_MARKET":
            raise Exception("SL placement failed")
        return await super().fapiPrivatePostAlgoOrder(params)


@pytest.mark.asyncio
async def test_full_sequence_returns_ids_and_avg():
    ex = FakeOK()
    res = await place_full_sequence(
        ex, symbol="BTCUSDT", side="BUY", qty=0.01,
        sl_price=95.0, tp_price=110.0, leverage=5,
    )
    assert res.entry_order_id == "entry-1"
    assert res.sl_order_id == "sl-1"
    assert res.tp_order_id == "tp-1"
    assert res.avg_price == pytest.approx(100.5)


@pytest.mark.asyncio
async def test_sl_failure_triggers_emergency_close_and_raises():
    ex = FakeSLFails()
    with pytest.raises(OrderError) as exc:
        await place_full_sequence(
            ex, symbol="BTCUSDT", side="BUY", qty=0.01,
            sl_price=95.0, tp_price=110.0, leverage=5,
        )
    assert exc.value.stage == "sl"
    market_close = [c for c in ex.calls if c[0] == "create" and c[1] == "MARKET" and c[4] and c[4].get("reduceOnly")]
    assert len(market_close) == 1
