-- 0008: the company brain v1 (B1a): spaces, company documents with their chunks, and facts. Run by the
-- migrating role, which owns all of it.
--
-- * spaces: the one company space (made here; everyone signed in reads it) and group spaces, read by the groups
--   in reader_groups. Each has owners, principals or groups, who also read it. Admins make, change and delete
--   spaces; owners rename theirs and manage its documents and facts. A trigger keeps the rest an admin's: only an
--   admin's transaction changes who reads or owns a space, nobody changes its kind, and the company space stays.
-- * brain_documents: an uploaded file (kept here, in content: the server's image has a read-only root and no data
--   volume but Postgres), its processing status, and a version that a replace bumps. brain_chunks: its text in
--   parts, each with a tsvector and, once embedded, a vector (any dimension: the model and dimension are stored
--   with it and checked at query time). brain_facts: short statements owners write, retired rather than lost.
-- * Reading a document, chunk or fact needs reading its space (the spaces policies, through the EXISTS below);
--   writing one needs managing it (brain_manages: an admin's transaction, or an owner of the space). Recall is
--   just reading: row-level security decides what it can find.
-- * The worker (row_bot_org.brain) processes the queue with no principal at all: row_bot_is_brain_worker() is
--   true only in a transaction that names no principal and sets row_bot.brain_worker. It reads and updates
--   documents and facts, writes chunks and sees which embeddings model is the company's default; nothing else.
--   Like row_bot.admin it guards against application code that forgets a check, not against SQL the runtime role
--   runs on its own. No SECURITY DEFINER function: no lookup happens before a principal exists.
-- * OPERATOR(...) forms all share one precedence, so every expression mixing two of them is bracketed.

CREATE FUNCTION row_bot_org.row_bot_is_brain_worker() RETURNS boolean
    LANGUAGE sql STABLE PARALLEL SAFE
    AS $$
        SELECT row_bot_org.row_bot_principal() IS NULL
           AND COALESCE(pg_catalog.current_setting('row_bot.brain_worker', true) OPERATOR(pg_catalog.=) 'on', false)
    $$;

-- Whether the transaction's principal owns a space with these owners (a principal, or one of its groups).
CREATE FUNCTION row_bot_org.brain_owns(p_owner_principals uuid[], p_owner_groups text[]) RETURNS boolean
    LANGUAGE sql STABLE PARALLEL SAFE
    AS $$
        SELECT row_bot_org.row_bot_principal() IS NOT NULL
           AND (row_bot_org.row_bot_principal()::pg_catalog.uuid OPERATOR(pg_catalog.=) ANY (p_owner_principals)
                OR EXISTS (SELECT 1 FROM pg_catalog.unnest(p_owner_groups) AS g (name)
                           WHERE row_bot_org.row_bot_in_group(g.name)))
    $$;

-- Whether the transaction's principal reads a space: the company space, a group space of one of its groups, or
-- one it owns.
CREATE FUNCTION row_bot_org.brain_reads(p_kind text, p_reader_groups text[], p_owner_principals uuid[],
                                        p_owner_groups text[]) RETURNS boolean
    LANGUAGE sql STABLE PARALLEL SAFE
    AS $$
        SELECT row_bot_org.row_bot_principal() IS NOT NULL
           AND (p_kind OPERATOR(pg_catalog.=) 'company'
                OR EXISTS (SELECT 1 FROM pg_catalog.unnest(p_reader_groups) AS g (name)
                           WHERE row_bot_org.row_bot_in_group(g.name))
                OR row_bot_org.brain_owns(p_owner_principals, p_owner_groups))
    $$;

CREATE TABLE row_bot_org.spaces (
    id uuid PRIMARY KEY DEFAULT pg_catalog.gen_random_uuid(),
    name text NOT NULL CHECK (pg_catalog.char_length(name) OPERATOR(pg_catalog.>=) 1
                              AND pg_catalog.char_length(name) OPERATOR(pg_catalog.<=) 160),
    kind text NOT NULL CHECK (kind IN ('company', 'group')),
    -- The groups that read a group space (the company space is read by everyone signed in: none here).
    reader_groups text[] NOT NULL DEFAULT '{}'
        CHECK (pg_catalog.cardinality(reader_groups) OPERATOR(pg_catalog.<=) 64),
    owner_groups text[] NOT NULL DEFAULT '{}'
        CHECK (pg_catalog.cardinality(owner_groups) OPERATOR(pg_catalog.<=) 64),
    owner_principals uuid[] NOT NULL DEFAULT '{}'
        CHECK (pg_catalog.cardinality(owner_principals) OPERATOR(pg_catalog.<=) 64),
    -- NULL for the company space, which this migration makes.
    created_by uuid DEFAULT row_bot_org.row_bot_principal()::pg_catalog.uuid,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.now(),
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.now(),
    CHECK (kind OPERATOR(pg_catalog.=) 'group'
           OR pg_catalog.cardinality(reader_groups) OPERATOR(pg_catalog.=) 0)
);
CREATE UNIQUE INDEX spaces_one_company ON row_bot_org.spaces (kind) WHERE kind OPERATOR(pg_catalog.=) 'company';
-- Names aren't unique: a refused name would tell its writer that a space they can't read exists. Spaces are told
-- apart by id and kind.

-- The one company space, before row-level security is on (the migrating role has no policy to add rows).
INSERT INTO row_bot_org.spaces (name, kind) VALUES ('Company', 'company');

CREATE TABLE row_bot_org.brain_documents (
    id uuid PRIMARY KEY DEFAULT pg_catalog.gen_random_uuid(),
    space_id uuid NOT NULL REFERENCES row_bot_org.spaces (id) ON DELETE CASCADE,
    title text NOT NULL CHECK (pg_catalog.char_length(title) OPERATOR(pg_catalog.>=) 1
                               AND pg_catalog.char_length(title) OPERATOR(pg_catalog.<=) 300),
    file_name text NOT NULL CHECK (pg_catalog.char_length(file_name) OPERATOR(pg_catalog.>=) 1
                                   AND pg_catalog.char_length(file_name) OPERATOR(pg_catalog.<=) 255),
    media_type text NOT NULL CHECK (pg_catalog.char_length(media_type) OPERATOR(pg_catalog.<=) 200),
    byte_count bigint NOT NULL CHECK (byte_count OPERATOR(pg_catalog.>=) 0),
    sha256 text NOT NULL CHECK (sha256 OPERATOR(pg_catalog.~) '^[0-9a-f]{64}$'),
    content bytea NOT NULL,
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'processing', 'ready', 'failed')),
    -- Why it is failed, or still queued ("Add an embeddings model in Models").
    status_reason text CHECK (pg_catalog.char_length(status_reason) OPERATOR(pg_catalog.<=) 500),
    -- A replace bumps it; the worker writes its results only for the version it read.
    version integer NOT NULL DEFAULT 1 CHECK (version OPERATOR(pg_catalog.>=) 1),
    -- The version whose chunks are stored (NULL: none yet).
    chunked_version integer,
    claimed_at timestamptz,
    -- Claims since the document was last finished: a file that keeps stopping its worker is failed, not retried.
    claim_attempts integer NOT NULL DEFAULT 0 CHECK (claim_attempts OPERATOR(pg_catalog.>=) 0),
    uploaded_by uuid NOT NULL DEFAULT row_bot_org.row_bot_principal()::pg_catalog.uuid,
    -- The uploader's groups at the upload: what its embeddings' usage counts by, as a gateway call's does.
    uploader_groups text[] NOT NULL DEFAULT '{}',
    uploaded_at timestamptz NOT NULL DEFAULT pg_catalog.now(),
    created_at timestamptz NOT NULL DEFAULT pg_catalog.now(),
    processed_at timestamptz,
    UNIQUE (id, space_id)
);
CREATE INDEX brain_documents_space ON row_bot_org.brain_documents (space_id, uploaded_at);
CREATE INDEX brain_documents_queue ON row_bot_org.brain_documents (uploaded_at)
    WHERE status IN ('queued', 'processing');

CREATE TABLE row_bot_org.brain_chunks (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id uuid NOT NULL,
    -- The document's space, copied so reading a chunk checks its space directly (the key keeps them equal).
    space_id uuid NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal OPERATOR(pg_catalog.>=) 0),
    body text NOT NULL CHECK (pg_catalog.char_length(body) OPERATOR(pg_catalog.<=) 4000),
    tsv tsvector GENERATED ALWAYS AS (pg_catalog.to_tsvector('pg_catalog.english'::pg_catalog.regconfig, body)) STORED,
    embedding vector,
    embedding_model text,
    embedding_dim integer,
    CHECK ((embedding IS NULL) OPERATOR(pg_catalog.=) (embedding_model IS NULL)
           AND (embedding IS NULL) OPERATOR(pg_catalog.=) (embedding_dim IS NULL)),
    CHECK (embedding IS NULL OR vector_dims(embedding) OPERATOR(pg_catalog.=) embedding_dim),
    UNIQUE (document_id, ordinal),
    FOREIGN KEY (document_id, space_id) REFERENCES row_bot_org.brain_documents (id, space_id) ON DELETE CASCADE
);
CREATE INDEX brain_chunks_space ON row_bot_org.brain_chunks (space_id);
CREATE INDEX brain_chunks_text ON row_bot_org.brain_chunks USING gin (tsv);

CREATE TABLE row_bot_org.brain_facts (
    id uuid PRIMARY KEY DEFAULT pg_catalog.gen_random_uuid(),
    space_id uuid NOT NULL REFERENCES row_bot_org.spaces (id) ON DELETE CASCADE,
    body text NOT NULL CHECK (pg_catalog.char_length(body) OPERATOR(pg_catalog.>=) 1
                              AND pg_catalog.char_length(body) OPERATOR(pg_catalog.<=) 2000),
    -- The document it comes from, in the same space; deleting the document leaves the fact without one.
    source_document_id uuid,
    written_by uuid NOT NULL DEFAULT row_bot_org.row_bot_principal()::pg_catalog.uuid,
    writer_groups text[] NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT pg_catalog.now(),
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.now(),
    retired_at timestamptz,
    tsv tsvector GENERATED ALWAYS AS (pg_catalog.to_tsvector('pg_catalog.english'::pg_catalog.regconfig, body)) STORED,
    embedding vector,
    embedding_model text,
    embedding_dim integer,
    CHECK ((embedding IS NULL) OPERATOR(pg_catalog.=) (embedding_model IS NULL)
           AND (embedding IS NULL) OPERATOR(pg_catalog.=) (embedding_dim IS NULL)),
    CHECK (embedding IS NULL OR vector_dims(embedding) OPERATOR(pg_catalog.=) embedding_dim),
    FOREIGN KEY (source_document_id, space_id) REFERENCES row_bot_org.brain_documents (id, space_id)
        ON DELETE SET NULL (source_document_id)
);
CREATE INDEX brain_facts_space ON row_bot_org.brain_facts (space_id, created_at);
CREATE INDEX brain_facts_text ON row_bot_org.brain_facts USING gin (tsv);
CREATE INDEX brain_facts_source ON row_bot_org.brain_facts (source_document_id);

-- Whether the transaction may manage the documents and facts of a space: an admin's, or an owner's.
CREATE FUNCTION row_bot_org.brain_manages(p_space uuid) RETURNS boolean
    LANGUAGE sql STABLE PARALLEL SAFE
    AS $$
        SELECT row_bot_org.row_bot_is_admin()
            OR EXISTS (SELECT 1 FROM row_bot_org.spaces s
                       WHERE s.id OPERATOR(pg_catalog.=) p_space
                         AND row_bot_org.brain_owns(s.owner_principals, s.owner_groups))
    $$;

-- Only an admin's transaction changes who reads or owns a space; nobody changes its kind or origin; the company
-- space is never deleted. (Permissive policies OR together, so an owner's rename policy alone would let an owner
-- rewrite the owners too.)
CREATE FUNCTION row_bot_org.brain_spaces_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog, pg_temp
    AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.kind = 'company' THEN
            RAISE EXCEPTION 'the company space stays' USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN OLD;
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id OR NEW.kind IS DISTINCT FROM OLD.kind
       OR NEW.created_by IS DISTINCT FROM OLD.created_by OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'a space keeps its kind and origin' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NOT row_bot_org.row_bot_is_admin()
       AND (NEW.reader_groups IS DISTINCT FROM OLD.reader_groups OR NEW.owner_groups IS DISTINCT FROM OLD.owner_groups
            OR NEW.owner_principals IS DISTINCT FROM OLD.owner_principals) THEN
        RAISE EXCEPTION 'only an admin changes who reads or owns a space' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END
$$;
REVOKE ALL ON FUNCTION row_bot_org.brain_spaces_guard() FROM PUBLIC;
CREATE TRIGGER brain_spaces_guard BEFORE UPDATE OR DELETE ON row_bot_org.spaces
    FOR EACH ROW EXECUTE FUNCTION row_bot_org.brain_spaces_guard();

-- A document is uploaded, and a fact written, in the name of the transaction's own principal and groups (what
-- its embeddings are metered to), and only a replace or an edit by that principal changes them. Its arguments
-- name the table's author and groups columns. The worker never changes them.
CREATE FUNCTION row_bot_org.brain_keep_authors() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog, pg_temp
    AS $$
DECLARE
    new_row jsonb := to_jsonb(NEW);
    author text := new_row ->> TG_ARGV[0];
    author_groups jsonb := new_row -> TG_ARGV[1];
    principal text := row_bot_org.row_bot_principal();
    groups jsonb := COALESCE(NULLIF(current_setting('row_bot.groups', true), '')::jsonb, '[]'::jsonb);
BEGIN
    IF TG_OP = 'UPDATE' AND author IS NOT DISTINCT FROM (to_jsonb(OLD) ->> TG_ARGV[0])
       AND author_groups = (to_jsonb(OLD) -> TG_ARGV[1]) THEN
        RETURN NEW;
    END IF;
    IF principal IS NULL OR author IS NULL OR author::uuid IS DISTINCT FROM principal::uuid
       OR NOT (author_groups @> groups AND groups @> author_groups) THEN
        RAISE EXCEPTION 'a document or fact is in the name of the transaction''s own principal and groups'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END
$$;
REVOKE ALL ON FUNCTION row_bot_org.brain_keep_authors() FROM PUBLIC;
CREATE TRIGGER brain_documents_authors BEFORE INSERT OR UPDATE ON row_bot_org.brain_documents
    FOR EACH ROW EXECUTE FUNCTION row_bot_org.brain_keep_authors('uploaded_by', 'uploader_groups');
CREATE TRIGGER brain_facts_authors BEFORE INSERT OR UPDATE ON row_bot_org.brain_facts
    FOR EACH ROW EXECUTE FUNCTION row_bot_org.brain_keep_authors('written_by', 'writer_groups');

ALTER TABLE row_bot_org.spaces ENABLE ROW LEVEL SECURITY;
ALTER TABLE row_bot_org.spaces FORCE ROW LEVEL SECURITY;
ALTER TABLE row_bot_org.brain_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE row_bot_org.brain_documents FORCE ROW LEVEL SECURITY;
ALTER TABLE row_bot_org.brain_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE row_bot_org.brain_chunks FORCE ROW LEVEL SECURITY;
ALTER TABLE row_bot_org.brain_facts ENABLE ROW LEVEL SECURITY;
ALTER TABLE row_bot_org.brain_facts FORCE ROW LEVEL SECURITY;

-- The migrating role (operators) reads everything.
CREATE POLICY spaces_definer ON row_bot_org.spaces FOR SELECT TO CURRENT_USER USING (true);
CREATE POLICY brain_documents_definer ON row_bot_org.brain_documents FOR SELECT TO CURRENT_USER USING (true);
CREATE POLICY brain_chunks_definer ON row_bot_org.brain_chunks FOR SELECT TO CURRENT_USER USING (true);
CREATE POLICY brain_facts_definer ON row_bot_org.brain_facts FOR SELECT TO CURRENT_USER USING (true);

-- Spaces: readers (and owners) read; owners rename (the trigger keeps the rest); admins do everything.
CREATE POLICY spaces_read ON row_bot_org.spaces FOR SELECT
    USING (row_bot_org.brain_reads(kind, reader_groups, owner_principals, owner_groups));
CREATE POLICY spaces_owner_rename ON row_bot_org.spaces FOR UPDATE
    USING (row_bot_org.brain_owns(owner_principals, owner_groups))
    WITH CHECK (row_bot_org.brain_owns(owner_principals, owner_groups));
CREATE POLICY spaces_admin ON row_bot_org.spaces
    USING (row_bot_org.row_bot_is_admin()) WITH CHECK (row_bot_org.row_bot_is_admin());

-- Documents: read with their space; added by a manager in their own name; changed and removed by a manager.
CREATE POLICY brain_documents_read ON row_bot_org.brain_documents FOR SELECT
    USING (EXISTS (SELECT 1 FROM row_bot_org.spaces s
                   WHERE s.id OPERATOR(pg_catalog.=) brain_documents.space_id));
CREATE POLICY brain_documents_add ON row_bot_org.brain_documents FOR INSERT
    WITH CHECK (row_bot_org.brain_manages(space_id)
                AND uploaded_by OPERATOR(pg_catalog.=) row_bot_org.row_bot_principal()::pg_catalog.uuid);
CREATE POLICY brain_documents_change ON row_bot_org.brain_documents FOR UPDATE
    USING (row_bot_org.brain_manages(space_id)) WITH CHECK (row_bot_org.brain_manages(space_id));
CREATE POLICY brain_documents_remove ON row_bot_org.brain_documents FOR DELETE
    USING (row_bot_org.brain_manages(space_id));
CREATE POLICY brain_documents_worker_read ON row_bot_org.brain_documents FOR SELECT
    USING (row_bot_org.row_bot_is_brain_worker());
CREATE POLICY brain_documents_worker_update ON row_bot_org.brain_documents FOR UPDATE
    USING (row_bot_org.row_bot_is_brain_worker()) WITH CHECK (row_bot_org.row_bot_is_brain_worker());

-- Chunks: read with their space; written by the worker; cleared by a manager when a document is replaced.
CREATE POLICY brain_chunks_read ON row_bot_org.brain_chunks FOR SELECT
    USING (EXISTS (SELECT 1 FROM row_bot_org.spaces s
                   WHERE s.id OPERATOR(pg_catalog.=) brain_chunks.space_id));
CREATE POLICY brain_chunks_clear ON row_bot_org.brain_chunks FOR DELETE
    USING (row_bot_org.brain_manages(space_id));
CREATE POLICY brain_chunks_worker ON row_bot_org.brain_chunks
    USING (row_bot_org.row_bot_is_brain_worker()) WITH CHECK (row_bot_org.row_bot_is_brain_worker());

-- Facts: as documents; the worker reads them and stores their embeddings.
CREATE POLICY brain_facts_read ON row_bot_org.brain_facts FOR SELECT
    USING (EXISTS (SELECT 1 FROM row_bot_org.spaces s
                   WHERE s.id OPERATOR(pg_catalog.=) brain_facts.space_id));
CREATE POLICY brain_facts_add ON row_bot_org.brain_facts FOR INSERT
    WITH CHECK (row_bot_org.brain_manages(space_id)
                AND written_by OPERATOR(pg_catalog.=) row_bot_org.row_bot_principal()::pg_catalog.uuid);
CREATE POLICY brain_facts_change ON row_bot_org.brain_facts FOR UPDATE
    USING (row_bot_org.brain_manages(space_id)) WITH CHECK (row_bot_org.brain_manages(space_id));
CREATE POLICY brain_facts_remove ON row_bot_org.brain_facts FOR DELETE
    USING (row_bot_org.brain_manages(space_id));
CREATE POLICY brain_facts_worker_read ON row_bot_org.brain_facts FOR SELECT
    USING (row_bot_org.row_bot_is_brain_worker());
CREATE POLICY brain_facts_worker_update ON row_bot_org.brain_facts FOR UPDATE
    USING (row_bot_org.row_bot_is_brain_worker()) WITH CHECK (row_bot_org.row_bot_is_brain_worker());

-- The worker sees the catalog's models, to know whether the company has a default embeddings model.
CREATE POLICY catalog_models_brain_worker ON row_bot_org.catalog_models FOR SELECT
    USING (row_bot_org.row_bot_is_brain_worker());

CALL row_bot_org.grant_runtime_access('row_bot_org.spaces');
CALL row_bot_org.grant_runtime_access('row_bot_org.brain_documents');
CALL row_bot_org.grant_runtime_access('row_bot_org.brain_chunks');
CALL row_bot_org.grant_runtime_access('row_bot_org.brain_facts');

REVOKE ALL ON FUNCTION row_bot_org.row_bot_is_brain_worker() FROM PUBLIC;
REVOKE ALL ON FUNCTION row_bot_org.brain_owns(uuid[], text[]) FROM PUBLIC;
REVOKE ALL ON FUNCTION row_bot_org.brain_reads(text, text[], uuid[], text[]) FROM PUBLIC;
REVOKE ALL ON FUNCTION row_bot_org.brain_manages(uuid) FROM PUBLIC;
DO $$
BEGIN
    EXECUTE pg_catalog.format('GRANT EXECUTE ON FUNCTION row_bot_org.row_bot_is_brain_worker(), '
                              'row_bot_org.brain_owns(uuid[], text[]), '
                              'row_bot_org.brain_reads(text, text[], uuid[], text[]), '
                              'row_bot_org.brain_manages(uuid) TO %I', row_bot_org.runtime_role());
END
$$;
