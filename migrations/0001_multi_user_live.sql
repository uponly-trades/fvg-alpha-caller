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
