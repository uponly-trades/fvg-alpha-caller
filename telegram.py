import io
import logging
from typing import Optional

import requests

from config import BOT_TOKEN, CHAT_ID

logger = logging.getLogger(__name__)
TELEGRAM_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
TELEGRAM_PHOTO_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"


def _tv_link(symbol: str, tf: str) -> str:
    """Build TradingView chart URL."""
    # Binance spot prefix; futures use same chart
    interval_map = {"15m": "15", "1h": "60", "4h": "240"}
    iv = interval_map.get(tf, "60")
    sym = symbol.replace("USDT", "")
    return f"https://www.tradingview.com/chart/?symbol=BINANCE%3A{symbol}&interval={iv}"


def send_new_fvg_alert(zone, chart_png: Optional[bytes] = None) -> bool:
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

    caption = (
        f"{emoji} <b>{dir_text.upper()} FVG — {zone.label}</b>\n\n"
        f"📊 <code>{zone.symbol}</code> | TF: <code>{zone.tf}</code>\n"
        f"💰 Price : {zone.price}\n"
        f"📏 Zone  : {zone.bottom} — {zone.top}\n"
        f"📐 Size  : {zone.size:.4f}\n\n"
        f"📈 Strength: {zone.main_strength}%\n"
        f"   • Bull: {zone.bull_strength}% | Bear: {zone.bear_strength}%\n"
        f"📊 RSI(14) : {zone.rsi}\n"
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


def send_approach_alert(zone, current_price: float, chart_png: Optional[bytes] = None) -> bool:
    emoji = "🟢" if zone.direction == 1 else "🔴"
    dir_text = "Bullish" if zone.direction == 1 else "Bearish"
    tv_url = _tv_link(zone.symbol, zone.tf)
    vol_icon = "🟢" if zone.vol_change_pct > 0 else "🔴"
    dom_text = {"ALT": "🟢 Alt Season", "BTC": "🔴 BTC Season", "NEUTRAL": "Neutral"}.get(zone.dominance_state, "Neutral")
    btc_text = {"UP": "🟢 Uptrend", "DOWN": "🔴 Downtrend", "NEUTRAL": "Neutral"}.get(zone.btc_state, "Neutral")
    disp_text = "YES" if zone.displacement_ok else "NO"
    btc_align_text = "YES" if zone.btc_alignment_ok else "NO"
    msg = (
        f"⚡ <b>APPROACHING {dir_text.upper()} ZONE</b>\n\n"
        f"{emoji} {zone.label}\n"
        f"📊 <code>{zone.symbol}</code> | TF: <code>{zone.tf}</code>\n"
        f"💰 Price : {current_price}\n"
        f"📏 Zone  : {zone.bottom} — {zone.top}\n"
        f"📈 Strength: {zone.main_strength}%\n\n"
        f"{vol_icon} Vol Change: {zone.vol_change_pct:+.1f}%\n"
        f"📍 Dist to Zone: {zone.dist_to_zone:.4f}\n"
        f"📊 RSI(14) : {zone.rsi}\n"
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


def send_touch_alert(zone, current_price: float, chart_png: Optional[bytes] = None) -> bool:
    emoji = "🟢" if zone.direction == 1 else "🔴"
    dir_text = "Bullish" if zone.direction == 1 else "Bearish"
    tv_url = _tv_link(zone.symbol, zone.tf)
    vol_icon = "🟢" if zone.vol_change_pct > 0 else "🔴"
    dom_text = {"ALT": "🟢 Alt Season", "BTC": "🔴 BTC Season", "NEUTRAL": "Neutral"}.get(zone.dominance_state, "Neutral")
    btc_text = {"UP": "🟢 Uptrend", "DOWN": "🔴 Downtrend", "NEUTRAL": "Neutral"}.get(zone.btc_state, "Neutral")
    disp_text = "YES" if zone.displacement_ok else "NO"
    btc_align_text = "YES" if zone.btc_alignment_ok else "NO"
    msg = (
        f"🔥 <b>TOUCH — {dir_text.upper()} ZONE</b>\n\n"
        f"{emoji} {zone.label}\n"
        f"📊 <code>{zone.symbol}</code> | TF: <code>{zone.tf}</code>\n"
        f"💰 Price : {current_price}\n"
        f"📏 Zone  : {zone.bottom} — {zone.top}\n"
        f"📈 Strength: {zone.main_strength}%\n"
        f"{vol_icon} Vol Change: {zone.vol_change_pct:+.1f}%\n"
        f"📊 RSI(14) : {zone.rsi}\n"
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
