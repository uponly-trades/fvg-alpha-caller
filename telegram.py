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


def _rev_check_lines(timeframe_bars: dict) -> List[str]:
    """Detect recent swing highs/lows per TF using pivot_highs/pivot_lows."""
    try:
        from indicator_context import pivot_highs, pivot_lows
    except ImportError:
        return []
    lines = []
    tf_order = ("15m", "30m", "1h", "2h", "4h")
    for tf in tf_order:
        bars = timeframe_bars.get(tf, [])
        if len(bars) < 25:
            continue
        highs = [float(b.high) for b in bars]
        lows = [float(b.low) for b in bars]
        ph = pivot_highs(highs, left=5, right=5)
        pl = pivot_lows(lows, left=5, right=5)
        if ph:
            last_top = highs[ph[-1]]
            lines.append(f"✅ Rev. Top    — {tf}  ({_fmt_price(last_top)})")
        if pl:
            last_bot = lows[pl[-1]]
            lines.append(f"✅ Rev. Bottom — {tf}  ({_fmt_price(last_bot)})")
    return lines


def _trade_title(zone, trade_setup, prefix: str = None) -> str:
    status = prefix if prefix else trade_setup.status
    return f"{status} | {_fvg_direction_text(zone)} FVG | {zone.symbol} | {zone.tf}"


def _format_trade_alert(zone, current_price: float, trade_setup, prefix: str = None, timeframe_bars: dict = None) -> str:
    tv_url = _tv_link(zone.symbol, zone.tf)
    lines = [f"<b>{_trade_title(zone, trade_setup, prefix)}</b>", ""]
    if trade_setup is not None and trade_setup.trade is not None:
        trade = trade_setup.trade
        lines.extend([
            f"Entry : <b>{_fmt_price(trade.entry)}</b>",
            f"SL    : {_fmt_price(trade.sl)}",
            f"TP1   : {_fmt_price(trade.tp1)}",
            f"TP2   : {_fmt_price(trade.tp2)}",
            "RR    : 1:2",
        ])
    else:
        lines.append(f"Price: {_fmt_price(current_price)}")
        if trade_setup is not None:
            lines.append(f"Skip Reason: {trade_setup.reason}")

    rsi_val = getattr(zone, "rsi", None)
    rsi_str = f"{_rsi_emoji(rsi_val)} RSI: {rsi_val:.1f}" if rsi_val is not None else ""
    mode = trade_setup.mode if trade_setup is not None else "—"
    lines.extend([
        f"Mode: {mode}",
        f"Zone: {_fmt_price(zone.bottom)} — {_fmt_price(zone.top)}",
        f"Confidence: {_confidence_label(zone.main_strength)} ({zone.main_strength}%)",
    ])
    if rsi_str:
        lines.append(rsi_str)
    if trade_setup is not None and trade_setup.trade is not None:
        lines.append(f"Reason: {trade_setup.reason}")

    rev_lines = _rev_check_lines(timeframe_bars or {})
    if rev_lines:
        lines.append("")
        lines.extend(rev_lines)

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


def send_trade_recap(session_name: str, recap: dict) -> bool:
    date_str = recap.get("date", "—")
    open_c = recap["open"]
    tp1_c = recap["tp1"]
    win_c = recap["win"]
    loss_c = recap["loss"]
    wr = recap["closed_winrate"]
    closed = win_c + loss_c

    wr_emoji = "🔥" if wr >= 70 else "✅" if wr >= 50 else "⚠️" if wr >= 30 else "❌"

    lines = [
        f"📊 <b>Trade Recap — {session_name}</b>",
        f"📅 {date_str}",
        "",
        f"⏳ Open      : <b>{open_c}</b>",
        f"🎯 TP1 Hit   : <b>{tp1_c}</b>",
        f"✅ Win (TP2) : <b>{win_c}</b>",
        f"❌ Loss      : <b>{loss_c}</b>",
        f"📈 Closed    : <b>{closed}</b>  |  WR: {wr_emoji} <b>{wr}%</b>",
    ]

    recent = recap.get("recent", [])
    if recent:
        lines.extend(["", "━━━━━━━━━━━━━━━━", "🕐 <b>Recent Trades</b>"])
        status_emoji = {"win": "✅", "loss": "❌", "tp1_hit": "🎯", "open": "⏳"}
        dir_emoji = {"long": "🟢 LONG", "short": "🔴 SHORT"}
        for r in recent:
            direction = dir_emoji.get(r.get("direction", ""), r.get("direction", "?").upper())
            status = str(r.get("status", "open"))
            s_emoji = status_emoji.get(status, "❓")
            s_label = status.upper().replace("TP1_HIT", "TP1 HIT")
            created = _fmt_ts(r.get("created_at", 0))
            lines.extend([
                "",
                f"{direction} | <code>{r['symbol']}</code> {r['tf']} | {created}",
                f"  Entry: <b>{_fmt_price(r['entry'])}</b>  SL: {_fmt_price(r['sl'])}  TP1: {_fmt_price(r['tp1'])}  TP2: {_fmt_price(r['tp2'])}",
                f"  {s_emoji} {s_label}",
            ])

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
