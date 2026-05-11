-- migrations/0005_user_risk_mode_and_sl_controls.sql
BEGIN;

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS risk_mode TEXT NOT NULL DEFAULT 'percent';

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS sl_enabled BOOLEAN NOT NULL DEFAULT TRUE;

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS sl_mult DOUBLE PRECISION NOT NULL DEFAULT 1.0;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'users_risk_mode_chk'
    ) THEN
        ALTER TABLE users
            ADD CONSTRAINT users_risk_mode_chk
            CHECK (risk_mode IN ('percent', 'fixed'));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'users_sl_mult_range_chk'
    ) THEN
        ALTER TABLE users
            ADD CONSTRAINT users_sl_mult_range_chk
            CHECK (sl_mult >= 0.5 AND sl_mult <= 5.0);
    END IF;
END $$;

COMMIT;
