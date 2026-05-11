-- migrations/0006_default_rr_2_for_existing_users.sql
--
-- One-time upgrade: existing users whose rr_ratio still sits at the legacy
-- default 1.0 get bumped to 2.0 so live trade RR matches the public channel
-- banner (which always says "RR 1:2"). Users who explicitly set anything
-- else (1.2, 1.5, 3.0, ...) are left alone.
--
-- Safe to re-run: the UPDATE simply matches zero rows the second time.
BEGIN;

UPDATE users
   SET rr_ratio = 2.0,
       updated_at = (EXTRACT(EPOCH FROM NOW()) * 1000)::BIGINT
 WHERE rr_ratio = 1.0;

COMMIT;
