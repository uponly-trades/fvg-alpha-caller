"""
Pure feature extraction for ML training. NO side effects on trading logic.

Computes per-TF indicator snapshot at decision time:
  EMA20/50/200, StochRSI K/D, KDJ, RSI7, ATR, MACD, BB position, vol z-score,
  long/short ratio. Plus BTC regime context.

Returns plain dicts so caller (sim_trades) can JSONB-store them.
"""
from __future__ import annotations
import logging
from typing import Dict, List, Optional

import numpy as np

from indicator_context import (
    rsi_series,
    stochrsi_series,
    kdj_series,
    fetch_long_short_ratio,
    fetch_oi_change_pct,
)

logger = logging.getLogger(__name__)


def _last(values: List) -> Optional[float]:
    if not values:
        return None
    v = values[-1]
    if v is None:
        return None
    try:
        f = float(v)
        if np.isnan(f):
            return None
        return round(f, 6)
    except Exception:
        return None


def ema_series(values: List[float], length: int) -> List[Optional[float]]:
    if len(values) < length:
        return [None] * len(values)
    out: List[Optional[float]] = [None] * (length - 1)
    sma = sum(values[:length]) / length
    out.append(sma)
    k = 2.0 / (length + 1)
    prev = sma
    for v in values[length:]:
        prev = v * k + prev * (1 - k)
        out.append(prev)
    return out


def macd(closes: List[float]) -> Dict[str, Optional[float]]:
    if len(closes) < 35:
        return {"macd": None, "signal": None, "hist": None}
    ema12 = ema_series(closes, 12)
    ema26 = ema_series(closes, 26)
    macd_line = [
        (a - b) if a is not None and b is not None else None
        for a, b in zip(ema12, ema26)
    ]
    valid = [v for v in macd_line if v is not None]
    if len(valid) < 9:
        return {"macd": _last(macd_line), "signal": None, "hist": None}
    sig = ema_series(valid, 9)
    sig_last = _last(sig)
    macd_last = _last(macd_line)
    hist = (macd_last - sig_last) if (macd_last is not None and sig_last is not None) else None
    return {"macd": macd_last, "signal": sig_last, "hist": round(hist, 6) if hist is not None else None}


def bollinger_position(closes: List[float], length: int = 20, mult: float = 2.0) -> Optional[float]:
    """Returns price position in BB band: 0=lower, 0.5=mid, 1=upper. None if insufficient."""
    if len(closes) < length:
        return None
    window = closes[-length:]
    mid = sum(window) / length
    sd = float(np.std(window))
    if sd <= 0:
        return 0.5
    upper = mid + mult * sd
    lower = mid - mult * sd
    last = closes[-1]
    pos = (last - lower) / (upper - lower) if upper != lower else 0.5
    return round(float(pos), 4)


def atr_value(highs: List[float], lows: List[float], closes: List[float], length: int = 14) -> Optional[float]:
    if len(closes) < length + 1:
        return None
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    if len(trs) < length:
        return None
    atr = sum(trs[:length]) / length
    for tr in trs[length:]:
        atr = (atr * (length - 1) + tr) / length
    return round(float(atr), 8)


def volume_zscore(volumes: List[float], length: int = 20) -> Optional[float]:
    if len(volumes) < length + 1:
        return None
    window = volumes[-(length + 1):-1]
    mean = sum(window) / length
    sd = float(np.std(window))
    if sd <= 0:
        return None
    z = (volumes[-1] - mean) / sd
    return round(float(z), 4)


def extract_tf_features(bars, tf: str, symbol: str = "", with_ls_ratio: bool = False) -> Dict:
    """Extract feature vector for one TF. `bars` is list of Bar dataclasses with OHLCV."""
    if not bars or len(bars) < 30:
        return {"insufficient": True, "n_bars": len(bars) if bars else 0}

    closes = [float(b.close) for b in bars]
    highs = [float(b.high) for b in bars]
    lows = [float(b.low) for b in bars]
    vols = [float(b.volume) for b in bars]

    stoch_k, stoch_d = stochrsi_series(closes)
    kdj_k, kdj_d, kdj_j = kdj_series(highs, lows, closes)
    rsi7 = rsi_series(closes, 7)
    rsi14 = rsi_series(closes, 14)
    ema20 = ema_series(closes, 20)
    ema50 = ema_series(closes, 50) if len(closes) >= 50 else [None] * len(closes)
    ema200 = ema_series(closes, 200) if len(closes) >= 200 else [None] * len(closes)
    macd_d = macd(closes)
    bb_pos = bollinger_position(closes)
    atr = atr_value(highs, lows, closes)
    vol_z = volume_zscore(vols)
    vol_change_pct = None
    if len(vols) >= 2 and vols[-2] > 0:
        vol_change_pct = round((vols[-1] - vols[-2]) / vols[-2] * 100, 2)

    last_close = closes[-1]
    e20 = _last(ema20)
    e50 = _last(ema50)
    e200 = _last(ema200)

    feat = {
        "tf": tf,
        "n_bars": len(bars),
        "close": round(last_close, 8),
        "rsi7": _last(rsi7),
        "rsi14": _last(rsi14),
        "stoch_k": _last(stoch_k),
        "stoch_d": _last(stoch_d),
        "kdj_k": _last(kdj_k),
        "kdj_d": _last(kdj_d),
        "kdj_j": _last(kdj_j),
        "ema20": e20,
        "ema50": e50,
        "ema200": e200,
        "ema20_dist_pct": round((last_close - e20) / e20 * 100, 4) if e20 else None,
        "ema50_dist_pct": round((last_close - e50) / e50 * 100, 4) if e50 else None,
        "ema200_dist_pct": round((last_close - e200) / e200 * 100, 4) if e200 else None,
        "ema_stack": _ema_stack(e20, e50, e200),
        "atr": atr,
        "atr_pct": round(atr / last_close * 100, 4) if atr else None,
        "macd": macd_d["macd"],
        "macd_signal": macd_d["signal"],
        "macd_hist": macd_d["hist"],
        "bb_pos": bb_pos,
        "vol_z": vol_z,
        "vol_change_pct": vol_change_pct,
    }

    if with_ls_ratio and symbol:
        ls = fetch_long_short_ratio(symbol, tf)
        if ls:
            feat["long_pct"] = ls[0]
            feat["short_pct"] = ls[1]
        oi = fetch_oi_change_pct(symbol, tf)
        if oi is not None:
            feat["oi_change_pct"] = oi

    return feat


def _ema_stack(e20, e50, e200) -> Optional[str]:
    """Return 'bull' if e20>e50>e200, 'bear' if reversed, else 'mixed'."""
    if e20 is None or e50 is None or e200 is None:
        return None
    if e20 > e50 > e200:
        return "bull"
    if e20 < e50 < e200:
        return "bear"
    return "mixed"


def extract_multi_tf(bars_by_tf: Dict[str, list], symbol: str, with_ls_ratio: bool = False) -> Dict:
    """Snapshot all TFs into one feature dict, keyed by tf."""
    out = {}
    for tf, bars in bars_by_tf.items():
        out[tf] = extract_tf_features(bars, tf, symbol=symbol, with_ls_ratio=with_ls_ratio)
    return out


def btc_regime(btc_bars_by_tf: Dict[str, list]) -> Dict:
    """Snapshot BTC trend. Caller passes BTCUSDT bars per TF."""
    bars_1h = btc_bars_by_tf.get("1h") or []
    if len(bars_1h) < 50:
        return {"insufficient": True}
    closes = [float(b.close) for b in bars_1h]
    e20 = _last(ema_series(closes, 20))
    e50 = _last(ema_series(closes, 50))
    last = closes[-1]
    rsi14 = _last(rsi_series(closes, 14))
    trend = "neutral"
    if e20 and e50:
        if last > e20 > e50:
            trend = "bull"
        elif last < e20 < e50:
            trend = "bear"
    return {
        "btc_close": round(last, 2),
        "btc_ema20": e20,
        "btc_ema50": e50,
        "btc_rsi14": rsi14,
        "btc_trend": trend,
    }
