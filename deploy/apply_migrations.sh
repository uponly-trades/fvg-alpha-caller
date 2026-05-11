#!/usr/bin/env bash
#
# Apply pending DB migrations in numeric order.
#
# Usage (from repo root, on a host that can reach fvg-postgres):
#   DATABASE_URL="postgresql://user:pass@fvg-postgres:5432/fvg" \
#       bash deploy/apply_migrations.sh
#
# Or inside the trade_executor container shell:
#   docker exec -it fvg-trade_executor-1 bash /app/deploy/apply_migrations.sh
# (DATABASE_URL is already set by docker-compose env.)
#
# Idempotent: every migration uses ADD COLUMN IF NOT EXISTS / conditional
# constraints / filtered UPDATE, so re-running is safe and a no-op after
# the first successful pass.
set -euo pipefail

if [[ -z "${DATABASE_URL:-}" ]]; then
    echo "error: DATABASE_URL is not set" >&2
    exit 1
fi

if ! command -v psql >/dev/null 2>&1; then
    echo "error: psql not found on PATH" >&2
    echo "install postgres client: apt-get install -y postgresql-client" >&2
    exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MIG_DIR="$REPO_ROOT/migrations"

if [[ ! -d "$MIG_DIR" ]]; then
    echo "error: migrations dir not found at $MIG_DIR" >&2
    exit 1
fi

shopt -s nullglob
files=("$MIG_DIR"/*.sql)
if [[ ${#files[@]} -eq 0 ]]; then
    echo "no migration files in $MIG_DIR"
    exit 0
fi

for f in "${files[@]}"; do
    name="$(basename "$f")"
    echo "→ applying $name"
    if ! psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$f"; then
        echo "✗ migration failed: $name" >&2
        exit 1
    fi
done

echo "✓ all migrations applied"
