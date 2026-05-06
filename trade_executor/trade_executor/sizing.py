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
) -> SizeResult:
    if entry <= 0 or sl <= 0:
        return SizeResult(skip_reason="bad_levels")
    sl_distance_pct = abs(entry - sl) / entry * 100
    if sl_distance_pct <= 0:
        return SizeResult(skip_reason="zero_sl_distance")
    risk_usdt = balance * risk_pct / 100
    notional = risk_usdt / (sl_distance_pct / 100)
    if notional < meta.min_notional:
        return SizeResult(notional_usdt=notional, skip_reason="min_notional")
    qty_raw = notional / entry
    qty = round_step(qty_raw, meta.step_size)
    if qty <= 0:
        return SizeResult(notional_usdt=notional, skip_reason="qty_zero")
    actual_notional = qty * entry
    if actual_notional < meta.min_notional:
        return SizeResult(notional_usdt=actual_notional, skip_reason="min_notional")
    return SizeResult(
        qty=qty,
        notional_usdt=actual_notional,
        margin_usdt=actual_notional / max(1, leverage),
    )
