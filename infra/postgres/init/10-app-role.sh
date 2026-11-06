#!/bin/sh
# Local dev only: give the runtime role (ledgerguard_app) a login and password.
# Runs automatically on a NEW pg_data volume (docker-entrypoint-initdb.d). For an
# existing volume run `make db-role` / `./scripts/dev.ps1 db-role`. Idempotent.
# Privileges are granted by the Alembic migrations, not here.
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v app_password="$POSTGRES_APP_PASSWORD" <<'EOSQL'
SELECT 'CREATE ROLE ledgerguard_app'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ledgerguard_app') \gexec
ALTER ROLE ledgerguard_app WITH LOGIN PASSWORD :'app_password';
EOSQL
