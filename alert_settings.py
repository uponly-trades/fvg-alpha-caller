"""
Persistent alert trigger settings.

Stored at /app/data/alert_settings.json (or ALERT_SETTINGS_PATH env var).
Changed via Telegram /settings command; applied immediately, no restart needed.

Available triggers:
  new_fvg        — new FVG detected alert
  approach       — price approaching zone alert
  touch          — price touching zone alert
  snipe_long     — snipe long limit entry alert
  snipe_short    — retest short snipe alert
  snipe_htf_fade — HTF overbought fade short (4h RSI7 hard gate triggered)

model v2 gate is always active regardless of these settings.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Dict

logger = logging.getLogger("alert_settings")

_PATH = Path(os.environ.get("ALERT_SETTINGS_PATH", "/app/data/alert_settings.json"))

TRIGGER_KEYS = ("new_fvg", "approach", "touch", "snipe_long", "snipe_short", "snipe_htf_fade")

_DEFAULTS: Dict[str, bool] = {
    "new_fvg":        True,
    "approach":       True,
    "touch":          True,
    "snipe_long":     True,
    "snipe_short":    True,
    "snipe_htf_fade": True,
}

_cache: Dict[str, bool] = {}


def _load() -> Dict[str, bool]:
    global _cache
    try:
        if _PATH.exists():
            data = json.loads(_PATH.read_text())
            _cache = {k: bool(data.get(k, _DEFAULTS[k])) for k in TRIGGER_KEYS}
        else:
            _cache = dict(_DEFAULTS)
    except Exception as e:
        logger.warning("alert_settings load failed: %s", e)
        _cache = dict(_DEFAULTS)
    return _cache


def _save(settings: Dict[str, bool]) -> None:
    try:
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        _PATH.write_text(json.dumps(settings, indent=2))
    except Exception as e:
        logger.warning("alert_settings save failed: %s", e)


def get_settings() -> Dict[str, bool]:
    if not _cache:
        _load()
    return dict(_cache)


def is_enabled(trigger: str) -> bool:
    if not _cache:
        _load()
    return _cache.get(trigger, True)


def set_trigger(trigger: str, enabled: bool) -> bool:
    """Set one trigger on/off. Returns False if trigger name unknown."""
    if trigger not in TRIGGER_KEYS:
        return False
    if not _cache:
        _load()
    _cache[trigger] = enabled
    _save(_cache)
    logger.info("alert_settings: %s=%s", trigger, enabled)
    return True


# Load on import
_load()
