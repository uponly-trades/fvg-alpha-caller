import io
import logging
from typing import Dict, List, Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.gridspec import GridSpec
import numpy as np
import pandas as pd

from indicator_context import kdj_series, pivot_highs, pivot_lows, rsi_series, stochrsi_series

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
    predicted_bars: Optional[List[Dict]] = None,
) -> Optional[bytes]:
    """Generate candlestick chart with FVG zone, EMAs, RSI7, KDJ, StochRSI, and Kronos forecast."""
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

        stoch_tfs = ("15m", "30m", "1h", "2h", "4h")
        tf_colors = {"15m": "#00bfff", "30m": "#00e676", "1h": "#ff9800", "2h": "#e040fb", "4h": "#ff1744"}

        # Compute per-TF data (own x, not aligned to main)
        tf_data: Dict[str, dict] = {}
        for stf in stoch_tfs:
            tf_bars_list = timeframe_bars.get(stf, [])
            tf_closes = [float(b.close) for b in tf_bars_list]
            tf_highs = [float(b.high) for b in tf_bars_list]
            tf_lows = [float(b.low) for b in tf_bars_list]
            n = len(tf_closes)
            k_vals, d_vals = stochrsi_series(tf_closes)
            rsi7_tf = rsi_series(tf_closes, 7) if n >= 8 else [50.0] * n
            tf_data[stf] = {
                "k": _align_series(k_vals, n),
                "d": _align_series(d_vals, n),
                "rsi7": rsi7_tf if len(rsi7_tf) == n else [50.0] * n,
                "highs": tf_highs,
                "lows": tf_lows,
                "n": n,
            }

        zone_color = "#1AD8C2" if zone_direction == 1 else "#D81A66"

        # ── Layout: 5 rows × 5 cols ────────────────────────────────────────
        # Row 0: Candle (full width)
        # Row 1: KDJ   (full width)
        # Row 2: RSI7 per TF (5 cols)
        # Row 3: StochRSI+MaStochRSI per TF (5 cols)
        ncols = len(stoch_tfs)
        _BG = "#131722"
        plt.rcParams.update({
            "figure.facecolor": _BG, "axes.facecolor": _BG,
            "axes.edgecolor": "#444", "text.color": "white",
            "xtick.color": "white", "ytick.color": "white",
            "axes.labelcolor": "white",
        })
        fig = plt.figure(figsize=(14, 12), facecolor=_BG)
        gs = GridSpec(
            4, ncols,
            figure=fig,
            height_ratios=[4, 1.1, 1.0, 1.2],
            hspace=0.10,
            wspace=0.12,
        )

        ax_main = fig.add_subplot(gs[0, :])
        ax_kdj  = fig.add_subplot(gs[1, :], sharex=ax_main)
        rsi_axes   = [fig.add_subplot(gs[2, i]) for i in range(ncols)]
        stoch_axes = [fig.add_subplot(gs[3, i]) for i in range(ncols)]

        # Apply dark bg to every axes explicitly (rcParams alone not enough after add_subplot)
        for _ax in [ax_main, ax_kdj] + rsi_axes + stoch_axes:
            _ax.set_facecolor(_BG)
            _ax.spines[:].set_color("#444")
            _ax.tick_params(colors="white", labelsize=6)

        # ── Candles (manual — mplfinance clobbers GridSpec) ───────────────
        x = np.arange(len(df))
        c_open  = df["Open"].values
        c_close = df["Close"].values
        c_high  = df["High"].values
        c_low   = df["Low"].values
        up, down = c_close >= c_open, c_close < c_open
        w = 0.4
        for idx in x[up]:
            ax_main.bar(idx, c_close[idx]-c_open[idx], w, bottom=c_open[idx], color="#26a69a", edgecolor="#26a69a")
            ax_main.vlines(idx, c_low[idx], c_open[idx],   color="#26a69a", linewidth=0.8)
            ax_main.vlines(idx, c_close[idx], c_high[idx], color="#26a69a", linewidth=0.8)
        for idx in x[down]:
            ax_main.bar(idx, c_open[idx]-c_close[idx], w, bottom=c_close[idx], color="#ef5350", edgecolor="#ef5350")
            ax_main.vlines(idx, c_low[idx], c_close[idx],  color="#ef5350", linewidth=0.8)
            ax_main.vlines(idx, c_open[idx], c_high[idx],  color="#ef5350", linewidth=0.8)

        title_str = f"{symbol}  {tf}  |  RSI7: {rsi_value:.1f}" if rsi_value else f"{symbol}  {tf}"
        ax_main.set_title(title_str, fontsize=11, fontweight="bold", color="white")
        ax_main.set_ylabel("Price", color="white")
        ax_main.tick_params(colors="white")

        ax_main.plot(x, ema20, color="orange", linewidth=0.9, label="EMA20")
        ax_main.plot(x, ema50, color="#5599ff", linewidth=0.9, label="EMA50")
        ax_main.legend(loc="upper left", fontsize=7, framealpha=0.4,
                       facecolor="#1e2330", edgecolor="#444", labelcolor="white")

        xlim = ax_main.get_xlim()
        ax_main.add_patch(mpatches.Rectangle(
            (xlim[0], zone_bottom), xlim[1] - xlim[0], zone_top - zone_bottom,
            facecolor=zone_color, alpha=0.15, edgecolor=zone_color, linewidth=1.5, linestyle="--",
        ))

        if trade_plan is not None:
            x_text = xlim[0] + (xlim[1] - xlim[0]) * 0.02
            for label, price, color in [
                ("Entry", float(trade_plan.entry), "#1f77b4"),
                ("SL",    float(trade_plan.sl),    "#d62728"),
                ("TP1",   float(trade_plan.tp1),   "#2ca02c"),
                ("TP2",   float(trade_plan.tp2),   "#006400"),
            ]:
                ax_main.axhline(y=price, color=color, linestyle="-", linewidth=1.2, alpha=0.9)
                ax_main.text(x_text, price, f" {label} {price:g} ",
                             color="white", fontsize=8, va="center",
                             bbox={"facecolor": color, "alpha": 0.85, "edgecolor": color})

        # ── Kronos forecast ghost candles ─────────────────────────────────
        if predicted_bars:
            px_start = len(df)  # first forecast x index
            p_dir = "up"
            if trade_plan is not None:
                p_dir = "up" if trade_plan.direction == "long" else "down"
            elif predicted_bars[-1]["close"] >= predicted_bars[0]["open"]:
                p_dir = "up"
            else:
                p_dir = "down"

            fc_bull = "#26a69a44"  # transparent green
            fc_bear = "#ef535044"  # transparent red
            ec_bull = "#26a69a99"
            ec_bear = "#ef535099"

            for pi, pb in enumerate(predicted_bars):
                px = px_start + pi
                po, pc_ = pb["open"], pb["close"]
                ph_, pl_ = pb["high"], pb["low"]
                is_up = pc_ >= po
                fc = fc_bull if is_up else fc_bear
                ec = ec_bull if is_up else ec_bear
                ax_main.bar(px, abs(pc_ - po), w, bottom=min(po, pc_), color=fc, edgecolor=ec, linewidth=0.6)
                ax_main.vlines(px, pl_, min(po, pc_), color=ec, linewidth=0.6)
                ax_main.vlines(px, max(po, pc_), ph_, color=ec, linewidth=0.6)

            # Connect last real close to first forecast open with dashed line
            ax_main.plot(
                [len(df) - 1, px_start],
                [float(df["Close"].iloc[-1]), predicted_bars[0]["open"]],
                color="#ffffff44", linewidth=0.8, linestyle="--",
            )
            # Label
            ax_main.text(
                px_start + len(predicted_bars) / 2, predicted_bars[-1]["close"],
                "  Kronos →", color="#aaaaaa", fontsize=7, va="bottom",
            )

        ax_main.tick_params(labelbottom=False, colors="white")

        # ── KDJ ───────────────────────────────────────────────────────────
        ax_kdj.plot(x, kdj_k, color="#5599ff", linewidth=0.8, label="K")
        ax_kdj.plot(x, kdj_d, color="orange",  linewidth=0.8, label="D")
        ax_kdj.plot(x, kdj_j, color="#26a69a", linewidth=0.8, label="J")
        for lvl, c in [(80, "red"), (20, "green"), (50, "gray")]:
            ax_kdj.axhline(y=lvl, color=c, linestyle="--", linewidth=0.6, alpha=0.5)
        ax_kdj.set_ylabel("KDJ", fontsize=8, color="white")
        ax_kdj.set_ylim(-10, 110)
        ax_kdj.legend(loc="upper left", fontsize=6, framealpha=0.4,
                      facecolor="#1e2330", edgecolor="#444", labelcolor="white")
        ax_kdj.tick_params(labelbottom=False, colors="white")

        # ── RSI7 per TF (row 2) ───────────────────────────────────────────
        for i, stf in enumerate(stoch_tfs):
            ax = rsi_axes[i]
            color = tf_colors[stf]
            d = tf_data[stf]
            xs = range(d["n"])
            ax.plot(xs, d["rsi7"], color=color, linewidth=0.9)
            _draw_divergence(ax, d["highs"], d["lows"], d["rsi7"])
            for lvl, c in [(70, "red"), (30, "green"), (50, "gray")]:
                ax.axhline(y=lvl, color=c, linestyle="--", linewidth=0.6, alpha=0.5)
            ax.set_ylim(0, 100)
            ax.set_title(stf, fontsize=8, color=color, fontweight="bold", pad=2)
            if i == 0:
                ax.set_ylabel("RSI7", fontsize=8, color="white")
            else:
                ax.tick_params(labelleft=False)
            ax.tick_params(labelbottom=False, colors="white", labelsize=6)

        # ── StochRSI + MaStochRSI per TF (row 3) ─────────────────────────
        for i, stf in enumerate(stoch_tfs):
            ax = stoch_axes[i]
            color = tf_colors[stf]
            d = tf_data[stf]
            xs = range(d["n"])
            k_last = d["k"][-1] if d["k"] else 0.0
            ma_last = d["d"][-1] if d["d"] else 0.0
            ax.plot(xs, d["k"], color="#f0c040",  linewidth=0.9, label="StochRSI")
            ax.plot(xs, d["d"], color="#8888ff",  linewidth=0.9, label="MaStochRSI")
            for lvl, c in [(80, "red"), (20, "green"), (50, "gray")]:
                ax.axhline(y=lvl, color=c, linestyle="--", linewidth=0.6, alpha=0.5)
            ax.set_ylim(0, 100)
            # label with current values
            ax.set_title(
                f"{stf}  K:{k_last:.1f} MA:{ma_last:.1f}",
                fontsize=7, color=color, fontweight="bold", pad=2,
            )
            if i == 0:
                ax.set_ylabel("sRSI", fontsize=8, color="white")
            else:
                ax.tick_params(labelleft=False)
            ax.tick_params(labelbottom=False, colors="white", labelsize=6)

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=120, bbox_inches="tight", facecolor=_BG)
        plt.close(fig)
        buf.seek(0)
        return buf.read()
    except Exception as e:
        logger.error("Chart generation failed: %s", e)
        return None
