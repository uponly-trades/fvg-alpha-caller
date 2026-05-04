import io
import logging
from typing import Dict, List, Optional, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import mplfinance as mpf
import numpy as np
import pandas as pd

from indicator_context import divergence_state, kdj_series, pivot_highs, pivot_lows, rsi_series, stochrsi_series

logger = logging.getLogger(__name__)


def _calc_rsi(closes: List[float], length: int = 14) -> List[float]:
    if len(closes) < length + 1:
        return [50.0] * len(closes)
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains[:length])
    avg_loss = np.mean(losses[:length])
    rsis = [50.0] * (length + 1)
    for i in range(length, len(deltas)):
        avg_gain = (avg_gain * (length - 1) + gains[i]) / length
        avg_loss = (avg_loss * (length - 1) + losses[i]) / length
        if avg_loss == 0:
            rsis.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsis.append(100 - (100 / (1 + rs)))
    # Pad front
    return [50.0] * (len(closes) - len(rsis)) + rsis


def _calc_ema(values: List[float], length: int) -> List[float]:
    if len(values) < length:
        return values[:]
    k = 2 / (length + 1)
    ema = [sum(values[:length]) / length]
    for v in values[length:]:
        ema.append(v * k + ema[-1] * (1 - k))
    return [np.nan] * (length - 1) + ema


def _align_series(values: List[Optional[float]], length: int) -> List[float]:
    cleaned = [np.nan if v is None else v for v in values]
    if len(cleaned) >= length:
        aligned = cleaned[-length:]
    else:
        aligned = [np.nan] * (length - len(cleaned)) + cleaned
    if all(np.isnan(v) for v in aligned):
        return [50.0] * length
    return aligned


def _align_series_to_index(values: List[Optional[float]], bars, target_index) -> List[float]:
    if not bars:
        return [50.0] * len(target_index)
    source_index = pd.to_datetime([b.open_time for b in bars], unit="ms")
    source = pd.Series([np.nan if v is None else v for v in values], index=source_index).dropna()
    if source.empty:
        return [50.0] * len(target_index)
    aligned = source.reindex(source.index.union(target_index)).interpolate(method="time").reindex(target_index)
    aligned = aligned.ffill().bfill().tolist()
    return aligned


def _align_price(values: List[float], length: int) -> List[float]:
    if len(values) >= length:
        return values[-length:]
    return [np.nan] * (length - len(values)) + values


def _draw_divergence(ax, highs: List[float], lows: List[float], osc: List[Optional[float]], x_offset: int = 0):
    low_pivots = pivot_lows(osc)
    high_pivots = pivot_highs(osc)
    for prev, curr in zip(low_pivots, low_pivots[1:]):
        if 5 <= curr - prev <= 60 and lows[curr] < lows[prev] and osc[curr] is not None and osc[prev] is not None and osc[curr] > osc[prev]:
            ax.plot([prev + x_offset, curr + x_offset], [osc[prev], osc[curr]], color="cyan", linewidth=1.4)
    for prev, curr in zip(high_pivots, high_pivots[1:]):
        if 5 <= curr - prev <= 60 and highs[curr] > highs[prev] and osc[curr] is not None and osc[prev] is not None and osc[curr] < osc[prev]:
            ax.plot([prev + x_offset, curr + x_offset], [osc[prev], osc[curr]], color="red", linewidth=1.4)


def generate_chart(
    bars,
    zone_top: float,
    zone_bottom: float,
    zone_direction: int,
    symbol: str,
    tf: str,
    rsi_value: Optional[float] = None,
    timeframe_bars: Optional[Dict[str, List]] = None,
) -> Optional[bytes]:
    """Generate candlestick chart with FVG zone, EMAs, and RSI. Returns PNG bytes."""
    try:
        df = pd.DataFrame({
            "Open": [b.open for b in bars],
            "High": [b.high for b in bars],
            "Low": [b.low for b in bars],
            "Close": [b.close for b in bars],
            "Volume": [b.volume for b in bars],
        })
        df.index = pd.to_datetime([b.open_time for b in bars], unit="ms")

        closes = df["Close"].tolist()
        highs = df["High"].tolist()
        lows = df["Low"].tolist()
        ema20 = _calc_ema(closes, 20)
        ema50 = _calc_ema(closes, 50)
        rsi7 = rsi_series(closes, 7)
        kdj_k, kdj_d, kdj_j = kdj_series(highs, lows, closes)

        df["EMA20"] = ema20
        df["EMA50"] = ema50
        df["RSI7"] = rsi7
        df["KDJ_K"] = kdj_k
        df["KDJ_D"] = kdj_d
        df["KDJ_J"] = kdj_j

        timeframe_bars = timeframe_bars or {tf: bars}
        stoch_source = {}
        for stoch_tf in ("15m", "1h", "4h"):
            tf_bars = timeframe_bars.get(stoch_tf, [])
            tf_closes = [float(b.close) for b in tf_bars]
            stoch_k, stoch_d = stochrsi_series(tf_closes)
            df[f"StochRSI_{stoch_tf}"] = _align_series_to_index(stoch_k, tf_bars, df.index)
            df[f"MAStochRSI_{stoch_tf}"] = _align_series_to_index(stoch_d, tf_bars, df.index)
            stoch_source[stoch_tf] = (tf_bars, stoch_k)

        # Color for FVG zone
        zone_color = "#1AD8C2" if zone_direction == 1 else "#D81A66"
        zone_alpha = 0.15

        # Build addplot
        apds = [
            mpf.make_addplot(df["EMA20"], color="orange", width=0.8, label="EMA20"),
            mpf.make_addplot(df["EMA50"], color="blue", width=0.8, label="EMA50"),
            mpf.make_addplot(df["StochRSI_15m"], panel=1, color="teal", width=0.8, ylabel="sRSI 15m"),
            mpf.make_addplot(df["MAStochRSI_15m"], panel=1, color="magenta", width=0.8),
            mpf.make_addplot(df["StochRSI_1h"], panel=2, color="teal", width=0.8, ylabel="sRSI 1h"),
            mpf.make_addplot(df["MAStochRSI_1h"], panel=2, color="magenta", width=0.8),
            mpf.make_addplot(df["StochRSI_4h"], panel=3, color="teal", width=0.8, ylabel="sRSI 4h"),
            mpf.make_addplot(df["MAStochRSI_4h"], panel=3, color="magenta", width=0.8),
            mpf.make_addplot(df["RSI7"], panel=4, color="purple", width=0.8, ylabel="RSI7"),
            mpf.make_addplot(df["KDJ_K"], panel=5, color="blue", width=0.8, ylabel="KDJ"),
            mpf.make_addplot(df["KDJ_D"], panel=5, color="orange", width=0.8),
            mpf.make_addplot(df["KDJ_J"], panel=5, color="green", width=0.8),
        ]

        fig, axes = mpf.plot(
            df,
            type="candle",
            style="charles",
            title=f"{symbol}  {tf}  |  RSI: {rsi_value:.1f}" if rsi_value else f"{symbol}  {tf}",
            ylabel="Price",
            volume=False,
            addplot=apds,
            panel_ratios=(3, 1, 1, 1, 1, 1),
            returnfig=True,
            figsize=(10, 13),
        )

        ax_main = axes[0]
        ax_stoch_15m = axes[2]
        ax_stoch_1h = axes[4]
        ax_stoch_4h = axes[6]
        ax_rsi = axes[8]
        ax_kdj = axes[10]

        # Add FVG zone rectangle
        xlim = ax_main.get_xlim()
        rect = mpatches.Rectangle(
            (xlim[0], zone_bottom),
            xlim[1] - xlim[0],
            zone_top - zone_bottom,
            facecolor=zone_color,
            alpha=zone_alpha,
            edgecolor=zone_color,
            linewidth=2,
            linestyle="--",
        )
        ax_main.add_patch(rect)

        # Indicator horizontal lines
        for ax in (ax_stoch_15m, ax_stoch_1h, ax_stoch_4h, ax_rsi, ax_kdj):
            ax.axhline(y=80, color="red", linestyle="--", linewidth=0.7, alpha=0.5)
            ax.axhline(y=20, color="green", linestyle="--", linewidth=0.7, alpha=0.5)
            ax.axhline(y=50, color="gray", linestyle="-", linewidth=0.5, alpha=0.4)
        ax_rsi.axhline(y=70, color="red", linestyle="--", linewidth=0.8, alpha=0.7)
        ax_rsi.axhline(y=30, color="green", linestyle="--", linewidth=0.8, alpha=0.7)

        for stoch_tf, ax in (("15m", ax_stoch_15m), ("1h", ax_stoch_1h), ("4h", ax_stoch_4h)):
            tf_bars, stoch_k = stoch_source[stoch_tf]
            if len(tf_bars) >= 25:
                tf_highs = _align_series_to_index([float(b.high) for b in tf_bars], tf_bars, df.index)
                tf_lows = _align_series_to_index([float(b.low) for b in tf_bars], tf_bars, df.index)
                _draw_divergence(ax, tf_highs, tf_lows, _align_series_to_index(stoch_k, tf_bars, df.index))
        _draw_divergence(ax_rsi, highs, lows, rsi7)

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=120, bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        return buf.read()
    except Exception as e:
        logger.error("Chart generation failed: %s", e)
        return None
