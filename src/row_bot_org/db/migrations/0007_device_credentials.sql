-- 0007: device credentials (C1a). A Row-Bot joins through its person's browser (row_bot_org.identity.device):
-- the confirm step registers a device (0006's devices), or refreshes the one joining again, and makes a one-time
-- authorization code; the app trades the code, with its PKCE verifier, for an access token and a rotating refresh
-- token. Run by the migrating role, which owns all of it.
--
-- * Nothing here holds a secret: a code or token is kept only as its verifier, HMAC-SHA256(pepper, label ||
--   secret) (identity.tokens), unique, 32 bytes. Each row is bound to its device and its principal.
-- * device_codes: the runtime may only add a code for one of its principal's own devices, while it isn't
--   revoked, naming the device, verifier, PKCE challenge, redirect URI and the confirm form's nonce (unique, so a
--   form submitted twice makes nothing the second time). It can't read codes, choose a code's lifetime (60
--   seconds, the default) or mark one used.
-- * device_tokens: the runtime reads its principal's own tokens (never a verifier) and may revoke them (set
--   revoked_at, never clear it); it never adds one. parent_id is the refresh token a pair was issued from.
-- * Before a principal is known (the token endpoint, and each bearer request), the runtime calls the
--   SECURITY DEFINER functions below, as for sessions (0002): fixed search_path, every object named by
--   schema, EXECUTE only for the runtime group. Each takes the presented secret's verifier and matches it
--   exactly, so a guess finds nothing and one verifier finds at most one row.
-- * A person may now revoke their own device (Leave): devices_self_leave.
-- * OPERATOR(...) forms all share one precedence, so every expression mixing two of them is bracketed.

CREATE TABLE row_bot_org.device_codes (
    id uuid PRIMARY KEY DEFAULT pg_catalog.gen_random_uuid(),
    verifier bytea NOT NULL UNIQUE CHECK (pg_catalog.octet_length(verifier) OPERATOR(pg_catalog.=) 32),
    principal_id uuid NOT NULL REFERENCES row_bot_org.principals (id) ON DELETE CASCADE
        DEFAULT row_bot_org.row_bot_principal()::pg_catalog.uuid,
    device_id uuid NOT NULL REFERENCES row_bot_org.devices (id) ON DELETE CASCADE,
    -- The PKCE S256 challenge and the loopback redirect URI the code was issued for: the token request must
    -- match both exactly.
    code_challenge text NOT NULL CHECK (pg_catalog.char_length(code_challenge) OPERATOR(pg_catalog.=) 43),
    redirect_uri text NOT NULL CHECK (pg_catalog.char_length(redirect_uri) OPERATOR(pg_catalog.<=) 512),
    -- The verifier of the confirm form's nonce: one Connect per form.
    form_nonce bytea NOT NULL UNIQUE CHECK (pg_catalog.octet_length(form_nonce) OPERATOR(pg_catalog.=) 32),
    created_at timestamptz NOT NULL DEFAULT pg_catalog.now(),
    expires_at timestamptz NOT NULL
        DEFAULT (pg_catalog.now() OPERATOR(pg_catalog.+) '60 seconds'::pg_catalog.interval),
    used_at timestamptz
);
CREATE INDEX device_codes_device ON row_bot_org.device_codes (device_id);
CREATE INDEX device_codes_expiry ON row_bot_org.device_codes (expires_at);

CREATE TABLE row_bot_org.device_tokens (
    id uuid PRIMARY KEY DEFAULT pg_catalog.gen_random_uuid(),
    verifier bytea NOT NULL UNIQUE CHECK (pg_catalog.octet_length(verifier) OPERATOR(pg_catalog.=) 32),
    kind text NOT NULL CHECK (kind IN ('access', 'refresh')),
    principal_id uuid NOT NULL REFERENCES row_bot_org.principals (id) ON DELETE CASCADE,
    device_id uuid NOT NULL REFERENCES row_bot_org.devices (id) ON DELETE CASCADE,
    -- The refresh token this one was issued from (none for the pair a code gave).
    parent_id uuid REFERENCES row_bot_org.device_tokens (id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.now(),
    -- An access token's end; a refresh token's idle end (never past its absolute end).
    expires_at timestamptz NOT NULL,
    -- A refresh token's absolute end, set when the code was traded and carried to each rotation.
    absolute_expires_at timestamptz,
    -- A refresh token once rotated.
    used_at timestamptz,
    revoked_at timestamptz,
    CHECK ((kind OPERATOR(pg_catalog.=) 'refresh') OPERATOR(pg_catalog.=) (absolute_expires_at IS NOT NULL))
);
CREATE INDEX device_tokens_device ON row_bot_org.device_tokens (device_id);
CREATE INDEX device_tokens_principal ON row_bot_org.device_tokens (principal_id);
CREATE INDEX device_tokens_parent ON row_bot_org.device_tokens (parent_id);
CREATE INDEX device_tokens_expiry ON row_bot_org.device_tokens (expires_at);

ALTER TABLE row_bot_org.device_codes ENABLE ROW LEVEL SECURITY;
ALTER TABLE row_bot_org.device_codes FORCE ROW LEVEL SECURITY;
ALTER TABLE row_bot_org.device_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE row_bot_org.device_tokens FORCE ROW LEVEL SECURITY;

-- The migrating role (its definer functions, and operators) reaches every row.
CREATE POLICY device_codes_definer ON row_bot_org.device_codes TO CURRENT_USER USING (true) WITH CHECK (true);
CREATE POLICY device_tokens_definer ON row_bot_org.device_tokens TO CURRENT_USER USING (true) WITH CHECK (true);

-- A person: a code only for one of their own devices that isn't revoked.
CREATE POLICY device_codes_self_issue ON row_bot_org.device_codes FOR INSERT
    WITH CHECK (principal_id OPERATOR(pg_catalog.=) row_bot_org.row_bot_principal()::pg_catalog.uuid
                AND EXISTS (SELECT 1 FROM row_bot_org.devices d
                            WHERE d.id OPERATOR(pg_catalog.=) device_id
                              AND d.principal_id OPERATOR(pg_catalog.=) row_bot_org.row_bot_principal()::pg_catalog.uuid
                              AND d.revoked_at IS NULL));
-- A person: their own tokens, read, and revoked (Leave, joining again).
CREATE POLICY device_tokens_self_read ON row_bot_org.device_tokens FOR SELECT
    USING (principal_id OPERATOR(pg_catalog.=) row_bot_org.row_bot_principal()::pg_catalog.uuid);
CREATE POLICY device_tokens_self_revoke ON row_bot_org.device_tokens FOR UPDATE
    USING (principal_id OPERATOR(pg_catalog.=) row_bot_org.row_bot_principal()::pg_catalog.uuid)
    WITH CHECK (principal_id OPERATOR(pg_catalog.=) row_bot_org.row_bot_principal()::pg_catalog.uuid
                AND revoked_at IS NOT NULL);
-- A person leaves with one of their devices: they set its revoked_at (devices_keep_revoked keeps it set).
CREATE POLICY devices_self_leave ON row_bot_org.devices FOR UPDATE
    USING (principal_id OPERATOR(pg_catalog.=) row_bot_org.row_bot_principal()::pg_catalog.uuid
           AND revoked_at IS NULL)
    WITH CHECK (principal_id OPERATOR(pg_catalog.=) row_bot_org.row_bot_principal()::pg_catalog.uuid
                AND revoked_at IS NOT NULL);

CALL row_bot_org.grant_runtime_access('row_bot_org.device_codes');
CALL row_bot_org.grant_runtime_access('row_bot_org.device_tokens');
DO $$
BEGIN
    EXECUTE pg_catalog.format('REVOKE SELECT, INSERT, UPDATE, DELETE ON row_bot_org.device_codes FROM %I',
                              row_bot_org.runtime_role());
    EXECUTE pg_catalog.format('GRANT INSERT (device_id, verifier, code_challenge, redirect_uri, form_nonce) '
                              'ON row_bot_org.device_codes TO %I', row_bot_org.runtime_role());
    EXECUTE pg_catalog.format('REVOKE SELECT, INSERT, UPDATE, DELETE ON row_bot_org.device_tokens FROM %I',
                              row_bot_org.runtime_role());
    EXECUTE pg_catalog.format('GRANT SELECT (id, kind, principal_id, device_id, parent_id, created_at, expires_at, '
                              'absolute_expires_at, used_at, revoked_at), UPDATE (revoked_at) '
                              'ON row_bot_org.device_tokens TO %I', row_bot_org.runtime_role());
END
$$;

-- The token endpoint trades a code. The unused, unexpired code with this verifier is marked used first, so it
-- works once whatever follows; then the PKCE challenge (S256 of the presented verifier, computed by the server)
-- and the redirect URI must equal the code's, and its device and principal must still be live. Then a new access
-- token and refresh token are stored: (device, false). A code presented again (used before) revokes every token
-- of its device, since whoever has it isn't the app that already used it: (device, true), nothing issued.
-- Nothing else returns a row. Used codes are kept for a day after they expire, to tell a replay from a guess.
CREATE FUNCTION row_bot_org.redeem_device_code(p_code bytea, p_challenge text, p_redirect_uri text, p_access bytea,
                                               p_refresh bytea, p_access_lifetime interval, p_refresh_idle interval,
                                               p_refresh_lifetime interval)
    RETURNS TABLE (device_id uuid, reused boolean)
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER
    SET search_path = pg_catalog, pg_temp
    AS $$
DECLARE
    found_device uuid;
    found_principal uuid;
    found_challenge text;
    found_redirect text;
BEGIN
    DELETE FROM row_bot_org.device_codes c
    WHERE c.expires_at OPERATOR(pg_catalog.<) (pg_catalog.now() OPERATOR(pg_catalog.-) '1 day'::pg_catalog.interval);
    UPDATE row_bot_org.device_codes c SET used_at = pg_catalog.now()
    WHERE c.verifier OPERATOR(pg_catalog.=) p_code AND c.used_at IS NULL
      AND c.expires_at OPERATOR(pg_catalog.>) pg_catalog.now()
    RETURNING c.device_id, c.principal_id, c.code_challenge, c.redirect_uri
    INTO found_device, found_principal, found_challenge, found_redirect;
    IF NOT FOUND THEN
        SELECT c.device_id INTO found_device FROM row_bot_org.device_codes c
        WHERE c.verifier OPERATOR(pg_catalog.=) p_code AND c.used_at IS NOT NULL;
        IF NOT FOUND THEN
            RETURN;
        END IF;
        UPDATE row_bot_org.device_tokens t SET revoked_at = pg_catalog.now()
        WHERE t.device_id OPERATOR(pg_catalog.=) found_device AND t.revoked_at IS NULL;
        RETURN QUERY SELECT found_device, true;
        RETURN;
    END IF;
    IF found_challenge IS DISTINCT FROM p_challenge OR found_redirect IS DISTINCT FROM p_redirect_uri
       OR NOT EXISTS (SELECT 1 FROM row_bot_org.devices d
                      JOIN row_bot_org.principals p ON p.id OPERATOR(pg_catalog.=) d.principal_id
                      WHERE d.id OPERATOR(pg_catalog.=) found_device
                        AND d.principal_id OPERATOR(pg_catalog.=) found_principal
                        AND d.revoked_at IS NULL AND p.status OPERATOR(pg_catalog.=) 'active') THEN
        RETURN;
    END IF;
    INSERT INTO row_bot_org.device_tokens (verifier, kind, principal_id, device_id, expires_at, absolute_expires_at)
    VALUES (p_access, 'access', found_principal, found_device,
            (pg_catalog.now() OPERATOR(pg_catalog.+) p_access_lifetime), NULL),
           (p_refresh, 'refresh', found_principal, found_device,
            (pg_catalog.now() OPERATOR(pg_catalog.+) LEAST(p_refresh_idle, p_refresh_lifetime)),
            (pg_catalog.now() OPERATOR(pg_catalog.+) p_refresh_lifetime));
    RETURN QUERY SELECT found_device, false;
END
$$;

-- The token endpoint rotates a refresh token. The live refresh token with this verifier (unused, not revoked,
-- before its idle end, its device not revoked and its principal active) is marked used, and a new access token
-- and refresh token are issued from it; the new refresh token's idle end never passes the absolute end it
-- inherits: (device, false). A token that is merely expired, or whose person is suspended, finds nothing and isn't
-- spent, so it works again once the person is resumed.
-- A refresh token already used, presented again:
-- * within p_reuse_interval of its use, still live, and while nothing issued from it has been used, its answer
--   was probably lost on the way to the app: what it gave is revoked and a new pair issued from it, (device, false);
-- * otherwise it was copied: every token of its device is revoked (the device record stays; the app joins again),
--   (device, true), nothing issued.
-- Tokens are purged a day after they end.
CREATE FUNCTION row_bot_org.refresh_device_token(p_refresh bytea, p_access bytea, p_new_refresh bytea,
                                                 p_access_lifetime interval, p_refresh_idle interval,
                                                 p_reuse_interval interval)
    RETURNS TABLE (device_id uuid, reused boolean)
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER
    SET search_path = pg_catalog, pg_temp
    AS $$
DECLARE
    found_id uuid;
    found_device uuid;
    found_principal uuid;
    found_absolute timestamptz;
    answer_lost boolean;
BEGIN
    DELETE FROM row_bot_org.device_tokens t
    WHERE t.expires_at OPERATOR(pg_catalog.<) (pg_catalog.now() OPERATOR(pg_catalog.-) '1 day'::pg_catalog.interval);
    UPDATE row_bot_org.device_tokens t SET used_at = pg_catalog.now()
    FROM row_bot_org.devices d, row_bot_org.principals p
    WHERE t.verifier OPERATOR(pg_catalog.=) p_refresh AND t.kind OPERATOR(pg_catalog.=) 'refresh'
      AND t.used_at IS NULL AND t.revoked_at IS NULL AND t.expires_at OPERATOR(pg_catalog.>) pg_catalog.now()
      AND d.id OPERATOR(pg_catalog.=) t.device_id AND d.principal_id OPERATOR(pg_catalog.=) t.principal_id
      AND d.revoked_at IS NULL
      AND p.id OPERATOR(pg_catalog.=) t.principal_id AND p.status OPERATOR(pg_catalog.=) 'active'
    RETURNING t.id, t.device_id, t.principal_id, t.absolute_expires_at
    INTO found_id, found_device, found_principal, found_absolute;
    IF NOT FOUND THEN
        SELECT t.id, t.device_id, t.principal_id, t.absolute_expires_at,
               (t.used_at OPERATOR(pg_catalog.>) (pg_catalog.now() OPERATOR(pg_catalog.-) p_reuse_interval))
               AND t.revoked_at IS NULL AND (t.expires_at OPERATOR(pg_catalog.>) pg_catalog.now())
               AND NOT EXISTS (SELECT 1 FROM row_bot_org.device_tokens child
                               WHERE child.parent_id OPERATOR(pg_catalog.=) t.id
                                 AND child.kind OPERATOR(pg_catalog.=) 'refresh' AND child.used_at IS NOT NULL)
               AND EXISTS (SELECT 1 FROM row_bot_org.devices d
                           JOIN row_bot_org.principals p ON p.id OPERATOR(pg_catalog.=) d.principal_id
                           WHERE d.id OPERATOR(pg_catalog.=) t.device_id
                             AND d.principal_id OPERATOR(pg_catalog.=) t.principal_id
                             AND d.revoked_at IS NULL AND p.status OPERATOR(pg_catalog.=) 'active')
        INTO found_id, found_device, found_principal, found_absolute, answer_lost
        FROM row_bot_org.device_tokens t
        WHERE t.verifier OPERATOR(pg_catalog.=) p_refresh AND t.kind OPERATOR(pg_catalog.=) 'refresh'
          AND t.used_at IS NOT NULL
        FOR UPDATE OF t;
        IF NOT FOUND THEN
            RETURN;
        END IF;
        IF NOT answer_lost THEN
            UPDATE row_bot_org.device_tokens t SET revoked_at = pg_catalog.now()
            WHERE t.device_id OPERATOR(pg_catalog.=) found_device AND t.revoked_at IS NULL;
            RETURN QUERY SELECT found_device, true;
            RETURN;
        END IF;
        UPDATE row_bot_org.device_tokens t SET revoked_at = pg_catalog.now()
        WHERE t.parent_id OPERATOR(pg_catalog.=) found_id AND t.revoked_at IS NULL;
    END IF;
    INSERT INTO row_bot_org.device_tokens (verifier, kind, principal_id, device_id, parent_id, expires_at,
                                           absolute_expires_at)
    VALUES (p_access, 'access', found_principal, found_device, found_id,
            (pg_catalog.now() OPERATOR(pg_catalog.+) p_access_lifetime), NULL),
           (p_new_refresh, 'refresh', found_principal, found_device, found_id,
            LEAST((pg_catalog.now() OPERATOR(pg_catalog.+) p_refresh_idle), found_absolute), found_absolute);
    RETURN QUERY SELECT found_device, false;
END
$$;

-- A request carries a bearer token: the live access token with this verifier (not revoked, before its end, its
-- device not revoked, its principal active), its device, principal and groups. At most one row; read only.
CREATE FUNCTION row_bot_org.resume_device_token(p_access bytea)
    RETURNS TABLE (device_id uuid, principal_id uuid, groups text[])
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path = pg_catalog, pg_temp
    AS $$
        SELECT t.device_id, t.principal_id,
            ARRAY(SELECT g.name FROM row_bot_org.principal_groups g
                  WHERE g.principal_id OPERATOR(pg_catalog.=) t.principal_id ORDER BY g.name)
        FROM row_bot_org.device_tokens t
        JOIN row_bot_org.devices d ON d.id OPERATOR(pg_catalog.=) t.device_id
        JOIN row_bot_org.principals p ON p.id OPERATOR(pg_catalog.=) t.principal_id
        WHERE t.verifier OPERATOR(pg_catalog.=) p_access AND t.kind OPERATOR(pg_catalog.=) 'access'
          AND t.revoked_at IS NULL AND t.expires_at OPERATOR(pg_catalog.>) pg_catalog.now()
          AND d.principal_id OPERATOR(pg_catalog.=) t.principal_id AND d.revoked_at IS NULL
          AND p.status OPERATOR(pg_catalog.=) 'active';
    $$;

REVOKE ALL ON FUNCTION row_bot_org.redeem_device_code(bytea, text, text, bytea, bytea, interval, interval, interval)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION row_bot_org.refresh_device_token(bytea, bytea, bytea, interval, interval, interval)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION row_bot_org.resume_device_token(bytea) FROM PUBLIC;
DO $$
BEGIN
    EXECUTE pg_catalog.format(
        'GRANT EXECUTE ON FUNCTION '
        'row_bot_org.redeem_device_code(bytea, text, text, bytea, bytea, interval, interval, interval), '
        'row_bot_org.refresh_device_token(bytea, bytea, bytea, interval, interval, interval), '
        'row_bot_org.resume_device_token(bytea) TO %I', row_bot_org.runtime_role());
END
$$;
