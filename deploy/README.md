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

```
psql "$DATABASE_URL" -f ../migrations/0001_multi_user_live.sql
```

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
