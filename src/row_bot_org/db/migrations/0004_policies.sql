-- 0004: the company policy (S3). One company policy (group_name NULL) and optional overrides per group,
-- each change a new row with the next version, who made it and when. Rows are never updated or deleted:
-- the table is its own history. Run by the migrating role, which owns it.
--
-- Row-level security:
-- * A signed-in principal reads the company policy and the overrides of its own groups (its effective
--   policy), and nothing else.
-- * An admin reads every row and adds rows as itself. row_bot_is_admin() reads row_bot.admin, which the
--   server sets in a transaction only after its admin check (row_bot_org.identity.roles.enter_admin), so a
--   route that forgets the check reads and writes as a member. S2 may replace its body with a lookup of the
--   roles it stores.
-- * No role may UPDATE or DELETE a row: there is no policy for it, and the runtime group has no privilege.

CREATE FUNCTION row_bot_org.row_bot_is_admin() RETURNS boolean
    LANGUAGE sql STABLE PARALLEL SAFE
    AS $$
        SELECT row_bot_org.row_bot_principal() IS NOT NULL
           AND COALESCE(pg_catalog.current_setting('row_bot.admin', true) OPERATOR(pg_catalog.=) 'on', false)
    $$;

CREATE TABLE row_bot_org.policies (
    version bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    group_name text CHECK (group_name IS NULL OR (pg_catalog.char_length(group_name) OPERATOR(pg_catalog.>=) 1
                           AND pg_catalog.char_length(group_name) OPERATOR(pg_catalog.<=) 256)),
    -- As written and checked by row_bot.policy (only the settings it sets), without its version.
    document jsonb NOT NULL CHECK (pg_catalog.jsonb_typeof(document) OPERATOR(pg_catalog.=) 'object'),
    -- The admin's principal id. Not a foreign key: the history outlives the principal.
    changed_by uuid NOT NULL,
    changed_at timestamptz NOT NULL DEFAULT pg_catalog.now()
);
CREATE INDEX policies_scope ON row_bot_org.policies (group_name, version DESC);

ALTER TABLE row_bot_org.policies ENABLE ROW LEVEL SECURITY;
ALTER TABLE row_bot_org.policies FORCE ROW LEVEL SECURITY;

-- The migrating role (an operator) reads every row.
CREATE POLICY policies_definer ON row_bot_org.policies FOR SELECT TO CURRENT_USER USING (true);
CREATE POLICY policies_member_read ON row_bot_org.policies FOR SELECT
    USING (row_bot_org.row_bot_principal() IS NOT NULL
           AND (group_name IS NULL OR row_bot_org.row_bot_in_group(group_name)));
CREATE POLICY policies_admin_read ON row_bot_org.policies FOR SELECT
    USING (row_bot_org.row_bot_is_admin());
CREATE POLICY policies_admin_write ON row_bot_org.policies FOR INSERT
    WITH CHECK (row_bot_org.row_bot_is_admin()
                AND changed_by OPERATOR(pg_catalog.=) row_bot_org.row_bot_principal()::pg_catalog.uuid);

CALL row_bot_org.grant_runtime_access('row_bot_org.policies');

REVOKE ALL ON FUNCTION row_bot_org.row_bot_is_admin() FROM PUBLIC;
DO $$
BEGIN
    EXECUTE pg_catalog.format('REVOKE UPDATE, DELETE ON row_bot_org.policies FROM %I', row_bot_org.runtime_role());
    EXECUTE pg_catalog.format('GRANT EXECUTE ON FUNCTION row_bot_org.row_bot_is_admin() TO %I',
                              row_bot_org.runtime_role());
END
$$;
