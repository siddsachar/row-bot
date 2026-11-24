-- 0011: company connectors (B2): a SharePoint or OneDrive drive or folder (Microsoft Graph) or a Google Drive shared
-- drive or folder, synced into a space as documents, each with its file's upstream permissions mirrored. Run by the
-- migrating role, which owns all of it.
--
-- * connectors: an admin's source for one space, with what it needs to sign in (the tenant and client id, or the
--   service account's email, in the clear; the client secret or the service account's private key sealed with the
--   server key by row_bot_org.connectors.secrets, never stored or shown in the clear), its upstream principals mapped
--   to Row-Bot groups by hand (principal_map), where its sync stands (a cursor, a full listing under way) and its state
--   (active, paused by an admin, or error with a plain reason). Admins only; the brain's worker (0008's flag) reads
--   them and records how a sync went, to take the ones that are due. run_as is the admin whose transactions sync it
--   (the last to save or resume it): its documents are uploaded, and their model calls metered, as theirs.
-- * connector_items: what the sync knows of each upstream file and folder in scope (only those): its parent, version,
--   whether its content or permissions are to be read again, the document it became and the readers mirrored for it,
--   or why it isn't a document. Admins only.
-- * brain_documents gains connector_id and readers. readers NULL: everyone who reads the space (an upload). Else only
--   the people (e:<email, lower case>) and groups (g:<Row-Bot group>) listed, an admin's transaction and the worker;
--   an empty list is admins only. A connector's documents always have a list and uploads never do (a CHECK), so a
--   connector document is never readable by more than its list. Restrictive policies hide such a document, its
--   chunks, the facts that cite it and any mention of those from everyone else, on every read. Only an admin's
--   transaction sets or changes readers or connector_id, or a connector document's file (a trigger).
-- * A connector's documents aren't read for the knowledge graph (row_bot_org.brain.graph_store): an entity belongs to
--   the whole space, so its name, description and relations would tell people who may not open a file what it says.
-- * No SECURITY DEFINER function. OPERATOR(...) forms all share one precedence, so every expression mixing two of
--   them is bracketed.

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

-- Whether the transaction may read a document with these readers: NULL (an upload: the space decides), an admin's
-- transaction, the worker, or a principal listed by its email (as signed in, lower case) or one of its groups.
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
                                  AND p.email IS NOT NULL
                                  AND ('e:'::pg_catalog.text OPERATOR(pg_catalog.||) pg_catalog.lower(p.email))
                                      OPERATOR(pg_catalog.=) ANY (p_readers))))
    $$;

-- Only an admin's transaction gives a document readers or a connector, changes them, or changes a connector
-- document's file; a document never moves to another connector (or out of one).
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
DO $$
BEGIN
    EXECUTE pg_catalog.format('GRANT EXECUTE ON FUNCTION row_bot_org.brain_reads_document(text[]) TO %I',
                              row_bot_org.runtime_role());
END
$$;
