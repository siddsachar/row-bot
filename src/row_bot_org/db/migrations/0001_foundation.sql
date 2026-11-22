-- 0001: the foundation every later table builds on. Run by the migrating role, which owns the schema
-- row_bot_org (the runner made it, with the ledger) and everything in it, and owns the database.
-- Everything here names pg_catalog's functions and operators, so no search_path changes their meaning.

-- pgvector, for recall later (ORG-4). It isn't a trusted extension: a migrating role that isn't a
-- superuser needs an administrator to have run this once in the database first.
CREATE EXTENSION IF NOT EXISTS vector;

-- This database's runtime group, row_bot_org_runtime_<database> (row_bot_org_runtime_<md5 of the
-- name> when that is longer than 63 bytes), so two Org databases on one cluster never share one. The
-- server's login role is made a member of it (deploy/org/README.md). Creating it needs CREATEROLE, or
-- an administrator who made it first. Only the group may connect to the database.
DO $$
DECLARE
    runtime text := 'row_bot_org_runtime_' || pg_catalog.current_database();
BEGIN
    IF pg_catalog.octet_length(runtime) OPERATOR(pg_catalog.>) 63 THEN
        runtime := 'row_bot_org_runtime_' || pg_catalog.md5(pg_catalog.current_database());
    END IF;
    EXECUTE pg_catalog.format(
        'CREATE FUNCTION row_bot_org.runtime_role() RETURNS name LANGUAGE sql IMMUTABLE PARALLEL SAFE AS %L',
        pg_catalog.format('SELECT %L::pg_catalog.name', runtime));
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname OPERATOR(pg_catalog.=) runtime) THEN
        EXECUTE pg_catalog.format('CREATE ROLE %I NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE', runtime);
    END IF;
    EXECUTE pg_catalog.format('REVOKE CONNECT ON DATABASE %I FROM PUBLIC', pg_catalog.current_database());
    EXECUTE pg_catalog.format('GRANT CONNECT ON DATABASE %I TO %I', pg_catalog.current_database(), runtime);
    -- The group reads the ledger (serve and /readyz check that no migration is pending) and uses the
    -- schema. Nothing else: no CREATE, no ownership, so it can't alter a table or its row-level security.
    EXECUTE pg_catalog.format('GRANT USAGE ON SCHEMA row_bot_org TO %I', runtime);
    EXECUTE pg_catalog.format('GRANT SELECT ON row_bot_org.schema_migrations TO %I', runtime);
END
$$;

-- Row-level security reads who is asking from two transaction-local settings, which
-- principal_transaction (row_bot_org.db.rls) sets with set_config(..., true). Outside one they are
-- unset or empty: no principal, no groups, so policies match no row. Plain SQL bodies, so the planner
-- inlines them into the policies.
CREATE FUNCTION row_bot_org.row_bot_principal() RETURNS text
    LANGUAGE sql STABLE PARALLEL SAFE
    AS $$
        SELECT CASE WHEN pg_catalog.current_setting('row_bot.principal', true) OPERATOR(pg_catalog.=) ''
                    THEN NULL
                    ELSE pg_catalog.current_setting('row_bot.principal', true) END
    $$;

CREATE FUNCTION row_bot_org.row_bot_in_group(group_name text) RETURNS boolean
    LANGUAGE sql STABLE PARALLEL SAFE
    AS $$
        SELECT CASE WHEN pg_catalog.current_setting('row_bot.groups', true) OPERATOR(pg_catalog.=) ''
                    THEN false
                    ELSE COALESCE(pg_catalog.current_setting('row_bot.groups', true)::pg_catalog.jsonb
                                  OPERATOR(pg_catalog.?) group_name, false) END
    $$;

-- The only way a table reaches the runtime group: refused unless the table enables and forces
-- row-level security. (migrate also refuses any migration that leaves an unprotected table readable.)
CREATE PROCEDURE row_bot_org.grant_runtime_access(target regclass)
    LANGUAGE plpgsql
    SET search_path = pg_catalog
    AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_class
                   WHERE oid OPERATOR(pg_catalog.=) target AND relkind IN ('r', 'p')
                   AND relrowsecurity AND relforcerowsecurity) THEN
        RAISE EXCEPTION '% needs ENABLE and FORCE ROW LEVEL SECURITY before the runtime role may use it', target;
    END IF;
    EXECUTE pg_catalog.format('GRANT SELECT, INSERT, UPDATE, DELETE ON %s TO %I', target, row_bot_org.runtime_role());
END
$$;

REVOKE ALL ON FUNCTION row_bot_org.row_bot_principal() FROM PUBLIC;
REVOKE ALL ON FUNCTION row_bot_org.row_bot_in_group(text) FROM PUBLIC;
REVOKE ALL ON PROCEDURE row_bot_org.grant_runtime_access(regclass) FROM PUBLIC;
DO $$
BEGIN
    EXECUTE pg_catalog.format('GRANT EXECUTE ON FUNCTION row_bot_org.row_bot_principal() TO %I',
                              row_bot_org.runtime_role());
    EXECUTE pg_catalog.format('GRANT EXECUTE ON FUNCTION row_bot_org.row_bot_in_group(text) TO %I',
                              row_bot_org.runtime_role());
END
$$;

-- A principal-scoped table, from a later migration, follows this pattern (an identity column needs no
-- sequence grant):
--   CREATE TABLE row_bot_org.<name> (
--       id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
--       owner text NOT NULL DEFAULT row_bot_org.row_bot_principal(), ...);
--   ALTER TABLE row_bot_org.<name> ENABLE ROW LEVEL SECURITY;
--   ALTER TABLE row_bot_org.<name> FORCE ROW LEVEL SECURITY;
--   CREATE POLICY <name>_owner ON row_bot_org.<name>
--       USING (owner OPERATOR(pg_catalog.=) row_bot_org.row_bot_principal())
--       WITH CHECK (owner OPERATOR(pg_catalog.=) row_bot_org.row_bot_principal());
--   CALL row_bot_org.grant_runtime_access('row_bot_org.<name>');
