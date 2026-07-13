#!/bin/bash
# Runs on first boot of the dev Postgres container (docker-entrypoint-initdb.d).
# Applies every migration in order, then the Los Jazmines seed.
set -euo pipefail

for f in /db/migrations/*.sql; do
  echo ">> applying $f"
  psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f "$f"
done

echo ">> applying seed"
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f /db/seed.sql
