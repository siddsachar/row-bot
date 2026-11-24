-- 0010: the review queue (B4a): what people offer to a company space, waiting for its owners or an admin to
-- approve it (it becomes a fact of the space) or reject it. Run by the migrating role, which owns all of it.
--
-- * brain_submissions: a share (kind 'share'; 'skill' is reserved for the library's submissions) of a title, a
--   text and an optional note on where it came from, sent to one space by its submitter (and their groups). It is
--   pending, then approved (fact_id names the fact it became) or rejected (with a reason). A decision is final;
--   who decided and when stay with it. Its submitter may withdraw it while it waits: that deletes it, text and all.
-- * It holds what a person wrote in a chat, so before approval nobody but its submitter and the space's managers
--   (brain_manages, 0008: its owners, or an admin's transaction) ever reads it. Not the space's other readers, not
--   the owners of other spaces. A submitter reads their own whatever became of it.
-- * Submitting needs reading the space (the spaces policies, through EXISTS), in one's own name and groups
--   (brain_is_own_author, 0009). Only a manager decides it, once (the trigger below: permissive policies OR
--   together, so a policy alone can't say which change is whose). An approval may edit the title and text and move
--   it to another group space the reviewer manages, never into the company space (unless it was sent there, when
--   it isn't a move); moved_to then keeps the new space's name for its submitter, who may not read that space.
-- * brain_facts gains the share it came from and its submitter (source_submission_id, submitted_by: both or
--   neither, the pair a key into the share). A fact names a share only if, by the end of the transaction, that share
--   is approved as this fact (a deferred constraint trigger). The source is never changed afterwards, only cleared:
--   by a new wording (the editor's, not the submitter's) or by the share going with its space.
-- * brain_share_days counts each person's shares per (UTC) day, withdrawn ones included, so sending and withdrawing
--   can't loop without end. Each person reads and writes only their own count.
-- * No SECURITY DEFINER function. OPERATOR(...) forms all share one precedence, so every expression mixing two of
--   them is bracketed.

CREATE TABLE row_bot_org.brain_submissions (
    id uuid PRIMARY KEY DEFAULT pg_catalog.gen_random_uuid(),
    kind text NOT NULL DEFAULT 'share' CHECK (kind IN ('share', 'skill')),
    space_id uuid NOT NULL REFERENCES row_bot_org.spaces (id) ON DELETE CASCADE,
    -- The space it was sent to (space_id differs once an approval moved it), and the name of the space it was moved
    -- to, as its submitter sees it.
    sent_space_id uuid NOT NULL,
    moved_to text CHECK (pg_catalog.char_length(moved_to) OPERATOR(pg_catalog.<=) 160),
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
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    -- Who approved or rejected it, when, and why it was rejected.
    reviewed_by uuid,
    decided_at timestamptz,
    reason text CHECK (pg_catalog.char_length(reason) OPERATOR(pg_catalog.>=) 1
                       AND pg_catalog.char_length(reason) OPERATOR(pg_catalog.<=) 500),
    -- The fact it became (NULL again if that fact is deleted).
    fact_id uuid UNIQUE REFERENCES row_bot_org.brain_facts (id) ON DELETE SET NULL,
    UNIQUE (id, submitted_by),
    CHECK ((status OPERATOR(pg_catalog.=) 'pending') OPERATOR(pg_catalog.=) (decided_at IS NULL)),
    CHECK ((status OPERATOR(pg_catalog.=) 'pending') OPERATOR(pg_catalog.=) (reviewed_by IS NULL)),
    CHECK ((status OPERATOR(pg_catalog.=) 'rejected') OPERATOR(pg_catalog.=) (reason IS NOT NULL)),
    CHECK (status OPERATOR(pg_catalog.=) 'approved' OR fact_id IS NULL),
    CHECK ((space_id OPERATOR(pg_catalog.=) sent_space_id) OPERATOR(pg_catalog.=) (moved_to IS NULL))
);
-- The queue (each space's pending, oldest first) and a person's own.
CREATE INDEX brain_submissions_space ON row_bot_org.brain_submissions (space_id, status, submitted_at);
CREATE INDEX brain_submissions_submitter ON row_bot_org.brain_submissions (submitted_by, status, submitted_at);

ALTER TABLE row_bot_org.brain_facts
    ADD COLUMN source_submission_id uuid,
    ADD COLUMN submitted_by uuid,
    ADD CHECK ((source_submission_id IS NULL) OPERATOR(pg_catalog.=) (submitted_by IS NULL)),
    ADD FOREIGN KEY (source_submission_id, submitted_by) REFERENCES row_bot_org.brain_submissions (id, submitted_by)
        ON DELETE SET NULL;
CREATE UNIQUE INDEX brain_facts_source_submission ON row_bot_org.brain_facts (source_submission_id)
    WHERE source_submission_id IS NOT NULL;

CREATE TABLE row_bot_org.brain_share_days (
    submitted_by uuid NOT NULL DEFAULT row_bot_org.row_bot_principal()::pg_catalog.uuid,
    day date NOT NULL,
    shares integer NOT NULL CHECK (shares OPERATOR(pg_catalog.>=) 0),
    PRIMARY KEY (submitted_by, day)
);

-- Who may make which change to a submission (the policies let its space's managers update it). A submission keeps
-- who sent it, when, where to and what it is; it is decided once, from pending, by a manager of its space (and of
-- the space an approval moves it to) in their own name; only the key's SET NULL (its fact deleted) changes it later.
CREATE FUNCTION row_bot_org.brain_submissions_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog, pg_temp
    AS $$
DECLARE
    principal text := row_bot_org.row_bot_principal();
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id OR NEW.kind IS DISTINCT FROM OLD.kind
       OR NEW.submitted_by IS DISTINCT FROM OLD.submitted_by
       OR NEW.submitter_groups IS DISTINCT FROM OLD.submitter_groups
       OR NEW.submitted_at IS DISTINCT FROM OLD.submitted_at
       OR NEW.source_note IS DISTINCT FROM OLD.source_note
       OR NEW.sent_space_id IS DISTINCT FROM OLD.sent_space_id THEN
        RAISE EXCEPTION 'a submission keeps who sent it, when, where to and what it is'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- The fact it became was deleted (the key's SET NULL): nothing else changes.
    IF NEW.fact_id IS NULL AND OLD.fact_id IS NOT NULL
       AND NEW.status = OLD.status AND NEW.space_id = OLD.space_id AND NEW.title = OLD.title
       AND NEW.body = OLD.body AND NEW.moved_to IS NOT DISTINCT FROM OLD.moved_to
       AND NEW.reviewed_by IS NOT DISTINCT FROM OLD.reviewed_by
       AND NEW.decided_at IS NOT DISTINCT FROM OLD.decided_at AND NEW.reason IS NOT DISTINCT FROM OLD.reason THEN
        RETURN NEW;
    END IF;
    IF OLD.status <> 'pending' OR NEW.status = 'pending' THEN
        RAISE EXCEPTION 'a submission changes only by being decided, once' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF principal IS NULL OR NEW.reviewed_by IS DISTINCT FROM principal::uuid
       OR NOT row_bot_org.brain_manages(OLD.space_id) OR NOT row_bot_org.brain_manages(NEW.space_id) THEN
        RAISE EXCEPTION 'a manager of its space decides a submission, in their own name'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.status = 'rejected' AND (NEW.space_id IS DISTINCT FROM OLD.space_id OR NEW.title IS DISTINCT FROM OLD.title
                                    OR NEW.body IS DISTINCT FROM OLD.body
                                    OR NEW.moved_to IS DISTINCT FROM OLD.moved_to) THEN
        RAISE EXCEPTION 'a rejected submission stays as it was sent' USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- A move: to a group space, never into the company space, keeping the new space's name for its submitter.
    IF NEW.space_id IS DISTINCT FROM OLD.space_id AND (
            EXISTS (SELECT 1 FROM row_bot_org.spaces s
                    WHERE s.id OPERATOR(pg_catalog.=) NEW.space_id AND s.kind OPERATOR(pg_catalog.=) 'company')
            OR NEW.moved_to IS DISTINCT FROM (SELECT s.name FROM row_bot_org.spaces s
                                              WHERE s.id OPERATOR(pg_catalog.=) NEW.space_id)) THEN
        RAISE EXCEPTION 'a share moves only to a group space, which it then names'
            USING ERRCODE = 'insufficient_privilege';
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

-- A fact never comes to name a share it didn't name, or another one: its source is only ever cleared (a new
-- wording, or the share gone with its space).
CREATE FUNCTION row_bot_org.brain_facts_keep_source() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog, pg_temp
    AS $$
BEGIN
    IF NEW.source_submission_id IS NOT NULL
       AND (NEW.source_submission_id IS DISTINCT FROM OLD.source_submission_id
            OR NEW.submitted_by IS DISTINCT FROM OLD.submitted_by) THEN
        RAISE EXCEPTION 'a fact keeps the share it came from, or loses it' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER brain_facts_keep_source BEFORE UPDATE OF source_submission_id, submitted_by
    ON row_bot_org.brain_facts FOR EACH ROW EXECUTE FUNCTION row_bot_org.brain_facts_keep_source();

-- At the end of the transaction, a fact that names a share is the fact that share was approved as.
CREATE FUNCTION row_bot_org.brain_facts_source_approved() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog, pg_temp
    AS $$
BEGIN
    IF NEW.source_submission_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM row_bot_org.brain_submissions s
            WHERE s.id OPERATOR(pg_catalog.=) NEW.source_submission_id
              AND s.kind OPERATOR(pg_catalog.=) 'share' AND s.status OPERATOR(pg_catalog.=) 'approved'
              AND s.fact_id OPERATOR(pg_catalog.=) NEW.id AND s.space_id OPERATOR(pg_catalog.=) NEW.space_id) THEN
        RAISE EXCEPTION 'a fact from a share is the fact that share was approved as'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NULL;
END
$$;
CREATE CONSTRAINT TRIGGER brain_facts_source_approved AFTER INSERT OR UPDATE OF source_submission_id, submitted_by
    ON row_bot_org.brain_facts DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION row_bot_org.brain_facts_source_approved();

ALTER TABLE row_bot_org.brain_submissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE row_bot_org.brain_submissions FORCE ROW LEVEL SECURITY;
ALTER TABLE row_bot_org.brain_share_days ENABLE ROW LEVEL SECURITY;
ALTER TABLE row_bot_org.brain_share_days FORCE ROW LEVEL SECURITY;

-- The migrating role (operators) reads everything.
CREATE POLICY brain_submissions_definer ON row_bot_org.brain_submissions FOR SELECT TO CURRENT_USER USING (true);
CREATE POLICY brain_share_days_definer ON row_bot_org.brain_share_days FOR SELECT TO CURRENT_USER USING (true);

-- Its submitter, and its space's managers: nobody else.
CREATE POLICY brain_submissions_read ON row_bot_org.brain_submissions FOR SELECT
    USING (submitted_by OPERATOR(pg_catalog.=) row_bot_org.row_bot_principal()::pg_catalog.uuid
           OR row_bot_org.brain_manages(space_id));
-- Sent pending, in one's own name and groups, to a space one reads.
CREATE POLICY brain_submissions_submit ON row_bot_org.brain_submissions FOR INSERT
    WITH CHECK (status OPERATOR(pg_catalog.=) 'pending'
                AND sent_space_id OPERATOR(pg_catalog.=) space_id
                AND row_bot_org.brain_is_own_author(submitted_by, submitter_groups)
                AND EXISTS (SELECT 1 FROM row_bot_org.spaces s
                            WHERE s.id OPERATOR(pg_catalog.=) brain_submissions.space_id));
-- Its space's managers decide it (the trigger says how).
CREATE POLICY brain_submissions_decide ON row_bot_org.brain_submissions FOR UPDATE
    USING (row_bot_org.brain_manages(space_id)) WITH CHECK (row_bot_org.brain_manages(space_id));
-- Its submitter withdraws it while it waits, which deletes it. Otherwise it goes only with its space.
CREATE POLICY brain_submissions_withdraw ON row_bot_org.brain_submissions FOR DELETE
    USING (submitted_by OPERATOR(pg_catalog.=) row_bot_org.row_bot_principal()::pg_catalog.uuid
           AND status OPERATOR(pg_catalog.=) 'pending');

-- A person's own daily count.
CREATE POLICY brain_share_days_own ON row_bot_org.brain_share_days
    USING (submitted_by OPERATOR(pg_catalog.=) row_bot_org.row_bot_principal()::pg_catalog.uuid)
    WITH CHECK (submitted_by OPERATOR(pg_catalog.=) row_bot_org.row_bot_principal()::pg_catalog.uuid);

CALL row_bot_org.grant_runtime_access('row_bot_org.brain_submissions');
CALL row_bot_org.grant_runtime_access('row_bot_org.brain_share_days');

REVOKE ALL ON FUNCTION row_bot_org.brain_submissions_guard() FROM PUBLIC;
REVOKE ALL ON FUNCTION row_bot_org.brain_facts_keep_source() FROM PUBLIC;
REVOKE ALL ON FUNCTION row_bot_org.brain_facts_source_approved() FROM PUBLIC;
