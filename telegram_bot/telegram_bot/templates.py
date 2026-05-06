def _money(v: float) -> str:
    sign = "+" if v >= 0 else "-"
    return f"{sign}${abs(v):.2f}"


def _pct(v: float) -> str:
    sign = "+" if v >= 0 else "-"
    return f"{sign}{abs(v):.2f}%"


def _px(v: float) -> str:
    return f"${v:,.2f}" if v >= 100 else f"${v:,.4f}"


def fmt_opened(*, symbol, tf, direction, entry, sl, tp1, tp2,
               qty, leverage, notional, margin) -> str:
    sl_pct = (entry - sl) / entry * 100 if direction == "long" else (sl - entry) / entry * 100
    return (
        f"🟢 OPENED  {symbol} {tf} {direction.upper()}\n"
        f"   entry {_px(entry)}  sl {_px(sl)} ({_pct(-sl_pct)})\n"
        f"   tp1 {_px(tp1)}  tp2 {_px(tp2)}\n"
        f"   qty {qty}  ({leverage}x lev, ${notional:.2f} notional, ${margin:.2f} margin)"
    )


def fmt_tp1_trailed(*, symbol: str, new_sl: float) -> str:
    return f"🎯 TP1 HIT  {symbol}  → SL trailed to {_px(new_sl)} (locked +1R)"


def fmt_tp2(*, symbol: str, pnl_usdt: float, pnl_pct: float) -> str:
    return f"✅ TP2 HIT  {symbol}  closed {_money(pnl_usdt)} ({_pct(pnl_pct)})"


def fmt_sl(*, symbol: str, pnl_usdt: float, pnl_pct: float) -> str:
    return f"🛑 SL HIT  {symbol}  closed {_money(pnl_usdt)} ({_pct(pnl_pct)})"


def fmt_breakeven(*, symbol: str, pnl_usdt: float) -> str:
    return f"🔁 BREAKEVEN  {symbol}  TP1 trailed → SL hit at TP1 closed {_money(pnl_usdt)}"


def fmt_error(*, symbol: str, reason: str) -> str:
    return f"⚠️ ERROR  {symbol} — {reason}"


def fmt_daily(*, date: str, trades: int, wins: int, pnl_usdt: float, pnl_pct: float) -> str:
    return (
        f"📊 DAILY ({date})\n"
        f"   trades {trades}  wins {wins}  pnl {_money(pnl_usdt)} ({_pct(pnl_pct)})"
    )


def fmt_help() -> str:
    return (
        "🤖 <b>FVG Live Trading</b>\n\n"
        "Gunakan tombol di bawah untuk manage akun trading kamu."
    )


def fmt_dashboard(summary: dict, stats: dict) -> str:
    """Rich home screen with live status, balance, and today's stats."""
    # ── status line ──────────────────────────────────────────────
    if not summary.get("registered"):
        return "🤖 <b>FVG Alpha Caller</b>\n\nKirim /start untuk daftar."

    if not summary.get("has_keys"):
        status_line = "⚠️ <b>No API Keys</b> — tekan 🔑 Set API Keys"
    elif not summary.get("enabled"):
        status_line = "⏸ <b>Paused</b>"
    else:
        status_line = "✅ <b>Active</b> — trading live"

    # ── balance ──────────────────────────────────────────────────
    bal = summary.get("balance") or {}
    if bal:
        bal_line = f"💰 Balance: <b>${float(bal.get('total', 0)):.2f}</b> USDT  (free ${float(bal.get('free', 0)):.2f})"
    else:
        bal_line = "💰 Balance: <i>set API keys first</i>"

    # ── settings ─────────────────────────────────────────────────
    risk    = float(summary.get("risk_pct", 1))
    lev     = int(summary.get("leverage", 10))
    maxc    = int(summary.get("max_concurrent", 3))
    dloss   = float(summary.get("daily_loss_cap_pct", 5))
    key_tail = summary.get("api_key_tail") or "—"
    settings_line = (
        f"⚙️ Risk <b>{risk:.2f}%</b>  |  Lev <b>{lev}x</b>  |  "
        f"Max <b>{maxc}</b> trades  |  Daily cap <b>{dloss:.1f}%</b>"
    )

    # ── today stats ──────────────────────────────────────────────
    today_trades = stats.get("today_trades", 0)
    today_wins   = stats.get("today_wins", 0)
    today_pnl    = float(stats.get("today_pnl_usdt", 0) or 0)
    today_wr     = (today_wins / today_trades * 100) if today_trades else 0
    pnl_sign     = "+" if today_pnl >= 0 else ""
    today_line = (
        f"📊 Today: <b>{today_trades}</b> trades  "
        f"WR <b>{today_wr:.0f}%</b>  "
        f"PnL <b>{pnl_sign}${today_pnl:.2f}</b>"
    )

    # ── all-time ─────────────────────────────────────────────────
    closed  = stats.get("closed_trades", 0)
    wins    = stats.get("wins", 0)
    wr_all  = float(stats.get("winrate", 0) or 0)
    pnl_all = float(stats.get("pnl_usdt", 0) or 0)
    sign_all = "+" if pnl_all >= 0 else ""
    alltime_line = (
        f"🏆 All-time: <b>{closed}</b> trades  "
        f"WR <b>{wr_all:.1f}%</b>  "
        f"PnL <b>{sign_all}${pnl_all:.2f}</b>"
    )

    return (
        f"🤖 <b>FVG Alpha Caller</b>\n\n"
        f"{status_line}\n"
        f"{bal_line}\n\n"
        f"{settings_line}\n"
        f"🔑 Key: <code>...{key_tail}</code>\n\n"
        f"{today_line}\n"
        f"{alltime_line}"
    )


def fmt_key_saved(tail: str) -> str:
    return f"✅ API key saved. Tail: <code>...{tail}</code>"


def fmt_balance(summary: dict) -> str:
    if not summary.get("registered"):
        return "Send /start first."
    if not summary.get("has_keys"):
        return "No Binance keys yet. Send /setkeys."
    bal = summary.get("balance") or {}
    return (
        "💰 <b>Binance Futures USDT</b>\n"
        f"Free: ${float(bal.get('free', 0)):.2f}\n"
        f"Used: ${float(bal.get('used', 0)):.2f}\n"
        f"Total: ${float(bal.get('total', 0)):.2f}\n"
        f"Key: ...{summary.get('api_key_tail') or '—'}"
    )


def fmt_settings(row) -> str:
    if not row:
        return "Send /start first."
    state = "enabled" if row["enabled"] else "paused"
    return (
        f"⚙️ <b>Settings</b> ({state})\n"
        f"Risk: {float(row['risk_pct']):.2f}%\n"
        f"Leverage: {int(row['leverage'])}x\n"
        f"Max trades: {int(row['max_concurrent'])}\n"
        f"Daily loss cap: {float(row['daily_loss_cap_pct']):.2f}%\n"
        f"API key: ...{row['api_key_tail'] or 'not set'}"
    )


def fmt_trade_list(trades: list[dict], *, closed: bool) -> str:
    if not trades:
        return "No closed trades yet." if closed else "No active trades."
    title = "📕 <b>Closed trades</b>" if closed else "📗 <b>Active trades</b>"
    lines = [title]
    for t in trades:
        pnl = "" if t.get("pnl_usdt") is None else f" pnl {_money(float(t['pnl_usdt']))}"
        lines.append(
            f"{t['symbol']} {t['tf']} {str(t['direction']).upper()} {t['status']}\n"
            f"entry {_px(float(t['entry']))} SL {_px(float(t['sl_current']))} "
            f"TP1 {_px(float(t['tp1']))} TP2 {_px(float(t['tp2']))}{pnl}"
        )
    return "\n\n".join(lines)


def fmt_stats(s: dict) -> str:
    if not s.get("registered"):
        return "Send /start first."
    return (
        "📊 <b>Stats</b>\n"
        f"Today: {s['today_trades']} trades, {s['today_wins']} wins, "
        f"{_money(float(s['today_pnl_usdt']))} ({_pct(float(s['today_pnl_pct']))})\n"
        f"All-time: {s['closed_trades']} closed, {s['wins']} wins, "
        f"WR {float(s['winrate']):.1f}%, PnL {_money(float(s['pnl_usdt']))}"
    )


def onboarding_intro(dashboard_url: str = "", proxy_ip: str = "") -> str:
    return fmt_help()
