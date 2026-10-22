#!/usr/bin/env bash
# Executed by the postgres image on first start (docker-entrypoint-initdb.d).
set -euo pipefail
psql --username "$POSTGRES_USER" --dbname postgres \
  -v owner_password="${APP_OWNER_PASSWORD:?}" \
  -v app_password="${APP_USER_PASSWORD:?}" \
  -v databases="${APP_DATABASES:-app}" \
  -f /bootstrap/bootstrap.sql
