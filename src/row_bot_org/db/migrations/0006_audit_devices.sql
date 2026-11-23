-- 0006: the audit store and the device registry (S5). Run by the migrating role, which owns both.
--
-- audit_events is the server's one hash chain of audit events (row_bot.audit, contracts/audit/v1): each row is
-- a ChainLink (seq, prev_hash, hash, the event's JSON), with the event's action, actor, target and time copied
-- out for the admins' filters.
-- * Events are appended only through append_audit_event, a SECURITY DEFINER function that takes the event's
--   canonical (RFC 8785) bytes, serialises appends with a transaction-scoped advisory lock, and computes the
--   link's hash from the head it reads under that lock. It runs in the caller's transaction, so an event is
--   stored with the change it records, or neither is. The runtime group has no INSERT on the table.
-- * Nobody may UPDATE or DELETE a row: the runtime group has no such privilege, and no policy allows it, so
--   even the owner's statements change nothing while row-level security is forced. (An owner can still turn it
--   off: GET /api/admin/audit/verify finds any row changed that way.)
-- * Only an admin's transaction (row_bot_is_admin()) reads rows. A member reads none, not even their own.
--
-- devices are the Row-Bots people connect. A person registers their own and refreshes last_seen_at while it
-- isn't revoked; an admin reads every device and revokes one (sets revoked_at, never clears it).

CREATE TABLE row_bot_org.audit_events (
    seq bigint PRIMARY KEY CHECK (seq OPERATOR(pg_catalog.>=) 0),
    prev_hash text NOT NULL,
    hash text NOT NULL UNIQUE,
    event jsonb NOT NULL,
    event_id uuid NOT NULL UNIQUE,
    action text NOT NULL,
    actor_id text NOT NULL,
    target_ref text,
    recorded_at timestamptz NOT NULL
);
CREATE INDEX audit_events_action ON row_bot_org.audit_events (action, seq);
CREATE INDEX audit_events_actor ON row_bot_org.audit_events (actor_id, seq);
CREATE INDEX audit_events_target ON row_bot_org.audit_events (target_ref, seq);
CREATE INDEX audit_events_recorded ON row_bot_org.audit_events (recorded_at);

ALTER TABLE row_bot_org.audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE row_bot_org.audit_events FORCE ROW LEVEL SECURITY;

-- The owner (append_audit_event, and operators) reads and appends; admins read.
CREATE POLICY audit_events_definer_read ON row_bot_org.audit_events FOR SELECT TO CURRENT_USER USING (true);
CREATE POLICY audit_events_definer_append ON row_bot_org.audit_events FOR INSERT TO CURRENT_USER WITH CHECK (true);
CREATE POLICY audit_events_admin_read ON row_bot_org.audit_events FOR SELECT
    USING (row_bot_org.row_bot_is_admin());

CALL row_bot_org.grant_runtime_access('row_bot_org.audit_events');

-- Appends one event (its canonical bytes) at the head of the chain: hash = SHA-256(lp('row-bot/audit/v1') ||
-- lp(prev) || lp(event)), lp = an 8-byte big-endian length prefix, prev the previous hash's raw bytes, and the
-- first link's prev the genesis hash of the chain 'company-server' (row_bot.audit.chain). An event whose id
-- is already stored by the same actor is skipped (a device's retry): false. The id stored by another actor is
-- refused (unique_violation): one person's event never passes as a copy of another's.
CREATE FUNCTION row_bot_org.append_audit_event(p_event bytea) RETURNS boolean
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER
    SET search_path = pg_catalog, pg_temp
    AS $$
DECLARE
    doc jsonb := pg_catalog.convert_from(p_event, 'UTF8')::pg_catalog.jsonb;
    domain bytea := pg_catalog.convert_to('row-bot/audit/v1', 'UTF8');
    head_seq bigint;
    head_hash text;
    prev bytea;
    stored_actor text;
BEGIN
    -- The bytes of "RBOrgAud": one append at a time, until the appending transaction ends.
    PERFORM pg_catalog.pg_advisory_xact_lock(5927387412350203236);
    SELECT e.actor_id INTO stored_actor FROM row_bot_org.audit_events e
    WHERE e.event_id OPERATOR(pg_catalog.=) (doc OPERATOR(pg_catalog.->>) 'id')::pg_catalog.uuid;
    IF FOUND THEN
        IF stored_actor OPERATOR(pg_catalog.=) ((doc OPERATOR(pg_catalog.->) 'actor') OPERATOR(pg_catalog.->>) 'id')
        THEN
            RETURN false;
        END IF;
        RAISE EXCEPTION 'this audit event id is stored for another actor' USING ERRCODE = 'unique_violation';
    END IF;
    SELECT e.seq, e.hash INTO head_seq, head_hash FROM row_bot_org.audit_events e ORDER BY e.seq DESC LIMIT 1;
    IF head_seq IS NULL THEN
        head_seq := -1;
        prev := pg_catalog.sha256(
            pg_catalog.int8send((pg_catalog.octet_length(domain) OPERATOR(pg_catalog.+) 8)::bigint)
            OPERATOR(pg_catalog.||) domain OPERATOR(pg_catalog.||) pg_catalog.convert_to('/genesis', 'UTF8')
            OPERATOR(pg_catalog.||) pg_catalog.int8send(14::bigint)
            OPERATOR(pg_catalog.||) pg_catalog.convert_to('company-server', 'UTF8'));
    ELSE
        prev := pg_catalog.decode(head_hash, 'hex');
    END IF;
    INSERT INTO row_bot_org.audit_events (seq, prev_hash, hash, event, event_id, action, actor_id, target_ref,
                                          recorded_at)
    VALUES (
        head_seq OPERATOR(pg_catalog.+) 1,
        pg_catalog.encode(prev, 'hex'),
        pg_catalog.encode(pg_catalog.sha256(
            pg_catalog.int8send(pg_catalog.octet_length(domain)::bigint) OPERATOR(pg_catalog.||) domain
            OPERATOR(pg_catalog.||) pg_catalog.int8send(32::bigint) OPERATOR(pg_catalog.||) prev
            OPERATOR(pg_catalog.||) pg_catalog.int8send(pg_catalog.octet_length(p_event)::bigint)
            OPERATOR(pg_catalog.||) p_event), 'hex'),
        doc,
        (doc OPERATOR(pg_catalog.->>) 'id')::pg_catalog.uuid,
        doc OPERATOR(pg_catalog.->>) 'action',
        (doc OPERATOR(pg_catalog.->) 'actor') OPERATOR(pg_catalog.->>) 'id',
        (doc OPERATOR(pg_catalog.->) 'target') OPERATOR(pg_catalog.->>) 'ref',
        (doc OPERATOR(pg_catalog.->>) 'recorded_at')::pg_catalog.timestamptz);
    RETURN true;
END
$$;

REVOKE ALL ON FUNCTION row_bot_org.append_audit_event(bytea) FROM PUBLIC;

CREATE TABLE row_bot_org.devices (
    id uuid PRIMARY KEY DEFAULT pg_catalog.gen_random_uuid(),
    principal_id uuid NOT NULL REFERENCES row_bot_org.principals (id) ON DELETE CASCADE
        DEFAULT row_bot_org.row_bot_principal()::pg_catalog.uuid,
    name text NOT NULL CHECK (pg_catalog.char_length(name) OPERATOR(pg_catalog.>=) 1
                              AND pg_catalog.char_length(name) OPERATOR(pg_catalog.<=) 200),
    platform text NOT NULL CHECK (platform IN ('windows', 'macos', 'linux')),
    app_version text NOT NULL CHECK (pg_catalog.char_length(app_version) OPERATOR(pg_catalog.<=) 64),
    first_seen_at timestamptz NOT NULL DEFAULT pg_catalog.now(),
    last_seen_at timestamptz NOT NULL DEFAULT pg_catalog.now(),
    revoked_at timestamptz
);
CREATE INDEX devices_principal ON row_bot_org.devices (principal_id);

ALTER TABLE row_bot_org.devices ENABLE ROW LEVEL SECURITY;
ALTER TABLE row_bot_org.devices FORCE ROW LEVEL SECURITY;

CREATE POLICY devices_definer ON row_bot_org.devices FOR SELECT TO CURRENT_USER USING (true);
-- A person: their own devices; they add and refresh them only while they aren't revoked.
CREATE POLICY devices_self_read ON row_bot_org.devices FOR SELECT
    USING (principal_id OPERATOR(pg_catalog.=) row_bot_org.row_bot_principal()::pg_catalog.uuid);
CREATE POLICY devices_self_register ON row_bot_org.devices FOR INSERT
    WITH CHECK (principal_id OPERATOR(pg_catalog.=) row_bot_org.row_bot_principal()::pg_catalog.uuid
                AND revoked_at IS NULL);
CREATE POLICY devices_self_seen ON row_bot_org.devices FOR UPDATE
    USING (principal_id OPERATOR(pg_catalog.=) row_bot_org.row_bot_principal()::pg_catalog.uuid
           AND revoked_at IS NULL)
    WITH CHECK (principal_id OPERATOR(pg_catalog.=) row_bot_org.row_bot_principal()::pg_catalog.uuid
                AND revoked_at IS NULL);
-- An admin: every device, and revoking one.
CREATE POLICY devices_admin_read ON row_bot_org.devices FOR SELECT USING (row_bot_org.row_bot_is_admin());
CREATE POLICY devices_admin_revoke ON row_bot_org.devices FOR UPDATE
    USING (row_bot_org.row_bot_is_admin())
    WITH CHECK (row_bot_org.row_bot_is_admin() AND revoked_at IS NOT NULL);

-- A revoke is final, whoever updates the row: permissive policies OR together, so an admin's own device would
-- otherwise pass devices_admin_revoke's USING and devices_self_seen's WITH CHECK with revoked_at cleared.
CREATE FUNCTION row_bot_org.devices_keep_revoked() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog, pg_temp
    AS $$
BEGIN
    IF OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at THEN
        RAISE EXCEPTION 'a revoked device stays revoked' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END
$$;
REVOKE ALL ON FUNCTION row_bot_org.devices_keep_revoked() FROM PUBLIC;
CREATE TRIGGER devices_keep_revoked BEFORE UPDATE ON row_bot_org.devices
    FOR EACH ROW EXECUTE FUNCTION row_bot_org.devices_keep_revoked();

CALL row_bot_org.grant_runtime_access('row_bot_org.devices');

DO $$
BEGIN
    EXECUTE pg_catalog.format('REVOKE INSERT, UPDATE, DELETE ON row_bot_org.audit_events FROM %I',
                              row_bot_org.runtime_role());
    EXECUTE pg_catalog.format('GRANT EXECUTE ON FUNCTION row_bot_org.append_audit_event(bytea) TO %I',
                              row_bot_org.runtime_role());
    -- A device's owner refreshes when it was seen and what version it runs; an admin sets revoked_at.
    EXECUTE pg_catalog.format('REVOKE UPDATE, DELETE ON row_bot_org.devices FROM %I', row_bot_org.runtime_role());
    EXECUTE pg_catalog.format('GRANT UPDATE (name, app_version, last_seen_at, revoked_at) ON row_bot_org.devices TO %I',
                              row_bot_org.runtime_role());
END
$$;
