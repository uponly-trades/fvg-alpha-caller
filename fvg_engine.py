import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from config import (
    ATR_LEN, CANDLE_WEIGHT, GAP_WEIGHT, MIN_STRENGTH_TO_ALERT,
    TREND_EMA_LEN, TREND_WEIGHT, VOL_MA_LEN, VOL_WEIGHT,
)

logger = logging.getLogger(__name__)


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


def detect_fvg(bars: List) -> Optional[Dict]:
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


def calc_strength(bars: List, fvg: Dict) -> Dict:
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

    total = gap_strength + vol_strength + trend_strength + candle_strength
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
        "label": label,
        "rsi": round(rsi_val, 1),
        "atr": round(atr_val, 4),
        "sl": round(sl, 4),
        "tp1": round(tp1, 4),
        "tp2": round(tp2, 4),
        "price": round(curr.close, 4),
    }


class FVGTracker:
    def __init__(self):
        # key: (symbol, tf)  -> list of Bar
        self.buffers: Dict[tuple, List] = {}
        # key: zone_id -> FVGZone
        self.zones: Dict[str, FVGZone] = {}
        # key: (symbol, tf) -> last processed bar open_time
        self.last_bar_time: Dict[tuple, int] = {}

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

        fvg = detect_fvg(bars)
        if not fvg:
            return None

        strength = calc_strength(bars, fvg)
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
        )

        zone_id = f"{symbol}_{tf}_{zone.born_time}_{zone.direction}"
        self.zones[zone_id] = zone
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

        return mitigated
