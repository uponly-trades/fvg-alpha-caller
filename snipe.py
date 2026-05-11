"""
Snipe entry modes for FVG-Alpha-Caller.

Two modes:
  1. Long limit snipe  — entry at zone.bottom (not market price)
     Triggers on: approach / touch events for bullish FVG zones
     Better RR: SL below zone.bottom, entry AT zone.bottom → exact 1:1 TP1 / 1:2 TP2

  2. Retest short snipe — after bullish FVG fully mitigated, monitor former zone.bottom
     as resistance. 1-candle confirmation: candle closes ABOVE former zone.bottom then
     next candle closes BELOW → short entry with combo timeframe v2 gate.

Both produce sim_trades records and Telegram alerts.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from trade_combo import TradeLevels, TradeSetupResult, _risk_buffer, _v2_short_decision

logger = logging.getLogger("snipe")


# ──────────────────────────────────────────────────────────────────────────────
# Long limit snipe
# ──────────────────────────────────────────────────────────────────────────────

def build_long_snipe(zone) -> Optional[TradeSetupResult]:
    """
    Entry at zone.bottom (limit), SL below zone.bottom, TP1 1R, TP2 2R.
    Only for bullish FVG zones. Returns None if risk is degenerate.
    """
    if int(zone.direction) != 1:
        return None

    buffer = _risk_buffer(zone)
    entry = float(zone.bottom)  # limit entry at zone edge (better than market)
    sl = entry - buffer
    risk = entry - sl
    if risk <= 0:
        return None

    tp1 = entry + risk
    tp2 = entry + risk * 2

    trade = TradeLevels(
        direction="long",
        entry=entry,
        sl=sl,
        tp1=tp1,
        tp2=tp2,
        rr=2.0,
    )
    return TradeSetupResult(
        status="SNIPE LONG",
        valid=True,
        mode="snipe",
        reason=f"limit snipe at zone bottom {entry:.6g}",
        trade=trade,
        combo_states={},
        sparklines={},
        source="snipe_long",
    )


# ──────────────────────────────────────────────────────────────────────────────
# Retest short snipe tracker
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class RetestZone:
    """Mitigated bullish FVG whose former bottom is now watched as resistance."""
    symbol: str
    tf: str
    resistance: float       # former zone.bottom — now acts as supply
    zone_top: float         # former zone.top (size reference)
    born_time: int          # original zone born_time (for sim_trade FK)
    atr: float = 0.0
    created_at: float = field(default_factory=time.time)
    bounced: bool = False   # True after 1 candle closes ABOVE resistance
    alerted: bool = False   # prevent double alert

    # ── 24-hour TTL ──
    TTL_SECONDS: float = field(default=86_400, init=False, repr=False)

    def expired(self) -> bool:
        return (time.time() - self.created_at) > self.TTL_SECONDS


class RetestTracker:
    """
    Tracks mitigated bullish FVG zones for retest short signals.

    On mitigation: register zone with add().
    On every bar close: call check() — returns RetestZone if short signal fires.

    State machine per RetestZone:
      WATCHING → (candle closes > resistance) → BOUNCED
      BOUNCED  → (next candle closes < resistance) → TRIGGERED (alert + remove)
      Any state: if close < resistance - atr*0.5, zone invalidated (price crashed through)
    """

    def __init__(self):
        # key: "{symbol}_{tf}_{born_time}" → RetestZone
        self._zones: Dict[str, RetestZone] = {}

    def add(self, zone) -> None:
        """Register a newly mitigated bullish FVG for retest monitoring."""
        if int(zone.direction) != 1:
            return
        key = f"{zone.symbol}_{zone.tf}_{int(zone.born_time)}"
        rz = RetestZone(
            symbol=zone.symbol,
            tf=zone.tf,
            resistance=float(zone.bottom),
            zone_top=float(zone.top),
            born_time=int(zone.born_time),
            atr=float(getattr(zone, "atr", 0.0) or 0.0),
        )
        self._zones[key] = rz
        logger.info("RetestTracker add %s %s | resistance=%.6g", zone.symbol, zone.tf, rz.resistance)

    def check(self, symbol: str, tf: str, bar) -> Optional[RetestZone]:
        """
        Check one closed bar. Returns RetestZone if short signal fires, else None.
        Fired zones and expired/invalidated zones are removed.
        """
        to_remove: List[str] = []
        result: Optional[RetestZone] = None

        for key, rz in list(self._zones.items()):
            if rz.symbol != symbol or rz.tf != tf:
                continue
            if rz.expired():
                to_remove.append(key)
                logger.info("RetestTracker expire %s %s", symbol, tf)
                continue
            if rz.alerted:
                to_remove.append(key)
                continue

            close = float(bar.close)
            buf = rz.atr * 0.1 if rz.atr > 0 else (rz.zone_top - rz.resistance) * 0.1

            # Invalidate if price crashes far below resistance without a bounce
            if not rz.bounced and close < rz.resistance - rz.atr * 0.5:
                to_remove.append(key)
                logger.info(
                    "RetestTracker invalidate %s %s | close=%.6g below resistance=%.6g",
                    symbol, tf, close, rz.resistance,
                )
                continue

            if not rz.bounced:
                # Wait for a candle to close ABOVE resistance (the bounce)
                if close > rz.resistance:
                    rz.bounced = True
                    logger.info(
                        "RetestTracker bounce %s %s | close=%.6g > resistance=%.6g",
                        symbol, tf, close, rz.resistance,
                    )
            else:
                # Bounced — wait for next candle to close BELOW resistance (rejection)
                if close < rz.resistance:
                    rz.alerted = True
                    result = rz
                    to_remove.append(key)
                    logger.info(
                        "RetestTracker SHORT SIGNAL %s %s | resistance=%.6g close=%.6g",
                        symbol, tf, rz.resistance, close,
                    )
                    break  # one signal per bar per symbol/tf is enough

        for k in to_remove:
            self._zones.pop(k, None)

        return result


def build_retest_short(rz: RetestZone, current_price: float) -> Optional[TradeSetupResult]:
    """
    Build short TradeSetupResult from a triggered retest zone.
    Entry = current_price (market; already rejected), SL above resistance, TP 1:2.
    """
    buf = rz.atr * 0.1 if rz.atr > 0 else (rz.zone_top - rz.resistance) * 0.1
    entry = current_price
    sl = rz.resistance + buf           # just above former zone.bottom (now resistance)
    risk = abs(sl - entry)
    if risk <= 0 or entry >= sl:
        return None

    tp1 = entry - risk
    tp2 = entry - risk * 2

    trade = TradeLevels(
        direction="short",
        entry=entry,
        sl=sl,
        tp1=tp1,
        tp2=tp2,
        rr=2.0,
    )
    return TradeSetupResult(
        status="SNIPE SHORT",
        valid=True,
        mode="snipe",
        reason=f"retest short at former support {rz.resistance:.6g} → now resistance",
        trade=trade,
        combo_states={},
        sparklines={},
        source="snipe_retest_short",
    )


def gate_retest_short(bars_by_tf: Dict[str, List]) -> Tuple[bool, str]:
    """
    Gate a retest short through v2 short conditions:
      - 1h ema20_dist_pct < 0 (downtrend)
      - stable 15m vol spike
      - 15m OI change >= 0
    Returns (pass, reason).
    """
    dec = _v2_short_decision(bars_by_tf)
    return dec["valid"], dec["reason"]


# ──────────────────────────────────────────────────────────────────────────────
# HTF fade short
# ──────────────────────────────────────────────────────────────────────────────

def build_htf_fade_short(zone, current_price: float, htf_rsi7: float) -> Optional[TradeSetupResult]:
    """
    Fade short when a bullish FVG forms while 4h is overbought.
    model hard-gated this LONG → RANGING. We exploit the same signal as SHORT.

    Entry = current_price (market, at zone touch/approach).
    SL = zone.top + buffer (above the FVG that just formed — if price pushes above
         the whole FVG, the fade thesis is invalidated).
    TP1 = 1R, TP2 = 2R downward.
    """
    if int(zone.direction) != 1:
        return None  # only makes sense fading a bullish FVG

    buffer = _risk_buffer(zone)
    entry = float(current_price)
    sl = float(zone.top) + buffer   # invalidated if price clears the FVG top
    risk = abs(sl - entry)
    if risk <= 0 or entry >= sl:
        return None

    tp1 = entry - risk
    tp2 = entry - risk * 2

    trade = TradeLevels(
        direction="short",
        entry=entry,
        sl=sl,
        tp1=tp1,
        tp2=tp2,
        rr=2.0,
    )
    return TradeSetupResult(
        status="HTF FADE SHORT",
        valid=True,
        mode="snipe",
        reason=f"4h RSI7={htf_rsi7:.1f} OB → fade bullish FVG short (SL above zone.top)",
        trade=trade,
        combo_states={},
        sparklines={},
        source="snipe_htf_fade",
    )
