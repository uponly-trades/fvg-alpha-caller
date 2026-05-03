import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import requests

from config import (
    ATR_LEN, BASE_URL, CANDLE_WEIGHT, GAP_WEIGHT, MIN_STRENGTH_TO_ALERT,
    TREND_EMA_LEN, TREND_WEIGHT, VOL_MA_LEN, VOL_WEIGHT,
)

ZONE_TTL_MS = 24 * 60 * 60 * 1000  # 24 hours
PERSIST_PATH = os.environ.get("ZONE_PERSIST_PATH", "/app/data/zones.json")

logger = logging.getLogger(__name__)

# --- BTCDOM / BTC trend cache ---
# Fetches BTCDOMUSDT and BTCUSDT klines periodically to determine
# altcoin dominance bias and BTC trend for BTC's own signals.
_DOMINANCE_CACHE: Dict[str, float] = {}  # {"btcdom_ema": float, "btc_ema": float}
_DOMINANCE_LAST_FETCH = 0.0
DOMINANCE_FETCH_INTERVAL = 300  # refresh every 5 min


def _fetch_closes(symbol: str, interval: str = "1h", limit: int = 60) -> List[float]:
    """Fetch recent closed kline closes from Binance Futures."""
    url = f"{BASE_URL}/fapi/v1/klines"
    try:
        resp = requests.get(url, params={"symbol": symbol, "interval": interval, "limit": limit}, timeout=10)
        resp.raise_for_status()
        raw = resp.json()
        # drop last (forming) candle
        return [float(k[4]) for k in raw[:-1]]
    except Exception as e:
        logger.warning("Fetch closes failed %s: %s", symbol, e)
        return []


def get_dominance_bias() -> float:
    """
    Returns BTCDOM trend bias: negative = alt season (money leaving BTC), positive = BTC season.
    Range roughly -1.0 to +1.0 based on EMA slope.
    """
    global _DOMINANCE_CACHE, _DOMINANCE_LAST_FETCH
    now = time.time()
    if now - _DOMINANCE_LAST_FETCH < DOMINANCE_FETCH_INTERVAL and "btcdom_ema" in _DOMINANCE_CACHE:
        current = _DOMINANCE_CACHE.get("btcdom_current", 50.0)
        ema_val = _DOMINANCE_CACHE["btcdom_ema"]
        return (current - ema_val) / max(ema_val, 1.0) * 10  # scaled

    closes = _fetch_closes("BTCDOMUSDT", "1h", 60)
    if len(closes) < 20:
        return 0.0
    _DOMINANCE_LAST_FETCH = now
    _DOMINANCE_CACHE["btcdom_current"] = closes[-1]
    _DOMINANCE_CACHE["btcdom_ema"] = ema(closes, 20)
    current = closes[-1]
    ema_val = _DOMINANCE_CACHE["btcdom_ema"]
    return (current - ema_val) / max(ema_val, 1.0) * 10


def get_btc_trend() -> float:
    """
    Returns BTC trend: positive = uptrend, negative = downtrend.
    Based on close vs EMA50 on 1h.
    """
    global _DOMINANCE_CACHE
    now = time.time()
    if now - _DOMINANCE_LAST_FETCH < DOMINANCE_FETCH_INTERVAL and "btc_ema" in _DOMINANCE_CACHE:
        current = _DOMINANCE_CACHE.get("btc_current", 0.0)
        ema_val = _DOMINANCE_CACHE["btc_ema"]
        if ema_val > 0:
            return (current - ema_val) / ema_val * 10
        return 0.0

    closes = _fetch_closes("BTCUSDT", "1h", 60)
    if len(closes) < 50:
        return 0.0
    _DOMINANCE_CACHE["btc_current"] = closes[-1]
    _DOMINANCE_CACHE["btc_ema"] = ema(closes, 50)
    current = closes[-1]
    ema_val = _DOMINANCE_CACHE["btc_ema"]
    if ema_val > 0:
        return (current - ema_val) / ema_val * 10
    return 0.0


@dataclass
class FVGZone:
    symbol: str
    tf: str
    direction: int          # 1 = bull, -1 = bear
    top: float
    bottom: float
    size: float
    born_time: int
    mitigation: float = 0.0
    bull_strength: int = 0
    bear_strength: int = 0
    main_strength: int = 0
    label: str = ""
    alerted: bool = False
    mitigated_alerted: bool = False
    rsi: float = 50.0
    atr: float = 0.0
    sl: float = 0.0
    tp1: float = 0.0
    tp2: float = 0.0
    price: float = 0.0
    # Extra metrics
    vol_change_pct: float = 0.0   # vs previous bar
    price_change_pct: float = 0.0 # bar change percent
    candle_body_pct: float = 0.0  # body / range
    dist_to_zone: float = 0.0     # distance from price to zone edge
    # Interaction tracking
    approach_alerted: bool = False
    touch_alerted: bool = False
    # Market context
    dominance_bias: float = 0.0   # BTCDOM trend: negative=alt season
    btc_trend: float = 0.0        # BTC trend: positive=uptrend


def sma(values: List[float], length: int) -> Optional[float]:
    if len(values) < length:
        return None
    return sum(values[-length:]) / length


def ema(values: List[float], length: int) -> Optional[float]:
    if len(values) < length:
        return None
    k = 2 / (length + 1)
    ema_val = sum(values[:length]) / length
    for v in values[length:]:
        ema_val = v * k + ema_val * (1 - k)
    return ema_val


def rma(values: List[float], length: int) -> Optional[float]:
    """Wilder's smoothing — matches Pine Script ta.rma()."""
    if len(values) < length:
        return None
    alpha = 1.0 / length
    rma_val = sum(values[:length]) / length
    for v in values[length:]:
        rma_val = alpha * v + (1 - alpha) * rma_val
    return rma_val


def atr(highs: List[float], lows: List[float], closes: List[float], length: int) -> Optional[float]:
    if len(highs) < length + 1 or len(lows) < length + 1 or len(closes) < length + 1:
        return None
    trs = []
    for i in range(1, length + 1):
        h, l, c = highs[-i], lows[-i], closes[-(i + 1)]
        tr = max(h - l, abs(h - c), abs(l - c))
        trs.append(tr)
    trs.reverse()  # oldest first for RMA
    return rma(trs, length)


def detect_fvg(bars: List, symbol: str = "") -> Optional[Dict]:
    """Detect FVG on the most recently closed bar (last in list)."""
    if len(bars) < 3:
        return None
    # bars[-1] = current/just-closed, bars[-3] = 2 bars ago
    prev2 = bars[-3]   # bar[2] in Pine
    prev1 = bars[-2]   # bar[1] in Pine
    curr  = bars[-1]   # bar[0] in Pine

    bull = curr.low > prev2.high
    bear = curr.high < prev2.low

    if not bull and not bear:
        return None

    direction = 1 if bull else -1
    top = curr.low if bull else prev2.low
    bottom = prev2.high if bull else curr.high
    fvg_size = abs(top - bottom)

    return {
        "direction": direction,
        "top": top,
        "bottom": bottom,
        "size": fvg_size,
        "born_time": curr.open_time,
    }


def calc_strength(bars: List, fvg: Dict, symbol: str = "") -> Dict:
    """Calculate strength scores identical to Pine Script logic."""
    closes  = [b.close for b in bars]
    highs   = [b.high for b in bars]
    lows    = [b.low for b in bars]
    volumes = [b.volume for b in bars]

    curr = bars[-1]

    # Volume score
    vol_ma = sma(volumes, VOL_MA_LEN)
    vol_score = volumes[-1] / vol_ma if vol_ma and vol_ma != 0 else 1.0

    # Trend score
    trend_ema = ema(closes, TREND_EMA_LEN)
    direction = fvg["direction"]
    trend_score = 1.0 if (direction == 1 and curr.close > trend_ema) or (direction == -1 and curr.close < trend_ema) else 0.0

    # ATR for gap strength
    atr_val = atr(highs, lows, closes, ATR_LEN)
    gap_strength = min(fvg["size"] / atr_val, 2.0) / 2.0 * GAP_WEIGHT if atr_val and atr_val > 0 else 0

    # Volume strength
    vol_strength = min(vol_score, 2.0) / 2.0 * VOL_WEIGHT

    # Trend strength
    trend_strength = trend_score * TREND_WEIGHT

    # Candle strength
    candle_range = max(curr.high - curr.low, 0.01)
    candle_strength = abs(curr.close - curr.open) / candle_range * CANDLE_WEIGHT

    # Market context: dominance + BTC trend
    symbol = fvg.get("symbol", "")
    dom_bias = get_dominance_bias()
    btc_tr = get_btc_trend()
    is_btc = symbol == "BTCUSDT"

    # Context bonus/penalty (up to ±10 points)
    context_adj = 0.0
    if is_btc:
        # BTC signals: boosted when BTC in trend direction
        if direction == 1 and btc_tr > 0:
            context_adj = min(btc_tr, 1.0) * 10
        elif direction == -1 and btc_tr < 0:
            context_adj = min(abs(btc_tr), 1.0) * 10
        elif direction == 1 and btc_tr < 0:
            context_adj = max(btc_tr, -1.0) * 10
        elif direction == -1 and btc_tr > 0:
            context_adj = max(-btc_tr, -1.0) * 10
    else:
        # Alt signals: boosted when BTCDOM falling (alt season) for bullish, rising for bearish
        if direction == 1 and dom_bias < 0:
            context_adj = min(abs(dom_bias), 1.0) * 10
        elif direction == -1 and dom_bias > 0:
            context_adj = min(dom_bias, 1.0) * 10
        elif direction == 1 and dom_bias > 0:
            context_adj = max(-dom_bias, -1.0) * 10
        elif direction == -1 and dom_bias < 0:
            context_adj = max(dom_bias, -1.0) * 10

    total = gap_strength + vol_strength + trend_strength + candle_strength + context_adj
    main_strength = int(max(min(total, 100), 0))

    bull_str = main_strength if direction == 1 else 100 - main_strength
    bear_str = main_strength if direction == -1 else 100 - main_strength

    # Label
    if direction == -1:
        if bear_str >= 70:
            label = "Strong Bearish Imbalance"
        elif bear_str >= 55:
            label = "Bearish Bias"
        elif bull_str > bear_str:
            label = "Weak Bearish (Bull Pressure)"
        else:
            label = "Neutral Bearish"
    else:
        if bull_str >= 70:
            label = "Strong Bullish Imbalance"
        elif bull_str >= 55:
            label = "Bullish Bias"
        elif bear_str > bull_str:
            label = "Weak Bullish (Bear Pressure)"
        else:
            label = "Neutral Bullish"

    # RSI(14) with Wilder's smoothing
    rsi_val = 50.0
    if len(closes) >= 15:
        import numpy as np
        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        if len(gains) >= 14:
            avg_gain = np.mean(gains[:14])
            avg_loss = np.mean(losses[:14])
            for i in range(14, len(gains)):
                avg_gain = (avg_gain * 13 + gains[i]) / 14
                avg_loss = (avg_loss * 13 + losses[i]) / 14
            if avg_loss > 0:
                rs = avg_gain / avg_loss
                rsi_val = 100 - (100 / (1 + rs))
            else:
                rsi_val = 100.0

    # Extra metrics
    vol_change_pct = 0.0
    if len(volumes) >= 2 and volumes[-2] > 0:
        vol_change_pct = (volumes[-1] - volumes[-2]) / volumes[-2] * 100

    price_change_pct = 0.0
    if curr.open != 0:
        price_change_pct = (curr.close - curr.open) / curr.open * 100

    candle_body_pct = 0.0
    if candle_range > 0:
        candle_body_pct = abs(curr.close - curr.open) / candle_range * 100

    dist_to_zone = 0.0
    if direction == 1:
        dist_to_zone = curr.close - fvg["top"]
    else:
        dist_to_zone = fvg["bottom"] - curr.close

    # SL / TP based on ATR
    atr_val = atr_val if atr_val else fvg["size"]
    sl = fvg["bottom"] - atr_val * 0.8 if direction == 1 else fvg["top"] + atr_val * 0.8
    tp1 = fvg["top"] + atr_val * 1.5 if direction == 1 else fvg["bottom"] - atr_val * 1.5
    tp2 = fvg["top"] + atr_val * 2.5 if direction == 1 else fvg["bottom"] - atr_val * 2.5

    return {
        "main_strength": main_strength,
        "bull_strength": bull_str,
        "bear_strength": bear_str,
        "vol_score": vol_score,
        "trend_score": trend_score,
        "vol_change_pct": round(vol_change_pct, 1),
        "price_change_pct": round(price_change_pct, 2),
        "candle_body_pct": round(candle_body_pct, 1),
        "dist_to_zone": round(dist_to_zone, 4),
        "label": label,
        "rsi": round(rsi_val, 1),
        "atr": round(atr_val, 4),
        "sl": round(sl, 4),
        "tp1": round(tp1, 4),
        "tp2": round(tp2, 4),
        "price": round(curr.close, 4),
        "dominance_bias": round(dom_bias, 2),
        "btc_trend": round(btc_tr, 2),
    }


class FVGTracker:
    def __init__(self):
        # key: (symbol, tf)  -> list of Bar
        self.buffers: Dict[tuple, List] = {}
        # key: zone_id -> FVGZone
        self.zones: Dict[str, FVGZone] = {}
        # key: (symbol, tf) -> last processed bar open_time
        self.last_bar_time: Dict[tuple, int] = {}
        self._load_zones()

    def _zone_to_dict(self, zone: FVGZone) -> dict:
        return {
            "symbol": zone.symbol,
            "tf": zone.tf,
            "direction": zone.direction,
            "top": zone.top,
            "bottom": zone.bottom,
            "size": zone.size,
            "born_time": zone.born_time,
            "mitigation": zone.mitigation,
            "bull_strength": zone.bull_strength,
            "bear_strength": zone.bear_strength,
            "main_strength": zone.main_strength,
            "label": zone.label,
            "alerted": zone.alerted,
            "mitigated_alerted": zone.mitigated_alerted,
            "rsi": zone.rsi,
            "atr": zone.atr,
            "sl": zone.sl,
            "tp1": zone.tp1,
            "tp2": zone.tp2,
            "price": zone.price,
            "vol_change_pct": zone.vol_change_pct,
            "price_change_pct": zone.price_change_pct,
            "candle_body_pct": zone.candle_body_pct,
            "dist_to_zone": zone.dist_to_zone,
            "approach_alerted": zone.approach_alerted,
            "touch_alerted": zone.touch_alerted,
            "dominance_bias": zone.dominance_bias,
            "btc_trend": zone.btc_trend,
        }

    def _dict_to_zone(self, d: dict) -> FVGZone:
        return FVGZone(
            symbol=d["symbol"],
            tf=d["tf"],
            direction=d["direction"],
            top=d["top"],
            bottom=d["bottom"],
            size=d["size"],
            born_time=d["born_time"],
            mitigation=d.get("mitigation", 0.0),
            bull_strength=d.get("bull_strength", 0),
            bear_strength=d.get("bear_strength", 0),
            main_strength=d.get("main_strength", 0),
            label=d.get("label", ""),
            alerted=d.get("alerted", False),
            mitigated_alerted=d.get("mitigated_alerted", False),
            rsi=d.get("rsi", 50.0),
            atr=d.get("atr", 0.0),
            sl=d.get("sl", 0.0),
            tp1=d.get("tp1", 0.0),
            tp2=d.get("tp2", 0.0),
            price=d.get("price", 0.0),
            vol_change_pct=d.get("vol_change_pct", 0.0),
            price_change_pct=d.get("price_change_pct", 0.0),
            candle_body_pct=d.get("candle_body_pct", 0.0),
            dist_to_zone=d.get("dist_to_zone", 0.0),
            approach_alerted=d.get("approach_alerted", False),
            touch_alerted=d.get("touch_alerted", False),
            dominance_bias=d.get("dominance_bias", 0.0),
            btc_trend=d.get("btc_trend", 0.0),
        )

    def _save_zones(self):
        try:
            os.makedirs(os.path.dirname(PERSIST_PATH), exist_ok=True)
            data = {zid: self._zone_to_dict(z) for zid, z in self.zones.items()}
            with open(PERSIST_PATH, "w") as f:
                json.dump(data, f)
        except Exception as e:
            logger.warning("Zone save failed: %s", e)

    def _load_zones(self):
        if not os.path.exists(PERSIST_PATH):
            return
        try:
            with open(PERSIST_PATH, "r") as f:
                raw = json.load(f)
            now_ms = int(time.time() * 1000)
            loaded = 0
            dropped = 0
            for zid, d in raw.items():
                # Drop stale zones (> 24h old)
                if now_ms - d.get("born_time", 0) > ZONE_TTL_MS:
                    dropped += 1
                    continue
                # Drop weak zones (should not happen, but safety)
                if d.get("main_strength", 0) < MIN_STRENGTH_TO_ALERT:
                    dropped += 1
                    continue
                zone = self._dict_to_zone(d)
                self.zones[zid] = zone
                loaded += 1
            logger.info("Zones loaded: %d loaded, %d dropped (stale/weak)", loaded, dropped)
        except Exception as e:
            logger.warning("Zone load failed: %s", e)

    def update_buffer(self, symbol: str, tf: str, bars: List):
        self.buffers[(symbol, tf)] = bars

    def check_new_fvg(self, symbol: str, tf: str) -> Optional[FVGZone]:
        key = (symbol, tf)
        bars = self.buffers.get(key)
        if not bars or len(bars) < 3:
            return None

        last_time = bars[-1].open_time
        if self.last_bar_time.get(key) == last_time:
            return None  # already processed this bar

        # Warm-up: only set timestamp, skip detection on first ever bar
        if key not in self.last_bar_time:
            self.last_bar_time[key] = last_time
            return None

        self.last_bar_time[key] = last_time

        fvg = detect_fvg(bars, symbol=symbol)
        if not fvg:
            return None

        fvg["symbol"] = symbol
        strength = calc_strength(bars, fvg, symbol=symbol)
        if strength["main_strength"] < MIN_STRENGTH_TO_ALERT:
            return None

        zone = FVGZone(
            symbol=symbol,
            tf=tf,
            direction=fvg["direction"],
            top=fvg["top"],
            bottom=fvg["bottom"],
            size=fvg["size"],
            born_time=fvg["born_time"],
            main_strength=strength["main_strength"],
            bull_strength=strength["bull_strength"],
            bear_strength=strength["bear_strength"],
            label=strength["label"],
            rsi=strength["rsi"],
            atr=strength["atr"],
            sl=strength["sl"],
            tp1=strength["tp1"],
            tp2=strength["tp2"],
            price=strength["price"],
            vol_change_pct=strength["vol_change_pct"],
            price_change_pct=strength["price_change_pct"],
            candle_body_pct=strength["candle_body_pct"],
            dist_to_zone=strength["dist_to_zone"],
            dominance_bias=strength["dominance_bias"],
            btc_trend=strength["btc_trend"],
        )

        zone_id = f"{symbol}_{tf}_{zone.born_time}_{zone.direction}"
        self.zones[zone_id] = zone
        self._save_zones()
        logger.info("New FVG %s %s %s | strength=%d", symbol, tf, "BULL" if zone.direction == 1 else "BEAR", zone.main_strength)
        return zone

    def check_mitigation(self, symbol: str, tf: str, bars: List) -> List[FVGZone]:
        """Returns zones that became fully mitigated on this bar."""
        if not bars:
            return []
        curr = bars[-1]
        mitigated = []
        to_remove = []

        for zid, zone in list(self.zones.items()):
            if zone.symbol != symbol or zone.tf != tf:
                continue

            touched = False
            if zone.direction == 1:
                if curr.low <= zone.top:
                    touched = True
                    fill_dist = zone.top - curr.low
            else:
                if curr.high >= zone.bottom:
                    touched = True
                    fill_dist = curr.high - zone.bottom

            if touched:
                zone_size = max(zone.top - zone.bottom, 0.01)
                zone.mitigation = min(max(fill_dist / zone_size, 0), 1)

            if zone.mitigation >= 1.0 and not zone.mitigated_alerted:
                zone.mitigated_alerted = True
                mitigated.append(zone)
                to_remove.append(zid)
                logger.info("Mitigated %s %s", symbol, tf)

        for zid in to_remove:
            del self.zones[zid]

        if to_remove:
            self._save_zones()

        return mitigated

    def check_interaction(self, symbol: str, tf: str, bars: List) -> List[Dict]:
        """
        Check approaching and touch on strong FVG zones (strength >= 70).
        Returns list of dicts: {"type": "approaching"|"touch", "zone": FVGZone}
        """
        if not bars:
            return []
        curr = bars[-1]
        events = []

        # Calculate current ATR for approach distance
        closes = [b.close for b in bars]
        highs = [b.high for b in bars]
        lows = [b.low for b in bars]
        current_atr = atr(highs, lows, closes, ATR_LEN)

        for zid, zone in list(self.zones.items()):
            if zone.symbol != symbol or zone.tf != tf:
                continue
            # Only alert on strong imbalance zones
            if "Strong" not in zone.label:
                continue

            approach_dist = current_atr * 0.5 if current_atr else zone.size * 0.5

            if zone.direction == 1:
                # Bull zone: price approaching from above
                price_above = curr.close
                dist_to_top = price_above - zone.top
                dist_to_bottom = price_above - zone.bottom

                # Approaching: within approach_dist of zone top, but not yet touched
                if not zone.approach_alerted and dist_to_top > 0 and dist_to_top <= approach_dist:
                    zone.approach_alerted = True
                    events.append({"type": "approaching", "zone": zone})
                    logger.info("Approaching bull zone %s %s | dist=%.2f", symbol, tf, dist_to_top)

                # Touch: low entered the zone (low <= top)
                if not zone.touch_alerted and curr.low <= zone.top:
                    zone.touch_alerted = True
                    events.append({"type": "touch", "zone": zone})
                    logger.info("Touch bull zone %s %s | low=%.2f zone_top=%.2f", symbol, tf, curr.low, zone.top)

            else:
                # Bear zone: price approaching from below
                price_below = curr.close
                dist_to_bottom = zone.bottom - price_below
                dist_to_top = zone.top - price_below

                # Approaching: within approach_dist of zone bottom, but not yet touched
                if not zone.approach_alerted and dist_to_bottom > 0 and dist_to_bottom <= approach_dist:
                    zone.approach_alerted = True
                    events.append({"type": "approaching", "zone": zone})
                    logger.info("Approaching bear zone %s %s | dist=%.2f", symbol, tf, dist_to_bottom)

                # Touch: high entered the zone (high >= bottom)
                if not zone.touch_alerted and curr.high >= zone.bottom:
                    zone.touch_alerted = True
                    events.append({"type": "touch", "zone": zone})
                    logger.info("Touch bear zone %s %s | high=%.2f zone_bottom=%.2f", symbol, tf, curr.high, zone.bottom)

        return events
