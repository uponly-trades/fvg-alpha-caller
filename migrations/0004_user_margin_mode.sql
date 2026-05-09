-- migrations/0004_user_margin_mode.sql
BEGIN;

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS margin_mode TEXT NOT NULL DEFAULT 'ISOLATED';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'users_margin_mode_chk'
    ) THEN
        ALTER TABLE users
            ADD CONSTRAINT users_margin_mode_chk
            CHECK (margin_mode IN ('ISOLATED', 'CROSSED'));
    END IF;
END $$;

COMMIT;
