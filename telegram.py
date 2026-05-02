import logging
from typing import Optional

import requests

from config import BOT_TOKEN, CHAT_ID

logger = logging.getLogger(__name__)
TELEGRAM_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def send_new_fvg_alert(zone) -> bool:
    emoji = "🟢" if zone.direction == 1 else "🔴"
    dir_text = "Bullish" if zone.direction == 1 else "Bearish"
    msg = (
        f"{emoji} <b>NEW {dir_text.upper()} FVG</b>\n"
        f"Symbol : <code>{zone.symbol}</code>\n"
        f"TF     : <code>{zone.tf}</code>\n"
        f"Zone   : {zone.bottom:.4f} - {zone.top:.4f}\n"
        f"Size   : {zone.size:.4f}\n"
        f"Label  : {zone.label}\n"
        f"Bull%  : {zone.bull_strength}% | Bear% : {zone.bear_strength}%"
    )
    return _send(msg)


def send_mitigated_alert(zone) -> bool:
    emoji = "🟢" if zone.direction == 1 else "🔴"
    dir_text = "Bullish" if zone.direction == 1 else "Bearish"
    msg = (
        f"⚪ <b>FVG FULLY MITIGATED</b>\n"
        f"{emoji} {dir_text}\n"
        f"Symbol : <code>{zone.symbol}</code>\n"
        f"TF     : <code>{zone.tf}</code>\n"
        f"Zone   : {zone.bottom:.4f} - {zone.top:.4f}"
    )
    return _send(msg)


def send_touched_alert(zone, current_price: float, fill_pct: float) -> bool:
    emoji = "🟡"
    dir_text = "Bullish" if zone.direction == 1 else "Bearish"
    msg = (
        f"{emoji} <b>{dir_text.upper()} FVG TOUCHED</b>\n"
        f"Symbol : <code>{zone.symbol}</code>\n"
        f"TF     : <code>{zone.tf}</code>\n"
        f"Price  : {current_price:.4f}\n"
        f"Zone   : {zone.bottom:.4f} - {zone.top:.4f}\n"
        f"Fill   : {int(fill_pct * 100)}%"
    )
    return _send(msg)


def _send(text: str) -> bool:
    try:
        resp = requests.post(
            TELEGRAM_URL,
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error("Telegram send failed: %s", e)
        return False
