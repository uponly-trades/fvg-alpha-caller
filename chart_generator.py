import io
import logging
from typing import List, Optional, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import mplfinance as mpf
import numpy as np
import pandas as pd

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


def generate_chart(
    bars,
    zone_top: float,
    zone_bottom: float,
    zone_direction: int,
    symbol: str,
    tf: str,
    rsi_value: Optional[float] = None,
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
        df.index = pd.date_range(end=pd.Timestamp.now(), periods=len(df), freq="min")

        closes = df["Close"].tolist()
        ema20 = _calc_ema(closes, 20)
        ema50 = _calc_ema(closes, 50)
        rsi = _calc_rsi(closes, 14)

        df["EMA20"] = ema20
        df["EMA50"] = ema50
        df["RSI"] = rsi

        # Color for FVG zone
        zone_color = "#1AD8C2" if zone_direction == 1 else "#D81A66"
        zone_alpha = 0.15

        # Build addplot
        apds = [
            mpf.make_addplot(df["EMA20"], color="orange", width=0.8, label="EMA20"),
            mpf.make_addplot(df["EMA50"], color="blue", width=0.8, label="EMA50"),
            mpf.make_addplot(df["RSI"], panel=1, color="purple", width=0.8, ylabel="RSI"),
        ]

        # RSI overbought/oversold lines
        rsi_panel = len(apds) - 1  # last addplot index

        fig, axes = mpf.plot(
            df,
            type="candle",
            style="charles",
            title=f"{symbol}  {tf}  |  RSI: {rsi_value:.1f}" if rsi_value else f"{symbol}  {tf}",
            ylabel="Price",
            volume=False,
            addplot=apds,
            panel_ratios=(3, 1),
            returnfig=True,
            figsize=(10, 7),
        )

        ax_main = axes[0]
        ax_rsi = axes[2]

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

        # RSI horizontal lines
        ax_rsi.axhline(y=70, color="red", linestyle="--", linewidth=0.8, alpha=0.7)
        ax_rsi.axhline(y=30, color="green", linestyle="--", linewidth=0.8, alpha=0.7)
        ax_rsi.axhline(y=50, color="gray", linestyle="-", linewidth=0.5, alpha=0.5)

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=120, bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        return buf.read()
    except Exception as e:
        logger.error("Chart generation failed: %s", e)
        return None
