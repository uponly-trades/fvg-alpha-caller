import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import requests

from config import BASE_URL, TIMEFRAMES

logger = logging.getLogger(__name__)
LS_CACHE_TTL_SEC = 60
_LS_CACHE: Dict[Tuple[str, str], Tuple[float, Optional[Tuple[float, float]]]] = {}


@dataclass(frozen=True)
class IndicatorContext:
    tf: str
    stoch_k: Optional[float] = None
    stoch_d: Optional[float] = None
    stoch_state: str = "neutral"
    rsi7: Optional[float] = None
    kdj_k: Optional[float] = None
    kdj_d: Optional[float] = None
    kdj_j: Optional[float] = None
    kdj_state: str = "neutral"
    long_pct: Optional[float] = None
    short_pct: Optional[float] = None


def rsi_series(closes: List[float], length: int) -> List[Optional[float]]:
    if len(closes) < length + 1:
        return [None] * len(closes)
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = float(np.mean(gains[:length]))
    avg_loss = float(np.mean(losses[:length]))
    values: List[Optional[float]] = [None] * length
    for i in range(length, len(deltas)):
        avg_gain = (avg_gain * (length - 1) + float(gains[i])) / length
        avg_loss = (avg_loss * (length - 1) + float(losses[i])) / length
        if avg_loss == 0:
            values.append(100.0)
        else:
            rs = avg_gain / avg_loss
            values.append(100 - (100 / (1 + rs)))
    return [None] * (len(closes) - len(values)) + values


def sma_series(values: List[Optional[float]], length: int) -> List[Optional[float]]:
    result: List[Optional[float]] = []
    for i in range(len(values)):
        window = values[max(0, i - length + 1):i + 1]
        clean = [v for v in window if v is not None]
        if len(clean) < length:
            result.append(None)
        else:
            result.append(sum(clean) / length)
    return result


def stochrsi_series(closes: List[float], rsi_len: int = 14, stoch_len: int = 14, k_len: int = 3, d_len: int = 3) -> Tuple[List[Optional[float]], List[Optional[float]]]:
    rsis = rsi_series(closes, rsi_len)
    raw: List[Optional[float]] = []
    for i in range(len(rsis)):
        window = [v for v in rsis[max(0, i - stoch_len + 1):i + 1] if v is not None]
        if len(window) < stoch_len:
            raw.append(None)
            continue
        lo = min(window)
        hi = max(window)
        if hi == lo:
            raw.append(0.0)
        else:
            raw.append((rsis[i] - lo) / (hi - lo) * 100 if rsis[i] is not None else None)
    k = sma_series(raw, k_len)
    d = sma_series(k, d_len)
    return k, d


def kdj_series(highs: List[float], lows: List[float], closes: List[float], length: int = 9) -> Tuple[List[Optional[float]], List[Optional[float]], List[Optional[float]]]:
    k_values: List[Optional[float]] = []
    d_values: List[Optional[float]] = []
    j_values: List[Optional[float]] = []
    k_prev = 50.0
    d_prev = 50.0
    for i in range(len(closes)):
        if i + 1 < length:
            k_values.append(None)
            d_values.append(None)
            j_values.append(None)
            continue
        hh = max(highs[i - length + 1:i + 1])
        ll = min(lows[i - length + 1:i + 1])
        rsv = 50.0 if hh == ll else (closes[i] - ll) / (hh - ll) * 100
        k_prev = (2 / 3) * k_prev + (1 / 3) * rsv
        d_prev = (2 / 3) * d_prev + (1 / 3) * k_prev
        j = 3 * k_prev - 2 * d_prev
        k_values.append(k_prev)
        d_values.append(d_prev)
        j_values.append(j)
    return k_values, d_values, j_values


def cross_state(k_values: List[Optional[float]], d_values: List[Optional[float]]) -> str:
    pairs = [(k, d) for k, d in zip(k_values, d_values) if k is not None and d is not None]
    if not pairs:
        return "neutral"
    current_k, current_d = pairs[-1]
    if len(pairs) >= 2:
        prev_k, prev_d = pairs[-2]
        if prev_k <= prev_d and current_k > current_d:
            return "bull_cross"
        if prev_k >= prev_d and current_k < current_d:
            return "bear_cross"
    if current_k > current_d:
        return "bull"
    if current_k < current_d:
        return "bear"
    return "neutral"


def fetch_long_short_ratio(symbol: str, tf: str) -> Optional[Tuple[float, float]]:
    key = (symbol, tf)
    now = time.time()
    cached = _LS_CACHE.get(key)
    if cached and now - cached[0] < LS_CACHE_TTL_SEC:
        return cached[1]
    try:
        resp = requests.get(
            f"{BASE_URL}/futures/data/topLongShortPositionRatio",
            params={"symbol": symbol, "period": tf, "limit": 1},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data:
            _LS_CACHE[key] = (now, None)
            return None
        latest = data[-1]
        value = (round(float(latest["longAccount"]) * 100, 2), round(float(latest["shortAccount"]) * 100, 2))
        _LS_CACHE[key] = (now, value)
        return value
    except Exception as e:
        logger.warning("Fetch long/short ratio failed %s %s: %s", symbol, tf, e)
        _LS_CACHE[key] = (now, None)
        return None


def calculate_indicator_context(tf: str, bars, ls_ratio: Optional[Tuple[float, float]]) -> IndicatorContext:
    long_pct = ls_ratio[0] if ls_ratio else None
    short_pct = ls_ratio[1] if ls_ratio else None
    if len(bars) < 15:
        return IndicatorContext(tf=tf, long_pct=long_pct, short_pct=short_pct)
    closes = [float(b.close) for b in bars]
    highs = [float(b.high) for b in bars]
    lows = [float(b.low) for b in bars]
    stoch_k, stoch_d = stochrsi_series(closes)
    rsi7 = rsi_series(closes, 7)
    kdj_k, kdj_d, kdj_j = kdj_series(highs, lows, closes)
    return IndicatorContext(
        tf=tf,
        stoch_k=round(stoch_k[-1], 1) if stoch_k[-1] is not None else None,
        stoch_d=round(stoch_d[-1], 1) if stoch_d[-1] is not None else None,
        stoch_state=cross_state(stoch_k, stoch_d),
        rsi7=round(rsi7[-1], 1) if rsi7[-1] is not None else None,
        kdj_k=round(kdj_k[-1], 1) if kdj_k[-1] is not None else None,
        kdj_d=round(kdj_d[-1], 1) if kdj_d[-1] is not None else None,
        kdj_j=round(kdj_j[-1], 1) if kdj_j[-1] is not None else None,
        kdj_state=cross_state(kdj_k, kdj_d),
        long_pct=long_pct,
        short_pct=short_pct,
    )


def _fmt(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value:.1f}"


def format_context_line(ctx: IndicatorContext) -> str:
    if ctx.stoch_k is None and ctx.rsi7 is None and ctx.kdj_k is None:
        return f"{ctx.tf:<3}: n/a"
    ls = "LS n/a"
    if ctx.long_pct is not None and ctx.short_pct is not None:
        ls = f"LS L{ctx.long_pct:.1f}/S{ctx.short_pct:.1f}"
    return (
        f"{ctx.tf:<3}: StochRSI {_fmt(ctx.stoch_k)}/{_fmt(ctx.stoch_d)} {ctx.stoch_state} | "
        f"RSI7 {_fmt(ctx.rsi7)} | "
        f"KDJ K{_fmt(ctx.kdj_k)} D{_fmt(ctx.kdj_d)} J{_fmt(ctx.kdj_j)} {ctx.kdj_state} | "
        f"{ls}"
    )


def format_indicator_context(symbol: str, buffers: dict) -> str:
    lines = ["📊 Indicator Context"]
    for tf in TIMEFRAMES:
        bars = buffers.get((symbol, tf), [])
        if not bars:
            lines.append(f"{tf:<3}: n/a")
            continue
        ctx = calculate_indicator_context(tf, bars, fetch_long_short_ratio(symbol, tf))
        lines.append(format_context_line(ctx))
    return "\n".join(lines)
