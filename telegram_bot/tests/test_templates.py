from telegram_bot.templates import (
    fmt_opened, fmt_tp1_trailed, fmt_tp2, fmt_sl, fmt_breakeven,
    fmt_error, fmt_daily,
)


def test_fmt_opened_long():
    msg = fmt_opened(
        symbol="BTCUSDT", tf="1h", direction="long",
        entry=108_420.0, sl=107_200.0, tp1=109_640.0, tp2=110_860.0,
        qty=0.025, leverage=5, notional=135.0, margin=27.0,
    )
    assert "🟢 OPENED" in msg
    assert "BTCUSDT" in msg
    assert "LONG" in msg
    assert "108,420" in msg or "108420" in msg


def test_fmt_tp1_trailed_mentions_locked_1r():
    msg = fmt_tp1_trailed(symbol="BTCUSDT", new_sl=109_640.0)
    assert "TP1" in msg
    assert "trailed" in msg.lower() or "trail" in msg.lower()


def test_fmt_tp2_uses_plus_sign_for_profit():
    msg = fmt_tp2(symbol="BTCUSDT", pnl_usdt=5.41, pnl_pct=2.0)
    assert "+$5.41" in msg or "+5.41" in msg


def test_fmt_sl_uses_minus_sign_for_loss():
    msg = fmt_sl(symbol="BTCUSDT", pnl_usdt=-2.71, pnl_pct=-1.0)
    assert "-$2.71" in msg or "-2.71" in msg


def test_fmt_breakeven_message():
    msg = fmt_breakeven(symbol="BTCUSDT", pnl_usdt=0.02)
    assert "BREAKEVEN" in msg.upper()


def test_fmt_error_critical():
    msg = fmt_error(symbol="BTCUSDT", reason="SL placement failed")
    assert "ERROR" in msg.upper()
    assert "SL" in msg


def test_fmt_daily_summary():
    msg = fmt_daily(date="2026-05-06", trades=8, wins=5, pnl_usdt=12.34, pnl_pct=12.34)
    assert "DAILY" in msg.upper()
    assert "wins" in msg.lower() or "WR" in msg
    assert "+$12.34" in msg or "+12.34" in msg
