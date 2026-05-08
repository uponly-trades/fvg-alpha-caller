-- migrations/0003_fixed_risk_sizing.sql
BEGIN;

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS fixed_risk_usdt DOUBLE PRECISION NOT NULL DEFAULT 5.0;

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS max_notional_usdt DOUBLE PRECISION NOT NULL DEFAULT 250.0;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'users_fixed_risk_usdt_positive_chk'
    ) THEN
        ALTER TABLE users
            ADD CONSTRAINT users_fixed_risk_usdt_positive_chk
            CHECK (fixed_risk_usdt > 0.0);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'users_max_notional_usdt_min_chk'
    ) THEN
        ALTER TABLE users
            ADD CONSTRAINT users_max_notional_usdt_min_chk
            CHECK (max_notional_usdt >= 5.0);
    END IF;
END $$;

COMMIT;
