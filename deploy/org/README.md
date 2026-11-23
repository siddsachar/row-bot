# Row-Bot Org server (pre-release)

**Pre-release.** Row-Bot Org will hold a team's and a company's shared brain. This version starts,
checks its database, answers health checks and signs people in with the company's identity provider
(below); it stores nothing else yet. Don't run it for real data.

It is a separate deployment from the Row-Bot app. The desktop app never loads it, and desktop
installers and updates don't include it.

## Run it

It needs PostgreSQL 17 (16 at least) with the pgvector extension, and two roles (below).

```sh
uv sync --group org
ROW_BOT_ORG_MIGRATION_DATABASE_URL=<owner's address> uv run row-bot-org migrate
ROW_BOT_ORG_DATABASE_URL=<app role's address> uv run row-bot-org check-config  # 0 when it would start
ROW_BOT_ORG_DATABASE_URL=<app role's address> uv run row-bot-org serve  # http://127.0.0.1:8100/readyz
```

In a container (uid 10001, read-only root, all capabilities dropped), from the repository root: put the
app role's address in `deploy/org/secrets/ROW_BOT_ORG_DATABASE_URL` (ignored by git, readable by
uid 10001), then `docker compose -f deploy/org/compose.yaml up --build`.

## The database and its two roles

The server's tables use row-level security, so one person's rows stay theirs even if a query forgets
its `WHERE`. That only holds for a role that isn't a superuser, hasn't `BYPASSRLS`, can't `SET ROLE` to
a role that is either, and doesn't own the tables. So the server runs as such a role, and a second role
migrates and owns. As an administrator:

```sql
CREATE ROLE row_bot_org_owner LOGIN PASSWORD '...' CREATEROLE;  -- migrates; owns the database, schema, tables
CREATE DATABASE row_bot_org OWNER row_bot_org_owner;
\c row_bot_org
CREATE EXTENSION vector;                                         -- pgvector needs an administrator
CREATE ROLE row_bot_org_app LOGIN PASSWORD '...';                -- what serve connects as
```

`row-bot-org migrate`, run with the owner's address, makes the schema `row_bot_org` and this database's
runtime group, `row_bot_org_runtime_<database>` (`row_bot_org_runtime_<md5 of the name>` for a name
longer than 43 bytes; `SELECT row_bot_org.runtime_role()` shows it). Only that group may connect to the
database, and it may only read and write the rows of tables that force row-level security. Then make the
app role a member, as the owner:

```sql
SELECT format('GRANT %I TO row_bot_org_app', row_bot_org.runtime_role()) \gexec
```

Without `CREATEROLE` for the owner, an administrator creates the group (`CREATE ROLE
row_bot_org_runtime_row_bot_org NOLOGIN`) before the first `migrate`, and grants the membership.

The two roles are required. `serve` and `check-config` refuse a role that is a superuser, has
`BYPASSRLS`, can `SET ROLE` to one that is, or owns (or is a member of the owner of) the schema or its
tables, and the server checks again on every new database connection and every `/readyz`. The owner reads
every row (the sign-in functions it owns need that) and could turn row-level security off, so it never
serves. `migrate` refuses a superuser: the migrating role owns the functions the server calls before
anyone has signed in, and a superuser's would run past every check.

Migrations only go forward. `migrate` takes a lock (and gives up after 2 minutes), so two at once are
safe. It refuses a database whose applied migrations changed, that a newer server migrated, whose schema
another role made, or a new migration numbered below one already applied. It rolls back a migration that
leaves a table readable without forced row-level security: a table reaches the runtime group only through
`CALL row_bot_org.grant_runtime_access('row_bot_org.<table>')`, which checks that. `serve` refuses to
start while a migration is pending, unless `ROW_BOT_ORG_AUTO_MIGRATE=1`; then it migrates with
`ROW_BOT_ORG_MIGRATION_DATABASE_URL` and still serves with `ROW_BOT_ORG_DATABASE_URL`.

## Settings

Each is an environment variable, or a file of the same name in `ROW_BOT_SECRETS_DIR`; a variable wins
over a file. Any other `ROW_BOT_ORG_*` variable stops the server from starting.

- `ROW_BOT_ORG_DATABASE_URL`: the app role's address, `postgresql://...`, for `serve` and `check-config`.
- `ROW_BOT_ORG_MIGRATION_DATABASE_URL`: the owner's address, for `migrate` and auto-migrate.
  Both are secrets: the server takes them out of its environment once read, and no message or log shows
  any part of them.
- `ROW_BOT_ORG_AUTO_MIGRATE`: `1` lets `serve` apply pending migrations (it needs the migration address).
  Off by default.
- `ROW_BOT_ORG_STATEMENT_TIMEOUT`, `ROW_BOT_ORG_LOCK_TIMEOUT`, `ROW_BOT_ORG_IDLE_IN_TRANSACTION_TIMEOUT`:
  limits on each of the server's database connections, in seconds; 30, 10 and 60 by default.
- `ROW_BOT_ORG_HOST`: the address to listen on, `127.0.0.1` by default (`0.0.0.0` in the image).
- `ROW_BOT_ORG_PORT`: `8100` by default.
- `ROW_BOT_ORG_LOG_LEVEL`: `DEBUG`, `INFO` (default), `WARNING` or `ERROR`. Logs are JSON lines on stdout.
- `ROW_BOT_ORG_OPENAPI`: `1` publishes `/openapi.json`. Off by default. There is no docs page.

`/readyz` answers `ready` once the database is reachable as a fit role and no migration is pending, and
only says `ok` or `failing` for it, never where it is.

## Sign-in

People sign in with OpenID Connect (authorization code with PKCE) at `/auth/login`, with the server's one
identity provider; the provider sends them back to `<public URL>/auth/callback`, which you register with
it as the redirect URI, and they land on `/admin/` (the admin console, when it is served), or back on the
page they came from: `/auth/login?next=/console/policy` returns to that path, and a `next` that isn't a path
on this server is refused. Every route but `/healthz`, `/readyz` and the two sign-in steps needs a session:
without one, 401 (the console's pages send the browser to sign in instead). Requests that change something
must come from the public URL's origin. `/api/me` says who is signed in, and whether they are an admin.

- `ROW_BOT_ORG_PUBLIC_URL`: the origin browsers use, `https://org.example.com` (http only on loopback).
- `ROW_BOT_ORG_IDP`: the provider, a JSON object, for example
  `{"kind": "entra", "issuer": "https://login.microsoftonline.com/<tenant id>/v2.0", "client_id": "...",
  "allowed_tenants": ["<tenant id>"]}`. `kind` is `entra`, `okta` or `google`. Optional: `groups_claim`
  (`groups`), `group_map` (provider group to Row-Bot group; others are dropped), `admin_groups` (Row-Bot
  groups whose members are admins: each must be a `group_map` target, see People and admins) and
  `allowed_email_domains` (sign-in then needs a verified email in one of them; not for Entra).
  - **Entra** needs the tenant's own issuer and `allowed_tenants`: the `common` and `organizations`
    authorities are refused. The tenant check admits everyone with an account in the tenant, B2B guests
    included, so in the app registration's enterprise application set **Assignment required = Yes** and
    assign the groups who may sign in. Entra sends no `email_verified`, so `allowed_email_domains` is
    refused for it. Past 200 groups Entra sends a reference instead of the groups, and sign-in is
    refused: limit the groups claim to groups assigned to the application.
  - **Google** (issuer `https://accounts.google.com`) needs `hosted_domains`, the Workspace domains whose
    accounts may sign in (`["example.com"]`): any Google account can get a token for any Google client,
    so sign-in needs the token's `hd` in that list and a verified email. Make the OAuth client's consent
    screen **Internal**, so only your Workspace's accounts can use it at all.
- `ROW_BOT_ORG_IDP_CLIENT_SECRET`: the application's client secret (sent as `client_secret_basic`). A secret.
- `ROW_BOT_ORG_HMAC_PEPPER`: at least 32 random characters, the key of every stored verifier. A secret:
  changing it signs everyone out.
- `ROW_BOT_ORG_SESSION_LIFETIME` and `ROW_BOT_ORG_SESSION_IDLE_TIMEOUT`: seconds, 43200 and 3600 by default.
- `ROW_BOT_ORG_SIGN_IN_RATE_LIMIT`: requests to the two sign-in steps a minute from one client address
  (an IPv6 address counts by its /64), 60 by default; past it, 429.
- `ROW_BOT_ORG_TRUSTED_PROXIES`: the reverse proxies in front of the server, as comma-separated addresses
  or networks (`172.18.0.0/16`). Only from these are `X-Forwarded-For` and `X-Forwarded-Proto` believed;
  from anyone else they are ignored. Without it, behind a proxy every client is the proxy's address, so
  they all share one sign-in limit.

The server asks only for `openid email profile` and keeps no provider token. `row-bot-org principals
revoke-sessions <principal id>`, with the owner's address, signs a person out everywhere.

## People and admins

Everyone who signs in is a **member**. An **admin** is a person with the stored admin role, or a member of
one of `admin_groups`. (A **space owner** manages one company space; spaces come later.)

**The first admin:** a person exists on the server only once they have signed in, so

1. sign in once at `<public URL>/auth/login`, then
2. with the owner's address (`ROW_BOT_ORG_MIGRATION_DATABASE_URL`), run
   `row-bot-org principals grant-admin <your email or principal id>`.

`row-bot-org principals revoke-admin <email or id>` takes the stored role away; it refuses to remove the last
stored admin while `admin_groups` is unset, so the server always keeps one. An email two people share is
refused: name one by its id (`/api/me` shows yours). Suspended admins don't count as the one kept.

**Admins from your provider:** set `admin_groups` in `ROW_BOT_ORG_IDP` to Row-Bot groups that `group_map`
maps your provider's admin group to, for example `"group_map": {"<IT admins' object id>": "it-admins"},
"admin_groups": ["it-admins"]`. A group the provider merely sends never makes an admin, so the server refuses
an `admin_groups` entry that isn't a `group_map` target. Map by immutable ids (Entra object ids), never by
names people can create. Groups are read at sign-in, so a change in the provider applies at the person's
next sign-in; to take access away at once, suspend the person.

Admins manage people under `/api/admin/people`: the list (name, email, groups, stored `role`, `admin`,
last sign-in, `suspended`), `PUT .../{id}/role` with `{"role": "admin"}` or `{"role": "member"}`, and
`POST .../{id}/suspend` and `.../resume`. Suspending signs the person out everywhere and refuses their sign-in
until they are resumed; a model call already running finishes. An admin can't suspend themself or change
their own role (not even to admin: a provider-group admin would keep a stored role the provider can't take
back).

## Models

With sign-in and `ROW_BOT_ORG_GATEWAY_KEY` set, the server holds the company's model endpoints and
connected Row-Bots call models through it, so the endpoints' keys never reach a laptop.

- `ROW_BOT_ORG_GATEWAY_KEY`: 32 random bytes, base64
  (`python -c "import base64, secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"`).
  A secret: it seals every endpoint key in the database, and keys sealed with another one can't be used.
- `ROW_BOT_ORG_GATEWAY_BUDGETED_CALLS`: the calls a person under a hard budget may have running at once,
  per server process, 4 by default (budgets count a call's spend after it ends).
- Admins (People and admins) manage, under `/api/admin/`: `endpoints` (`api` `openai` for any
  OpenAI-compatible server, with the SDK's base URL such as `http://gpu-box:8000/v1`, or `anthropic` with
  `https://api.anthropic.com`; `api_key`, `timeout_seconds`, `enabled`; a new host, scheme or port in
  `base_url` drops the stored key unless `api_key` comes with it), `models` (the `name` people call, a
  model id the policy can name: letters, digits and `._:/@+-`, starting with a letter or digit; the
  endpoint's `upstream_model`, `kind` `chat` or `embeddings`, prices per million tokens, and `is_default`),
  `budgets` (monthly, per person or group, `soft_limit` adds a warning header, `hard_limit` refuses) and `usage?by=person|group|model&start=&end=`.
- **Who may use which model is the policy's `models.allowed`** (Policy below), with its group overrides:
  `*`, the default, is every catalog model. `/api/models` lists only the models a person's effective policy
  allows, and a call to any other is refused as if the model didn't exist.
- People list their models at `/api/models` and call them at `/api/gateway/openai/v1/chat/completions`,
  `/api/gateway/openai/v1/embeddings` and `/api/gateway/anthropic/v1/messages`. The gateway picks the model:
  requests naming others (`models`, `route`, `fallbacks`, `context_window_fallbacks`, `provider`), Anthropic
  server tools, `mcp_servers` and beta flags other than Row-Bot's own are refused.

## Policy

Admins keep one company policy and optional overrides per group (`/api/admin/policy`); each signed-in
Row-Bot fetches its person's merged policy from `/api/policy/effective`. The settings, their defaults and
the merge rules are in `contracts/policy/v1/`. An override can only widen the company policy: restrictions
go in the company policy. A write that names catalog models that don't exist (yet) is kept, and its answer
lists them as `unknown_models`.

**An override follows the group's name, so name only groups nobody can give themselves.** Set `group_map`
in `ROW_BOT_ORG_IDP` from the provider's immutable group ids (Entra object ids) or admin-managed group
names to the names the overrides use; never write an override for a display name people can create or rename
themselves (self-service groups). `row-bot-org check-config` warns when overrides exist without a
`group_map`.

## Admin console

Admins run the server from a browser at `<public URL>/console/`: people (make or remove admins, suspend and
resume), devices (revoke), models (endpoints, catalog, budgets, usage), the policy (the company policy in
plain words, group overrides and history) and the audit log (search, integrity check, JSON Lines export). It
uses the admin API above and nothing else.

- `ROW_BOT_ORG_CONSOLE_DIR`: the folder of the built console, `frontend/dist-console` after
  `npm --prefix frontend run console:build` (the server image will carry it). Unset, there is no console and
  `/console/` is an unknown path.
- Someone not signed in is sent to sign in and back; a signed-in member who isn't an admin gets a page saying
  they need to be one. The console's pages have their own Content-Security-Policy: only the console's own
  scripts, styles, fonts and requests, nothing inline, never in a frame.

## Audit

The server keeps one append-only audit log of what happened under company rules: sign-ins and sign-outs,
revoked sessions, role changes, suspensions, policy changes, model catalog and budget changes, model calls
(counts only: model, tokens, cost, duration and outcome, never a prompt or an answer), devices joining and
being revoked, exports of the log, and what connected Row-Bots report (tool actions under company policy,
their exports, an offline lockdown). The events follow `contracts/audit/v1/`; the operator's commands
(`row-bot-org principals ...`) are recorded with the actor `operator` / `cli`.

- Each event is stored in the same database transaction as the change it records, and linked into a hash
  chain. Nobody can change or delete a stored event through the server or its database role; an owner who
  switches row-level security off could, and `GET /api/admin/audit/verify` then reports the first broken link.
  Cutting events off the end shows only against a head kept elsewhere: keep the verify report's `head` (or an
  export) somewhere else from time to time.
- Only admins read the log: `GET /api/admin/audit` (newest first; `action`, `actor`, `target`, `from`, `to`,
  `before`, `limit`), `GET /api/admin/audit/verify` and `GET /api/admin/audit/export` (JSON Lines, 10,000
  events a file, `after` for the next one; each export is itself recorded). People can't read it, not even
  their own events.
- **Retention:** every event is kept. There is no expiry in this version; size the database for it (each event is
  a few hundred bytes).

## Devices

A connected Row-Bot registers itself (`POST /api/devices`) and reports in (`POST /api/devices/{id}/seen`).
Admins list devices (`GET /api/admin/devices?person=&revoked=`) and revoke one
(`POST /api/admin/devices/{id}/revoke`): a revoked device is refused from then on. To stop a person
altogether, suspend them (People and admins).
