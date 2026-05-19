"""Best-effort outbound webhook that forwards generated chart PNGs to notify-bot.

notify-bot caches the chart keyed by signal_id and later attaches it to its
"Signals · Exec" Telegram card when app-stable reports the same symbol+direction
has been executed.

Failures here MUST NEVER affect the user-facing fvg-alpha-caller alert path:
every call is wrapped, every error is logged at WARNING, nothing is raised.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_TIMEOUT_SEC = 5
_ENV_URL = "NOTIFY_BOT_WEBHOOK_URL"
_ENV_TOKEN = "NOTIFY_BOT_WEBHOOK_TOKEN"


def _normalize_direction(direction) -> Optional[str]:
    """Map fvg-alpha-caller direction (1/-1 int or 'LONG'/'SHORT' str) to
    lowercase 'long'/'short' to match app-stable's signal_decision_log column."""
    if isinstance(direction, str):
        d = direction.strip().lower()
        if d in ("long", "short"):
            return d
        return None
    try:
        d_int = int(direction)
    except (TypeError, ValueError):
        return None
    if d_int == 1:
        return "long"
    if d_int == -1:
        return "short"
    return None


def post_chart(
    signal_id: str,
    symbol: str,
    direction,
    tf: str,
    entry,
    sl,
    tp1,
    png_bytes: Optional[bytes],
) -> None:
    """Send chart PNG + minimal correlation metadata to notify-bot.

    No-op (logs at debug) when the webhook URL, bearer token, or PNG bytes are
    missing. Never raises.
    """
    try:
        url = (os.environ.get(_ENV_URL) or "").strip()
        token = (os.environ.get(_ENV_TOKEN) or "").strip()
        if not url or not token or not png_bytes:
            logger.debug(
                "notify-bot webhook skipped (url=%s token=%s png=%s)",
                bool(url), bool(token), bool(png_bytes),
            )
            return

        dir_str = _normalize_direction(direction)
        if dir_str is None:
            logger.warning(
                "notify-bot webhook skipped: unrecognized direction %r for %s",
                direction, signal_id,
            )
            return

        meta = {
            "signal_id": signal_id,
            "symbol": symbol,
            "direction": dir_str,
            "tf": tf,
            "entry": float(entry) if entry is not None else None,
            "sl": float(sl) if sl is not None else None,
            "tp1": float(tp1) if tp1 is not None else None,
            "ts_iso": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": "fvg-alpha-caller",
        }

        files = {
            "meta": ("meta.json", json.dumps(meta), "application/json"),
            "chart": ("chart.png", png_bytes, "image/png"),
        }
        headers = {"Authorization": f"Bearer {token}"}

        resp = requests.post(url, headers=headers, files=files, timeout=_TIMEOUT_SEC)
        if 200 <= resp.status_code < 300:
            logger.info(
                "notify-bot webhook ok %s (%d bytes, status=%d)",
                signal_id, len(png_bytes), resp.status_code,
            )
        else:
            logger.warning(
                "notify-bot webhook non-2xx %s status=%d body=%r",
                signal_id, resp.status_code, resp.text[:200],
            )
    except Exception as e:
        logger.warning("notify-bot webhook failed %s: %s", signal_id, e)
