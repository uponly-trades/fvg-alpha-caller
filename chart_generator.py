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
    trade_plan=None,
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

        # StochRSI per TF — all overlaid in one panel
        stoch_tfs = ("15m", "30m", "1h", "2h", "4h")
        stoch_colors = {"15m": "#00bfff", "30m": "#00e676", "1h": "#ff9800", "2h": "#e040fb", "4h": "#ff1744"}
        for stoch_tf in stoch_tfs:
            tf_bars = timeframe_bars.get(stoch_tf, [])
            tf_closes = [float(b.close) for b in tf_bars]
            stoch_k, stoch_d = stochrsi_series(tf_closes)
            df[f"StochRSI_{stoch_tf}"] = _align_series_to_index(stoch_k, tf_bars, df.index) if tf_bars else _align_series(stoch_k, len(df))

        # Color for FVG zone
        zone_color = "#1AD8C2" if zone_direction == 1 else "#D81A66"
        zone_alpha = 0.15

        # Build addplot — StochRSI all in panel 1, RSI7 panel 2, KDJ panel 3
        first_stoch = True
        apds = [
            mpf.make_addplot(df["EMA20"], color="orange", width=0.8, label="EMA20"),
            mpf.make_addplot(df["EMA50"], color="blue", width=0.8, label="EMA50"),
        ]
        for stoch_tf in stoch_tfs:
            kwargs = dict(panel=1, color=stoch_colors[stoch_tf], width=0.9, alpha=0.85)
            if first_stoch:
                kwargs["ylabel"] = "sRSI"
                first_stoch = False
            apds.append(mpf.make_addplot(df[f"StochRSI_{stoch_tf}"], **kwargs))

        apds += [
            mpf.make_addplot(df["RSI7"], panel=2, color="purple", width=0.8, ylabel="RSI7"),
            mpf.make_addplot(df["KDJ_K"], panel=3, color="blue", width=0.8, ylabel="KDJ"),
            mpf.make_addplot(df["KDJ_D"], panel=3, color="orange", width=0.8),
            mpf.make_addplot(df["KDJ_J"], panel=3, color="green", width=0.8),
        ]

        fig, axes = mpf.plot(
            df,
            type="candle",
            style="charles",
            title=f"{symbol}  {tf}  |  RSI: {rsi_value:.1f}" if rsi_value else f"{symbol}  {tf}",
            ylabel="Price",
            volume=False,
            addplot=apds,
            panel_ratios=(4, 1.2, 1, 1),
            returnfig=True,
            figsize=(10, 10),
        )

        ax_main = axes[0]
        ax_stoch = axes[2]
        ax_rsi = axes[4]
        ax_kdj = axes[6]

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

        if trade_plan is not None:
            overlay_levels = [
                ("Entry", float(trade_plan.entry), "#1f77b4"),
                ("SL", float(trade_plan.sl), "#d62728"),
                ("TP1", float(trade_plan.tp1), "#2ca02c"),
                ("TP2", float(trade_plan.tp2), "#006400"),
            ]
            x_text = xlim[0] + (xlim[1] - xlim[0]) * 0.02
            for label, price, color in overlay_levels:
                ax_main.axhline(y=price, color=color, linestyle="-", linewidth=1.2, alpha=0.9)
                ax_main.text(
                    x_text,
                    price,
                    f" {label} {price:g} ",
                    color="white",
                    fontsize=8,
                    va="center",
                    bbox={"facecolor": color, "alpha": 0.85, "edgecolor": color},
                )

        # Indicator horizontal lines
        for ax in (ax_stoch, ax_rsi, ax_kdj):
            ax.axhline(y=80, color="red", linestyle="--", linewidth=0.7, alpha=0.5)
            ax.axhline(y=20, color="green", linestyle="--", linewidth=0.7, alpha=0.5)
            ax.axhline(y=50, color="gray", linestyle="-", linewidth=0.5, alpha=0.4)
        ax_rsi.axhline(y=70, color="red", linestyle="--", linewidth=0.8, alpha=0.7)
        ax_rsi.axhline(y=30, color="green", linestyle="--", linewidth=0.8, alpha=0.7)

        # Legend untuk StochRSI panel
        legend_patches = [
            mpatches.Patch(color=stoch_colors[stf], label=stf)
            for stf in stoch_tfs
        ]
        ax_stoch.legend(handles=legend_patches, loc="upper left", fontsize=6, ncol=5, framealpha=0.5)

        _draw_divergence(ax_rsi, highs, lows, rsi7)

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=120, bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        return buf.read()
    except Exception as e:
        logger.error("Chart generation failed: %s", e)
        return None
