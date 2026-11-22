-- 0002: who signs in. Principals (one per identity provider subject), their groups, server sessions for
-- the browser, and the short-lived sign-in attempts. Run by the migrating role, which owns all of it.
--
-- Row-level security, and the two moments before a principal is known:
--
-- * Every table enables and forces row-level security. The runtime group reads only its principal's
--   own rows (principal_transaction sets who that is), through the "_self" policies.
-- * Resuming a session (each request's cookie) and finishing a sign-in happen before the server knows
--   who is asking, so they can't run inside principal_transaction. They run in SECURITY DEFINER
--   functions owned by the migrating role: resume_session takes one verifier and returns at most one
--   row; sign_in takes one verified identity and makes or updates that principal. The runtime group
--   may EXECUTE them, and never reads another principal's sessions, groups or attempts directly.
--   Inside them the forced row-level security still applies, to the owner; the "_definer" policies,
--   granted TO the migrating role alone (CURRENT_USER here), let them do their work. No other role
--   matches those policies, and the migrating role is also what an operator's `row-bot-org principals`
--   command connects as.
-- * The functions fix search_path to pg_catalog, pg_temp and name every object by schema, so no
--   object a caller creates can stand in for one of these.
-- * auth_attempts is never granted to the runtime group: begin_sign_in and take_sign_in are its only
--   way in, and a taken attempt is deleted (single use).

CREATE TABLE row_bot_org.principals (
    id uuid PRIMARY KEY DEFAULT pg_catalog.gen_random_uuid(),
    issuer text NOT NULL CHECK (pg_catalog.octet_length(issuer) OPERATOR(pg_catalog.<=) 512),
    subject text NOT NULL CHECK (pg_catalog.octet_length(subject) OPERATOR(pg_catalog.<=) 1024),
    display_name text NOT NULL DEFAULT '' CHECK (pg_catalog.char_length(display_name) OPERATOR(pg_catalog.<=) 200),
    email text CHECK (pg_catalog.char_length(email) OPERATOR(pg_catalog.<=) 320),
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended')),
    created_at timestamptz NOT NULL DEFAULT pg_catalog.now(),
    last_sign_in_at timestamptz NOT NULL DEFAULT pg_catalog.now(),
    UNIQUE (issuer, subject)
);

CREATE TABLE row_bot_org.principal_groups (
    principal_id uuid NOT NULL REFERENCES row_bot_org.principals (id) ON DELETE CASCADE,
    name text NOT NULL CHECK (pg_catalog.char_length(name) OPERATOR(pg_catalog.<=) 256),
    PRIMARY KEY (principal_id, name)
);

CREATE TABLE row_bot_org.server_sessions (
    id uuid PRIMARY KEY DEFAULT pg_catalog.gen_random_uuid(),
    -- HMAC-SHA256(pepper, the cookie's secret): the secret itself is never stored.
    verifier bytea NOT NULL UNIQUE CHECK (pg_catalog.octet_length(verifier) OPERATOR(pg_catalog.=) 32),
    principal_id uuid NOT NULL REFERENCES row_bot_org.principals (id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.now(),
    last_seen_at timestamptz NOT NULL DEFAULT pg_catalog.now(),
    expires_at timestamptz NOT NULL,
    idle_timeout interval NOT NULL,
    idle_expires_at timestamptz NOT NULL,
    user_agent text NOT NULL DEFAULT '' CHECK (pg_catalog.char_length(user_agent) OPERATOR(pg_catalog.<=) 200),
    address text NOT NULL DEFAULT '' CHECK (pg_catalog.char_length(address) OPERATOR(pg_catalog.<=) 64),
    revoked_at timestamptz
);
CREATE INDEX server_sessions_principal ON row_bot_org.server_sessions (principal_id);

CREATE TABLE row_bot_org.auth_attempts (
    -- HMAC of the state, of the secret in the browser's sign-in cookie and of the nonce.
    state bytea PRIMARY KEY CHECK (pg_catalog.octet_length(state) OPERATOR(pg_catalog.=) 32),
    browser bytea NOT NULL CHECK (pg_catalog.octet_length(browser) OPERATOR(pg_catalog.=) 32),
    nonce bytea NOT NULL CHECK (pg_catalog.octet_length(nonce) OPERATOR(pg_catalog.=) 32),
    -- The PKCE verifier: only the token endpoint may see it, so it is kept for the ten minutes.
    code_verifier text NOT NULL CHECK (pg_catalog.char_length(code_verifier) OPERATOR(pg_catalog.=) 43),
    expires_at timestamptz NOT NULL
);
CREATE INDEX auth_attempts_expiry ON row_bot_org.auth_attempts (expires_at);

ALTER TABLE row_bot_org.principals ENABLE ROW LEVEL SECURITY;
ALTER TABLE row_bot_org.principals FORCE ROW LEVEL SECURITY;
ALTER TABLE row_bot_org.principal_groups ENABLE ROW LEVEL SECURITY;
ALTER TABLE row_bot_org.principal_groups FORCE ROW LEVEL SECURITY;
ALTER TABLE row_bot_org.server_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE row_bot_org.server_sessions FORCE ROW LEVEL SECURITY;
ALTER TABLE row_bot_org.auth_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE row_bot_org.auth_attempts FORCE ROW LEVEL SECURITY;

-- The migrating role (its definer functions, and operators) reaches every row.
CREATE POLICY principals_definer ON row_bot_org.principals TO CURRENT_USER USING (true) WITH CHECK (true);
CREATE POLICY principal_groups_definer ON row_bot_org.principal_groups TO CURRENT_USER USING (true) WITH CHECK (true);
CREATE POLICY server_sessions_definer ON row_bot_org.server_sessions TO CURRENT_USER USING (true) WITH CHECK (true);
CREATE POLICY auth_attempts_definer ON row_bot_org.auth_attempts TO CURRENT_USER USING (true) WITH CHECK (true);

-- Everyone else: their own principal's rows, read only, except that a session may be revoked (logout).
CREATE POLICY principals_self ON row_bot_org.principals FOR SELECT
    USING (id OPERATOR(pg_catalog.=) row_bot_org.row_bot_principal()::pg_catalog.uuid);
CREATE POLICY principal_groups_self ON row_bot_org.principal_groups FOR SELECT
    USING (principal_id OPERATOR(pg_catalog.=) row_bot_org.row_bot_principal()::pg_catalog.uuid);
CREATE POLICY server_sessions_self ON row_bot_org.server_sessions FOR SELECT
    USING (principal_id OPERATOR(pg_catalog.=) row_bot_org.row_bot_principal()::pg_catalog.uuid);
CREATE POLICY server_sessions_self_revoke ON row_bot_org.server_sessions FOR UPDATE
    USING (principal_id OPERATOR(pg_catalog.=) row_bot_org.row_bot_principal()::pg_catalog.uuid)
    WITH CHECK (principal_id OPERATOR(pg_catalog.=) row_bot_org.row_bot_principal()::pg_catalog.uuid);

CALL row_bot_org.grant_runtime_access('row_bot_org.principals');
CALL row_bot_org.grant_runtime_access('row_bot_org.principal_groups');
CALL row_bot_org.grant_runtime_access('row_bot_org.server_sessions');

-- A sign-in starts: keep its attempt for p_lifetime (at most 15 minutes), and drop expired ones.
CREATE FUNCTION row_bot_org.begin_sign_in(p_state bytea, p_browser bytea, p_nonce bytea, p_code_verifier text,
                                          p_lifetime interval) RETURNS void
    LANGUAGE sql VOLATILE SECURITY DEFINER
    SET search_path = pg_catalog, pg_temp
    AS $$
        DELETE FROM row_bot_org.auth_attempts WHERE expires_at OPERATOR(pg_catalog.<=) pg_catalog.now();
        INSERT INTO row_bot_org.auth_attempts (state, browser, nonce, code_verifier, expires_at)
        VALUES (p_state, p_browser, p_nonce, p_code_verifier,
                pg_catalog.now() OPERATOR(pg_catalog.+)
                    LEAST(p_lifetime, '15 minutes'::pg_catalog.interval));
    $$;

-- The callback arrives: the attempt for this state is deleted whatever happens next (single use), and
-- returned only while it is unexpired.
CREATE FUNCTION row_bot_org.take_sign_in(p_state bytea)
    RETURNS TABLE (browser bytea, nonce bytea, code_verifier text)
    LANGUAGE sql VOLATILE SECURITY DEFINER
    SET search_path = pg_catalog, pg_temp
    AS $$
        WITH taken AS (
            DELETE FROM row_bot_org.auth_attempts a WHERE a.state OPERATOR(pg_catalog.=) p_state
            RETURNING a.browser, a.nonce, a.code_verifier, a.expires_at
        )
        SELECT t.browser, t.nonce, t.code_verifier FROM taken t
        WHERE t.expires_at OPERATOR(pg_catalog.>) pg_catalog.now();
    $$;

-- A verified identity signs in: its principal is made or updated (by issuer and subject, never by
-- email), its groups replaced with the token's, the browser's earlier session (p_replaces) revoked, and
-- a new session made, unless the principal is suspended. Returns the principal and its status.
CREATE FUNCTION row_bot_org.sign_in(p_issuer text, p_subject text, p_display_name text, p_email text,
                                    p_groups text[], p_verifier bytea, p_lifetime interval, p_idle interval,
                                    p_user_agent text, p_address text, p_replaces bytea)
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
    INSERT INTO row_bot_org.principals AS p (issuer, subject, display_name, email)
    VALUES (p_issuer, p_subject, p_display_name, p_email)
    ON CONFLICT (issuer, subject) DO UPDATE
        SET display_name = EXCLUDED.display_name, email = EXCLUDED.email, last_sign_in_at = pg_catalog.now()
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

-- A request carries a session cookie: the one live session with this verifier (not revoked, not past
-- its lifetime or idle limit, its principal active), its idle limit pushed back, with its groups.
CREATE FUNCTION row_bot_org.resume_session(p_verifier bytea)
    RETURNS TABLE (session_id uuid, principal_id uuid, groups text[])
    LANGUAGE sql VOLATILE SECURITY DEFINER
    SET search_path = pg_catalog, pg_temp
    AS $$
        UPDATE row_bot_org.server_sessions s
        SET last_seen_at = pg_catalog.now(),
            idle_expires_at = LEAST(pg_catalog.now() OPERATOR(pg_catalog.+) s.idle_timeout, s.expires_at)
        FROM row_bot_org.principals p
        WHERE s.verifier OPERATOR(pg_catalog.=) p_verifier AND s.revoked_at IS NULL
          AND s.expires_at OPERATOR(pg_catalog.>) pg_catalog.now()
          AND s.idle_expires_at OPERATOR(pg_catalog.>) pg_catalog.now()
          AND p.id OPERATOR(pg_catalog.=) s.principal_id AND p.status OPERATOR(pg_catalog.=) 'active'
        RETURNING s.id, s.principal_id,
            ARRAY(SELECT g.name FROM row_bot_org.principal_groups g
                  WHERE g.principal_id OPERATOR(pg_catalog.=) s.principal_id ORDER BY g.name);
    $$;

REVOKE ALL ON FUNCTION row_bot_org.begin_sign_in(bytea, bytea, bytea, text, interval) FROM PUBLIC;
REVOKE ALL ON FUNCTION row_bot_org.take_sign_in(bytea) FROM PUBLIC;
REVOKE ALL ON FUNCTION row_bot_org.sign_in(text, text, text, text, text[], bytea, interval, interval, text, text,
                                           bytea) FROM PUBLIC;
REVOKE ALL ON FUNCTION row_bot_org.resume_session(bytea) FROM PUBLIC;
DO $$
BEGIN
    EXECUTE pg_catalog.format(
        'GRANT EXECUTE ON FUNCTION row_bot_org.begin_sign_in(bytea, bytea, bytea, text, interval), '
        'row_bot_org.take_sign_in(bytea), '
        'row_bot_org.sign_in(text, text, text, text, text[], bytea, interval, interval, text, text, bytea), '
        'row_bot_org.resume_session(bytea) TO %I', row_bot_org.runtime_role());
END
$$;
