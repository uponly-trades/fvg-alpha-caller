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


_ERROR_STAGE_INFO = {
    "leverage": (
        "Gagal set leverage di Binance",
        (
            "Cek di Binance:\n"
            "1. <b>API Management</b> → pastikan key punya izin <b>Enable Futures</b>.\n"
            "2. <b>Futures</b> → mode <b>One-way</b> (bukan Hedge).\n"
            "3. <b>Asset Mode</b> → <b>Single-Asset</b> (bukan Multi-Assets).\n"
            "4. Pastikan symbol-nya tradeable (USDT-M perp aktif)."
        ),
    ),
    "entry": (
        "Gagal place entry order (MARKET)",
        (
            "Penyebab umum:\n"
            "• Saldo USDT kurang untuk margin.\n"
            "• Symbol tidak tradeable / pair delisted.\n"
            "• Min notional belum tercapai (<b>Risk %</b> × balance terlalu kecil).\n"
            "• API key tidak punya izin <b>Trade</b>."
        ),
    ),
    "sl": (
        "Gagal pasang Stop Loss (algo order)",
        (
            "Cek:\n"
            "• API key butuh izin <b>Enable Futures</b> + <b>Trade</b>.\n"
            "• Harga SL terlalu dekat ke mark price (auto-trigger).\n"
            "Posisi sudah di-emergency-close untuk safety."
        ),
    ),
    "tp": (
        "Gagal pasang Take Profit (algo order)",
        "Posisi tetap aktif dengan SL terpasang. Bisa close manual via Binance kalau perlu.",
    ),
}


def fmt_error(*, symbol: str, reason: str) -> str:
    info = _ERROR_STAGE_INFO.get((reason or "").lower())
    if info:
        title, howto = info
        sym = f" <b>{symbol}</b>" if symbol else ""
        return f"⚠️ {title}{sym}\n\n{howto}"
    sym_part = f" {symbol}" if symbol else ""
    return f"⚠️ ERROR{sym_part} — {reason or 'unknown'}"


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


def _estimate_trade_size(balance_total: float, risk_pct: float, leverage: int, sl_distance_pct: float = 1.0) -> dict:
    """Return per-trade size estimate at given SL distance.

    Formula matches trade_executor.sizing.compute_size:
      risk_usdt   = balance * risk_pct/100
      notional    = risk_usdt / (sl_distance_pct/100)
      margin      = notional / leverage
    """
    if balance_total <= 0 or risk_pct <= 0 or sl_distance_pct <= 0:
        return {"risk": 0.0, "notional": 0.0, "margin": 0.0}
    risk_usdt = balance_total * risk_pct / 100
    notional = risk_usdt / (sl_distance_pct / 100)
    margin = notional / max(1, int(leverage))
    return {"risk": risk_usdt, "notional": notional, "margin": margin}


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

    # ── per-trade size estimate ──────────────────────────────────
    bal_total = float(bal.get("total", 0) or 0)
    est_lines = ""
    if bal_total > 0:
        # Reference SL distance: 1% (typical FVG entry). Show 1% and 2% so
        # user understands range — closer SL = bigger position.
        est_1 = _estimate_trade_size(bal_total, risk, lev, sl_distance_pct=1.0)
        est_2 = _estimate_trade_size(bal_total, risk, lev, sl_distance_pct=2.0)
        max_concurrent_margin = est_1["margin"] * maxc
        est_lines = (
            f"\n📐 <b>Per-Trade Size</b> (estimate)\n"
            f"  Risk/trade: <b>${est_1['risk']:.2f}</b>\n"
            f"  SL 1.0%: notional <b>${est_1['notional']:.2f}</b>  margin <b>${est_1['margin']:.2f}</b>\n"
            f"  SL 2.0%: notional <b>${est_2['notional']:.2f}</b>  margin <b>${est_2['margin']:.2f}</b>\n"
            f"  Max exposure (×{maxc}): margin <b>${max_concurrent_margin:.2f}</b>"
        )

    return (
        f"🤖 <b>FVG Alpha Caller</b>\n\n"
        f"{status_line}\n"
        f"{bal_line}\n\n"
        f"{settings_line}\n"
        f"🔑 Key: <code>...{key_tail}</code>\n"
        f"{est_lines}\n\n"
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

    # ── config (so user always sees what bot is doing) ────────────
    enabled = s.get("enabled", True)
    risk    = float(s.get("risk_pct", 0) or 0)
    lev     = int(s.get("leverage", 0) or 0)
    maxc    = int(s.get("max_concurrent", 0) or 0)
    dloss   = float(s.get("daily_loss_cap_pct", 0) or 0)
    state   = "✅ Active" if enabled else "⏸ Paused"
    cfg_block = (
        "⚙️ <b>Config</b>\n"
        f"  Status: <b>{state}</b>\n"
        f"  Risk: <b>{risk:.2f}%</b>  |  Lev: <b>{lev}x</b>  |  Max: <b>{maxc}</b>  |  Cap: <b>{dloss:.1f}%</b>\n\n"
    )

    # ── today ─────────────────────────────────────────────────────
    tt   = s["today_trades"]
    tw   = s["today_wins"]
    tl   = tt - tw
    tpnl = float(s["today_pnl_usdt"])
    twr  = (tw / tt * 100) if tt else 0.0

    # ── all-time ──────────────────────────────────────────────────
    closed = s["closed_trades"]
    wins   = s["wins"]
    losses = closed - wins
    wr     = float(s["winrate"])
    pnl    = float(s["pnl_usdt"])
    tp2    = s.get("tp2_hits", 0)
    sl_h   = s.get("sl_hits", 0)
    be_h   = s.get("be_hits", 0)
    avg_w  = float(s.get("avg_win", 0))
    avg_l  = float(s.get("avg_loss", 0))
    best   = float(s.get("best_trade", 0))
    worst  = float(s.get("worst_trade", 0))
    pf     = float(s.get("profit_factor", 0))

    # ── expectancy per trade ──────────────────────────────────────
    exp = (wr / 100 * avg_w + (1 - wr / 100) * avg_l) if closed else 0.0

    return (
        "📊 <b>Trading Stats</b>\n\n"
        + cfg_block +

        "📅 <b>Today</b>\n"
        f"  Trades: <b>{tt}</b>  (W {tw} / L {tl})"
        + (f"  WR <b>{twr:.0f}%</b>" if tt else "") + "\n"
        f"  PnL: <b>{_money(tpnl)}</b>\n\n"

        "🏆 <b>All-Time</b>\n"
        f"  Trades: <b>{closed}</b>  (W {wins} / L {losses})\n"
        f"  Win Rate: <b>{wr:.1f}%</b>  |  Profit Factor: <b>{pf:.2f}</b>\n"
        f"  Net PnL: <b>{_money(pnl)}</b>\n\n"

        "🎯 <b>Exits</b>\n"
        f"  TP2 ✅ {tp2}  |  SL 🛑 {sl_h}  |  Breakeven 🔁 {be_h}\n\n"

        "📐 <b>Per-Trade</b>\n"
        f"  Avg Win: <b>{_money(avg_w)}</b>  |  Avg Loss: <b>{_money(avg_l)}</b>\n"
        f"  Best: <b>{_money(best)}</b>  |  Worst: <b>{_money(worst)}</b>\n"
        f"  Expectancy: <b>{_money(exp)}</b> / trade"
    )


def onboarding_intro(dashboard_url: str = "", proxy_ip: str = "") -> str:
    return fmt_help()
