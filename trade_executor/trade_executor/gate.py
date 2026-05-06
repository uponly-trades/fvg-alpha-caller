from __future__ import annotations

import time
from dataclasses import dataclass

MIN_BALANCE_USDT = 5.0


@dataclass(frozen=True)
class GateResult:
    skip_reason: str | None = None
    should_pause_forever: bool = False


def check_user_gate(
    user: dict,
    *,
    open_count: int,
    today_pnl_pct: float,
    balance_usdt: float,
    decision_id: str,
    existing_trade: bool,
) -> GateResult:
    if not user.get("enabled"):
        return GateResult(skip_reason="user_disabled")
    paused_until = user.get("paused_until")
    if paused_until and paused_until > int(time.time() * 1000):
        return GateResult(skip_reason="paused")
    cap = float(user["daily_loss_cap_pct"])
    if today_pnl_pct <= -cap:
        return GateResult(skip_reason="daily_cap_hit", should_pause_forever=True)
    if open_count >= int(user["max_concurrent"]):
        return GateResult(skip_reason="max_concurrent")
    if existing_trade:
        return GateResult(skip_reason="duplicate")
    if balance_usdt < MIN_BALANCE_USDT:
        return GateResult(skip_reason="low_balance")
    return GateResult(skip_reason=None)
