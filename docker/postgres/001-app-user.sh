#!/bin/sh
set -eu
app_password="$(cat /run/secrets/postgres_password)"
[ -n "$app_password" ] || { echo 'app password secret is empty' >&2; exit 1; }
psql --set=ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=app_password="$app_password" <<'SQL'
CREATE ROLE tracecause_app LOGIN PASSWORD :'app_password';
GRANT CONNECT ON DATABASE tracecause TO tracecause_app;
GRANT USAGE ON SCHEMA public TO tracecause_app;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO tracecause_app;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO tracecause_app;
SQL
