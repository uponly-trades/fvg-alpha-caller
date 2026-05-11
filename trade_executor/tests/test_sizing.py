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


def test_compute_size_legacy_fixed_notional_does_not_override_risk_percent():
    meta = SymbolMeta(step_size=0.001, min_notional=5.0)
    res = compute_size(
        balance=100, risk_pct=10, entry=100, sl=95, leverage=5,
        meta=meta, fixed_notional_usdt=25,
    )
    assert res.notional_usdt == pytest.approx(200.0)
    assert res.qty == pytest.approx(2.0)
    assert res.margin_usdt == pytest.approx(40.0)


def test_compute_size_legacy_fixed_notional_below_min_is_ignored():
    meta = SymbolMeta(step_size=0.001, min_notional=5.0)
    res = compute_size(
        balance=100, risk_pct=2, entry=100, sl=95, leverage=5,
        meta=meta, fixed_notional_usdt=4.99,
    )
    assert res.skip_reason is None
    assert res.notional_usdt == pytest.approx(40.0)


def test_compute_size_percent_risk_targets_equity_pnl_at_one_percent_sl():
    meta = SymbolMeta(step_size=0.001, min_notional=5.0)
    res = compute_size(
        balance=100, risk_pct=3, entry=100, sl=99, leverage=10, meta=meta,
    )
    assert res.notional_usdt == pytest.approx(300.0)
    assert res.margin_usdt == pytest.approx(30.0)
    assert res.target_risk_usdt == pytest.approx(3.0)
    assert res.expected_pnl_1r_usdt == pytest.approx(3.0)
    assert res.capped is False


def test_compute_size_percent_risk_keeps_dollar_risk_for_wider_sl():
    meta = SymbolMeta(step_size=0.001, min_notional=5.0)
    res = compute_size(
        balance=100, risk_pct=3, entry=100, sl=97, leverage=10, meta=meta,
    )
    assert res.notional_usdt == pytest.approx(100.0)
    assert res.margin_usdt == pytest.approx(10.0)
    assert res.target_risk_usdt == pytest.approx(3.0)
    assert res.expected_pnl_1r_usdt == pytest.approx(3.0)


def test_compute_size_ignores_legacy_fixed_risk_and_notional_params():
    meta = SymbolMeta(step_size=0.001, min_notional=5.0)
    res = compute_size(
        balance=100, risk_pct=3, entry=100, sl=99, leverage=10,
        meta=meta, fixed_notional_usdt=25, fixed_risk_usdt=5, max_notional_usdt=50,
    )
    assert res.notional_usdt == pytest.approx(300.0)
    assert res.expected_pnl_1r_usdt == pytest.approx(3.0)


def test_compute_size_percent_risk_below_exchange_min_skips():
    meta = SymbolMeta(step_size=0.001, min_notional=300.0)
    res = compute_size(
        balance=100, risk_pct=3, entry=100, sl=95, leverage=10, meta=meta,
    )
    assert res.skip_reason == "min_notional"
    assert res.notional_usdt == pytest.approx(60.0)
    assert res.capped is False


def test_compute_size_skips_when_required_margin_exceeds_free_balance_cap():
    meta = SymbolMeta(step_size=0.001, min_notional=5.0)
    res = compute_size(
        balance=100, risk_pct=3, entry=100, sl=99.9, leverage=10, meta=meta,
        free_balance=20, margin_usage_cap=0.70,
    )
    assert res.skip_reason == "margin_required"
    assert res.margin_usdt > 14.0


def test_compute_size_allows_when_required_margin_within_free_balance_cap():
    meta = SymbolMeta(step_size=0.001, min_notional=5.0)
    res = compute_size(
        balance=100, risk_pct=3, entry=100, sl=99, leverage=10, meta=meta,
        free_balance=50, margin_usage_cap=0.70,
    )
    assert res.skip_reason is None
    assert res.notional_usdt == pytest.approx(300.0)
    assert res.margin_usdt == pytest.approx(30.0)
