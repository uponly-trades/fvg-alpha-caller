from telegram_bot.templates import (
    fmt_opened, fmt_tp2, fmt_sl, fmt_breakeven, fmt_manual_close,
    fmt_error, fmt_daily, fmt_balance, fmt_stats, fmt_trade_list,
    fmt_settings, fmt_key_saved, fmt_help, fmt_trade_skipped,
)


_CLOSE_KW = dict(
    tf="15m", direction="long", entry=100.0, sl=99.0, tp1=101.0, tp2=102.0,
    qty=1.0, leverage=10, notional=100.0,
)


def test_fmt_manual_close_neutral_label():
    msg = fmt_manual_close(symbol="DOGEUSDT", pnl_usdt=-0.09, pnl_pct=-0.04, **_CLOSE_KW)
    assert "MANUAL CLOSE" in msg
    assert "DOGEUSDT" in msg
    assert "-$0.09" in msg or "-0.09" in msg


def test_fmt_close_includes_setup_and_rr():
    msg = fmt_sl(symbol="BTCUSDT", pnl_usdt=-2.71, pnl_pct=-1.0, **_CLOSE_KW)
    assert "SL HIT" in msg
    assert "entry" in msg.lower()
    assert "RR" in msg
    assert "10x" in msg


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
    assert "FVG Retest" in msg
    assert "TradingView" in msg
    assert "BINANCE:BTCUSDT.P" in msg


def test_fmt_opened_sl_off_is_clear_not_fake_100pct_sl():
    msg = fmt_opened(
        symbol="ORDIUSDT", tf="15m", direction="short",
        entry=4.1310, sl=0.0, tp1=4.0323, tp2=3.9296,
        qty=42.7, leverage=15, notional=176.35, margin=11.76,
    )
    assert "sl OFF (isolated)" in msg
    assert "+100.00%" not in msg
    assert "interval=15" in msg


def test_fmt_tp2_uses_plus_sign_for_profit():
    msg = fmt_tp2(symbol="BTCUSDT", pnl_usdt=5.41, pnl_pct=2.0, **_CLOSE_KW)
    assert "+$5.41" in msg or "+5.41" in msg


def test_fmt_tp2_single_target_uses_tp_hit_label():
    msg = fmt_tp2(symbol="BTCUSDT", pnl_usdt=5.41, pnl_pct=2.0, **{**_CLOSE_KW, "tp2": 101.0})
    assert "TP HIT" in msg
    assert "TP2 HIT" not in msg
    assert " tp " in msg
    assert "tp2" not in msg


def test_fmt_tp2_multi_target_keeps_tp2_label():
    msg = fmt_tp2(symbol="BTCUSDT", pnl_usdt=5.41, pnl_pct=2.0, **{**_CLOSE_KW, "tp2": 103.0})
    assert "TP2 HIT" in msg
    assert "tp2" in msg


def test_fmt_sl_uses_minus_sign_for_loss():
    msg = fmt_sl(symbol="BTCUSDT", pnl_usdt=-2.71, pnl_pct=-1.0, **_CLOSE_KW)
    assert "-$2.71" in msg or "-2.71" in msg


def test_fmt_breakeven_message():
    msg = fmt_breakeven(symbol="BTCUSDT", pnl_usdt=0.02, **_CLOSE_KW)
    assert "BREAKEVEN" in msg.upper()


def test_fmt_error_critical():
    msg = fmt_error(symbol="BTCUSDT", reason="SL placement failed")
    assert "ERROR" in msg.upper()
    assert "SL" in msg


def test_fmt_error_known_stage_leverage_includes_howto():
    msg = fmt_error(symbol="DOGEUSDT", reason="leverage")
    # User-friendly: should include how-to hints, not just stage code.
    assert "leverage" in msg.lower()
    assert "DOGEUSDT" in msg
    assert "Futures" in msg


def test_fmt_error_known_stage_entry_includes_howto():
    msg = fmt_error(symbol="ETHUSDT", reason="entry")
    assert "entry" in msg.lower() or "MARKET" in msg
    assert "ETHUSDT" in msg
    assert "USDT" in msg or "Trade" in msg


def test_fmt_trade_skipped_min_notional_is_short_and_actionable():
    msg = fmt_trade_skipped(symbol="GRTUSDT", reason="min_notional")
    assert "SKIP" in msg
    assert "GRTUSDT" in msg
    assert "notional" in msg.lower()
    assert "minimum Binance" in msg
    assert len(msg) < 120


def test_fmt_stats_includes_config_block():
    msg = fmt_stats({
        "registered": True,
        "enabled": True,
        "risk_pct": 1.5, "leverage": 10, "margin_mode": "CROSSED", "max_concurrent": 3, "daily_loss_cap_pct": 5.0,
        "today_trades": 0, "today_wins": 0, "today_pnl_usdt": 0.0, "today_pnl_pct": 0.0,
        "closed_trades": 0, "wins": 0, "winrate": 0.0, "pnl_usdt": 0.0,
    })
    assert "Config" in msg
    assert "10x" in msg
    assert "CROSS" in msg
    assert "Risk 1.50% equity" in msg


def test_fmt_daily_summary():
    msg = fmt_daily(date="2026-05-06", trades=8, wins=5, pnl_usdt=12.34, pnl_pct=12.34)
    assert "DAILY" in msg.upper()
    assert "wins" in msg.lower() or "WR" in msg
    assert "+$12.34" in msg or "+12.34" in msg


def test_fmt_help_lists_key_commands():
    msg = fmt_help()
    assert "/setkeys" in msg
    assert "/balance" in msg
    assert "/resume" in msg


def test_fmt_key_saved_only_shows_tail():
    msg = fmt_key_saved("ABCD")
    assert "ABCD" in msg
    assert "API key saved" in msg


def test_fmt_balance_no_keys():
    msg = fmt_balance({"registered": True, "has_keys": False})
    assert "/setkeys" in msg


def test_fmt_balance_with_usdt_values():
    msg = fmt_balance({
        "registered": True,
        "has_keys": True,
        "api_key_tail": "WXYZ",
        "balance": {"free": 10.5, "used": 2, "total": 12.5},
    })
    assert "$10.50" in msg
    assert "$12.50" in msg
    assert "WXYZ" in msg


def test_fmt_settings_row_mapping():
    row = {
        "enabled": True,
        "risk_pct": 2.0,
        "leverage": 10,
        "margin_mode": "CROSSED",
        "max_concurrent": 3,
        "daily_loss_cap_pct": 6.0,
        "api_key_tail": "TAIL",
    }
    msg = fmt_settings(row)
    assert "enabled" in msg
    assert "10x" in msg
    assert "Margin mode: CROSS" in msg
    assert "Risk: 2.00% equity" in msg
    assert "TAIL" in msg


def test_fmt_trade_list_open_trade():
    msg = fmt_trade_list([
        {
            "symbol": "BTCUSDT", "tf": "1h", "direction": "long",
            "status": "open", "entry": 100.0, "sl_current": 95.0,
            "tp1": 105.0, "tp2": 110.0, "pnl_usdt": None,
        }
    ], closed=False)
    assert "BTCUSDT" in msg
    assert "Active" in msg


def test_fmt_stats_winrate():
    msg = fmt_stats({
        "registered": True,
        "today_trades": 2,
        "today_wins": 1,
        "today_pnl_usdt": 3.0,
        "today_pnl_pct": 1.5,
        "closed_trades": 4,
        "wins": 3,
        "winrate": 75.0,
        "pnl_usdt": 9.0,
    })
    assert "75.0%" in msg
    assert "+$9.00" in msg


def test_fmt_trade_skipped_margin_required_is_actionable():
    msg = fmt_trade_skipped(symbol="BTCUSDT", reason="margin_required")
    assert "SKIP" in msg
    assert "BTCUSDT" in msg
    assert "margin" in msg.lower()
    assert "SL" in msg
