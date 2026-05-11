# Deployment

Live host: **Linode Tokyo `172.104.77.28`** (ssh alias `fvg-tokyo`).
Deployment is plain Docker Compose on Ubuntu — no Coolify, no Portainer, no
container orchestrator. The compose file lives at `/opt/fvg/docker-compose.yaml`
on the server; the canonical source-of-truth copy is `docker-compose.yaml` at
the repo root.

## Host layout

```
/opt/fvg/
├── docker-compose.yaml        # production compose (source: repo root)
├── .env                       # secrets (DB_PW, tokens, keys) — server-only
├── repo/                      # git clone of fvg-alpha-caller (branch fvg-v2)
├── data/                      # alpha-caller persistent buffers
└── pgdata/                    # postgres volume
```

The four services (`postgres`, `fvg-alpha-caller`, `trade_executor`,
`telegram_bot`) all build from `/opt/fvg/repo`. Postgres is internal to the
stack — there is no separate fvg-postgres service.

## Generate secrets (one-time)

```bash
# 32-byte MASTER_ENCRYPTION_KEY
python -c "import os, base64; print(base64.b64encode(os.urandom(32)).decode())"

# INTERNAL_TOKEN for executor ↔ telegram_bot HTTP auth
python -c "import secrets; print(secrets.token_hex(32))"
```

Store these in `/opt/fvg/.env` on the server.

## Deploy cycle (code change)

```bash
ssh fvg-tokyo
cd /opt/fvg/repo
git pull origin fvg-v2

# Rebuild + restart the changed services (compose auto-detects rebuild needs)
cd /opt/fvg
docker compose up -d --build

# Verify all four containers are healthy
docker compose ps
```

## Migrations

Migrations live in `migrations/*.sql` and are applied manually. Every file is
idempotent (`ADD COLUMN IF NOT EXISTS`, conditional constraints, filtered
`UPDATE`), safe to re-run after the first successful pass.

After `git pull` brings in new migrations:

```bash
ssh fvg-tokyo
# Run from the host (psql is reachable via the postgres container):
docker exec -i fvg-postgres-1 psql -U fvg -d fvg < /opt/fvg/repo/migrations/0005_user_risk_mode_and_sl_controls.sql
docker exec -i fvg-postgres-1 psql -U fvg -d fvg < /opt/fvg/repo/migrations/0006_default_rr_2_for_existing_users.sql
```

Or use the wrapper that loops all migrations in order. It expects
`DATABASE_URL` to point at postgres; on this host you can run it inside the
`trade_executor` container which already has DATABASE_URL set:

```bash
ssh fvg-tokyo
docker exec -it fvg-trade_executor-1 bash /app/deploy/apply_migrations.sh
```

If `deploy/` is not copied into the executor image yet, run the script via
the host with an env shim:

```bash
ssh fvg-tokyo
export DATABASE_URL="postgresql://fvg:$(grep ^DB_PW /opt/fvg/.env | cut -d= -f2)@127.0.0.1:5432/fvg"
# (start postgres ephemeral port-forward if needed: docker run --rm --network host)
bash /opt/fvg/repo/deploy/apply_migrations.sh
```

### Current migrations

- `0001_multi_user_live.sql` — base multi-user schema.
- `0002_user_rr_and_notional.sql` — `rr_ratio`, notional columns.
- `0003_fixed_risk_sizing.sql` — `fixed_risk_usdt`, `max_notional_usdt`.
- `0004_user_margin_mode.sql` — `margin_mode` (ISOLATED/CROSSED).
- `0005_user_risk_mode_and_sl_controls.sql` — `risk_mode`, `sl_enabled`,
  `sl_mult` so users can choose percent-equity vs fixed-USD risk and toggle SL.
- `0006_default_rr_2_for_existing_users.sql` — bumps legacy `rr_ratio=1.0`
  users to `2.0` so live trades match the channel banner.

## Watchdog

`/etc/systemd/system/fvg-watchdog.service` + `.timer` restart
`fvg-fvg-alpha-caller-1` if no `WS bar closed` log line in 17 minutes. Source
at [`deploy/systemd/`](systemd/).

## Promote first admin

```bash
docker exec -i fvg-postgres-1 psql -U fvg -d fvg \
    -c "UPDATE users SET is_admin = true WHERE telegram_id = <ADMIN_TG_ID>;"
```
