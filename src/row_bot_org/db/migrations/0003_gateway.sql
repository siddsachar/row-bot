-- 0003: the model catalog and gateway. Endpoints (with their keys sealed by the server key), the models
-- under them, monthly budgets and one usage row per call. Run by the migrating role, which owns all of it.
--
-- Row-level security:
-- * A signed-in principal (principal_transaction) sees the models it may use (allowed_groups empty, or
--   one of its groups) and the endpoints behind them; it reads and records its own usage rows.
-- * An admin (row_bot_org.admin.admin_transaction sets row_bot.admin) manages endpoints, models and
--   budgets and reads everyone's usage. That flag is a placeholder for the admin role S2 adds.
-- * Budgets need everyone's spend in a group, which no principal may read row by row:
--   current_budget_state(), SECURITY DEFINER, returns only the caller's own and its groups' budgets
--   with what was spent this month, taking the caller from the transaction's settings, not arguments.

CREATE FUNCTION row_bot_org.row_bot_is_admin() RETURNS boolean
    LANGUAGE sql STABLE PARALLEL SAFE
    AS $$
        SELECT row_bot_org.row_bot_principal() IS NOT NULL
           AND COALESCE(pg_catalog.current_setting('row_bot.admin', true) OPERATOR(pg_catalog.=) 'on', false)
    $$;

CREATE TABLE row_bot_org.model_endpoints (
    id uuid PRIMARY KEY DEFAULT pg_catalog.gen_random_uuid(),
    name text NOT NULL UNIQUE CHECK (pg_catalog.char_length(name) OPERATOR(pg_catalog.<=) 100),
    api text NOT NULL CHECK (api IN ('openai', 'anthropic')),
    base_url text NOT NULL CHECK (pg_catalog.char_length(base_url) OPERATOR(pg_catalog.<=) 2048),
    -- AES-256-GCM under ROW_BOT_ORG_GATEWAY_KEY (row_bot_org.gateway.keys); NULL for an endpoint without a key.
    key_sealed bytea,
    timeout_seconds integer NOT NULL DEFAULT 60
        CHECK (timeout_seconds OPERATOR(pg_catalog.>=) 1 AND timeout_seconds OPERATOR(pg_catalog.<=) 600),
    enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.now(),
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.now()
);

CREATE TABLE row_bot_org.catalog_models (
    id uuid PRIMARY KEY DEFAULT pg_catalog.gen_random_uuid(),
    endpoint_id uuid NOT NULL REFERENCES row_bot_org.model_endpoints (id) ON DELETE RESTRICT,
    -- The id Row-Bots call it by, and the endpoint's own name for it.
    name text NOT NULL UNIQUE CHECK (pg_catalog.char_length(name) OPERATOR(pg_catalog.<=) 200),
    upstream_model text NOT NULL CHECK (pg_catalog.char_length(upstream_model) OPERATOR(pg_catalog.<=) 200),
    display_name text NOT NULL CHECK (pg_catalog.char_length(display_name) OPERATOR(pg_catalog.<=) 200),
    kind text NOT NULL CHECK (kind IN ('chat', 'embeddings')),
    context_window integer CHECK (context_window OPERATOR(pg_catalog.>) 0),
    -- Per million tokens, in the company's currency; zero for its own servers.
    input_price numeric(14, 6) NOT NULL DEFAULT 0 CHECK (input_price OPERATOR(pg_catalog.>=) 0),
    output_price numeric(14, 6) NOT NULL DEFAULT 0 CHECK (output_price OPERATOR(pg_catalog.>=) 0),
    -- Empty: everyone.
    allowed_groups text[] NOT NULL DEFAULT '{}',
    is_default boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.now()
);
CREATE INDEX catalog_models_endpoint ON row_bot_org.catalog_models (endpoint_id);
-- One company default per kind.
CREATE UNIQUE INDEX catalog_models_default ON row_bot_org.catalog_models (kind) WHERE is_default;

CREATE TABLE row_bot_org.budgets (
    id uuid PRIMARY KEY DEFAULT pg_catalog.gen_random_uuid(),
    scope text NOT NULL CHECK (scope IN ('person', 'group')),
    -- A principal's id, or a group's name.
    subject text NOT NULL CHECK (pg_catalog.char_length(subject) OPERATOR(pg_catalog.<=) 256),
    soft_limit numeric(14, 6) CHECK (soft_limit OPERATOR(pg_catalog.>=) 0),
    hard_limit numeric(14, 6) CHECK (hard_limit OPERATOR(pg_catalog.>=) 0),
    CHECK (soft_limit IS NOT NULL OR hard_limit IS NOT NULL),
    UNIQUE (scope, subject)
);

CREATE TABLE row_bot_org.usage_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    principal_id uuid NOT NULL DEFAULT row_bot_org.row_bot_principal()::pg_catalog.uuid,
    -- The principal's groups at the time of the call: what group usage and budgets count by.
    groups text[] NOT NULL,
    model text NOT NULL,
    input_tokens bigint NOT NULL CHECK (input_tokens OPERATOR(pg_catalog.>=) 0),
    output_tokens bigint NOT NULL CHECK (output_tokens OPERATOR(pg_catalog.>=) 0),
    cost numeric(18, 8) NOT NULL CHECK (cost OPERATOR(pg_catalog.>=) 0),
    duration_ms integer NOT NULL CHECK (duration_ms OPERATOR(pg_catalog.>=) 0),
    outcome text NOT NULL CHECK (outcome IN ('ok', 'error')),
    created_at timestamptz NOT NULL DEFAULT pg_catalog.now()
);
CREATE INDEX usage_events_principal_time ON row_bot_org.usage_events (principal_id, created_at);
CREATE INDEX usage_events_time ON row_bot_org.usage_events (created_at);
CREATE INDEX usage_events_groups ON row_bot_org.usage_events USING gin (groups);

ALTER TABLE row_bot_org.model_endpoints ENABLE ROW LEVEL SECURITY;
ALTER TABLE row_bot_org.model_endpoints FORCE ROW LEVEL SECURITY;
ALTER TABLE row_bot_org.catalog_models ENABLE ROW LEVEL SECURITY;
ALTER TABLE row_bot_org.catalog_models FORCE ROW LEVEL SECURITY;
ALTER TABLE row_bot_org.budgets ENABLE ROW LEVEL SECURITY;
ALTER TABLE row_bot_org.budgets FORCE ROW LEVEL SECURITY;
ALTER TABLE row_bot_org.usage_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE row_bot_org.usage_events FORCE ROW LEVEL SECURITY;

-- The migrating role (current_budget_state, and operators) reads budgets and usage.
CREATE POLICY budgets_definer ON row_bot_org.budgets FOR SELECT TO CURRENT_USER USING (true);
CREATE POLICY usage_events_definer ON row_bot_org.usage_events FOR SELECT TO CURRENT_USER USING (true);

-- A principal: the models it may use, the endpoints behind them, its own usage.
CREATE POLICY catalog_models_member ON row_bot_org.catalog_models FOR SELECT
    USING (row_bot_org.row_bot_principal() IS NOT NULL
           AND (pg_catalog.cardinality(allowed_groups) OPERATOR(pg_catalog.=) 0
                OR EXISTS (SELECT 1 FROM pg_catalog.unnest(allowed_groups) AS g (name)
                           WHERE row_bot_org.row_bot_in_group(g.name))));
CREATE POLICY model_endpoints_member ON row_bot_org.model_endpoints FOR SELECT
    USING (EXISTS (SELECT 1 FROM row_bot_org.catalog_models m
                   WHERE m.endpoint_id OPERATOR(pg_catalog.=) model_endpoints.id));
CREATE POLICY usage_events_self ON row_bot_org.usage_events FOR SELECT
    USING (principal_id OPERATOR(pg_catalog.=) row_bot_org.row_bot_principal()::pg_catalog.uuid);
CREATE POLICY usage_events_record ON row_bot_org.usage_events FOR INSERT
    WITH CHECK (principal_id OPERATOR(pg_catalog.=) row_bot_org.row_bot_principal()::pg_catalog.uuid);

-- An admin: everything but changing or removing usage.
CREATE POLICY model_endpoints_admin ON row_bot_org.model_endpoints
    USING (row_bot_org.row_bot_is_admin()) WITH CHECK (row_bot_org.row_bot_is_admin());
CREATE POLICY catalog_models_admin ON row_bot_org.catalog_models
    USING (row_bot_org.row_bot_is_admin()) WITH CHECK (row_bot_org.row_bot_is_admin());
CREATE POLICY budgets_admin ON row_bot_org.budgets
    USING (row_bot_org.row_bot_is_admin()) WITH CHECK (row_bot_org.row_bot_is_admin());
CREATE POLICY usage_events_admin ON row_bot_org.usage_events FOR SELECT
    USING (row_bot_org.row_bot_is_admin());

CALL row_bot_org.grant_runtime_access('row_bot_org.model_endpoints');
CALL row_bot_org.grant_runtime_access('row_bot_org.catalog_models');
CALL row_bot_org.grant_runtime_access('row_bot_org.budgets');
CALL row_bot_org.grant_runtime_access('row_bot_org.usage_events');

-- The caller's own budget and its groups' budgets, each with this calendar month's (UTC) spend.
CREATE FUNCTION row_bot_org.current_budget_state()
    RETURNS TABLE (scope text, subject text, soft_limit numeric, hard_limit numeric, spent numeric)
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path = pg_catalog, pg_temp
    AS $$
        SELECT b.scope, b.subject, b.soft_limit, b.hard_limit,
               COALESCE((SELECT pg_catalog.sum(u.cost) FROM row_bot_org.usage_events u
                         WHERE u.created_at OPERATOR(pg_catalog.>=)
                                   (pg_catalog.date_trunc('month', pg_catalog.now() AT TIME ZONE 'UTC')
                                    AT TIME ZONE 'UTC')
                           AND CASE WHEN b.scope OPERATOR(pg_catalog.=) 'person'
                                    THEN u.principal_id::pg_catalog.text OPERATOR(pg_catalog.=) b.subject
                                    ELSE u.groups OPERATOR(pg_catalog.@>) ARRAY[b.subject] END), 0)
        FROM row_bot_org.budgets b
        WHERE row_bot_org.row_bot_principal() IS NOT NULL
          AND ((b.scope OPERATOR(pg_catalog.=) 'person'
                AND b.subject OPERATOR(pg_catalog.=) row_bot_org.row_bot_principal())
               OR (b.scope OPERATOR(pg_catalog.=) 'group' AND row_bot_org.row_bot_in_group(b.subject)));
    $$;

REVOKE ALL ON FUNCTION row_bot_org.row_bot_is_admin() FROM PUBLIC;
REVOKE ALL ON FUNCTION row_bot_org.current_budget_state() FROM PUBLIC;
DO $$
BEGIN
    EXECUTE pg_catalog.format('GRANT EXECUTE ON FUNCTION row_bot_org.row_bot_is_admin(), '
                              'row_bot_org.current_budget_state() TO %I', row_bot_org.runtime_role());
END
$$;
