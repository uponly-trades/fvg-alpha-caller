"""Pure-function guard tests for orchestrator (no DB required)."""
import pytest

from trade_executor.orchestrator import validate_sl_off_margin_combo


def test_sl_on_allows_any_margin_mode():
    # SL ON + ISOLATED → OK
    assert validate_sl_off_margin_combo(sl_enabled=True, margin_mode="ISOLATED") is None
    # SL ON + CROSSED → also OK; only SL OFF has the margin constraint
    assert validate_sl_off_margin_combo(sl_enabled=True, margin_mode="CROSSED") is None


def test_sl_off_with_isolated_passes():
    assert validate_sl_off_margin_combo(sl_enabled=False, margin_mode="ISOLATED") is None


def test_sl_off_with_crossed_rejects_with_reason():
    reason = validate_sl_off_margin_combo(sl_enabled=False, margin_mode="CROSSED")
    assert reason is not None
    assert "ISOLATED" in reason
    assert "SL OFF" in reason
