# Deployment

## Generate secrets

```
# Generate MASTER_ENCRYPTION_KEY
python -c "import os, base64; print(base64.b64encode(os.urandom(32)).decode())"

# Generate INTERNAL_TOKEN
python -c "import secrets; print(secrets.token_hex(32))"
```

## Coolify

1. New resource → Docker Compose → paste `docker-compose.coolify.yml`
2. Set env vars from `.env.example` (real values)
3. Attach to network containing `fvg-postgres`
4. Set domain (e.g. `dashboard.uponlytrader.xyz`) on `dashboard` service
5. Deploy

## Migration

Migrations are applied manually via `psql` in numeric order. Each file is safe
to re-run (idempotent: `ADD COLUMN IF NOT EXISTS`, conditional constraints,
filtered `UPDATE`). Run them in sequence on a fresh deploy, and run any new
ones on existing deploys.

Easiest path — run the wrapper script:

```bash
# Inside fvg-alpha-caller container (DATABASE_URL set by Coolify):
bash deploy/apply_migrations.sh
```

Or manually, file-by-file:

```bash
# From the repo root with $DATABASE_URL pointing at fvg-postgres:
for f in migrations/*.sql; do
  echo "Applying $f"
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$f" || exit 1
done
```

Current migrations:

- `0001_multi_user_live.sql` — base multi-user schema (users, user_trades, etc.)
- `0002_user_rr_and_notional.sql` — adds `rr_ratio`, notional config columns.
- `0003_fixed_risk_sizing.sql` — adds `fixed_risk_usdt`, `max_notional_usdt`.
- `0004_user_margin_mode.sql` — adds `margin_mode` (ISOLATED/CROSSED).
- `0005_user_risk_mode_and_sl_controls.sql` — adds `risk_mode`, `sl_enabled`,
  `sl_mult` so users can pick percent-of-equity vs fixed-$ risk and toggle SL
  on/off per their preference.
- `0006_default_rr_2_for_existing_users.sql` — one-time bump: any user still
  on the legacy `rr_ratio=1.0` is upgraded to `2.0` so live trades match the
  public channel banner. Users with custom RR are untouched.

If `0006` is skipped, live trades for legacy users keep returning `RR 1:1`
(seen as "+$0.72 on KSMUSDT" while channel says "RR 1:2"). Apply once.

## Telegram

In BotFather:
```
/setdomain
@fvg_alpha_bot
dashboard.uponlytrader.xyz
```

## Promote first admin

```sql
UPDATE users SET is_admin = true WHERE telegram_id = <ADMIN_TG_ID>;
```
