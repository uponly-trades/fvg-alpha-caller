# Combo Trade Alerts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add alert-only combo trade setup evaluation on FVG events, with trade mode classification, risk plans, simulated trade tracking, reduced Telegram alerts, chart trade overlays, and scheduled daily recaps.

**Architecture:** Keep FVG as the only trigger. Add a focused `trade_combo.py` module for classification, combo validation, and risk math; add `sim_trades.py` for JSON persistence, status updates, and recap summaries; wire both into `main.py` without changing FVG detection. Telegram and chart code become consumers of an optional trade plan object.

**Tech Stack:** Python 3, dataclasses, JSON file persistence, pytest, matplotlib/mplfinance, existing Binance WebSocket and Telegram modules.

---

## File Structure

- Create `trade_combo.py`
  - Owns trade mode classification, StochRSI combo state evaluation, skip reason selection, and entry/SL/TP risk math.
  - Exposes one public function: `evaluate_trade_setup(zone, current_price, bars_by_tf)`.

- Create `sim_trades.py`
  - Owns `/app/data/sim_trades.json` persistence, trade deduplication, open trade updates from closed candles, and recap summaries.
  - Exposes `SimTradeStore` plus pure helpers for testing.

- Modify `main.py`
  - Instantiate `SimTradeStore`.
  - Update open simulations on every closed candle.
  - Evaluate combo plans on new/approach/touch FVG events.
  - Save only valid trade plans.
  - Send recap messages at session windows.
  - Pass optional trade plan into chart generation and Telegram alerts.

- Modify `telegram.py`
  - Replace verbose new/approach/touch alert text with reduced trade-plan-first format.
  - Support skipped setup labels.
  - Add `send_trade_recap(session_name, recap)`.
  - Fix TradingView interval mapping for `30m` and `2h`.
  - Remove stale `_indicator_block(zone)` references from approach/touch paths.

- Modify `chart_generator.py`
  - Add optional `trade_plan` parameter.
  - Draw entry, SL, TP1, TP2 overlays only when a valid trade plan exists.

- Create `tests/test_trade_combo.py`
  - Test mode classification, combo states, skip reasons, and risk math.

- Create `tests/test_sim_trades.py`
  - Test JSON persistence, duplicate prevention, conservative SL-first status updates, and recap math.

- Modify `tests/test_indicator_context.py`
  - Update Telegram assertions for reduced alert content.
  - Add chart overlay smoke assertion.

---

## Task 1: Combo trade evaluation module

**Files:**
- Create: `trade_combo.py`
- Test: `tests/test_trade_combo.py`

- [ ] **Step 1: Write failing tests for mode classification and missing data**

Add `tests/test_trade_combo.py`:

```python
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
os.environ.setdefault("TELEGRAM_CHAT_ID", "x")

import trade_combo


@dataclass(frozen=True)
class Bar:
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_closed: bool = True


def bars_from_closes(closes):
    return [
        Bar(
            open_time=i * 60_000,
            open=close - 0.2,
            high=close + 1.0,
            low=close - 1.0,
            close=close,
            volume=100 + i,
        )
        for i, close in enumerate(closes)
    ]


def zone(**overrides):
    data = dict(
        symbol="BTCUSDT",
        tf="15m",
        direction=1,
        top=101.0,
        bottom=99.0,
        main_strength=80,
        atr=1.0,
        born_time=123,
    )
    data.update(overrides)
    return SimpleNamespace(**data)


def test_classifies_trade_mode_by_setup_timeframe():
    assert trade_combo.classify_mode("15m") == "scalping"
    assert trade_combo.classify_mode("30m") == "scalping"
    assert trade_combo.classify_mode("1h") == "intraday"
    assert trade_combo.classify_mode("2h") == "intraday"
    assert trade_combo.classify_mode("4h") == "swing"
    assert trade_combo.classify_mode("12h") is None


def test_missing_required_indicator_data_skips_trade_setup():
    result = trade_combo.evaluate_trade_setup(
        zone(tf="15m", direction=1),
        current_price=100.0,
        bars_by_tf={"15m": bars_from_closes([1, 2, 3])},
    )

    assert result.status == "SKIP: MISSING DATA"
    assert result.valid is False
    assert result.mode == "scalping"
    assert result.trade is None
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
cd /Users/joseph/Documents/fvg-alpha-caller && pytest tests/test_trade_combo.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'trade_combo'`.

- [ ] **Step 3: Add minimal `trade_combo.py` skeleton**

Create `trade_combo.py`:

```python
from dataclasses import dataclass
from typing import Dict, List, Optional

from config import MIN_STRENGTH_TO_ALERT
from indicator_context import stochrsi_series


MODE_TIMEFRAMES = {
    "scalping": ("15m", "30m"),
    "intraday": ("1h", "2h"),
    "swing": ("2h", "4h"),
}

COMBO_TIMEFRAMES = {
    "scalping": ("15m", "30m", "1h"),
    "intraday": ("30m", "1h", "2h", "4h"),
    "swing": ("1h", "2h", "4h"),
}


@dataclass(frozen=True)
class TradeLevels:
    direction: str
    entry: float
    sl: float
    tp1: float
    tp2: float
    rr: float


@dataclass(frozen=True)
class TradeSetupResult:
    status: str
    valid: bool
    mode: Optional[str]
    reason: str
    trade: Optional[TradeLevels]
    combo_states: Dict[str, str]


def classify_mode(tf: str) -> Optional[str]:
    for mode, timeframes in MODE_TIMEFRAMES.items():
        if tf in timeframes:
            return mode
    return None


def _latest_stoch_state(bars, direction: int) -> Optional[str]:
    closes = [float(bar.close) for bar in bars]
    k_values, d_values = stochrsi_series(closes)
    pairs = [(k, d) for k, d in zip(k_values, d_values) if k is not None and d is not None]
    if len(pairs) < 2:
        return None

    prev_k, prev_d = pairs[-2]
    k, d = pairs[-1]
    if direction == 1:
        if k <= 30 and d <= 30:
            return "long"
        if prev_k <= prev_d and k > d and min(prev_k, prev_d, k, d) <= 40:
            return "long"
        if k >= 70 and d >= 70:
            return "short"
    else:
        if k >= 70 and d >= 70:
            return "short"
        if prev_k >= prev_d and k < d and max(prev_k, prev_d, k, d) >= 60:
            return "short"
        if k <= 30 and d <= 30:
            return "long"
    return "neutral"


def _price_too_far(zone, current_price: float) -> bool:
    zone_size = abs(float(zone.top) - float(zone.bottom))
    if zone_size <= 0:
        return True
    if float(zone.bottom) <= current_price <= float(zone.top):
        return False
    if current_price > float(zone.top):
        distance = current_price - float(zone.top)
    else:
        distance = float(zone.bottom) - current_price
    return distance > zone_size


def _build_trade_levels(zone, current_price: float) -> Optional[TradeLevels]:
    entry = float(current_price)
    atr = float(getattr(zone, "atr", 0.0) or 0.0)
    buffer = atr * 0.1
    if buffer <= 0:
        buffer = abs(float(zone.top) - float(zone.bottom)) * 0.1

    if int(zone.direction) == 1:
        sl = float(zone.bottom) - buffer
        risk = entry - sl
        if risk <= 0:
            return None
        return TradeLevels(
            direction="long",
            entry=entry,
            sl=sl,
            tp1=entry + risk,
            tp2=entry + risk * 2,
            rr=2.0,
        )

    sl = float(zone.top) + buffer
    risk = sl - entry
    if risk <= 0:
        return None
    return TradeLevels(
        direction="short",
        entry=entry,
        sl=sl,
        tp1=entry - risk,
        tp2=entry - risk * 2,
        rr=2.0,
    )


def evaluate_trade_setup(zone, current_price: float, bars_by_tf: Dict[str, List]) -> TradeSetupResult:
    mode = classify_mode(zone.tf)
    if mode is None:
        return TradeSetupResult("SKIP: MISSING DATA", False, None, "unsupported timeframe", None, {})

    if int(getattr(zone, "main_strength", 0)) < MIN_STRENGTH_TO_ALERT:
        return TradeSetupResult("SKIP: WEAK FVG", False, mode, "FVG strength below alert threshold", None, {})

    required_tfs = COMBO_TIMEFRAMES[mode]
    combo_states = {}
    for tf in required_tfs:
        state = _latest_stoch_state(bars_by_tf.get(tf, []), int(zone.direction))
        if state is None:
            return TradeSetupResult("SKIP: MISSING DATA", False, mode, f"missing StochRSI data for {tf}", None, combo_states)
        combo_states[tf] = state

    desired = "long" if int(zone.direction) == 1 else "short"
    matches = sum(1 for state in combo_states.values() if state == desired)
    conflicts = sum(1 for state in combo_states.values() if state not in {desired, "neutral"})
    if conflicts or matches < max(2, len(required_tfs) - 1):
        return TradeSetupResult("SKIP: MIXED COMBO", False, mode, "combo timeframes are mixed", None, combo_states)

    if _price_too_far(zone, current_price):
        return TradeSetupResult("SKIP: FAR FROM FVG", False, mode, "price is too far from FVG zone", None, combo_states)

    trade = _build_trade_levels(zone, current_price)
    if trade is None:
        return TradeSetupResult("SKIP: INVALID RISK", False, mode, "risk is zero or invalid", None, combo_states)

    direction_text = "LONG" if int(zone.direction) == 1 else "SHORT"
    reason = f"{desired} FVG with aligned StochRSI combo"
    return TradeSetupResult(f"{direction_text} VALID", True, mode, reason, trade, combo_states)
```

- [ ] **Step 4: Run tests to verify current tests pass**

Run:

```bash
cd /Users/joseph/Documents/fvg-alpha-caller && pytest tests/test_trade_combo.py -v
```

Expected: PASS for first two tests.

- [ ] **Step 5: Add tests for valid long, valid short, far price, weak FVG, and invalid risk**

Append to `tests/test_trade_combo.py`:

```python

def aligned_bars():
    return bars_from_closes([
        100, 99, 98, 97, 96, 95, 94, 93, 92, 91,
        90, 89, 88, 87, 86, 85, 84, 83, 82, 81,
        80, 79, 78, 77, 76, 75, 76, 77, 78, 79,
        80, 81, 82, 83, 84, 85, 86, 87, 88, 89,
    ])


def overbought_bars():
    return bars_from_closes([
        50, 51, 52, 53, 54, 55, 56, 57, 58, 59,
        60, 61, 62, 63, 64, 65, 66, 67, 68, 69,
        70, 71, 72, 73, 74, 75, 74, 73, 72, 71,
        70, 69, 68, 67, 66, 65, 64, 63, 62, 61,
    ])


def test_bullish_fvg_with_aligned_combo_builds_long_risk_plan(monkeypatch):
    monkeypatch.setattr(
        trade_combo,
        "_latest_stoch_state",
        lambda bars, direction: "long",
    )
    result = trade_combo.evaluate_trade_setup(
        zone(tf="15m", direction=1, top=101.0, bottom=99.0, atr=1.0),
        current_price=100.0,
        bars_by_tf={"15m": aligned_bars(), "30m": aligned_bars(), "1h": aligned_bars()},
    )

    assert result.status == "LONG VALID"
    assert result.valid is True
    assert result.mode == "scalping"
    assert result.trade.direction == "long"
    assert result.trade.entry == 100.0
    assert result.trade.sl == 98.9
    assert result.trade.tp1 == 101.1
    assert result.trade.tp2 == 102.2
    assert result.trade.rr == 2.0


def test_bearish_fvg_with_aligned_combo_builds_short_risk_plan(monkeypatch):
    monkeypatch.setattr(
        trade_combo,
        "_latest_stoch_state",
        lambda bars, direction: "short",
    )
    result = trade_combo.evaluate_trade_setup(
        zone(tf="1h", direction=-1, top=101.0, bottom=99.0, atr=1.0),
        current_price=100.0,
        bars_by_tf={
            "30m": overbought_bars(),
            "1h": overbought_bars(),
            "2h": overbought_bars(),
            "4h": overbought_bars(),
        },
    )

    assert result.status == "SHORT VALID"
    assert result.valid is True
    assert result.mode == "intraday"
    assert result.trade.direction == "short"
    assert result.trade.entry == 100.0
    assert result.trade.sl == 101.1
    assert result.trade.tp1 == 98.9
    assert result.trade.tp2 == 97.8


def test_mixed_combo_skips_trade(monkeypatch):
    states = iter(["long", "short", "neutral"])
    monkeypatch.setattr(trade_combo, "_latest_stoch_state", lambda bars, direction: next(states))

    result = trade_combo.evaluate_trade_setup(
        zone(tf="15m", direction=1),
        current_price=100.0,
        bars_by_tf={"15m": aligned_bars(), "30m": aligned_bars(), "1h": aligned_bars()},
    )

    assert result.status == "SKIP: MIXED COMBO"
    assert result.valid is False


def test_far_from_fvg_skips_trade(monkeypatch):
    monkeypatch.setattr(trade_combo, "_latest_stoch_state", lambda bars, direction: "long")

    result = trade_combo.evaluate_trade_setup(
        zone(tf="15m", direction=1, top=101.0, bottom=99.0),
        current_price=104.5,
        bars_by_tf={"15m": aligned_bars(), "30m": aligned_bars(), "1h": aligned_bars()},
    )

    assert result.status == "SKIP: FAR FROM FVG"


def test_weak_fvg_skips_before_combo_validation():
    result = trade_combo.evaluate_trade_setup(
        zone(tf="15m", direction=1, main_strength=20),
        current_price=100.0,
        bars_by_tf={},
    )

    assert result.status == "SKIP: WEAK FVG"


def test_invalid_risk_skips_trade(monkeypatch):
    monkeypatch.setattr(trade_combo, "_latest_stoch_state", lambda bars, direction: "long")

    result = trade_combo.evaluate_trade_setup(
        zone(tf="15m", direction=1, top=101.0, bottom=100.0, atr=0.0),
        current_price=99.0,
        bars_by_tf={"15m": aligned_bars(), "30m": aligned_bars(), "1h": aligned_bars()},
    )

    assert result.status == "SKIP: INVALID RISK"
```

- [ ] **Step 6: Run tests to verify failure or pass**

Run:

```bash
cd /Users/joseph/Documents/fvg-alpha-caller && pytest tests/test_trade_combo.py -v
```

Expected: PASS. If float formatting causes tiny precision differences, use `pytest.approx()` in assertions rather than changing risk math.

- [ ] **Step 7: Commit Task 1**

Run:

```bash
git add trade_combo.py tests/test_trade_combo.py && git commit -m "feat: add combo trade evaluation"
```

---

## Task 2: Simulation trade storage and recap module

**Files:**
- Create: `sim_trades.py`
- Test: `tests/test_sim_trades.py`

- [ ] **Step 1: Write failing tests for persistence and duplicate prevention**

Create `tests/test_sim_trades.py`:

```python
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
os.environ.setdefault("TELEGRAM_CHAT_ID", "x")

from sim_trades import SimTradeStore


@dataclass(frozen=True)
class Bar:
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_closed: bool = True


def trade(direction="long", created_at=1777899600000):
    levels = SimpleNamespace(
        direction=direction,
        entry=100.0,
        sl=98.0 if direction == "long" else 102.0,
        tp1=102.0 if direction == "long" else 98.0,
        tp2=104.0 if direction == "long" else 96.0,
    )
    return SimpleNamespace(
        status="LONG VALID" if direction == "long" else "SHORT VALID",
        mode="scalping",
        reason="aligned combo",
        trade=levels,
    )


def zone(direction=1):
    return SimpleNamespace(symbol="BTCUSDT", tf="15m", direction=direction, born_time=1777899600000)


def test_add_valid_trade_persists_json_record(tmp_path):
    store = SimTradeStore(tmp_path / "sim_trades.json")

    added = store.add_trade(zone(), trade("long"), created_at=1777899600000)
    records = store.load()

    assert added is True
    assert len(records) == 1
    assert records[0]["id"] == "BTCUSDT-15m-1777899600000"
    assert records[0]["date"] == "2026-05-04"
    assert records[0]["symbol"] == "BTCUSDT"
    assert records[0]["mode"] == "scalping"
    assert records[0]["direction"] == "long"
    assert records[0]["entry"] == 100.0
    assert records[0]["sl"] == 98.0
    assert records[0]["tp1"] == 102.0
    assert records[0]["tp2"] == 104.0
    assert records[0]["status"] == "open"
    assert records[0]["closed_at"] is None


def test_add_trade_deduplicates_by_id(tmp_path):
    store = SimTradeStore(tmp_path / "sim_trades.json")

    assert store.add_trade(zone(), trade("long"), created_at=1777899600000) is True
    assert store.add_trade(zone(), trade("long"), created_at=1777899600000) is False

    assert len(store.load()) == 1
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
cd /Users/joseph/Documents/fvg-alpha-caller && pytest tests/test_sim_trades.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'sim_trades'`.

- [ ] **Step 3: Add `sim_trades.py` persistence implementation**

Create `sim_trades.py`:

```python
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

DEFAULT_SIM_TRADES_PATH = Path("/app/data/sim_trades.json")


class SimTradeStore:
    def __init__(self, path: Path = DEFAULT_SIM_TRADES_PATH):
        self.path = Path(path)

    def load(self) -> List[Dict]:
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return []
        return data

    def save(self, records: List[Dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(records, f, indent=2, sort_keys=True)
        tmp_path.replace(self.path)

    def add_trade(self, zone, setup, created_at: int) -> bool:
        if not getattr(setup, "valid", True) and not getattr(setup, "trade", None):
            return False
        trade = setup.trade
        if trade is None:
            return False

        trade_id = f"{zone.symbol}-{zone.tf}-{int(created_at)}"
        records = self.load()
        if any(record.get("id") == trade_id for record in records):
            return False

        created_dt = datetime.fromtimestamp(int(created_at) / 1000, tz=timezone.utc)
        records.append({
            "id": trade_id,
            "date": created_dt.date().isoformat(),
            "symbol": zone.symbol,
            "mode": setup.mode,
            "tf": zone.tf,
            "direction": trade.direction,
            "entry": trade.entry,
            "sl": trade.sl,
            "tp1": trade.tp1,
            "tp2": trade.tp2,
            "status": "open",
            "created_at": int(created_at),
            "closed_at": None,
            "reason": setup.reason,
        })
        self.save(records)
        return True
```

- [ ] **Step 4: Run persistence tests**

Run:

```bash
cd /Users/joseph/Documents/fvg-alpha-caller && pytest tests/test_sim_trades.py -v
```

Expected: PASS for first two tests.

- [ ] **Step 5: Add tests for conservative status updates and recap summaries**

Append to `tests/test_sim_trades.py`:

```python

def test_long_trade_counts_sl_first_when_sl_and_tp_touch_same_candle(tmp_path):
    store = SimTradeStore(tmp_path / "sim_trades.json")
    store.add_trade(zone(), trade("long"), created_at=1777899600000)

    updated = store.update_open_trades("BTCUSDT", Bar(1777899660000, 100, 105, 97, 101, 100))
    record = store.load()[0]

    assert updated == 1
    assert record["status"] == "loss"
    assert record["closed_at"] == 1777899660000


def test_long_trade_updates_to_tp1_then_win(tmp_path):
    store = SimTradeStore(tmp_path / "sim_trades.json")
    store.add_trade(zone(), trade("long"), created_at=1777899600000)

    assert store.update_open_trades("BTCUSDT", Bar(1777899660000, 100, 102.5, 99, 102, 100)) == 1
    assert store.load()[0]["status"] == "tp1_hit"
    assert store.load()[0]["closed_at"] is None

    assert store.update_open_trades("BTCUSDT", Bar(1777899720000, 102, 104.5, 101, 104, 100)) == 1
    record = store.load()[0]
    assert record["status"] == "win"
    assert record["closed_at"] == 1777899720000


def test_short_trade_updates_to_loss_before_tp(tmp_path):
    store = SimTradeStore(tmp_path / "sim_trades.json")
    store.add_trade(zone(direction=-1), trade("short"), created_at=1777899600000)

    updated = store.update_open_trades("BTCUSDT", Bar(1777899660000, 100, 102.5, 95.5, 99, 100))
    record = store.load()[0]

    assert updated == 1
    assert record["status"] == "loss"
    assert record["closed_at"] == 1777899660000


def test_daily_recap_counts_today_statuses_and_recent_trades(tmp_path):
    store = SimTradeStore(tmp_path / "sim_trades.json")
    records = [
        {"id": "a", "date": "2026-05-04", "symbol": "BTCUSDT", "tf": "15m", "direction": "long", "entry": 100, "sl": 98, "tp1": 102, "tp2": 104, "status": "open", "created_at": 1, "closed_at": None, "mode": "scalping", "reason": "x"},
        {"id": "b", "date": "2026-05-04", "symbol": "ETHUSDT", "tf": "1h", "direction": "short", "entry": 100, "sl": 102, "tp1": 98, "tp2": 96, "status": "tp1_hit", "created_at": 2, "closed_at": None, "mode": "intraday", "reason": "x"},
        {"id": "c", "date": "2026-05-04", "symbol": "SOLUSDT", "tf": "4h", "direction": "long", "entry": 100, "sl": 98, "tp1": 102, "tp2": 104, "status": "win", "created_at": 3, "closed_at": 4, "mode": "swing", "reason": "x"},
        {"id": "d", "date": "2026-05-04", "symbol": "XRPUSDT", "tf": "15m", "direction": "short", "entry": 100, "sl": 102, "tp1": 98, "tp2": 96, "status": "loss", "created_at": 5, "closed_at": 6, "mode": "scalping", "reason": "x"},
    ]
    store.save(records)

    recap = store.daily_recap("2026-05-04")

    assert recap["open"] == 1
    assert recap["tp1"] == 1
    assert recap["win"] == 1
    assert recap["loss"] == 1
    assert recap["closed_winrate"] == 50.0
    assert recap["recent"][0]["symbol"] == "XRPUSDT"
```

- [ ] **Step 6: Implement status updates and recap summaries**

Append methods inside `SimTradeStore` in `sim_trades.py`:

```python
    def update_open_trades(self, symbol: str, bar) -> int:
        records = self.load()
        updated = 0
        for record in records:
            if record.get("symbol") != symbol:
                continue
            if record.get("status") not in {"open", "tp1_hit"}:
                continue
            new_status = _next_status(record, bar)
            if new_status and new_status != record["status"]:
                record["status"] = new_status
                if new_status in {"win", "loss"}:
                    record["closed_at"] = int(bar.open_time)
                updated += 1
        if updated:
            self.save(records)
        return updated

    def daily_recap(self, date: Optional[str] = None) -> Dict:
        if date is None:
            date = datetime.now(timezone.utc).date().isoformat()
        records = [record for record in self.load() if record.get("date") == date]
        open_count = sum(1 for record in records if record.get("status") == "open")
        tp1_count = sum(1 for record in records if record.get("status") == "tp1_hit")
        win_count = sum(1 for record in records if record.get("status") == "win")
        loss_count = sum(1 for record in records if record.get("status") == "loss")
        closed = win_count + loss_count
        winrate = round((win_count / closed) * 100, 1) if closed else 0.0
        recent = sorted(records, key=lambda record: record.get("created_at", 0), reverse=True)[:5]
        return {
            "date": date,
            "open": open_count,
            "tp1": tp1_count,
            "win": win_count,
            "loss": loss_count,
            "closed_winrate": winrate,
            "recent": recent,
        }
```

Add module-level helper below the class:

```python

def _next_status(record: Dict, bar) -> Optional[str]:
    direction = record.get("direction")
    high = float(bar.high)
    low = float(bar.low)
    sl = float(record["sl"])
    tp1 = float(record["tp1"])
    tp2 = float(record["tp2"])

    if direction == "long":
        if low <= sl:
            return "loss"
        if high >= tp2:
            return "win"
        if record.get("status") == "open" and high >= tp1:
            return "tp1_hit"
        return None

    if high >= sl:
        return "loss"
    if low <= tp2:
        return "win"
    if record.get("status") == "open" and low <= tp1:
        return "tp1_hit"
    return None
```

- [ ] **Step 7: Run sim trade tests**

Run:

```bash
cd /Users/joseph/Documents/fvg-alpha-caller && pytest tests/test_sim_trades.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit Task 2**

Run:

```bash
git add sim_trades.py tests/test_sim_trades.py && git commit -m "feat: add simulated trade storage"
```

---

## Task 3: Reduced Telegram trade alerts and recap sender

**Files:**
- Modify: `telegram.py`
- Test: `tests/test_indicator_context.py`

- [ ] **Step 1: Add failing tests for reduced trade alert content and recap text**

Append to `tests/test_indicator_context.py`:

```python

def test_trade_plan_alert_uses_reduced_trade_content(monkeypatch):
    import telegram

    class Zone:
        direction = 1
        label = "Strong Bullish Imbalance"
        symbol = "BTCUSDT"
        tf = "30m"
        price = 100.0
        bottom = 99.0
        top = 101.0
        main_strength = 80
        atr = 1.2
        vol_change_pct = 10.0
        price_change_pct = 1.0
        price_change_24h_pct = 2.0
        dominance_state = "ALT"
        btc_state = "UP"
        dominance_bias = -0.01
        btc_trend = 0.01
        confirm_score = 80
        confirm_label = "A+"
        indicator_context = "StochRSI should not render"

    setup = SimpleNamespace(
        status="LONG VALID",
        valid=True,
        mode="scalping",
        reason="long FVG with aligned StochRSI combo",
        trade=SimpleNamespace(direction="long", entry=100.0, sl=98.9, tp1=101.1, tp2=102.2, rr=2.0),
    )
    sent = {}
    monkeypatch.setattr(telegram, "_send", lambda text: sent.setdefault("text", text) or True)

    telegram.send_new_fvg_alert(Zone(), trade_setup=setup)

    text = sent["text"]
    assert "LONG VALID - BULLISH FVG | BTCUSDT | 30m" in text
    assert "Entry: 100.0" in text
    assert "SL: 98.9" in text
    assert "TP1: 101.1" in text
    assert "TP2: 102.2" in text
    assert "RR: 1:2" in text
    assert "Mode: scalping" in text
    assert "Zone: 99.0 — 101.0" in text
    assert "Strength: 80%" in text
    assert "Reason: long FVG with aligned StochRSI combo" in text
    assert "interval=30" in text
    assert "StochRSI should not render" not in text
    assert "Vol Change" not in text
    assert "BTCDOM" not in text


def test_skipped_trade_alert_renders_skip_reason(monkeypatch):
    import telegram

    zone = SimpleNamespace(direction=-1, symbol="ETHUSDT", tf="2h", bottom=99.0, top=101.0, main_strength=80)
    setup = SimpleNamespace(status="SKIP: MIXED COMBO", valid=False, mode="intraday", reason="combo timeframes are mixed", trade=None)
    sent = {}
    monkeypatch.setattr(telegram, "_send", lambda text: sent.setdefault("text", text) or True)

    telegram.send_touch_alert(zone, 100.0, trade_setup=setup)

    text = sent["text"]
    assert "SKIP: MIXED COMBO - BEARISH FVG | ETHUSDT | 2h" in text
    assert "Entry:" not in text
    assert "Skip Reason: combo timeframes are mixed" in text
    assert "interval=120" in text


def test_send_trade_recap_formats_daily_summary(monkeypatch):
    import telegram

    sent = {}
    monkeypatch.setattr(telegram, "_send", lambda text: sent.setdefault("text", text) or True)

    telegram.send_trade_recap("Siang", {
        "open": 4,
        "tp1": 2,
        "win": 1,
        "loss": 1,
        "closed_winrate": 50.0,
        "recent": [
            {"direction": "long", "symbol": "BTCUSDT", "tf": "15m", "entry": 100.0, "sl": 98.0, "tp1": 102.0, "tp2": 104.0, "status": "tp1_hit"}
        ],
    })

    text = sent["text"]
    assert "Trade Recap — Siang" in text
    assert "Open: 4" in text
    assert "TP1: 2" in text
    assert "Win TP2: 1" in text
    assert "Loss: 1" in text
    assert "Closed Winrate: 50.0%" in text
    assert "LONG VALID - BTCUSDT 15m" in text
    assert "Status: TP1" in text
```

- [ ] **Step 2: Run focused tests to verify failure**

Run:

```bash
cd /Users/joseph/Documents/fvg-alpha-caller && pytest tests/test_indicator_context.py::test_trade_plan_alert_uses_reduced_trade_content tests/test_indicator_context.py::test_skipped_trade_alert_renders_skip_reason tests/test_indicator_context.py::test_send_trade_recap_formats_daily_summary -v
```

Expected: FAIL because alert functions do not accept `trade_setup` and recap sender does not exist.

- [ ] **Step 3: Update `telegram.py` signatures and helper functions**

Modify imports if needed:

```python
from typing import Optional
```

Replace `_tv_link()` with:

```python
def _tv_link(symbol: str, tf: str) -> str:
    interval_map = {"15m": "15", "30m": "30", "1h": "60", "2h": "120", "4h": "240"}
    iv = interval_map.get(tf, "60")
    tv_symbol = f"{symbol}.P"
    return f"https://www.tradingview.com/chart/?symbol=BINANCE:{tv_symbol}&interval={iv}"
```

Add helpers above alert senders:

```python
def _fvg_direction_text(zone) -> str:
    return "BULLISH" if int(zone.direction) == 1 else "BEARISH"


def _trade_title(zone, trade_setup) -> str:
    return f"{trade_setup.status} - {_fvg_direction_text(zone)} FVG | {zone.symbol} | {zone.tf}"


def _format_trade_alert(zone, current_price: float, trade_setup) -> str:
    tv_url = _tv_link(zone.symbol, zone.tf)
    lines = [
        f"<b>{_trade_title(zone, trade_setup)}</b>",
        "",
    ]
    if trade_setup.trade is not None:
        trade = trade_setup.trade
        lines.extend([
            f"Entry: {trade.entry}",
            f"SL: {trade.sl}",
            f"TP1: {trade.tp1}",
            f"TP2: {trade.tp2}",
            "RR: 1:2",
        ])
    else:
        lines.append(f"Price: {current_price}")
        lines.append(f"Skip Reason: {trade_setup.reason}")

    lines.extend([
        f"Mode: {trade_setup.mode}",
        f"Zone: {zone.bottom} — {zone.top}",
        f"Strength: {zone.main_strength}%",
    ])
    if trade_setup.trade is not None:
        lines.append(f"Reason: {trade_setup.reason}")
    lines.append("")
    lines.append(f"<a href='{tv_url}'>Open TradingView</a>")
    return "\n".join(lines)
```

- [ ] **Step 4: Update alert function signatures and branch on `trade_setup`**

Change signatures:

```python
def send_new_fvg_alert(zone, chart_png: Optional[bytes] = None, trade_setup=None) -> bool:
```

```python
def send_approach_alert(zone, current_price: float, chart_png: Optional[bytes] = None, trade_setup=None) -> bool:
```

```python
def send_touch_alert(zone, current_price: float, chart_png: Optional[bytes] = None, trade_setup=None) -> bool:
```

At top of each function body, add:

```python
    if trade_setup is not None:
        msg = _format_trade_alert(zone, getattr(zone, "price", current_price), trade_setup)
        if chart_png:
            return _send_photo(msg, chart_png)
        return _send(msg)
```

For `send_new_fvg_alert()`, use `getattr(zone, "price", 0.0)` because there is no `current_price` argument:

```python
    if trade_setup is not None:
        msg = _format_trade_alert(zone, getattr(zone, "price", 0.0), trade_setup)
        if chart_png:
            return _send_photo(msg, chart_png)
        return _send(msg)
```

Remove stale lines from `send_approach_alert()` and `send_touch_alert()`:

```python
    indicator_block = _indicator_block(zone)
```

- [ ] **Step 5: Add recap sender**

Append before `_send()`:

```python
def send_trade_recap(session_name: str, recap: dict) -> bool:
    lines = [
        f"<b>Trade Recap — {session_name}</b>",
        "",
        f"Open: {recap['open']}",
        f"TP1: {recap['tp1']}",
        f"Win TP2: {recap['win']}",
        f"Loss: {recap['loss']}",
        f"Closed Winrate: {recap['closed_winrate']}%",
    ]
    recent = recap.get("recent", [])
    if recent:
        lines.extend(["", "Recent:"])
        for record in recent:
            direction = "LONG" if record.get("direction") == "long" else "SHORT"
            status = str(record.get("status", "")).upper().replace("TP1_HIT", "TP1")
            lines.extend([
                f"{direction} VALID - {record['symbol']} {record['tf']}",
                f"Entry {record['entry']} | SL {record['sl']} | TP1 {record['tp1']} | TP2 {record['tp2']}",
                f"Status: {status}",
            ])
    return _send("\n".join(lines))
```

- [ ] **Step 6: Run Telegram tests**

Run:

```bash
cd /Users/joseph/Documents/fvg-alpha-caller && pytest tests/test_indicator_context.py::test_zone_indicator_context_text_is_not_rendered_in_alert tests/test_indicator_context.py::test_trade_plan_alert_uses_reduced_trade_content tests/test_indicator_context.py::test_skipped_trade_alert_renders_skip_reason tests/test_indicator_context.py::test_send_trade_recap_formats_daily_summary -v
```

Expected: PASS.

- [ ] **Step 7: Commit Task 3**

Run:

```bash
git add telegram.py tests/test_indicator_context.py && git commit -m "feat: reduce combo trade Telegram alerts"
```

---

## Task 4: Chart trade overlays

**Files:**
- Modify: `chart_generator.py`
- Modify: `tests/test_indicator_context.py`

- [ ] **Step 1: Add failing chart overlay smoke test**

Append to `tests/test_indicator_context.py`:

```python

def test_chart_generator_draws_trade_plan_overlays(monkeypatch):
    bars = make_bars([10, 11, 12, 13, 14, 13, 12, 11, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 20, 19, 18, 19, 20, 21, 22, 23, 24, 23, 22, 24, 25, 26, 27, 28, 29, 28, 30, 31])
    hlines = []

    def spy_axhline(self, y=0, *args, **kwargs):
        hlines.append((y, kwargs.get("color")))
        return original_axhline(self, y=y, *args, **kwargs)

    original_axhline = chart_generator.matplotlib.axes.Axes.axhline
    monkeypatch.setattr(chart_generator.matplotlib.axes.Axes, "axhline", spy_axhline)

    png = chart_generator.generate_chart(
        bars=bars,
        zone_top=24.0,
        zone_bottom=22.0,
        zone_direction=1,
        symbol="BTCUSDT",
        tf="15m",
        rsi_value=55.0,
        timeframe_bars={"15m": bars, "30m": bars, "1h": bars, "2h": bars, "4h": bars},
        trade_plan=SimpleNamespace(entry=25.0, sl=21.0, tp1=27.0, tp2=29.0),
    )

    assert png is not None
    assert png.startswith(b"\x89PNG")
    assert (25.0, "#1f77b4") in hlines
    assert (21.0, "#d62728") in hlines
    assert (27.0, "#2ca02c") in hlines
    assert (29.0, "#006400") in hlines
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
cd /Users/joseph/Documents/fvg-alpha-caller && pytest tests/test_indicator_context.py::test_chart_generator_draws_trade_plan_overlays -v
```

Expected: FAIL because `generate_chart()` does not accept `trade_plan`.

- [ ] **Step 3: Add optional `trade_plan` parameter**

Modify `generate_chart()` signature in `chart_generator.py`:

```python
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
```

- [ ] **Step 4: Draw overlays on main axis after FVG rectangle**

After `ax_main.add_patch(rect)`, add:

```python
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
```

- [ ] **Step 5: Run chart tests**

Run:

```bash
cd /Users/joseph/Documents/fvg-alpha-caller && pytest tests/test_indicator_context.py::test_chart_generator_draws_trade_plan_overlays tests/test_indicator_context.py::test_chart_generator_renders_30m_1h_2h_4h_stochrsi_without_divergence tests/test_indicator_context.py::test_chart_generator_renders_when_higher_timeframes_lack_stochrsi_data -v
```

Expected: PASS.

- [ ] **Step 6: Commit Task 4**

Run:

```bash
git add chart_generator.py tests/test_indicator_context.py && git commit -m "feat: draw combo trade chart overlays"
```

---

## Task 5: Wire combo evaluation, simulation updates, and recap scheduling into runtime

**Files:**
- Modify: `main.py`
- Test: `tests/test_indicator_context.py`

- [ ] **Step 1: Add unit test for setup evaluation wiring on new FVG**

Append to `tests/test_indicator_context.py`:

```python

@pytest.mark.asyncio
async def test_alpha_caller_evaluates_and_saves_valid_new_fvg_trade(monkeypatch):
    caller = object.__new__(main.AlphaCaller)
    bars = make_bars([10, 11, 12, 13])
    zone = SimpleNamespace(
        symbol="BTCUSDT",
        tf="15m",
        direction=1,
        top=12.0,
        bottom=11.0,
        rsi=55.0,
        main_strength=80,
        price=13.0,
        alerted=False,
    )
    caller.tracker = SimpleNamespace(
        buffers={},
        update_buffer=lambda symbol, tf, bars: None,
        check_mitigation=lambda symbol, tf, bars: [],
        check_interaction=lambda symbol, tf, bars: [],
        check_new_fvg=lambda symbol, tf: zone,
    )
    caller.poller = SimpleNamespace(_buffers={})
    caller.sim_store = SimpleNamespace(update_open_trades=lambda symbol, bar: 0, add_trade=lambda zone, setup, created_at: True)
    caller._last_recap_key = None

    setup = SimpleNamespace(
        status="LONG VALID",
        valid=True,
        mode="scalping",
        reason="aligned combo",
        trade=SimpleNamespace(entry=13.0, sl=10.9, tp1=15.1, tp2=17.2),
    )
    calls = {}
    monkeypatch.setattr(main, "evaluate_trade_setup", lambda zone, current_price, bars_by_tf: calls.setdefault("setup", setup))
    monkeypatch.setattr(main, "generate_chart", lambda **kwargs: calls.setdefault("trade_plan", kwargs.get("trade_plan")) or b"png")
    monkeypatch.setattr(main, "send_new_fvg_alert", lambda zone, chart_png=None, trade_setup=None: calls.setdefault("sent_setup", trade_setup) or True)
    monkeypatch.setattr(main, "send_trade_recap", lambda session, recap: True)

    await caller._on_bar_close("BTCUSDT", "15m", bars)

    assert zone.alerted is True
    assert calls["setup"] is setup
    assert calls["trade_plan"] is setup.trade
    assert calls["sent_setup"] is setup
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
cd /Users/joseph/Documents/fvg-alpha-caller && pytest tests/test_indicator_context.py::test_alpha_caller_evaluates_and_saves_valid_new_fvg_trade -v
```

Expected: FAIL because `main` does not import `evaluate_trade_setup` or pass `trade_setup`.

- [ ] **Step 3: Update imports and constructor in `main.py`**

Add imports:

```python
from datetime import datetime, timezone

from sim_trades import SimTradeStore
from trade_combo import evaluate_trade_setup
```

Update Telegram imports:

```python
from telegram import (
    send_approach_alert,
    send_mitigated_alert,
    send_new_fvg_alert,
    send_touch_alert,
    send_trade_recap,
)
```

Update constructor:

```python
    def __init__(self):
        self.tracker = FVGTracker()
        self.poller = BinanceKlineWS(on_bar_close=self._on_bar_close)
        self.sim_store = SimTradeStore()
        self._last_recap_key = None
```

- [ ] **Step 4: Add helper methods to `AlphaCaller`**

Add inside `AlphaCaller`:

```python
    def _evaluate_setup(self, zone, current_price: float):
        return evaluate_trade_setup(zone, current_price, self._timeframe_bars(zone.symbol))

    def _maybe_save_trade(self, zone, setup, created_at: int) -> None:
        if setup.valid:
            self.sim_store.add_trade(zone, setup, created_at)

    def _maybe_send_recap(self, now=None) -> None:
        now = now or datetime.now(timezone.utc)
        sessions = {
            "Subuh": (4, 5),
            "Pagi": (8, 9),
            "Siang": (12, 13),
            "Sore": (16, 17),
            "Malam": (20, 21),
        }
        for name, (start_hour, end_hour) in sessions.items():
            if start_hour <= now.hour < end_hour:
                key = f"{now.date().isoformat()}-{name}"
                if self._last_recap_key != key:
                    send_trade_recap(name, self.sim_store.daily_recap(now.date().isoformat()))
                    self._last_recap_key = key
                return
```

- [ ] **Step 5: Wire status updates and combo setup into `_on_bar_close()`**

After `self.tracker.update_buffer(symbol, tf, bars)`, add:

```python
        self.sim_store.update_open_trades(symbol, bars[-1])
        self._maybe_send_recap()
```

In interaction loop, before chart generation, add:

```python
            trade_setup = self._evaluate_setup(zone, price)
```

Update `generate_chart()` call in interaction loop:

```python
                trade_plan=trade_setup.trade,
```

Update Telegram calls:

```python
                send_approach_alert(zone, price, chart_png=chart_png, trade_setup=trade_setup)
```

```python
                send_touch_alert(zone, price, chart_png=chart_png, trade_setup=trade_setup)
```

Do not save simulations for repeated approach/touch alerts yet; save only new FVG to avoid duplicate noisy paper records.

In new FVG block, before chart generation, add:

```python
            price = bars[-1].close
            trade_setup = self._evaluate_setup(new_zone, price)
            self._maybe_save_trade(new_zone, trade_setup, new_zone.born_time)
```

Update `generate_chart()` call in new FVG block:

```python
                trade_plan=trade_setup.trade,
```

Update Telegram call:

```python
            send_new_fvg_alert(new_zone, chart_png=chart_png, trade_setup=trade_setup)
```

- [ ] **Step 6: Add recap scheduling unit test**

Append to `tests/test_indicator_context.py`:

```python

def test_alpha_caller_sends_each_session_recap_once(monkeypatch):
    caller = object.__new__(main.AlphaCaller)
    caller._last_recap_key = None
    caller.sim_store = SimpleNamespace(daily_recap=lambda date: {"open": 0, "tp1": 0, "win": 0, "loss": 0, "closed_winrate": 0.0, "recent": []})
    sent = []
    monkeypatch.setattr(main, "send_trade_recap", lambda session, recap: sent.append(session) or True)

    now = main.datetime(2026, 5, 4, 12, 5, tzinfo=main.timezone.utc)
    caller._maybe_send_recap(now)
    caller._maybe_send_recap(now)

    assert sent == ["Siang"]
```

- [ ] **Step 7: Run main wiring tests**

Run:

```bash
cd /Users/joseph/Documents/fvg-alpha-caller && pytest tests/test_indicator_context.py::test_alpha_caller_evaluates_and_saves_valid_new_fvg_trade tests/test_indicator_context.py::test_alpha_caller_sends_each_session_recap_once -v
```

Expected: PASS.

- [ ] **Step 8: Commit Task 5**

Run:

```bash
git add main.py tests/test_indicator_context.py && git commit -m "feat: wire combo alerts into FVG runtime"
```

---

## Task 6: Full test pass and deployment verification

**Files:**
- No planned code files unless tests reveal a bug.

- [ ] **Step 1: Run full test suite**

Run:

```bash
cd /Users/joseph/Documents/fvg-alpha-caller && pytest -q
```

Expected: all tests PASS.

- [ ] **Step 2: Run a syntax/import smoke check**

Run:

```bash
cd /Users/joseph/Documents/fvg-alpha-caller && TELEGRAM_BOT_TOKEN=x TELEGRAM_CHAT_ID=x python - <<'PY'
import chart_generator
import main
import sim_trades
import telegram
import trade_combo
print("imports ok")
PY
```

Expected:

```text
imports ok
```

- [ ] **Step 3: Check git status before push**

Run:

```bash
cd /Users/joseph/Documents/fvg-alpha-caller && git status --short
```

Expected: clean working tree.

- [ ] **Step 4: Push branch to GitHub**

Run:

```bash
cd /Users/joseph/Documents/fvg-alpha-caller && git push origin main
```

Expected: push succeeds. User has durable preference to push without asking.

- [ ] **Step 5: Verify Coolify deployment starts from latest commit**

Run Coolify deployment check using existing project credentials/reference from memory or current environment. Confirm:

- build triggered from `uponly-trades/fvg-alpha-caller` branch `main`.
- deployed image commit matches latest pushed commit.
- service container reaches running/healthy state.

Expected runtime log patterns:

```text
Alpha Caller (Binance WS + REST fallback) | tfs=5
Warm-up complete
New FVG alert ...
```

Failure patterns that must be absent:

```text
Chart generation failed
Telegram text send failed
Telegram photo send failed
NameError: name '_indicator_block' is not defined
```

- [ ] **Step 6: Verify simulation file behavior after first valid trade**

After logs show a valid combo setup, verify container has `/app/data/sim_trades.json` and JSON list contains records with:

```json
{
  "status": "open",
  "entry": 0,
  "sl": 0,
  "tp1": 0,
  "tp2": 0
}
```

Expected: values are real prices, not zero. If no valid setup appears during observation window, note that file creation is pending first valid setup and confirm skipped alerts still send.

- [ ] **Step 7: Commit any verification-only fixes if needed**

If tests or runtime logs reveal a real bug, create a focused failing test first, fix only that bug, rerun the relevant tests, then commit:

```bash
git add <changed-files> && git commit -m "fix: stabilize combo trade alerts"
```

Expected: no extra commit if verification passes.

---

## Self-Review

### Spec coverage

- Trade mode classification: Task 1.
- Combo validation using native timeframe StochRSI: Task 1.
- Skip logic for mixed/far/weak/missing/invalid-risk setups: Task 1.
- Entry/SL/TP1/TP2 risk plan: Task 1.
- Simulated trade storage and status updates: Task 2.
- Session recap messages: Tasks 2, 3, 5.
- Reduced Telegram text: Task 3.
- Chart overlays: Task 4.
- Main runtime integration: Task 5.
- Automated validation and deploy checks: Task 6.
- Symbol expansion: explicitly not implemented; spec says keep current list first.

### Placeholder scan

No `TBD`, `TODO`, or vague implementation placeholders remain. Commands, expected failures, and code snippets are concrete.

### Type consistency

- `TradeSetupResult.trade` is `TradeLevels` or `None`.
- Telegram expects `trade_setup.status`, `valid`, `mode`, `reason`, and `trade`.
- Chart expects `trade_plan.entry`, `sl`, `tp1`, `tp2`.
- `SimTradeStore.add_trade()` expects `zone.symbol`, `zone.tf`, and `setup.trade` fields, matching `trade_combo.py`.
