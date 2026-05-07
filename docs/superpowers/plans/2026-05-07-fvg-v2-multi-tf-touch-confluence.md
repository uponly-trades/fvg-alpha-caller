# FVG v2 Multi-TF Touch Confluence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship pure-TA v2 alert pipeline (15m/30m FVG touch + ≥1 HTF confluence) on `fvg-v2` branch + 3 cloned Coolify Apps, with Kronos disabled and wick-by-wick trail manager.

**Architecture:** New `strategy_v2.py` (signal builder) + `trail_manager.py` (SL ratchet) + `cooldown.py` (per-symbol throttle). `main.py` branches by `STRATEGY_VERSION` env: v1 keeps existing Kronos path untouched, v2 runs new pipeline. v2 ignores `MIN_STRENGTH_TO_ALERT` (uses any strength). Telegram bot reuses `@campinaz_bot` token; v1 service stops before v2 starts. v1 stays on `main`, v2 lives on `fvg-v2` branch.

**Tech Stack:** Python 3.11, asyncio, existing `fvg_engine.FVGTracker`, `BinanceKlineWS`, `psycopg2-binary`, pytest. No new deps.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `config.py` | Modify | Add `STRATEGY_VERSION`, `KRONOS_ENABLED`, `ATR_BUFFER_V2`, `HTF_TOUCH_LOOKBACK`, `V2_COOLDOWN_SEC`, `V2_TRIGGER_TFS`, `V2_HTF_TFS` |
| `strategy_v2.py` | Create | `V2Signal` dataclass + `evaluate_v2_signal(symbol, tracker, bars_by_tf)` |
| `trail_manager.py` | Create | `TrailState` + `TrailManager` (ratchet on bar close, touch-based stop) |
| `cooldown.py` | Create | `CooldownStore` per (symbol, direction) — 30min throttle |
| `main.py` | Modify | Branch on `STRATEGY_VERSION`; v2 path skips kronos, uses `strategy_v2` + `trail_manager` |
| `telegram.py` | Modify | Add `send_v2_alert`, `send_v2_trail_update`, `send_v2_stopped` |
| `tests/test_strategy_v2.py` | Create | Unit tests for signal evaluation |
| `tests/test_trail_manager.py` | Create | Unit tests for trail mechanic |
| `tests/test_cooldown.py` | Create | Unit tests for cooldown throttle |
| `docs/superpowers/specs/coolify-v2-apps-cutover.md` | Create | Manual ops runbook for v1→v2 cutover |

---

## Task 1: Create branch and skeleton files

**Files:**
- Modify: git refs (create branch `fvg-v2` from `main`)
- Create: `strategy_v2.py` (empty stub)
- Create: `trail_manager.py` (empty stub)
- Create: `cooldown.py` (empty stub)
- Create: `tests/__init__.py` (if missing)

- [ ] **Step 1: Verify clean working tree**

Run: `cd /Users/joseph/Documents/fvg-alpha-caller && git status`
Expected: clean tree (no uncommitted changes on tracked files relevant to v2)

- [ ] **Step 2: Create branch from main**

```bash
git checkout main
git pull origin main
git checkout -b fvg-v2
git push -u origin fvg-v2
```

- [ ] **Step 3: Create empty stub files**

```bash
touch strategy_v2.py trail_manager.py cooldown.py
touch tests/__init__.py
```

- [ ] **Step 4: Commit skeleton**

```bash
git add strategy_v2.py trail_manager.py cooldown.py tests/__init__.py
git commit -m "chore: v2 skeleton files"
git push
```

---

## Task 2: Add v2 config constants

**Files:**
- Modify: `config.py` (append at end)

- [ ] **Step 1: Append v2 config to `config.py`**

Append these lines at the very end of `/Users/joseph/Documents/fvg-alpha-caller/config.py`:

```python

# =====================================================
# v2 Strategy (Multi-TF FVG Touch Confluence)
# =====================================================
STRATEGY_VERSION = os.environ.get("STRATEGY_VERSION", "v1")  # "v1" or "v2"
KRONOS_ENABLED = os.environ.get("KRONOS_ENABLED", "true").lower() == "true"

# v2 detection params
V2_TRIGGER_TFS = ["15m", "30m"]              # bullish/bearish FVG touch on these
V2_HTF_TFS = ["1h", "2h", "4h"]              # confluence sources
V2_HTF_WEIGHTS = {"1h": 1, "2h": 2, "4h": 3} # display-only confidence score
V2_HTF_TOUCH_LOOKBACK = int(os.environ.get("HTF_TOUCH_LOOKBACK", "1"))  # closed-candle window for "currently touched"
ATR_BUFFER_V2 = float(os.environ.get("ATR_BUFFER_V2", "0.3"))           # SL buffer multiplier

# v2 trail
V2_TRAIL_ATR_BUFFER = ATR_BUFFER_V2  # alias — trail uses same buffer

# v2 throttle (mitigate higher alert volume from no-Kronos)
V2_COOLDOWN_SEC = int(os.environ.get("V2_COOLDOWN_SEC", "1800"))  # 30 minutes
```

- [ ] **Step 2: Verify import works**

Run: `cd /Users/joseph/Documents/fvg-alpha-caller && python -c "from config import STRATEGY_VERSION, KRONOS_ENABLED, V2_TRIGGER_TFS, V2_HTF_WEIGHTS, ATR_BUFFER_V2, V2_COOLDOWN_SEC; print(STRATEGY_VERSION, KRONOS_ENABLED, V2_TRIGGER_TFS, V2_HTF_WEIGHTS, ATR_BUFFER_V2, V2_COOLDOWN_SEC)"`
Expected: `v1 True ['15m', '30m'] {'1h': 1, '2h': 2, '4h': 3} 0.3 1800`

- [ ] **Step 3: Commit**

```bash
git add config.py
git commit -m "feat(v2): config constants for strategy v2"
git push
```

---

## Task 3: Cooldown store (TDD)

**Files:**
- Create: `cooldown.py`
- Create: `tests/test_cooldown.py`

- [ ] **Step 1: Write failing tests**

Write `/Users/joseph/Documents/fvg-alpha-caller/tests/test_cooldown.py`:

```python
import time
from cooldown import CooldownStore


def test_first_signal_passes():
    cd = CooldownStore(window_sec=60)
    assert cd.allow("BTCUSDT", "long") is True


def test_second_signal_within_window_blocked():
    cd = CooldownStore(window_sec=60)
    cd.allow("BTCUSDT", "long")
    assert cd.allow("BTCUSDT", "long") is False


def test_signal_after_window_passes():
    cd = CooldownStore(window_sec=1)
    cd.allow("BTCUSDT", "long")
    time.sleep(1.1)
    assert cd.allow("BTCUSDT", "long") is True


def test_different_direction_independent():
    cd = CooldownStore(window_sec=60)
    cd.allow("BTCUSDT", "long")
    assert cd.allow("BTCUSDT", "short") is True


def test_different_symbol_independent():
    cd = CooldownStore(window_sec=60)
    cd.allow("BTCUSDT", "long")
    assert cd.allow("ETHUSDT", "long") is True
```

- [ ] **Step 2: Run test, verify FAIL**

Run: `cd /Users/joseph/Documents/fvg-alpha-caller && python -m pytest tests/test_cooldown.py -v`
Expected: ImportError or AttributeError on `CooldownStore`

- [ ] **Step 3: Implement minimal `cooldown.py`**

Write `/Users/joseph/Documents/fvg-alpha-caller/cooldown.py`:

```python
import time
from typing import Dict, Tuple


class CooldownStore:
    """Per-(symbol, direction) signal throttle for v2 to mitigate alert spam."""

    def __init__(self, window_sec: int):
        self.window_sec = window_sec
        self._last_emit: Dict[Tuple[str, str], float] = {}

    def allow(self, symbol: str, direction: str) -> bool:
        """Return True if signal allowed (and record emission)."""
        key = (symbol, direction)
        now = time.time()
        last = self._last_emit.get(key)
        if last is not None and (now - last) < self.window_sec:
            return False
        self._last_emit[key] = now
        return True
```

- [ ] **Step 4: Run tests, verify PASS**

Run: `python -m pytest tests/test_cooldown.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add cooldown.py tests/test_cooldown.py
git commit -m "feat(v2): cooldown store for per-symbol-direction throttle"
git push
```

---

## Task 4: V2Signal dataclass + helper utilities (TDD)

**Files:**
- Create: `strategy_v2.py` (initial structure)
- Create: `tests/test_strategy_v2.py` (initial tests)

- [ ] **Step 1: Write failing test for `V2Signal` dataclass**

Write `/Users/joseph/Documents/fvg-alpha-caller/tests/test_strategy_v2.py`:

```python
import pytest
from strategy_v2 import V2Signal


def test_v2signal_fields_present():
    sig = V2Signal(
        symbol="BTCUSDT",
        direction=1,
        trigger_tf="15m",
        zone_top=67500.0,
        zone_bottom=67200.0,
        zone_born_time=1714915200000,
        entry=67250.0,
        sl=66890.0,
        atr=120.0,
        confluence_score=3,
        htf_touches={"1h": False, "2h": False, "4h": True},
        indicators={"stoch_rsi_15m": 23.0, "vol_change_pct": 18.0},
    )
    assert sig.direction == 1
    assert sig.trigger_tf == "15m"
    assert sig.confluence_score == 3
    assert sig.htf_touches["4h"] is True
```

- [ ] **Step 2: Run test, verify FAIL**

Run: `python -m pytest tests/test_strategy_v2.py::test_v2signal_fields_present -v`
Expected: ImportError on `V2Signal`

- [ ] **Step 3: Implement `V2Signal` in `strategy_v2.py`**

Write `/Users/joseph/Documents/fvg-alpha-caller/strategy_v2.py`:

```python
from dataclasses import dataclass, field
from typing import Dict, Optional, List

from config import (
    V2_TRIGGER_TFS, V2_HTF_TFS, V2_HTF_WEIGHTS,
    V2_HTF_TOUCH_LOOKBACK, ATR_BUFFER_V2,
)
from fvg_engine import FVGZone, detect_fvg, atr as compute_atr


@dataclass
class V2Signal:
    symbol: str
    direction: int                    # 1 long, -1 short
    trigger_tf: str                   # "15m" or "30m"
    zone_top: float
    zone_bottom: float
    zone_born_time: int
    entry: float
    sl: float
    atr: float
    confluence_score: int             # 1-6
    htf_touches: Dict[str, bool]      # {"1h": bool, "2h": bool, "4h": bool}
    indicators: Dict[str, float] = field(default_factory=dict)

    @property
    def direction_str(self) -> str:
        return "long" if self.direction == 1 else "short"
```

- [ ] **Step 4: Run test, verify PASS**

Run: `python -m pytest tests/test_strategy_v2.py::test_v2signal_fields_present -v`
Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
git add strategy_v2.py tests/test_strategy_v2.py
git commit -m "feat(v2): V2Signal dataclass"
git push
```

---

## Task 5: Bar/Zone test fixtures helper

**Files:**
- Modify: `tests/test_strategy_v2.py` (append fixture helpers)

- [ ] **Step 1: Append shared fixture helpers**

Append to `/Users/joseph/Documents/fvg-alpha-caller/tests/test_strategy_v2.py`:

```python
from rest_client import Bar
from fvg_engine import FVGZone


def make_bar(open_time: int, o: float, h: float, l: float, c: float, v: float = 100.0) -> Bar:
    return Bar(
        open_time=open_time, open=o, high=h, low=l, close=c, volume=v, is_closed=True,
    )


def make_zone(
    symbol: str = "BTCUSDT",
    tf: str = "15m",
    direction: int = 1,
    top: float = 100.5,
    bottom: float = 99.5,
    born_time: int = 1700000000000,
    atr_val: float = 1.0,
) -> FVGZone:
    return FVGZone(
        symbol=symbol, tf=tf, direction=direction,
        top=top, bottom=bottom, size=top - bottom,
        born_time=born_time, atr=atr_val,
    )


def make_bull_fvg_bars(zone_low: float = 99.5, zone_high: float = 100.5) -> list:
    """Build 3 bars where the third bar's low > first bar's high (bull FVG)."""
    return [
        make_bar(1, 98.0, 99.0, 97.5, zone_low - 0.5, 200.0),  # prev2: high=99.0
        make_bar(2, zone_low - 0.4, 102.0, zone_low - 0.6, 101.5, 500.0),  # prev1: displacement
        make_bar(3, 101.6, 102.5, zone_high + 0.1, 102.0, 300.0),  # curr: low=zone_high+0.1 > prev2.high=99
    ]


def make_bear_fvg_bars(zone_low: float = 99.5, zone_high: float = 100.5) -> list:
    """3 bars where curr.high < prev2.low (bear FVG)."""
    return [
        make_bar(1, 102.0, 102.5, zone_high + 0.5, 102.3, 200.0),  # prev2: low=zone_high+0.5
        make_bar(2, 102.0, 102.0, 99.0, 99.2, 500.0),               # prev1: displacement
        make_bar(3, 99.0, zone_low - 0.1, 98.0, 98.5, 300.0),       # curr: high=zone_low-0.1 < prev2.low
    ]
```

- [ ] **Step 2: Verify Bar dataclass importable**

Run: `python -c "from rest_client import Bar; b = Bar(open_time=1, open=1.0, high=2.0, low=0.5, close=1.5, volume=10.0, is_closed=True); print(b)"`
Expected: prints Bar instance

- [ ] **Step 3: Run existing test still passes**

Run: `python -m pytest tests/test_strategy_v2.py -v`
Expected: 1 passed (V2Signal still passes)

- [ ] **Step 4: Commit**

```bash
git add tests/test_strategy_v2.py
git commit -m "test(v2): bar/zone fixture helpers"
git push
```

---

## Task 6: HTF zone-active-and-touched detector (TDD)

**Files:**
- Modify: `strategy_v2.py` (add `_htf_active_and_touched`)
- Modify: `tests/test_strategy_v2.py` (add tests)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_strategy_v2.py`:

```python
from strategy_v2 import _htf_active_and_touched


def test_htf_no_zone_returns_false():
    bars = [make_bar(i, 100, 101, 99, 100.5) for i in range(1, 25)]
    result = _htf_active_and_touched(zone=None, bars=bars, lookback=1)
    assert result is False


def test_htf_zone_present_but_not_touched_returns_false():
    bars = [make_bar(i, 200, 201, 199, 200.5) for i in range(1, 25)]
    zone = make_zone(top=100.5, bottom=99.5)  # bars are at 200, zone at 100 — no overlap
    result = _htf_active_and_touched(zone=zone, bars=bars, lookback=1)
    assert result is False


def test_htf_zone_touched_within_lookback_returns_true():
    # Latest bar overlaps zone [99.5, 100.5]
    bars = [make_bar(i, 100, 101, 99, 100.5) for i in range(1, 24)]
    bars.append(make_bar(24, 100, 100.6, 99.4, 100.0))  # overlaps zone
    zone = make_zone(top=100.5, bottom=99.5)
    result = _htf_active_and_touched(zone=zone, bars=bars, lookback=1)
    assert result is True


def test_htf_zone_touched_only_outside_lookback_returns_false():
    bars = []
    bars.append(make_bar(1, 100, 100.6, 99.4, 100.0))   # touch (oldest)
    for i in range(2, 25):
        bars.append(make_bar(i, 200, 201, 199, 200.5))  # no touch (recent)
    zone = make_zone(top=100.5, bottom=99.5)
    result = _htf_active_and_touched(zone=zone, bars=bars, lookback=1)
    assert result is False


def test_htf_zone_fully_mitigated_returns_false():
    """Zone fully mitigated = bottom (long) breached → not active."""
    zone = make_zone(top=100.5, bottom=99.5, direction=1)
    zone.mitigation = 1.0  # 100% mitigated
    bars = [make_bar(i, 100, 101, 99, 100.0) for i in range(1, 25)]
    result = _htf_active_and_touched(zone=zone, bars=bars, lookback=1)
    assert result is False
```

- [ ] **Step 2: Run tests, verify FAIL**

Run: `python -m pytest tests/test_strategy_v2.py -v`
Expected: ImportError on `_htf_active_and_touched`

- [ ] **Step 3: Implement `_htf_active_and_touched`**

Append to `/Users/joseph/Documents/fvg-alpha-caller/strategy_v2.py`:

```python
def _htf_active_and_touched(
    zone: Optional[FVGZone],
    bars: List,
    lookback: int = 1,
) -> bool:
    """True if zone exists, is not fully mitigated, and price overlapped zone
    on any of the last `lookback` closed bars."""
    if zone is None:
        return False
    if zone.mitigation >= 1.0:
        return False
    if not bars or len(bars) < lookback:
        return False
    recent = bars[-lookback:]
    for b in recent:
        if b.high >= zone.bottom and b.low <= zone.top:
            return True
    return False
```

- [ ] **Step 4: Run tests, verify PASS**

Run: `python -m pytest tests/test_strategy_v2.py -v`
Expected: 6 passed (1 V2Signal + 5 htf tests)

- [ ] **Step 5: Commit**

```bash
git add strategy_v2.py tests/test_strategy_v2.py
git commit -m "feat(v2): HTF active-and-touched detector"
git push
```

---

## Task 7: Pick latest active HTF zone (TDD)

**Files:**
- Modify: `strategy_v2.py` (add `_latest_active_zone`)
- Modify: `tests/test_strategy_v2.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_strategy_v2.py`:

```python
from strategy_v2 import _latest_active_zone


def test_latest_active_zone_returns_none_when_empty():
    assert _latest_active_zone(zones={}, symbol="BTCUSDT", tf="1h", direction=1) is None


def test_latest_active_zone_filters_by_symbol_tf_direction():
    z_match = make_zone(symbol="BTCUSDT", tf="1h", direction=1, born_time=2000)
    z_wrong_symbol = make_zone(symbol="ETHUSDT", tf="1h", direction=1, born_time=3000)
    z_wrong_tf = make_zone(symbol="BTCUSDT", tf="2h", direction=1, born_time=3000)
    z_wrong_dir = make_zone(symbol="BTCUSDT", tf="1h", direction=-1, born_time=3000)
    zones = {"a": z_match, "b": z_wrong_symbol, "c": z_wrong_tf, "d": z_wrong_dir}
    result = _latest_active_zone(zones=zones, symbol="BTCUSDT", tf="1h", direction=1)
    assert result is z_match


def test_latest_active_zone_picks_youngest():
    z_old = make_zone(symbol="BTCUSDT", tf="1h", direction=1, born_time=1000)
    z_new = make_zone(symbol="BTCUSDT", tf="1h", direction=1, born_time=2000)
    zones = {"a": z_old, "b": z_new}
    result = _latest_active_zone(zones=zones, symbol="BTCUSDT", tf="1h", direction=1)
    assert result is z_new


def test_latest_active_zone_skips_fully_mitigated():
    z_mit = make_zone(symbol="BTCUSDT", tf="1h", direction=1, born_time=2000)
    z_mit.mitigation = 1.0
    z_active = make_zone(symbol="BTCUSDT", tf="1h", direction=1, born_time=1500)
    zones = {"a": z_mit, "b": z_active}
    result = _latest_active_zone(zones=zones, symbol="BTCUSDT", tf="1h", direction=1)
    assert result is z_active
```

- [ ] **Step 2: Run tests, verify FAIL**

Run: `python -m pytest tests/test_strategy_v2.py -v`
Expected: ImportError on `_latest_active_zone`

- [ ] **Step 3: Implement `_latest_active_zone`**

Append to `strategy_v2.py`:

```python
def _latest_active_zone(
    zones: Dict[str, FVGZone],
    symbol: str,
    tf: str,
    direction: int,
) -> Optional[FVGZone]:
    """Return the most recently born, not-fully-mitigated zone for (symbol, tf, direction)."""
    candidates = [
        z for z in zones.values()
        if z.symbol == symbol
        and z.tf == tf
        and z.direction == direction
        and z.mitigation < 1.0
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda z: z.born_time)
```

- [ ] **Step 4: Run tests, verify PASS**

Run: `python -m pytest tests/test_strategy_v2.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add strategy_v2.py tests/test_strategy_v2.py
git commit -m "feat(v2): latest active zone picker"
git push
```

---

## Task 8: Confluence score builder (TDD)

**Files:**
- Modify: `strategy_v2.py` (add `_compute_htf_confluence`)
- Modify: `tests/test_strategy_v2.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_strategy_v2.py`:

```python
from strategy_v2 import _compute_htf_confluence


def test_confluence_no_htf_zones_returns_zero():
    zones = {}
    bars_by_tf = {"1h": [], "2h": [], "4h": []}
    score, touches = _compute_htf_confluence(zones, "BTCUSDT", direction=1, bars_by_tf=bars_by_tf)
    assert score == 0
    assert touches == {"1h": False, "2h": False, "4h": False}


def test_confluence_only_4h_touched_returns_score_3():
    z_4h = make_zone(tf="4h", direction=1, top=100.5, bottom=99.5)
    zones = {"a": z_4h}
    bars_4h = [make_bar(i, 100, 101, 99, 100.0) for i in range(1, 25)]
    bars_4h.append(make_bar(25, 100, 100.6, 99.4, 100.0))  # last bar touches
    bars_by_tf = {"1h": [], "2h": [], "4h": bars_4h}
    score, touches = _compute_htf_confluence(zones, "BTCUSDT", direction=1, bars_by_tf=bars_by_tf)
    assert score == 3
    assert touches == {"1h": False, "2h": False, "4h": True}


def test_confluence_all_three_touched_returns_score_6():
    bars_touch = [make_bar(i, 100, 101, 99, 100.0) for i in range(1, 24)]
    bars_touch.append(make_bar(24, 100, 100.6, 99.4, 100.0))
    z_1h = make_zone(tf="1h", direction=1, top=100.5, bottom=99.5, born_time=100)
    z_2h = make_zone(tf="2h", direction=1, top=100.5, bottom=99.5, born_time=200)
    z_4h = make_zone(tf="4h", direction=1, top=100.5, bottom=99.5, born_time=300)
    zones = {"a": z_1h, "b": z_2h, "c": z_4h}
    bars_by_tf = {"1h": bars_touch, "2h": bars_touch, "4h": bars_touch}
    score, touches = _compute_htf_confluence(zones, "BTCUSDT", direction=1, bars_by_tf=bars_by_tf)
    assert score == 6
    assert touches == {"1h": True, "2h": True, "4h": True}


def test_confluence_direction_filters():
    """Bull zone present at 1h/2h/4h but request short → score 0."""
    bars_touch = [make_bar(i, 100, 101, 99, 100.0) for i in range(1, 24)]
    bars_touch.append(make_bar(24, 100, 100.6, 99.4, 100.0))
    z = make_zone(tf="1h", direction=1, top=100.5, bottom=99.5)
    zones = {"a": z}
    bars_by_tf = {"1h": bars_touch, "2h": [], "4h": []}
    score, touches = _compute_htf_confluence(zones, "BTCUSDT", direction=-1, bars_by_tf=bars_by_tf)
    assert score == 0
```

- [ ] **Step 2: Run tests, verify FAIL**

Run: `python -m pytest tests/test_strategy_v2.py -v`
Expected: ImportError on `_compute_htf_confluence`

- [ ] **Step 3: Implement `_compute_htf_confluence`**

Append to `strategy_v2.py`:

```python
def _compute_htf_confluence(
    zones: Dict[str, FVGZone],
    symbol: str,
    direction: int,
    bars_by_tf: Dict[str, List],
) -> tuple:
    """Returns (score, touches_dict). Score sums weights for HTFs that have an
    active same-direction zone touched within V2_HTF_TOUCH_LOOKBACK."""
    touches: Dict[str, bool] = {}
    score = 0
    for tf in V2_HTF_TFS:
        zone = _latest_active_zone(zones, symbol, tf, direction)
        bars = bars_by_tf.get(tf, [])
        touched = _htf_active_and_touched(zone, bars, lookback=V2_HTF_TOUCH_LOOKBACK)
        touches[tf] = touched
        if touched:
            score += V2_HTF_WEIGHTS[tf]
    return score, touches
```

- [ ] **Step 4: Run tests, verify PASS**

Run: `python -m pytest tests/test_strategy_v2.py -v`
Expected: 14 passed

- [ ] **Step 5: Commit**

```bash
git add strategy_v2.py tests/test_strategy_v2.py
git commit -m "feat(v2): HTF confluence score"
git push
```

---

## Task 9: Trigger TF zone touched (TDD)

**Files:**
- Modify: `strategy_v2.py` (add `_trigger_zone_touched`)
- Modify: `tests/test_strategy_v2.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_strategy_v2.py`:

```python
from strategy_v2 import _trigger_zone_touched


def test_trigger_no_zone_returns_none():
    bars = [make_bar(i, 100, 101, 99, 100.0) for i in range(1, 5)]
    assert _trigger_zone_touched(zone=None, bars=bars) is None


def test_trigger_zone_not_touched_returns_none():
    z = make_zone(top=100.5, bottom=99.5)
    bars = [make_bar(i, 200, 201, 199, 200.5) for i in range(1, 5)]  # bars at 200
    assert _trigger_zone_touched(zone=z, bars=bars) is None


def test_trigger_zone_touched_returns_zone():
    z = make_zone(top=100.5, bottom=99.5)
    bars = [make_bar(i, 100, 101, 99, 100.0) for i in range(1, 5)]
    assert _trigger_zone_touched(zone=z, bars=bars) is z


def test_trigger_zone_fully_mitigated_returns_none():
    z = make_zone(top=100.5, bottom=99.5)
    z.mitigation = 1.0
    bars = [make_bar(i, 100, 101, 99, 100.0) for i in range(1, 5)]
    assert _trigger_zone_touched(zone=z, bars=bars) is None
```

- [ ] **Step 2: Run, verify FAIL**

Run: `python -m pytest tests/test_strategy_v2.py -v`
Expected: ImportError on `_trigger_zone_touched`

- [ ] **Step 3: Implement `_trigger_zone_touched`**

Append to `strategy_v2.py`:

```python
def _trigger_zone_touched(zone: Optional[FVGZone], bars: List) -> Optional[FVGZone]:
    """Return zone if last closed bar touched it (and zone is not fully mitigated)."""
    if zone is None or zone.mitigation >= 1.0:
        return None
    if not bars:
        return None
    last = bars[-1]
    if last.high >= zone.bottom and last.low <= zone.top:
        return zone
    return None
```

- [ ] **Step 4: Run, verify PASS**

Run: `python -m pytest tests/test_strategy_v2.py -v`
Expected: 18 passed

- [ ] **Step 5: Commit**

```bash
git add strategy_v2.py tests/test_strategy_v2.py
git commit -m "feat(v2): trigger TF touch detector"
git push
```

---

## Task 10: SL formula + entry helpers (TDD)

**Files:**
- Modify: `strategy_v2.py` (add `_compute_sl`, `_entry_price`)
- Modify: `tests/test_strategy_v2.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_strategy_v2.py`:

```python
from strategy_v2 import _compute_sl


def test_sl_long_below_zone_bottom():
    z = make_zone(top=100.5, bottom=99.5, direction=1, atr_val=2.0)
    sl = _compute_sl(zone=z, atr_val=2.0)
    # 99.5 - 2.0 * 0.3 = 98.9
    assert abs(sl - 98.9) < 1e-9


def test_sl_short_above_zone_top():
    z = make_zone(top=100.5, bottom=99.5, direction=-1, atr_val=2.0)
    sl = _compute_sl(zone=z, atr_val=2.0)
    # 100.5 + 2.0 * 0.3 = 101.1
    assert abs(sl - 101.1) < 1e-9


def test_sl_uses_zone_bottom_not_wick():
    """SL is below FVG, not below candle wick (per spec)."""
    z = make_zone(top=100.5, bottom=99.5, direction=1, atr_val=2.0)
    sl = _compute_sl(zone=z, atr_val=2.0)
    assert sl < z.bottom
    assert sl > z.bottom - 5.0   # not stupidly far
```

- [ ] **Step 2: Run, verify FAIL**

Run: `python -m pytest tests/test_strategy_v2.py -v`
Expected: ImportError on `_compute_sl`

- [ ] **Step 3: Implement `_compute_sl`**

Append to `strategy_v2.py`:

```python
def _compute_sl(zone: FVGZone, atr_val: float) -> float:
    """SL = zone edge ± ATR*buffer. Below FVG bottom for long, above top for short."""
    buf = atr_val * ATR_BUFFER_V2
    if zone.direction == 1:
        return zone.bottom - buf
    return zone.top + buf
```

- [ ] **Step 4: Run, verify PASS**

Run: `python -m pytest tests/test_strategy_v2.py -v`
Expected: 21 passed

- [ ] **Step 5: Commit**

```bash
git add strategy_v2.py tests/test_strategy_v2.py
git commit -m "feat(v2): SL computation below/above FVG"
git push
```

---

## Task 11: Main `evaluate_v2_signal` orchestrator (TDD)

**Files:**
- Modify: `strategy_v2.py` (add `evaluate_v2_signal`)
- Modify: `tests/test_strategy_v2.py`

- [ ] **Step 1: Write failing tests for orchestrator**

Append to `tests/test_strategy_v2.py`:

```python
from strategy_v2 import evaluate_v2_signal


def _bars_at_zone(zone, n=25):
    """Build n closed bars all overlapping a zone."""
    bars = []
    for i in range(1, n + 1):
        bars.append(make_bar(i, zone.bottom + 0.1, zone.top + 0.1, zone.bottom - 0.1, zone.bottom + 0.2, 100.0))
    return bars


def _bars_far_from_zone(zone, n=25):
    """Build n bars far above zone (no overlap)."""
    far = zone.top + 50.0
    return [make_bar(i, far, far + 1, far - 1, far + 0.5, 100.0) for i in range(1, n + 1)]


def test_eval_no_15m_no_30m_zones_returns_none():
    zones = {}
    bars_by_tf = {tf: [make_bar(i, 100, 101, 99, 100.0) for i in range(1, 25)] for tf in ("15m", "30m", "1h", "2h", "4h")}
    sig = evaluate_v2_signal("BTCUSDT", zones, bars_by_tf)
    assert sig is None


def test_eval_15m_touched_no_htf_confluence_returns_none():
    z_15m = make_zone(tf="15m", direction=1, top=100.5, bottom=99.5, atr_val=1.0)
    zones = {"a": z_15m}
    bars_by_tf = {
        "15m": _bars_at_zone(z_15m),
        "30m": _bars_far_from_zone(z_15m),
        "1h": _bars_far_from_zone(z_15m),
        "2h": _bars_far_from_zone(z_15m),
        "4h": _bars_far_from_zone(z_15m),
    }
    sig = evaluate_v2_signal("BTCUSDT", zones, bars_by_tf)
    assert sig is None


def test_eval_15m_touched_with_4h_confluence_returns_long_signal():
    z_15m = make_zone(tf="15m", direction=1, top=100.5, bottom=99.5, atr_val=1.0)
    z_4h = make_zone(tf="4h", direction=1, top=100.5, bottom=99.5, atr_val=1.0)
    zones = {"a": z_15m, "b": z_4h}
    bars_at = _bars_at_zone(z_15m)
    bars_by_tf = {
        "15m": bars_at,
        "30m": _bars_far_from_zone(z_15m),
        "1h": _bars_far_from_zone(z_15m),
        "2h": _bars_far_from_zone(z_15m),
        "4h": bars_at,
    }
    sig = evaluate_v2_signal("BTCUSDT", zones, bars_by_tf)
    assert sig is not None
    assert sig.direction == 1
    assert sig.trigger_tf == "15m"
    assert sig.confluence_score == 3
    assert sig.htf_touches["4h"] is True
    assert sig.htf_touches["1h"] is False


def test_eval_30m_fallback_when_no_15m_zone():
    z_30m = make_zone(tf="30m", direction=1, top=100.5, bottom=99.5, atr_val=1.0)
    z_1h = make_zone(tf="1h", direction=1, top=100.5, bottom=99.5, atr_val=1.0)
    zones = {"a": z_30m, "b": z_1h}
    bars_at = _bars_at_zone(z_30m)
    bars_by_tf = {
        "15m": _bars_far_from_zone(z_30m),
        "30m": bars_at,
        "1h": bars_at,
        "2h": _bars_far_from_zone(z_30m),
        "4h": _bars_far_from_zone(z_30m),
    }
    sig = evaluate_v2_signal("BTCUSDT", zones, bars_by_tf)
    assert sig is not None
    assert sig.trigger_tf == "30m"
    assert sig.confluence_score == 1


def test_eval_short_mirror():
    z_15m = make_zone(tf="15m", direction=-1, top=100.5, bottom=99.5, atr_val=1.0)
    z_4h = make_zone(tf="4h", direction=-1, top=100.5, bottom=99.5, atr_val=1.0)
    zones = {"a": z_15m, "b": z_4h}
    bars_at = _bars_at_zone(z_15m)
    bars_by_tf = {
        "15m": bars_at, "30m": _bars_far_from_zone(z_15m),
        "1h": _bars_far_from_zone(z_15m), "2h": _bars_far_from_zone(z_15m),
        "4h": bars_at,
    }
    sig = evaluate_v2_signal("BTCUSDT", zones, bars_by_tf)
    assert sig is not None
    assert sig.direction == -1
    assert sig.sl > sig.zone_top  # short SL is above zone top


def test_eval_sl_below_fvg_bottom_long():
    z_15m = make_zone(tf="15m", direction=1, top=100.5, bottom=99.5, atr_val=1.0)
    z_4h = make_zone(tf="4h", direction=1, top=100.5, bottom=99.5, atr_val=1.0)
    zones = {"a": z_15m, "b": z_4h}
    bars_at = _bars_at_zone(z_15m)
    bars_by_tf = {tf: bars_at if tf in ("15m", "4h") else _bars_far_from_zone(z_15m)
                  for tf in ("15m", "30m", "1h", "2h", "4h")}
    sig = evaluate_v2_signal("BTCUSDT", zones, bars_by_tf)
    assert sig is not None
    assert sig.sl < z_15m.bottom
    # SL should be zone.bottom - atr*0.3 (atr=1.0, buffer=0.3) = 99.5 - 0.3 = 99.2
    assert abs(sig.sl - 99.2) < 1e-9
```

- [ ] **Step 2: Run, verify FAIL**

Run: `python -m pytest tests/test_strategy_v2.py -v`
Expected: ImportError on `evaluate_v2_signal`

- [ ] **Step 3: Implement `evaluate_v2_signal`**

Append to `strategy_v2.py`:

```python
def evaluate_v2_signal(
    symbol: str,
    zones: Dict[str, FVGZone],
    bars_by_tf: Dict[str, List],
) -> Optional[V2Signal]:
    """Multi-TF FVG touch confluence detector.

    Returns V2Signal if:
      1. 15m or 30m FVG (any strength, any direction) is touched on latest bar.
      2. At least one of {1h, 2h, 4h} same-direction FVG is active+touched.
    """
    for direction in (1, -1):
        for trigger_tf in V2_TRIGGER_TFS:
            zone = _latest_active_zone(zones, symbol, trigger_tf, direction)
            triggered = _trigger_zone_touched(zone, bars_by_tf.get(trigger_tf, []))
            if triggered is None:
                continue
            score, touches = _compute_htf_confluence(zones, symbol, direction, bars_by_tf)
            if score < 1:
                continue

            atr_val = float(triggered.atr) if triggered.atr else 0.0
            if atr_val <= 0:
                # Fallback: compute ATR from trigger TF bars
                bars = bars_by_tf.get(trigger_tf, [])
                if len(bars) >= 15:
                    highs = [b.high for b in bars]
                    lows = [b.low for b in bars]
                    closes = [b.close for b in bars]
                    atr_val = compute_atr(highs, lows, closes, 14) or triggered.size
                else:
                    atr_val = triggered.size

            sl = _compute_sl(triggered, atr_val)
            entry = float(bars_by_tf[trigger_tf][-1].close)

            return V2Signal(
                symbol=symbol,
                direction=direction,
                trigger_tf=trigger_tf,
                zone_top=triggered.top,
                zone_bottom=triggered.bottom,
                zone_born_time=triggered.born_time,
                entry=entry,
                sl=sl,
                atr=atr_val,
                confluence_score=score,
                htf_touches=touches,
                indicators={},
            )
    return None
```

- [ ] **Step 4: Run, verify PASS**

Run: `python -m pytest tests/test_strategy_v2.py -v`
Expected: 27 passed

- [ ] **Step 5: Commit**

```bash
git add strategy_v2.py tests/test_strategy_v2.py
git commit -m "feat(v2): evaluate_v2_signal orchestrator"
git push
```

---

## Task 12: TrailManager — TrailState + register/snapshot (TDD)

**Files:**
- Create: `tests/test_trail_manager.py`
- Modify: `trail_manager.py`

- [ ] **Step 1: Write failing tests**

Write `/Users/joseph/Documents/fvg-alpha-caller/tests/test_trail_manager.py`:

```python
from rest_client import Bar
from trail_manager import TrailManager, TrailState


def make_bar(t, o, h, l, c, v=100.0):
    return Bar(open_time=t, open=o, high=h, low=l, close=c, volume=v, is_closed=True)


def test_register_creates_state():
    tm = TrailManager()
    tm.register(
        signal_id="BTCUSDT_15m_1700_1",
        symbol="BTCUSDT", trigger_tf="15m", direction=1,
        entry=100.0, sl=99.0, atr=1.0,
    )
    states = tm.snapshot()
    assert len(states) == 1
    assert states[0].symbol == "BTCUSDT"
    assert states[0].current_sl == 99.0


def test_register_duplicate_signal_id_idempotent():
    tm = TrailManager()
    tm.register(signal_id="x", symbol="BTCUSDT", trigger_tf="15m", direction=1,
                entry=100.0, sl=99.0, atr=1.0)
    tm.register(signal_id="x", symbol="BTCUSDT", trigger_tf="15m", direction=1,
                entry=100.0, sl=99.0, atr=1.0)
    assert len(tm.snapshot()) == 1
```

- [ ] **Step 2: Run, verify FAIL**

Run: `python -m pytest tests/test_trail_manager.py -v`
Expected: ImportError on `TrailManager`

- [ ] **Step 3: Implement initial `trail_manager.py`**

Write `/Users/joseph/Documents/fvg-alpha-caller/trail_manager.py`:

```python
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from config import V2_TRAIL_ATR_BUFFER


@dataclass
class TrailState:
    signal_id: str
    symbol: str
    trigger_tf: str
    direction: int                # 1 long, -1 short
    entry: float
    current_sl: float
    atr: float
    last_update_time: int = 0
    closed: bool = False
    close_reason: str = ""


class TrailManager:
    """In-memory wick-by-wick trail manager. Persisted state lives in DB layer
    (sim_trades / executor_state) — this class only manages live ratchet logic."""

    def __init__(self):
        self._states: Dict[str, TrailState] = {}

    def register(
        self, signal_id: str, symbol: str, trigger_tf: str,
        direction: int, entry: float, sl: float, atr: float,
    ) -> Optional[TrailState]:
        if signal_id in self._states:
            return None
        state = TrailState(
            signal_id=signal_id, symbol=symbol, trigger_tf=trigger_tf,
            direction=direction, entry=entry, current_sl=sl, atr=atr,
        )
        self._states[signal_id] = state
        return state

    def snapshot(self) -> List[TrailState]:
        return list(self._states.values())

    def get(self, signal_id: str) -> Optional[TrailState]:
        return self._states.get(signal_id)
```

- [ ] **Step 4: Run, verify PASS**

Run: `python -m pytest tests/test_trail_manager.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add trail_manager.py tests/test_trail_manager.py
git commit -m "feat(v2): TrailManager register + snapshot"
git push
```

---

## Task 13: Trail ratchet on bar close (TDD)

**Files:**
- Modify: `trail_manager.py` (add `on_bar_close`)
- Modify: `tests/test_trail_manager.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_trail_manager.py`:

```python
def test_long_trail_ratchets_up_on_higher_low():
    tm = TrailManager()
    tm.register(signal_id="x", symbol="BTCUSDT", trigger_tf="15m", direction=1,
                entry=100.0, sl=98.0, atr=1.0)
    # prev candle (bars[-2]) has low=99.5 → new_sl = 99.5 - 1.0*0.3 = 99.2
    bars = [
        make_bar(1, 99.5, 100.5, 99.5, 100.2),  # bars[-2] = prev closed
        make_bar(2, 100.2, 100.8, 100.0, 100.5),  # bars[-1] = current closed (newer)
    ]
    updates = tm.on_bar_close("BTCUSDT", "15m", bars)
    assert len(updates) == 1
    state = tm.get("x")
    assert abs(state.current_sl - 99.2) < 1e-9
    assert updates[0].previous_sl == 98.0
    assert abs(updates[0].new_sl - 99.2) < 1e-9


def test_long_trail_does_not_lower_sl():
    tm = TrailManager()
    tm.register(signal_id="x", symbol="BTCUSDT", trigger_tf="15m", direction=1,
                entry=100.0, sl=99.5, atr=1.0)
    # prev candle low=99.0 → new_sl = 99.0 - 0.3 = 98.7 (lower than current 99.5) → NO change
    bars = [
        make_bar(1, 99.0, 99.5, 99.0, 99.3),
        make_bar(2, 99.3, 99.4, 99.0, 99.2),
    ]
    updates = tm.on_bar_close("BTCUSDT", "15m", bars)
    assert updates == []
    state = tm.get("x")
    assert state.current_sl == 99.5


def test_short_trail_ratchets_down_on_lower_high():
    tm = TrailManager()
    tm.register(signal_id="x", symbol="BTCUSDT", trigger_tf="15m", direction=-1,
                entry=100.0, sl=102.0, atr=1.0)
    # prev candle high=100.5 → new_sl = 100.5 + 0.3 = 100.8
    bars = [
        make_bar(1, 100.4, 100.5, 100.0, 100.2),
        make_bar(2, 100.2, 100.3, 99.5, 99.8),
    ]
    updates = tm.on_bar_close("BTCUSDT", "15m", bars)
    assert len(updates) == 1
    state = tm.get("x")
    assert abs(state.current_sl - 100.8) < 1e-9


def test_trail_ignores_states_for_other_symbols():
    tm = TrailManager()
    tm.register(signal_id="x", symbol="ETHUSDT", trigger_tf="15m", direction=1,
                entry=100.0, sl=98.0, atr=1.0)
    bars = [make_bar(1, 99.0, 100.0, 99.0, 99.5), make_bar(2, 99.5, 100.0, 99.0, 99.8)]
    updates = tm.on_bar_close("BTCUSDT", "15m", bars)
    assert updates == []


def test_trail_ignores_states_for_other_tf():
    tm = TrailManager()
    tm.register(signal_id="x", symbol="BTCUSDT", trigger_tf="30m", direction=1,
                entry=100.0, sl=98.0, atr=1.0)
    bars = [make_bar(1, 99.0, 100.0, 99.0, 99.5), make_bar(2, 99.5, 100.0, 99.0, 99.8)]
    updates = tm.on_bar_close("BTCUSDT", "15m", bars)
    assert updates == []


def test_trail_skips_when_only_one_bar():
    tm = TrailManager()
    tm.register(signal_id="x", symbol="BTCUSDT", trigger_tf="15m", direction=1,
                entry=100.0, sl=98.0, atr=1.0)
    bars = [make_bar(1, 99.0, 100.0, 99.0, 99.5)]
    updates = tm.on_bar_close("BTCUSDT", "15m", bars)
    assert updates == []
```

- [ ] **Step 2: Run, verify FAIL**

Run: `python -m pytest tests/test_trail_manager.py -v`
Expected: AttributeError on `on_bar_close` (and `previous_sl` field)

- [ ] **Step 3: Implement `on_bar_close` and update record**

Append to `trail_manager.py`:

```python
@dataclass
class TrailUpdate:
    signal_id: str
    symbol: str
    trigger_tf: str
    direction: int
    previous_sl: float
    new_sl: float


def _new_sl_long(prev_low: float, atr_val: float) -> float:
    return prev_low - atr_val * V2_TRAIL_ATR_BUFFER


def _new_sl_short(prev_high: float, atr_val: float) -> float:
    return prev_high + atr_val * V2_TRAIL_ATR_BUFFER


# Patch onto class:
def _on_bar_close(self, symbol: str, tf: str, bars) -> List[TrailUpdate]:
    if len(bars) < 2:
        return []
    prev = bars[-2]
    updates: List[TrailUpdate] = []
    for state in list(self._states.values()):
        if state.closed:
            continue
        if state.symbol != symbol or state.trigger_tf != tf:
            continue
        if state.direction == 1:
            candidate = _new_sl_long(prev.low, state.atr)
            if candidate > state.current_sl:
                prev_sl = state.current_sl
                state.current_sl = candidate
                state.last_update_time = prev.open_time
                updates.append(TrailUpdate(
                    signal_id=state.signal_id, symbol=symbol, trigger_tf=tf,
                    direction=1, previous_sl=prev_sl, new_sl=candidate,
                ))
        else:
            candidate = _new_sl_short(prev.high, state.atr)
            if candidate < state.current_sl:
                prev_sl = state.current_sl
                state.current_sl = candidate
                state.last_update_time = prev.open_time
                updates.append(TrailUpdate(
                    signal_id=state.signal_id, symbol=symbol, trigger_tf=tf,
                    direction=-1, previous_sl=prev_sl, new_sl=candidate,
                ))
    return updates


TrailManager.on_bar_close = _on_bar_close
```

- [ ] **Step 4: Run, verify PASS**

Run: `python -m pytest tests/test_trail_manager.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add trail_manager.py tests/test_trail_manager.py
git commit -m "feat(v2): trail ratchet on bar close"
git push
```

---

## Task 14: Touch-based stop check (TDD)

**Files:**
- Modify: `trail_manager.py` (add `check_stop_hit`)
- Modify: `tests/test_trail_manager.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_trail_manager.py`:

```python
def test_long_stop_hit_when_price_touches_sl():
    tm = TrailManager()
    tm.register(signal_id="x", symbol="BTCUSDT", trigger_tf="15m", direction=1,
                entry=100.0, sl=99.0, atr=1.0)
    stops = tm.check_stop_hit("BTCUSDT", last_price=99.0)
    assert len(stops) == 1
    assert stops[0].signal_id == "x"
    state = tm.get("x")
    assert state.closed is True


def test_long_no_stop_when_price_above_sl():
    tm = TrailManager()
    tm.register(signal_id="x", symbol="BTCUSDT", trigger_tf="15m", direction=1,
                entry=100.0, sl=99.0, atr=1.0)
    stops = tm.check_stop_hit("BTCUSDT", last_price=99.5)
    assert stops == []
    state = tm.get("x")
    assert state.closed is False


def test_short_stop_hit_when_price_at_or_above_sl():
    tm = TrailManager()
    tm.register(signal_id="x", symbol="BTCUSDT", trigger_tf="15m", direction=-1,
                entry=100.0, sl=101.0, atr=1.0)
    stops = tm.check_stop_hit("BTCUSDT", last_price=101.0)
    assert len(stops) == 1


def test_check_stop_filters_by_symbol():
    tm = TrailManager()
    tm.register(signal_id="x", symbol="ETHUSDT", trigger_tf="15m", direction=1,
                entry=100.0, sl=99.0, atr=1.0)
    stops = tm.check_stop_hit("BTCUSDT", last_price=98.0)
    assert stops == []


def test_check_stop_skips_already_closed():
    tm = TrailManager()
    tm.register(signal_id="x", symbol="BTCUSDT", trigger_tf="15m", direction=1,
                entry=100.0, sl=99.0, atr=1.0)
    tm.check_stop_hit("BTCUSDT", last_price=99.0)  # closes it
    stops = tm.check_stop_hit("BTCUSDT", last_price=99.0)  # should be no-op
    assert stops == []
```

- [ ] **Step 2: Run, verify FAIL**

Run: `python -m pytest tests/test_trail_manager.py -v`
Expected: AttributeError on `check_stop_hit`

- [ ] **Step 3: Implement `check_stop_hit`**

Append to `trail_manager.py`:

```python
@dataclass
class TrailStop:
    signal_id: str
    symbol: str
    direction: int
    sl_at_stop: float
    last_price: float


def _check_stop_hit(self, symbol: str, last_price: float) -> List[TrailStop]:
    stops: List[TrailStop] = []
    for state in self._states.values():
        if state.closed:
            continue
        if state.symbol != symbol:
            continue
        hit = (
            (state.direction == 1 and last_price <= state.current_sl)
            or (state.direction == -1 and last_price >= state.current_sl)
        )
        if hit:
            state.closed = True
            state.close_reason = "trail_stop"
            stops.append(TrailStop(
                signal_id=state.signal_id, symbol=symbol,
                direction=state.direction, sl_at_stop=state.current_sl,
                last_price=last_price,
            ))
    return stops


TrailManager.check_stop_hit = _check_stop_hit
```

- [ ] **Step 4: Run, verify PASS**

Run: `python -m pytest tests/test_trail_manager.py -v`
Expected: 13 passed

- [ ] **Step 5: Commit**

```bash
git add trail_manager.py tests/test_trail_manager.py
git commit -m "feat(v2): touch-based stop check on trail manager"
git push
```

---

## Task 15: Telegram v2 alert formatter

**Files:**
- Modify: `telegram.py` (append three new functions)

- [ ] **Step 1: Append v2 alert sender to `telegram.py`**

Append at the END of `/Users/joseph/Documents/fvg-alpha-caller/telegram.py`:

```python


# =====================================================
# v2 Strategy Alerts (Multi-TF FVG Touch Confluence)
# =====================================================

def _v2_confluence_stars(score: int) -> str:
    # Max 6 (1+2+3). Render proportional stars for visibility.
    n = max(0, min(score, 6))
    return "⭐" * n if n > 0 else "—"


def _v2_format_indicators(timeframe_bars: dict) -> List[str]:
    """Display-only StochRSI per TF + vol delta. Reuses existing helpers."""
    out: List[str] = []
    try:
        out.extend(_stoch_state_lines(timeframe_bars))
    except Exception:
        pass
    return out


def _v2_format_oi_vol(zone_or_symbol, timeframe_bars: dict) -> List[str]:
    """Wrap _oi_vol_lines; tolerant to passing either FVGZone or symbol str."""
    try:
        if hasattr(zone_or_symbol, "symbol"):
            return _oi_vol_lines(zone_or_symbol, timeframe_bars)
        # Build a tiny stub object with .symbol attribute
        class _S:
            pass
        s = _S(); s.symbol = zone_or_symbol
        return _oi_vol_lines(s, timeframe_bars)
    except Exception:
        return []


def send_v2_alert(signal, timeframe_bars: dict, chart_png: Optional[bytes] = None) -> None:
    """Send a v2 entry alert. `signal` is a strategy_v2.V2Signal instance."""
    direction_emoji = "🟢 LONG" if signal.direction == 1 else "🔴 SHORT"
    status_trade = "NEW LONG" if signal.direction == 1 else "NEW SHORT"
    title = f"({status_trade} - FRESH FVG | {signal.symbol} | {signal.trigger_tf})"

    sl_pct = (signal.sl - signal.entry) / signal.entry * 100 if signal.entry else 0.0

    htf_line_parts = []
    for tf in ("1h", "2h", "4h"):
        mark = "✓" if signal.htf_touches.get(tf) else "·"
        htf_line_parts.append(f"{tf}{mark}")
    htf_line = " ".join(htf_line_parts)

    lines = [
        f"<b>{title}</b>",
        "",
        f"{direction_emoji}",
        f"📍 Entry: <code>{signal.entry:g}</code>",
        f"🛑 SL:    <code>{signal.sl:g}</code> ({sl_pct:+.2f}%)",
        f"🎯 TP:    trail (RR 1:∞)",
        "",
        f"Confluence: {_v2_confluence_stars(signal.confluence_score)}  ({signal.confluence_score}/6)",
        f"Trigger: {signal.trigger_tf} {'bullish' if signal.direction == 1 else 'bearish'} FVG touch",
        f"HTF:     {htf_line}",
        "",
        f"<a href='{_tv_link(signal.symbol, signal.trigger_tf)}'>📊 TradingView</a>",
    ]

    indicator_lines = _v2_format_indicators(timeframe_bars)
    if indicator_lines:
        lines.append("")
        lines.extend(indicator_lines)

    oi_lines = _v2_format_oi_vol(signal.symbol, timeframe_bars)
    if oi_lines:
        lines.append("")
        lines.extend(oi_lines)

    text = "\n".join(lines)
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}

    if chart_png:
        files = {"photo": ("chart.png", chart_png, "image/png")}
        data = {"chat_id": CHAT_ID, "caption": text, "parse_mode": "HTML"}
        try:
            requests.post(TELEGRAM_PHOTO_URL, data=data, files=files, timeout=15)
        except Exception as e:
            logger.error("send_v2_alert (photo) failed: %s", e)
    else:
        try:
            requests.post(TELEGRAM_URL, json=payload, timeout=15)
        except Exception as e:
            logger.error("send_v2_alert failed: %s", e)


def send_v2_trail_update(symbol: str, trigger_tf: str, previous_sl: float, new_sl: float, direction: int) -> None:
    arrow = "→"
    pct = (new_sl - previous_sl) / previous_sl * 100 if previous_sl else 0.0
    title = f"(TRAIL UPDATE | {symbol} | {trigger_tf})"
    text = (
        f"<b>{title}</b>\n"
        f"SL: <code>{previous_sl:g}</code> {arrow} <code>{new_sl:g}</code> ({pct:+.2f}%)"
    )
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    try:
        requests.post(TELEGRAM_URL, json=payload, timeout=15)
    except Exception as e:
        logger.error("send_v2_trail_update failed: %s", e)


def send_v2_stopped(symbol: str, trigger_tf: str, direction: int, entry: float, sl_at_stop: float, last_price: float) -> None:
    pnl_pct = (sl_at_stop - entry) / entry * 100 if entry else 0.0
    if direction == -1:
        pnl_pct = -pnl_pct
    title = f"(STOPPED | {symbol} | {trigger_tf})"
    text = (
        f"<b>{title}</b>\n"
        f"Entry: <code>{entry:g}</code>\n"
        f"Stop:  <code>{sl_at_stop:g}</code>\n"
        f"Last:  <code>{last_price:g}</code>\n"
        f"PnL:   {pnl_pct:+.2f}%"
    )
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    try:
        requests.post(TELEGRAM_URL, json=payload, timeout=15)
    except Exception as e:
        logger.error("send_v2_stopped failed: %s", e)
```

- [ ] **Step 2: Verify imports clean**

Run: `python -c "import telegram; print(hasattr(telegram, 'send_v2_alert'), hasattr(telegram, 'send_v2_trail_update'), hasattr(telegram, 'send_v2_stopped'))"`
Expected: `True True True`

- [ ] **Step 3: Commit**

```bash
git add telegram.py
git commit -m "feat(v2): telegram alert formatters (entry, trail update, stopped)"
git push
```

---

## Task 16: Modify `main.py` — STRATEGY_VERSION branching

**Files:**
- Modify: `main.py` (gate kronos imports + add v2 path)

- [ ] **Step 1: Replace top-of-file imports + add v2 imports**

Replace lines 11-19 of `/Users/joseph/Documents/fvg-alpha-caller/main.py`:

```python
from chart_generator import generate_chart
from config import TIMEFRAMES, STRATEGY_VERSION, KRONOS_ENABLED, V2_COOLDOWN_SEC, V2_TRIGGER_TFS
from fvg_engine import FVGTracker, detect_fvg, calc_strength
from sim_trades import SimTradeStore
from trade_combo import (
    evaluate_trade_setup,
    build_trade_from_kronos,
    v2_decision,
    build_mitigated_breakout,
    build_mitigated_reversal,
)
from feature_extractor import extract_multi_tf, btc_regime
if STRATEGY_VERSION == "v1" and KRONOS_ENABLED:
    import kronos_client
else:
    kronos_client = None  # disabled in v2
from websocket_client import BinanceKlineWS
from strategy_v2 import evaluate_v2_signal
from trail_manager import TrailManager
from cooldown import CooldownStore
```

- [ ] **Step 2: Update telegram imports**

Replace lines 21-29 of `main.py`:

```python
from telegram import (
    send_approach_alert,
    send_mitigated_alert,
    send_new_fvg_alert,
    send_touch_alert,
    send_trade_recap,
    send_snipe_alert,
    send_v2_alert,
    send_v2_trail_update,
    send_v2_stopped,
)
```

- [ ] **Step 3: Add v2 components to `AlphaCaller.__init__`**

Replace `AlphaCaller.__init__` (around line 44-48) with:

```python
    def __init__(self):
        self.tracker = FVGTracker()
        self.poller = BinanceKlineWS(on_bar_close=self._on_bar_close)
        self.sim_store = SimTradeStore()
        self.retest_tracker = RetestTracker()
        # v2 components (no-op in v1 mode)
        self.v2_trail = TrailManager()
        self.v2_cooldown = CooldownStore(window_sec=V2_COOLDOWN_SEC)
```

- [ ] **Step 4: Guard kronos call in `_evaluate_setup_async`**

Replace lines 76-152 (the entire `_evaluate_setup_async` method) so kronos call only runs when enabled. Insert at the very top of the method body (after the docstring) and adjust the kronos block:

```python
    async def _evaluate_setup_async(self, zone, current_price: float):
        """Kronos-only path in v1; in v2 this is unused."""
        if STRATEGY_VERSION == "v2" or not KRONOS_ENABLED or kronos_client is None:
            from trade_combo import TradeSetupResult
            return TradeSetupResult(
                "SKIP: V2_MODE", False, None,
                "v2 mode — kronos disabled", None, {}, {}, source="v2_disabled",
            )
        bars_by_tf = self._timeframe_bars(zone.symbol)
        tf_bars = bars_by_tf.get(zone.tf, [])
        ohlcv = [
            {"open": float(b.open), "high": float(b.high), "low": float(b.low),
             "close": float(b.close), "volume": float(b.volume)}
            for b in tf_bars
        ]
        atr = float(getattr(zone, "atr", 0.0) or 0.0)

        htf_raw = bars_by_tf.get("4h", [])
        htf_bars = [
            {"open": float(b.open), "high": float(b.high), "low": float(b.low),
             "close": float(b.close), "volume": float(b.volume)}
            for b in htf_raw
        ] if htf_raw else None

        kronos = await kronos_client.predict(
            bars=ohlcv,
            current_price=float(current_price),
            atr=atr,
            zone_direction=int(zone.direction),
            symbol=zone.symbol,
            tf=zone.tf,
            htf_bars=htf_bars,
        )
        if kronos is not None:
            setup = build_trade_from_kronos(kronos, zone)
            htf_note = kronos.get("htf_note", "")
            htf_rsi7 = kronos.get("htf_rsi7")
            if (
                not setup.valid
                and kronos.get("direction") == "RANGING"
                and "hard_gate" in htf_note
                and int(zone.direction) == 1
                and htf_rsi7 is not None
            ):
                fade = build_htf_fade_short(zone, float(current_price), htf_rsi7)
                if fade is not None:
                    setup = fade
                    logger.info(
                        "HTF fade short %s %s | 4h_rsi7=%.1f | %s",
                        zone.symbol, zone.tf, htf_rsi7, htf_note,
                    )
        else:
            from trade_combo import TradeSetupResult
            setup = TradeSetupResult(
                "SKIP: KRONOS UNAVAILABLE", False, None,
                "Kronos offline, combo path disabled",
                None, {}, {}, source="kronos",
            )

        try:
            v2 = v2_decision(zone, bars_by_tf)
        except Exception as e:
            logger.warning("v2_decision failed: %s", e)
            v2 = None

        if v2 and not v2["valid"] and setup.valid:
            logger.info("v2 gate blocked %s %s | %s", zone.symbol, zone.tf, v2["reason"])
            from trade_combo import TradeSetupResult
            setup = TradeSetupResult(
                f"SKIP: {v2['reason']}", False, setup.mode,
                v2["reason"], None, {}, {}, source="v2_gate",
            )

        return setup.__class__(
            status=setup.status, valid=setup.valid, mode=setup.mode, reason=setup.reason,
            trade=setup.trade, combo_states=setup.combo_states, sparklines=setup.sparklines,
            source=setup.source, kronos_raw=getattr(setup, "kronos_raw", None),
            predicted_bars=getattr(setup, "predicted_bars", None), v2_decision=v2,
        )
```

- [ ] **Step 5: Add v2 entry path to `_on_bar_close`**

Insert this block at the very TOP of `_on_bar_close` body, immediately after `if len(bars) < 3: return`:

```python
        # =====================================================
        # v2 Strategy Path (multi-TF touch confluence)
        # =====================================================
        if STRATEGY_VERSION == "v2":
            self.tracker.update_buffer(symbol, tf, bars)
            # 1. Detect & store any FVG (any strength) on this bar
            self._v2_capture_fvg(symbol, tf, bars)
            # 2. Trail bookkeeping for any open v2 trades on this trigger TF
            if tf in V2_TRIGGER_TFS:
                self._v2_handle_trail(symbol, tf, bars)
            # 3. Evaluate signal only on trigger TFs (15m / 30m)
            if tf in V2_TRIGGER_TFS:
                self._v2_try_emit_signal(symbol, tf, bars)
            return
```

- [ ] **Step 6: Add the three v2 helper methods to `AlphaCaller`**

Insert these methods inside `class AlphaCaller`, BEFORE `async def _on_bar_close`:

```python
    def _v2_capture_fvg(self, symbol: str, tf: str, bars) -> None:
        """v2 FVG ingest: bypass MIN_STRENGTH_TO_ALERT, store ANY detected FVG."""
        key = (symbol, tf)
        last_time = bars[-1].open_time
        if self.tracker.last_bar_time.get(key) == last_time:
            return
        if key not in self.tracker.last_bar_time:
            self.tracker.last_bar_time[key] = last_time
            return
        self.tracker.last_bar_time[key] = last_time
        fvg = detect_fvg(bars, symbol=symbol)
        if not fvg:
            return
        fvg["symbol"] = symbol
        s = calc_strength(bars, fvg, symbol=symbol, existing_zones=self.tracker.zones)
        from fvg_engine import FVGZone
        zone = FVGZone(
            symbol=symbol, tf=tf, direction=fvg["direction"],
            top=fvg["top"], bottom=fvg["bottom"], size=fvg["size"],
            born_time=fvg["born_time"],
            main_strength=s["main_strength"], bull_strength=s["bull_strength"],
            bear_strength=s["bear_strength"], label=s["label"],
            rsi=s["rsi"], atr=s["atr"], sl=s["sl"], tp1=s["tp1"], tp2=s["tp2"],
            price=s["price"],
            vol_change_pct=s["vol_change_pct"], price_change_pct=s["price_change_pct"],
            candle_body_pct=s["candle_body_pct"], dist_to_zone=s["dist_to_zone"],
            dominance_bias=s["dominance_bias"], btc_trend=s["btc_trend"],
            dominance_state=s["dominance_state"], btc_state=s["btc_state"],
            volume_spike_ratio=s["volume_spike_ratio"],
            displacement_ok=s["displacement_ok"],
            btc_alignment_ok=s["btc_alignment_ok"],
            confluence_tf_count=s["confluence_tf_count"],
            price_change_24h_pct=s["price_change_24h_pct"],
            confirm_score=s["confirm_score"], confirm_label=s["confirm_label"],
        )
        zone_id = f"{symbol}_{tf}_{zone.born_time}_{zone.direction}"
        self.tracker.zones[zone_id] = zone
        self.tracker._save_zones()

    def _v2_handle_trail(self, symbol: str, tf: str, bars) -> None:
        updates = self.v2_trail.on_bar_close(symbol, tf, bars)
        for u in updates:
            send_v2_trail_update(
                symbol=u.symbol, trigger_tf=u.trigger_tf,
                previous_sl=u.previous_sl, new_sl=u.new_sl, direction=u.direction,
            )
            logger.info(
                "v2 trail %s %s %s | %g -> %g",
                u.symbol, u.trigger_tf, "long" if u.direction == 1 else "short",
                u.previous_sl, u.new_sl,
            )
        # Touch-based stop on latest closed bar (use both wicks for conservative check)
        last = bars[-1]
        for probe_price in (last.low, last.high):
            stops = self.v2_trail.check_stop_hit(symbol, last_price=probe_price)
            for st in stops:
                state = self.v2_trail.get(st.signal_id)
                send_v2_stopped(
                    symbol=st.symbol, trigger_tf=state.trigger_tf if state else tf,
                    direction=st.direction, entry=state.entry if state else 0.0,
                    sl_at_stop=st.sl_at_stop, last_price=st.last_price,
                )
                logger.info("v2 stopped %s %s | sl=%g price=%g",
                            st.symbol, st.signal_id, st.sl_at_stop, st.last_price)

    def _v2_try_emit_signal(self, symbol: str, tf: str, bars) -> None:
        bars_by_tf = self._timeframe_bars(symbol)
        sig = evaluate_v2_signal(symbol, self.tracker.zones, bars_by_tf)
        if sig is None:
            return
        if sig.trigger_tf != tf:
            return  # only emit on the originating TF's bar close
        if not self.v2_cooldown.allow(symbol, sig.direction_str):
            logger.info("v2 cooldown skip %s %s", symbol, sig.direction_str)
            return
        signal_id = f"{symbol}_{sig.trigger_tf}_{sig.zone_born_time}_{sig.direction}"
        self.v2_trail.register(
            signal_id=signal_id, symbol=symbol, trigger_tf=sig.trigger_tf,
            direction=sig.direction, entry=sig.entry, sl=sig.sl, atr=sig.atr,
        )
        send_v2_alert(sig, timeframe_bars=bars_by_tf)
        logger.info(
            "v2 signal %s %s %s | score=%d entry=%g sl=%g",
            symbol, sig.trigger_tf, sig.direction_str,
            sig.confluence_score, sig.entry, sig.sl,
        )
```

- [ ] **Step 7: Smoke test imports**

Run: `cd /Users/joseph/Documents/fvg-alpha-caller && STRATEGY_VERSION=v2 KRONOS_ENABLED=false python -c "import main; print('main import ok')"`
Expected: `main import ok`

- [ ] **Step 8: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: all v2 tests pass; existing v1 tests unchanged.

- [ ] **Step 9: Commit**

```bash
git add main.py
git commit -m "feat(v2): main.py STRATEGY_VERSION branch — v2 entry path + trail handling"
git push
```

---

## Task 17: Integration smoke test (manual — local dry run)

**Files:**
- No new files; verify v2 path runs against live Binance.

- [ ] **Step 1: Run with v2 flag locally**

```bash
cd /Users/joseph/Documents/fvg-alpha-caller
STRATEGY_VERSION=v2 KRONOS_ENABLED=false \
  TELEGRAM_BOT_TOKEN=$TELEGRAM_BOT_TOKEN \
  TELEGRAM_CHAT_ID=$TELEGRAM_CHAT_ID \
  DATABASE_URL=$DATABASE_URL \
  python main.py 2>&1 | tee /tmp/v2_smoke.log
```

Let it run 5 minutes. Expected log lines:
- `Alpha Caller (Binance WS + REST fallback) | tfs=5`
- `Bar closed BTCUSDT 15m ...`
- Possibly `v2 signal ...` or `v2 cooldown skip ...`
- NO `kronos` references in any log line

- [ ] **Step 2: Verify zero kronos calls**

```bash
grep -i kronos /tmp/v2_smoke.log || echo "OK: no kronos refs"
```
Expected: `OK: no kronos refs`

- [ ] **Step 3: Stop with Ctrl-C, commit logs (optional)**

If a v2 alert fired and looked correct, save artifact:
```bash
cp /tmp/v2_smoke.log docs/superpowers/specs/v2-smoke-2026-05-07.txt
git add docs/superpowers/specs/v2-smoke-2026-05-07.txt
git commit -m "test(v2): local smoke run log artifact"
git push
```

If smoke run reveals issues, fix in a separate task before proceeding.

---

## Task 18: Coolify cutover runbook

**Files:**
- Create: `docs/superpowers/specs/coolify-v2-apps-cutover.md`

- [ ] **Step 1: Write runbook**

Write `/Users/joseph/Documents/fvg-alpha-caller/docs/superpowers/specs/coolify-v2-apps-cutover.md`:

```markdown
# Coolify v1→v2 Cutover Runbook

**Date:** 2026-05-07
**Owner:** joseph
**Goal:** Bring up `fvg-alpha-v2-app`, `trade-executor-v2`, `telegram-bot-v2` (branch `fvg-v2`); stop legacy v1 services without losing `@campinaz_bot` continuity.

## Prerequisites

- `fvg-v2` branch pushed and CI green.
- All Tasks 1-17 complete in this plan.
- Local v2 smoke test (Task 17) passed.

## Cutover sequence

1. **Coolify: Duplicate Apps from v1.**
   In Coolify UI for each of the 3 existing services:
   - `fvg-alpha-caller-app` → Duplicate → name `fvg-alpha-v2-app`
   - `trade-executor` → Duplicate → name `trade-executor-v2`
   - `telegram-bot` → Duplicate → name `telegram-bot-v2`

   For each duplicate:
   - Source: same GitHub repo `fvg-alpha-caller`.
   - Branch: change to `fvg-v2`.
   - Environment: copy from v1, then add:
     - `STRATEGY_VERSION=v2`
     - `KRONOS_ENABLED=false`
     - `V2_COOLDOWN_SEC=1800`
     - `HTF_TOUCH_LOOKBACK=1`
     - `ATR_BUFFER_V2=0.3`
   - Persistent volume for buffer cache: NEW volume per service (do NOT share with v1). Mount at `/app/data`.
   - Database URL: same as v1 (shared Postgres).
   - Network: same as v1 (Coolify default).

2. **Build v2 services.**
   Trigger build for all 3 v2 Apps. Wait for "Running" status. Check logs for clean startup.

3. **Verify v2-app produces signals (no telegram yet).**
   Tail `fvg-alpha-v2-app` logs:
   ```
   coolify logs fvg-alpha-v2-app --tail=100
   ```
   Expected: bar-close lines, no kronos refs, possibly `v2 signal` events.

4. **Stop v1 telegram-bot first** (to free `@campinaz_bot` polling):
   ```
   coolify stop telegram-bot
   ```
   Wait 30s for polling to drop.

5. **Start v2 telegram-bot:**
   ```
   coolify start telegram-bot-v2
   ```
   Verify polling resumed (check Telegram for /start response).

6. **Stop v1 alpha + executor:**
   ```
   coolify stop fvg-alpha-caller-app
   coolify stop trade-executor
   ```

7. **Monitor for 24h.** Watch:
   - Alert volume on `@campinaz_bot` (expect higher than v1 due to no Kronos gate; cooldown should cap it).
   - SL placement on TradingView vs zone.bottom + 0.3*ATR.
   - Trail update messages firing every 15m/30m bar close on open positions.

## Rollback

1. `coolify stop telegram-bot-v2 fvg-alpha-v2-app trade-executor-v2`
2. `coolify start fvg-alpha-caller-app trade-executor telegram-bot`
3. v1 polling resumes on `@campinaz_bot`. No DB rollback needed.

## Cleanup (after 7-day soak)

1. In Coolify, delete v1 Apps:
   - `fvg-alpha-caller-app`
   - `trade-executor`
   - `telegram-bot`
2. Keep `main` branch — it remains the v1 reference and rollback target if v2 ever needs to be recreated from scratch.
```

- [ ] **Step 2: Commit runbook**

```bash
git add docs/superpowers/specs/coolify-v2-apps-cutover.md
git commit -m "docs(v2): coolify v1->v2 cutover runbook"
git push
```

---

## Task 19: Final spec self-check (manual)

- [ ] **Step 1: Spec coverage walk-through**

Open `docs/superpowers/specs/2026-05-07-fvg-v2-multi-tf-touch-confluence-design.md` side by side with this plan. For each spec section verify a task implements it:

- §3.1 Trigger TF (15m/30m touch) → Task 9, 11
- §3.2 HTF confluence ≥1 → Task 6, 7, 8, 11
- §3.3 Confluence weighting → Task 8 (weights), Task 15 (display)
- §3.4 Direction independence → Task 11 (loops both)
- §4 SL placement → Task 10
- §5 Trail (immediate, wick-by-wick, touch-based) → Task 13, 14
- §6 Short mirror → Task 13, 14, 11 (test_eval_short_mirror)
- §7 Telegram format → Task 15
- §8 What's removed (Kronos disabled) → Task 16 (kronos guarded)
- §9 File-level changes → Tasks 2, 4, 11, 12-14, 15, 16
- §11 Testing strategy → Tasks 3-14 (TDD), Task 17 (smoke)
- §12 Migration / Cutover → Task 18

- [ ] **Step 2: If a spec requirement isn't covered, add a task**

Inspect carefully. If a gap is found, append a follow-up task before Task 19 and re-walk this checklist.

- [ ] **Step 3: Run full test suite + manual smoke once more**

```bash
cd /Users/joseph/Documents/fvg-alpha-caller && python -m pytest tests/ -v
```
Expected: all green.

---

## Out of Scope (deferred to future plans)

- Backtesting harness for v2 (separate plan).
- Database persistence of `TrailManager` state across restarts (currently in-memory; restart = lose open trail positions). Acceptable for v0; user accepted.
- Chart PNG generation for v2 alerts (currently text-only `send_v2_alert(chart_png=None)` path; v1 chart_generator can be wired in later).
- Per-signal sim_trades persistence for v2 (existing `add_sim_trade_raw` could be reused; deferred).
- Removing `confluence_tf_count` v1 field rename — kept as-is, v2 ignores it.
- Alert volume monitoring dashboard (use Telegram chat history during 24h soak).
