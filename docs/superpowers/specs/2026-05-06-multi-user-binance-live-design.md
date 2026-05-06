# Multi-User Binance Live Trading Platform — Design Spec

**Date:** 2026-05-06
**Status:** Draft → pending user review
**Budget:** $100 testing capital per onboarded user
**Risk:** Real money. Real Binance USDT-M Futures orders.

---

## 1. Goal

Add a multi-user live-trading layer on top of the existing `fvg-alpha-caller` signal stream. Each user connects their own Binance API keys, configures per-user risk parameters, and the system auto-executes trades on their account when the bot emits valid signals. Users monitor everything via a Telegram-authed Next.js dashboard.

The existing `fvg-alpha-caller` container stays unchanged — it remains the single source of signals (`kronos_decisions` table). The new services consume from there.

---

## 2. Service Topology

Four containers in Coolify Dell Dubai, sharing one Postgres instance.

| Service             | Lang        | Port | Purpose                                                        |
|---------------------|-------------|------|----------------------------------------------------------------|
| `fvg-alpha-caller`  | Python      | —    | (existing, untouched) signal generator → `kronos_decisions`    |
| `trade_executor`    | Python      | 8014 | per-user order placement, TP1 trail, daily PnL reconcile       |
| `telegram_bot`      | Python      | —    | onboarding, alerts via `pg_notify`                             |
| `dashboard`         | Next.js 15  | 8013 | user-facing UI (Telegram auth, settings, stats, trades)        |

Shared:
- `fvg-postgres` (existing) — adds new tables, no schema change to existing ones
- `BINANCE_PROXY_URL` — single static-IP HTTP proxy used by `trade_executor` for all Binance calls

---

## 3. Database Schema (additive only)

### `users`
```sql
CREATE TABLE users (
    id                  SERIAL PRIMARY KEY,
    telegram_id         BIGINT UNIQUE NOT NULL,
    telegram_username   TEXT,
    first_name          TEXT,
    photo_url           TEXT,
    is_admin            BOOLEAN DEFAULT FALSE,
    binance_api_key_enc BYTEA,         -- AES-256-GCM(nonce||ct||tag)
    binance_api_secret_enc BYTEA,
    enabled             BOOLEAN DEFAULT FALSE,
    paused_until        BIGINT,        -- ms epoch; NULL = not paused
    pause_reason        TEXT,
    risk_pct            DOUBLE PRECISION DEFAULT 2.0   CHECK (risk_pct BETWEEN 0.1 AND 10),
    leverage            SMALLINT       DEFAULT 5      CHECK (leverage BETWEEN 5 AND 20),
    max_concurrent      SMALLINT       DEFAULT 3      CHECK (max_concurrent BETWEEN 1 AND 10),
    daily_loss_cap_pct  DOUBLE PRECISION DEFAULT 6.0  CHECK (daily_loss_cap_pct BETWEEN 1 AND 50),
    created_at          BIGINT NOT NULL,
    updated_at          BIGINT NOT NULL
);
CREATE INDEX idx_users_enabled ON users(enabled) WHERE enabled = true;
```

### `user_trades`
```sql
CREATE TABLE user_trades (
    id              TEXT PRIMARY KEY,         -- "{user_id}-{decision_id}" (idempotency)
    user_id         INTEGER NOT NULL REFERENCES users(id),
    decision_id     TEXT NOT NULL,            -- FK-style ref to kronos_decisions.id
    symbol          TEXT NOT NULL,
    tf              TEXT NOT NULL,
    direction       TEXT NOT NULL,            -- 'long' | 'short'
    leverage        SMALLINT NOT NULL,
    margin_usdt     DOUBLE PRECISION NOT NULL,
    notional_usdt   DOUBLE PRECISION NOT NULL,
    qty             DOUBLE PRECISION NOT NULL,
    entry           DOUBLE PRECISION NOT NULL,
    sl              DOUBLE PRECISION NOT NULL,
    sl_current      DOUBLE PRECISION NOT NULL,  -- updated on TP1 trail
    tp1             DOUBLE PRECISION NOT NULL,
    tp2             DOUBLE PRECISION NOT NULL,
    entry_order_id  TEXT,
    sl_order_id     TEXT,
    tp_order_id     TEXT,
    status          TEXT NOT NULL DEFAULT 'opening',
                    -- opening | open | tp1_trailed | closed_tp2 | closed_sl 
                    -- | closed_breakeven | error_open | error_no_sl | error_restart | manual_close
    pnl_usdt        DOUBLE PRECISION,
    pnl_pct         DOUBLE PRECISION,
    fees_usdt       DOUBLE PRECISION,
    error_msg       TEXT,
    opened_at       BIGINT NOT NULL,
    closed_at       BIGINT,
    UNIQUE (user_id, decision_id)
);
CREATE INDEX idx_user_trades_status   ON user_trades(user_id, status);
CREATE INDEX idx_user_trades_open     ON user_trades(status) WHERE status IN ('opening','open','tp1_trailed');
CREATE INDEX idx_user_trades_opened_at ON user_trades(opened_at DESC);
```

### `user_daily_pnl`
```sql
CREATE TABLE user_daily_pnl (
    user_id     INTEGER NOT NULL REFERENCES users(id),
    day         DATE NOT NULL,
    realized_pnl_usdt DOUBLE PRECISION DEFAULT 0,
    realized_pnl_pct  DOUBLE PRECISION DEFAULT 0,    -- vs day_start_balance
    trades_count INTEGER DEFAULT 0,
    wins_count   INTEGER DEFAULT 0,
    day_start_balance_usdt DOUBLE PRECISION,
    PRIMARY KEY (user_id, day)
);
```

### `sessions`
```sql
CREATE TABLE sessions (
    token       TEXT PRIMARY KEY,           -- random 32-byte hex
    user_id     INTEGER NOT NULL REFERENCES users(id),
    created_at  BIGINT NOT NULL,
    expires_at  BIGINT NOT NULL
);
CREATE INDEX idx_sessions_user ON sessions(user_id);
```

### `user_audit_log`
```sql
CREATE TABLE user_audit_log (
    id          BIGSERIAL PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    action      TEXT NOT NULL,    -- login | settings_update | keys_rotated | enabled | disabled | paused | resumed
    payload     JSONB,
    created_at  BIGINT NOT NULL
);
CREATE INDEX idx_audit_user_time ON user_audit_log(user_id, created_at DESC);
```

---

## 4. Encryption

- **Algorithm:** AES-256-GCM
- **Master key:** `MASTER_ENCRYPTION_KEY` env var (32 bytes, base64), held only by `trade_executor`
- **Storage format:** `nonce(12) || ciphertext || tag(16)` as `bytea`
- **Decrypt scope:** `trade_executor` only. Dashboard displays `••••••••XXXX` (last 4 of api key only — last 4 stored in plaintext column `api_key_tail` for display).
- **Internal encrypt endpoint:** `POST /encrypt` on `trade_executor:8014` (auth via shared `INTERNAL_TOKEN`), called by dashboard during key rotation.

Schema addition:
```sql
ALTER TABLE users ADD COLUMN api_key_tail TEXT;   -- last 4 chars, plaintext for UI
```

---

## 5. trade_executor Service

Single Python container. Four async loops via `asyncio.gather`:

### 5.1 signal_poller
- Every 2s: `SELECT * FROM kronos_decisions WHERE created_at > $last_seen AND valid=true ORDER BY created_at`
- For each signal × each enabled user → push to `order_queue`
- Persist `last_seen` per service restart in `executor_state` table

### 5.2 order_placer
Consumes queue. Per signal+user:

**Pre-trade gate (fail fast, log reason to audit):**
```
if not user.enabled                                  → skip 'user_disabled'
if user.paused_until and now < paused_until          → skip 'paused'
if today_pnl_pct <= -daily_loss_cap_pct              → pause forever, skip 'daily_cap_hit'
if open_count(user) >= max_concurrent                → skip 'max_concurrent'
if exists user_trades(user_id, decision_id)          → skip 'duplicate'    (idempotency)
balance = ccxt.fetch_balance()
if balance.USDT < 5                                  → skip 'low_balance'
```

**Sizing:**
```
risk_per_trade_usdt = balance * risk_pct / 100
sl_distance_pct     = abs(entry - sl) / entry * 100
notional_usdt       = risk_per_trade_usdt / (sl_distance_pct/100)
margin_usdt         = notional_usdt / leverage
qty                 = round_to_step(notional_usdt / entry, symbol.stepSize)
if notional_usdt < symbol.minNotional                → skip 'min_notional'
```

**Order sequence:**
```
INSERT user_trades(... status='opening', opened_at=now)
1. POST /fapi/v1/leverage      {symbol, leverage}
2. POST /fapi/v1/marginType    {symbol, marginType=ISOLATED}    # ignore err 4046 (already isolated)
3. POST /fapi/v1/order  type=MARKET side qty                    → entry_order_id
   wait for FILLED, capture avgPrice
4. POST /fapi/v1/order  type=STOP_MARKET stopPrice=sl  closePosition=true   → sl_order_id
5. POST /fapi/v1/order  type=TAKE_PROFIT_MARKET stopPrice=tp2 closePosition=true → tp_order_id
UPDATE user_trades SET status='open', entry=avgPrice, sl_current=sl, *_order_id=...
NOTIFY trade_opened (user_id, trade_id)
```

**Failure handling:**
- step 3 fails → status `error_open`, no orphan position
- step 4 fails after entry filled → emergency `MARKET reduceOnly` close, status `error_no_sl`, CRITICAL telegram alert
- step 5 fails → keep SL, status `open` but `tp_order_id=NULL`, alert user, `/trades/[id]` shows "set TP manually" CTA

### 5.3 trail_manager
One mark-price WS per active symbol (`{symbol}@markPrice@1s`):
```python
on_tick(symbol, price):
    for trade in trades_open_in_symbol():
        if trade.status != 'open': continue
        crossed = (long and price >= trade.tp1) or (short and price <= trade.tp1)
        if crossed:
            cancel_order(trade.sl_order_id)
            new_id = place_stop_market(stopPrice=trade.tp1, closePosition=true)
            UPDATE user_trades SET status='tp1_trailed', sl_current=tp1, sl_order_id=new_id
            NOTIFY trade_tp1_trailed
```

### 5.4 daily_pnl_aggregator
Every 60s:
- `fetch_my_trades(symbol, since=day_start)` per user with positions today
- For each filled SL/TP order → match to `user_trades` row, compute `pnl_usdt`, `fees_usdt`, set status `closed_*`
- Upsert `user_daily_pnl` aggregate
- If realized_pnl_pct ≤ -daily_loss_cap_pct → `UPDATE users SET paused_until=9999999999999, pause_reason='daily_cap'`, NOTIFY

### 5.5 user-data WS (per user)
Listens for ORDER_TRADE_UPDATE → near-real-time fill detection (faster than 60s reconcile, but reconcile is the source of truth).

### 5.6 IP Proxy

ccxt:
```python
exchange = ccxt.binanceusdm({
    'apiKey': key, 'secret': sec,
    'aiohttp_proxy': os.environ['BINANCE_PROXY_URL'],
    'options': {'defaultType': 'future'},
    'enableRateLimit': True,
})
```
WS via `aiohttp.ClientSession(connector=ProxyConnector.from_url(...))`.

All users share the proxy egress IP. Onboarding instructions tell user to whitelist that single IP on their Binance API key.

### 5.7 Restart resume
On boot: `SELECT * FROM user_trades WHERE status IN ('opening','open','tp1_trailed')`. For each:
- `opening` → check entry order via `fetch_order`. Filled → place SL+TP now. Not filled → cancel, status `error_restart`.
- `open`/`tp1_trailed` → re-attach mark-price WS.

---

## 6. telegram_bot Service

Listens on Postgres `LISTEN trade_opened, trade_tp1_trailed, trade_closed, error, daily_summary`.

**Onboarding flow** (`/start` in DM):
```
Bot: Welcome! Visit https://dashboard.uponly.../login to log in with Telegram.
Bot: Then add your Binance API keys in /api-keys.
Bot: Whitelist this IP on your Binance API restriction: <PROXY_IP>
Bot: Required permissions: Futures Trading + Read. NEVER enable Withdraw.
```

**Alert templates:**
```
🟢 OPENED  BTCUSDT 1h LONG
   entry $108,420  sl $107,200 (-1.13%)
   tp1 $109,640  tp2 $110,860
   qty 0.025  (5x lev, $135 notional, $27 margin)

🎯 TP1 HIT  BTCUSDT  → SL trailed to $109,640 (locked +1R)

✅ TP2 HIT  BTCUSDT  closed +$5.41 (+2.0%)

🛑 SL HIT  BTCUSDT  closed -$2.71 (-1.0%)

🔁 BREAKEVEN  BTCUSDT  TP1 trailed → SL hit at TP1 closed +$0.02

⚠️ ERROR  BTCUSDT entry filled but SL placement failed. Position closed via emergency market.

📊 DAILY (2026-05-06)
   trades 8  wins 5  pnl +$12.34 (+12.34%)
```

---

## 7. Dashboard (Next.js 15)

### 7.1 Stack
- Next.js 15 App Router (Server Components default)
- React 19
- Tailwind 3.4 + shadcn/ui (Radix primitives)
- `postgres` (porsager) for DB
- `@tanstack/react-table` v8
- Recharts v2
- `zod` for validation

### 7.2 Auth
- Telegram Login Widget on `/login`
- Server verifies HMAC: `secret = sha256(BOT_TOKEN); hmac_sha256(secret, sorted_payload)`
- Reject if `now - auth_date > 86400`
- UPSERT `users`, INSERT `sessions`, set httpOnly cookie `session=<token>; Secure; SameSite=Lax; Max-Age=2592000`
- `middleware.ts` validates cookie on every `/dashboard/**` and `/api/**` route

### 7.3 Pages

| Route                          | Purpose                                                               |
|--------------------------------|-----------------------------------------------------------------------|
| `/login`                       | public, Telegram widget                                               |
| `/dashboard`                   | overview cards: today PnL, open trades, WR 7d/30d                     |
| `/dashboard/signals`           | live feed (kronos_decisions last 50, SWR 5s)                          |
| `/dashboard/trades`            | TanStack Table of `user_trades`, filterable                           |
| `/dashboard/trades/[id]`       | single trade chart (entry/sl/tp1/tp2 lines, Binance fills overlay)    |
| `/dashboard/stats`             | Recharts: cum PnL, WR by symbol, WR by TF, v1 vs v2 filter compare    |
| `/dashboard/settings`          | risk_pct, leverage, max_concurrent, daily_cap, enabled toggle         |
| `/dashboard/api-keys`          | display masked, rotate (server action calls executor `/encrypt`)      |
| `/dashboard/audit`             | last 100 audit log entries                                            |
| `/admin/users` (admin only)    | all users, force-pause                                                |
| `/admin/system` (admin only)   | executor heartbeat, last signal age, error tail                       |

### 7.4 Server Actions (mutations)
- `updateSettings` — clamp inputs, write users + audit
- `rotateKeys` — POST plaintext to `trade_executor:8014/encrypt`, store `bytea`, write `api_key_tail`, audit
- `toggleEnabled` — flip `users.enabled`, audit
- `resumeFromPause` — clear `paused_until`, audit (admin or self)
- `forcePauseUser` — admin only

### 7.5 Live data
- `useSWR('/api/signals', fetcher, { refreshInterval: 5000 })` — signal feed
- `useSWR('/api/trades/open', ..., { refreshInterval: 3000 })` — open trades counter on overview

---

## 8. Risk Controls (hard rules)

1. **Isolated margin only** — hardcoded, user can't change
2. **Leverage clamp 5–20x** — DB CHECK + UI clamp + executor re-validate
3. **Max concurrent ≤ 10** — DB CHECK
4. **Daily loss cap → pause forever** until manual `/resume`
5. **No withdraw API permission** — onboarding instructs read+futures only; we don't validate (Binance doesn't expose perm bits via API), but no code path calls withdraw
6. **No min balance enforcement** — fail-soft, just skip trade if balance too low for min notional
7. **Idempotency** — UNIQUE `(user_id, decision_id)` prevents double-entry on restart/retry
8. **Emergency close on SL placement failure** — entry without SL is unacceptable

---

## 9. Deploy

Coolify Dell Dubai. New compose stack alongside existing `fvg-alpha-caller`:

```yaml
services:
  trade_executor:
    image: ghcr.io/.../trade_executor:latest
    environment:
      DATABASE_URL: postgresql://fvg:.../fvg
      MASTER_ENCRYPTION_KEY: ${MASTER_ENCRYPTION_KEY}
      BINANCE_PROXY_URL: ${BINANCE_PROXY_URL}
      INTERNAL_TOKEN: ${INTERNAL_TOKEN}
      TELEGRAM_BOT_TOKEN: ${TELEGRAM_BOT_TOKEN}
    ports: ["8014:8014"]   # internal only

  telegram_bot:
    image: ghcr.io/.../telegram_bot:latest
    environment:
      DATABASE_URL: ...
      TELEGRAM_BOT_TOKEN: ${TELEGRAM_BOT_TOKEN}

  dashboard:
    image: ghcr.io/.../dashboard:latest
    environment:
      DATABASE_URL: ...
      TELEGRAM_BOT_TOKEN: ${TELEGRAM_BOT_TOKEN}
      INTERNAL_TOKEN: ${INTERNAL_TOKEN}
      EXECUTOR_URL: http://trade_executor:8014
      NEXT_PUBLIC_BOT_USERNAME: ${BOT_USERNAME}
    ports: ["8013:3000"]
    domains: dashboard.uponly...
```

Existing `fvg-alpha-caller` and `fvg-postgres` untouched.

---

## 10. Out of Scope (explicit non-goals)

- No paper trading mode (live only)
- No backtesting in dashboard (use existing shadow simulators)
- No mobile app (responsive web only)
- No multi-exchange (Binance USDT-M Futures only)
- No DCA / pyramiding / scaling in (one entry per signal)
- No partial TP at TP1 (TP1 = trail trigger only, not close)
- No copy-trading between users
- No referral/affiliate system
- No KYC (Telegram auth is the only identity layer)
- No 2FA (Telegram itself is the second factor)

---

## 11. Open Questions

1. **Proxy provider:** user will provide static IP. Need provider name + auth format (HTTP basic vs IP-allowlist) before implementation.
2. **Master key rotation:** out of scope for v1, but document that re-encrypting all `*_enc` columns is a manual SQL+Python task.
3. **Symbol metadata:** does `trade_executor` cache `exchangeInfo` (stepSize, minNotional)? Refresh cadence? Decision: cache 1h, refresh lazily.
4. **TP1 detection lag:** mark-price WS is ~1s tick. If price gaps over TP1 by 0.5R+ between ticks, do we still trail to TP1 or to current price? Decision: trail to TP1 always (we never lose that as the floor).
5. **Daily reset boundary:** UTC 00:00 vs user-local? Decision: UTC 00:00 (matches Binance funding clock).

---

## 12. Acceptance Criteria

- [ ] Two test users can connect API keys, get signals, see trades open on Binance
- [ ] $100 testing capital survives a full week of live signals without unexpected drawdown beyond `daily_loss_cap_pct`
- [ ] Restart of `trade_executor` resumes all open trades correctly (no orphans, no duplicates)
- [ ] TP1 hit trails SL within 5s of mark-price cross
- [ ] Dashboard loads `/dashboard` < 1s p95 with 100 trades in DB
- [ ] All mutations write `user_audit_log` entries
- [ ] Telegram alerts arrive within 3s of DB NOTIFY
- [ ] Encryption round-trip verified: encrypt → store → decrypt → matches plaintext
- [ ] Failed SL placement triggers emergency close + CRITICAL alert in test scenario

---

End of spec.
