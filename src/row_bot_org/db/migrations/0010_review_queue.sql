-- 0010: the review queue (B4a): what people offer to a company space, waiting for its owners or an admin to
-- approve it (it becomes a fact of the space) or reject it. Run by the migrating role, which owns all of it.
--
-- * brain_submissions: a share (kind 'share'; 'skill' is reserved for the library's submissions) of a title, a
--   text and an optional note on where it came from, in one space, by its submitter (and their groups) when they
--   sent it. It is pending, then approved (fact_id names the fact it became), rejected (with a reason) or
--   withdrawn by its submitter. A decision is final; who decided and when stay with it.
-- * It holds what a person wrote in a chat, so before approval nobody but its submitter and the space's managers
--   (brain_manages, 0008: its owners, or an admin's transaction) ever reads it. Not the space's other readers, not
--   the owners of other spaces. A submitter reads their own whatever became of it.
-- * Submitting needs reading the space (the spaces policies, through EXISTS), in one's own name and groups
--   (brain_is_own_author, 0009). Only its submitter withdraws it, only a manager decides it, and only while it is
--   pending (the trigger below: permissive policies OR together, so a policy alone can't say who may make which
--   change). An approval may edit the title and text and move it to another space the reviewer manages.
-- * brain_facts gains the share it came from (source_submission_id) and who offered it (submitted_by), set only
--   when the fact is made, from a pending share of the reviewer's, with the share's own submitter; never changed
--   after. The fact itself is the reviewer's (written_by), as 0008 has it.
-- * No SECURITY DEFINER function. OPERATOR(...) forms all share one precedence, so every expression mixing two of
--   them is bracketed.

CREATE TABLE row_bot_org.brain_submissions (
    id uuid PRIMARY KEY DEFAULT pg_catalog.gen_random_uuid(),
    kind text NOT NULL DEFAULT 'share' CHECK (kind IN ('share', 'skill')),
    space_id uuid NOT NULL REFERENCES row_bot_org.spaces (id) ON DELETE CASCADE,
    title text NOT NULL CHECK (pg_catalog.char_length(title) OPERATOR(pg_catalog.>=) 1
                               AND pg_catalog.char_length(title) OPERATOR(pg_catalog.<=) 200),
    body text NOT NULL CHECK (pg_catalog.char_length(body) OPERATOR(pg_catalog.>=) 1
                              AND pg_catalog.char_length(body) OPERATOR(pg_catalog.<=) 8000),
    -- Where it came from, in the submitter's words ("From a chat on 12 Nov").
    source_note text CHECK (pg_catalog.char_length(source_note) OPERATOR(pg_catalog.>=) 1
                            AND pg_catalog.char_length(source_note) OPERATOR(pg_catalog.<=) 500),
    submitted_by uuid NOT NULL DEFAULT row_bot_org.row_bot_principal()::pg_catalog.uuid,
    submitter_groups text[] NOT NULL DEFAULT '{}',
    submitted_at timestamptz NOT NULL DEFAULT pg_catalog.now(),
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'withdrawn')),
    -- Who approved or rejected it (a withdrawal is its submitter's), when it was decided, and why it was rejected.
    reviewed_by uuid,
    decided_at timestamptz,
    reason text CHECK (pg_catalog.char_length(reason) OPERATOR(pg_catalog.>=) 1
                       AND pg_catalog.char_length(reason) OPERATOR(pg_catalog.<=) 500),
    -- The fact it became (NULL again if that fact is deleted).
    fact_id uuid UNIQUE REFERENCES row_bot_org.brain_facts (id) ON DELETE SET NULL,
    CHECK ((status OPERATOR(pg_catalog.=) 'pending') OPERATOR(pg_catalog.=) (decided_at IS NULL)),
    CHECK ((status IN ('approved', 'rejected')) OPERATOR(pg_catalog.=) (reviewed_by IS NOT NULL)),
    CHECK ((status OPERATOR(pg_catalog.=) 'rejected') OPERATOR(pg_catalog.=) (reason IS NOT NULL)),
    CHECK (status OPERATOR(pg_catalog.=) 'approved' OR fact_id IS NULL)
);
-- The queue (each space's pending, oldest first) and a person's own.
CREATE INDEX brain_submissions_space ON row_bot_org.brain_submissions (space_id, status, submitted_at);
CREATE INDEX brain_submissions_submitter ON row_bot_org.brain_submissions (submitted_by, status, submitted_at);

ALTER TABLE row_bot_org.brain_facts
    ADD COLUMN source_submission_id uuid REFERENCES row_bot_org.brain_submissions (id) ON DELETE SET NULL,
    ADD COLUMN submitted_by uuid;
CREATE UNIQUE INDEX brain_facts_source_submission ON row_bot_org.brain_facts (source_submission_id)
    WHERE source_submission_id IS NOT NULL;

-- Who may make which change to a submission (the policies let its submitter and its space's managers update it).
CREATE FUNCTION row_bot_org.brain_submissions_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog, pg_temp
    AS $$
DECLARE
    principal text := row_bot_org.row_bot_principal();
BEGIN
    -- The fact it became was deleted (the key's SET NULL): nothing else changes.
    IF NEW.fact_id IS NULL AND OLD.fact_id IS NOT NULL
       AND NEW.status = OLD.status AND NEW.space_id = OLD.space_id AND NEW.title = OLD.title
       AND NEW.body = OLD.body AND NEW.reviewed_by IS NOT DISTINCT FROM OLD.reviewed_by
       AND NEW.decided_at IS NOT DISTINCT FROM OLD.decided_at AND NEW.reason IS NOT DISTINCT FROM OLD.reason THEN
        RETURN NEW;
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id OR NEW.kind IS DISTINCT FROM OLD.kind
       OR NEW.submitted_by IS DISTINCT FROM OLD.submitted_by
       OR NEW.submitter_groups IS DISTINCT FROM OLD.submitter_groups
       OR NEW.submitted_at IS DISTINCT FROM OLD.submitted_at
       OR NEW.source_note IS DISTINCT FROM OLD.source_note THEN
        RAISE EXCEPTION 'a submission keeps who sent it, when and what it is' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.status <> 'pending' OR NEW.status = 'pending' THEN
        RAISE EXCEPTION 'a submission changes only by being decided or withdrawn, once'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.status = 'withdrawn' THEN
        IF principal IS NULL OR principal::uuid IS DISTINCT FROM OLD.submitted_by
           OR NEW.space_id IS DISTINCT FROM OLD.space_id OR NEW.title IS DISTINCT FROM OLD.title
           OR NEW.body IS DISTINCT FROM OLD.body THEN
            RAISE EXCEPTION 'only its submitter withdraws a submission, as it is'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN NEW;
    END IF;
    -- Approved or rejected: by a manager of its space (and of the space an approval moves it to), in their name.
    IF principal IS NULL OR NEW.reviewed_by IS DISTINCT FROM principal::uuid
       OR NOT row_bot_org.brain_manages(OLD.space_id) OR NOT row_bot_org.brain_manages(NEW.space_id) THEN
        RAISE EXCEPTION 'a manager of its space decides a submission, in their own name'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.status = 'rejected' AND (NEW.space_id IS DISTINCT FROM OLD.space_id OR NEW.title IS DISTINCT FROM OLD.title
                                    OR NEW.body IS DISTINCT FROM OLD.body) THEN
        RAISE EXCEPTION 'a rejected submission stays as it was sent' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.status = 'approved' AND NOT EXISTS (
            SELECT 1 FROM row_bot_org.brain_facts f
            WHERE f.id OPERATOR(pg_catalog.=) NEW.fact_id
              AND f.source_submission_id OPERATOR(pg_catalog.=) NEW.id
              AND f.space_id OPERATOR(pg_catalog.=) NEW.space_id) THEN
        RAISE EXCEPTION 'an approved submission names the fact made from it, in its space'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER brain_submissions_guard BEFORE UPDATE ON row_bot_org.brain_submissions
    FOR EACH ROW EXECUTE FUNCTION row_bot_org.brain_submissions_guard();

-- A fact names the share it came from only when it is made, from a pending share (of the fact's submitter) whose
-- space the transaction manages; it never names another one later (losing it, when the share goes, is allowed).
CREATE FUNCTION row_bot_org.brain_facts_keep_source() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog, pg_temp
    AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF NEW.submitted_by IS DISTINCT FROM OLD.submitted_by
           OR (NEW.source_submission_id IS DISTINCT FROM OLD.source_submission_id
               AND NEW.source_submission_id IS NOT NULL) THEN
            RAISE EXCEPTION 'a fact keeps the share it came from' USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.source_submission_id IS NULL AND NEW.submitted_by IS NULL THEN
        RETURN NEW;
    END IF;
    IF NOT EXISTS (
            SELECT 1 FROM row_bot_org.brain_submissions s
            WHERE s.id OPERATOR(pg_catalog.=) NEW.source_submission_id
              AND s.kind OPERATOR(pg_catalog.=) 'share' AND s.status OPERATOR(pg_catalog.=) 'pending'
              AND s.submitted_by OPERATOR(pg_catalog.=) NEW.submitted_by
              AND row_bot_org.brain_manages(s.space_id)) THEN
        RAISE EXCEPTION 'a fact from a share is made from a pending share its reviewer manages, with its submitter'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER brain_facts_source BEFORE INSERT OR UPDATE OF source_submission_id, submitted_by
    ON row_bot_org.brain_facts FOR EACH ROW EXECUTE FUNCTION row_bot_org.brain_facts_keep_source();

ALTER TABLE row_bot_org.brain_submissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE row_bot_org.brain_submissions FORCE ROW LEVEL SECURITY;

-- The migrating role (operators) reads everything.
CREATE POLICY brain_submissions_definer ON row_bot_org.brain_submissions FOR SELECT TO CURRENT_USER USING (true);

-- Its submitter, and its space's managers: nobody else.
CREATE POLICY brain_submissions_read ON row_bot_org.brain_submissions FOR SELECT
    USING (submitted_by OPERATOR(pg_catalog.=) row_bot_org.row_bot_principal()::pg_catalog.uuid
           OR row_bot_org.brain_manages(space_id));
-- Sent pending, in one's own name and groups, to a space one reads.
CREATE POLICY brain_submissions_submit ON row_bot_org.brain_submissions FOR INSERT
    WITH CHECK (status OPERATOR(pg_catalog.=) 'pending'
                AND row_bot_org.brain_is_own_author(submitted_by, submitter_groups)
                AND EXISTS (SELECT 1 FROM row_bot_org.spaces s
                            WHERE s.id OPERATOR(pg_catalog.=) brain_submissions.space_id));
-- Its submitter withdraws it while pending; its space's managers decide it (the trigger says which change is
-- whose).
CREATE POLICY brain_submissions_withdraw ON row_bot_org.brain_submissions FOR UPDATE
    USING (submitted_by OPERATOR(pg_catalog.=) row_bot_org.row_bot_principal()::pg_catalog.uuid
           AND status OPERATOR(pg_catalog.=) 'pending')
    WITH CHECK (submitted_by OPERATOR(pg_catalog.=) row_bot_org.row_bot_principal()::pg_catalog.uuid);
CREATE POLICY brain_submissions_decide ON row_bot_org.brain_submissions FOR UPDATE
    USING (row_bot_org.brain_manages(space_id)) WITH CHECK (row_bot_org.brain_manages(space_id));
-- No DELETE policy: a submission goes only with its space.

CALL row_bot_org.grant_runtime_access('row_bot_org.brain_submissions');

REVOKE ALL ON FUNCTION row_bot_org.brain_submissions_guard() FROM PUBLIC;
REVOKE ALL ON FUNCTION row_bot_org.brain_facts_keep_source() FROM PUBLIC;
