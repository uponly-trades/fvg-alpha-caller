# Dynamic SL/TP from Market Structure

**Branch**: fvg-v2
**Date**: 2026-05-12
**Status**: Approved by user, in implementation

## 1. What

Replace static geometric SL/TP with structure-anchored SL and magnet-anchored TP
for v2 FVG signals. SL is placed behind the swing that would invalidate the FVG
thesis. TP1/TP2 snap to nearest unmitigated swing/HTF FVG in trade direction.
A minimum structural RR gate rejects signals that lack room to a real magnet,
which is preferable to forcing a bad RR.

## 2. Why

Current SL = `zone_far_edge ± ATR*0.3` and TP = `entry ± risk * RR(2)` are
geometric; they ignore actual swing pivots and HTF magnets. Symptoms in
production: KSMUSDT TP2 hit at +$0.72 (RR=1 era), and many trades stop out on
wick noise that does not actually invalidate the zone. Anchoring SL to the
structural invalidation point and TP to where price is genuinely magnetized
should improve RR realized per trade and reduce noise stop-outs.

## 3. Boundaries

In scope:
- Pure swing detector (fractal 2-2: high higher than 2 left + 2 right).
- Structural SL chooser per side, behind worst-case of {zone far edge, last N=10
  swing extreme on trigger TF}, plus 0.25 ATR buffer.
- TP magnet finder over trigger TF swings + 1h/4h FVG opposite-direction edges.
- TP1 = nearest magnet, TP2 = second magnet capped at `entry ± risk * 4`.
- RR gate `V2_MIN_STRUCTURAL_RR=1.2` (skip with reason `no_tp_room`).
- Trailing in trade_executor: after TP1 hit, move SL to `max(BE, latest swing in
  favor)` instead of fixed-percent ladder.
- Env toggles for safe rollout: `V2_SL_MODE=structural|atr` (default structural,
  fallback atr if no swing valid), `V2_TP_MODE=magnet|fixed` (default magnet),
  `V2_MIN_STRUCTURAL_RR=1.2`, `V2_TRAIL_MODE=structural|percent` (default
  structural in executor, percent fallback retained).

Out of scope:
- ZVZ overlap gate (separate spec; this work composes cleanly with it later).
- Liquidity-pool detection beyond fractal swings (no equal-high cluster scoring
  in v1).
- Order-flow / footprint inputs.
- Per-symbol auto tuning.

## 4. Inputs and assumptions

- Trigger TF bars (default 15m) and HTF bars (1h, 4h) already in `bars_by_tf`.
- ATR already available via `triggered.atr` or computed fallback.
- `FVGZone` exposes `top, bottom, direction, atr`.
- A "swing high" at index i requires `bars[i].high > bars[i-1..i-2].high` and
  `bars[i].high > bars[i+1..i+2].high`. Same logic mirrored for swing low.
- Swing list is built from the latest closed bars; the last 2 bars cannot form
  a confirmed swing (need 2 right neighbors).

## 5. Rules

**R1 (Swing detection).** `_swings(bars, kind, lookback=60)` returns list of
`(index, price)` where `kind in {"high","low"}` using fractal 2-2 over the last
`lookback` closed bars. Empty if `len(bars) < 5`.

**R2 (Structural SL).**
- Long: `sl = min(zone.bottom, last_swing_low_after_zone_birth) - 0.25 * ATR`.
- Short: `sl = max(zone.top, last_swing_high_after_zone_birth) + 0.25 * ATR`.
- Falls back to existing `_compute_sl(zone, atr)` if no swing exists after zone
  birth (early signals on fresh symbols).
- Never tighter than zone-edge baseline; only equal-or-wider.

**R3 (TP magnets).**
- Build candidate list:
  - All trigger-TF swings opposite-direction extremes ahead of entry that are
    not already taken by current price. For long, swing highs above entry; for
    short, swing lows below entry.
  - All FVG zones on 1h and 4h whose direction is opposite (long target = bear
    FVG above; short target = bull FVG below) and not fully mitigated.
- Magnet price for an FVG = its near edge (bottom for long target, top for
  short target).
- Sort by distance from entry ascending. Drop magnets within `0.5 * risk`
  distance (too close to be meaningful TP1).
- TP1 = first magnet. TP2 = second magnet, else `entry ± risk * V2_RR_CAP(4)`.
- If no magnet exists for TP1: skip signal with reason `no_tp_room`.

**R4 (RR gate).** Compute `rr = abs(tp1 - entry) / abs(entry - sl)`. If
`rr < V2_MIN_STRUCTURAL_RR (1.2)`, skip with reason `rr_too_low_structural`.

**R5 (Indicators).** `V2Signal.indicators` records: `sl_mode`, `tp_mode`,
`structural_rr`, `tp1_magnet_kind` (`swing|fvg_1h|fvg_4h`), `tp2_magnet_kind`,
`swing_anchor_price`. These flow to logs and Telegram telemetry.

**R6 (Executor compatibility).** `V2Signal.tp` continues to mean TP2 for
backward compat with `signal_poller -> orchestrator`. `tp1` is published as a
new signal field `signal["tp1"]` (optional). Orchestrator uses `signal["tp1"]`
when present, else falls back to current `entry + risk` math.

**R7 (Structural trailing, scope-reduced for v1).** In `trail_manager.maybe_trail`, when
`V2_TRAIL_MODE=structural` and trade is in `tp1_trailed` status (TP1 hit), set
SL to `max(BE, latest swing low after entry) - 0.10 * ATR` for long (mirror for
short). When no fresh swing exists, keep current `r_progress` ladder behavior.

**R8 (Skip telemetry).** New skip reasons logged: `no_tp_room`,
`rr_too_low_structural`. Counted in same per-hour skip distribution.

## 6. Success Criteria

1. Unit: `_swings`, `_structural_sl`, `_tp_magnets`, `_rr_gate` covered with
   golden cases including: no swings, swing equals zone edge, magnet too close,
   magnet at HTF FVG, RR exactly at threshold.
2. Unit: `trail_manager.trail_to_structural_sl` returns expected SL given a
   sequence of swings post-entry; never loosens SL.
3. E2E: `tests/test_e2e_signal_flow.py` extends with two cases:
   - structural happy path: signal carries `tp1`, `structural_rr >= 1.2`, SL
     matches swing low - 0.25 ATR.
   - `no_tp_room`: signal rejected when zone has no opposite magnet within
     `4 * risk`.
4. Backward compat: existing tests still pass without setting new env vars
   (`V2_SL_MODE` defaults to structural with safe fallback to atr).
5. Server smoke: after deploy, within 24h channel produces at least one signal
   carrying `sl_mode=structural` and `tp1_magnet_kind != fixed`.

## 7. Risks & Mitigations

- Fewer signals due to `no_tp_room`. → Mitigated by 24h observation; tune by
  raising `V2_RR_CAP` cap or relaxing minimum-distance filter.
- Swing detection noise on illiquid coins. → Fractal 2-2 + min-distance filter;
  fallback to ATR SL if no confirmed swing.
- Magnet too far → TP2 unrealistic. → Cap at `entry ± risk * 4`.
- Trailing logic regression on legacy trades. → Keep percent-ladder branch
  intact; new branch gated by `V2_TRAIL_MODE` env, default structural but
  reverts to percent if no swing.
