-- 0005: people, groups and roles (S2). Each principal has a stored role, admin or member (the default for
-- anyone who signs in); admins manage people; the gateway's catalog loses its per-model groups, because the
-- policy's models.allowed is now the one list of the models a person may use. Run by the migrating role.
--
-- Who is an admin: a stored role of admin, or membership of a group named in ROW_BOT_ORG_IDP's admin_groups
-- (row_bot_org.identity.roles.is_admin). row_bot_is_admin() (migration 0003) is unchanged: it still reads
-- row_bot.admin, which the server sets in a transaction only after that check (roles.enter_admin), so a
-- route that forgets the check acts as a member, whoever calls it.
--
-- Row-level security: a principal still reads only its own row (its role included). An admin's transaction
-- reads every principal, their groups and sessions, may change a principal's role and status (no other
-- column), and may revoke sessions (set revoked_at, never clear it).

ALTER TABLE row_bot_org.principals
    ADD COLUMN role text NOT NULL DEFAULT 'member' CHECK (role IN ('admin', 'member'));

CREATE POLICY principals_admin_read ON row_bot_org.principals FOR SELECT
    USING (row_bot_org.row_bot_is_admin());
CREATE POLICY principals_admin_change ON row_bot_org.principals FOR UPDATE
    USING (row_bot_org.row_bot_is_admin()) WITH CHECK (row_bot_org.row_bot_is_admin());
CREATE POLICY principal_groups_admin_read ON row_bot_org.principal_groups FOR SELECT
    USING (row_bot_org.row_bot_is_admin());
CREATE POLICY server_sessions_admin_read ON row_bot_org.server_sessions FOR SELECT
    USING (row_bot_org.row_bot_is_admin());
CREATE POLICY server_sessions_admin_revoke ON row_bot_org.server_sessions FOR UPDATE
    USING (row_bot_org.row_bot_is_admin())
    WITH CHECK (row_bot_org.row_bot_is_admin() AND revoked_at IS NOT NULL);

DO $$
BEGIN
    EXECUTE pg_catalog.format('REVOKE UPDATE ON row_bot_org.principals FROM %I', row_bot_org.runtime_role());
    EXECUTE pg_catalog.format('GRANT UPDATE (role, status) ON row_bot_org.principals TO %I',
                              row_bot_org.runtime_role());
END
$$;

-- The gateway: every signed-in principal sees the catalog; the server filters it by the caller's effective
-- policy (models.allowed), for /api/models and every call.
DROP POLICY catalog_models_member ON row_bot_org.catalog_models;
ALTER TABLE row_bot_org.catalog_models DROP COLUMN allowed_groups;
CREATE POLICY catalog_models_member ON row_bot_org.catalog_models FOR SELECT
    USING (row_bot_org.row_bot_principal() IS NOT NULL);
