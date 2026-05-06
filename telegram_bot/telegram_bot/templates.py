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
        "🤖 <b>FVG Live Binance</b>\n"
        "/setkeys — setup Binance API key\n"
        "/balance — saldo USDT Binance Futures\n"
        "/trades — trade aktif\n"
        "/closed — trade closed terakhir\n"
        "/stats — PnL + winrate\n"
        "/settings — risk/leverage/max trade\n"
        "/setrisk 2 — risk % per trade\n"
        "/setlev 5 — leverage 5-20x\n"
        "/setmax 3 — max concurrent trades\n"
        "/setloss 6 — daily loss cap %\n"
        "/pause — stop new trades\n"
        "/resume — enable live trades\n"
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
