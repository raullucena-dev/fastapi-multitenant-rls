-- Cluster-level bootstrap: roles and databases. Run once as a superuser:
--
--   psql -v owner_password=... -v app_password=... -v databases='app app_test' -f db/bootstrap.sql
--
-- Everything inside the databases (tables, RLS policies, grants, the login
-- function) is owned by Alembic migrations, not by this script.
--
--   app_owner  LOGIN    owns the schema; used only by migrations.
--   app_user   LOGIN    used by the API. Not superuser, not owner, NOBYPASSRLS.
--   app_auth   NOLOGIN  owns the SECURITY DEFINER login lookup function.
\set ON_ERROR_STOP on

SELECT format('CREATE ROLE app_owner LOGIN PASSWORD %L', :'owner_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_owner')
\gexec
SELECT format('CREATE ROLE app_user LOGIN PASSWORD %L', :'app_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_user')
\gexec
SELECT 'CREATE ROLE app_auth NOLOGIN'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_auth')
\gexec

ALTER ROLE app_owner NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
ALTER ROLE app_user  NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
ALTER ROLE app_auth  NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOLOGIN;

-- app_owner must be a member of app_auth to hand it ownership of the login
-- function. INHERIT FALSE / SET TRUE: app_owner gains no privileges from it.
GRANT app_auth TO app_owner WITH INHERIT FALSE, SET TRUE;

SELECT format('CREATE DATABASE %I OWNER app_owner', db)
FROM unnest(string_to_array(:'databases', ' ')) AS db
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = db)
\gexec
