-- Cluster-level bootstrap: roles and databases. Idempotent; safe to re-run (it also
-- rotates the two passwords to the values given).
--
-- Works both as a real superuser (docker compose, CI) and as the owner role of a managed
-- PostgreSQL such as Neon, which has CREATEROLE/CREATEDB but is NOT a superuser:
--
--   export APP_OWNER_PASSWORD=... APP_USER_PASSWORD=... APP_DATABASES=app
--   psql "$OWNER_CONNECTION_STRING" -f scripts/bootstrap_roles.sql
--
-- Passwords are read from the environment (\getenv), never from the command line.
-- Everything inside the databases (tables, RLS, grants, the login function) belongs to
-- Alembic migrations, not to this script.
--
--   app_owner  LOGIN    owns the schema; used only by migrations and operational jobs.
--   app_user   LOGIN    used by the API. Not superuser, not owner, NOBYPASSRLS.
--   app_auth   NOLOGIN  owns the SECURITY DEFINER login lookup function.
\set ON_ERROR_STOP on

\getenv owner_password APP_OWNER_PASSWORD
\getenv app_password APP_USER_PASSWORD
\getenv databases APP_DATABASES
\if :{?owner_password}
\else
  DO $$ BEGIN RAISE EXCEPTION 'APP_OWNER_PASSWORD is not set'; END $$;
\endif
\if :{?app_password}
\else
  DO $$ BEGIN RAISE EXCEPTION 'APP_USER_PASSWORD is not set'; END $$;
\endif
\if :{?databases}
\else
  \set databases app
\endif

-- New roles get the safe defaults (NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS).
-- A non-superuser cannot even *mention* SUPERUSER/BYPASSRLS in CREATE/ALTER ROLE, so the
-- attributes are verified below instead of being set explicitly.
SELECT 'CREATE ROLE app_owner LOGIN'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_owner')
\gexec
SELECT 'CREATE ROLE app_user LOGIN'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_user')
\gexec
SELECT 'CREATE ROLE app_auth NOLOGIN'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_auth')
\gexec

SELECT format('ALTER ROLE app_owner LOGIN NOCREATEDB NOCREATEROLE PASSWORD %L', :'owner_password')
\gexec
SELECT format('ALTER ROLE app_user LOGIN NOCREATEDB NOCREATEROLE PASSWORD %L', :'app_password')
\gexec
ALTER ROLE app_auth NOLOGIN NOCREATEDB NOCREATEROLE;

DO $$
DECLARE
  bad text;
BEGIN
  SELECT string_agg(rolname, ', ') INTO bad
  FROM pg_roles
  WHERE rolname IN ('app_owner', 'app_user', 'app_auth')
    AND (rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb OR rolreplication);
  IF bad IS NOT NULL THEN
    RAISE EXCEPTION 'roles must not be privileged (superuser/bypassrls/createrole/createdb/replication): %', bad
      USING HINT = 'Fix them as a superuser, or drop and re-create them with this script.';
  END IF;
  IF (SELECT rolcanlogin FROM pg_roles WHERE rolname = 'app_auth') THEN
    RAISE EXCEPTION 'app_auth must be NOLOGIN';
  END IF;
END
$$;

-- app_owner must be able to SET ROLE app_auth to hand it ownership of the login
-- function. INHERIT FALSE: app_owner gains none of app_auth's privileges implicitly.
GRANT app_auth TO app_owner WITH INHERIT FALSE, SET TRUE;

-- CREATE DATABASE ... OWNER app_owner requires being able to SET ROLE app_owner. A
-- superuser always can; a managed owner role (Neon) needs an explicit grant. Its
-- CREATEROLE already gives it ADMIN on the roles it created.
SELECT format('GRANT app_owner TO %I WITH INHERIT FALSE, SET TRUE', current_user)
WHERE NOT (SELECT rolsuper FROM pg_roles WHERE rolname = current_user)
\gexec

SELECT format('CREATE DATABASE %I OWNER app_owner', db)
FROM unnest(string_to_array(:'databases', ' ')) AS db
WHERE db <> '' AND NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = db)
\gexec

\echo 'bootstrap complete'
