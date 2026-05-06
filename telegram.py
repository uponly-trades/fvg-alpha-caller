import io
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional

import requests

from config import BOT_TOKEN, CHAT_ID

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
    """OI/vol_change_15m context. Compute live from 15m bars."""
    try:
        from feature_extractor import extract_tf_features
    except ImportError:
        return []
    bars = timeframe_bars.get("15m", [])
    if len(bars) < 30:
        return []
    try:
        f = extract_tf_features(bars, "15m", symbol=zone.symbol, with_ls_ratio=True)
    except Exception:
        return []
    vc = f.get("vol_change_pct")
    oi = f.get("oi_change_pct")
    out = []
    if vc is not None:
        emoji = "🔥" if vc >= 100 else "📈" if vc >= 50 else "↔️"
        out.append(f"Vol Δ : {emoji} {vc:+.1f}%  (15m)")
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
        lines.extend([
            f"Entry : <b>{_fmt_price(trade.entry)}</b>",
            f"SL    : {_fmt_price(trade.sl)}",
            f"TP1   : {_fmt_price(trade.tp1)}",
            f"TP2   : {_fmt_price(trade.tp2)}",
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
        f"{'⏳':2} {open_c} open   {'🎯':2} {tp1_c} tp1   {'✅':2} {win_c} win   {'❌':2} {loss_c} loss",
        f"WR {wr_emoji} <b>{wr}%</b>  ({closed} closed)   {r_emoji} <b>{r_sign}{total_r:.1f}R</b>",
    ]

    recent = recap.get("recent", [])
    if recent:
        lines.extend(["", "─────────────────────"])
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
            created = _fmt_ts(r.get("created_at", 0))
            lines.append(
                f"{s_icon} {d_icon} <b>{sym}</b> {tf}  {entry} → {tp2}  SL {sl}{pnl_str}  <i>{created}</i>"
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
