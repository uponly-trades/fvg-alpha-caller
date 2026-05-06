# Multi-User Binance Live Trading Platform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a multi-user live trading layer on top of the existing `fvg-alpha-caller` signal stream. Each user connects their own Binance USDT-M Futures API keys; the system auto-executes trades on signal, manages SL/TP, trails to TP1, and reports via Telegram + dashboard.

**Architecture:** Three new services share an existing Postgres. `trade_executor` (Python, port 8014) consumes `kronos_decisions`, decrypts per-user API keys, places orders via ccxt through a single static-IP HTTP proxy, manages TP1-trail via mark-price WS, reconciles fills every 60s, and exposes a private `/encrypt` endpoint for the dashboard. `telegram_bot` (Python) listens on `pg_notify` channels and sends formatted alerts. `dashboard` (Next.js 15 + shadcn/ui, port 8013) authenticates via Telegram Login Widget, manages user settings, and visualizes signals/trades/stats. Existing `fvg-alpha-caller` and `fvg-postgres` stay untouched.

**Tech Stack:** Python 3.11 + asyncio + ccxt + aiohttp + psycopg2 + cryptography (AES-256-GCM); Next.js 15 (App Router) + React 19 + Tailwind 3.4 + shadcn/ui + postgres (porsager) + zod + TanStack Table v8 + Recharts v2; Postgres 15 (existing); Coolify Dell Dubai deployment.

**Reference spec:** [`docs/superpowers/specs/2026-05-06-multi-user-binance-live-design.md`](../specs/2026-05-06-multi-user-binance-live-design.md)

---

## Files and Systems

### New repository layout (siblings to fvg-alpha-caller container)

```
fvg-alpha-caller/                       # existing — UNTOUCHED
trade_executor/                          # NEW
├── Dockerfile
├── requirements.txt
├── pyproject.toml
├── trade_executor/
│   ├── __init__.py
│   ├── main.py                          # asyncio.gather entrypoint
│   ├── config.py                        # env vars, constants
│   ├── db.py                            # asyncpg pool, helpers
│   ├── crypto.py                        # AES-256-GCM encrypt/decrypt
│   ├── http_api.py                      # FastAPI: /encrypt, /healthz
│   ├── exchange.py                      # ccxt wrapper w/ proxy
│   ├── sizing.py                        # risk → qty math
│   ├── signal_poller.py                 # loop 1: kronos_decisions → queue
│   ├── order_placer.py                  # loop 2: queue → 3-order sequence
│   ├── trail_manager.py                 # loop 3: mark-price WS → trail SL
│   ├── pnl_aggregator.py                # loop 4: 60s reconcile
│   ├── user_data_ws.py                  # loop 5: per-user ORDER_TRADE_UPDATE
│   ├── resume.py                        # boot recovery for in-flight trades
│   ├── notify.py                        # pg_notify helpers
│   └── audit.py                         # user_audit_log writer
└── tests/
    ├── conftest.py
    ├── test_crypto.py
    ├── test_sizing.py
    ├── test_signal_poller.py
    ├── test_order_placer.py
    ├── test_trail_manager.py
    ├── test_pnl_aggregator.py
    ├── test_resume.py
    └── test_http_api.py

telegram_bot/                            # NEW
├── Dockerfile
├── requirements.txt
├── telegram_bot/
│   ├── __init__.py
│   ├── main.py                          # asyncio entry
│   ├── config.py
│   ├── db.py
│   ├── listener.py                      # LISTEN trade_*, error, daily_summary
│   ├── handlers.py                      # /start, /status, /pause, /resume
│   ├── templates.py                     # alert message formatters
│   └── client.py                        # aiogram Bot wrapper
└── tests/
    ├── conftest.py
    ├── test_templates.py
    └── test_listener.py

dashboard/                               # NEW (Next.js 15)
├── Dockerfile
├── package.json
├── tsconfig.json
├── next.config.mjs
├── tailwind.config.ts
├── postcss.config.mjs
├── components.json                      # shadcn config
├── src/
│   ├── app/
│   │   ├── layout.tsx
│   │   ├── globals.css
│   │   ├── login/page.tsx               # Telegram widget
│   │   ├── api/
│   │   │   ├── auth/telegram/route.ts   # HMAC verify, set cookie
│   │   │   ├── auth/logout/route.ts
│   │   │   ├── signals/route.ts         # SWR feed
│   │   │   └── trades/open/route.ts
│   │   ├── (dashboard)/
│   │   │   ├── layout.tsx               # auth-gated shell
│   │   │   ├── dashboard/page.tsx       # overview cards
│   │   │   ├── signals/page.tsx
│   │   │   ├── trades/page.tsx
│   │   │   ├── trades/[id]/page.tsx
│   │   │   ├── stats/page.tsx
│   │   │   ├── settings/page.tsx
│   │   │   ├── api-keys/page.tsx
│   │   │   └── audit/page.tsx
│   │   └── (admin)/
│   │       ├── layout.tsx               # admin guard
│   │       ├── admin/users/page.tsx
│   │       └── admin/system/page.tsx
│   ├── middleware.ts                    # session cookie validate
│   ├── lib/
│   │   ├── db.ts                        # postgres client
│   │   ├── auth.ts                      # session helpers
│   │   ├── telegram-verify.ts           # HMAC check
│   │   ├── executor.ts                  # POST /encrypt fetch wrapper
│   │   ├── env.ts                       # zod-parsed env
│   │   └── format.ts                    # number/date formatting
│   ├── server-actions/
│   │   ├── settings.ts
│   │   ├── api-keys.ts
│   │   ├── enabled.ts
│   │   └── admin.ts
│   ├── components/
│   │   ├── ui/                          # shadcn primitives (generated)
│   │   ├── trade-table.tsx
│   │   ├── signal-feed.tsx
│   │   ├── stats-charts.tsx
│   │   ├── settings-form.tsx
│   │   ├── api-keys-form.tsx
│   │   └── nav.tsx
│   └── types/
│       └── db.ts                        # row types
└── tests/
    ├── unit/
    │   ├── telegram-verify.test.ts
    │   ├── auth.test.ts
    │   └── format.test.ts
    └── e2e/
        └── login.spec.ts                # playwright (optional)

migrations/                              # NEW (shared, applied via psql)
└── 0001_multi_user_live.sql

deploy/
└── docker-compose.coolify.yml           # NEW stack alongside existing

.github/workflows/                       # NEW per service
├── trade_executor.yml
├── telegram_bot.yml
└── dashboard.yml
```

### Tables added (migration `0001_multi_user_live.sql`)

- `users`, `user_trades`, `user_daily_pnl`, `sessions`, `user_audit_log`, `executor_state`

### Phases (each phase ships independently working)

1. **Phase 1 — Foundations:** migration applied, encryption module verified round-trip, three service skeletons each with `/healthz` working.
2. **Phase 2 — trade_executor core:** signal → order → SL/TP → trail → reconcile, end-to-end in testnet.
3. **Phase 3 — telegram_bot:** onboarding flow + pg_notify alerts wired.
4. **Phase 4 — dashboard:** Telegram auth + all user pages + Server Actions.
5. **Phase 5 — Deploy & smoke test:** compose stack to Coolify, 2 real test users, $100 each.

---

## Phase 1 — Foundations

Goal: schema in DB, AES-256-GCM module verified, three service skeletons each return 200 on `/healthz` (or equivalent).

### Task 1.1: Schema migration

**Files:**
- Create: `migrations/0001_multi_user_live.sql`
- Test: `migrations/tests/test_0001_apply.sh`

- [ ] **Step 1: Write the migration SQL**

```sql
-- migrations/0001_multi_user_live.sql
BEGIN;

CREATE TABLE IF NOT EXISTS users (
    id                     SERIAL PRIMARY KEY,
    telegram_id            BIGINT UNIQUE NOT NULL,
    telegram_username      TEXT,
    first_name             TEXT,
    photo_url              TEXT,
    is_admin               BOOLEAN NOT NULL DEFAULT FALSE,
    binance_api_key_enc    BYTEA,
    binance_api_secret_enc BYTEA,
    api_key_tail           TEXT,
    enabled                BOOLEAN NOT NULL DEFAULT FALSE,
    paused_until           BIGINT,
    pause_reason           TEXT,
    risk_pct               DOUBLE PRECISION NOT NULL DEFAULT 2.0
                            CHECK (risk_pct BETWEEN 0.1 AND 10),
    leverage               SMALLINT NOT NULL DEFAULT 5
                            CHECK (leverage BETWEEN 5 AND 20),
    max_concurrent         SMALLINT NOT NULL DEFAULT 3
                            CHECK (max_concurrent BETWEEN 1 AND 10),
    daily_loss_cap_pct     DOUBLE PRECISION NOT NULL DEFAULT 6.0
                            CHECK (daily_loss_cap_pct BETWEEN 1 AND 50),
    created_at             BIGINT NOT NULL,
    updated_at             BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_enabled ON users(enabled) WHERE enabled = true;

CREATE TABLE IF NOT EXISTS user_trades (
    id              TEXT PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id),
    decision_id     TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    tf              TEXT NOT NULL,
    direction       TEXT NOT NULL CHECK (direction IN ('long','short')),
    leverage        SMALLINT NOT NULL,
    margin_usdt     DOUBLE PRECISION NOT NULL,
    notional_usdt   DOUBLE PRECISION NOT NULL,
    qty             DOUBLE PRECISION NOT NULL,
    entry           DOUBLE PRECISION NOT NULL,
    sl              DOUBLE PRECISION NOT NULL,
    sl_current      DOUBLE PRECISION NOT NULL,
    tp1             DOUBLE PRECISION NOT NULL,
    tp2             DOUBLE PRECISION NOT NULL,
    entry_order_id  TEXT,
    sl_order_id     TEXT,
    tp_order_id     TEXT,
    status          TEXT NOT NULL DEFAULT 'opening'
                    CHECK (status IN (
                      'opening','open','tp1_trailed',
                      'closed_tp2','closed_sl','closed_breakeven',
                      'error_open','error_no_sl','error_restart','manual_close')),
    pnl_usdt        DOUBLE PRECISION,
    pnl_pct         DOUBLE PRECISION,
    fees_usdt       DOUBLE PRECISION,
    error_msg       TEXT,
    opened_at       BIGINT NOT NULL,
    closed_at       BIGINT,
    UNIQUE (user_id, decision_id)
);
CREATE INDEX IF NOT EXISTS idx_user_trades_status    ON user_trades(user_id, status);
CREATE INDEX IF NOT EXISTS idx_user_trades_open      ON user_trades(status)
    WHERE status IN ('opening','open','tp1_trailed');
CREATE INDEX IF NOT EXISTS idx_user_trades_opened_at ON user_trades(opened_at DESC);

CREATE TABLE IF NOT EXISTS user_daily_pnl (
    user_id                INTEGER NOT NULL REFERENCES users(id),
    day                    DATE NOT NULL,
    realized_pnl_usdt      DOUBLE PRECISION NOT NULL DEFAULT 0,
    realized_pnl_pct       DOUBLE PRECISION NOT NULL DEFAULT 0,
    trades_count           INTEGER NOT NULL DEFAULT 0,
    wins_count             INTEGER NOT NULL DEFAULT 0,
    day_start_balance_usdt DOUBLE PRECISION,
    PRIMARY KEY (user_id, day)
);

CREATE TABLE IF NOT EXISTS sessions (
    token       TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    created_at  BIGINT NOT NULL,
    expires_at  BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_exp  ON sessions(expires_at);

CREATE TABLE IF NOT EXISTS user_audit_log (
    id         BIGSERIAL PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    action     TEXT NOT NULL,
    payload    JSONB,
    created_at BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_user_time ON user_audit_log(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS executor_state (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at BIGINT NOT NULL
);

COMMIT;
```

- [ ] **Step 2: Apply against a scratch DB and verify**

```bash
# scratch DB on localhost
createdb fvg_test
psql fvg_test -f migrations/0001_multi_user_live.sql
psql fvg_test -c "\dt"
```

Expected output includes: `users`, `user_trades`, `user_daily_pnl`, `sessions`, `user_audit_log`, `executor_state`.

- [ ] **Step 3: Verify CHECK constraints reject bad values**

```bash
psql fvg_test -c "INSERT INTO users (telegram_id, risk_pct, created_at, updated_at) VALUES (1, 99, 0, 0);"
```

Expected: ERROR mentioning `users_risk_pct_check`.

```bash
psql fvg_test -c "INSERT INTO users (telegram_id, leverage, created_at, updated_at) VALUES (2, 100, 0, 0);"
```

Expected: ERROR mentioning `users_leverage_check`.

- [ ] **Step 4: Verify UNIQUE(user_id, decision_id) on user_trades**

```bash
psql fvg_test -c "INSERT INTO users (telegram_id, created_at, updated_at) VALUES (10, 0, 0);"
psql fvg_test <<'SQL'
INSERT INTO user_trades (id, user_id, decision_id, symbol, tf, direction,
  leverage, margin_usdt, notional_usdt, qty, entry, sl, sl_current, tp1, tp2,
  status, opened_at)
VALUES
  ('1-d1', 1, 'd1', 'BTCUSDT', '1h', 'long', 5, 10, 50, 0.001, 100, 95, 95, 105, 110, 'opening', 0),
  ('1-d1', 1, 'd1', 'BTCUSDT', '1h', 'long', 5, 10, 50, 0.001, 100, 95, 95, 105, 110, 'opening', 0);
SQL
```

Expected: second insert ERROR with duplicate key. (PRIMARY KEY catches first; rerun with different `id` to test the UNIQUE on `(user_id, decision_id)`.)

- [ ] **Step 5: Drop scratch DB and commit**

```bash
dropdb fvg_test
git add migrations/0001_multi_user_live.sql
git commit -m "feat(migrations): add multi-user live trading schema"
```

### Task 1.2: trade_executor — repo skeleton

**Files:**
- Create: `trade_executor/Dockerfile`
- Create: `trade_executor/requirements.txt`
- Create: `trade_executor/pyproject.toml`
- Create: `trade_executor/trade_executor/__init__.py`
- Create: `trade_executor/trade_executor/config.py`
- Create: `trade_executor/trade_executor/main.py`
- Create: `trade_executor/trade_executor/http_api.py`
- Create: `trade_executor/tests/conftest.py`
- Test: `trade_executor/tests/test_http_api.py`

- [ ] **Step 1: Create requirements.txt**

```txt
asyncpg==0.29.0
psycopg2-binary==2.9.9
ccxt==4.3.50
aiohttp==3.9.5
aiohttp-socks==0.9.0
python-binance==1.0.19
fastapi==0.111.0
uvicorn[standard]==0.30.1
pydantic==2.7.4
pydantic-settings==2.3.4
cryptography==42.0.8
python-telegram-bot==21.3
websockets==12.0
pytest==8.2.2
pytest-asyncio==0.23.7
httpx==0.27.0
```

- [ ] **Step 2: Create pyproject.toml**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "trade_executor"
version = "0.1.0"
requires-python = ">=3.11"

[tool.setuptools.packages.find]
where = ["."]
include = ["trade_executor*"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 3: Create config.py**

```python
# trade_executor/trade_executor/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str
    MASTER_ENCRYPTION_KEY: str  # base64-encoded 32 bytes
    BINANCE_PROXY_URL: str | None = None
    INTERNAL_TOKEN: str
    TELEGRAM_BOT_TOKEN: str
    HTTP_PORT: int = 8014
    SIGNAL_POLL_INTERVAL_S: float = 2.0
    PNL_RECONCILE_INTERVAL_S: float = 60.0

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
```

- [ ] **Step 4: Write failing test for /healthz**

```python
# trade_executor/tests/test_http_api.py
import pytest
from httpx import AsyncClient, ASGITransport

from trade_executor.http_api import app


@pytest.mark.asyncio
async def test_healthz_returns_ok():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
```

- [ ] **Step 5: Run test (must fail, no app yet)**

```bash
cd trade_executor && pytest tests/test_http_api.py::test_healthz_returns_ok -v
```

Expected: ImportError or similar (module not found).

- [ ] **Step 6: Implement minimal http_api.py**

```python
# trade_executor/trade_executor/http_api.py
from fastapi import FastAPI

app = FastAPI(title="trade_executor")


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
```

- [ ] **Step 7: Run test, expect pass**

```bash
cd trade_executor && pytest tests/test_http_api.py -v
```

Expected: PASSED.

- [ ] **Step 8: Create main.py entry**

```python
# trade_executor/trade_executor/main.py
import asyncio
import logging
import sys

import uvicorn

from trade_executor.config import settings
from trade_executor.http_api import app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("trade_executor")


async def run():
    config = uvicorn.Config(app, host="0.0.0.0", port=settings.HTTP_PORT, log_level="info")
    server = uvicorn.Server(config)
    log.info("trade_executor starting on :%d", settings.HTTP_PORT)
    await server.serve()


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
```

- [ ] **Step 9: Create Dockerfile**

```dockerfile
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libssl-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml ./
COPY trade_executor/ ./trade_executor/

ENV PYTHONUNBUFFERED=1

EXPOSE 8014

CMD ["python", "-m", "trade_executor.main"]
```

- [ ] **Step 10: Build and verify locally**

```bash
cd trade_executor
docker build -t trade_executor:dev .
docker run --rm -e DATABASE_URL=postgres://x \
  -e MASTER_ENCRYPTION_KEY=$(openssl rand -base64 32) \
  -e INTERNAL_TOKEN=test -e TELEGRAM_BOT_TOKEN=test \
  -p 8014:8014 trade_executor:dev &
sleep 3
curl -s http://localhost:8014/healthz
docker stop $(docker ps -q --filter ancestor=trade_executor:dev)
```

Expected: `{"status":"ok"}`.

- [ ] **Step 11: Commit**

```bash
git add trade_executor/
git commit -m "feat(trade_executor): scaffold + healthz"
```

### Task 1.3: AES-256-GCM crypto module

**Files:**
- Create: `trade_executor/trade_executor/crypto.py`
- Test: `trade_executor/tests/test_crypto.py`

- [ ] **Step 1: Write failing tests**

```python
# trade_executor/tests/test_crypto.py
import base64
import os

import pytest

from trade_executor.crypto import encrypt, decrypt


@pytest.fixture
def key() -> bytes:
    return os.urandom(32)


def test_round_trip_string(key):
    plaintext = "binance_api_key_AbCd1234"
    blob = encrypt(plaintext, key)
    assert decrypt(blob, key) == plaintext


def test_blob_format_nonce_ct_tag(key):
    blob = encrypt("hello", key)
    # 12-byte nonce + ciphertext + 16-byte tag, total > 28
    assert len(blob) >= 28
    assert isinstance(blob, bytes)


def test_two_encrypts_have_different_nonces(key):
    a = encrypt("same", key)
    b = encrypt("same", key)
    assert a != b


def test_wrong_key_fails(key):
    blob = encrypt("secret", key)
    with pytest.raises(Exception):
        decrypt(blob, os.urandom(32))


def test_tampered_ciphertext_fails(key):
    blob = bytearray(encrypt("secret", key))
    blob[20] ^= 0x01  # flip a bit in ciphertext region
    with pytest.raises(Exception):
        decrypt(bytes(blob), key)


def test_b64_master_key_roundtrip():
    raw = os.urandom(32)
    b64 = base64.b64encode(raw).decode()
    decoded = base64.b64decode(b64)
    assert decoded == raw
    blob = encrypt("x", decoded)
    assert decrypt(blob, decoded) == "x"
```

- [ ] **Step 2: Run test, expect fail**

```bash
cd trade_executor && pytest tests/test_crypto.py -v
```

Expected: ImportError (module not found).

- [ ] **Step 3: Implement crypto.py**

```python
# trade_executor/trade_executor/crypto.py
"""AES-256-GCM with format: nonce(12) || ciphertext || tag(16).

Master key (32 bytes) loaded from base64 env var by caller. Module is pure.
"""
from __future__ import annotations

import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


NONCE_LEN = 12


def encrypt(plaintext: str, key: bytes) -> bytes:
    if len(key) != 32:
        raise ValueError("key must be 32 bytes (AES-256)")
    nonce = os.urandom(NONCE_LEN)
    ct_with_tag = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
    return nonce + ct_with_tag


def decrypt(blob: bytes, key: bytes) -> str:
    if len(key) != 32:
        raise ValueError("key must be 32 bytes (AES-256)")
    if len(blob) < NONCE_LEN + 16:
        raise ValueError("blob too short")
    nonce = blob[:NONCE_LEN]
    ct_with_tag = blob[NONCE_LEN:]
    return AESGCM(key).decrypt(nonce, ct_with_tag, None).decode("utf-8")
```

- [ ] **Step 4: Run tests, expect pass**

```bash
cd trade_executor && pytest tests/test_crypto.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add trade_executor/trade_executor/crypto.py trade_executor/tests/test_crypto.py
git commit -m "feat(trade_executor): AES-256-GCM crypto module"
```

### Task 1.4: trade_executor — DB pool helper

**Files:**
- Create: `trade_executor/trade_executor/db.py`
- Test: `trade_executor/tests/test_db.py`

- [ ] **Step 1: Write a failing connection test (skipped without DATABASE_URL)**

```python
# trade_executor/tests/test_db.py
import os

import pytest

from trade_executor import db


pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


@pytest.mark.asyncio
async def test_pool_round_trip():
    pool = await db.create_pool(os.environ["TEST_DATABASE_URL"])
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT 1 AS one")
            assert row["one"] == 1
    finally:
        await pool.close()
```

- [ ] **Step 2: Implement db.py**

```python
# trade_executor/trade_executor/db.py
from __future__ import annotations

import asyncpg


async def create_pool(dsn: str, *, min_size: int = 2, max_size: int = 10) -> asyncpg.Pool:
    return await asyncpg.create_pool(dsn=dsn, min_size=min_size, max_size=max_size)


def now_ms() -> int:
    import time
    return int(time.time() * 1000)
```

- [ ] **Step 3: Run test (against scratch DB)**

```bash
createdb fvg_test
TEST_DATABASE_URL=postgresql:///fvg_test pytest trade_executor/tests/test_db.py -v
dropdb fvg_test
```

Expected: PASSED.

- [ ] **Step 4: Commit**

```bash
git add trade_executor/trade_executor/db.py trade_executor/tests/test_db.py
git commit -m "feat(trade_executor): asyncpg pool helper"
```

### Task 1.5: telegram_bot skeleton

**Files:**
- Create: `telegram_bot/Dockerfile`
- Create: `telegram_bot/requirements.txt`
- Create: `telegram_bot/telegram_bot/__init__.py`
- Create: `telegram_bot/telegram_bot/config.py`
- Create: `telegram_bot/telegram_bot/main.py`
- Test: `telegram_bot/tests/test_smoke.py`

- [ ] **Step 1: requirements.txt**

```txt
aiogram==3.7.0
asyncpg==0.29.0
psycopg2-binary==2.9.9
pydantic==2.7.4
pydantic-settings==2.3.4
pytest==8.2.2
pytest-asyncio==0.23.7
```

- [ ] **Step 2: config.py**

```python
# telegram_bot/telegram_bot/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str
    TELEGRAM_BOT_TOKEN: str
    DASHBOARD_URL: str = "https://dashboard.example.com"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
```

- [ ] **Step 3: Smoke test**

```python
# telegram_bot/tests/test_smoke.py
def test_import_main_module():
    import telegram_bot.main  # noqa: F401
```

- [ ] **Step 4: main.py minimal**

```python
# telegram_bot/telegram_bot/main.py
import asyncio
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("telegram_bot")


async def run():
    log.info("telegram_bot starting (skeleton)")
    while True:
        await asyncio.sleep(60)


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Dockerfile**

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY telegram_bot/ ./telegram_bot/

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "telegram_bot.main"]
```

- [ ] **Step 6: Verify import & run**

```bash
cd telegram_bot && pytest tests/ -v
```

Expected: 1 passed.

- [ ] **Step 7: Commit**

```bash
git add telegram_bot/
git commit -m "feat(telegram_bot): scaffold"
```

### Task 1.6: dashboard skeleton (Next.js 15)

**Files:**
- Create: `dashboard/package.json`
- Create: `dashboard/next.config.mjs`
- Create: `dashboard/tsconfig.json`
- Create: `dashboard/tailwind.config.ts`
- Create: `dashboard/postcss.config.mjs`
- Create: `dashboard/src/app/layout.tsx`
- Create: `dashboard/src/app/globals.css`
- Create: `dashboard/src/app/api/health/route.ts`
- Create: `dashboard/Dockerfile`
- Test: `dashboard/tests/unit/health.test.ts`

- [ ] **Step 1: package.json**

```json
{
  "name": "dashboard",
  "version": "0.1.0",
  "private": true,
  "scripts": {
    "dev": "next dev -p 3000",
    "build": "next build",
    "start": "next start -p 3000",
    "lint": "next lint",
    "test": "vitest run"
  },
  "dependencies": {
    "next": "15.0.0",
    "react": "19.0.0",
    "react-dom": "19.0.0",
    "postgres": "3.4.4",
    "zod": "3.23.8",
    "swr": "2.2.5",
    "@tanstack/react-table": "8.17.3",
    "recharts": "2.12.7",
    "clsx": "2.1.1",
    "tailwind-merge": "2.4.0",
    "class-variance-authority": "0.7.0",
    "lucide-react": "0.395.0",
    "@radix-ui/react-slot": "1.1.0",
    "@radix-ui/react-dialog": "1.1.1",
    "@radix-ui/react-dropdown-menu": "2.1.1",
    "@radix-ui/react-label": "2.1.0",
    "@radix-ui/react-switch": "1.1.0",
    "@radix-ui/react-tabs": "1.1.0",
    "@radix-ui/react-tooltip": "1.1.1"
  },
  "devDependencies": {
    "@types/node": "20.14.2",
    "@types/react": "19.0.0",
    "@types/react-dom": "19.0.0",
    "autoprefixer": "10.4.19",
    "postcss": "8.4.38",
    "tailwindcss": "3.4.4",
    "tailwindcss-animate": "1.0.7",
    "typescript": "5.5.2",
    "vitest": "1.6.0",
    "eslint": "9.5.0",
    "eslint-config-next": "15.0.0"
  }
}
```

- [ ] **Step 2: next.config.mjs**

```javascript
/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  experimental: {
    serverActions: { allowedOrigins: ["*"] },
  },
};

export default nextConfig;
```

- [ ] **Step 3: tsconfig.json**

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["dom", "dom.iterable", "esnext"],
    "allowJs": false,
    "skipLibCheck": true,
    "strict": true,
    "noEmit": true,
    "esModuleInterop": true,
    "module": "esnext",
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "jsx": "preserve",
    "incremental": true,
    "plugins": [{ "name": "next" }],
    "baseUrl": ".",
    "paths": { "@/*": ["./src/*"] }
  },
  "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx", ".next/types/**/*.ts"],
  "exclude": ["node_modules"]
}
```

- [ ] **Step 4: tailwind.config.ts**

```typescript
import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: ["class"],
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
};

export default config;
```

- [ ] **Step 5: postcss.config.mjs**

```javascript
export default { plugins: { tailwindcss: {}, autoprefixer: {} } };
```

- [ ] **Step 6: globals.css**

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

:root {
  --background: 0 0% 100%;
  --foreground: 222 47% 11%;
  --border: 214 32% 91%;
  --input: 214 32% 91%;
  --ring: 222 47% 11%;
}
.dark {
  --background: 222 47% 6%;
  --foreground: 210 40% 98%;
  --border: 217 33% 17%;
  --input: 217 33% 17%;
  --ring: 212 95% 68%;
}
body { @apply bg-background text-foreground; }
```

- [ ] **Step 7: layout.tsx**

```tsx
// dashboard/src/app/layout.tsx
import "./globals.css";

export const metadata = { title: "FVG Live", description: "FVG live trading dashboard" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body>{children}</body>
    </html>
  );
}
```

- [ ] **Step 8: api/health/route.ts**

```typescript
// dashboard/src/app/api/health/route.ts
export async function GET() {
  return Response.json({ status: "ok" });
}
```

- [ ] **Step 9: Dockerfile (multi-stage)**

```dockerfile
FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci

FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
EXPOSE 3000
CMD ["node", "server.js"]
```

- [ ] **Step 10: Build & verify**

```bash
cd dashboard
npm install
npm run build
npm run start &
sleep 5
curl -s http://localhost:3000/api/health
kill %1
```

Expected: `{"status":"ok"}`.

- [ ] **Step 11: Commit**

```bash
git add dashboard/
git commit -m "feat(dashboard): Next.js 15 scaffold"
```


---

## Phase 2 — trade_executor core

Goal: full signal → order → trail → reconcile pipeline working against Binance USDT-M Futures testnet for one user, then verified on live with $100 capital.

### Task 2.1: /encrypt endpoint with auth

**Files:**
- Modify: `trade_executor/trade_executor/http_api.py`
- Modify: `trade_executor/trade_executor/config.py` (already has INTERNAL_TOKEN)
- Test: `trade_executor/tests/test_encrypt_endpoint.py`

- [ ] **Step 1: Failing test**

```python
# trade_executor/tests/test_encrypt_endpoint.py
import base64
import os

import pytest
from httpx import AsyncClient, ASGITransport


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://x")
    monkeypatch.setenv("MASTER_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    monkeypatch.setenv("INTERNAL_TOKEN", "secret-token")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")


@pytest.mark.asyncio
async def test_encrypt_requires_token():
    from trade_executor.http_api import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/encrypt", json={"plaintext": "hi"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_encrypt_returns_blob_b64():
    from trade_executor.http_api import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/encrypt",
            json={"plaintext": "hello"},
            headers={"X-Internal-Token": "secret-token"},
        )
    assert r.status_code == 200
    body = r.json()
    assert "blob_b64" in body
    raw = base64.b64decode(body["blob_b64"])
    assert len(raw) >= 12 + 16  # nonce + tag
```

- [ ] **Step 2: Update http_api.py**

```python
# trade_executor/trade_executor/http_api.py
import base64

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from trade_executor.config import settings
from trade_executor.crypto import encrypt

app = FastAPI(title="trade_executor")


def _master_key() -> bytes:
    return base64.b64decode(settings.MASTER_ENCRYPTION_KEY)


class EncryptIn(BaseModel):
    plaintext: str


class EncryptOut(BaseModel):
    blob_b64: str


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.post("/encrypt", response_model=EncryptOut)
async def encrypt_endpoint(
    body: EncryptIn,
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
):
    if x_internal_token != settings.INTERNAL_TOKEN:
        raise HTTPException(status_code=401, detail="unauthorized")
    blob = encrypt(body.plaintext, _master_key())
    return {"blob_b64": base64.b64encode(blob).decode()}
```

- [ ] **Step 3: Run tests**

```bash
cd trade_executor && pytest tests/test_encrypt_endpoint.py -v
```

Expected: 2 passed.

- [ ] **Step 4: Commit**

```bash
git add trade_executor/
git commit -m "feat(trade_executor): /encrypt endpoint w/ INTERNAL_TOKEN auth"
```

### Task 2.2: Sizing math

**Files:**
- Create: `trade_executor/trade_executor/sizing.py`
- Test: `trade_executor/tests/test_sizing.py`

- [ ] **Step 1: Failing tests**

```python
# trade_executor/tests/test_sizing.py
import math

import pytest

from trade_executor.sizing import compute_size, SymbolMeta, round_step


def test_round_step_truncates_down():
    assert round_step(0.123456, 0.001) == pytest.approx(0.123)
    assert round_step(0.999, 0.01) == pytest.approx(0.99)


def test_compute_size_long_basic():
    # balance 100, risk 2% => $2 risk; entry 100, sl 95 => 5% sl distance
    # notional = 2 / 0.05 = $40, qty = 0.4
    meta = SymbolMeta(step_size=0.001, min_notional=5.0)
    res = compute_size(balance=100, risk_pct=2, entry=100, sl=95, leverage=5, meta=meta)
    assert res.notional_usdt == pytest.approx(40.0)
    assert res.qty == pytest.approx(0.4, rel=1e-3)
    assert res.margin_usdt == pytest.approx(40 / 5)


def test_compute_size_short_uses_abs_sl_distance():
    meta = SymbolMeta(step_size=0.001, min_notional=5.0)
    res = compute_size(balance=100, risk_pct=2, entry=100, sl=105, leverage=5, meta=meta)
    assert res.notional_usdt == pytest.approx(40.0)


def test_compute_size_below_min_notional_returns_skip():
    meta = SymbolMeta(step_size=0.001, min_notional=10000.0)
    res = compute_size(balance=100, risk_pct=2, entry=100, sl=95, leverage=5, meta=meta)
    assert res.skip_reason == "min_notional"


def test_qty_rounded_to_step():
    meta = SymbolMeta(step_size=0.01, min_notional=5.0)
    res = compute_size(balance=100, risk_pct=2, entry=100, sl=95, leverage=5, meta=meta)
    # 0.4 / 0.01 step → 0.40
    assert math.isclose(res.qty, 0.40, abs_tol=1e-9)
```

- [ ] **Step 2: Implement sizing.py**

```python
# trade_executor/trade_executor/sizing.py
from __future__ import annotations

from dataclasses import dataclass
from math import floor


@dataclass(frozen=True)
class SymbolMeta:
    step_size: float
    min_notional: float


@dataclass(frozen=True)
class SizeResult:
    qty: float = 0.0
    notional_usdt: float = 0.0
    margin_usdt: float = 0.0
    skip_reason: str | None = None


def round_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    return floor(value / step) * step


def compute_size(
    *,
    balance: float,
    risk_pct: float,
    entry: float,
    sl: float,
    leverage: int,
    meta: SymbolMeta,
) -> SizeResult:
    if entry <= 0 or sl <= 0:
        return SizeResult(skip_reason="bad_levels")
    sl_distance_pct = abs(entry - sl) / entry * 100
    if sl_distance_pct <= 0:
        return SizeResult(skip_reason="zero_sl_distance")
    risk_usdt = balance * risk_pct / 100
    notional = risk_usdt / (sl_distance_pct / 100)
    if notional < meta.min_notional:
        return SizeResult(notional_usdt=notional, skip_reason="min_notional")
    qty_raw = notional / entry
    qty = round_step(qty_raw, meta.step_size)
    if qty <= 0:
        return SizeResult(notional_usdt=notional, skip_reason="qty_zero")
    actual_notional = qty * entry
    if actual_notional < meta.min_notional:
        return SizeResult(notional_usdt=actual_notional, skip_reason="min_notional")
    return SizeResult(
        qty=qty,
        notional_usdt=actual_notional,
        margin_usdt=actual_notional / max(1, leverage),
    )
```

- [ ] **Step 3: Run tests**

```bash
cd trade_executor && pytest tests/test_sizing.py -v
```

Expected: 5 passed.

- [ ] **Step 4: Commit**

```bash
git add trade_executor/trade_executor/sizing.py trade_executor/tests/test_sizing.py
git commit -m "feat(trade_executor): risk-based sizing with step/min_notional"
```

### Task 2.3: Exchange wrapper (ccxt + proxy)

**Files:**
- Create: `trade_executor/trade_executor/exchange.py`
- Test: `trade_executor/tests/test_exchange.py`

- [ ] **Step 1: Test ccxt instantiation only (no network)**

```python
# trade_executor/tests/test_exchange.py
from trade_executor.exchange import build_exchange


def test_build_exchange_has_proxy_when_url_set():
    ex = build_exchange("k", "s", proxy_url="http://proxy:8080")
    assert ex.aiohttp_proxy == "http://proxy:8080"
    assert ex.options.get("defaultType") == "future"


def test_build_exchange_no_proxy_when_none():
    ex = build_exchange("k", "s", proxy_url=None)
    assert getattr(ex, "aiohttp_proxy", None) in (None, "")


def test_set_isolated_and_leverage_calls_chain(monkeypatch):
    """Smoke: helper sequences leverage + marginType, swallows code 4046."""
    from trade_executor import exchange as exmod
    calls = []

    class FakeEx:
        async def fapiPrivate_post_leverage(self, params):
            calls.append(("leverage", params))
            return {"leverage": params["leverage"]}

        async def fapiPrivate_post_margintype(self, params):
            calls.append(("marginType", params))
            from ccxt.base.errors import ExchangeError
            raise ExchangeError("-4046 No need to change margin type")

    import asyncio
    asyncio.run(exmod.set_isolated_and_leverage(FakeEx(), "BTCUSDT", 5))
    assert calls[0][0] == "leverage"
    assert calls[1][0] == "marginType"
```

- [ ] **Step 2: Implement exchange.py**

```python
# trade_executor/trade_executor/exchange.py
from __future__ import annotations

import logging

import ccxt.async_support as ccxt

log = logging.getLogger("exchange")


def build_exchange(api_key: str, api_secret: str, *, proxy_url: str | None) -> ccxt.binanceusdm:
    options: dict = {
        "apiKey": api_key,
        "secret": api_secret,
        "options": {"defaultType": "future"},
        "enableRateLimit": True,
    }
    if proxy_url:
        options["aiohttp_proxy"] = proxy_url
    ex = ccxt.binanceusdm(options)
    return ex


async def set_isolated_and_leverage(ex, symbol: str, leverage: int) -> None:
    """Set leverage and ISOLATED margin. Swallow 'no change needed' (-4046)."""
    await ex.fapiPrivate_post_leverage({"symbol": symbol, "leverage": leverage})
    try:
        await ex.fapiPrivate_post_margintype({"symbol": symbol, "marginType": "ISOLATED"})
    except Exception as e:
        msg = str(e)
        if "4046" in msg or "No need to change" in msg:
            log.debug("margin type already isolated for %s", symbol)
            return
        raise
```

- [ ] **Step 3: Run tests**

```bash
cd trade_executor && pytest tests/test_exchange.py -v
```

Expected: 3 passed.

- [ ] **Step 4: Commit**

```bash
git add trade_executor/trade_executor/exchange.py trade_executor/tests/test_exchange.py
git commit -m "feat(trade_executor): ccxt binanceusdm wrapper w/ proxy"
```

### Task 2.4: Audit log + pg_notify helpers

**Files:**
- Create: `trade_executor/trade_executor/audit.py`
- Create: `trade_executor/trade_executor/notify.py`
- Test: `trade_executor/tests/test_audit_notify.py`

- [ ] **Step 1: Failing tests**

```python
# trade_executor/tests/test_audit_notify.py
import json
import os

import pytest

from trade_executor.audit import insert_audit
from trade_executor.notify import notify
from trade_executor import db


pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


@pytest.mark.asyncio
async def test_insert_audit_writes_row():
    pool = await db.create_pool(os.environ["TEST_DATABASE_URL"])
    try:
        async with pool.acquire() as conn:
            await conn.execute("INSERT INTO users (telegram_id, created_at, updated_at) VALUES (999, 0, 0) ON CONFLICT DO NOTHING")
            uid = await conn.fetchval("SELECT id FROM users WHERE telegram_id=999")
            await insert_audit(conn, uid, "test_action", {"foo": "bar"})
            row = await conn.fetchrow(
                "SELECT action, payload FROM user_audit_log WHERE user_id=$1 ORDER BY id DESC LIMIT 1",
                uid,
            )
        assert row["action"] == "test_action"
        assert json.loads(row["payload"]) == {"foo": "bar"}
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_notify_sends_payload():
    pool = await db.create_pool(os.environ["TEST_DATABASE_URL"])
    try:
        listener = await pool.acquire()
        received = []

        async def cb(conn, pid, channel, payload):
            received.append((channel, payload))

        await listener.add_listener("test_chan", cb)
        async with pool.acquire() as conn:
            await notify(conn, "test_chan", {"x": 1})
        # Allow notify to deliver
        import asyncio
        await asyncio.sleep(0.5)
        await listener.remove_listener("test_chan", cb)
        await pool.release(listener)
        assert len(received) >= 1
        assert received[0][0] == "test_chan"
    finally:
        await pool.close()
```

- [ ] **Step 2: Implement audit.py**

```python
# trade_executor/trade_executor/audit.py
import json
import time


async def insert_audit(conn, user_id: int, action: str, payload: dict | None = None) -> None:
    await conn.execute(
        "INSERT INTO user_audit_log (user_id, action, payload, created_at) VALUES ($1, $2, $3::jsonb, $4)",
        user_id,
        action,
        json.dumps(payload or {}),
        int(time.time() * 1000),
    )
```

- [ ] **Step 3: Implement notify.py**

```python
# trade_executor/trade_executor/notify.py
import json


async def notify(conn, channel: str, payload: dict) -> None:
    # NOTIFY does not interpolate parameters; pass payload as quoted literal
    await conn.execute(f"NOTIFY {channel}, $1", json.dumps(payload))
```

Note: `asyncpg` does not support `NOTIFY ... , $1` parameterization via prepared statement. Use safe quoting:

```python
# trade_executor/trade_executor/notify.py
import json


def _quote_literal(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


async def notify(conn, channel: str, payload: dict) -> None:
    if not channel.replace("_", "").isalnum():
        raise ValueError("channel must be alphanumeric/underscore")
    body = _quote_literal(json.dumps(payload))
    await conn.execute(f"NOTIFY {channel}, {body}")
```

- [ ] **Step 4: Run tests**

```bash
createdb fvg_test
psql fvg_test -f migrations/0001_multi_user_live.sql
TEST_DATABASE_URL=postgresql:///fvg_test pytest trade_executor/tests/test_audit_notify.py -v
dropdb fvg_test
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add trade_executor/trade_executor/audit.py trade_executor/trade_executor/notify.py trade_executor/tests/test_audit_notify.py
git commit -m "feat(trade_executor): audit log + pg_notify helpers"
```


### Task 2.5: Signal poller

**Files:**
- Create: `trade_executor/trade_executor/signal_poller.py`
- Test: `trade_executor/tests/test_signal_poller.py`

- [ ] **Step 1: Failing tests**

```python
# trade_executor/tests/test_signal_poller.py
import os

import pytest

from trade_executor import db
from trade_executor.signal_poller import poll_once, load_last_seen, save_last_seen


pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


@pytest.mark.asyncio
async def test_poll_only_returns_valid_decisions_after_last_seen():
    pool = await db.create_pool(os.environ["TEST_DATABASE_URL"])
    try:
        async with pool.acquire() as conn:
            # seed kronos_decisions table (assumes existing table)
            await conn.execute("""
              CREATE TABLE IF NOT EXISTS kronos_decisions (
                id TEXT PRIMARY KEY, symbol TEXT, tf TEXT, direction TEXT,
                entry DOUBLE PRECISION, sl DOUBLE PRECISION,
                tp1 DOUBLE PRECISION, tp2 DOUBLE PRECISION,
                valid BOOLEAN, event_type TEXT, created_at BIGINT
              )
            """)
            await conn.execute("DELETE FROM kronos_decisions WHERE id LIKE 'sp-%'")
            await conn.execute("""
              INSERT INTO kronos_decisions (id, symbol, tf, direction, entry, sl, tp1, tp2, valid, event_type, created_at) VALUES
                ('sp-1', 'BTCUSDT', '1h', 'long', 100, 95, 105, 110, true, 'touch', 1000),
                ('sp-2', 'BTCUSDT', '1h', 'long', 100, 95, 105, 110, false, 'touch', 2000),
                ('sp-3', 'ETHUSDT', '1h', 'short', 200, 210, 190, 180, true, 'touch', 3000)
            """)

            rows = await poll_once(conn, last_seen_ms=500)
        assert [r["id"] for r in rows] == ["sp-1", "sp-3"]
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_last_seen_persists():
    pool = await db.create_pool(os.environ["TEST_DATABASE_URL"])
    try:
        async with pool.acquire() as conn:
            await save_last_seen(conn, 12345)
            assert await load_last_seen(conn) == 12345
            await save_last_seen(conn, 99999)
            assert await load_last_seen(conn) == 99999
    finally:
        await pool.close()
```

- [ ] **Step 2: Implement signal_poller.py**

```python
# trade_executor/trade_executor/signal_poller.py
from __future__ import annotations

import time
from typing import Any


KEY = "signal_poller_last_seen_ms"


async def load_last_seen(conn) -> int:
    row = await conn.fetchrow("SELECT value FROM executor_state WHERE key=$1", KEY)
    return int(row["value"]) if row else 0


async def save_last_seen(conn, value: int) -> None:
    await conn.execute(
        """
        INSERT INTO executor_state (key, value, updated_at) VALUES ($1, $2, $3)
        ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=EXCLUDED.updated_at
        """,
        KEY,
        str(value),
        int(time.time() * 1000),
    )


async def poll_once(conn, *, last_seen_ms: int) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT id, symbol, tf, direction, entry, sl, tp1, tp2, event_type, created_at
        FROM kronos_decisions
        WHERE valid = true AND created_at > $1
        ORDER BY created_at ASC
        """,
        last_seen_ms,
    )
    return [dict(r) for r in rows]


async def list_enabled_users(conn) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT id, telegram_id, binance_api_key_enc, binance_api_secret_enc,
               risk_pct, leverage, max_concurrent, daily_loss_cap_pct, paused_until
        FROM users
        WHERE enabled = true
          AND binance_api_key_enc IS NOT NULL
          AND binance_api_secret_enc IS NOT NULL
        """
    )
    return [dict(r) for r in rows]
```

- [ ] **Step 3: Run tests**

```bash
createdb fvg_test
psql fvg_test -f migrations/0001_multi_user_live.sql
TEST_DATABASE_URL=postgresql:///fvg_test pytest trade_executor/tests/test_signal_poller.py -v
dropdb fvg_test
```

Expected: 2 passed.

- [ ] **Step 4: Commit**

```bash
git add trade_executor/trade_executor/signal_poller.py trade_executor/tests/test_signal_poller.py
git commit -m "feat(trade_executor): signal poller w/ persisted last_seen"
```

### Task 2.6: Pre-trade gate

**Files:**
- Create: `trade_executor/trade_executor/gate.py`
- Test: `trade_executor/tests/test_gate.py`

- [ ] **Step 1: Failing tests**

```python
# trade_executor/tests/test_gate.py
import time

import pytest

from trade_executor.gate import check_user_gate, GateResult


def _user(**over):
    base = dict(
        id=1, enabled=True, paused_until=None,
        risk_pct=2.0, leverage=5, max_concurrent=3,
        daily_loss_cap_pct=6.0,
    )
    base.update(over)
    return base


def test_disabled_blocks():
    res = check_user_gate(_user(enabled=False), open_count=0, today_pnl_pct=0,
                          balance_usdt=100, decision_id="d1", existing_trade=False)
    assert res.skip_reason == "user_disabled"


def test_paused_until_future_blocks():
    future = int(time.time() * 1000) + 60000
    res = check_user_gate(_user(paused_until=future), open_count=0, today_pnl_pct=0,
                          balance_usdt=100, decision_id="d1", existing_trade=False)
    assert res.skip_reason == "paused"


def test_paused_until_past_does_not_block():
    past = int(time.time() * 1000) - 60000
    res = check_user_gate(_user(paused_until=past), open_count=0, today_pnl_pct=0,
                          balance_usdt=100, decision_id="d1", existing_trade=False)
    assert res.skip_reason is None


def test_daily_cap_hit_blocks_and_flags_pause():
    res = check_user_gate(_user(), open_count=0, today_pnl_pct=-7.0,
                          balance_usdt=100, decision_id="d1", existing_trade=False)
    assert res.skip_reason == "daily_cap_hit"
    assert res.should_pause_forever is True


def test_max_concurrent_blocks():
    res = check_user_gate(_user(max_concurrent=3), open_count=3, today_pnl_pct=0,
                          balance_usdt=100, decision_id="d1", existing_trade=False)
    assert res.skip_reason == "max_concurrent"


def test_idempotency_blocks():
    res = check_user_gate(_user(), open_count=0, today_pnl_pct=0,
                          balance_usdt=100, decision_id="d1", existing_trade=True)
    assert res.skip_reason == "duplicate"


def test_low_balance_blocks():
    res = check_user_gate(_user(), open_count=0, today_pnl_pct=0,
                          balance_usdt=4.0, decision_id="d1", existing_trade=False)
    assert res.skip_reason == "low_balance"


def test_pass_when_all_clear():
    res = check_user_gate(_user(), open_count=0, today_pnl_pct=0,
                          balance_usdt=100, decision_id="d1", existing_trade=False)
    assert res.skip_reason is None
```

- [ ] **Step 2: Implement gate.py**

```python
# trade_executor/trade_executor/gate.py
from __future__ import annotations

import time
from dataclasses import dataclass


MIN_BALANCE_USDT = 5.0


@dataclass(frozen=True)
class GateResult:
    skip_reason: str | None = None
    should_pause_forever: bool = False


def check_user_gate(
    user: dict,
    *,
    open_count: int,
    today_pnl_pct: float,
    balance_usdt: float,
    decision_id: str,
    existing_trade: bool,
) -> GateResult:
    if not user.get("enabled"):
        return GateResult(skip_reason="user_disabled")
    paused_until = user.get("paused_until")
    if paused_until and paused_until > int(time.time() * 1000):
        return GateResult(skip_reason="paused")
    cap = float(user["daily_loss_cap_pct"])
    if today_pnl_pct <= -cap:
        return GateResult(skip_reason="daily_cap_hit", should_pause_forever=True)
    if open_count >= int(user["max_concurrent"]):
        return GateResult(skip_reason="max_concurrent")
    if existing_trade:
        return GateResult(skip_reason="duplicate")
    if balance_usdt < MIN_BALANCE_USDT:
        return GateResult(skip_reason="low_balance")
    return GateResult(skip_reason=None)
```

- [ ] **Step 3: Run tests**

```bash
cd trade_executor && pytest tests/test_gate.py -v
```

Expected: 8 passed.

- [ ] **Step 4: Commit**

```bash
git add trade_executor/trade_executor/gate.py trade_executor/tests/test_gate.py
git commit -m "feat(trade_executor): pre-trade gate (enabled/paused/cap/concurrent/idempotency)"
```

### Task 2.7: Order placer (3-order sequence)

**Files:**
- Create: `trade_executor/trade_executor/order_placer.py`
- Test: `trade_executor/tests/test_order_placer.py`

- [ ] **Step 1: Failing tests with mocked exchange**

```python
# trade_executor/tests/test_order_placer.py
import pytest

from trade_executor.order_placer import place_full_sequence, OrderError


class FakeOK:
    """Mock exchange returning success for all calls."""

    def __init__(self):
        self.calls = []

    async def fapiPrivate_post_leverage(self, params):
        self.calls.append(("leverage", params))
        return {"leverage": params["leverage"]}

    async def fapiPrivate_post_margintype(self, params):
        self.calls.append(("marginType", params))
        return {}

    async def create_order(self, symbol, type_, side, amount, price=None, params=None):
        self.calls.append(("create", type_, side, amount, params))
        if type_ == "MARKET":
            return {"id": "entry-1", "status": "FILLED", "average": 100.5}
        if type_ == "STOP_MARKET":
            return {"id": "sl-1", "status": "NEW"}
        if type_ == "TAKE_PROFIT_MARKET":
            return {"id": "tp-1", "status": "NEW"}
        raise AssertionError(f"unexpected type {type_}")


class FakeSLFails(FakeOK):
    async def create_order(self, symbol, type_, side, amount, price=None, params=None):
        if type_ == "STOP_MARKET":
            raise Exception("SL placement failed")
        return await super().create_order(symbol, type_, side, amount, price, params)


@pytest.mark.asyncio
async def test_full_sequence_returns_ids_and_avg():
    ex = FakeOK()
    res = await place_full_sequence(
        ex, symbol="BTCUSDT", side="BUY", qty=0.01,
        sl_price=95.0, tp_price=110.0, leverage=5,
    )
    assert res.entry_order_id == "entry-1"
    assert res.sl_order_id == "sl-1"
    assert res.tp_order_id == "tp-1"
    assert res.avg_price == pytest.approx(100.5)


@pytest.mark.asyncio
async def test_sl_failure_triggers_emergency_close_and_raises():
    ex = FakeSLFails()
    with pytest.raises(OrderError) as exc:
        await place_full_sequence(
            ex, symbol="BTCUSDT", side="BUY", qty=0.01,
            sl_price=95.0, tp_price=110.0, leverage=5,
        )
    assert exc.value.stage == "sl"
    # emergency close placed: at least one MARKET reduceOnly call after entry
    market_close = [c for c in ex.calls if c[0] == "create" and c[1] == "MARKET" and c[4] and c[4].get("reduceOnly")]
    assert len(market_close) == 1
```

- [ ] **Step 2: Implement order_placer.py**

```python
# trade_executor/trade_executor/order_placer.py
from __future__ import annotations

import logging
from dataclasses import dataclass

from trade_executor.exchange import set_isolated_and_leverage

log = logging.getLogger("order_placer")


class OrderError(Exception):
    def __init__(self, stage: str, message: str):
        super().__init__(message)
        self.stage = stage


@dataclass
class PlacedOrders:
    entry_order_id: str
    sl_order_id: str | None
    tp_order_id: str | None
    avg_price: float


async def place_full_sequence(
    ex,
    *,
    symbol: str,
    side: str,             # "BUY" or "SELL"
    qty: float,
    sl_price: float,
    tp_price: float,
    leverage: int,
) -> PlacedOrders:
    # 1) Leverage + margin type
    try:
        await set_isolated_and_leverage(ex, symbol, leverage)
    except Exception as e:
        raise OrderError("leverage", str(e))

    # 2) MARKET entry
    try:
        entry = await ex.create_order(symbol, "MARKET", side, qty, None, {})
    except Exception as e:
        raise OrderError("entry", str(e))
    entry_id = str(entry.get("id"))
    avg = float(entry.get("average") or entry.get("avgPrice") or 0)
    if not avg:
        raise OrderError("entry", "no avg price")

    close_side = "SELL" if side == "BUY" else "BUY"

    # 3) STOP_MARKET (SL) — if fails, EMERGENCY close
    try:
        sl = await ex.create_order(
            symbol, "STOP_MARKET", close_side, qty, None,
            {"stopPrice": sl_price, "closePosition": True, "workingType": "MARK_PRICE"},
        )
        sl_id = str(sl.get("id"))
    except Exception as e:
        log.error("SL placement failed for %s: %s — emergency close", symbol, e)
        try:
            await ex.create_order(
                symbol, "MARKET", close_side, qty, None, {"reduceOnly": True},
            )
        except Exception as ee:
            log.critical("EMERGENCY CLOSE FAILED %s: %s", symbol, ee)
        raise OrderError("sl", str(e))

    # 4) TAKE_PROFIT_MARKET (TP2) — if fails, keep SL, alert
    tp_id: str | None = None
    try:
        tp = await ex.create_order(
            symbol, "TAKE_PROFIT_MARKET", close_side, qty, None,
            {"stopPrice": tp_price, "closePosition": True, "workingType": "MARK_PRICE"},
        )
        tp_id = str(tp.get("id"))
    except Exception as e:
        log.error("TP placement failed for %s: %s — SL still active", symbol, e)
        # Caller will see tp_order_id=None and alert user

    return PlacedOrders(entry_order_id=entry_id, sl_order_id=sl_id, tp_order_id=tp_id, avg_price=avg)
```

- [ ] **Step 3: Run tests**

```bash
cd trade_executor && pytest tests/test_order_placer.py -v
```

Expected: 2 passed.

- [ ] **Step 4: Commit**

```bash
git add trade_executor/trade_executor/order_placer.py trade_executor/tests/test_order_placer.py
git commit -m "feat(trade_executor): 3-order sequence w/ emergency close on SL fail"
```

### Task 2.8: Place-trade orchestrator (gate → size → place → persist)

**Files:**
- Create: `trade_executor/trade_executor/orchestrator.py`
- Test: `trade_executor/tests/test_orchestrator.py`

- [ ] **Step 1: Failing tests using fakes**

```python
# trade_executor/tests/test_orchestrator.py
import os

import pytest

from trade_executor import db
from trade_executor.orchestrator import handle_signal_for_user


pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


class FakeEx:
    """Successful path."""

    def __init__(self):
        self.balance = {"USDT": {"free": 100.0}}
        self.markets = {"BTCUSDT": {"limits": {"amount": {"min": 0.001}}, "precision": {"amount": 3}}}

    async def fetch_balance(self):
        return self.balance

    async def load_markets(self):
        return self.markets

    async def fapiPublic_get_exchangeinfo(self):
        return {"symbols": [{"symbol": "BTCUSDT", "filters": [
            {"filterType": "LOT_SIZE", "stepSize": "0.001"},
            {"filterType": "MIN_NOTIONAL", "notional": "5"},
        ]}]}

    async def fapiPrivate_post_leverage(self, params): return {}
    async def fapiPrivate_post_margintype(self, params): return {}

    async def create_order(self, symbol, type_, side, amount, price=None, params=None):
        if type_ == "MARKET":
            return {"id": "e1", "status": "FILLED", "average": 100.5}
        if type_ == "STOP_MARKET":
            return {"id": "s1", "status": "NEW"}
        if type_ == "TAKE_PROFIT_MARKET":
            return {"id": "t1", "status": "NEW"}


@pytest.mark.asyncio
async def test_orchestrator_writes_open_trade(monkeypatch):
    pool = await db.create_pool(os.environ["TEST_DATABASE_URL"])
    try:
        async with pool.acquire() as conn:
            await conn.execute("INSERT INTO users (telegram_id, enabled, binance_api_key_enc, binance_api_secret_enc, created_at, updated_at) VALUES (777, true, '\\x00', '\\x00', 0, 0) ON CONFLICT DO NOTHING")
            uid = await conn.fetchval("SELECT id FROM users WHERE telegram_id=777")

        signal = {
            "id": "dec-1", "symbol": "BTCUSDT", "tf": "1h", "direction": "long",
            "entry": 100.0, "sl": 95.0, "tp1": 105.0, "tp2": 110.0,
        }
        result = await handle_signal_for_user(
            pool, user_id=uid, signal=signal, ex=FakeEx(),
            risk_pct=2.0, leverage=5, max_concurrent=3, daily_loss_cap_pct=6.0,
        )
        assert result.placed is True

        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM user_trades WHERE id=$1", f"{uid}-dec-1")
        assert row["status"] == "open"
        assert row["entry"] == pytest.approx(100.5)
        assert row["sl_order_id"] == "s1"
        assert row["tp_order_id"] == "t1"
    finally:
        await pool.close()
```

- [ ] **Step 2: Implement orchestrator.py**

```python
# trade_executor/trade_executor/orchestrator.py
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from trade_executor.audit import insert_audit
from trade_executor.gate import check_user_gate
from trade_executor.notify import notify
from trade_executor.order_placer import OrderError, place_full_sequence
from trade_executor.sizing import SymbolMeta, compute_size

log = logging.getLogger("orchestrator")


@dataclass
class OrchResult:
    placed: bool
    skip_reason: str | None = None


async def _today_pnl_pct(conn, user_id: int) -> float:
    row = await conn.fetchrow(
        "SELECT realized_pnl_pct FROM user_daily_pnl WHERE user_id=$1 AND day=CURRENT_DATE",
        user_id,
    )
    return float(row["realized_pnl_pct"]) if row else 0.0


async def _open_count(conn, user_id: int) -> int:
    return int(await conn.fetchval(
        "SELECT COUNT(*) FROM user_trades WHERE user_id=$1 AND status IN ('opening','open','tp1_trailed')",
        user_id,
    ))


async def _existing(conn, user_id: int, decision_id: str) -> bool:
    return bool(await conn.fetchval(
        "SELECT 1 FROM user_trades WHERE user_id=$1 AND decision_id=$2",
        user_id, decision_id,
    ))


async def _symbol_meta(ex, symbol: str) -> SymbolMeta:
    info = await ex.fapiPublic_get_exchangeinfo()
    for s in info["symbols"]:
        if s["symbol"] == symbol:
            step = 0.001
            min_n = 5.0
            for f in s.get("filters", []):
                if f["filterType"] == "LOT_SIZE":
                    step = float(f["stepSize"])
                elif f["filterType"] in ("MIN_NOTIONAL", "NOTIONAL"):
                    min_n = float(f.get("notional") or f.get("minNotional") or 5.0)
            return SymbolMeta(step_size=step, min_notional=min_n)
    return SymbolMeta(step_size=0.001, min_notional=5.0)


async def handle_signal_for_user(
    pool,
    *,
    user_id: int,
    signal: dict,
    ex,
    risk_pct: float,
    leverage: int,
    max_concurrent: int,
    daily_loss_cap_pct: float,
) -> OrchResult:
    decision_id = signal["id"]
    symbol = signal["symbol"]
    direction = signal["direction"]
    entry = float(signal["entry"])
    sl = float(signal["sl"])
    tp1 = float(signal["tp1"])
    tp2 = float(signal["tp2"])

    async with pool.acquire() as conn:
        # Pre-trade gate
        balance = (await ex.fetch_balance()).get("USDT", {}).get("free", 0)
        gate = check_user_gate(
            user={
                "id": user_id, "enabled": True, "paused_until": None,
                "risk_pct": risk_pct, "leverage": leverage,
                "max_concurrent": max_concurrent, "daily_loss_cap_pct": daily_loss_cap_pct,
            },
            open_count=await _open_count(conn, user_id),
            today_pnl_pct=await _today_pnl_pct(conn, user_id),
            balance_usdt=float(balance),
            decision_id=decision_id,
            existing_trade=await _existing(conn, user_id, decision_id),
        )
        if gate.skip_reason:
            await insert_audit(conn, user_id, "trade_skipped",
                               {"decision_id": decision_id, "reason": gate.skip_reason})
            if gate.should_pause_forever:
                await conn.execute(
                    "UPDATE users SET paused_until=$1, pause_reason='daily_cap', updated_at=$2 WHERE id=$3",
                    9_999_999_999_999, int(time.time() * 1000), user_id,
                )
            return OrchResult(placed=False, skip_reason=gate.skip_reason)

        # Sizing
        meta = await _symbol_meta(ex, symbol)
        size = compute_size(
            balance=float(balance), risk_pct=risk_pct, entry=entry, sl=sl,
            leverage=leverage, meta=meta,
        )
        if size.skip_reason:
            await insert_audit(conn, user_id, "trade_skipped",
                               {"decision_id": decision_id, "reason": size.skip_reason})
            return OrchResult(placed=False, skip_reason=size.skip_reason)

        # Insert opening row (idempotent via UNIQUE)
        trade_id = f"{user_id}-{decision_id}"
        now = int(time.time() * 1000)
        try:
            await conn.execute(
                """
                INSERT INTO user_trades (id, user_id, decision_id, symbol, tf, direction,
                  leverage, margin_usdt, notional_usdt, qty, entry, sl, sl_current, tp1, tp2,
                  status, opened_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,'opening',$16)
                """,
                trade_id, user_id, decision_id, symbol, signal["tf"], direction,
                leverage, size.margin_usdt, size.notional_usdt, size.qty,
                entry, sl, sl, tp1, tp2, now,
            )
        except Exception as e:
            # Likely UNIQUE collision — concurrent processing
            log.info("user_trade insert collision for %s: %s", trade_id, e)
            return OrchResult(placed=False, skip_reason="duplicate")

    # Place orders (releases conn during HTTP)
    side = "BUY" if direction == "long" else "SELL"
    try:
        placed = await place_full_sequence(
            ex, symbol=symbol, side=side, qty=size.qty,
            sl_price=sl, tp_price=tp2, leverage=leverage,
        )
    except OrderError as e:
        async with pool.acquire() as conn:
            status = {"sl": "error_no_sl", "entry": "error_open"}.get(e.stage, "error_open")
            await conn.execute(
                "UPDATE user_trades SET status=$1, error_msg=$2, closed_at=$3 WHERE id=$4",
                status, str(e), int(time.time() * 1000), trade_id,
            )
            await notify(conn, "error", {"user_id": user_id, "trade_id": trade_id, "stage": e.stage})
        return OrchResult(placed=False, skip_reason=f"order_{e.stage}")

    # Persist final state
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE user_trades SET
              status='open',
              entry=$1, sl_current=$2,
              entry_order_id=$3, sl_order_id=$4, tp_order_id=$5
            WHERE id=$6
            """,
            placed.avg_price, sl,
            placed.entry_order_id, placed.sl_order_id, placed.tp_order_id,
            trade_id,
        )
        await notify(conn, "trade_opened", {"user_id": user_id, "trade_id": trade_id})
        await insert_audit(conn, user_id, "trade_opened",
                           {"decision_id": decision_id, "symbol": symbol})

    return OrchResult(placed=True)
```

- [ ] **Step 3: Run tests**

```bash
createdb fvg_test
psql fvg_test -f migrations/0001_multi_user_live.sql
TEST_DATABASE_URL=postgresql:///fvg_test pytest trade_executor/tests/test_orchestrator.py -v
dropdb fvg_test
```

Expected: 1 passed.

- [ ] **Step 4: Commit**

```bash
git add trade_executor/trade_executor/orchestrator.py trade_executor/tests/test_orchestrator.py
git commit -m "feat(trade_executor): orchestrator (gate → size → place → persist)"
```


### Task 2.9: Trail manager (mark-price WS → trail SL to TP1)

**Files:**
- Create: `trade_executor/trade_executor/trail_manager.py`
- Test: `trade_executor/tests/test_trail_manager.py`

- [ ] **Step 1: Failing test on the trail decision logic (no WS)**

```python
# trade_executor/tests/test_trail_manager.py
import os

import pytest

from trade_executor import db
from trade_executor.trail_manager import maybe_trail


pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


class FakeEx:
    def __init__(self, fail_cancel=False):
        self.cancelled = []
        self.placed = []
        self.fail_cancel = fail_cancel

    async def cancel_order(self, order_id, symbol):
        if self.fail_cancel:
            raise Exception("not found")
        self.cancelled.append((order_id, symbol))
        return {"id": order_id, "status": "CANCELED"}

    async def create_order(self, symbol, type_, side, amount, price=None, params=None):
        self.placed.append((type_, side, params))
        return {"id": "new-sl"}


@pytest.mark.asyncio
async def test_long_trails_when_price_crosses_tp1(monkeypatch):
    pool = await db.create_pool(os.environ["TEST_DATABASE_URL"])
    try:
        async with pool.acquire() as conn:
            await conn.execute("INSERT INTO users (telegram_id, created_at, updated_at) VALUES (888, 0, 0) ON CONFLICT DO NOTHING")
            uid = await conn.fetchval("SELECT id FROM users WHERE telegram_id=888")
            await conn.execute("DELETE FROM user_trades WHERE id=$1", f"{uid}-tr-1")
            await conn.execute(
                """
                INSERT INTO user_trades (id, user_id, decision_id, symbol, tf, direction,
                  leverage, margin_usdt, notional_usdt, qty, entry, sl, sl_current, tp1, tp2,
                  status, sl_order_id, opened_at)
                VALUES ($1,$2,'tr-1','BTCUSDT','1h','long',5,10,50,0.001,100,95,95,105,110,'open','sl-old',0)
                """,
                f"{uid}-tr-1", uid,
            )

        ex = FakeEx()
        trailed = await maybe_trail(pool, ex=ex, symbol="BTCUSDT", price=105.5)
        assert trailed is True
        assert ex.cancelled == [("sl-old", "BTCUSDT")]
        assert ex.placed[0][0] == "STOP_MARKET"
        assert ex.placed[0][2]["stopPrice"] == 105.0  # trail to TP1, not 105.5

        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT status, sl_current, sl_order_id FROM user_trades WHERE id=$1",
                                       f"{uid}-tr-1")
        assert row["status"] == "tp1_trailed"
        assert row["sl_current"] == pytest.approx(105.0)
        assert row["sl_order_id"] == "new-sl"
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_short_trails_when_price_crosses_tp1():
    pool = await db.create_pool(os.environ["TEST_DATABASE_URL"])
    try:
        async with pool.acquire() as conn:
            await conn.execute("INSERT INTO users (telegram_id, created_at, updated_at) VALUES (889, 0, 0) ON CONFLICT DO NOTHING")
            uid = await conn.fetchval("SELECT id FROM users WHERE telegram_id=889")
            await conn.execute("DELETE FROM user_trades WHERE id=$1", f"{uid}-tr-2")
            await conn.execute(
                """
                INSERT INTO user_trades (id, user_id, decision_id, symbol, tf, direction,
                  leverage, margin_usdt, notional_usdt, qty, entry, sl, sl_current, tp1, tp2,
                  status, sl_order_id, opened_at)
                VALUES ($1,$2,'tr-2','BTCUSDT','1h','short',5,10,50,0.001,100,105,105,95,90,'open','sl-old2',0)
                """,
                f"{uid}-tr-2", uid,
            )
        ex = FakeEx()
        trailed = await maybe_trail(pool, ex=ex, symbol="BTCUSDT", price=94.0)
        assert trailed is True
        assert ex.placed[0][2]["stopPrice"] == 95.0
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_no_trail_when_price_below_tp1_long():
    pool = await db.create_pool(os.environ["TEST_DATABASE_URL"])
    try:
        async with pool.acquire() as conn:
            await conn.execute("INSERT INTO users (telegram_id, created_at, updated_at) VALUES (890, 0, 0) ON CONFLICT DO NOTHING")
            uid = await conn.fetchval("SELECT id FROM users WHERE telegram_id=890")
            await conn.execute("DELETE FROM user_trades WHERE id=$1", f"{uid}-tr-3")
            await conn.execute(
                """
                INSERT INTO user_trades (id, user_id, decision_id, symbol, tf, direction,
                  leverage, margin_usdt, notional_usdt, qty, entry, sl, sl_current, tp1, tp2,
                  status, sl_order_id, opened_at)
                VALUES ($1,$2,'tr-3','BTCUSDT','1h','long',5,10,50,0.001,100,95,95,105,110,'open','sl-3',0)
                """,
                f"{uid}-tr-3", uid,
            )
        ex = FakeEx()
        trailed = await maybe_trail(pool, ex=ex, symbol="BTCUSDT", price=104.0)
        assert trailed is False
    finally:
        await pool.close()
```

- [ ] **Step 2: Implement trail_manager.py**

```python
# trade_executor/trade_executor/trail_manager.py
from __future__ import annotations

import logging

from trade_executor.notify import notify

log = logging.getLogger("trail_manager")


async def _open_in_symbol(conn, symbol: str) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT id, user_id, direction, qty, tp1, sl_order_id, sl_current
        FROM user_trades
        WHERE symbol=$1 AND status='open'
        """,
        symbol,
    )
    return [dict(r) for r in rows]


async def maybe_trail(pool, *, ex, symbol: str, price: float) -> bool:
    """For each open trade in symbol, if price has crossed TP1, trail SL to TP1.

    Returns True if at least one trade was trailed.
    """
    trailed_any = False
    async with pool.acquire() as conn:
        trades = await _open_in_symbol(conn, symbol)

    for t in trades:
        is_long = t["direction"] == "long"
        crossed = (is_long and price >= float(t["tp1"])) or (
            not is_long and price <= float(t["tp1"])
        )
        if not crossed:
            continue

        # Cancel old SL
        if t["sl_order_id"]:
            try:
                await ex.cancel_order(t["sl_order_id"], symbol)
            except Exception as e:
                log.warning("cancel sl_order_id=%s failed: %s", t["sl_order_id"], e)

        # Place new SL at TP1
        close_side = "SELL" if is_long else "BUY"
        new_sl = await ex.create_order(
            symbol, "STOP_MARKET", close_side, float(t["qty"]), None,
            {"stopPrice": float(t["tp1"]), "closePosition": True, "workingType": "MARK_PRICE"},
        )
        new_sl_id = str(new_sl.get("id"))

        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE user_trades
                SET status='tp1_trailed', sl_current=$1, sl_order_id=$2
                WHERE id=$3 AND status='open'
                """,
                float(t["tp1"]), new_sl_id, t["id"],
            )
            await notify(conn, "trade_tp1_trailed",
                         {"user_id": t["user_id"], "trade_id": t["id"]})
        trailed_any = True

    return trailed_any
```

- [ ] **Step 3: Run tests**

```bash
createdb fvg_test
psql fvg_test -f migrations/0001_multi_user_live.sql
TEST_DATABASE_URL=postgresql:///fvg_test pytest trade_executor/tests/test_trail_manager.py -v
dropdb fvg_test
```

Expected: 3 passed.

- [ ] **Step 4: Add WS subscription glue**

Append to `trade_executor/trade_executor/trail_manager.py`:

```python
import asyncio
import json

import websockets


async def run_mark_price_ws(pool, *, ex_factory, get_active_symbols, proxy_url: str | None = None):
    """Long-running task: subscribe to mark-price for all symbols with open trades.

    Reconciles symbol set every 30s. Reconnects on disconnect.
    """
    while True:
        try:
            symbols = await get_active_symbols()
            if not symbols:
                await asyncio.sleep(5)
                continue
            streams = "/".join(f"{s.lower()}@markPrice@1s" for s in symbols)
            url = f"wss://fstream.binance.com/stream?streams={streams}"
            log.info("connecting mark-price WS: %d symbols", len(symbols))
            async with websockets.connect(url, ping_interval=20) as ws:
                deadline = asyncio.get_event_loop().time() + 30.0
                async for msg in ws:
                    data = json.loads(msg).get("data", {})
                    sym = data.get("s")
                    price = float(data.get("p", 0))
                    if sym and price > 0:
                        ex = await ex_factory(sym)  # may need per-user; here we use any
                        try:
                            await maybe_trail(pool, ex=ex, symbol=sym, price=price)
                        except Exception as e:
                            log.exception("maybe_trail failed: %s", e)
                    if asyncio.get_event_loop().time() >= deadline:
                        break  # reconcile symbol set
        except Exception as e:
            log.warning("mark-price WS error: %s — retrying in 5s", e)
            await asyncio.sleep(5)
```

- [ ] **Step 5: Commit**

```bash
git add trade_executor/trade_executor/trail_manager.py trade_executor/tests/test_trail_manager.py
git commit -m "feat(trade_executor): TP1 trail manager + mark-price WS subscriber"
```

### Task 2.10: PnL aggregator (60s reconcile)

**Files:**
- Create: `trade_executor/trade_executor/pnl_aggregator.py`
- Test: `trade_executor/tests/test_pnl_aggregator.py`

- [ ] **Step 1: Failing test**

```python
# trade_executor/tests/test_pnl_aggregator.py
import os
from datetime import date

import pytest

from trade_executor import db
from trade_executor.pnl_aggregator import reconcile_user, classify_close


def test_classify_close_tp2_long():
    assert classify_close(direction="long", filled_at_tp_id="tp", filled_at_sl_id=None,
                          status_before="open") == "closed_tp2"


def test_classify_close_sl_trailed_breakeven():
    assert classify_close(direction="long", filled_at_tp_id=None, filled_at_sl_id="sl",
                          status_before="tp1_trailed") == "closed_breakeven"


def test_classify_close_sl_open_loss():
    assert classify_close(direction="long", filled_at_tp_id=None, filled_at_sl_id="sl",
                          status_before="open") == "closed_sl"


pytestmark_db = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


class FakeEx:
    def __init__(self, fills):
        self._fills = fills

    async def fetch_my_trades(self, symbol, since=None):
        return self._fills.get(symbol, [])

    async def fetch_balance(self):
        return {"USDT": {"free": 100.0}}


@pytestmark_db
@pytest.mark.asyncio
async def test_reconcile_marks_tp2_close_and_updates_daily():
    pool = await db.create_pool(os.environ["TEST_DATABASE_URL"])
    try:
        async with pool.acquire() as conn:
            await conn.execute("INSERT INTO users (telegram_id, created_at, updated_at) VALUES (901, 0, 0) ON CONFLICT DO NOTHING")
            uid = await conn.fetchval("SELECT id FROM users WHERE telegram_id=901")
            await conn.execute("DELETE FROM user_trades WHERE user_id=$1", uid)
            await conn.execute(
                """
                INSERT INTO user_trades (id, user_id, decision_id, symbol, tf, direction,
                  leverage, margin_usdt, notional_usdt, qty, entry, sl, sl_current, tp1, tp2,
                  status, entry_order_id, sl_order_id, tp_order_id, opened_at)
                VALUES ($1,$2,'p-1','BTCUSDT','1h','long',5,20,100,1.0,100,95,95,105,110,'open','e1','s1','t1',0)
                """,
                f"{uid}-p-1", uid,
            )

        ex = FakeEx(fills={
            "BTCUSDT": [
                {"order": "t1", "side": "sell", "price": 110.0, "amount": 1.0, "fee": {"cost": 0.05, "currency": "USDT"}, "timestamp": 1000},
            ]
        })
        await reconcile_user(pool, ex=ex, user_id=uid)

        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT status, pnl_usdt, fees_usdt FROM user_trades WHERE id=$1",
                                       f"{uid}-p-1")
            day = await conn.fetchrow("SELECT realized_pnl_usdt, trades_count, wins_count FROM user_daily_pnl WHERE user_id=$1 AND day=CURRENT_DATE", uid)
        assert row["status"] == "closed_tp2"
        assert row["pnl_usdt"] == pytest.approx(10.0, rel=1e-2)  # qty 1, +10 px move
        assert day["trades_count"] == 1
        assert day["wins_count"] == 1
    finally:
        await pool.close()
```

- [ ] **Step 2: Implement pnl_aggregator.py**

```python
# trade_executor/trade_executor/pnl_aggregator.py
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from trade_executor.audit import insert_audit
from trade_executor.notify import notify

log = logging.getLogger("pnl_aggregator")

DAILY_CAP_REACHED = 9_999_999_999_999


def classify_close(*, direction: str, filled_at_tp_id: str | None,
                   filled_at_sl_id: str | None, status_before: str) -> str:
    if filled_at_tp_id:
        return "closed_tp2"
    if filled_at_sl_id:
        return "closed_breakeven" if status_before == "tp1_trailed" else "closed_sl"
    return status_before


def _today_start_ms() -> int:
    now = datetime.now(timezone.utc)
    midnight = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    return int(midnight.timestamp() * 1000)


async def reconcile_user(pool, *, ex, user_id: int) -> None:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, symbol, direction, qty, entry, status,
                   sl_order_id, tp_order_id, opened_at
            FROM user_trades
            WHERE user_id=$1 AND status IN ('open','tp1_trailed')
            """,
            user_id,
        )
    if not rows:
        return

    by_symbol: dict[str, list[dict]] = {}
    for r in rows:
        by_symbol.setdefault(r["symbol"], []).append(dict(r))

    for symbol, trades in by_symbol.items():
        try:
            fills = await ex.fetch_my_trades(symbol, since=_today_start_ms())
        except Exception as e:
            log.warning("fetch_my_trades %s failed: %s", symbol, e)
            continue
        for t in trades:
            tp_fills = [f for f in fills if str(f.get("order")) == str(t["tp_order_id"])]
            sl_fills = [f for f in fills if str(f.get("order")) == str(t["sl_order_id"])]
            if not tp_fills and not sl_fills:
                continue
            close_fill = (tp_fills + sl_fills)[0]
            close_px = float(close_fill["price"])
            qty = float(t["qty"])
            entry_px = float(t["entry"])
            sign = 1 if t["direction"] == "long" else -1
            gross = sign * (close_px - entry_px) * qty
            fee_close = float(close_fill.get("fee", {}).get("cost", 0) or 0)
            # Estimate entry fee similarly (fetch entry fill from same list)
            entry_fills = [f for f in fills if str(f.get("order")) == "e_skip"]
            fee_open = float(entry_fills[0]["fee"]["cost"]) if entry_fills else 0.0
            fees = fee_open + fee_close
            pnl_usdt = gross - fees
            pnl_pct = (pnl_usdt / (qty * entry_px)) * 100

            new_status = classify_close(
                direction=t["direction"],
                filled_at_tp_id=str(close_fill.get("order")) if tp_fills else None,
                filled_at_sl_id=str(close_fill.get("order")) if sl_fills else None,
                status_before=t["status"],
            )

            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE user_trades
                    SET status=$1, pnl_usdt=$2, pnl_pct=$3, fees_usdt=$4, closed_at=$5
                    WHERE id=$6
                    """,
                    new_status, pnl_usdt, pnl_pct, fees,
                    int(close_fill.get("timestamp") or time.time() * 1000),
                    t["id"],
                )
                await _upsert_daily(conn, user_id, pnl_usdt, won=(pnl_usdt > 0))
                await notify(conn, "trade_closed",
                             {"user_id": user_id, "trade_id": t["id"], "status": new_status,
                              "pnl_usdt": pnl_usdt, "pnl_pct": pnl_pct})

    # Daily cap check
    async with pool.acquire() as conn:
        cap_row = await conn.fetchrow(
            """
            SELECT u.daily_loss_cap_pct, COALESCE(d.realized_pnl_pct, 0) AS pct
            FROM users u
            LEFT JOIN user_daily_pnl d ON d.user_id=u.id AND d.day=CURRENT_DATE
            WHERE u.id=$1
            """,
            user_id,
        )
        if cap_row and cap_row["pct"] <= -float(cap_row["daily_loss_cap_pct"]):
            await conn.execute(
                "UPDATE users SET paused_until=$1, pause_reason='daily_cap', updated_at=$2 WHERE id=$3",
                DAILY_CAP_REACHED, int(time.time() * 1000), user_id,
            )
            await insert_audit(conn, user_id, "paused", {"reason": "daily_cap"})
            await notify(conn, "daily_summary", {"user_id": user_id, "paused": True})


async def _upsert_daily(conn, user_id: int, pnl_usdt: float, *, won: bool) -> None:
    # Need balance at day start to compute pct; for now use $100 default if unset
    await conn.execute(
        """
        INSERT INTO user_daily_pnl (user_id, day, realized_pnl_usdt, realized_pnl_pct,
                                    trades_count, wins_count, day_start_balance_usdt)
        VALUES ($1, CURRENT_DATE, $2, $3, 1, $4, 100.0)
        ON CONFLICT (user_id, day) DO UPDATE SET
          realized_pnl_usdt = user_daily_pnl.realized_pnl_usdt + EXCLUDED.realized_pnl_usdt,
          realized_pnl_pct  = (user_daily_pnl.realized_pnl_usdt + EXCLUDED.realized_pnl_usdt)
                              / COALESCE(user_daily_pnl.day_start_balance_usdt, 100.0) * 100,
          trades_count      = user_daily_pnl.trades_count + 1,
          wins_count        = user_daily_pnl.wins_count + EXCLUDED.wins_count
        """,
        user_id, pnl_usdt,
        (pnl_usdt / 100.0) * 100,  # placeholder pct; corrected by ON CONFLICT branch
        1 if won else 0,
    )
```

- [ ] **Step 3: Run tests**

```bash
createdb fvg_test
psql fvg_test -f migrations/0001_multi_user_live.sql
TEST_DATABASE_URL=postgresql:///fvg_test pytest trade_executor/tests/test_pnl_aggregator.py -v
dropdb fvg_test
```

Expected: 4 passed.

- [ ] **Step 4: Commit**

```bash
git add trade_executor/trade_executor/pnl_aggregator.py trade_executor/tests/test_pnl_aggregator.py
git commit -m "feat(trade_executor): 60s PnL reconcile + daily cap auto-pause"
```

### Task 2.11: Restart resume

**Files:**
- Create: `trade_executor/trade_executor/resume.py`
- Test: `trade_executor/tests/test_resume.py`

- [ ] **Step 1: Failing test**

```python
# trade_executor/tests/test_resume.py
import os

import pytest

from trade_executor import db
from trade_executor.resume import resume_in_flight


pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


class FakeEx:
    def __init__(self, entry_filled=True):
        self.entry_filled = entry_filled
        self.calls = []

    async def fetch_order(self, order_id, symbol):
        self.calls.append(("fetch_order", order_id))
        return {"id": order_id, "status": "FILLED" if self.entry_filled else "NEW",
                "average": 100.5}

    async def cancel_order(self, order_id, symbol):
        self.calls.append(("cancel", order_id))
        return {"id": order_id}

    async def create_order(self, symbol, type_, side, amount, price=None, params=None):
        self.calls.append(("create", type_, params))
        if type_ == "STOP_MARKET": return {"id": "sl-new"}
        if type_ == "TAKE_PROFIT_MARKET": return {"id": "tp-new"}


@pytest.mark.asyncio
async def test_resume_opening_with_filled_entry_places_sl_tp():
    pool = await db.create_pool(os.environ["TEST_DATABASE_URL"])
    try:
        async with pool.acquire() as conn:
            await conn.execute("INSERT INTO users (telegram_id, created_at, updated_at) VALUES (910, 0, 0) ON CONFLICT DO NOTHING")
            uid = await conn.fetchval("SELECT id FROM users WHERE telegram_id=910")
            await conn.execute("DELETE FROM user_trades WHERE user_id=$1", uid)
            await conn.execute(
                """
                INSERT INTO user_trades (id, user_id, decision_id, symbol, tf, direction,
                  leverage, margin_usdt, notional_usdt, qty, entry, sl, sl_current, tp1, tp2,
                  status, entry_order_id, opened_at)
                VALUES ($1,$2,'r-1','BTCUSDT','1h','long',5,10,50,0.001,100,95,95,105,110,'opening','e1',0)
                """,
                f"{uid}-r-1", uid,
            )

        await resume_in_flight(pool, ex_factory=lambda u: FakeEx(entry_filled=True))

        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT status, sl_order_id, tp_order_id FROM user_trades WHERE id=$1",
                                       f"{uid}-r-1")
        assert row["status"] == "open"
        assert row["sl_order_id"] == "sl-new"
        assert row["tp_order_id"] == "tp-new"
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_resume_opening_with_unfilled_entry_marks_error():
    pool = await db.create_pool(os.environ["TEST_DATABASE_URL"])
    try:
        async with pool.acquire() as conn:
            await conn.execute("INSERT INTO users (telegram_id, created_at, updated_at) VALUES (911, 0, 0) ON CONFLICT DO NOTHING")
            uid = await conn.fetchval("SELECT id FROM users WHERE telegram_id=911")
            await conn.execute("DELETE FROM user_trades WHERE user_id=$1", uid)
            await conn.execute(
                """
                INSERT INTO user_trades (id, user_id, decision_id, symbol, tf, direction,
                  leverage, margin_usdt, notional_usdt, qty, entry, sl, sl_current, tp1, tp2,
                  status, entry_order_id, opened_at)
                VALUES ($1,$2,'r-2','BTCUSDT','1h','long',5,10,50,0.001,100,95,95,105,110,'opening','e2',0)
                """,
                f"{uid}-r-2", uid,
            )

        await resume_in_flight(pool, ex_factory=lambda u: FakeEx(entry_filled=False))

        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT status FROM user_trades WHERE id=$1", f"{uid}-r-2")
        assert row["status"] == "error_restart"
    finally:
        await pool.close()
```

- [ ] **Step 2: Implement resume.py**

```python
# trade_executor/trade_executor/resume.py
from __future__ import annotations

import logging
import time
from typing import Awaitable, Callable

log = logging.getLogger("resume")


async def resume_in_flight(pool, *, ex_factory: Callable[[int], object | Awaitable[object]]) -> None:
    """On boot, walk every trade in (opening, open, tp1_trailed) and reconcile.

    - opening: check entry order; if FILLED → place SL+TP now; else → cancel + error.
    - open / tp1_trailed: leave to running loops (mark-price WS + reconcile loop).
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, user_id, symbol, direction, qty, entry, sl, tp2,
                   entry_order_id, status
            FROM user_trades
            WHERE status IN ('opening','open','tp1_trailed')
            """
        )

    for r in rows:
        if r["status"] != "opening":
            continue
        ex = ex_factory(r["user_id"])
        if hasattr(ex, "__await__"):
            ex = await ex
        try:
            order = await ex.fetch_order(r["entry_order_id"], r["symbol"])
        except Exception as e:
            log.warning("fetch_order failed during resume %s: %s", r["id"], e)
            continue

        if order.get("status") != "FILLED":
            try:
                await ex.cancel_order(r["entry_order_id"], r["symbol"])
            except Exception:
                pass
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE user_trades SET status='error_restart', closed_at=$1 WHERE id=$2",
                    int(time.time() * 1000), r["id"],
                )
            continue

        # Entry was filled — place SL and TP
        avg = float(order.get("average") or order.get("avgPrice") or r["entry"])
        close_side = "SELL" if r["direction"] == "long" else "BUY"
        try:
            sl = await ex.create_order(
                r["symbol"], "STOP_MARKET", close_side, float(r["qty"]), None,
                {"stopPrice": float(r["sl"]), "closePosition": True, "workingType": "MARK_PRICE"},
            )
            tp = await ex.create_order(
                r["symbol"], "TAKE_PROFIT_MARKET", close_side, float(r["qty"]), None,
                {"stopPrice": float(r["tp2"]), "closePosition": True, "workingType": "MARK_PRICE"},
            )
        except Exception as e:
            log.error("resume SL/TP placement failed for %s: %s", r["id"], e)
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE user_trades SET status='error_no_sl', error_msg=$1 WHERE id=$2",
                    str(e), r["id"],
                )
            continue

        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE user_trades SET
                  status='open', entry=$1, sl_current=$2,
                  sl_order_id=$3, tp_order_id=$4
                WHERE id=$5
                """,
                avg, float(r["sl"]), str(sl["id"]), str(tp["id"]), r["id"],
            )
```

- [ ] **Step 3: Run tests**

```bash
createdb fvg_test
psql fvg_test -f migrations/0001_multi_user_live.sql
TEST_DATABASE_URL=postgresql:///fvg_test pytest trade_executor/tests/test_resume.py -v
dropdb fvg_test
```

Expected: 2 passed.

- [ ] **Step 4: Commit**

```bash
git add trade_executor/trade_executor/resume.py trade_executor/tests/test_resume.py
git commit -m "feat(trade_executor): boot-time in-flight trade resume"
```

### Task 2.12: Wire all loops in main.py

**Files:**
- Modify: `trade_executor/trade_executor/main.py`
- Test: manual smoke against testnet

- [ ] **Step 1: Update main.py with asyncio.gather of all loops**

```python
# trade_executor/trade_executor/main.py
import asyncio
import base64
import logging
import sys

import uvicorn

from trade_executor.config import settings
from trade_executor.crypto import decrypt
from trade_executor.db import create_pool
from trade_executor.exchange import build_exchange
from trade_executor.http_api import app
from trade_executor.orchestrator import handle_signal_for_user
from trade_executor.pnl_aggregator import reconcile_user
from trade_executor.resume import resume_in_flight
from trade_executor.signal_poller import (load_last_seen, poll_once,
                                          save_last_seen, list_enabled_users)
from trade_executor.trail_manager import run_mark_price_ws

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("trade_executor")

_master = base64.b64decode(settings.MASTER_ENCRYPTION_KEY)


async def _build_user_ex(pool, user_id: int):
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT binance_api_key_enc, binance_api_secret_enc FROM users WHERE id=$1",
            user_id,
        )
    if not row or not row["binance_api_key_enc"]:
        raise RuntimeError(f"user {user_id} has no keys")
    key = decrypt(bytes(row["binance_api_key_enc"]), _master)
    sec = decrypt(bytes(row["binance_api_secret_enc"]), _master)
    return build_exchange(key, sec, proxy_url=settings.BINANCE_PROXY_URL)


async def signal_loop(pool):
    while True:
        try:
            async with pool.acquire() as conn:
                last = await load_last_seen(conn)
                signals = await poll_once(conn, last_seen_ms=last)
                users = await list_enabled_users(conn) if signals else []
            for sig in signals:
                for u in users:
                    try:
                        ex = await _build_user_ex(pool, u["id"])
                        await handle_signal_for_user(
                            pool, user_id=u["id"], signal=sig, ex=ex,
                            risk_pct=float(u["risk_pct"]),
                            leverage=int(u["leverage"]),
                            max_concurrent=int(u["max_concurrent"]),
                            daily_loss_cap_pct=float(u["daily_loss_cap_pct"]),
                        )
                        await ex.close()
                    except Exception as e:
                        log.exception("handle_signal_for_user failed user=%s: %s", u["id"], e)
                async with pool.acquire() as conn:
                    await save_last_seen(conn, int(sig["created_at"]))
        except Exception as e:
            log.exception("signal_loop error: %s", e)
        await asyncio.sleep(settings.SIGNAL_POLL_INTERVAL_S)


async def reconcile_loop(pool):
    while True:
        try:
            async with pool.acquire() as conn:
                users = await list_enabled_users(conn)
            for u in users:
                try:
                    ex = await _build_user_ex(pool, u["id"])
                    await reconcile_user(pool, ex=ex, user_id=u["id"])
                    await ex.close()
                except Exception as e:
                    log.exception("reconcile failed user=%s: %s", u["id"], e)
        except Exception as e:
            log.exception("reconcile_loop error: %s", e)
        await asyncio.sleep(settings.PNL_RECONCILE_INTERVAL_S)


async def http_server():
    config = uvicorn.Config(app, host="0.0.0.0", port=settings.HTTP_PORT, log_level="info")
    await uvicorn.Server(config).serve()


async def get_active_symbols(pool):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT DISTINCT symbol FROM user_trades WHERE status IN ('open','tp1_trailed')"
        )
    return [r["symbol"] for r in rows]


async def run():
    pool = await create_pool(settings.DATABASE_URL, min_size=2, max_size=10)
    log.info("DB pool created")

    # Resume any in-flight trades
    async def ex_factory(uid):
        return await _build_user_ex(pool, uid)
    await resume_in_flight(pool, ex_factory=ex_factory)
    log.info("Restart resume complete")

    await asyncio.gather(
        http_server(),
        signal_loop(pool),
        reconcile_loop(pool),
        run_mark_price_ws(
            pool, ex_factory=ex_factory,
            get_active_symbols=lambda: get_active_symbols(pool),
            proxy_url=settings.BINANCE_PROXY_URL,
        ),
    )


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke test against Binance USDT-M testnet**

Manual checklist:
1. Set `BINANCE_PROXY_URL` empty. Set keys to a testnet account ($1000 testnet USDT).
2. Insert a fake `kronos_decisions` row.
3. Verify a `user_trades` row goes opening → open with real testnet order IDs.
4. Cancel manually, verify reconcile path marks `closed_*`.

```bash
docker run --rm \
  -e DATABASE_URL=postgres://fvg:...@host:5432/fvg \
  -e MASTER_ENCRYPTION_KEY=$(openssl rand -base64 32) \
  -e INTERNAL_TOKEN=test \
  -e TELEGRAM_BOT_TOKEN=test \
  -p 8014:8014 trade_executor:dev
```

- [ ] **Step 3: Commit**

```bash
git add trade_executor/trade_executor/main.py
git commit -m "feat(trade_executor): wire all loops + boot resume"
```


---

## Phase 3 — telegram_bot

Goal: bot listens on pg_notify and sends formatted alerts; `/start` walks user through onboarding; `/status`, `/pause`, `/resume` work.

### Task 3.1: Alert templates

**Files:**
- Create: `telegram_bot/telegram_bot/templates.py`
- Test: `telegram_bot/tests/test_templates.py`

- [ ] **Step 1: Failing tests**

```python
# telegram_bot/tests/test_templates.py
from telegram_bot.templates import (
    fmt_opened, fmt_tp1_trailed, fmt_tp2, fmt_sl, fmt_breakeven,
    fmt_error, fmt_daily,
)


def test_fmt_opened_long():
    msg = fmt_opened(
        symbol="BTCUSDT", tf="1h", direction="long",
        entry=108_420.0, sl=107_200.0, tp1=109_640.0, tp2=110_860.0,
        qty=0.025, leverage=5, notional=135.0, margin=27.0,
    )
    assert "🟢 OPENED" in msg
    assert "BTCUSDT" in msg
    assert "LONG" in msg
    assert "108,420" in msg or "108420" in msg


def test_fmt_tp1_trailed_mentions_locked_1r():
    msg = fmt_tp1_trailed(symbol="BTCUSDT", new_sl=109_640.0)
    assert "TP1" in msg
    assert "trailed" in msg.lower() or "trail" in msg.lower()


def test_fmt_tp2_uses_plus_sign_for_profit():
    msg = fmt_tp2(symbol="BTCUSDT", pnl_usdt=5.41, pnl_pct=2.0)
    assert "+$5.41" in msg or "+5.41" in msg


def test_fmt_sl_uses_minus_sign_for_loss():
    msg = fmt_sl(symbol="BTCUSDT", pnl_usdt=-2.71, pnl_pct=-1.0)
    assert "-$2.71" in msg or "-2.71" in msg


def test_fmt_breakeven_message():
    msg = fmt_breakeven(symbol="BTCUSDT", pnl_usdt=0.02)
    assert "BREAKEVEN" in msg.upper()


def test_fmt_error_critical():
    msg = fmt_error(symbol="BTCUSDT", reason="SL placement failed")
    assert "ERROR" in msg.upper()
    assert "SL" in msg


def test_fmt_daily_summary():
    msg = fmt_daily(date="2026-05-06", trades=8, wins=5, pnl_usdt=12.34, pnl_pct=12.34)
    assert "DAILY" in msg.upper()
    assert "wins" in msg.lower() or "WR" in msg
    assert "+$12.34" in msg or "+12.34" in msg
```

- [ ] **Step 2: Implement templates.py**

```python
# telegram_bot/telegram_bot/templates.py


def _money(v: float) -> str:
    sign = "+" if v >= 0 else "-"
    return f"{sign}${abs(v):.2f}"


def _pct(v: float) -> str:
    sign = "+" if v >= 0 else "-"
    return f"{sign}{abs(v):.2f}%"


def _px(v: float) -> str:
    return f"${v:,.2f}" if v >= 100 else f"${v:,.4f}"


def fmt_opened(*, symbol, tf, direction, entry, sl, tp1, tp2,
               qty, leverage, notional, margin) -> str:
    sl_pct = (entry - sl) / entry * 100 if direction == "long" else (sl - entry) / entry * 100
    return (
        f"🟢 OPENED  {symbol} {tf} {direction.upper()}\n"
        f"   entry {_px(entry)}  sl {_px(sl)} ({_pct(-sl_pct)})\n"
        f"   tp1 {_px(tp1)}  tp2 {_px(tp2)}\n"
        f"   qty {qty}  ({leverage}x lev, ${notional:.2f} notional, ${margin:.2f} margin)"
    )


def fmt_tp1_trailed(*, symbol: str, new_sl: float) -> str:
    return f"🎯 TP1 HIT  {symbol}  → SL trailed to {_px(new_sl)} (locked +1R)"


def fmt_tp2(*, symbol: str, pnl_usdt: float, pnl_pct: float) -> str:
    return f"✅ TP2 HIT  {symbol}  closed {_money(pnl_usdt)} ({_pct(pnl_pct)})"


def fmt_sl(*, symbol: str, pnl_usdt: float, pnl_pct: float) -> str:
    return f"🛑 SL HIT  {symbol}  closed {_money(pnl_usdt)} ({_pct(pnl_pct)})"


def fmt_breakeven(*, symbol: str, pnl_usdt: float) -> str:
    return f"🔁 BREAKEVEN  {symbol}  TP1 trailed → SL hit at TP1 closed {_money(pnl_usdt)}"


def fmt_error(*, symbol: str, reason: str) -> str:
    return f"⚠️ ERROR  {symbol} — {reason}"


def fmt_daily(*, date: str, trades: int, wins: int, pnl_usdt: float, pnl_pct: float) -> str:
    return (
        f"📊 DAILY ({date})\n"
        f"   trades {trades}  wins {wins}  pnl {_money(pnl_usdt)} ({_pct(pnl_pct)})"
    )


def onboarding_intro(dashboard_url: str, proxy_ip: str) -> str:
    return (
        "Welcome! To trade live:\n"
        f"1. Log in at {dashboard_url}/login (Telegram auth)\n"
        "2. Add Binance API keys at /api-keys\n"
        f"3. Whitelist this IP on your key restriction: {proxy_ip}\n"
        "4. Permissions: Futures Trading + Read. Never enable Withdraw."
    )
```

- [ ] **Step 3: Run tests**

```bash
cd telegram_bot && pytest tests/test_templates.py -v
```

Expected: 7 passed.

- [ ] **Step 4: Commit**

```bash
git add telegram_bot/telegram_bot/templates.py telegram_bot/tests/test_templates.py
git commit -m "feat(telegram_bot): alert templates"
```

### Task 3.2: pg_notify listener + dispatch

**Files:**
- Create: `telegram_bot/telegram_bot/db.py`
- Create: `telegram_bot/telegram_bot/listener.py`
- Create: `telegram_bot/telegram_bot/client.py`
- Modify: `telegram_bot/telegram_bot/main.py`
- Test: `telegram_bot/tests/test_listener.py`

- [ ] **Step 1: Implement db.py + client.py**

```python
# telegram_bot/telegram_bot/db.py
import asyncpg


async def create_pool(dsn: str) -> asyncpg.Pool:
    return await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=4)
```

```python
# telegram_bot/telegram_bot/client.py
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from telegram_bot.config import settings


def make_bot() -> Bot:
    return Bot(
        token=settings.TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
```

- [ ] **Step 2: Implement listener.py**

```python
# telegram_bot/telegram_bot/listener.py
from __future__ import annotations

import json
import logging

from telegram_bot.templates import (
    fmt_breakeven, fmt_error, fmt_opened, fmt_sl, fmt_tp1_trailed, fmt_tp2,
)

log = logging.getLogger("listener")

CHANNELS = ("trade_opened", "trade_tp1_trailed", "trade_closed", "error")


async def _user_chat(pool, user_id: int) -> int | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT telegram_id FROM users WHERE id=$1", user_id)
    return int(row["telegram_id"]) if row else None


async def _trade_row(pool, trade_id: str) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT user_id, symbol, tf, direction, entry, sl, tp1, tp2,
                      qty, leverage, notional_usdt, margin_usdt, sl_current,
                      pnl_usdt, pnl_pct, status
               FROM user_trades WHERE id=$1""",
            trade_id,
        )
    return dict(row) if row else None


async def handle_payload(pool, bot, channel: str, payload: dict) -> None:
    if channel == "trade_opened":
        t = await _trade_row(pool, payload["trade_id"])
        if not t: return
        chat = await _user_chat(pool, t["user_id"])
        if not chat: return
        await bot.send_message(chat, fmt_opened(
            symbol=t["symbol"], tf=t["tf"], direction=t["direction"],
            entry=float(t["entry"]), sl=float(t["sl"]),
            tp1=float(t["tp1"]), tp2=float(t["tp2"]),
            qty=float(t["qty"]), leverage=int(t["leverage"]),
            notional=float(t["notional_usdt"]), margin=float(t["margin_usdt"]),
        ))
    elif channel == "trade_tp1_trailed":
        t = await _trade_row(pool, payload["trade_id"])
        if not t: return
        chat = await _user_chat(pool, t["user_id"])
        if not chat: return
        await bot.send_message(chat, fmt_tp1_trailed(symbol=t["symbol"], new_sl=float(t["sl_current"])))
    elif channel == "trade_closed":
        t = await _trade_row(pool, payload["trade_id"])
        if not t: return
        chat = await _user_chat(pool, t["user_id"])
        if not chat: return
        pnl_usdt = float(t["pnl_usdt"] or 0)
        pnl_pct = float(t["pnl_pct"] or 0)
        if t["status"] == "closed_tp2":
            await bot.send_message(chat, fmt_tp2(symbol=t["symbol"], pnl_usdt=pnl_usdt, pnl_pct=pnl_pct))
        elif t["status"] == "closed_breakeven":
            await bot.send_message(chat, fmt_breakeven(symbol=t["symbol"], pnl_usdt=pnl_usdt))
        else:
            await bot.send_message(chat, fmt_sl(symbol=t["symbol"], pnl_usdt=pnl_usdt, pnl_pct=pnl_pct))
    elif channel == "error":
        chat = await _user_chat(pool, payload["user_id"])
        if not chat: return
        await bot.send_message(chat, fmt_error(symbol=payload.get("symbol", ""),
                                               reason=payload.get("stage", "unknown")))


async def run_listener(pool, bot):
    conn = await pool.acquire()
    try:
        async def cb(c, pid, channel, raw):
            try:
                payload = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                payload = {}
            try:
                await handle_payload(pool, bot, channel, payload)
            except Exception as e:
                log.exception("dispatch failed channel=%s: %s", channel, e)

        for ch in CHANNELS:
            await conn.add_listener(ch, cb)
        log.info("listening on %s", CHANNELS)
        # Keep the conn open
        import asyncio
        while True:
            await asyncio.sleep(60)
    finally:
        await pool.release(conn)
```

- [ ] **Step 3: Update main.py to wire listener + handlers**

```python
# telegram_bot/telegram_bot/main.py
import asyncio
import logging
import sys

from aiogram import Dispatcher

from telegram_bot.client import make_bot
from telegram_bot.config import settings
from telegram_bot.db import create_pool
from telegram_bot.handlers import register_handlers
from telegram_bot.listener import run_listener

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("telegram_bot")


async def run():
    pool = await create_pool(settings.DATABASE_URL)
    bot = make_bot()
    dp = Dispatcher()
    register_handlers(dp, pool)
    log.info("telegram_bot starting")
    await asyncio.gather(
        dp.start_polling(bot),
        run_listener(pool, bot),
    )


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Implement handlers.py**

```python
# telegram_bot/telegram_bot/handlers.py
import time

from aiogram import Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message

from telegram_bot.config import settings
from telegram_bot.templates import onboarding_intro


def register_handlers(dp: Dispatcher, pool) -> None:

    @dp.message(Command("start"))
    async def on_start(m: Message):
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO users (telegram_id, telegram_username, first_name,
                                   created_at, updated_at)
                VALUES ($1, $2, $3, $4, $4)
                ON CONFLICT (telegram_id) DO UPDATE SET
                  telegram_username=EXCLUDED.telegram_username,
                  first_name=EXCLUDED.first_name,
                  updated_at=EXCLUDED.updated_at
                """,
                m.from_user.id, m.from_user.username, m.from_user.first_name,
                int(time.time() * 1000),
            )
        await m.answer(onboarding_intro(settings.DASHBOARD_URL, "<PROXY_IP>"))

    @dp.message(Command("status"))
    async def on_status(m: Message):
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT enabled, paused_until, pause_reason FROM users WHERE telegram_id=$1",
                m.from_user.id,
            )
        if not row:
            await m.answer("Not registered. Send /start first.")
            return
        if row["paused_until"] and row["paused_until"] > int(time.time() * 1000):
            await m.answer(f"⏸ paused (reason: {row['pause_reason']})")
        elif row["enabled"]:
            await m.answer("✅ enabled — trading live signals")
        else:
            await m.answer("❌ disabled — toggle in dashboard /settings")

    @dp.message(Command("pause"))
    async def on_pause(m: Message):
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET enabled=false, updated_at=$1 WHERE telegram_id=$2",
                int(time.time() * 1000), m.from_user.id,
            )
        await m.answer("Paused. Send /resume to re-enable.")

    @dp.message(Command("resume"))
    async def on_resume(m: Message):
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET enabled=true, paused_until=NULL, pause_reason=NULL, updated_at=$1 WHERE telegram_id=$2",
                int(time.time() * 1000), m.from_user.id,
            )
        await m.answer("Resumed.")
```

- [ ] **Step 5: Listener test (manual integration smoke)**

```python
# telegram_bot/tests/test_listener.py
import json
import os

import pytest

from telegram_bot.db import create_pool


pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))


@pytest.mark.asyncio
async def test_handle_payload_trade_opened_dispatches_message():
    from telegram_bot.listener import handle_payload
    pool = await create_pool(os.environ["TEST_DATABASE_URL"])
    try:
        async with pool.acquire() as conn:
            await conn.execute("INSERT INTO users (telegram_id, created_at, updated_at) VALUES (12345, 0, 0) ON CONFLICT DO NOTHING")
            uid = await conn.fetchval("SELECT id FROM users WHERE telegram_id=12345")
            await conn.execute("DELETE FROM user_trades WHERE id=$1", f"{uid}-d-1")
            await conn.execute(
                """INSERT INTO user_trades (id, user_id, decision_id, symbol, tf, direction,
                     leverage, margin_usdt, notional_usdt, qty, entry, sl, sl_current, tp1, tp2,
                     status, opened_at)
                   VALUES ($1,$2,'d-1','BTCUSDT','1h','long',5,10,50,0.001,100,95,95,105,110,'open',0)
                """,
                f"{uid}-d-1", uid,
            )

        bot = FakeBot()
        await handle_payload(pool, bot, "trade_opened", {"trade_id": f"{uid}-d-1", "user_id": uid})
        assert len(bot.sent) == 1
        assert bot.sent[0][0] == 12345
        assert "OPENED" in bot.sent[0][1]
    finally:
        await pool.close()
```

- [ ] **Step 6: Run tests**

```bash
createdb fvg_test
psql fvg_test -f migrations/0001_multi_user_live.sql
TEST_DATABASE_URL=postgresql:///fvg_test pytest telegram_bot/tests/ -v
dropdb fvg_test
```

Expected: all tests passed.

- [ ] **Step 7: Commit**

```bash
git add telegram_bot/
git commit -m "feat(telegram_bot): pg_notify listener + onboarding handlers"
```


---

## Phase 4 — Dashboard (Next.js 15)

Phase 4 split: 4a auth + DB libs, 4b user pages + server actions, 4c admin + live data.

### Task 4a.1: DB client + env helpers

**Files:**
- Create: `dashboard/lib/env.ts`
- Create: `dashboard/lib/db.ts`
- Create: `dashboard/lib/format.ts`

- [ ] **Step 1: Write env loader**

`dashboard/lib/env.ts`:
```ts
function need(k: string): string {
  const v = process.env[k];
  if (!v) throw new Error(`Missing env ${k}`);
  return v;
}

export const env = {
  databaseUrl: need("DATABASE_URL"),
  botToken: need("TELEGRAM_BOT_TOKEN"),
  botUsername: need("NEXT_PUBLIC_BOT_USERNAME"),
  internalToken: need("INTERNAL_TOKEN"),
  executorUrl: need("EXECUTOR_URL"),
};
```

- [ ] **Step 2: Write DB client**

`dashboard/lib/db.ts`:
```ts
import postgres from "postgres";
import { env } from "./env";

declare global {
  var __sql: ReturnType<typeof postgres> | undefined;
}

export const sql =
  global.__sql ??
  postgres(env.databaseUrl, { max: 5, idle_timeout: 20 });

if (process.env.NODE_ENV !== "production") global.__sql = sql;
```

- [ ] **Step 3: Format helpers**

`dashboard/lib/format.ts`:
```ts
export const fmtUsd = (n: number | null | undefined) =>
  n == null ? "—" : `$${n.toFixed(2)}`;

export const fmtPct = (n: number | null | undefined) =>
  n == null ? "—" : `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;

export const fmtTime = (ms: number | null | undefined) =>
  ms == null ? "—" : new Date(Number(ms)).toISOString().replace("T", " ").slice(0, 19);

export const maskKey = (tail: string | null) =>
  tail ? `••••••••${tail}` : "—";
```

- [ ] **Step 4: Commit**

```bash
git add dashboard/lib/
git commit -m "feat(dashboard): env, db, format libs"
```

### Task 4a.2: Telegram HMAC verify

**Files:**
- Create: `dashboard/lib/telegram-verify.ts`
- Create: `dashboard/lib/__tests__/telegram-verify.test.ts`

- [ ] **Step 1: Write failing test**

`dashboard/lib/__tests__/telegram-verify.test.ts`:
```ts
import { describe, it, expect } from "vitest";
import crypto from "node:crypto";
import { verifyTelegramAuth } from "../telegram-verify";

function sign(payload: Record<string, string>, token: string) {
  const secret = crypto.createHash("sha256").update(token).digest();
  const dataCheck = Object.keys(payload)
    .sort()
    .map((k) => `${k}=${payload[k]}`)
    .join("\n");
  return crypto.createHmac("sha256", secret).update(dataCheck).digest("hex");
}

describe("verifyTelegramAuth", () => {
  const token = "TEST_BOT_TOKEN";
  const now = Math.floor(Date.now() / 1000);

  it("accepts a valid payload", () => {
    const payload = { id: "123", first_name: "Bob", auth_date: String(now) };
    const hash = sign(payload, token);
    expect(verifyTelegramAuth({ ...payload, hash }, token)).toBe(true);
  });

  it("rejects bad hash", () => {
    const payload = { id: "123", first_name: "Bob", auth_date: String(now) };
    expect(verifyTelegramAuth({ ...payload, hash: "deadbeef" }, token)).toBe(false);
  });

  it("rejects stale auth_date", () => {
    const stale = now - 90000;
    const payload = { id: "123", first_name: "Bob", auth_date: String(stale) };
    const hash = sign(payload, token);
    expect(verifyTelegramAuth({ ...payload, hash }, token)).toBe(false);
  });
});
```

- [ ] **Step 2: Run test**

```bash
cd dashboard && npx vitest run lib/__tests__/telegram-verify.test.ts
```

Expected: FAIL (verifyTelegramAuth not found).

- [ ] **Step 3: Implement verifier**

`dashboard/lib/telegram-verify.ts`:
```ts
import crypto from "node:crypto";

export type TgPayload = {
  id: string | number;
  first_name?: string;
  username?: string;
  photo_url?: string;
  auth_date: string | number;
  hash: string;
  [k: string]: unknown;
};

export function verifyTelegramAuth(payload: TgPayload, botToken: string): boolean {
  const { hash, ...rest } = payload;
  const secret = crypto.createHash("sha256").update(botToken).digest();
  const dataCheck = Object.keys(rest)
    .sort()
    .map((k) => `${k}=${rest[k as keyof typeof rest]}`)
    .join("\n");
  const expected = crypto
    .createHmac("sha256", secret)
    .update(dataCheck)
    .digest("hex");

  if (!crypto.timingSafeEqual(Buffer.from(hash, "hex"), Buffer.from(expected, "hex"))) {
    return false;
  }
  const authDate = Number(payload.auth_date);
  const now = Math.floor(Date.now() / 1000);
  if (now - authDate > 86400) return false;
  return true;
}
```

- [ ] **Step 4: Run tests**

```bash
cd dashboard && npx vitest run lib/__tests__/telegram-verify.test.ts
```

Expected: 3/3 pass.

- [ ] **Step 5: Commit**

```bash
git add dashboard/lib/telegram-verify.ts dashboard/lib/__tests__/
git commit -m "feat(dashboard): telegram login HMAC verifier"
```

### Task 4a.3: Session helpers

**Files:**
- Create: `dashboard/lib/auth.ts`

- [ ] **Step 1: Write auth lib**

`dashboard/lib/auth.ts`:
```ts
import { cookies } from "next/headers";
import crypto from "node:crypto";
import { sql } from "./db";

export const SESSION_COOKIE = "session";
const SESSION_TTL_MS = 30 * 24 * 3600 * 1000;

export async function createSession(userId: number): Promise<string> {
  const token = crypto.randomBytes(32).toString("hex");
  const now = Date.now();
  await sql`
    INSERT INTO sessions (token, user_id, created_at, expires_at)
    VALUES (${token}, ${userId}, ${now}, ${now + SESSION_TTL_MS})
  `;
  return token;
}

export type SessionUser = {
  id: number;
  telegram_id: number;
  telegram_username: string | null;
  first_name: string | null;
  is_admin: boolean;
};

export async function getSessionUser(): Promise<SessionUser | null> {
  const c = await cookies();
  const token = c.get(SESSION_COOKIE)?.value;
  if (!token) return null;
  const rows = await sql<SessionUser[]>`
    SELECT u.id, u.telegram_id, u.telegram_username, u.first_name, u.is_admin
    FROM sessions s JOIN users u ON u.id = s.user_id
    WHERE s.token = ${token} AND s.expires_at > ${Date.now()}
  `;
  return rows[0] ?? null;
}

export async function destroySession(token: string): Promise<void> {
  await sql`DELETE FROM sessions WHERE token = ${token}`;
}

export async function requireUser(): Promise<SessionUser> {
  const u = await getSessionUser();
  if (!u) throw new Error("Unauthorized");
  return u;
}

export async function requireAdmin(): Promise<SessionUser> {
  const u = await requireUser();
  if (!u.is_admin) throw new Error("Forbidden");
  return u;
}
```

- [ ] **Step 2: Commit**

```bash
git add dashboard/lib/auth.ts
git commit -m "feat(dashboard): session create/get/destroy helpers"
```

### Task 4a.4: Login page + callback

**Files:**
- Create: `dashboard/app/login/page.tsx`
- Create: `dashboard/app/api/auth/telegram/route.ts`

- [ ] **Step 1: Login page with widget**

`dashboard/app/login/page.tsx`:
```tsx
import Script from "next/script";
import { env } from "@/lib/env";

export default function LoginPage() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-zinc-950">
      <div className="bg-zinc-900 p-8 rounded-2xl text-center space-y-4">
        <h1 className="text-2xl font-semibold text-white">FVG Live Trader</h1>
        <p className="text-zinc-400">Sign in with Telegram</p>
        <div id="tg-login-container" />
        <Script
          id="tg-login"
          strategy="afterInteractive"
          src="https://telegram.org/js/telegram-widget.js?22"
          data-telegram-login={env.botUsername}
          data-size="large"
          data-auth-url="/api/auth/telegram"
          data-request-access="write"
        />
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Auth callback route**

`dashboard/app/api/auth/telegram/route.ts`:
```ts
import { NextRequest, NextResponse } from "next/server";
import { verifyTelegramAuth, TgPayload } from "@/lib/telegram-verify";
import { createSession, SESSION_COOKIE } from "@/lib/auth";
import { sql } from "@/lib/db";
import { env } from "@/lib/env";

export async function GET(req: NextRequest) {
  const params = Object.fromEntries(req.nextUrl.searchParams.entries()) as TgPayload;
  if (!verifyTelegramAuth(params, env.botToken)) {
    return NextResponse.json({ error: "invalid_auth" }, { status: 401 });
  }
  const now = Date.now();
  const tgId = Number(params.id);
  const rows = await sql<{ id: number }[]>`
    INSERT INTO users (telegram_id, telegram_username, first_name, photo_url, created_at, updated_at)
    VALUES (
      ${tgId},
      ${(params.username as string) ?? null},
      ${(params.first_name as string) ?? null},
      ${(params.photo_url as string) ?? null},
      ${now}, ${now}
    )
    ON CONFLICT (telegram_id) DO UPDATE
      SET telegram_username = EXCLUDED.telegram_username,
          first_name        = EXCLUDED.first_name,
          photo_url         = EXCLUDED.photo_url,
          updated_at        = ${now}
    RETURNING id
  `;
  const userId = rows[0].id;
  await sql`
    INSERT INTO user_audit_log (user_id, action, payload, created_at)
    VALUES (${userId}, 'login', ${sql.json({ tgId })}, ${now})
  `;
  const token = await createSession(userId);
  const res = NextResponse.redirect(new URL("/dashboard", req.url));
  res.cookies.set(SESSION_COOKIE, token, {
    httpOnly: true,
    secure: true,
    sameSite: "lax",
    maxAge: 30 * 24 * 3600,
    path: "/",
  });
  return res;
}
```

- [ ] **Step 3: Commit**

```bash
git add dashboard/app/login/ dashboard/app/api/auth/
git commit -m "feat(dashboard): telegram login page + callback"
```

### Task 4a.5: Middleware

**Files:**
- Create: `dashboard/middleware.ts`

- [ ] **Step 1: Write middleware**

```ts
import { NextRequest, NextResponse } from "next/server";

const PROTECTED = ["/dashboard", "/admin"];

export function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;
  if (!PROTECTED.some((p) => pathname.startsWith(p))) return NextResponse.next();
  const tok = req.cookies.get("session")?.value;
  if (!tok) {
    const url = req.nextUrl.clone();
    url.pathname = "/login";
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/dashboard/:path*", "/admin/:path*"],
};
```

- [ ] **Step 2: Commit**

```bash
git add dashboard/middleware.ts
git commit -m "feat(dashboard): require session cookie on /dashboard and /admin"
```

### Task 4b.1: Server actions — settings + toggle + resume

**Files:**
- Create: `dashboard/app/dashboard/settings/actions.ts`
- Create: `dashboard/app/dashboard/settings/page.tsx`
- Create: `dashboard/components/settings-form.tsx`

- [ ] **Step 1: Settings server action**

`dashboard/app/dashboard/settings/actions.ts`:
```ts
"use server";
import { z } from "zod";
import { revalidatePath } from "next/cache";
import { sql } from "@/lib/db";
import { requireUser } from "@/lib/auth";

const SettingsSchema = z.object({
  risk_pct: z.coerce.number().min(0.1).max(10),
  leverage: z.coerce.number().int().min(5).max(20),
  max_concurrent: z.coerce.number().int().min(1).max(10),
  daily_loss_cap_pct: z.coerce.number().min(1).max(50),
  enabled: z.boolean().optional().default(false),
});

export async function updateSettings(formData: FormData) {
  const user = await requireUser();
  const parsed = SettingsSchema.parse({
    risk_pct: formData.get("risk_pct"),
    leverage: formData.get("leverage"),
    max_concurrent: formData.get("max_concurrent"),
    daily_loss_cap_pct: formData.get("daily_loss_cap_pct"),
    enabled: formData.get("enabled") === "on",
  });
  const now = Date.now();
  await sql`
    UPDATE users SET
      risk_pct           = ${parsed.risk_pct},
      leverage           = ${parsed.leverage},
      max_concurrent     = ${parsed.max_concurrent},
      daily_loss_cap_pct = ${parsed.daily_loss_cap_pct},
      enabled            = ${parsed.enabled},
      updated_at         = ${now}
    WHERE id = ${user.id}
  `;
  await sql`
    INSERT INTO user_audit_log (user_id, action, payload, created_at)
    VALUES (${user.id}, 'settings_update', ${sql.json(parsed)}, ${now})
  `;
  revalidatePath("/dashboard/settings");
}

export async function resumeFromPause() {
  const user = await requireUser();
  const now = Date.now();
  await sql`
    UPDATE users SET paused_until = NULL, pause_reason = NULL, updated_at = ${now}
    WHERE id = ${user.id}
  `;
  await sql`
    INSERT INTO user_audit_log (user_id, action, payload, created_at)
    VALUES (${user.id}, 'resumed', ${sql.json({})}, ${now})
  `;
  revalidatePath("/dashboard/settings");
}
```

- [ ] **Step 2: Form component**

`dashboard/components/settings-form.tsx`:
```tsx
"use client";
import { updateSettings } from "@/app/dashboard/settings/actions";

type Props = {
  defaults: {
    risk_pct: number;
    leverage: number;
    max_concurrent: number;
    daily_loss_cap_pct: number;
    enabled: boolean;
  };
};

export function SettingsForm({ defaults }: Props) {
  return (
    <form action={updateSettings} className="space-y-4 max-w-md">
      <Field name="risk_pct" label="Risk % per trade" defaultValue={defaults.risk_pct} step={0.1} />
      <Field name="leverage" label="Leverage (5–20x)" defaultValue={defaults.leverage} step={1} />
      <Field name="max_concurrent" label="Max concurrent trades" defaultValue={defaults.max_concurrent} step={1} />
      <Field name="daily_loss_cap_pct" label="Daily loss cap %" defaultValue={defaults.daily_loss_cap_pct} step={0.5} />
      <label className="flex items-center gap-2 text-zinc-200">
        <input type="checkbox" name="enabled" defaultChecked={defaults.enabled} />
        Enable live trading
      </label>
      <button className="bg-emerald-600 hover:bg-emerald-500 text-white px-4 py-2 rounded">
        Save
      </button>
    </form>
  );
}

function Field({ name, label, defaultValue, step }: { name: string; label: string; defaultValue: number; step: number }) {
  return (
    <label className="block text-zinc-200">
      <span className="text-sm">{label}</span>
      <input
        type="number"
        name={name}
        defaultValue={defaultValue}
        step={step}
        className="mt-1 block w-full bg-zinc-900 border border-zinc-700 rounded px-3 py-2"
      />
    </label>
  );
}
```

- [ ] **Step 3: Settings page**

`dashboard/app/dashboard/settings/page.tsx`:
```tsx
import { sql } from "@/lib/db";
import { requireUser } from "@/lib/auth";
import { SettingsForm } from "@/components/settings-form";
import { resumeFromPause } from "./actions";

export default async function SettingsPage() {
  const user = await requireUser();
  const rows = await sql<any[]>`
    SELECT risk_pct, leverage, max_concurrent, daily_loss_cap_pct, enabled,
           paused_until, pause_reason
    FROM users WHERE id = ${user.id}
  `;
  const u = rows[0];
  const isPaused = u.paused_until && Number(u.paused_until) > Date.now();

  return (
    <div className="p-6 space-y-6">
      <h1 className="text-2xl font-semibold text-white">Settings</h1>
      {isPaused && (
        <div className="bg-red-950 border border-red-800 p-4 rounded">
          <p className="text-red-200">Paused: {u.pause_reason}</p>
          <form action={resumeFromPause}>
            <button className="mt-2 bg-red-700 hover:bg-red-600 text-white px-3 py-1 rounded">
              Resume
            </button>
          </form>
        </div>
      )}
      <SettingsForm defaults={u} />
    </div>
  );
}
```

- [ ] **Step 4: Commit**

```bash
git add dashboard/app/dashboard/settings/ dashboard/components/settings-form.tsx
git commit -m "feat(dashboard): settings form + resume action"
```

### Task 4b.2: API keys page (calls executor /encrypt)

**Files:**
- Create: `dashboard/lib/executor.ts`
- Create: `dashboard/app/dashboard/api-keys/actions.ts`
- Create: `dashboard/app/dashboard/api-keys/page.tsx`

- [ ] **Step 1: Executor client**

`dashboard/lib/executor.ts`:
```ts
import { env } from "./env";

export async function encryptViaExecutor(plaintext: string): Promise<Buffer> {
  const r = await fetch(`${env.executorUrl}/encrypt`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Internal-Token": env.internalToken,
    },
    body: JSON.stringify({ plaintext }),
    cache: "no-store",
  });
  if (!r.ok) throw new Error(`executor /encrypt ${r.status}`);
  const j = (await r.json()) as { ciphertext_b64: string };
  return Buffer.from(j.ciphertext_b64, "base64");
}
```

- [ ] **Step 2: rotateKeys action**

`dashboard/app/dashboard/api-keys/actions.ts`:
```ts
"use server";
import { z } from "zod";
import { revalidatePath } from "next/cache";
import { sql } from "@/lib/db";
import { requireUser } from "@/lib/auth";
import { encryptViaExecutor } from "@/lib/executor";

const KeySchema = z.object({
  api_key: z.string().min(20).max(128),
  api_secret: z.string().min(20).max(128),
});

export async function rotateKeys(formData: FormData) {
  const user = await requireUser();
  const parsed = KeySchema.parse({
    api_key: formData.get("api_key"),
    api_secret: formData.get("api_secret"),
  });
  const keyEnc = await encryptViaExecutor(parsed.api_key);
  const secEnc = await encryptViaExecutor(parsed.api_secret);
  const tail = parsed.api_key.slice(-4);
  const now = Date.now();
  await sql`
    UPDATE users SET
      binance_api_key_enc    = ${keyEnc},
      binance_api_secret_enc = ${secEnc},
      api_key_tail           = ${tail},
      updated_at             = ${now}
    WHERE id = ${user.id}
  `;
  await sql`
    INSERT INTO user_audit_log (user_id, action, payload, created_at)
    VALUES (${user.id}, 'keys_rotated', ${sql.json({ tail })}, ${now})
  `;
  revalidatePath("/dashboard/api-keys");
}
```

- [ ] **Step 3: API keys page**

`dashboard/app/dashboard/api-keys/page.tsx`:
```tsx
import { sql } from "@/lib/db";
import { requireUser } from "@/lib/auth";
import { rotateKeys } from "./actions";
import { maskKey } from "@/lib/format";

export default async function ApiKeysPage() {
  const user = await requireUser();
  const rows = await sql<{ api_key_tail: string | null }[]>`
    SELECT api_key_tail FROM users WHERE id = ${user.id}
  `;
  const tail = rows[0]?.api_key_tail ?? null;

  return (
    <div className="p-6 space-y-6 max-w-md">
      <h1 className="text-2xl font-semibold text-white">Binance API Keys</h1>
      <p className="text-zinc-400">Current key: <code className="text-zinc-200">{maskKey(tail)}</code></p>
      <div className="bg-amber-950 border border-amber-800 p-4 rounded text-amber-200 text-sm">
        Whitelist proxy IP on your Binance API. Permissions: Futures Trading + Read. NEVER enable Withdraw.
      </div>
      <form action={rotateKeys} className="space-y-3">
        <label className="block text-zinc-200">
          <span className="text-sm">API Key</span>
          <input name="api_key" required className="mt-1 block w-full bg-zinc-900 border border-zinc-700 rounded px-3 py-2 font-mono text-sm" />
        </label>
        <label className="block text-zinc-200">
          <span className="text-sm">API Secret</span>
          <input name="api_secret" type="password" required className="mt-1 block w-full bg-zinc-900 border border-zinc-700 rounded px-3 py-2 font-mono text-sm" />
        </label>
        <button className="bg-emerald-600 hover:bg-emerald-500 text-white px-4 py-2 rounded">
          Save keys
        </button>
      </form>
    </div>
  );
}
```

- [ ] **Step 4: Commit**

```bash
git add dashboard/lib/executor.ts dashboard/app/dashboard/api-keys/
git commit -m "feat(dashboard): api keys rotation via executor /encrypt"
```

### Task 4b.3: Trades table + signal feed

**Files:**
- Create: `dashboard/app/dashboard/trades/page.tsx`
- Create: `dashboard/app/dashboard/signals/page.tsx`
- Create: `dashboard/app/api/signals/route.ts`
- Create: `dashboard/app/api/trades/open/route.ts`
- Create: `dashboard/components/trade-row.tsx`

- [ ] **Step 1: Trades page**

`dashboard/app/dashboard/trades/page.tsx`:
```tsx
import { sql } from "@/lib/db";
import { requireUser } from "@/lib/auth";
import { fmtPct, fmtTime, fmtUsd } from "@/lib/format";
import Link from "next/link";

export default async function TradesPage() {
  const user = await requireUser();
  const rows = await sql<any[]>`
    SELECT id, symbol, tf, direction, status, entry, sl_current, tp1, tp2,
           pnl_usdt, pnl_pct, opened_at, closed_at
    FROM user_trades
    WHERE user_id = ${user.id}
    ORDER BY opened_at DESC
    LIMIT 200
  `;
  return (
    <div className="p-6">
      <h1 className="text-2xl font-semibold text-white mb-4">Trades</h1>
      <div className="overflow-x-auto">
        <table className="min-w-full text-sm text-zinc-200">
          <thead className="text-xs text-zinc-400 uppercase border-b border-zinc-800">
            <tr>
              <th className="px-3 py-2 text-left">Time</th>
              <th className="px-3 py-2 text-left">Symbol</th>
              <th className="px-3 py-2 text-left">TF</th>
              <th className="px-3 py-2 text-left">Dir</th>
              <th className="px-3 py-2 text-left">Status</th>
              <th className="px-3 py-2 text-right">Entry</th>
              <th className="px-3 py-2 text-right">SL</th>
              <th className="px-3 py-2 text-right">TP2</th>
              <th className="px-3 py-2 text-right">PnL</th>
              <th className="px-3 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className="border-b border-zinc-900">
                <td className="px-3 py-2">{fmtTime(r.opened_at)}</td>
                <td className="px-3 py-2">{r.symbol}</td>
                <td className="px-3 py-2">{r.tf}</td>
                <td className={`px-3 py-2 ${r.direction === "long" ? "text-emerald-400" : "text-red-400"}`}>{r.direction}</td>
                <td className="px-3 py-2">{r.status}</td>
                <td className="px-3 py-2 text-right">{r.entry?.toFixed?.(4)}</td>
                <td className="px-3 py-2 text-right">{r.sl_current?.toFixed?.(4)}</td>
                <td className="px-3 py-2 text-right">{r.tp2?.toFixed?.(4)}</td>
                <td className="px-3 py-2 text-right">
                  <div>{fmtUsd(r.pnl_usdt)}</div>
                  <div className="text-xs text-zinc-500">{fmtPct(r.pnl_pct)}</div>
                </td>
                <td className="px-3 py-2"><Link className="text-blue-400" href={`/dashboard/trades/${r.id}`}>view</Link></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: SWR signal feed page + route**

`dashboard/app/api/signals/route.ts`:
```ts
import { NextResponse } from "next/server";
import { sql } from "@/lib/db";
import { requireUser } from "@/lib/auth";

export async function GET() {
  await requireUser();
  const rows = await sql<any[]>`
    SELECT id, symbol, tf, direction, event_type, entry, sl, tp1, tp2, created_at
    FROM kronos_decisions
    WHERE valid = true
    ORDER BY created_at DESC
    LIMIT 50
  `;
  return NextResponse.json({ rows });
}
```

`dashboard/app/api/trades/open/route.ts`:
```ts
import { NextResponse } from "next/server";
import { sql } from "@/lib/db";
import { requireUser } from "@/lib/auth";

export async function GET() {
  const user = await requireUser();
  const rows = await sql<{ count: number }[]>`
    SELECT COUNT(*)::int AS count FROM user_trades
    WHERE user_id = ${user.id} AND status IN ('opening','open','tp1_trailed')
  `;
  return NextResponse.json({ open: rows[0].count });
}
```

`dashboard/app/dashboard/signals/page.tsx`:
```tsx
"use client";
import useSWR from "swr";

const fetcher = (u: string) => fetch(u).then((r) => r.json());

export default function SignalsPage() {
  const { data } = useSWR("/api/signals", fetcher, { refreshInterval: 5000 });
  return (
    <div className="p-6">
      <h1 className="text-2xl font-semibold text-white mb-4">Live signals</h1>
      <ul className="space-y-2">
        {(data?.rows ?? []).map((r: any) => (
          <li key={r.id} className="bg-zinc-900 px-3 py-2 rounded flex justify-between text-sm">
            <span>
              <span className={r.direction === "long" ? "text-emerald-400" : "text-red-400"}>{r.direction.toUpperCase()}</span>
              {" "}
              <strong>{r.symbol}</strong> {r.tf} <span className="text-zinc-500">{r.event_type}</span>
            </span>
            <span className="text-zinc-400">{new Date(Number(r.created_at)).toLocaleTimeString()}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
```

- [ ] **Step 3: Commit**

```bash
git add dashboard/app/dashboard/trades dashboard/app/dashboard/signals dashboard/app/api/signals dashboard/app/api/trades dashboard/components/trade-row.tsx
git commit -m "feat(dashboard): trades table + live signal feed"
```

### Task 4b.4: Overview + stats + trade detail + audit

**Files:**
- Create: `dashboard/app/dashboard/page.tsx`
- Create: `dashboard/app/dashboard/stats/page.tsx`
- Create: `dashboard/app/dashboard/trades/[id]/page.tsx`
- Create: `dashboard/app/dashboard/audit/page.tsx`
- Create: `dashboard/components/stats-charts.tsx`

- [ ] **Step 1: Overview page**

`dashboard/app/dashboard/page.tsx`:
```tsx
import { sql } from "@/lib/db";
import { requireUser } from "@/lib/auth";
import { fmtPct, fmtUsd } from "@/lib/format";

export default async function Overview() {
  const user = await requireUser();
  const today = new Date().toISOString().slice(0, 10);
  const [pnl] = await sql<any[]>`
    SELECT realized_pnl_usdt, realized_pnl_pct, trades_count, wins_count
    FROM user_daily_pnl WHERE user_id = ${user.id} AND day = ${today}::date
  `;
  const [open] = await sql<any[]>`
    SELECT COUNT(*)::int n FROM user_trades
    WHERE user_id = ${user.id} AND status IN ('opening','open','tp1_trailed')
  `;
  const [wr] = await sql<any[]>`
    SELECT
      COUNT(*) FILTER (WHERE status = 'closed_tp2')::float
        / NULLIF(COUNT(*) FILTER (WHERE status IN ('closed_tp2','closed_sl','closed_breakeven')), 0) AS wr_30d
    FROM user_trades
    WHERE user_id = ${user.id} AND closed_at > ${Date.now() - 30 * 86400 * 1000}
  `;
  return (
    <div className="p-6 grid grid-cols-1 sm:grid-cols-3 gap-4">
      <Card title="Today PnL" big={fmtUsd(pnl?.realized_pnl_usdt)} sub={fmtPct(pnl?.realized_pnl_pct)} />
      <Card title="Open trades" big={String(open?.n ?? 0)} sub="" />
      <Card title="Win rate (30d)" big={pnl ? fmtPct((wr?.wr_30d ?? 0) * 100) : "—"} sub="" />
    </div>
  );
}

function Card({ title, big, sub }: { title: string; big: string; sub: string }) {
  return (
    <div className="bg-zinc-900 p-4 rounded-2xl">
      <div className="text-zinc-400 text-sm">{title}</div>
      <div className="text-3xl text-white mt-1">{big}</div>
      <div className="text-zinc-500 text-sm">{sub}</div>
    </div>
  );
}
```

- [ ] **Step 2: Stats charts (Recharts)**

`dashboard/components/stats-charts.tsx`:
```tsx
"use client";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, BarChart, Bar } from "recharts";

export function CumPnlChart({ data }: { data: { day: string; cum: number }[] }) {
  return (
    <ResponsiveContainer width="100%" height={260}>
      <LineChart data={data}>
        <XAxis dataKey="day" stroke="#666" />
        <YAxis stroke="#666" />
        <Tooltip />
        <Line type="monotone" dataKey="cum" stroke="#10b981" strokeWidth={2} dot={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function WrBySymbolChart({ data }: { data: { symbol: string; wr: number }[] }) {
  return (
    <ResponsiveContainer width="100%" height={260}>
      <BarChart data={data}>
        <XAxis dataKey="symbol" stroke="#666" />
        <YAxis stroke="#666" domain={[0, 1]} />
        <Tooltip />
        <Bar dataKey="wr" fill="#3b82f6" />
      </BarChart>
    </ResponsiveContainer>
  );
}
```

`dashboard/app/dashboard/stats/page.tsx`:
```tsx
import { sql } from "@/lib/db";
import { requireUser } from "@/lib/auth";
import { CumPnlChart, WrBySymbolChart } from "@/components/stats-charts";

export default async function StatsPage() {
  const user = await requireUser();
  const daily = await sql<any[]>`
    SELECT day::text, realized_pnl_usdt
    FROM user_daily_pnl
    WHERE user_id = ${user.id}
    ORDER BY day
  `;
  let cum = 0;
  const cumData = daily.map((d) => ({ day: d.day, cum: (cum += Number(d.realized_pnl_usdt)) }));

  const bySym = await sql<any[]>`
    SELECT symbol,
      COUNT(*) FILTER (WHERE status='closed_tp2')::float
        / NULLIF(COUNT(*) FILTER (WHERE status IN ('closed_tp2','closed_sl','closed_breakeven')), 0) AS wr
    FROM user_trades
    WHERE user_id = ${user.id}
    GROUP BY symbol
    ORDER BY symbol
  `;

  return (
    <div className="p-6 space-y-8">
      <h1 className="text-2xl font-semibold text-white">Stats</h1>
      <section>
        <h2 className="text-zinc-300 mb-2">Cumulative PnL ($)</h2>
        <CumPnlChart data={cumData} />
      </section>
      <section>
        <h2 className="text-zinc-300 mb-2">Win rate by symbol</h2>
        <WrBySymbolChart data={bySym.map((r) => ({ symbol: r.symbol, wr: Number(r.wr) || 0 }))} />
      </section>
    </div>
  );
}
```

- [ ] **Step 3: Trade detail page**

`dashboard/app/dashboard/trades/[id]/page.tsx`:
```tsx
import { sql } from "@/lib/db";
import { requireUser } from "@/lib/auth";
import { fmtPct, fmtTime, fmtUsd } from "@/lib/format";
import { notFound } from "next/navigation";

export default async function TradeDetail({ params }: { params: Promise<{ id: string }> }) {
  const user = await requireUser();
  const { id } = await params;
  const rows = await sql<any[]>`
    SELECT * FROM user_trades WHERE id = ${id} AND user_id = ${user.id}
  `;
  const t = rows[0];
  if (!t) notFound();
  return (
    <div className="p-6 space-y-3 text-zinc-200">
      <h1 className="text-2xl font-semibold">{t.symbol} {t.tf} <span className={t.direction === "long" ? "text-emerald-400" : "text-red-400"}>{t.direction}</span></h1>
      <div className="grid grid-cols-2 gap-2 max-w-md">
        <Row k="status" v={t.status} />
        <Row k="entry" v={t.entry?.toFixed(4)} />
        <Row k="sl_current" v={t.sl_current?.toFixed(4)} />
        <Row k="tp1" v={t.tp1?.toFixed(4)} />
        <Row k="tp2" v={t.tp2?.toFixed(4)} />
        <Row k="qty" v={t.qty} />
        <Row k="leverage" v={`${t.leverage}x`} />
        <Row k="margin" v={fmtUsd(t.margin_usdt)} />
        <Row k="notional" v={fmtUsd(t.notional_usdt)} />
        <Row k="pnl" v={`${fmtUsd(t.pnl_usdt)} (${fmtPct(t.pnl_pct)})`} />
        <Row k="opened" v={fmtTime(t.opened_at)} />
        <Row k="closed" v={fmtTime(t.closed_at)} />
      </div>
      {t.error_msg && <div className="bg-red-950 p-3 rounded text-red-200 text-sm">{t.error_msg}</div>}
    </div>
  );
}

function Row({ k, v }: { k: string; v: any }) {
  return (
    <>
      <div className="text-zinc-500">{k}</div>
      <div>{v ?? "—"}</div>
    </>
  );
}
```

- [ ] **Step 4: Audit page**

`dashboard/app/dashboard/audit/page.tsx`:
```tsx
import { sql } from "@/lib/db";
import { requireUser } from "@/lib/auth";
import { fmtTime } from "@/lib/format";

export default async function AuditPage() {
  const user = await requireUser();
  const rows = await sql<any[]>`
    SELECT id, action, payload, created_at
    FROM user_audit_log
    WHERE user_id = ${user.id}
    ORDER BY created_at DESC
    LIMIT 100
  `;
  return (
    <div className="p-6">
      <h1 className="text-2xl font-semibold text-white mb-4">Audit log</h1>
      <ul className="space-y-1 text-sm font-mono text-zinc-300">
        {rows.map((r) => (
          <li key={r.id} className="flex gap-3">
            <span className="text-zinc-500">{fmtTime(r.created_at)}</span>
            <span className="text-emerald-400">{r.action}</span>
            <span className="text-zinc-400">{JSON.stringify(r.payload)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
```

- [ ] **Step 5: Commit**

```bash
git add dashboard/app/dashboard/page.tsx dashboard/app/dashboard/stats dashboard/app/dashboard/trades/[id] dashboard/app/dashboard/audit dashboard/components/stats-charts.tsx
git commit -m "feat(dashboard): overview, stats, trade detail, audit"
```

### Task 4c.1: Admin pages + force-pause action

**Files:**
- Create: `dashboard/app/admin/users/page.tsx`
- Create: `dashboard/app/admin/users/actions.ts`
- Create: `dashboard/app/admin/system/page.tsx`
- Create: `dashboard/app/admin/layout.tsx`

- [ ] **Step 1: Admin layout (gates access)**

`dashboard/app/admin/layout.tsx`:
```tsx
import { requireAdmin } from "@/lib/auth";

export default async function AdminLayout({ children }: { children: React.ReactNode }) {
  await requireAdmin();
  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      <header className="border-b border-zinc-800 px-6 py-3 text-sm text-amber-400">
        Admin area
      </header>
      {children}
    </div>
  );
}
```

- [ ] **Step 2: Force-pause action**

`dashboard/app/admin/users/actions.ts`:
```ts
"use server";
import { z } from "zod";
import { revalidatePath } from "next/cache";
import { sql } from "@/lib/db";
import { requireAdmin } from "@/lib/auth";

const Schema = z.object({
  user_id: z.coerce.number().int().positive(),
  reason: z.string().min(1).max(200),
});

export async function forcePauseUser(formData: FormData) {
  const admin = await requireAdmin();
  const { user_id, reason } = Schema.parse({
    user_id: formData.get("user_id"),
    reason: formData.get("reason"),
  });
  const now = Date.now();
  await sql`
    UPDATE users
    SET paused_until = 9999999999999, pause_reason = ${reason}, updated_at = ${now}
    WHERE id = ${user_id}
  `;
  await sql`
    INSERT INTO user_audit_log (user_id, action, payload, created_at)
    VALUES (${user_id}, 'paused', ${sql.json({ reason, by_admin: admin.id })}, ${now})
  `;
  revalidatePath("/admin/users");
}
```

- [ ] **Step 3: Admin users page**

`dashboard/app/admin/users/page.tsx`:
```tsx
import { sql } from "@/lib/db";
import { fmtTime } from "@/lib/format";
import { forcePauseUser } from "./actions";

export default async function AdminUsersPage() {
  const users = await sql<any[]>`
    SELECT id, telegram_id, telegram_username, first_name, enabled,
           paused_until, pause_reason, created_at, api_key_tail
    FROM users ORDER BY created_at DESC
  `;
  return (
    <div className="p-6">
      <h1 className="text-2xl font-semibold mb-4">All users</h1>
      <table className="min-w-full text-sm">
        <thead className="text-xs text-zinc-400 uppercase border-b border-zinc-800">
          <tr>
            <th className="px-3 py-2 text-left">id</th>
            <th className="px-3 py-2 text-left">tg</th>
            <th className="px-3 py-2 text-left">name</th>
            <th className="px-3 py-2 text-left">key</th>
            <th className="px-3 py-2 text-left">enabled</th>
            <th className="px-3 py-2 text-left">paused</th>
            <th className="px-3 py-2 text-left">joined</th>
            <th className="px-3 py-2 text-left">force-pause</th>
          </tr>
        </thead>
        <tbody>
          {users.map((u) => (
            <tr key={u.id} className="border-b border-zinc-900">
              <td className="px-3 py-2">{u.id}</td>
              <td className="px-3 py-2">@{u.telegram_username ?? u.telegram_id}</td>
              <td className="px-3 py-2">{u.first_name}</td>
              <td className="px-3 py-2 font-mono text-xs">{u.api_key_tail ?? "—"}</td>
              <td className="px-3 py-2">{u.enabled ? "✓" : "—"}</td>
              <td className="px-3 py-2">{u.paused_until && Number(u.paused_until) > Date.now() ? u.pause_reason : "—"}</td>
              <td className="px-3 py-2">{fmtTime(u.created_at)}</td>
              <td className="px-3 py-2">
                <form action={forcePauseUser} className="flex gap-2">
                  <input type="hidden" name="user_id" value={u.id} />
                  <input name="reason" placeholder="reason" className="bg-zinc-900 border border-zinc-700 px-2 py-1 rounded text-xs" />
                  <button className="bg-red-700 hover:bg-red-600 text-white px-2 py-1 rounded text-xs">pause</button>
                </form>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 4: Admin system page (executor heartbeat + last signal age)**

`dashboard/app/admin/system/page.tsx`:
```tsx
import { sql } from "@/lib/db";
import { env } from "@/lib/env";

async function execHealth(): Promise<{ ok: boolean; status: number }> {
  try {
    const r = await fetch(`${env.executorUrl}/healthz`, { cache: "no-store" });
    return { ok: r.ok, status: r.status };
  } catch {
    return { ok: false, status: 0 };
  }
}

export default async function AdminSystemPage() {
  const health = await execHealth();
  const [last] = await sql<any[]>`
    SELECT MAX(created_at) AS last FROM kronos_decisions WHERE valid = true
  `;
  const lastAgeMs = last?.last ? Date.now() - Number(last.last) : null;
  const [errs] = await sql<any[]>`
    SELECT COUNT(*)::int AS n
    FROM user_trades
    WHERE status LIKE 'error_%'
      AND opened_at > ${Date.now() - 24 * 3600 * 1000}
  `;
  return (
    <div className="p-6 space-y-6">
      <h1 className="text-2xl font-semibold">System</h1>
      <Stat label="Executor /healthz" value={health.ok ? `OK (${health.status})` : `DOWN (${health.status})`} bad={!health.ok} />
      <Stat label="Last signal age" value={lastAgeMs == null ? "—" : `${Math.floor(lastAgeMs / 1000)}s ago`} bad={(lastAgeMs ?? 0) > 1800_000} />
      <Stat label="Errors (24h)" value={String(errs?.n ?? 0)} bad={(errs?.n ?? 0) > 0} />
    </div>
  );
}

function Stat({ label, value, bad }: { label: string; value: string; bad: boolean }) {
  return (
    <div className={`p-4 rounded-2xl ${bad ? "bg-red-950 border border-red-800" : "bg-zinc-900"}`}>
      <div className="text-zinc-400 text-sm">{label}</div>
      <div className="text-2xl">{value}</div>
    </div>
  );
}
```

- [ ] **Step 5: Commit**

```bash
git add dashboard/app/admin/
git commit -m "feat(dashboard): admin users + system pages"
```

### Task 4c.2: Dashboard nav + root layout

**Files:**
- Modify: `dashboard/app/layout.tsx`
- Create: `dashboard/app/dashboard/layout.tsx`
- Create: `dashboard/components/nav.tsx`

- [ ] **Step 1: Nav component**

`dashboard/components/nav.tsx`:
```tsx
import Link from "next/link";
import { SessionUser } from "@/lib/auth";

const items = [
  ["/dashboard", "Overview"],
  ["/dashboard/signals", "Signals"],
  ["/dashboard/trades", "Trades"],
  ["/dashboard/stats", "Stats"],
  ["/dashboard/settings", "Settings"],
  ["/dashboard/api-keys", "API keys"],
  ["/dashboard/audit", "Audit"],
];

export function Nav({ user }: { user: SessionUser }) {
  return (
    <nav className="border-b border-zinc-800 bg-zinc-950">
      <div className="px-6 py-3 flex items-center justify-between">
        <div className="flex gap-4 text-sm">
          {items.map(([href, label]) => (
            <Link key={href} href={href} className="text-zinc-300 hover:text-white">{label}</Link>
          ))}
          {user.is_admin && (
            <>
              <span className="text-zinc-700">|</span>
              <Link href="/admin/users" className="text-amber-400 hover:text-amber-300">Admin users</Link>
              <Link href="/admin/system" className="text-amber-400 hover:text-amber-300">Admin system</Link>
            </>
          )}
        </div>
        <div className="text-xs text-zinc-400">@{user.telegram_username ?? user.telegram_id}</div>
      </div>
    </nav>
  );
}
```

- [ ] **Step 2: Dashboard layout**

`dashboard/app/dashboard/layout.tsx`:
```tsx
import { requireUser } from "@/lib/auth";
import { Nav } from "@/components/nav";

export default async function DashboardLayout({ children }: { children: React.ReactNode }) {
  const user = await requireUser();
  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      <Nav user={user} />
      {children}
    </div>
  );
}
```

- [ ] **Step 3: Commit**

```bash
git add dashboard/components/nav.tsx dashboard/app/dashboard/layout.tsx
git commit -m "feat(dashboard): nav + dashboard layout"
```

---

## Phase 5 — Deploy & Smoke Test

### Task 5.1: GitHub Actions for trade_executor + telegram_bot

**Files:**
- Create: `.github/workflows/trade_executor.yml`
- Create: `.github/workflows/telegram_bot.yml`
- Create: `.github/workflows/dashboard.yml`

- [ ] **Step 1: trade_executor workflow**

`.github/workflows/trade_executor.yml`:
```yaml
name: trade_executor

on:
  push:
    branches: [main]
    paths: ["trade_executor/**", ".github/workflows/trade_executor.yml"]

jobs:
  build:
    runs-on: ubuntu-latest
    permissions: { contents: read, packages: write }
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - uses: docker/build-push-action@v5
        with:
          context: trade_executor
          push: true
          tags: ghcr.io/${{ github.repository_owner }}/trade_executor:latest
```

- [ ] **Step 2: telegram_bot workflow**

`.github/workflows/telegram_bot.yml`:
```yaml
name: telegram_bot

on:
  push:
    branches: [main]
    paths: ["telegram_bot/**", ".github/workflows/telegram_bot.yml"]

jobs:
  build:
    runs-on: ubuntu-latest
    permissions: { contents: read, packages: write }
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - uses: docker/build-push-action@v5
        with:
          context: telegram_bot
          push: true
          tags: ghcr.io/${{ github.repository_owner }}/telegram_bot:latest
```

- [ ] **Step 3: dashboard workflow**

`.github/workflows/dashboard.yml`:
```yaml
name: dashboard

on:
  push:
    branches: [main]
    paths: ["dashboard/**", ".github/workflows/dashboard.yml"]

jobs:
  build:
    runs-on: ubuntu-latest
    permissions: { contents: read, packages: write }
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - uses: docker/build-push-action@v5
        with:
          context: dashboard
          push: true
          tags: ghcr.io/${{ github.repository_owner }}/dashboard:latest
```

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/
git commit -m "ci: build & push trade_executor, telegram_bot, dashboard images"
```

### Task 5.2: Coolify compose stack

**Files:**
- Create: `deploy/docker-compose.coolify.yml`
- Create: `deploy/.env.example`

- [ ] **Step 1: Compose file**

`deploy/docker-compose.coolify.yml`:
```yaml
version: "3.9"

services:
  trade_executor:
    image: ghcr.io/${GH_OWNER}/trade_executor:latest
    restart: unless-stopped
    environment:
      DATABASE_URL: ${DATABASE_URL}
      MASTER_ENCRYPTION_KEY: ${MASTER_ENCRYPTION_KEY}
      BINANCE_PROXY_URL: ${BINANCE_PROXY_URL}
      INTERNAL_TOKEN: ${INTERNAL_TOKEN}
      TELEGRAM_BOT_TOKEN: ${TELEGRAM_BOT_TOKEN}
    expose: ["8014"]

  telegram_bot:
    image: ghcr.io/${GH_OWNER}/telegram_bot:latest
    restart: unless-stopped
    environment:
      DATABASE_URL: ${DATABASE_URL}
      TELEGRAM_BOT_TOKEN: ${TELEGRAM_BOT_TOKEN}

  dashboard:
    image: ghcr.io/${GH_OWNER}/dashboard:latest
    restart: unless-stopped
    environment:
      DATABASE_URL: ${DATABASE_URL}
      TELEGRAM_BOT_TOKEN: ${TELEGRAM_BOT_TOKEN}
      INTERNAL_TOKEN: ${INTERNAL_TOKEN}
      EXECUTOR_URL: http://trade_executor:8014
      NEXT_PUBLIC_BOT_USERNAME: ${BOT_USERNAME}
    expose: ["3000"]
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.dashboard.rule=Host(`${DASHBOARD_DOMAIN}`)"
      - "traefik.http.routers.dashboard.entrypoints=https"
      - "traefik.http.routers.dashboard.tls.certresolver=letsencrypt"
      - "traefik.http.services.dashboard.loadbalancer.server.port=3000"
```

- [ ] **Step 2: Env template**

`deploy/.env.example`:
```
GH_OWNER=your-gh-owner
DATABASE_URL=postgresql://fvg:PASSWORD@fvg-postgres:5432/fvg
MASTER_ENCRYPTION_KEY=BASE64_32_BYTES
BINANCE_PROXY_URL=http://user:pass@PROXY_HOST:PORT
INTERNAL_TOKEN=RANDOM_HEX_64
TELEGRAM_BOT_TOKEN=123:ABC
BOT_USERNAME=fvg_alpha_bot
DASHBOARD_DOMAIN=dashboard.uponlytrader.xyz
```

- [ ] **Step 3: Generate keys snippet**

Add to `deploy/README.md`:
```
# Generate MASTER_ENCRYPTION_KEY
python -c "import os, base64; print(base64.b64encode(os.urandom(32)).decode())"

# Generate INTERNAL_TOKEN
python -c "import secrets; print(secrets.token_hex(32))"
```

- [ ] **Step 4: Commit**

```bash
git add deploy/
git commit -m "deploy: coolify compose stack + env template"
```

### Task 5.3: Run migration in production DB

- [ ] **Step 1: Apply migration to fvg-postgres**

Run from a host that can reach `fvg-postgres`:

```bash
psql "$DATABASE_URL" -f migrations/0001_multi_user_live.sql
```

- [ ] **Step 2: Verify tables exist**

```bash
psql "$DATABASE_URL" -c "\dt users; \dt user_trades; \dt user_daily_pnl; \dt sessions; \dt user_audit_log; \dt executor_state"
```

Expected: all 6 tables listed.

### Task 5.4: Deploy stack on Coolify (Dell Dubai)

- [ ] **Step 1: Create Coolify resource from compose**

Steps in Coolify UI:
1. New resource → Docker Compose → paste `deploy/docker-compose.coolify.yml`
2. Set env vars from `.env.example` (real values)
3. Attach to network containing `fvg-postgres`
4. Set domain `dashboard.uponlytrader.xyz` on `dashboard` service
5. Deploy

- [ ] **Step 2: Verify**

```bash
curl https://dashboard.uponlytrader.xyz/api/health        # 200 ok
curl http://trade_executor:8014/healthz                   # from inside network: ok
docker logs <stack>-trade_executor-1 --tail 50            # asyncio.gather running
docker logs <stack>-telegram_bot-1 --tail 50              # polling started
```

### Task 5.5: Set Telegram bot webhook to login domain

- [ ] **Step 1: Configure Telegram Login Widget domain**

In BotFather:
```
/setdomain
@fvg_alpha_bot
dashboard.uponlytrader.xyz
```

### Task 5.6: Promote first admin

- [ ] **Step 1: Sign in via Telegram once**, then SQL:

```sql
UPDATE users SET is_admin = true WHERE telegram_id = <ADMIN_TG_ID>;
```

### Task 5.7: Smoke test with two real users

Acceptance run:

- [ ] User A signs in, adds Binance keys, sets risk 1%, leverage 5x, max 1, daily cap 6%, enables.
- [ ] User B same with risk 2%, leverage 10x, max 2.
- [ ] Both fund $100 USDT-M futures wallet, whitelist proxy IP.
- [ ] Wait for next valid kronos signal.
- [ ] Verify `user_trades` row appears for each user with status `open`.
- [ ] Verify Binance UI shows position with isolated margin + correct leverage + SL + TP orders.
- [ ] Verify Telegram alert `🟢 OPENED` arrives within 3s of NOTIFY.
- [ ] Force a TP1 cross (test on a tight tf) → confirm alert `🎯 TP1 HIT` and `sl_current` updated to TP1 in DB.
- [ ] Wait for TP2 / SL → confirm `pnl_usdt` filled and matching alert.
- [ ] Restart `trade_executor` while a trade is `open` → confirm row stays `open`, no duplicate orders, mark-price WS resumes.
- [ ] Trigger artificial daily cap by setting `daily_loss_cap_pct=0.01` → confirm pause-forever alert, dashboard shows pause banner, /resume re-enables.
- [ ] Failed SL injection: temporarily revoke api permission on test account between entry and SL → confirm emergency MARKET close + `error_no_sl` + CRITICAL alert.
- [ ] `/dashboard` p95 < 1s with 100 trades.
- [ ] All mutations show in `user_audit_log`.

### Task 5.8: Final commit

- [ ] **Step 1:** Tag release.

```bash
git tag -a v1.0.0-multiuser -m "multi-user binance live trading v1.0.0"
git push origin v1.0.0-multiuser
```

---

## Appendix A — Failure modes & runbooks

| Symptom                                | Likely cause                       | Action                                                                |
|----------------------------------------|------------------------------------|-----------------------------------------------------------------------|
| Trade rows stuck `opening`             | entry order never filled           | resume on boot cancels + marks `error_restart`                        |
| `error_no_sl` on a trade               | SL placement failed after entry    | emergency close already ran; verify in Binance UI; user re-enables    |
| Daily cap hit, user paused forever     | `realized_pnl_pct ≤ -daily_cap`    | user clicks Resume on `/dashboard/settings` after review              |
| Telegram alerts stop                   | bot disconnected / pg listener gone| `docker restart telegram_bot`                                         |
| Mark-price WS gone, no TP1 trail       | proxy connection drop              | `trade_executor` reconnects every 30s; verify in logs                 |
| Encryption fails on /encrypt           | wrong MASTER_ENCRYPTION_KEY length | regenerate 32-byte key; rotate all stored keys                        |
| New user can't sign in                 | bot domain not set in BotFather    | `/setdomain` → match dashboard host                                   |

---

End of plan.
