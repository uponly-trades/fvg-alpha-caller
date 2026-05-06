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


def onboarding_intro(dashboard_url: str, proxy_ip: str) -> str:
    return (
        "Welcome! To trade live:\n"
        f"1. Log in at {dashboard_url}/login (Telegram auth)\n"
        "2. Add Binance API keys at /api-keys\n"
        f"3. Whitelist this IP on your key restriction: {proxy_ip}\n"
        "4. Permissions: Futures Trading + Read. Never enable Withdraw."
    )
