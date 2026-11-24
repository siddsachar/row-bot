-- 0011: company connectors (B2): a SharePoint or OneDrive drive or folder (Microsoft Graph) or a Google Drive shared
-- drive or folder, synced into a space as documents, each with its file's upstream permissions mirrored. Run by the
-- migrating role, which owns all of it.
--
-- * connectors: an admin's source for one space, with what it needs to sign in (the tenant and client id, or the
--   service account's email, in the clear; the client secret or the service account's private key sealed with the
--   server key by row_bot_org.connectors.secrets, never stored or shown in the clear), its upstream principals mapped
--   to Row-Bot groups by hand (principal_map), where its sync stands (a cursor, a full listing under way) and its state
--   (active, paused by an admin, or error with a plain reason). Admins only; the brain's worker (0008's flag) reads
--   them and records how a sync went, to take the ones that are due (a trigger keeps it to those columns). run_as
--   is the admin whose transactions sync it (the last to save or resume it): its documents are uploaded, and their
--   model calls metered, as theirs.
-- * connector_items: what the sync knows of each upstream file and folder in scope (only those): its parent, version,
--   whether its content or permissions are to be read again, the document it became and the readers mirrored for it,
--   or why it isn't a document. Admins only.
-- * brain_documents gains connector_id and readers. readers NULL: everyone who reads the space (an upload). Else only
--   the people (e:<email, lower case>, o:<Entra object id>) and groups (g:<Row-Bot group>) listed, an admin's
--   transaction and the worker; an empty list is admins only. A connector's documents always have a list and uploads
--   never do (a CHECK), so a connector document is never readable by more than its list. Restrictive policies hide
--   such a document, its chunks, the facts that cite it and any mention of those from everyone else, on every read.
--   Only an admin's transaction sets or changes readers or connector_id, or a connector document's file; the worker
--   may only narrow a connector document's readers to admins (a stopped connector's). Deleting a connector document
--   deletes the facts that cite it, which would otherwise lose their source and be read by the whole space (triggers).
-- * principals gains entra_oid, the Entra object id (the ID token's oid) sign-in stores for an Entra principal, which
--   Graph names people by: sign_in (0002, SECURITY DEFINER) is made again with it, otherwise unchanged.
-- * A connector's documents aren't read for the knowledge graph (row_bot_org.brain.graph_store): an entity belongs to
--   the whole space, so its name, description and relations would tell people who may not open a file what it says.
-- * No new SECURITY DEFINER function. OPERATOR(...) forms all share one precedence, so every expression mixing two
--   of them is bracketed.

CREATE TABLE row_bot_org.connectors (
    id uuid PRIMARY KEY DEFAULT pg_catalog.gen_random_uuid(),
    kind text NOT NULL CHECK (kind IN ('microsoft', 'google')),
    space_id uuid NOT NULL REFERENCES row_bot_org.spaces (id) ON DELETE CASCADE,
    name text NOT NULL CHECK (pg_catalog.char_length(name) OPERATOR(pg_catalog.>=) 1
                              AND pg_catalog.char_length(name) OPERATOR(pg_catalog.<=) 160),
    -- Where: Microsoft, a drive (a site's document library or a OneDrive) and optionally a folder in it; Google, a
    -- shared drive, a folder, or a folder in a shared drive.
    drive_id text CHECK (pg_catalog.char_length(drive_id) OPERATOR(pg_catalog.<=) 512),
    folder_id text CHECK (pg_catalog.char_length(folder_id) OPERATOR(pg_catalog.<=) 512),
    -- Signing in: Microsoft's tenant and client id; Google's service account email (client_id) and, with
    -- domain-wide delegation, the user it acts as (subject).
    tenant_id text CHECK (pg_catalog.char_length(tenant_id) OPERATOR(pg_catalog.<=) 64),
    client_id text NOT NULL CHECK (pg_catalog.char_length(client_id) OPERATOR(pg_catalog.>=) 1
                                   AND pg_catalog.char_length(client_id) OPERATOR(pg_catalog.<=) 320),
    subject text CHECK (pg_catalog.char_length(subject) OPERATOR(pg_catalog.<=) 320),
    -- The client secret or private key, sealed (nonce || ciphertext and tag); NULL: none saved.
    secret_sealed bytea CHECK (pg_catalog.octet_length(secret_sealed) OPERATOR(pg_catalog.<=) 16384),
    -- Upstream principal key (row_bot_org.connectors.mirror) -> Row-Bot group, set by an admin.
    principal_map jsonb NOT NULL DEFAULT '{}'
        CHECK (pg_catalog.jsonb_typeof(principal_map) OPERATOR(pg_catalog.=) 'object'),
    state text NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'paused', 'error')),
    state_reason text CHECK (pg_catalog.char_length(state_reason) OPERATOR(pg_catalog.<=) 500),
    -- The upstream folder everything in scope is under (a drive's root, or the folder), once known.
    root_id text CHECK (pg_catalog.char_length(root_id) OPERATOR(pg_catalog.<=) 512),
    -- Where the change feed stands (Graph's delta link, Drive's page token); NULL before the first listing.
    feed_cursor text CHECK (pg_catalog.char_length(feed_cursor) OPERATOR(pg_catalog.<=) 8192),
    -- A full listing is under way: what it doesn't see again (seen < generation) is gone once it ends.
    listing boolean NOT NULL DEFAULT false,
    generation integer NOT NULL DEFAULT 0 CHECK (generation OPERATOR(pg_catalog.>=) 0),
    -- Refused sign-ins in a row (after a few the connector stops with a reason), and other failures in a row (each
    -- waits longer).
    auth_failures integer NOT NULL DEFAULT 0 CHECK (auth_failures OPERATOR(pg_catalog.>=) 0),
    failures integer NOT NULL DEFAULT 0 CHECK (failures OPERATOR(pg_catalog.>=) 0),
    next_sync_at timestamptz NOT NULL DEFAULT pg_catalog.now(),
    claimed_at timestamptz,
    last_sync_at timestamptz,
    -- When the last full re-read of every file's permissions began (a listing, or the daily sweep), and when the
    -- permissions were last all read again (shown to admins).
    swept_at timestamptz,
    permissions_checked_at timestamptz,
    -- Since when it has been stopped with an error, and when its documents were made admins only because of that.
    error_since timestamptz,
    locked_at timestamptz,
    run_as uuid NOT NULL DEFAULT row_bot_org.row_bot_principal()::pg_catalog.uuid,
    created_by uuid NOT NULL DEFAULT row_bot_org.row_bot_principal()::pg_catalog.uuid,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.now(),
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.now(),
    CHECK (kind OPERATOR(pg_catalog.=) 'google' OR (drive_id IS NOT NULL AND tenant_id IS NOT NULL)),
    CHECK (kind OPERATOR(pg_catalog.=) 'microsoft' OR drive_id IS NOT NULL OR folder_id IS NOT NULL),
    UNIQUE (id, space_id)
);
CREATE INDEX connectors_due ON row_bot_org.connectors (next_sync_at) WHERE state OPERATOR(pg_catalog.=) 'active';

CREATE TABLE row_bot_org.connector_items (
    connector_id uuid NOT NULL REFERENCES row_bot_org.connectors (id) ON DELETE CASCADE,
    upstream_id text NOT NULL CHECK (pg_catalog.char_length(upstream_id) OPERATOR(pg_catalog.>=) 1
                                     AND pg_catalog.char_length(upstream_id) OPERATOR(pg_catalog.<=) 512),
    -- NULL for the root.
    parent_id text CHECK (pg_catalog.char_length(parent_id) OPERATOR(pg_catalog.<=) 512),
    is_folder boolean NOT NULL,
    name text NOT NULL CHECK (pg_catalog.char_length(name) OPERATOR(pg_catalog.<=) 1024),
    media_type text CHECK (pg_catalog.char_length(media_type) OPERATOR(pg_catalog.<=) 200),
    size bigint,
    -- The content's version upstream (Graph's cTag, Drive's md5Checksum or modifiedTime), and the one the document
    -- holds (or that was last decided on: a file skipped, or that couldn't be fetched).
    version text CHECK (pg_catalog.char_length(version) OPERATOR(pg_catalog.<=) 512),
    synced_version text CHECK (pg_catalog.char_length(synced_version) OPERATOR(pg_catalog.<=) 512),
    -- Folders: whether their children were listed, and where a listing of them stopped.
    listed boolean NOT NULL DEFAULT true,
    list_page text CHECK (pg_catalog.char_length(list_page) OPERATOR(pg_catalog.<=) 8192),
    -- The listing (connectors.generation) that last saw it.
    seen integer NOT NULL DEFAULT 0,
    -- Failed reads of this file in a row, and when it is tried again (after a few it is skipped).
    failures integer NOT NULL DEFAULT 0 CHECK (failures OPERATOR(pg_catalog.>=) 0),
    retry_at timestamptz,
    content_due boolean NOT NULL DEFAULT false,
    readers_due boolean NOT NULL DEFAULT false,
    document_id uuid REFERENCES row_bot_org.brain_documents (id) ON DELETE SET NULL,
    readers text[] CHECK (pg_catalog.cardinality(readers) OPERATOR(pg_catalog.<=) 1000),
    -- The upstream principals it is shared with that nothing maps ([{"key", "name"}]): while any is, only admins read
    -- the file.
    unmapped jsonb NOT NULL DEFAULT '[]' CHECK (pg_catalog.jsonb_typeof(unmapped) OPERATOR(pg_catalog.=) 'array'),
    -- Why it isn't a document (its type, its size, it couldn't be fetched); NULL when it is one or is still to read.
    skipped text CHECK (pg_catalog.char_length(skipped) OPERATOR(pg_catalog.<=) 300),
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.now(),
    PRIMARY KEY (connector_id, upstream_id)
);
CREATE INDEX connector_items_parent ON row_bot_org.connector_items (connector_id, parent_id);
CREATE INDEX connector_items_due ON row_bot_org.connector_items (connector_id)
    WHERE content_due OR readers_due OR NOT listed;
CREATE INDEX connector_items_document ON row_bot_org.connector_items (document_id) WHERE document_id IS NOT NULL;

ALTER TABLE row_bot_org.brain_documents
    ADD COLUMN connector_id uuid,
    ADD COLUMN readers text[] CHECK (pg_catalog.cardinality(readers) OPERATOR(pg_catalog.<=) 1000),
    ADD CONSTRAINT brain_documents_connector_readers CHECK ((connector_id IS NULL) OPERATOR(pg_catalog.=) (readers IS NULL)),
    ADD FOREIGN KEY (connector_id, space_id) REFERENCES row_bot_org.connectors (id, space_id) ON DELETE CASCADE;
CREATE INDEX brain_documents_connector ON row_bot_org.brain_documents (connector_id) WHERE connector_id IS NOT NULL;

-- An Entra principal's object id (the ID token's oid), which Graph names people by.
ALTER TABLE row_bot_org.principals
    ADD COLUMN entra_oid text CHECK (entra_oid OPERATOR(pg_catalog.~) '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$');

-- 0002's sign_in, with the Entra object id (NULL for other providers) stored with the principal.
DROP FUNCTION row_bot_org.sign_in(text, text, text, text, text[], bytea, interval, interval, text, text, bytea);
CREATE FUNCTION row_bot_org.sign_in(p_issuer text, p_subject text, p_display_name text, p_email text,
                                    p_groups text[], p_verifier bytea, p_lifetime interval, p_idle interval,
                                    p_user_agent text, p_address text, p_replaces bytea, p_entra_oid text)
    RETURNS TABLE (principal_id uuid, status text)
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER
    SET search_path = pg_catalog, pg_temp
    AS $$
DECLARE
    found_id uuid;
    found_status text;
BEGIN
    IF p_replaces IS NOT NULL THEN
        UPDATE row_bot_org.server_sessions s SET revoked_at = pg_catalog.now()
        WHERE s.verifier OPERATOR(pg_catalog.=) p_replaces AND s.revoked_at IS NULL;
    END IF;
    INSERT INTO row_bot_org.principals AS p (issuer, subject, display_name, email, entra_oid)
    VALUES (p_issuer, p_subject, p_display_name, p_email, p_entra_oid)
    ON CONFLICT (issuer, subject) DO UPDATE
        SET display_name = EXCLUDED.display_name, email = EXCLUDED.email, entra_oid = EXCLUDED.entra_oid,
            last_sign_in_at = pg_catalog.now()
    RETURNING p.id, p.status INTO found_id, found_status;
    IF found_status OPERATOR(pg_catalog.=) 'active' THEN
        DELETE FROM row_bot_org.principal_groups g WHERE g.principal_id OPERATOR(pg_catalog.=) found_id;
        INSERT INTO row_bot_org.principal_groups (principal_id, name)
        SELECT DISTINCT found_id, g FROM pg_catalog.unnest(p_groups) AS g;
        INSERT INTO row_bot_org.server_sessions
            (verifier, principal_id, expires_at, idle_timeout, idle_expires_at, user_agent, address)
        VALUES (p_verifier, found_id, pg_catalog.now() OPERATOR(pg_catalog.+) p_lifetime, p_idle,
                pg_catalog.now() OPERATOR(pg_catalog.+) LEAST(p_idle, p_lifetime),
                pg_catalog.left(p_user_agent, 200), pg_catalog.left(p_address, 64));
    END IF;
    RETURN QUERY SELECT found_id, found_status;
END
$$;
REVOKE ALL ON FUNCTION row_bot_org.sign_in(text, text, text, text, text[], bytea, interval, interval, text, text, bytea,
                                           text) FROM PUBLIC;

-- Whether the transaction may read a document with these readers: NULL (an upload: the space decides), an admin's
-- transaction, the worker, or a principal listed by its email (as signed in, lower case), its Entra object id or one
-- of its groups.
CREATE FUNCTION row_bot_org.brain_reads_document(p_readers text[]) RETURNS boolean
    LANGUAGE sql STABLE PARALLEL SAFE
    AS $$
        SELECT p_readers IS NULL
            OR row_bot_org.row_bot_is_admin()
            OR row_bot_org.row_bot_is_brain_worker()
            OR (row_bot_org.row_bot_principal() IS NOT NULL
                AND (EXISTS (SELECT 1 FROM pg_catalog.unnest(p_readers) AS r (entry)
                             WHERE pg_catalog.starts_with(r.entry, 'g:')
                               AND row_bot_org.row_bot_in_group(pg_catalog.substr(r.entry, 3)))
                     OR EXISTS (SELECT 1 FROM row_bot_org.principals p
                                WHERE p.id OPERATOR(pg_catalog.=) row_bot_org.row_bot_principal()::pg_catalog.uuid
                                  AND ((p.email IS NOT NULL
                                        AND ('e:'::pg_catalog.text OPERATOR(pg_catalog.||) pg_catalog.lower(p.email))
                                            OPERATOR(pg_catalog.=) ANY (p_readers))
                                       OR (p.entra_oid IS NOT NULL
                                           AND ('o:'::pg_catalog.text OPERATOR(pg_catalog.||) p.entra_oid)
                                               OPERATOR(pg_catalog.=) ANY (p_readers))))))
    $$;

-- Only an admin's transaction gives a document readers or a connector, changes them, or changes a connector
-- document's file; a document never moves to another connector (or out of one). The worker may only make a connector
-- document admins only (a connector stopped too long, or whose admin isn't one any more).
CREATE FUNCTION row_bot_org.brain_documents_keep_readers() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog, pg_temp
    AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.connector_id IS NULL AND NEW.readers IS NULL THEN
            RETURN NEW;
        END IF;
    ELSIF NEW.connector_id IS DISTINCT FROM OLD.connector_id THEN
        RAISE EXCEPTION 'a document keeps its connector' USING ERRCODE = 'insufficient_privilege';
    ELSIF NEW.readers IS NOT DISTINCT FROM OLD.readers
          AND (OLD.connector_id IS NULL OR NEW.content IS NOT DISTINCT FROM OLD.content) THEN
        RETURN NEW;
    ELSIF row_bot_org.row_bot_is_brain_worker() AND OLD.connector_id IS NOT NULL
          AND NEW.readers OPERATOR(pg_catalog.=) '{}'::text[] AND NEW.content IS NOT DISTINCT FROM OLD.content THEN
        RETURN NEW;
    END IF;
    IF NOT row_bot_org.row_bot_is_admin() THEN
        RAISE EXCEPTION 'only an admin''s transaction sets who reads a document, or changes a connector''s file'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER brain_documents_readers BEFORE INSERT OR UPDATE OF connector_id, readers, content
    ON row_bot_org.brain_documents FOR EACH ROW EXECUTE FUNCTION row_bot_org.brain_documents_keep_readers();

-- A connector document is deleted only in an admin's transaction (its connector's sync, the connector or its space
-- deleted), and the facts citing it go with it: through the key's ON DELETE SET NULL they would lose their source
-- and be read by everyone who reads the space.
CREATE FUNCTION row_bot_org.brain_documents_take_facts() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog, pg_temp
    AS $$
BEGIN
    IF NOT row_bot_org.row_bot_is_admin() THEN
        RAISE EXCEPTION 'only an admin''s transaction deletes a connector''s document'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    DELETE FROM row_bot_org.brain_facts f WHERE f.source_document_id OPERATOR(pg_catalog.=) OLD.id;
    RETURN OLD;
END
$$;
CREATE TRIGGER brain_documents_facts BEFORE DELETE ON row_bot_org.brain_documents
    FOR EACH ROW WHEN (OLD.connector_id IS NOT NULL) EXECUTE FUNCTION row_bot_org.brain_documents_take_facts();

-- The worker changes only a connector's sync state: its claim, its waits and failures, and stopping it (active to
-- error, with a reason, and when its documents were made admins only). Everything else is an admin's.
CREATE FUNCTION row_bot_org.connectors_worker_columns() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog, pg_temp
    AS $$
BEGIN
    IF row_bot_org.row_bot_is_brain_worker()
       AND ((NEW.id, NEW.kind, NEW.space_id, NEW.name, NEW.drive_id, NEW.folder_id, NEW.tenant_id, NEW.client_id,
             NEW.subject, NEW.secret_sealed, NEW.principal_map, NEW.root_id, NEW.feed_cursor, NEW.listing,
             NEW.generation, NEW.auth_failures, NEW.last_sync_at, NEW.swept_at, NEW.permissions_checked_at,
             NEW.run_as, NEW.created_by, NEW.created_at, NEW.updated_at)
            IS DISTINCT FROM
            (OLD.id, OLD.kind, OLD.space_id, OLD.name, OLD.drive_id, OLD.folder_id, OLD.tenant_id, OLD.client_id,
             OLD.subject, OLD.secret_sealed, OLD.principal_map, OLD.root_id, OLD.feed_cursor, OLD.listing,
             OLD.generation, OLD.auth_failures, OLD.last_sync_at, OLD.swept_at, OLD.permissions_checked_at,
             OLD.run_as, OLD.created_by, OLD.created_at, OLD.updated_at)
            OR (NEW.state IS DISTINCT FROM OLD.state
                AND NOT (OLD.state OPERATOR(pg_catalog.=) 'active' AND NEW.state OPERATOR(pg_catalog.=) 'error'))) THEN
        RAISE EXCEPTION 'the brain worker changes only a connector''s sync state'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER connectors_worker_columns BEFORE UPDATE ON row_bot_org.connectors
    FOR EACH ROW EXECUTE FUNCTION row_bot_org.connectors_worker_columns();

ALTER TABLE row_bot_org.connectors ENABLE ROW LEVEL SECURITY;
ALTER TABLE row_bot_org.connectors FORCE ROW LEVEL SECURITY;
ALTER TABLE row_bot_org.connector_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE row_bot_org.connector_items FORCE ROW LEVEL SECURITY;

-- The migrating role (operators) reads everything.
CREATE POLICY connectors_definer ON row_bot_org.connectors FOR SELECT TO CURRENT_USER USING (true);
CREATE POLICY connector_items_definer ON row_bot_org.connector_items FOR SELECT TO CURRENT_USER USING (true);

-- Connectors and their items: admins; the worker reads connectors and records how their syncs went.
CREATE POLICY connectors_admin ON row_bot_org.connectors
    USING (row_bot_org.row_bot_is_admin()) WITH CHECK (row_bot_org.row_bot_is_admin());
CREATE POLICY connectors_worker_read ON row_bot_org.connectors FOR SELECT
    USING (row_bot_org.row_bot_is_brain_worker());
CREATE POLICY connectors_worker_update ON row_bot_org.connectors FOR UPDATE
    USING (row_bot_org.row_bot_is_brain_worker()) WITH CHECK (row_bot_org.row_bot_is_brain_worker());
CREATE POLICY connector_items_admin ON row_bot_org.connector_items
    USING (row_bot_org.row_bot_is_admin()) WITH CHECK (row_bot_org.row_bot_is_admin());

-- Mirrored permissions, on every read (restrictive: each holds as well as the space's own policies). A document is
-- read only by its readers; a chunk only with its document; a fact that cites a document only with it; a mention only
-- with its chunk or fact.
CREATE POLICY brain_documents_readers ON row_bot_org.brain_documents AS RESTRICTIVE FOR SELECT
    USING (row_bot_org.brain_reads_document(readers));
CREATE POLICY brain_chunks_readers ON row_bot_org.brain_chunks AS RESTRICTIVE FOR SELECT
    USING (EXISTS (SELECT 1 FROM row_bot_org.brain_documents d
                   WHERE d.id OPERATOR(pg_catalog.=) brain_chunks.document_id));
CREATE POLICY brain_facts_readers ON row_bot_org.brain_facts AS RESTRICTIVE FOR SELECT
    USING (source_document_id IS NULL
           OR EXISTS (SELECT 1 FROM row_bot_org.brain_documents d
                      WHERE d.id OPERATOR(pg_catalog.=) brain_facts.source_document_id));
CREATE POLICY brain_mentions_readers ON row_bot_org.brain_mentions AS RESTRICTIVE FOR SELECT
    USING ((chunk_id IS NULL
            OR EXISTS (SELECT 1 FROM row_bot_org.brain_chunks c WHERE c.id OPERATOR(pg_catalog.=) brain_mentions.chunk_id))
           AND (fact_id IS NULL
                OR EXISTS (SELECT 1 FROM row_bot_org.brain_facts f
                           WHERE f.id OPERATOR(pg_catalog.=) brain_mentions.fact_id)));

CALL row_bot_org.grant_runtime_access('row_bot_org.connectors');
CALL row_bot_org.grant_runtime_access('row_bot_org.connector_items');

REVOKE ALL ON FUNCTION row_bot_org.brain_reads_document(text[]) FROM PUBLIC;
REVOKE ALL ON FUNCTION row_bot_org.brain_documents_keep_readers() FROM PUBLIC;
REVOKE ALL ON FUNCTION row_bot_org.brain_documents_take_facts() FROM PUBLIC;
REVOKE ALL ON FUNCTION row_bot_org.connectors_worker_columns() FROM PUBLIC;
DO $$
BEGIN
    EXECUTE pg_catalog.format('GRANT EXECUTE ON FUNCTION row_bot_org.brain_reads_document(text[]), '
                              'row_bot_org.sign_in(text, text, text, text, text[], bytea, interval, interval, text, '
                              'text, bytea, text) TO %I', row_bot_org.runtime_role());
END
$$;
