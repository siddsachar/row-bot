-- 0001: the foundation every later table builds on. Run by the migrating role, which owns the schema
-- row_bot_org (the runner made it, with the ledger) and everything in it.

-- pgvector, for recall later (ORG-4). It isn't a trusted extension: a migrating role that isn't a
-- superuser needs an administrator to have run this once in the database first.
CREATE EXTENSION IF NOT EXISTS vector;

-- The runtime role's privileges go to this group, never to a login role directly: the server's login
-- role is made a member of it (deploy/org/README.md). Creating it needs CREATEROLE, or an
-- administrator who made it first.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'row_bot_org_runtime') THEN
        CREATE ROLE row_bot_org_runtime NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
    END IF;
END
$$;

-- The runtime role reads and writes rows and nothing more: no CREATE in the schema, no ownership,
-- so it can't alter a table or turn its row-level security off.
GRANT USAGE ON SCHEMA row_bot_org TO row_bot_org_runtime;
-- /readyz and serve read the ledger to see that no migration is pending.
GRANT SELECT ON row_bot_org.schema_migrations TO row_bot_org_runtime;
-- Every table and sequence a later migration creates gets the same DML grants.
ALTER DEFAULT PRIVILEGES IN SCHEMA row_bot_org
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO row_bot_org_runtime;
ALTER DEFAULT PRIVILEGES IN SCHEMA row_bot_org
    GRANT USAGE, SELECT ON SEQUENCES TO row_bot_org_runtime;

-- Row-level security reads who is asking from two transaction-local settings, which
-- principal_transaction (row_bot_org.db.rls) sets with set_config(..., true). Outside one they are
-- unset or empty: no principal, no groups, so policies match no row.
CREATE FUNCTION row_bot_org.row_bot_principal() RETURNS text
    LANGUAGE sql STABLE PARALLEL SAFE
    AS $$ SELECT nullif(current_setting('row_bot.principal', true), '') $$;

CREATE FUNCTION row_bot_org.row_bot_in_group(group_name text) RETURNS boolean
    LANGUAGE sql STABLE PARALLEL SAFE
    AS $$ SELECT coalesce(nullif(current_setting('row_bot.groups', true), '')::jsonb ? group_name, false) $$;

REVOKE ALL ON FUNCTION row_bot_org.row_bot_principal() FROM PUBLIC;
REVOKE ALL ON FUNCTION row_bot_org.row_bot_in_group(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION row_bot_org.row_bot_principal() TO row_bot_org_runtime;
GRANT EXECUTE ON FUNCTION row_bot_org.row_bot_in_group(text) TO row_bot_org_runtime;

-- A principal-scoped table, from a later migration, follows this pattern:
--   CREATE TABLE row_bot_org.<name> (..., owner text NOT NULL DEFAULT row_bot_org.row_bot_principal());
--   ALTER TABLE row_bot_org.<name> ENABLE ROW LEVEL SECURITY;
--   ALTER TABLE row_bot_org.<name> FORCE ROW LEVEL SECURITY;
--   CREATE POLICY <name>_owner ON row_bot_org.<name>
--       USING (owner = row_bot_org.row_bot_principal())
--       WITH CHECK (owner = row_bot_org.row_bot_principal());
