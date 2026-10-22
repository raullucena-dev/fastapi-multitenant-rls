#!/usr/bin/env bash
# Executed by the postgres image on first start (docker-entrypoint-initdb.d).
# APP_OWNER_PASSWORD, APP_USER_PASSWORD and APP_DATABASES come from the container env.
set -euo pipefail
psql --username "$POSTGRES_USER" --dbname postgres -f /bootstrap/bootstrap_roles.sql
