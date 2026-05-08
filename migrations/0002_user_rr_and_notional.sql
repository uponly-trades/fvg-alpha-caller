-- migrations/0002_user_rr_and_notional.sql
BEGIN;

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS rr_ratio DOUBLE PRECISION NOT NULL DEFAULT 1.0;

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS fixed_notional_usdt DOUBLE PRECISION;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'users_rr_ratio_min_chk'
    ) THEN
        ALTER TABLE users
            ADD CONSTRAINT users_rr_ratio_min_chk CHECK (rr_ratio >= 1.0);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'users_fixed_notional_positive_chk'
    ) THEN
        ALTER TABLE users
            ADD CONSTRAINT users_fixed_notional_positive_chk
            CHECK (fixed_notional_usdt IS NULL OR fixed_notional_usdt >= 5.0);
    END IF;
END $$;

COMMIT;
