import math

import pytest

from trade_executor.sizing import compute_size, SymbolMeta, round_step


def test_round_step_truncates_down():
    assert round_step(0.123456, 0.001) == pytest.approx(0.123)
    assert round_step(0.999, 0.01) == pytest.approx(0.99)


def test_compute_size_long_basic():
    meta = SymbolMeta(step_size=0.001, min_notional=5.0)
    res = compute_size(balance=100, risk_pct=2, entry=100, sl=95, leverage=5, meta=meta)
    assert res.notional_usdt == pytest.approx(40.0)
    assert res.qty == pytest.approx(0.4, rel=1e-3)
    assert res.margin_usdt == pytest.approx(40 / 5)


def test_compute_size_short_uses_abs_sl_distance():
    meta = SymbolMeta(step_size=0.001, min_notional=5.0)
    res = compute_size(balance=100, risk_pct=2, entry=100, sl=105, leverage=5, meta=meta)
    assert res.notional_usdt == pytest.approx(40.0)


def test_compute_size_below_min_notional_returns_skip():
    meta = SymbolMeta(step_size=0.001, min_notional=10000.0)
    res = compute_size(balance=100, risk_pct=2, entry=100, sl=95, leverage=5, meta=meta)
    assert res.skip_reason == "min_notional"


def test_qty_rounded_to_step():
    meta = SymbolMeta(step_size=0.01, min_notional=5.0)
    res = compute_size(balance=100, risk_pct=2, entry=100, sl=95, leverage=5, meta=meta)
    assert math.isclose(res.qty, 0.40, abs_tol=1e-9)
