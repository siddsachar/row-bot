-- 0009: the company knowledge graph (B7): entities and the relations between them, extracted from a space's
-- documents and facts, with mentions back to where each was found. Run by the migrating role, which owns all of it.
--
-- * brain_entities: a name (and its compared form, name_key), a type, a short description and aliases, in one
--   space. Unique on space, name_key and type: extraction adds to the one entity a name and type have (or, for a
--   name an owner renamed away, the one that keeps it as an alias: alias_keys). An entity
--   never crosses spaces (the same name in two spaces is two entities), so reading one is exactly reading its space.
--   It is active, retired, or merged_into another entity of its space (whose name then keeps leading there).
-- * brain_relations: from one entity to another of the same space, with a label from row_bot_org.brain.graph's
--   small vocabulary; extracted, or made by hand (origin), active or retired.
-- * brain_mentions: where an entity or relation was found: a chunk of a document of its space, or a fact of it.
--   Deleting a document deletes its chunks and so its mentions (the keys cascade); an entity or extracted relation
--   that loses its last mention that way is retired (retired_auto), and found again it is active again. One an
--   owner retired stays retired.
-- * Reading follows the space (the spaces policies, through EXISTS); curation (an UPDATE, a relation added or
--   removed, a mention moved by a merge) needs managing the space (brain_manages, 0008); the worker (0008's flag)
--   reads, adds and changes them. No SECURITY DEFINER function.
-- * brain_documents and brain_facts gain the graph's own state. 0008's author trigger now fires only when an
--   author column is written, so the worker's progress updates never turn a stored file into JSON.
-- * OPERATOR(...) forms all share one precedence, so every expression mixing two of them is bracketed.

ALTER TABLE row_bot_org.brain_chunks ADD UNIQUE (id, space_id);
ALTER TABLE row_bot_org.brain_facts ADD UNIQUE (id, space_id);

-- A document's graph: pending (to read), extracting (claimed), done, waiting (graph_reason says for what: a chat
-- model, or graph_retry_at), or failed. graph_version is the version whose mentions are stored, and graph_next
-- the next part to read, so a pass can stop anywhere and the next one carry on.
ALTER TABLE row_bot_org.brain_documents
    ADD COLUMN graph_status text NOT NULL DEFAULT 'pending'
        CHECK (graph_status IN ('pending', 'extracting', 'done', 'waiting', 'failed')),
    ADD COLUMN graph_reason text CHECK (pg_catalog.char_length(graph_reason) OPERATOR(pg_catalog.<=) 500),
    ADD COLUMN graph_version integer,
    ADD COLUMN graph_next integer NOT NULL DEFAULT 0 CHECK (graph_next OPERATOR(pg_catalog.>=) 0),
    ADD COLUMN graph_claimed_at timestamptz,
    ADD COLUMN graph_attempts integer NOT NULL DEFAULT 0 CHECK (graph_attempts OPERATOR(pg_catalog.>=) 0),
    ADD COLUMN graph_retry_at timestamptz;
CREATE INDEX brain_documents_graph_queue ON row_bot_org.brain_documents (uploaded_at)
    WHERE graph_status IN ('pending', 'extracting', 'waiting');

-- A fact's graph: read once (graph_done_at), again after an edit; graph_retry_at holds it back after a failed call.
ALTER TABLE row_bot_org.brain_facts
    ADD COLUMN graph_done_at timestamptz,
    ADD COLUMN graph_retry_at timestamptz;

DROP TRIGGER brain_documents_authors ON row_bot_org.brain_documents;
DROP TRIGGER brain_facts_authors ON row_bot_org.brain_facts;
CREATE TRIGGER brain_documents_authors BEFORE INSERT OR UPDATE OF uploaded_by, uploader_groups
    ON row_bot_org.brain_documents
    FOR EACH ROW EXECUTE FUNCTION row_bot_org.brain_keep_authors('uploaded_by', 'uploader_groups');
CREATE TRIGGER brain_facts_authors BEFORE INSERT OR UPDATE OF written_by, writer_groups ON row_bot_org.brain_facts
    FOR EACH ROW EXECUTE FUNCTION row_bot_org.brain_keep_authors('written_by', 'writer_groups');

CREATE TABLE row_bot_org.brain_entities (
    id uuid PRIMARY KEY DEFAULT pg_catalog.gen_random_uuid(),
    space_id uuid NOT NULL REFERENCES row_bot_org.spaces (id) ON DELETE CASCADE,
    name text NOT NULL CHECK (pg_catalog.char_length(name) OPERATOR(pg_catalog.>=) 1
                              AND pg_catalog.char_length(name) OPERATOR(pg_catalog.<=) 160),
    -- The name as compared (row_bot_org.brain.graph.name_key: case-folded, whitespace collapsed).
    name_key text NOT NULL CHECK (pg_catalog.char_length(name_key) OPERATOR(pg_catalog.>=) 1
                                  AND pg_catalog.char_length(name_key) OPERATOR(pg_catalog.<=) 160),
    entity_type text NOT NULL CHECK (entity_type OPERATOR(pg_catalog.~) '^[a-z_]{1,32}$'),
    description text NOT NULL DEFAULT '' CHECK (pg_catalog.char_length(description) OPERATOR(pg_catalog.<=) 1000),
    aliases text[] NOT NULL DEFAULT '{}' CHECK (pg_catalog.cardinality(aliases) OPERATOR(pg_catalog.<=) 16),
    -- The aliases as compared (name_key), so extraction finds a renamed entity by its old name.
    alias_keys text[] NOT NULL DEFAULT '{}',
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'merged_into', 'retired')),
    merged_into uuid,
    -- Retired because its last mention went (not by an owner): found again, it is active again.
    retired_auto boolean NOT NULL DEFAULT false,
    -- Who last curated it, and when (extraction isn't an edit).
    edited_by uuid,
    edited_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.now(),
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.now(),
    UNIQUE (space_id, name_key, entity_type),
    UNIQUE (id, space_id),
    CHECK ((status OPERATOR(pg_catalog.=) 'merged_into') OPERATOR(pg_catalog.=) (merged_into IS NOT NULL)),
    CHECK (merged_into IS DISTINCT FROM id),
    CHECK (pg_catalog.cardinality(alias_keys) OPERATOR(pg_catalog.=) pg_catalog.cardinality(aliases)),
    CHECK (NOT retired_auto OR status OPERATOR(pg_catalog.=) 'retired'),
    -- Entities go only with their space, all at once.
    FOREIGN KEY (merged_into, space_id) REFERENCES row_bot_org.brain_entities (id, space_id) ON DELETE CASCADE
);
CREATE INDEX brain_entities_space ON row_bot_org.brain_entities (space_id, status);
CREATE INDEX brain_entities_merged ON row_bot_org.brain_entities (merged_into) WHERE merged_into IS NOT NULL;
CREATE INDEX brain_entities_alias_keys ON row_bot_org.brain_entities USING gin (alias_keys);

CREATE TABLE row_bot_org.brain_relations (
    id uuid PRIMARY KEY DEFAULT pg_catalog.gen_random_uuid(),
    space_id uuid NOT NULL REFERENCES row_bot_org.spaces (id) ON DELETE CASCADE,
    source_id uuid NOT NULL,
    target_id uuid NOT NULL,
    relation_type text NOT NULL CHECK (relation_type OPERATOR(pg_catalog.~) '^[a-z_]{1,40}$'),
    origin text NOT NULL DEFAULT 'extracted' CHECK (origin IN ('extracted', 'manual')),
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'retired')),
    retired_auto boolean NOT NULL DEFAULT false,
    edited_by uuid,
    edited_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.now(),
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.now(),
    UNIQUE (space_id, source_id, target_id, relation_type),
    UNIQUE (id, space_id),
    CHECK (source_id OPERATOR(pg_catalog.<>) target_id),
    CHECK (NOT retired_auto OR (status OPERATOR(pg_catalog.=) 'retired'
                                AND origin OPERATOR(pg_catalog.=) 'extracted')),
    FOREIGN KEY (source_id, space_id) REFERENCES row_bot_org.brain_entities (id, space_id) ON DELETE CASCADE,
    FOREIGN KEY (target_id, space_id) REFERENCES row_bot_org.brain_entities (id, space_id) ON DELETE CASCADE
);
CREATE INDEX brain_relations_source ON row_bot_org.brain_relations (source_id);
CREATE INDEX brain_relations_target ON row_bot_org.brain_relations (target_id);

CREATE TABLE row_bot_org.brain_mentions (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    space_id uuid NOT NULL,
    entity_id uuid,
    relation_id uuid,
    chunk_id bigint,
    fact_id uuid,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.now(),
    CHECK ((entity_id IS NULL) OPERATOR(pg_catalog.<>) (relation_id IS NULL)),
    CHECK ((chunk_id IS NULL) OPERATOR(pg_catalog.<>) (fact_id IS NULL)),
    UNIQUE NULLS NOT DISTINCT (entity_id, relation_id, chunk_id, fact_id),
    FOREIGN KEY (entity_id, space_id) REFERENCES row_bot_org.brain_entities (id, space_id) ON DELETE CASCADE,
    FOREIGN KEY (relation_id, space_id) REFERENCES row_bot_org.brain_relations (id, space_id) ON DELETE CASCADE,
    FOREIGN KEY (chunk_id, space_id) REFERENCES row_bot_org.brain_chunks (id, space_id) ON DELETE CASCADE,
    FOREIGN KEY (fact_id, space_id) REFERENCES row_bot_org.brain_facts (id, space_id) ON DELETE CASCADE
);
CREATE INDEX brain_mentions_relation ON row_bot_org.brain_mentions (relation_id) WHERE relation_id IS NOT NULL;
CREATE INDEX brain_mentions_chunk ON row_bot_org.brain_mentions (chunk_id) WHERE chunk_id IS NOT NULL;
CREATE INDEX brain_mentions_fact ON row_bot_org.brain_mentions (fact_id) WHERE fact_id IS NOT NULL;

ALTER TABLE row_bot_org.brain_entities ENABLE ROW LEVEL SECURITY;
ALTER TABLE row_bot_org.brain_entities FORCE ROW LEVEL SECURITY;
ALTER TABLE row_bot_org.brain_relations ENABLE ROW LEVEL SECURITY;
ALTER TABLE row_bot_org.brain_relations FORCE ROW LEVEL SECURITY;
ALTER TABLE row_bot_org.brain_mentions ENABLE ROW LEVEL SECURITY;
ALTER TABLE row_bot_org.brain_mentions FORCE ROW LEVEL SECURITY;

-- The migrating role (operators) reads everything.
CREATE POLICY brain_entities_definer ON row_bot_org.brain_entities FOR SELECT TO CURRENT_USER USING (true);
CREATE POLICY brain_relations_definer ON row_bot_org.brain_relations FOR SELECT TO CURRENT_USER USING (true);
CREATE POLICY brain_mentions_definer ON row_bot_org.brain_mentions FOR SELECT TO CURRENT_USER USING (true);

-- Entities: read with their space; curated by a manager; found by the worker.
CREATE POLICY brain_entities_read ON row_bot_org.brain_entities FOR SELECT
    USING (EXISTS (SELECT 1 FROM row_bot_org.spaces s
                   WHERE s.id OPERATOR(pg_catalog.=) brain_entities.space_id));
CREATE POLICY brain_entities_curate ON row_bot_org.brain_entities FOR UPDATE
    USING (row_bot_org.brain_manages(space_id)) WITH CHECK (row_bot_org.brain_manages(space_id));
CREATE POLICY brain_entities_worker_read ON row_bot_org.brain_entities FOR SELECT
    USING (row_bot_org.row_bot_is_brain_worker());
CREATE POLICY brain_entities_worker_add ON row_bot_org.brain_entities FOR INSERT
    WITH CHECK (row_bot_org.row_bot_is_brain_worker());
CREATE POLICY brain_entities_worker_update ON row_bot_org.brain_entities FOR UPDATE
    USING (row_bot_org.row_bot_is_brain_worker()) WITH CHECK (row_bot_org.row_bot_is_brain_worker());

-- Relations: read with their space; added by hand, curated and (a merge's duplicates) removed by a manager; found
-- by the worker.
CREATE POLICY brain_relations_read ON row_bot_org.brain_relations FOR SELECT
    USING (EXISTS (SELECT 1 FROM row_bot_org.spaces s
                   WHERE s.id OPERATOR(pg_catalog.=) brain_relations.space_id));
CREATE POLICY brain_relations_add ON row_bot_org.brain_relations FOR INSERT
    WITH CHECK (row_bot_org.brain_manages(space_id));
CREATE POLICY brain_relations_curate ON row_bot_org.brain_relations FOR UPDATE
    USING (row_bot_org.brain_manages(space_id)) WITH CHECK (row_bot_org.brain_manages(space_id));
CREATE POLICY brain_relations_remove ON row_bot_org.brain_relations FOR DELETE
    USING (row_bot_org.brain_manages(space_id));
CREATE POLICY brain_relations_worker_read ON row_bot_org.brain_relations FOR SELECT
    USING (row_bot_org.row_bot_is_brain_worker());
CREATE POLICY brain_relations_worker_add ON row_bot_org.brain_relations FOR INSERT
    WITH CHECK (row_bot_org.row_bot_is_brain_worker());
CREATE POLICY brain_relations_worker_update ON row_bot_org.brain_relations FOR UPDATE
    USING (row_bot_org.row_bot_is_brain_worker()) WITH CHECK (row_bot_org.row_bot_is_brain_worker());

-- Mentions: read with their space; moved (a merge) and removed (a fact edited, a merge's duplicates) by a manager;
-- added and cleared by the worker.
CREATE POLICY brain_mentions_read ON row_bot_org.brain_mentions FOR SELECT
    USING (EXISTS (SELECT 1 FROM row_bot_org.spaces s
                   WHERE s.id OPERATOR(pg_catalog.=) brain_mentions.space_id));
CREATE POLICY brain_mentions_move ON row_bot_org.brain_mentions FOR UPDATE
    USING (row_bot_org.brain_manages(space_id)) WITH CHECK (row_bot_org.brain_manages(space_id));
CREATE POLICY brain_mentions_remove ON row_bot_org.brain_mentions FOR DELETE
    USING (row_bot_org.brain_manages(space_id));
CREATE POLICY brain_mentions_worker_read ON row_bot_org.brain_mentions FOR SELECT
    USING (row_bot_org.row_bot_is_brain_worker());
CREATE POLICY brain_mentions_worker_add ON row_bot_org.brain_mentions FOR INSERT
    WITH CHECK (row_bot_org.row_bot_is_brain_worker());
CREATE POLICY brain_mentions_worker_clear ON row_bot_org.brain_mentions FOR DELETE
    USING (row_bot_org.row_bot_is_brain_worker());

CALL row_bot_org.grant_runtime_access('row_bot_org.brain_entities');
CALL row_bot_org.grant_runtime_access('row_bot_org.brain_relations');
CALL row_bot_org.grant_runtime_access('row_bot_org.brain_mentions');
