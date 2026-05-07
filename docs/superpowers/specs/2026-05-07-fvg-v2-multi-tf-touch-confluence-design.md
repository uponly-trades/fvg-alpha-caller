# FVG v2 — Multi-TF Touch Confluence Strategy

**Date:** 2026-05-07
**Branch:** `fvg-v2`
**Status:** Locked design (user-approved 2026-05-07)
**Supersedes (in v2 path only):** Kronos-gated entry logic in main.py / trade_combo.py / snipe.py

---

## 1. Goal

Replace Kronos-LLM-gated v1 alert pipeline with pure-TA v2 that triggers on multi-timeframe FVG touch confluence. v1 stays untouched on `main` for rollback. v2 ships as separate Coolify Apps + reuses `@campinaz_bot` (v1 bot stops, v2 takes over).

**Success criteria:**
- v2 emits long/short alerts when 15m or 30m FVG is touched AND ≥1 of {1h, 2h, 4h} same-direction FVG is currently active+touched.
- SL placed below FVG zone (long) / above FVG zone (short) with ATR*0.3 buffer.
- No fixed TP — pure wick-by-wick trail every closed trigger-TF candle.
- Zero Kronos coupling in v2 trigger path (no LLM call, no kronos_client import).
- Telegram alerts identical to v1 format minus Kronos section.
- v1 (`main` branch + legacy Coolify Apps) untouched; rollback = restart legacy Apps.

**Non-goals:**
- Backtesting v2 (deferred, future work).
- Re-evaluating retest_short / htf_fade_short (kept as separate paths, may merge later).
- Migrating database schema (reuse existing tables).
- Strength filter (weak/bias/strong all valid in v2).

---

## 2. Architecture

### 2.1 Branch + Repo

- **Repo:** `fvg-alpha-caller` (same).
- **Branch:** `fvg-v2` cut from current `main` HEAD.
- **Feature flag:** env `STRATEGY_VERSION=v2` (default `v1` if absent → backward-compatible). Carried so future merge to `main` can A/B by flag.
- **Kronos kill-switch:** env `KRONOS_ENABLED=false` hardcoded in v2 Coolify env. Code path checks flag; if false, never imports/calls kronos_client.

### 2.2 Coolify Apps (NEW, parallel to v1)

| Service | Branch | Image | Notes |
|---|---|---|---|
| `fvg-alpha-v2-app` | `fvg-v2` | python | Detection + alert producer |
| `trade-executor-v2` | `fvg-v2` | python | Position tracker + trail mgr |
| `telegram-bot-v2` | `fvg-v2` | python | Same `@campinaz_bot` token |

- Clone via Coolify "Duplicate App" from existing 3 services.
- Same Postgres + same Redis + same SOCKS5 proxy.
- Buffer cache volume: NEW volume per v2 service (do not share with v1).
- Legacy v1 Apps: STOP (preserve config for rollback). Containers retained 7 days, then cleanup.

### 2.3 Kronos decoupling

- v2 entry path (`main.py::AlphaCaller._evaluate_setup_async` v2 branch) skips kronos prediction entirely.
- `import kronos_client` guarded behind `if STRATEGY_VERSION == "v1":` or moved to v1-only module.
- All 29 kronos refs in main.py audited; in v2, only display fields (if any) populated as `None`/`"N/A"`.
- Telegram bot v2: drop Kronos section in alert template.

---

## 3. Entry Rules

### 3.1 Trigger TF (one of)

- **15m bullish/bearish FVG touch** — price wick OR body enters active zone, any strength.
- **30m bullish/bearish FVG touch** — same, fallback if 15m has no active zone.

A "touch" = current candle's high ≥ zone.bottom AND low ≤ zone.top (zone overlaps candle range), AND zone is active (not fully mitigated).

Strength filter: **NONE.** weak/bias/strong all valid.

### 3.2 HTF confluence (≥1 required, gate)

For long signal, at least one of:
- 1h bullish FVG **active** AND **currently touched** by latest 1h candle (wick or body).
- 2h bullish FVG active AND currently touched.
- 4h bullish FVG active AND currently touched.

For short signal, mirror with bearish FVGs.

**"Currently touched"** = within the most recent N closed candles of that HTF (N=1 by default — only the latest closed candle on that TF). Rationale: touch must be fresh enough to imply alignment, not stale from days ago.

### 3.3 Confidence weighting (display only, not gate)

Score = sum of HTF weights:
- 1h touch active = +1
- 2h touch active = +2
- 4h touch active = +3

Range 1–6. Shown in alert as confluence stars or numeric. Not a gate.

### 3.4 Direction independence

Long and short signals evaluated independently per symbol per cycle. A symbol can have both a long alert (bull touch + bull HTF) and a short alert (bear touch + bear HTF) firing in same cycle if both side-paths qualify (rare but allowed).

---

## 4. SL Placement (LOCKED)

```python
ATR_BUFFER = 0.3  # vs v1's 0.8 — looser since v2 trail is wick-by-wick

# Long
sl = trigger_tf_zone.bottom - atr * ATR_BUFFER

# Short
sl = trigger_tf_zone.top + atr * ATR_BUFFER
```

- `trigger_tf_zone` = the 15m or 30m FVG that fired the touch.
- `atr` = ATR(14) on trigger TF at signal candle.
- SL is below/above the **FVG zone**, NOT below/above the trigger candle wick.
- Rationale: zone breach = invalidation; wick alone may be noise.

---

## 5. TP / Trail (LOCKED)

### 5.1 No fixed TP

- RR = 1:∞.
- TP1/TP2 fields in alert: `"trail"` (display string).

### 5.2 Pure wick-by-wick trail (literal)

On every closed trigger-TF candle (15m or 30m, whichever fired):

```python
# Long
new_sl = previous_closed_trigger_tf_candle.low - atr * ATR_BUFFER
sl = max(sl, new_sl)  # only ratchet up, never lower

# Short
new_sl = previous_closed_trigger_tf_candle.high + atr * ATR_BUFFER
sl = min(sl, new_sl)  # only ratchet down, never higher
```

- **Trail starts immediately on entry.** No +0.5R unlock threshold.
- **No close-confirm** — touch-based exit (price hits SL = stop).
- **No timeout** (no "exit if no progress in N candles").
- Whipsaw acceptable as v0; iterate after data review.

---

## 6. Short Mirror

Bearish FVG version of all rules:
- Trigger: 15m or 30m bearish touch.
- HTF confluence: ≥1 of {1h, 2h, 4h} bearish active+touched.
- SL: `zone.top + ATR * 0.3`.
- Trail: previous closed wick high + ATR * 0.3, ratchet down.

---

## 7. Telegram Alert Format

### 7.1 Same `@campinaz_bot` (LOCKED)

- Reuse existing token (`TELEGRAM_BOT_TOKEN` env, same recipients).
- v1 bot service STOP before v2 starts (avoid double polling).

### 7.2 Format (v1 minus Kronos)

```
(NEW LONG - FRESH FVG | BTCUSDT | 15m)

📍 Entry: 67,250.00
🛑 SL:    66,890.00 (-0.54%)
🎯 TP:    trail (RR 1:∞)

Confluence: ⭐⭐⭐⭐ (1h✓ 2h✓ 4h✓ = 6)
Trigger: 15m bullish FVG touch
HTF:     1h✓ 2h✓ 4h✓

StochRSI: 15m=23 30m=31 1h=42 2h=55 4h=67  [display only]
Volume Δ: +18%  OI Δ: +2.1%  [display only]

[Removed: Kronos forecast section]
```

- **Title format preserved:** `(status_trade - status_fvg | symbol | timeframe)`.
- Status_trade values: `NEW LONG`, `NEW SHORT`, `TRAIL UPDATE`, `STOPPED`.
- Status_fvg values: `FRESH FVG`, `RETEST`, `BIAS`, `STRONG`.
- StochRSI / Volume Δ / OI Δ shown for context, NOT gates.

### 7.3 Trail update alerts

When SL ratchets, send compact update:
```
(TRAIL UPDATE | BTCUSDT | 15m)
SL: 66,890 → 67,420 (+0.79%)
Locked: +0.25R
```

---

## 8. What's Removed vs v1

| Component | v1 | v2 |
|---|---|---|
| Kronos LLM gate | Required for trigger | DISABLED (`KRONOS_ENABLED=false`) |
| `_v2_short_decision` combo gate | Used | NOT USED in v2 entry |
| StochRSI per-TF | Gate | Display only |
| Volume Δ / OI Δ | Gate | Display only |
| HTF fade short / retest short | Active alongside main path | Kept separate (re-evaluate later) |
| ATR SL buffer | 0.8 | 0.3 |
| TP1/TP2 fixed | Set | None — trail only |
| Trail unlock | +0.5R | Immediate |
| Close-confirm exit | Yes | No (touch-based) |
| Strength filter | weak filtered out | None |

---

## 9. File-Level Changes

### 9.1 New / modified files

| File | Action | Purpose |
|---|---|---|
| `config.py` | Modify | Add `STRATEGY_VERSION`, `KRONOS_ENABLED`, `ATR_BUFFER_V2=0.3`, HTF confluence params |
| `main.py` | Modify | v2 branch in `_evaluate_setup_async`; skip kronos when `STRATEGY_VERSION=v2` |
| `strategy_v2.py` | **NEW** | Pure v2 logic: `evaluate_v2_signal(symbol)` returns long/short setup or None |
| `trail_manager.py` | **NEW** | Wick-by-wick trail state machine, called per closed trigger-TF candle |
| `telegram.py` | Modify | Add v2 alert formatter (drop Kronos section); branch by `STRATEGY_VERSION` |
| `trade_combo.py` | Untouched in v2 path | v1 still uses it; v2 imports nothing from it for triggers |
| `snipe.py` | Untouched | v1 retest/htf_fade still uses; v2 doesn't |
| `kronos_client.py` | Untouched | Only imported when `STRATEGY_VERSION=v1` |
| `fvg_engine.py` | Untouched | Source of truth for FVG detection; v2 reads via existing API |

### 9.2 `strategy_v2.py` interface (sketch)

```python
@dataclass
class V2Signal:
    symbol: str
    direction: int            # 1 long, -1 short
    trigger_tf: str           # "15m" or "30m"
    trigger_zone: FVGZone
    entry: float
    sl: float
    confluence_score: int     # 1-6
    htf_touches: dict[str, bool]   # {"1h": True, "2h": False, "4h": True}
    indicators: dict[str, float]   # stoch_rsi per tf, vol_delta, oi_delta — display only
    atr: float

def evaluate_v2_signal(
    symbol: str,
    fvg_tracker: FVGTracker,
    bar_buffer: dict[tuple[str, str], list[Bar]],
) -> Optional[V2Signal]:
    # 1. Check 15m active FVG touched on latest 15m close
    # 2. If none, check 30m
    # 3. If trigger found, count HTF confluences (1h/2h/4h same direction, active+touched)
    # 4. If confluence >= 1, build V2Signal with SL = zone.bottom - atr*0.3
    # 5. Else return None
    ...
```

### 9.3 `trail_manager.py` interface (sketch)

```python
@dataclass
class TrailState:
    signal_id: str
    symbol: str
    trigger_tf: str
    direction: int
    entry: float
    current_sl: float
    last_update_candle_time: int

class TrailManager:
    def on_bar_close(self, symbol: str, tf: str, bars: list[Bar]):
        # For each open trail state matching this (symbol, tf):
        #   prev_candle = bars[-2]   # last closed
        #   if direction == 1:
        #       new_sl = prev_candle.low - atr * 0.3
        #       state.current_sl = max(state.current_sl, new_sl)
        #   else:
        #       new_sl = prev_candle.high + atr * 0.3
        #       state.current_sl = min(state.current_sl, new_sl)
        #   if changed: emit trail-update alert
        ...

    def check_stop_hit(self, symbol: str, last_price: float):
        # Touch-based: if last_price <= current_sl (long) or >= (short), close + emit alert
        ...
```

---

## 10. Data Flow

```
Binance WS klines (5 TFs)
        │
        ▼
fvg_engine.FVGTracker (per symbol/tf)
        │
        ▼
strategy_v2.evaluate_v2_signal(symbol)
        │ (on 15m or 30m bar close)
        ▼
V2Signal? ── No → drop
   │
   Yes
   ▼
telegram.send_v2_alert(signal)  + persist to executor_state
        │
        ▼
trail_manager.register(signal)
        │
        ▼ (every closed trigger-TF candle)
trail_manager.on_bar_close → ratchet SL → trail-update alert
        │
        ▼ (every tick / mark price)
trail_manager.check_stop_hit → STOPPED alert
```

---

## 11. Testing Strategy

### 11.1 Unit tests (`tests/test_strategy_v2.py`)

- `test_15m_touch_no_htf_confluence_returns_none` — trigger present, all HTF empty → None.
- `test_15m_touch_with_4h_confluence_emits_long` — bull 15m touch + 4h bull active+touched → V2Signal with score=3.
- `test_30m_fallback_when_no_15m_zone` — no 15m zone, 30m bull touch + 1h confluence → V2Signal with trigger_tf=30m.
- `test_short_mirror` — bearish version of above.
- `test_sl_below_fvg_not_below_wick` — assert `sl == zone.bottom - atr * 0.3`, not `low - atr * 0.3`.
- `test_confluence_score_additive` — 1h+2h+4h all touched → score=6.
- `test_strength_not_filtered` — weak FVG zone still triggers.

### 11.2 Trail tests (`tests/test_trail_manager.py`)

- `test_trail_ratchets_on_higher_low` — long trail moves up when prev candle low > current SL+buffer.
- `test_trail_does_not_lower_sl` — prev candle low < current SL → no change.
- `test_short_trail_ratchets_on_lower_high` — mirror.
- `test_stop_hit_touch_based` — price touches SL → close emitted (not "close below").
- `test_immediate_trail_no_unlock` — first bar after entry already trails.

### 11.3 Integration (manual, post-deploy)

- Start v2 in shadow mode (alerts only, no executor) for 24h.
- Compare v2 alert count vs v1 alert count (expect higher — no Kronos gate).
- Spot-check 5 v2 alerts vs TradingView Zeiierman indicator → zones must visually match.
- Verify SL placement on chart vs zone bottom + 0.3*ATR.

### 11.4 Out of scope (deferred)

- Backtest harness for v2 (separate spec).
- Live PnL tracking (use existing v1 trade_executor mechanics, just point at v2 signals).

---

## 12. Migration / Cutover

1. Cut `fvg-v2` branch from `main`.
2. Implement spec (see writing-plans output).
3. Push branch; trigger Coolify build of 3 new Apps (`*-v2`).
4. Verify v2 services healthy (logs, DB connection, FVG detection running).
5. **STOP v1 telegram-bot service** (avoid double polling on `@campinaz_bot`).
6. **START v2 telegram-bot service.**
7. Monitor 24h: alert volume, SL placement correctness, trail behavior.
8. v1 alpha + executor: STOP (preserve config). Containers kept 7d for rollback.
9. After 7d clean run: delete v1 Coolify Apps.

**Rollback procedure:**
- STOP all 3 v2 Apps.
- START 3 v1 Apps.
- v1 bot resumes on same `@campinaz_bot` token.
- No DB rollback needed (schema unchanged).

---

## 13. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Higher alert volume floods bot (no Kronos filter) | Add per-symbol cooldown (e.g. 30min) before second v2 signal same direction |
| Wick-by-wick trail whipsaws on volatile candle | Accepted as v0; revisit if SL hit rate >70% within 1h of entry |
| HTF "currently touched" window N=1 too strict | Tunable via `HTF_TOUCH_LOOKBACK` env (default 1, can bump to 2-3) |
| Buffer cache restart slowness | New v2 cache volume — first restart re-warms; subsequent uses cache |
| Binance IP ban during warmup | Reuse v1 SOCKS5 proxy; same buffer cache pattern |
| Two bots same token = polling conflict | Strict ordering: STOP v1 bot before START v2 bot |

---

## 14. Open Questions (NONE — all resolved)

All design decisions locked per user 2026-05-07. If implementer hits ambiguity, escalate before assuming.

---

## 15. References

- Memory: `~/.claude/projects/-Users-joseph-Documents/memory/project_fvg_v2_strategy.md`
- Visual reference: Zeiierman "Ranked FVG Imbalance Zones" (TradingView Pine).
- Source of truth FVG logic: `fvg_engine.py:281-540` (FVGZone, detect_fvg, FVGTracker).
- v1 SL fix commit (`atr*0.1 → atr*0.8`): main branch HEAD as of 2026-05-07.
