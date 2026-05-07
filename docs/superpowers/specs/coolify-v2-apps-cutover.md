# Coolify v1→v2 Cutover Runbook

**Date:** 2026-05-07
**Owner:** joseph
**Goal:** Bring up `fvg-alpha-v2-app`, `trade-executor-v2`, `telegram-bot-v2` (branch `fvg-v2`); stop legacy v1 services without losing `@campinaz_bot` continuity.

**Coolify instance:** `https://ctrl.uponlytrader.xyz` (Dell Dubai). Coolify v4 has NO `coolify` shell CLI — operate via Web UI or REST API.

## Vars

```bash
export COOLIFY_URL="https://ctrl.uponlytrader.xyz"
export COOLIFY_TOKEN="8|Fv0tMK2n7kxWa76ZWonFtFtpXPFIxG56mZU9EpYg60f3fe14"
# v1 service umbrella (existing)
export V1_SERVICE_UUID="x11j71l7mde2nsujipb23uky"
# v1 app uuids (current)
export V1_TRADE_EXECUTOR_UUID="yxv3tfpu1kdzq5gesfw0k28s"
export V1_TELEGRAM_BOT_UUID="gdk2op1j6bx6iqrflmhgvcr5"
# v2 uuids — fill after Step 1 duplicates run
export V2_FVG_ALPHA_UUID=""
export V2_TRADE_EXECUTOR_UUID=""
export V2_TELEGRAM_BOT_UUID=""
```

API helpers:
```bash
ccurl() { curl -sS -H "Authorization: Bearer $COOLIFY_TOKEN" -H "Content-Type: application/json" "$@"; }
app_start() { ccurl -X POST "$COOLIFY_URL/api/v1/applications/$1/start"; }
app_stop()  { ccurl -X POST "$COOLIFY_URL/api/v1/applications/$1/stop"; }
app_logs()  { ccurl "$COOLIFY_URL/api/v1/applications/$1/logs?lines=100"; }
app_status(){ ccurl "$COOLIFY_URL/api/v1/applications/$1" | jq '{name,status,git_branch}'; }
```

## Prerequisites

- `fvg-v2` branch pushed and CI green.
- All Tasks 1–17 complete in this plan.
- Local v2 smoke test (Task 17) passed.

## Cutover sequence

### 1. Duplicate v1 Apps in Coolify UI

Coolify v4 does not expose a "duplicate" REST endpoint. Use the UI:

- Navigate to `Project → fvg-alpha-caller` (or wherever the 3 services live).
- For each of: `fvg-alpha-caller-app`, `trade-executor`, `telegram-bot`:
  - Click `…` → `Clone` (or `Duplicate`).
  - New name: append `-v2` → `fvg-alpha-v2-app`, `trade-executor-v2`, `telegram-bot-v2`.
  - Source: same GitHub repo `uponly-trades/fvg-alpha-caller`.
  - **Branch: change to `fvg-v2`.**
  - Network: same as v1 (Coolify default).
  - Database URL: same as v1 (shared Postgres `zz1q4m2u2363ucx0ebosb41u`).
- Persistent storage: create a NEW volume per v2 service (do NOT share with v1). Mount at `/app/data`.
- After clone, copy the 3 new app UUIDs from the URL bar / API listing into the env vars above:
  ```bash
  ccurl "$COOLIFY_URL/api/v1/applications" | jq '.[] | {uuid,name,git_branch} | select(.name|test("-v2$"))'
  ```

### 2. Set v2 environment vars (per app)

Patch env vars via API (or paste into UI's Environment tab):
```bash
for UUID in $V2_FVG_ALPHA_UUID $V2_TRADE_EXECUTOR_UUID $V2_TELEGRAM_BOT_UUID; do
  ccurl -X PATCH "$COOLIFY_URL/api/v1/applications/$UUID/envs" -d '{
    "data": [
      {"key":"STRATEGY_VERSION","value":"v2"},
      {"key":"KRONOS_ENABLED","value":"false"},
      {"key":"V2_COOLDOWN_SEC","value":"1800"},
      {"key":"V2_HTF_TOUCH_LOOKBACK","value":"1"},
      {"key":"ATR_BUFFER_V2","value":"0.3"},
      {"key":"V2_TRAIL_ATR_BUFFER","value":"0.3"}
    ]
  }'
done
```
All other env (DB URL, SOCKS5 proxy, Telegram token) is inherited from the cloned v1 config.

### 3. Build v2 services
```bash
app_start $V2_FVG_ALPHA_UUID
app_start $V2_TRADE_EXECUTOR_UUID
# do NOT start telegram-bot-v2 yet — would collide with v1 bot polling
```
Poll until "Running":
```bash
watch -n 5 'app_status '$V2_FVG_ALPHA_UUID' && app_status '$V2_TRADE_EXECUTOR_UUID
```
Tail logs for clean startup:
```bash
app_logs $V2_FVG_ALPHA_UUID | jq -r '.logs[]' | tail -100
```
Expect: bar-close lines, no `kronos` references, possibly `v2 signal` log entries.

### 4. Stop v1 telegram-bot (free `@campinaz_bot` polling)
```bash
app_stop $V1_TELEGRAM_BOT_UUID
sleep 30   # polling drops within long-poll timeout
```

### 5. Start v2 telegram-bot
```bash
app_start $V2_TELEGRAM_BOT_UUID
```
Verify polling resumed: send `/start` to `@campinaz_bot` from Telegram and confirm response.

### 6. Stop v1 alpha + executor
```bash
app_stop $V1_TRADE_EXECUTOR_UUID
# fvg-alpha-caller-app uuid:
ccurl "$COOLIFY_URL/api/v1/applications" | jq '.[] | select(.name=="fvg-alpha-caller-app") | .uuid'
# stop it the same way
```

### 7. Monitor for 24h

Watch:
- Alert volume on `@campinaz_bot` (expect higher than v1 due to no Kronos gate; 30-min cooldown should cap it).
- SL placement on TradingView vs `zone.bottom + 0.3*ATR` (long) / `zone.top + 0.3*ATR` (short).
- TRAIL UPDATE messages firing every 15m/30m bar close on open positions; `Locked: +X.XXR` line present.

## Rollback
```bash
app_stop  $V2_TELEGRAM_BOT_UUID
app_stop  $V2_FVG_ALPHA_UUID
app_stop  $V2_TRADE_EXECUTOR_UUID
app_start $V1_TELEGRAM_BOT_UUID
app_start $V1_TRADE_EXECUTOR_UUID
app_start <V1_FVG_ALPHA_UUID>
```
v1 polling resumes on `@campinaz_bot`. No DB rollback needed (schema unchanged).

## Cleanup (after 7-day soak)

In Coolify UI, delete the v1 Apps:
- `fvg-alpha-caller-app`
- `trade-executor`
- `telegram-bot`

Keep `main` branch — it remains the v1 reference and rollback target if v2 ever needs to be recreated from scratch.
