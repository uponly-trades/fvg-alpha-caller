from __future__ import annotations

from dataclasses import dataclass
from math import floor


@dataclass(frozen=True)
class SymbolMeta:
    step_size: float
    min_notional: float


@dataclass(frozen=True)
class SizeResult:
    qty: float = 0.0
    notional_usdt: float = 0.0
    margin_usdt: float = 0.0
    target_risk_usdt: float = 0.0
    expected_pnl_1r_usdt: float = 0.0
    capped: bool = False
    skip_reason: str | None = None


def round_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    return floor(value / step) * step


def compute_size(
    *,
    balance: float,
    risk_pct: float,
    entry: float,
    sl: float,
    leverage: int,
    meta: SymbolMeta,
    fixed_notional_usdt: float | None = None,
    fixed_risk_usdt: float | None = None,
    max_notional_usdt: float | None = None,
    free_balance: float | None = None,
    margin_usage_cap: float | None = None,
    risk_mode: str = "percent",
) -> SizeResult:
    if entry <= 0 or sl <= 0:
        return SizeResult(skip_reason="bad_levels")
    sl_distance_pct = abs(entry - sl) / entry * 100
    if sl_distance_pct <= 0:
        return SizeResult(skip_reason="zero_sl_distance")

    if str(risk_mode).lower() == "fixed" and fixed_risk_usdt and fixed_risk_usdt > 0:
        target_risk_usdt = float(fixed_risk_usdt)
    else:
        target_risk_usdt = balance * risk_pct / 100
    capped = False
    notional = target_risk_usdt / (sl_distance_pct / 100)

    expected_pnl_1r_usdt = notional * (sl_distance_pct / 100)
    if notional < meta.min_notional:
        return SizeResult(
            notional_usdt=notional,
            target_risk_usdt=target_risk_usdt,
            expected_pnl_1r_usdt=expected_pnl_1r_usdt,
            capped=capped,
            skip_reason="min_notional",
        )
    qty_raw = notional / entry
    qty = round_step(qty_raw, meta.step_size)
    if qty <= 0:
        return SizeResult(
            notional_usdt=notional,
            target_risk_usdt=target_risk_usdt,
            expected_pnl_1r_usdt=expected_pnl_1r_usdt,
            capped=capped,
            skip_reason="qty_zero",
        )
    actual_notional = qty * entry
    actual_expected_pnl_1r = actual_notional * (sl_distance_pct / 100)
    actual_margin = actual_notional / max(1, leverage)
    if actual_notional < meta.min_notional:
        return SizeResult(
            notional_usdt=actual_notional,
            margin_usdt=actual_margin,
            target_risk_usdt=target_risk_usdt,
            expected_pnl_1r_usdt=actual_expected_pnl_1r,
            capped=capped,
            skip_reason="min_notional",
        )
    if free_balance is not None and margin_usage_cap is not None:
        allowed_margin = max(0.0, float(free_balance)) * max(0.0, float(margin_usage_cap))
        if actual_margin > allowed_margin:
            return SizeResult(
                qty=qty,
                notional_usdt=actual_notional,
                margin_usdt=actual_margin,
                target_risk_usdt=target_risk_usdt,
                expected_pnl_1r_usdt=actual_expected_pnl_1r,
                capped=capped,
                skip_reason="margin_required",
            )
    return SizeResult(
        qty=qty,
        notional_usdt=actual_notional,
        margin_usdt=actual_margin,
        target_risk_usdt=target_risk_usdt,
        expected_pnl_1r_usdt=actual_expected_pnl_1r,
        capped=capped,
    )
