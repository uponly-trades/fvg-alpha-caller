ALTER TABLE user_trades ADD COLUMN IF NOT EXISTS exit_mode TEXT NOT NULL DEFAULT 'legacy';

CREATE TABLE IF NOT EXISTS supertrend_state (
    symbol       TEXT NOT NULL,
    tf           TEXT NOT NULL,
    trend        SMALLINT NOT NULL,
    band         DOUBLE PRECISION NOT NULL,
    switch_price DOUBLE PRECISION NOT NULL,
    bar_time     BIGINT NOT NULL,
    updated_at   BIGINT NOT NULL,
    PRIMARY KEY (symbol, tf)
);
CREATE INDEX IF NOT EXISTS idx_supertrend_state_symbol_tf ON supertrend_state(symbol, tf);
