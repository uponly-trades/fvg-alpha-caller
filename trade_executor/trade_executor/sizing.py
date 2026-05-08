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
) -> SizeResult:
    if entry <= 0 or sl <= 0:
        return SizeResult(skip_reason="bad_levels")
    sl_distance_pct = abs(entry - sl) / entry * 100
    if sl_distance_pct <= 0:
        return SizeResult(skip_reason="zero_sl_distance")

    target_risk_usdt = 0.0
    capped = False
    if fixed_risk_usdt is not None and fixed_risk_usdt > 0:
        target_risk_usdt = fixed_risk_usdt
        raw_notional = fixed_risk_usdt / (sl_distance_pct / 100)
        cap = max_notional_usdt if max_notional_usdt is not None and max_notional_usdt > 0 else raw_notional
        notional = min(raw_notional, cap)
        capped = notional < raw_notional
    elif fixed_notional_usdt is not None and fixed_notional_usdt > 0:
        notional = fixed_notional_usdt
        target_risk_usdt = notional * (sl_distance_pct / 100)
    else:
        target_risk_usdt = balance * risk_pct / 100
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
    if actual_notional < meta.min_notional:
        return SizeResult(
            notional_usdt=actual_notional,
            target_risk_usdt=target_risk_usdt,
            expected_pnl_1r_usdt=actual_expected_pnl_1r,
            capped=capped,
            skip_reason="min_notional",
        )
    return SizeResult(
        qty=qty,
        notional_usdt=actual_notional,
        margin_usdt=actual_notional / max(1, leverage),
        target_risk_usdt=target_risk_usdt,
        expected_pnl_1r_usdt=actual_expected_pnl_1r,
        capped=capped,
    )
