import io
import logging
from datetime import datetime, timezone
from typing import Optional

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


def _trade_title(zone, trade_setup, prefix: str = None) -> str:
    status = prefix if prefix else trade_setup.status
    return f"{status} | {_fvg_direction_text(zone)} FVG | {zone.symbol} | {zone.tf}"


def _format_trade_alert(zone, current_price: float, trade_setup, prefix: str = None) -> str:
    tv_url = _tv_link(zone.symbol, zone.tf)
    lines = [f"<b>{_trade_title(zone, trade_setup, prefix)}</b>", ""]
    if trade_setup.trade is not None:
        trade = trade_setup.trade
        lines.extend([
            f"Entry : <b>{_fmt_price(trade.entry)}</b>",
            f"SL    : {_fmt_price(trade.sl)}",
            f"TP1   : {_fmt_price(trade.tp1)}",
            f"TP2   : {_fmt_price(trade.tp2)}",
            "RR    : 1:2",
        ])
    else:
        lines.append(f"Price: {current_price}")
        lines.append(f"Skip Reason: {trade_setup.reason}")

    rsi_val = getattr(zone, "rsi", None)
    rsi_str = f"{_rsi_emoji(rsi_val)} RSI: {rsi_val:.1f}" if rsi_val is not None else ""
    lines.extend([
        f"Mode: {trade_setup.mode}",
        f"Zone: {zone.bottom} — {zone.top}",
        f"Confidence: {_confidence_label(zone.main_strength)} ({zone.main_strength}%)",
    ])
    if rsi_str:
        lines.append(rsi_str)
    if trade_setup.trade is not None:
        lines.append(f"Reason: {trade_setup.reason}")
    lines.append("")
    lines.append(f"<a href='{tv_url}'>Open TradingView</a>")
    return "\n".join(lines)


def send_new_fvg_alert(zone, chart_png: Optional[bytes] = None, trade_setup=None) -> bool:
    if trade_setup is not None:
        msg = _format_trade_alert(zone, getattr(zone, "price", 0.0), trade_setup, prefix="NEW FVG")
        if chart_png:
            return _send_photo(msg, chart_png)
        return _send(msg)

    emoji = "🟢" if zone.direction == 1 else "🔴"
    dir_text = "Bullish" if zone.direction == 1 else "Bearish"
    tv_url = _tv_link(zone.symbol, zone.tf)
    vol_icon = "🟢" if zone.vol_change_pct > 0 else "🔴"
    price_icon = "🟢" if zone.price_change_pct > 0 else "🔴"

    dom_text = {"ALT": "🟢 Alt Season", "BTC": "🔴 BTC Season", "NEUTRAL": "Neutral"}.get(zone.dominance_state, "Neutral")
    btc_text = {"UP": "🟢 Uptrend", "DOWN": "🔴 Downtrend", "NEUTRAL": "Neutral"}.get(zone.btc_state, "Neutral")
    disp_text = "YES" if zone.displacement_ok else "NO"
    btc_align_text = "YES" if zone.btc_alignment_ok else "NO"
    invalid_text = f"\n❌ Invalid: {zone.invalid_reason}" if getattr(zone, "invalidated", False) and zone.invalid_reason else ""
    rsi_icon = _rsi_emoji(zone.rsi)
    caption = (
        f"{emoji} <b>{dir_text.upper()} FVG — {zone.label}</b>\n\n"
        f"📊 <code>{zone.symbol}</code> | TF: <code>{zone.tf}</code>\n"
        f"💰 Price : {zone.price}\n"
        f"📏 Zone  : {zone.bottom} — {zone.top}\n"
        f"📐 Size  : {zone.size:.4f}\n\n"
        f"🎯 Confidence: {_confidence_label(zone.main_strength)} ({zone.main_strength}%)\n"
        f"   • Bull: {zone.bull_strength}% | Bear: {zone.bear_strength}%\n"
        f"{rsi_icon} RSI(14) : {zone.rsi}\n"
        f"📊 ATR(14) : {zone.atr}\n\n"
        f"{vol_icon} Vol Change: {zone.vol_change_pct:+.1f}%\n"
        f"{price_icon} Bar Change: {zone.price_change_pct:+.2f}%\n"
        f"📆 24h Change: {zone.price_change_24h_pct:+.2f}%\n"
        f"📊 Candle Body: {zone.candle_body_pct:.1f}%\n"
        f"📍 Dist to Zone: {zone.dist_to_zone:.4f}\n\n"
        f"🌐 BTCDOM: {dom_text} ({zone.dominance_bias:+.4f})\n"
        f"₿ BTC Trend: {btc_text} ({zone.btc_trend:+.4f})\n"
        f"⚡ Confirm: {zone.confirm_score} ({zone.confirm_label})\n"
        f"• Vol Spike: {zone.volume_spike_ratio:.2f}x\n"
        f"• Confluence: {zone.confluence_tf_count} TF\n"
        f"• Displacement: {disp_text}\n"
        f"• BTC Align: {btc_align_text}{invalid_text}\n\n"
        f"🛑 SL : {zone.sl}\n"
        f"🎯 TP1: {zone.tp1} (1.5×)\n"
        f"🎯 TP2: {zone.tp2} (2.5×)\n\n"
        f"🔗 <a href='{tv_url}'>Open TradingView</a>"
    )

    if chart_png:
        return _send_photo(caption, chart_png)
    return _send(caption)


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


def send_approach_alert(zone, current_price: float, chart_png: Optional[bytes] = None, trade_setup=None) -> bool:
    if trade_setup is not None:
        msg = _format_trade_alert(zone, current_price, trade_setup)
        if chart_png:
            return _send_photo(msg, chart_png)
        return _send(msg)

    emoji = "🟢" if zone.direction == 1 else "🔴"
    dir_text = "Bullish" if zone.direction == 1 else "Bearish"
    tv_url = _tv_link(zone.symbol, zone.tf)
    vol_icon = "🟢" if zone.vol_change_pct > 0 else "🔴"
    dom_text = {"ALT": "🟢 Alt Season", "BTC": "🔴 BTC Season", "NEUTRAL": "Neutral"}.get(zone.dominance_state, "Neutral")
    btc_text = {"UP": "🟢 Uptrend", "DOWN": "🔴 Downtrend", "NEUTRAL": "Neutral"}.get(zone.btc_state, "Neutral")
    disp_text = "YES" if zone.displacement_ok else "NO"
    btc_align_text = "YES" if zone.btc_alignment_ok else "NO"
    rsi_icon = _rsi_emoji(zone.rsi)
    msg = (
        f"⚡ <b>APPROACHING {dir_text.upper()} ZONE</b>\n\n"
        f"{emoji} {zone.label}\n"
        f"📊 <code>{zone.symbol}</code> | TF: <code>{zone.tf}</code>\n"
        f"💰 Price : {current_price}\n"
        f"📏 Zone  : {zone.bottom} — {zone.top}\n"
        f"🎯 Confidence: {_confidence_label(zone.main_strength)} ({zone.main_strength}%)\n\n"
        f"{vol_icon} Vol Change: {zone.vol_change_pct:+.1f}%\n"
        f"📍 Dist to Zone: {zone.dist_to_zone:.4f}\n"
        f"{rsi_icon} RSI(14) : {zone.rsi}\n"
        f"📆 24h Change: {zone.price_change_24h_pct:+.2f}%\n"
        f"🌐 BTCDOM: {dom_text} ({zone.dominance_bias:+.4f})\n"
        f"₿ BTC: {btc_text} ({zone.btc_trend:+.4f})\n"
        f"⚡ Confirm: {zone.confirm_score} ({zone.confirm_label}) | Vol {zone.volume_spike_ratio:.2f}x | Conf {zone.confluence_tf_count}TF | Disp {disp_text} | BTC {btc_align_text}\n\n"
        f"🛑 SL : {zone.sl}\n"
        f"🎯 TP1: {zone.tp1} (1.5×)\n"
        f"🎯 TP2: {zone.tp2} (2.5×)\n\n"
        f"🔗 <a href='{tv_url}'>Open TradingView</a>"
    )
    if chart_png:
        return _send_photo(msg, chart_png)
    return _send(msg)


def send_touch_alert(zone, current_price: float, chart_png: Optional[bytes] = None, trade_setup=None) -> bool:
    if trade_setup is not None:
        msg = _format_trade_alert(zone, current_price, trade_setup)
        if chart_png:
            return _send_photo(msg, chart_png)
        return _send(msg)

    emoji = "🟢" if zone.direction == 1 else "🔴"
    dir_text = "Bullish" if zone.direction == 1 else "Bearish"
    tv_url = _tv_link(zone.symbol, zone.tf)
    vol_icon = "🟢" if zone.vol_change_pct > 0 else "🔴"
    dom_text = {"ALT": "🟢 Alt Season", "BTC": "🔴 BTC Season", "NEUTRAL": "Neutral"}.get(zone.dominance_state, "Neutral")
    btc_text = {"UP": "🟢 Uptrend", "DOWN": "🔴 Downtrend", "NEUTRAL": "Neutral"}.get(zone.btc_state, "Neutral")
    disp_text = "YES" if zone.displacement_ok else "NO"
    btc_align_text = "YES" if zone.btc_alignment_ok else "NO"
    rsi_icon = _rsi_emoji(zone.rsi)
    msg = (
        f"🔥 <b>TOUCH — {dir_text.upper()} ZONE</b>\n\n"
        f"{emoji} {zone.label}\n"
        f"📊 <code>{zone.symbol}</code> | TF: <code>{zone.tf}</code>\n"
        f"💰 Price : {current_price}\n"
        f"📏 Zone  : {zone.bottom} — {zone.top}\n"
        f"🎯 Confidence: {_confidence_label(zone.main_strength)} ({zone.main_strength}%)\n"
        f"{vol_icon} Vol Change: {zone.vol_change_pct:+.1f}%\n"
        f"{rsi_icon} RSI(14) : {zone.rsi}\n"
        f"📆 24h Change: {zone.price_change_24h_pct:+.2f}%\n"
        f"🌐 BTCDOM: {dom_text} ({zone.dominance_bias:+.4f})\n"
        f"₿ BTC: {btc_text} ({zone.btc_trend:+.4f})\n"
        f"⚡ Confirm: {zone.confirm_score} ({zone.confirm_label}) | Vol {zone.volume_spike_ratio:.2f}x | Conf {zone.confluence_tf_count}TF | Disp {disp_text} | BTC {btc_align_text}\n\n"
        f"🛑 SL : {zone.sl}\n"
        f"🎯 TP1: {zone.tp1} (1.5×)\n"
        f"🎯 TP2: {zone.tp2} (2.5×)\n\n"
        f"🔗 <a href='{tv_url}'>Open TradingView</a>"
    )
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


def _fmt_ts(ts_ms) -> str:
    try:
        dt = datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc)
        return dt.strftime("%d %b %H:%M UTC")
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
