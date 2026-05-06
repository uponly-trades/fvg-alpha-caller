import time

import pytest

from trade_executor.gate import check_user_gate, GateResult


def _user(**over):
    base = dict(
        id=1, enabled=True, paused_until=None,
        risk_pct=2.0, leverage=5, max_concurrent=3,
        daily_loss_cap_pct=6.0,
    )
    base.update(over)
    return base


def test_disabled_blocks():
    res = check_user_gate(_user(enabled=False), open_count=0, today_pnl_pct=0,
                          balance_usdt=100, decision_id="d1", existing_trade=False)
    assert res.skip_reason == "user_disabled"


def test_paused_until_future_blocks():
    future = int(time.time() * 1000) + 60000
    res = check_user_gate(_user(paused_until=future), open_count=0, today_pnl_pct=0,
                          balance_usdt=100, decision_id="d1", existing_trade=False)
    assert res.skip_reason == "paused"


def test_paused_until_past_does_not_block():
    past = int(time.time() * 1000) - 60000
    res = check_user_gate(_user(paused_until=past), open_count=0, today_pnl_pct=0,
                          balance_usdt=100, decision_id="d1", existing_trade=False)
    assert res.skip_reason is None


def test_daily_cap_hit_blocks_and_flags_pause():
    res = check_user_gate(_user(), open_count=0, today_pnl_pct=-7.0,
                          balance_usdt=100, decision_id="d1", existing_trade=False)
    assert res.skip_reason == "daily_cap_hit"
    assert res.should_pause_forever is True


def test_max_concurrent_blocks():
    res = check_user_gate(_user(max_concurrent=3), open_count=3, today_pnl_pct=0,
                          balance_usdt=100, decision_id="d1", existing_trade=False)
    assert res.skip_reason == "max_concurrent"


def test_idempotency_blocks():
    res = check_user_gate(_user(), open_count=0, today_pnl_pct=0,
                          balance_usdt=100, decision_id="d1", existing_trade=True)
    assert res.skip_reason == "duplicate"


def test_low_balance_blocks():
    res = check_user_gate(_user(), open_count=0, today_pnl_pct=0,
                          balance_usdt=4.0, decision_id="d1", existing_trade=False)
    assert res.skip_reason == "low_balance"


def test_pass_when_all_clear():
    res = check_user_gate(_user(), open_count=0, today_pnl_pct=0,
                          balance_usdt=100, decision_id="d1", existing_trade=False)
    assert res.skip_reason is None
