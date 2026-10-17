#!/bin/sh
# Runs only on initial PostgreSQL data-directory creation, as bootstrap admin.
set -eu
psql --set=ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB"   --set=app_password="$(cat /run/secrets/postgres_password)" <<'SQL'
CREATE ROLE tracecause_app LOGIN PASSWORD :'app_password';
GRANT CONNECT ON DATABASE tracecause TO tracecause_app;
GRANT USAGE ON SCHEMA public TO tracecause_app;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO tracecause_app;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO tracecause_app;
SQL
# Migrations run as bootstrap/migration owner, never through the runtime API.
# Existing volumes need an explicit audited role/grant migration, not rerunning
# initialization by deleting the volume. Never print secrets or enable set -x.
