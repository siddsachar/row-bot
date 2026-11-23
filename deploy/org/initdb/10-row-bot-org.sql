-- Row-Bot Org: the database, its two roles and the runtime group. The Postgres image runs this once, on an
-- empty volume, as the postgres superuser, before the database listens on the network (psql with
-- ON_ERROR_STOP). The passwords come from the secret files deploy/org/make_secrets.py wrote, never from the
-- environment; deploy/org/README.md describes the roles.
--
-- * row_bot_org_owner migrates, and owns the database, the schema and every table. Never a superuser:
--   `migrate` refuses one, since the sign-in functions it owns would run past every check.
-- * row_bot_org_runtime_row_bot_org is the runtime group migration 0001 expects: row_bot_org.runtime_role()
--   for a database named row_bot_org. Made here, so the owner needs no CREATEROLE; 0001 finds it and gives
--   it its privileges. Like 0001, only the group may connect (the owner keeps its own right), set here too
--   so that a restored dump, which carries no database-level grants, gets it as well.
-- * row_bot_org_app is what `serve` connects as: a member of the runtime group and nothing else.

-- A statement that fails is not written to the log with its text: these carry passwords.
SET log_min_error_statement = panic;
\set owner_password `cat /run/secrets/owner_password`
\set app_password `cat /run/secrets/app_password`

CREATE ROLE row_bot_org_owner LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
    PASSWORD :'owner_password';
CREATE ROLE row_bot_org_runtime_row_bot_org NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE row_bot_org_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
    PASSWORD :'app_password';
GRANT row_bot_org_runtime_row_bot_org TO row_bot_org_app;
\unset owner_password
\unset app_password

CREATE DATABASE row_bot_org OWNER row_bot_org_owner;
REVOKE CONNECT ON DATABASE row_bot_org FROM PUBLIC;
GRANT CONNECT ON DATABASE row_bot_org TO row_bot_org_runtime_row_bot_org;

-- pgvector isn't a trusted extension, so the superuser makes it; 0001's CREATE EXTENSION IF NOT EXISTS
-- then finds it.
\connect row_bot_org
CREATE EXTENSION vector;
