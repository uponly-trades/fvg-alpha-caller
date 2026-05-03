# Indicator Context Alerts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add StochRSI, MAStochRSI, RSI(7), KDJ, and Binance top-trader long/short ratio context to FVG alerts for `15m`, `1h`, and `4h`.

**Architecture:** Add a focused `indicator_context.py` module for indicator calculations, formatting, and LS API caching. Attach preformatted indicator context to `FVGZone` before Telegram sends, and extend chart generation with StochRSI/RSI7/KDJ panels for the alert timeframe only.

**Tech Stack:** Python, pytest, requests, pandas/numpy, mplfinance/matplotlib, Binance Futures API, Coolify GitHub-connected deployment.

---

## Files and Systems

- Create: `indicator_context.py` — indicator formulas, cross labels, context formatting, LS ratio API cache.
- Create: `tests/test_indicator_context.py` — calculation, formatting, missing buffer, and LS API tests.
- Modify: `chart_generator.py` — chart panels for StochRSI/MAStochRSI, RSI(7), KDJ.
- Modify: `fvg_engine.py` — add `indicator_context` to `FVGZone` and zone persistence.
- Modify: `main.py` — build indicator context from `FVGTracker.buffers` before sending alerts.
- Modify: `telegram.py` — include indicator context in new/approach/touch alerts.
- External systems: Binance Futures data endpoint and Coolify app `kpax6cckqhg7veqqc9kqiikd`.

---

### Task 1: Indicator Context Module

**Files:**
- Create: `indicator_context.py`
- Create: `tests/test_indicator_context.py`

- [ ] **Step 1: Write tests for RSI7, StochRSI, KDJ, cross labels, context formatting, and missing buffers**

Create `tests/test_indicator_context.py` with:

```python
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import indicator_context


@dataclass(frozen=True)
class Bar:
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_closed: bool = True


def make_bars(closes):
    bars = []
    for i, close in enumerate(closes):
        bars.append(Bar(
            open_time=i,
            open=close - 0.2,
            high=close + 1.0,
            low=close - 1.0,
            close=close,
            volume=100 + i,
        ))
    return bars


def test_rsi7_returns_latest_value_for_trending_data():
    bars = make_bars([10, 11, 12, 13, 12, 14, 15, 16, 17, 18, 17, 19, 20, 21, 22])

    ctx = indicator_context.calculate_indicator_context("15m", bars, ls_ratio=None)

    assert ctx.rsi7 > 70
    assert ctx.tf == "15m"


def test_stochrsi_and_kdj_return_cross_state():
    bars = make_bars([10, 11, 12, 13, 14, 13, 12, 11, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21])

    ctx = indicator_context.calculate_indicator_context("1h", bars, ls_ratio=(72.2, 27.8))

    assert ctx.stoch_k is not None
    assert ctx.stoch_d is not None
    assert ctx.stoch_state in {"bull_cross", "bear_cross", "bull", "bear", "neutral"}
    assert ctx.kdj_k is not None
    assert ctx.kdj_d is not None
    assert ctx.kdj_j is not None
    assert ctx.kdj_state in {"bull_cross", "bear_cross", "bull", "bear", "neutral"}
    assert ctx.long_pct == 72.2
    assert ctx.short_pct == 27.8


def test_build_indicator_context_formats_three_timeframes(monkeypatch):
    bars = make_bars([10, 11, 12, 13, 12, 14, 15, 16, 17, 18, 17, 19, 20, 21, 22, 23, 22, 24, 25, 26])
    buffers = {
        ("BTCUSDT", "15m"): bars,
        ("BTCUSDT", "1h"): bars,
    }

    monkeypatch.setattr(indicator_context, "fetch_long_short_ratio", lambda symbol, tf: (60.0, 40.0))

    text = indicator_context.format_indicator_context("BTCUSDT", buffers)

    assert "📊 Indicator Context" in text
    assert "15m:" in text
    assert "1h :" in text
    assert "4h : n/a" in text
    assert "StochRSI" in text
    assert "RSI7" in text
    assert "KDJ" in text
    assert "LS L60.0/S40.0" in text


def test_long_short_ratio_uses_binance_response_and_cache(monkeypatch):
    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return [{"longAccount": "0.7221", "shortAccount": "0.2779"}]

    def fake_get(url, params, timeout):
        calls.append((url, params, timeout))
        return Response()

    indicator_context._LS_CACHE.clear()
    monkeypatch.setattr(indicator_context.requests, "get", fake_get)

    first = indicator_context.fetch_long_short_ratio("BTCUSDT", "15m")
    second = indicator_context.fetch_long_short_ratio("BTCUSDT", "15m")

    assert first == (72.21, 27.79)
    assert second == (72.21, 27.79)
    assert len(calls) == 1
    assert calls[0][1]["period"] == "15m"
```

- [ ] **Step 2: Run tests to verify they fail because module does not exist**

Run:

```bash
pytest tests/test_indicator_context.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'indicator_context'
```

- [ ] **Step 3: Implement `indicator_context.py`**

Create `indicator_context.py` with:

```python
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
    if len(bars) < 35:
        return IndicatorContext(tf=tf, long_pct=ls_ratio[0] if ls_ratio else None, short_pct=ls_ratio[1] if ls_ratio else None)
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
        long_pct=ls_ratio[0] if ls_ratio else None,
        short_pct=ls_ratio[1] if ls_ratio else None,
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
```

- [ ] **Step 4: Run indicator tests**

Run:

```bash
pytest tests/test_indicator_context.py -q
```

Expected:

```text
4 passed
```

- [ ] **Step 5: Commit indicator module**

Run:

```bash
git add indicator_context.py tests/test_indicator_context.py
git commit -m "feat: add indicator context calculations"
```

Expected: commit succeeds.

---

### Task 2: Telegram Alert Context Wiring

**Files:**
- Modify: `fvg_engine.py`
- Modify: `main.py`
- Modify: `telegram.py`

- [ ] **Step 1: Write alert context test**

Append to `tests/test_indicator_context.py`:

```python
def test_zone_indicator_context_text_can_be_rendered_in_alert(monkeypatch):
    import os
    os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
    os.environ.setdefault("TELEGRAM_CHAT_ID", "x")
    import telegram

    class Zone:
        direction = 1
        label = "Strong Bullish Imbalance"
        symbol = "BTCUSDT"
        tf = "15m"
        price = 100.0
        bottom = 99.0
        top = 101.0
        size = 2.0
        main_strength = 80
        bull_strength = 80
        bear_strength = 20
        rsi = 55.0
        atr = 1.2
        vol_change_pct = 10.0
        price_change_pct = 1.0
        price_change_24h_pct = 2.0
        candle_body_pct = 70.0
        dist_to_zone = 0.1
        dominance_state = "ALT"
        btc_state = "UP"
        dominance_bias = -0.01
        btc_trend = 0.01
        confirm_score = 80
        confirm_label = "A+"
        volume_spike_ratio = 2.0
        confluence_tf_count = 2
        displacement_ok = True
        btc_alignment_ok = True
        invalidated = False
        invalid_reason = ""
        sl = 98.0
        tp1 = 103.0
        tp2 = 105.0
        indicator_context = "📊 Indicator Context\n15m: StochRSI 15.0/10.0 bull | RSI7 55.0 | KDJ K50.0 D45.0 J60.0 bull | LS L60.0/S40.0"

    sent = {}
    monkeypatch.setattr(telegram, "_send", lambda text: sent.setdefault("text", text) or True)

    telegram.send_new_fvg_alert(Zone())

    assert "📊 Indicator Context" in sent["text"]
    assert "StochRSI" in sent["text"]
    assert "RSI7" in sent["text"]
    assert "KDJ" in sent["text"]
    assert "LS L60.0/S40.0" in sent["text"]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_indicator_context.py::test_zone_indicator_context_text_can_be_rendered_in_alert -q
```

Expected: fail because alert text does not include `indicator_context`.

- [ ] **Step 3: Add `indicator_context` to `FVGZone` and persistence**

In `fvg_engine.py`, add this field to `FVGZone` after `invalid_reason`:

```python
    indicator_context: str = ""
```

In `_zone_to_dict()`, add:

```python
            "indicator_context": zone.indicator_context,
```

In `_zone_from_dict()`, set the field when constructing `FVGZone`:

```python
            indicator_context=data.get("indicator_context", ""),
```

- [ ] **Step 4: Attach indicator context before every alert send**

In `main.py`, add import:

```python
from indicator_context import format_indicator_context
```

Before each send in `_on_bar_close`, set:

```python
zone.indicator_context = format_indicator_context(zone.symbol, self.tracker.buffers)
```

For new FVG, set:

```python
new_zone.indicator_context = format_indicator_context(new_zone.symbol, self.tracker.buffers)
```

Do this immediately before chart generation or send so context uses latest buffers.

- [ ] **Step 5: Render context in Telegram messages**

In `telegram.py`, inside `send_new_fvg_alert`, `send_approach_alert`, and `send_touch_alert`, define after `tv_url`:

```python
    indicator_context = getattr(zone, "indicator_context", "")
    indicator_block = f"\n\n{indicator_context}" if indicator_context else ""
```

Insert `{indicator_block}` before SL/TP section in each message.

- [ ] **Step 6: Run alert context test**

Run:

```bash
pytest tests/test_indicator_context.py::test_zone_indicator_context_text_can_be_rendered_in_alert -q
```

Expected:

```text
1 passed
```

- [ ] **Step 7: Run all tests**

Run:

```bash
pytest -q
```

Expected: all tests pass.

- [ ] **Step 8: Commit Telegram wiring**

Run:

```bash
git add fvg_engine.py main.py telegram.py tests/test_indicator_context.py
git commit -m "feat: show indicator context in alerts"
```

Expected: commit succeeds.

---

### Task 3: Chart Indicator Panels

**Files:**
- Modify: `chart_generator.py`

- [ ] **Step 1: Write chart generation test**

Append to `tests/test_indicator_context.py`:

```python
def test_chart_generation_with_indicator_panels_returns_png_bytes():
    import chart_generator

    bars = make_bars([10, 11, 12, 13, 12, 14, 15, 16, 17, 18, 17, 19, 20, 21, 22, 23, 22, 24, 25, 26, 27, 28, 27, 29, 30, 31, 32, 31, 33, 34, 35, 36, 35, 37, 38, 39, 40, 41, 40, 42])

    png = chart_generator.generate_chart(
        bars=bars,
        zone_top=38.0,
        zone_bottom=36.0,
        zone_direction=1,
        symbol="BTCUSDT",
        tf="15m",
        rsi_value=55.0,
    )

    assert png is not None
    assert png.startswith(b"\x89PNG")
```

- [ ] **Step 2: Run chart test before changes**

Run:

```bash
pytest tests/test_indicator_context.py::test_chart_generation_with_indicator_panels_returns_png_bytes -q
```

Expected: it may pass on existing chart. This is a smoke guard before modifying chart panels.

- [ ] **Step 3: Reuse indicator formulas in chart generator**

In `chart_generator.py`, add import:

```python
from indicator_context import kdj_series, rsi_series, stochrsi_series
```

Replace the old RSI-only calculation block with:

```python
        stoch_k, stoch_d = stochrsi_series(closes)
        rsi7 = rsi_series(closes, 7)
        kdj_k, kdj_d, kdj_j = kdj_series(df["High"].tolist(), df["Low"].tolist(), closes)

        df["EMA20"] = ema20
        df["EMA50"] = ema50
        df["STOCHRSI"] = stoch_k
        df["MASTOCHRSI"] = stoch_d
        df["RSI7"] = rsi7
        df["KDJ_K"] = kdj_k
        df["KDJ_D"] = kdj_d
        df["KDJ_J"] = kdj_j
```

Replace `apds` with:

```python
        apds = [
            mpf.make_addplot(df["EMA20"], color="orange", width=0.8, label="EMA20"),
            mpf.make_addplot(df["EMA50"], color="blue", width=0.8, label="EMA50"),
            mpf.make_addplot(df["STOCHRSI"], panel=1, color="#D8B11E", width=0.8, ylabel="StochRSI"),
            mpf.make_addplot(df["MASTOCHRSI"], panel=1, color="#7E57C2", width=0.8),
            mpf.make_addplot(df["RSI7"], panel=2, color="#D8B11E", width=0.8, ylabel="RSI7"),
            mpf.make_addplot(df["KDJ_K"], panel=3, color="#D8B11E", width=0.8, ylabel="KDJ"),
            mpf.make_addplot(df["KDJ_D"], panel=3, color="#D64BA2", width=0.8),
            mpf.make_addplot(df["KDJ_J"], panel=3, color="#7E57C2", width=0.8),
        ]
```

Change plot `panel_ratios` and `figsize`:

```python
            panel_ratios=(3, 1, 1, 1),
            figsize=(10, 10),
```

After plot, set axes:

```python
        ax_main = axes[0]
        ax_stoch = axes[2]
        ax_rsi = axes[4]
        ax_kdj = axes[6]
```

Replace RSI horizontal lines with:

```python
        for ax in (ax_stoch, ax_rsi, ax_kdj):
            ax.axhline(y=80, color="red", linestyle="--", linewidth=0.6, alpha=0.5)
            ax.axhline(y=20, color="green", linestyle="--", linewidth=0.6, alpha=0.5)
            ax.axhline(y=50, color="gray", linestyle="-", linewidth=0.5, alpha=0.4)
```

- [ ] **Step 4: Run chart test**

Run:

```bash
pytest tests/test_indicator_context.py::test_chart_generation_with_indicator_panels_returns_png_bytes -q
```

Expected:

```text
1 passed
```

- [ ] **Step 5: Run all tests**

Run:

```bash
pytest -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit chart panels**

Run:

```bash
git add chart_generator.py tests/test_indicator_context.py
git commit -m "feat: add indicator panels to alert charts"
```

Expected: commit succeeds.

---

### Task 4: Local Integration Check

**Files:**
- No new files.
- Verify: `indicator_context.py`, `telegram.py`, `chart_generator.py`

- [ ] **Step 1: Run all tests**

Run:

```bash
pytest -q
```

Expected: all tests pass.

- [ ] **Step 2: Run a local formatting smoke script**

Run:

```bash
python3 - <<'PY'
import os
import sys
from dataclasses import dataclass

os.environ.setdefault('TELEGRAM_BOT_TOKEN', 'x')
os.environ.setdefault('TELEGRAM_CHAT_ID', 'x')
sys.path.insert(0, '/Users/joseph/Documents/fvg-alpha-caller')

from indicator_context import format_indicator_context

@dataclass(frozen=True)
class Bar:
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_closed: bool = True

bars = [Bar(i, 10+i*0.1, 11+i*0.1, 9+i*0.1, 10+i*0.1, 100+i) for i in range(60)]
text = format_indicator_context('BTCUSDT', {('BTCUSDT', '15m'): bars, ('BTCUSDT', '1h'): bars, ('BTCUSDT', '4h'): bars})
print(text)
assert '15m:' in text and '1h :' in text and '4h :' in text
assert 'StochRSI' in text and 'RSI7' in text and 'KDJ' in text and 'LS' in text
PY
```

Expected: prints three timeframe context lines.

- [ ] **Step 3: Run chart smoke script**

Run:

```bash
python3 - <<'PY'
import os
import sys
from dataclasses import dataclass

os.environ.setdefault('TELEGRAM_BOT_TOKEN', 'x')
os.environ.setdefault('TELEGRAM_CHAT_ID', 'x')
sys.path.insert(0, '/Users/joseph/Documents/fvg-alpha-caller')

from chart_generator import generate_chart

@dataclass(frozen=True)
class Bar:
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_closed: bool = True

bars = [Bar(i, 10+i*0.1, 11+i*0.1, 9+i*0.1, 10+i*0.1, 100+i) for i in range(80)]
png = generate_chart(bars, 16, 15, 1, 'BTCUSDT', '15m', 55.0)
print(len(png or b''))
assert png and png.startswith(b'\x89PNG')
PY
```

Expected: prints PNG byte size and exits 0.

- [ ] **Step 4: Commit any integration fixes only if needed**

If Step 2 or Step 3 required edits, run:

```bash
git add indicator_context.py chart_generator.py fvg_engine.py main.py telegram.py tests/test_indicator_context.py
git commit -m "fix: stabilize indicator context integration"
```

Expected: commit succeeds if changes exist. If no changes exist, skip this commit.

---

### Task 5: Push, Deploy, and Verify Production

**Files:**
- No repository files modified.
- External systems: GitHub main, Coolify app `kpax6cckqhg7veqqc9kqiikd`, Docker host.

- [ ] **Step 1: Push main**

Run:

```bash
git push origin main
```

Expected: push succeeds.

- [ ] **Step 2: Trigger Coolify deploy**

Run:

```bash
python3 - <<'PY'
import json, urllib.request
base='https://ctrl.uponlytrader.xyz'
token='8|Fv0tMK2n7kxWa76ZWonFtFtpXPFIxG56mZU9EpYg60f3fe14'
payload={'uuid':'kpax6cckqhg7veqqc9kqiikd','force':False}
req=urllib.request.Request(
    base+'/api/v1/deploy',
    data=json.dumps(payload).encode(),
    method='POST',
    headers={'Authorization':'Bearer '+token,'Accept':'application/json','Content-Type':'application/json','User-Agent':'fvg-alpha-caller-indicator-context'},
)
with urllib.request.urlopen(req,timeout=60) as r:
    print(r.status)
    print(r.read().decode())
PY
```

Expected: response contains deployment UUID.

- [ ] **Step 3: Wait for deployment finish**

Run with the deployment UUID from Step 2:

```bash
python3 - <<'PY'
import json,time,urllib.request
base='https://ctrl.uponlytrader.xyz'
token='8|Fv0tMK2n7kxWa76ZWonFtFtpXPFIxG56mZU9EpYg60f3fe14'
deployment_uuid='<deployment-uuid>'
url=base+'/api/v1/deployments/'+deployment_uuid
for i in range(36):
    req=urllib.request.Request(url,headers={'Authorization':'Bearer '+token,'Accept':'application/json','User-Agent':'fvg-alpha-caller-indicator-context'})
    with urllib.request.urlopen(req,timeout=30) as r:
        data=json.loads(r.read().decode())
    print(i, data.get('status'), data.get('commit'), data.get('updated_at'))
    if data.get('status') not in ('queued','in_progress'):
        break
    time.sleep(10)
PY
```

Expected:

```text
finished <latest commit hash>
```

- [ ] **Step 4: Verify deployed code and one running bot**

Run:

```bash
ssh root@ssh.uponlytrader.xyz "docker ps --format '{{.Names}} {{.Image}} {{.Status}}' | grep kpax6cckqhg7veqqc9kqiikd"
ssh root@ssh.uponlytrader.xyz 'docker ps --format "{{.Names}}" | grep -E "kpax6cckqhg7veqqc9kqiikd|fvg-alpha-caller" | wc -l'
ssh root@ssh.uponlytrader.xyz 'docker exec <current-container> sh -lc "grep -n \"Indicator Context\|StochRSI\|topLongShortPositionRatio\" /app/indicator_context.py /app/telegram.py"'
```

Expected:

```text
one running kpax6cckqhg7veqqc9kqiikd container
running count 1
indicator_context.py and telegram.py contain indicator context code
```

- [ ] **Step 5: Verify production logs**

Run:

```bash
ssh root@ssh.uponlytrader.xyz 'docker logs --since 5m <current-container> 2>&1 | grep -E "Alpha Caller|BinanceKlineWS|WS warm-up complete|WS connected|ERROR|Traceback|Fetch long/short" | tail -n 120'
```

Expected:

```text
Alpha Caller (Binance WS + REST fallback)
BinanceKlineWS starting | symbols=100 tfs=3 streams=300 conns=3
WS warm-up complete | buffers=300
WS connected | conn=0 streams=100
WS connected | conn=1 streams=100
WS connected | conn=2 streams=100
no Traceback
no repeated long/short API failures
```

- [ ] **Step 6: Final response**

Report:

```text
implemented indicators
commit hash
Coolify deployment UUID
current container
running count
log health
notes about LS fallback behavior
```

---

## Self-Review

- Spec coverage: Plan covers indicator formulas, alert text, chart panels, LS API caching, missing data fallback, tests, deployment, and production verification.
- Placeholder scan: The only angle-bracket placeholders are runtime values (`<deployment-uuid>`, `<current-container>`) that are intentionally captured during deployment; no TBD/TODO remains.
- Type consistency: `IndicatorContext`, `format_indicator_context`, `calculate_indicator_context`, and `indicator_context` field names are consistent across tasks.
- Scope check: Single subsystem: indicator context in alerts and chart images.
