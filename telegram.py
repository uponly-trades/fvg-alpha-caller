import asyncio
import io
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional

import requests

from config import BOT_TOKEN, CHAT_ID
import alert_settings as _asettings

logger = logging.getLogger(__name__)
TELEGRAM_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
TELEGRAM_PHOTO_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"


def _tv_link(symbol: str, tf: str) -> str:
    """Build TradingView chart URL."""
    interval_map = {"15m": "15", "30m": "30", "1h": "60", "2h": "120", "4h": "240"}
    iv = interval_map.get(tf, "60")
    tv_symbol = f"{symbol}.P"
    return f"https://www.tradingview.com/chart/?symbol=BINANCE:{tv_symbol}&interval={iv}"


def _fvg_direction_text(zone) -> str:
    return "BULLISH" if int(zone.direction) == 1 else "BEARISH"


def _rsi_emoji(rsi: float) -> str:
    if rsi >= 70:
        return "🔥"
    if rsi >= 55:
        return "📈"
    if rsi <= 30:
        return "🧊"
    if rsi <= 45:
        return "📉"
    return "↔️"


def _confidence_label(score: int) -> str:
    if score >= 85:
        return "💎 Very High"
    if score >= 70:
        return "✅ High"
    if score >= 55:
        return "⚠️ Medium"
    return "❌ Low"


def _oi_vol_lines(zone, timeframe_bars: dict) -> List[str]:
    """Pine-parity volume score (vol / SMA20) + OI delta on 15m bars."""
    try:
        from feature_extractor import extract_tf_features
    except ImportError:
        return []
    bars = timeframe_bars.get("15m", [])
    if len(bars) < 21:
        return []
    try:
        f = extract_tf_features(bars, "15m", symbol=zone.symbol, with_ls_ratio=True)
    except Exception:
        f = {}
    out = []
    # Pine `volScore`: ratio current vol vs 20-bar SMA. Same metric Zeiierman shows.
    try:
        vols = [float(b.volume) for b in bars[-21:]]
        sma20 = sum(vols[:-1]) / 20 if len(vols) >= 21 else 0.0
        if sma20 > 0:
            ratio = vols[-1] / sma20
            emoji = "🔥" if ratio >= 2.0 else "📈" if ratio >= 1.3 else "↔️"
            out.append(f"Vol×  : {emoji} {ratio:.2f}× SMA20  (15m)")
    except Exception:
        pass
    oi = (f or {}).get("oi_change_pct")
    if oi is not None:
        emoji = "📈" if oi > 0 else "📉" if oi < 0 else "↔️"
        out.append(f"OI Δ  : {emoji} {oi:+.2f}%  (15m)")
    return out


def _stoch_state_lines(timeframe_bars: dict) -> List[str]:
    """StochRSI state per TF — oversold/overbought/ranging with K value."""
    try:
        from indicator_context import stochrsi_series
    except ImportError:
        return []

    tf_order = ("15m", "30m", "1h", "2h", "4h")
    lines = ["<b>StochRSI per TF</b>"]
    for tf in tf_order:
        bars = timeframe_bars.get(tf, [])
        if len(bars) < 20:
            lines.append(f"  {tf:<4} —")
            continue
        closes = [float(b.close) for b in bars]
        k_vals, d_vals = stochrsi_series(closes)
        pairs = [(k, d) for k, d in zip(k_vals, d_vals) if k is not None and d is not None]
        if len(pairs) < 2:
            lines.append(f"  {tf:<4} —")
            continue
        k, d = pairs[-1]
        pk, pd = pairs[-2]

        # State
        if k <= 20 and d <= 20:
            state = "🟢 Oversold"
        elif k >= 80 and d >= 80:
            state = "🔴 Overbought"
        elif pk <= pd and k > d and min(pk, pd, k, d) <= 30:
            state = "🟢 Bullish cross"
        elif pk >= pd and k < d and max(pk, pd, k, d) >= 70:
            state = "🔴 Bearish cross"
        elif k > 50 and d > 50:
            state = "↗️ Bullish"
        elif k < 50 and d < 50:
            state = "↘️ Bearish"
        else:
            state = "↔️ Ranging"

        lines.append(f"  {tf:<4} {state}  K:{k:.0f} D:{d:.0f}")
    return lines


def _trade_title(zone, trade_setup, prefix: str = None) -> str:
    status = prefix if prefix else trade_setup.status
    return f"{status} | {_fvg_direction_text(zone)} FVG | {zone.symbol} | {zone.tf}"


def _format_trade_alert(zone, current_price: float, trade_setup, prefix: str = None, timeframe_bars: dict = None) -> str:
    tv_url = _tv_link(zone.symbol, zone.tf)
    lines = [f"<b>{_trade_title(zone, trade_setup, prefix)}</b>", ""]
    rsi_val = getattr(zone, "rsi", None)
    rsi_str = f"{_rsi_emoji(rsi_val)} RSI: {rsi_val:.1f}" if rsi_val is not None else ""

    if trade_setup is not None and trade_setup.trade is not None:
        trade = trade_setup.trade
        src = getattr(trade_setup, "source", "combo")
        src_label = "🤖 Kronos" if src == "kronos" else "📊 Combo"
        ep = float(trade.entry)
        def _pct_t(price: float) -> str:
            return f" ({(price - ep) / ep * 100:+.2f}%)" if ep > 0 else ""
        lines.extend([
            f"Entry : <b>{_fmt_price(trade.entry)}</b>",
            f"SL    : {_fmt_price(trade.sl)}{_pct_t(float(trade.sl))}",
            f"TP1   : {_fmt_price(trade.tp1)}{_pct_t(float(trade.tp1))}",
            f"TP2   : {_fmt_price(trade.tp2)}{_pct_t(float(trade.tp2))}",
            f"RR    : 1:2  |  {src_label}",
        ])
    else:
        # Skip — show price + zone + why it was skipped
        dir_emoji = "🟢" if int(zone.direction) == 1 else "🔴"
        lines.extend([
            f"Price : {_fmt_price(current_price)}",
            f"Zone  : {dir_emoji} {_fmt_price(zone.bottom)} — {_fmt_price(zone.top)}",
        ])
        if trade_setup is not None:
            status = trade_setup.status  # e.g. "SKIP: MIXED COMBO"
            skip_label = status.replace("SKIP: ", "").title()
            lines.append(f"Skip  : {skip_label}")
            # Show combo states if available
            combo = getattr(trade_setup, "combo_states", {})
            if combo:
                desired = "long" if int(zone.direction) == 1 else "short"
                state_icons = {"long": "🟢", "short": "🔴", "neutral": "⚪"}
                combo_parts = [f"{tf}:{state_icons.get(s,'❓')}" for tf, s in combo.items()]
                lines.append(f"Combo : {' '.join(combo_parts)}")

    lines.extend([
        f"FVG   : {_confidence_label(zone.main_strength)} ({zone.main_strength}%)",
    ])
    if rsi_str:
        lines.append(rsi_str)
    if trade_setup is not None and trade_setup.trade is not None:
        lines.append(f"Signal: {trade_setup.reason}")

    # OI / vol_change context (15m) — read from features cached on zone if present
    ctx_lines = _oi_vol_lines(zone, timeframe_bars or {})
    if ctx_lines:
        lines.extend(ctx_lines)

    # v1 vs v2 shadow filter compare
    if trade_setup is not None:
        v1_icon = "✅" if trade_setup.valid else "⚠️"
        v2 = getattr(trade_setup, "v2_decision", None)
        v2_icon = "✅" if (v2 and v2.get("valid")) else "⚠️"
        v2_reason = v2.get("reason", "—") if v2 else "—"
        lines.append(f"Filter: v1 {v1_icon} | v2 {v2_icon} ({v2_reason})")

    stoch_lines = _stoch_state_lines(timeframe_bars or {})
    if stoch_lines:
        lines.append("")
        lines.extend(stoch_lines)

    lines.extend(["", f"<a href='{tv_url}'>Open TradingView</a>"])
    return "\n".join(lines)


def send_new_fvg_alert(zone, chart_png: Optional[bytes] = None, trade_setup=None, timeframe_bars: dict = None) -> bool:
    msg = _format_trade_alert(zone, getattr(zone, "price", 0.0), trade_setup,
                              prefix="NEW FVG", timeframe_bars=timeframe_bars or {})
    if chart_png:
        return _send_photo(msg, chart_png)
    return _send(msg)


def send_snipe_alert(
    snipe_type: str,          # "long_limit" | "retest_short"
    symbol: str,
    tf: str,
    trade_setup,
    chart_png: Optional[bytes] = None,
    zone=None,
    timeframe_bars: dict = None,
) -> bool:
    """Send a snipe entry alert (long limit or retest short)."""
    if trade_setup is None or trade_setup.trade is None:
        return False
    trade = trade_setup.trade
    tv_url = _tv_link(symbol, tf)

    if snipe_type == "long_limit":
        icon = "🎯"
        title = f"SNIPE LONG (limit) | {symbol} | {tf}"
        dir_line = f"Dir   : 🟢 LONG (limit @ zone bottom)"
    elif snipe_type == "htf_fade":
        icon = "🌊"
        title = f"HTF FADE SHORT | {symbol} | {tf}"
        dir_line = f"Dir   : 🔴 SHORT (4h OB fade)"
    else:
        icon = "🩸"
        title = f"SNIPE SHORT (retest) | {symbol} | {tf}"
        dir_line = f"Dir   : 🔴 SHORT (retest rejection)"

    entry_p = float(trade.entry)
    def _pct(price: float) -> str:
        if entry_p <= 0:
            return ""
        return f" ({(price - entry_p) / entry_p * 100:+.2f}%)"

    lines = [
        f"{icon} <b>{title}</b>",
        "",
        dir_line,
        f"Entry : <b>{_fmt_price(trade.entry)}</b>",
        f"SL    : {_fmt_price(trade.sl)}{_pct(float(trade.sl))}",
        f"TP1   : {_fmt_price(trade.tp1)}{_pct(float(trade.tp1))}",
        f"TP2   : {_fmt_price(trade.tp2)}{_pct(float(trade.tp2))}",
        f"RR    : 1:2",
        f"Signal: {trade_setup.reason}",
    ]

    if zone is not None:
        lines.append(f"Zone  : {_fmt_price(zone.bottom)} — {_fmt_price(zone.top)}")

    ctx_lines = _oi_vol_lines(zone, timeframe_bars or {}) if zone is not None else []
    if ctx_lines:
        lines.extend(ctx_lines)

    stoch_lines = _stoch_state_lines(timeframe_bars or {})
    if stoch_lines:
        lines.append("")
        lines.extend(stoch_lines)

    lines.extend(["", f"<a href='{tv_url}'>Open TradingView</a>"])
    msg = "\n".join(lines)

    if chart_png:
        return _send_photo(msg, chart_png)
    return _send(msg)


def send_mitigated_alert(zone) -> bool:
    emoji = "🟢" if zone.direction == 1 else "🔴"
    dir_text = "Bullish" if zone.direction == 1 else "Bearish"
    msg = (
        f"⚪ <b>FVG FULLY MITIGATED</b>\n\n"
        f"{emoji} {dir_text}\n"
        f"Symbol : <code>{zone.symbol}</code>\n"
        f"TF     : <code>{zone.tf}</code>\n"
        f"Zone   : {zone.bottom} — {zone.top}"
    )
    return _send(msg)


def send_approach_alert(zone, current_price: float, chart_png: Optional[bytes] = None, trade_setup=None, timeframe_bars: dict = None) -> bool:
    msg = _format_trade_alert(zone, current_price, trade_setup,
                              timeframe_bars=timeframe_bars or {})
    if chart_png:
        return _send_photo(msg, chart_png)
    return _send(msg)


def send_touch_alert(zone, current_price: float, chart_png: Optional[bytes] = None, trade_setup=None, timeframe_bars: dict = None) -> bool:
    msg = _format_trade_alert(zone, current_price, trade_setup,
                              timeframe_bars=timeframe_bars or {})
    if chart_png:
        return _send_photo(msg, chart_png)
    return _send(msg)


def _fmt_price(v) -> str:
    try:
        f = float(v)
        if f >= 1000:
            return f"{f:,.2f}"
        if f >= 1:
            return f"{f:.4f}"
        return f"{f:.6f}"
    except Exception:
        return str(v)


_TZ_WIB = timezone(timedelta(hours=7))

def _fmt_ts(ts_ms) -> str:
    try:
        dt = datetime.fromtimestamp(int(ts_ms) / 1000, tz=_TZ_WIB)
        return dt.strftime("%d %b %H:%M WIB")
    except Exception:
        return "—"


def _calc_pnl_pct(r: dict) -> Optional[float]:
    """Realized PnL% for closed trades. None for open/tp1_hit."""
    status = r.get("status", "open")
    direction = r.get("direction", "")
    try:
        entry = float(r["entry"])
        if entry <= 0:
            return None
        if status == "win":
            exit_price = float(r["tp2"])
        elif status == "loss":
            exit_price = float(r["sl"])
        else:
            return None
        if direction == "long":
            return (exit_price - entry) / entry * 100
        else:
            return (entry - exit_price) / entry * 100
    except Exception:
        return None


# ─── /settings command poller ────────────────────────────────────────────────

_last_update_id: int = 0

_TRIGGER_LABELS = {
    "new_fvg":    "New FVG alert",
    "approach":   "Approaching zone",
    "touch":      "Touch zone",
    "snipe_long":  "Snipe LONG (limit)",
    "snipe_short": "Snipe SHORT (retest)",
}

_HELP = (
    "⚙️ <b>Alert Trigger Settings</b>\n\n"
    "Commands:\n"
    "/settings — show current settings\n"
    "/settings &lt;trigger&gt; on|off — toggle\n\n"
    "Triggers: new_fvg  approach  touch  snipe_long  snipe_short\n\n"
    "Example: /settings snipe_short off"
)


def _settings_status_msg() -> str:
    s = _asettings.get_settings()
    lines = ["⚙️ <b>Alert Trigger Settings</b>\n"]
    for key in _asettings.TRIGGER_KEYS:
        icon = "✅" if s.get(key, True) else "❌"
        label = _TRIGGER_LABELS.get(key, key)
        lines.append(f"{icon} <code>{key}</code>  —  {label}")
    lines.append("\nToggle: /settings &lt;trigger&gt; on|off")
    return "\n".join(lines)


def _handle_settings_command(text: str) -> str:
    """Parse /settings [trigger] [on|off] and return reply text."""
    parts = text.strip().split()
    # /settings with no args → show status
    if len(parts) == 1:
        return _settings_status_msg()
    if len(parts) == 2:
        # /settings help
        return _HELP
    if len(parts) == 3:
        _, trigger, value = parts
        trigger = trigger.lower().strip()
        value = value.lower().strip()
        if value not in ("on", "off"):
            return f"❌ Value must be on or off, got: {value}"
        enabled = value == "on"
        ok = _asettings.set_trigger(trigger, enabled)
        if not ok:
            valid = ", ".join(_asettings.TRIGGER_KEYS)
            return f"❌ Unknown trigger: {trigger}\nValid: {valid}"
        icon = "✅" if enabled else "❌"
        label = _TRIGGER_LABELS.get(trigger, trigger)
        return f"{icon} <b>{trigger}</b> ({label}) turned <b>{'ON' if enabled else 'OFF'}</b>\n\n{_settings_status_msg()}"
    return _HELP


async def _poll_commands_loop() -> None:
    """Long-poll Telegram for bot commands every 3s."""
    global _last_update_id
    get_url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    while True:
        try:
            resp = requests.get(
                get_url,
                params={"offset": _last_update_id + 1, "timeout": 2, "allowed_updates": ["message"]},
                timeout=10,
            )
            if resp.ok:
                updates = resp.json().get("result", [])
                for upd in updates:
                    _last_update_id = upd["update_id"]
                    msg = upd.get("message", {})
                    text = msg.get("text", "")
                    chat_id = msg.get("chat", {}).get("id")
                    if text.startswith("/settings") and str(chat_id) == str(CHAT_ID):
                        reply = _handle_settings_command(text)
                        requests.post(
                            TELEGRAM_URL,
                            json={"chat_id": CHAT_ID, "text": reply, "parse_mode": "HTML"},
                            timeout=10,
                        )
        except Exception as e:
            logger.debug("command poll error: %s", e)
        await asyncio.sleep(3)


def start_command_poller() -> asyncio.Task:
    """Start background command polling task. Call once from main event loop."""
    return asyncio.ensure_future(_poll_commands_loop())


# ─── Daily recap ──────────────────────────────────────────────────────────────

def send_trade_recap(session_name: str, recap: dict) -> bool:
    date_str = recap.get("date", "—")
    open_c = recap["open"]
    tp1_c = recap["tp1"]
    win_c = recap["win"]
    loss_c = recap["loss"]
    wr = recap["closed_winrate"]
    closed = win_c + loss_c

    wr_emoji = "🔥" if wr >= 70 else "✅" if wr >= 50 else "⚠️" if wr >= 30 else "❌"
    total_r = win_c * 2.0 + tp1_c * 1.0 - loss_c * 1.0
    r_sign = "+" if total_r >= 0 else ""
    r_emoji = "🟢" if total_r > 0 else "🔴" if total_r < 0 else "⚪"

    lines = [
        f"📊 <b>Trade Recap — {session_name}  |  {date_str}</b>",
        "",
        f"⏳ {open_c} open   🎯 {tp1_c} tp1   ✅ {win_c} win   ❌ {loss_c} loss",
        f"WR {wr_emoji} <b>{wr}%</b>  ({closed} closed)   {r_emoji} <b>{r_sign}{total_r:.1f}R</b>",
    ]

    # Per-trigger breakdown
    by_trigger = recap.get("by_trigger", {})
    if by_trigger:
        lines.extend(["", "── Per Trigger ──────────────────"])
        trigger_labels = {
            "new_fvg": "NewFVG", "approach": "Approach", "touch": "Touch",
            "snipe": "Snipe", "snipe_long": "SnipeLong", "snipe_short": "SnipeShort",
        }
        for mode, stats in sorted(by_trigger.items()):
            w = stats.get("win", 0)
            l = stats.get("loss", 0)
            t = stats.get("tp1", 0)
            n = w + l
            wr_t = round(w / n * 100) if n else 0
            r_t = w * 2.0 + t * 1.0 - l * 1.0
            r_t_str = f"{'+' if r_t >= 0 else ''}{r_t:.1f}R"
            icon = "🔥" if wr_t >= 70 else "✅" if wr_t >= 50 else "⚠️" if n > 0 else "─"
            label = trigger_labels.get(mode, mode)
            lines.append(f"{icon} <code>{label:<12}</code> {w}W {l}L {wr_t}%  {r_t_str}")

    # Per-symbol breakdown
    by_symbol = recap.get("by_symbol", {})
    if by_symbol:
        lines.extend(["", "── Per Symbol ───────────────────"])
        for sym, stats in sorted(by_symbol.items(), key=lambda x: -(x[1].get("win", 0) - x[1].get("loss", 0))):
            w = stats.get("win", 0)
            l = stats.get("loss", 0)
            t = stats.get("tp1", 0)
            n = w + l
            wr_s = round(w / n * 100) if n else 0
            r_s = w * 2.0 + t * 1.0 - l * 1.0
            r_s_str = f"{'+' if r_s >= 0 else ''}{r_s:.1f}R"
            icon = "🔥" if wr_s >= 70 else "✅" if wr_s >= 50 else "⚠️" if n > 0 else "─"
            short_sym = sym.replace("USDT", "")
            lines.append(f"{icon} <b>{short_sym:<8}</b> {w}W {l}L {wr_s}%  {r_s_str}")

    # Recent trades (last 5)
    recent = recap.get("recent", [])
    if recent:
        lines.extend(["", "── Recent ───────────────────────"])
        status_icon = {"win": "✅", "loss": "❌", "tp1_hit": "🎯", "open": "⏳"}
        dir_icon = {"long": "🟢", "short": "🔴"}
        for r in recent:
            d_icon = dir_icon.get(r.get("direction", ""), "❓")
            s_icon = status_icon.get(str(r.get("status", "open")), "❓")
            sym = r["symbol"].replace("USDT", "")
            tf = r["tf"]
            entry = _fmt_price(r["entry"])
            tp2 = _fmt_price(r["tp2"])
            sl = _fmt_price(r["sl"])
            pnl = _calc_pnl_pct(r)
            pnl_str = f"  {'+' if pnl >= 0 else ''}{pnl:.2f}%" if pnl is not None else ""
            mode = r.get("mode", "")
            mode_tag = f" [{mode}]" if mode and mode != "intraday" else ""
            lines.append(
                f"{s_icon} {d_icon} <b>{sym}</b> {tf}{mode_tag}  {entry} → {tp2}  SL {sl}{pnl_str}"
            )

    return _send("\n".join(lines))


def _send(text: str) -> bool:
    try:
        resp = requests.post(
            TELEGRAM_URL,
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=15,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error("Telegram text send failed: %s", e)
        return False


def _send_photo(caption: str, png_bytes: bytes) -> bool:
    try:
        files = {"photo": ("chart.png", io.BytesIO(png_bytes), "image/png")}
        data = {"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML"}
        resp = requests.post(TELEGRAM_PHOTO_URL, data=data, files=files, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error("Telegram photo send failed: %s", e)
        # Fallback to text-only
        return _send(caption)


# =====================================================
# v2 Strategy Alerts (Multi-TF FVG Touch Confluence)
# =====================================================

def _v2_confluence_stars(score: int) -> str:
    n = max(0, min(score, 4))
    return "⭐" * n if n > 0 else "—"


def _v2_format_indicators(timeframe_bars: dict) -> List[str]:
    """Display-only StochRSI per TF + vol delta. Reuses existing helpers."""
    out: List[str] = []
    try:
        out.extend(_stoch_state_lines(timeframe_bars))
    except Exception:
        pass
    return out


def _v2_format_oi_vol(zone_or_symbol, timeframe_bars: dict) -> List[str]:
    """Wrap _oi_vol_lines; tolerant to passing either FVGZone or symbol str."""
    try:
        if hasattr(zone_or_symbol, "symbol"):
            return _oi_vol_lines(zone_or_symbol, timeframe_bars)
        class _S:
            pass
        s = _S(); s.symbol = zone_or_symbol
        return _oi_vol_lines(s, timeframe_bars)
    except Exception:
        return []


def _v2_taker_buy_sell_lines(timeframe_bars: dict, tf: str = "15m") -> List[str]:
    """Buy/sell vol % from taker buy base volume on latest closed `tf` bar."""
    try:
        bars = timeframe_bars.get(tf, [])
        if not bars:
            return []
        last = bars[-1]
        vol = float(getattr(last, "volume", 0) or 0)
        buy = float(getattr(last, "taker_buy_volume", 0) or 0)
        if vol <= 0:
            return []
        buy_pct = max(0.0, min(100.0, buy / vol * 100))
        sell_pct = 100.0 - buy_pct
        if vol >= 1e6:
            vol_str = f"{vol/1e6:.2f}M"
        elif vol >= 1e3:
            vol_str = f"{vol/1e3:.1f}k"
        else:
            vol_str = f"{vol:.0f}"
        return [f"Vol {tf}: {vol_str} (buy {buy_pct:.0f}% / sell {sell_pct:.0f}%)"]
    except Exception:
        return []


def send_v2_alert(signal, timeframe_bars: dict, chart_png: Optional[bytes] = None) -> None:
    """Send a v2 entry alert. `signal` is a strategy_v2.V2Signal instance."""
    direction_emoji = "🟢 LONG" if signal.direction == 1 else "🔴 SHORT"
    status_trade = "NEW LONG" if signal.direction == 1 else "NEW SHORT"
    title = f"({status_trade} | {signal.symbol} | {signal.trigger_tf})"

    sl_pct = (signal.sl - signal.entry) / signal.entry * 100 if signal.entry else 0.0
    tp = getattr(signal, "tp", None)
    tp_pct = (tp - signal.entry) / signal.entry * 100 if (tp and signal.entry) else 0.0

    # Direction-aware HTF marker. ✓ shown ONLY for same-direction zone touch.
    # 🟢 long match, 🔴 short match, · no active+touched zone in that direction.
    dir_emoji = "🟢" if signal.direction == 1 else "🔴"
    htf_line_parts = []
    for tf in ("30m", "1h", "2h", "4h"):
        mark = dir_emoji if signal.htf_touches.get(tf) else "·"
        htf_line_parts.append(f"{tf}{mark}")
    htf_line = " ".join(htf_line_parts)
    htf_max = 1 + 1 + 2 + 3  # weighted max

    tp_line = (
        f"🎯 TP:    <code>{tp:g}</code> ({tp_pct:+.2f}%) RR 1:2"
        if tp else
        f"🎯 TP:    trail (RR 1:∞)"
    )

    lines = [
        f"<b>{title}</b>",
        "",
        f"{direction_emoji}",
        f"📍 Entry: <code>{signal.entry:g}</code>",
        f"🛑 SL:    <code>{signal.sl:g}</code> ({sl_pct:+.2f}%)",
        tp_line,
        "",
        f"Confluence: {_v2_confluence_stars(signal.confluence_score)}  ({signal.confluence_score}/{htf_max})",
        f"Trigger: {signal.trigger_tf} {'bullish' if signal.direction == 1 else 'bearish'} touch",
        f"HTF:     {htf_line}",
        "",
        f"<a href='{_tv_link(signal.symbol, signal.trigger_tf)}'>📊 TradingView</a>",
    ]

    indicator_lines = _v2_format_indicators(timeframe_bars)
    if indicator_lines:
        lines.append("")
        lines.extend(indicator_lines)

    oi_lines = _v2_format_oi_vol(signal.symbol, timeframe_bars)
    if oi_lines:
        lines.append("")
        lines.extend(oi_lines)

    taker_lines = _v2_taker_buy_sell_lines(timeframe_bars, signal.trigger_tf)
    if taker_lines:
        lines.extend(taker_lines)

    text = "\n".join(lines)
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}

    if chart_png:
        files = {"photo": ("chart.png", chart_png, "image/png")}
        data = {"chat_id": CHAT_ID, "caption": text, "parse_mode": "HTML"}
        try:
            requests.post(TELEGRAM_PHOTO_URL, data=data, files=files, timeout=15)
        except Exception as e:
            logger.error("send_v2_alert (photo) failed: %s", e)
    else:
        try:
            requests.post(TELEGRAM_URL, json=payload, timeout=15)
        except Exception as e:
            logger.error("send_v2_alert failed: %s", e)


def send_v2_stopped(symbol: str, trigger_tf: str, direction: int, entry: float, sl_at_stop: float, last_price: float) -> None:
    pnl_pct = (sl_at_stop - entry) / entry * 100 if entry else 0.0
    if direction == -1:
        pnl_pct = -pnl_pct
    title = f"(STOPPED | {symbol} | {trigger_tf})"
    text = (
        f"<b>{title}</b>\n"
        f"Entry: <code>{entry:g}</code>\n"
        f"Stop:  <code>{sl_at_stop:g}</code>\n"
        f"Last:  <code>{last_price:g}</code>\n"
        f"PnL:   {pnl_pct:+.2f}%"
    )
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    try:
        requests.post(TELEGRAM_URL, json=payload, timeout=15)
    except Exception as e:
        logger.error("send_v2_stopped failed: %s", e)
