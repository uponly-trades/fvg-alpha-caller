# Plan: Dynamic SL/TP — Implementation Tasks

Spec: `.specify/specs/dynamic-sltp.md`

## Phase 0 — Config & flags
- [ ] `config.py`: add `V2_SL_MODE`, `V2_TP_MODE`, `V2_MIN_STRUCTURAL_RR`,
  `V2_RR_CAP`, `V2_SWING_LOOKBACK`, `V2_SWING_FRACTAL`, `V2_TP_MIN_DIST_R`,
  `V2_TRAIL_MODE`, `V2_TRAIL_BUFFER_ATR`. Defaults: structural, 1.2, 4.0, 60,
  2, 0.5, structural, 0.10.

## Phase 1 — Strategy: structural SL + TP magnets
- [ ] `strategy_v2._swings(bars, kind, lookback, fractal)` pure helper.
- [ ] `strategy_v2._structural_sl(zone, bars, atr_val, side)` returns float.
- [ ] `strategy_v2._tp_magnets(entry, side, risk, bars_15m, zones_by_tf)`
  returns `(tp1, tp1_kind, tp2, tp2_kind)`.
- [ ] Wire into `evaluate_v2_signal` after retest passes:
  - compute structural sl (if mode=structural)
  - compute risk = abs(entry - sl)
  - compute tp1, tp2 from magnets (if mode=magnet)
  - rr gate -> skip `rr_too_low_structural` or `no_tp_room`
  - record indicators + return `V2Signal` with `tp = tp2`, plus expose `tp1`
    via `indicators["tp1"]` (executor reads from there).

## Phase 2 — Signal contract & executor
- [ ] `signal_poller`: include `tp1` in signal dict if available
  (`indicators["tp1"]`).
- [ ] `orchestrator.handle_signal_for_user`: when `signal.get("tp1")` present,
  use it as `tp1`; else current `entry + risk` math. `tp2` continues to come
  from `signal["tp"]` (now magnet-derived) when present.
- [ ] `trail_manager.trail_to_structural_sl` when `V2_TRAIL_MODE=structural`
  and trade in `tp1_trailed`. Reuse fetched bars (REST 15m) per symbol; cache
  per loop tick.

## Phase 3 — Tests (TDD)
- [ ] `tests/test_strategy_v2_structural.py`: unit cases R1-R5.
- [ ] `tests/test_e2e_signal_flow.py`: extend with structural happy + no_tp_room.
- [ ] `trade_executor/tests/test_trail_manager.py`: extend with
  `trail_to_structural_sl` non-loosening test.
- [ ] Run full suite: `pytest -q tests/` (alpha) and `PYTHONPATH=..:. pytest -q
  tests/` (executor). Goal: 0 failures.

## Phase 4 — Migration & deploy
- [ ] No DB schema change required (tp1/tp2 columns already exist on
  `user_trades`). No migration file needed.
- [ ] Commit per phase, push to `fvg-v2`.
- [ ] Deploy to fvg-tokyo: `cd /opt/fvg/repo && git pull && cd /opt/fvg && docker
  compose up -d --build fvg-alpha-caller trade_executor`.
- [ ] Monitor 30 min: `signal_decisions` rows carry `sl_mode=structural` and
  `event_type=v2_fvg_touch|v2_fvg_retest`.

## Phase 5 — Observation
- [ ] After 24h: pull skip distribution (`reason` counts) and win-rate of new
  trades. If `no_tp_room` >70% of skips on quiet hours, lower
  `V2_MIN_STRUCTURAL_RR` to 1.0 via env override (no rebuild).

## Rollback
- Set `V2_SL_MODE=atr V2_TP_MODE=fixed V2_TRAIL_MODE=percent` in
  `/opt/fvg/docker-compose.yaml` env, `docker compose up -d` (no rebuild).
