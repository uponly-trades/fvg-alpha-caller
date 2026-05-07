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
