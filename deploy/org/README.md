# Row-Bot Org server (pre-release)

**Pre-release.** Row-Bot Org is the Company Server: it signs people in with the company's identity
provider and holds the company's policy, model gateway, audit log, devices and admin console (below).
Don't run it for real data yet.

It is a separate deployment from the Row-Bot app. The desktop app never loads it, and desktop
installers and updates don't include it.

## Run it: the first run with Docker Compose

`compose.yaml` runs three services on one machine:

- `postgres`: PostgreSQL 17 with pgvector, its data in the `row-bot-org-db` volume, its port never
  published. The first time the volume is empty, `initdb/` makes the database, the two roles and the
  runtime group (The database and its two roles, below).
- `migrate`: brings the database up to date as the owner role, then exits. It runs at every `up`.
- `row-bot-org`: the server, with the admin console, as the app role. It starts only after `migrate`
  succeeded, and listens on `127.0.0.1:8100` on the host (uid 10001, read-only root, every capability
  dropped).

You need Docker with Compose v2, Python 3 (for step 1), this repository checked out (no image is
published yet: Compose builds it), and an application registered with your identity provider. Run every
command from `deploy/org`.

1. **Make the secrets.** `python make_secrets.py` (on Windows `py make_secrets.py`) writes
   `secrets/`: the three database passwords, the two database addresses built from them, the session
   pepper, the gateway key and an empty file for the provider's client secret. It never writes over a file
   that is there: those are the passwords the database was made with. The folder is yours alone; Compose
   gives each container only the files it needs, as `/run/secrets/<name>`.
2. **Fill in the settings.** Copy `org.env.example` to `org.env` and set:
   - `ROW_BOT_ORG_PUBLIC_URL`, the https address people will use (TLS goes in front in step 7; to try the
     server on this machine first, `http://localhost:8100`);
   - `ROW_BOT_ORG_IDP`, your provider (Sign-in, below).

   Register `<ROW_BOT_ORG_PUBLIC_URL>/auth/callback` with the provider as the redirect URI (a web
   application with a client secret), and paste its client secret into
   `secrets/ROW_BOT_ORG_IDP_CLIENT_SECRET`. `org.env` holds only the server's own settings. Compose's own
   variables (`ROW_BOT_ORG_IMAGE`, `ROW_BOT_ORG_BIND_ADDRESS`, `ROW_BOT_ORG_HOST_PORT`, listed at the top of
   `compose.yaml`) go in a `.env` file beside it or in the shell: the server refuses any `ROW_BOT_ORG_*`
   variable it doesn't know.
3. **Start it.** `docker compose up -d --build`. `docker compose ps -a` then shows `migrate` exited (0) and
   the other two healthy, and `http://127.0.0.1:8100/readyz` says `ready`. If the server stopped,
   `docker compose logs row-bot-org` says which setting is wrong.
4. **Sign in once** at `<ROW_BOT_ORG_PUBLIC_URL>/console/`. You aren't an admin yet, so it says you need to
   be one; the server now knows you. `<ROW_BOT_ORG_PUBLIC_URL>/api/me` shows your `id`.
5. **Make yourself the first admin**, as the owner role, through the `migrate` service:

   ```sh
   docker compose run --rm migrate principals grant-admin you@example.com
   ```

   Use your `id` instead of the email when the provider sends no verified email (Entra doesn't).
6. **Open the console** at `<ROW_BOT_ORG_PUBLIC_URL>/console/` again: People, Devices, Models, Policy and
   Audit (Admin console, below). More admins: make them there, or with `admin_groups` (People and admins).
7. **Put TLS in front**, on a reverse proxy that forwards to `127.0.0.1:8100`. With
   [Caddy](https://caddyserver.com/) on the same machine, this is the whole Caddyfile (it gets the
   certificate itself):

   ```
   rowbot.example.com {
       reverse_proxy 127.0.0.1:8100
   }
   ```

   Then set `ROW_BOT_ORG_TRUSTED_PROXIES` in `org.env` to the address the proxy reaches the server from (a
   proxy on the host comes through the Compose network's gateway: `docker network inspect org_default`
   shows it), so the sign-in limit counts each person, not the proxy, and run `docker compose up -d`.

`docker compose down` stops everything and keeps the database; `docker compose down -v` **deletes** it.

## Back up and restore

Back up as the `postgres` superuser, through the `postgres` service (the owner can't: row-level security
holds it back too):

```sh
docker compose exec postgres pg_dump --username postgres --format custom --file /tmp/row-bot-org.dump row_bot_org
docker compose cp postgres:/tmp/row-bot-org.dump ./row-bot-org.dump
docker compose exec postgres rm /tmp/row-bot-org.dump
```

**Back up `secrets/` and `org.env` too**, kept apart from the dumps and encrypted. Without them:

- `ROW_BOT_ORG_GATEWAY_KEY`: every model endpoint's stored API key can't be opened. Enter each key again in
  the console.
- `ROW_BOT_ORG_HMAC_PEPPER`: every session ends; everyone signs in again.
- `ROW_BOT_ORG_IDP_CLIENT_SECRET`: make a new one with your provider.
- The passwords and addresses matter least: a restore makes the roles again, with whatever passwords
  `secrets/` holds then (a new set from `make_secrets.py` works).

Restore into an empty volume (on a new machine, or after removing the old volume: `docker volume ls` shows
it, `org_row-bot-org-db` by default, and removing it deletes that database):

```sh
docker compose up -d postgres    # the empty volume gets the roles and an empty database
docker compose cp ./row-bot-org.dump postgres:/tmp/row-bot-org.dump
docker compose exec postgres pg_restore --username postgres --dbname row_bot_org --exit-on-error --single-transaction /tmp/row-bot-org.dump
docker compose exec postgres rm /tmp/row-bot-org.dump
docker compose up -d             # migrate applies anything newer, then the server starts
```

## Upgrades

No image is published yet, so you build it from this repository, under a tag of your own. (Publishing
images is a later decision.)

1. Pin the image: `ROW_BOT_ORG_IMAGE=row-bot-org:<version>` in `deploy/org/.env` (Compose's file, not
   `org.env`). Without it every build replaces `row-bot-org:dev`.
2. Back up (above). Migrations only go forward: the old image refuses a database a newer one migrated, so
   going back means restoring the backup into an empty volume.
3. Check out the new version (`git fetch --tags`, `git checkout <tag>`), set its tag in `.env`, then
   `docker compose build` and `docker compose up -d`. `migrate` runs first and the server is replaced once
   it has succeeded (it isn't ready for the moment between).

When images are published, `docker compose pull` takes the place of `build`. PostgreSQL stays on 17 in
`compose.yaml`; moving to another major version is a dump and a restore into a new volume.

## Run it without Compose

With your own PostgreSQL 17 (16 at least) with the pgvector extension, make the two roles as an
administrator (next section), then:

```sh
uv sync --group org
ROW_BOT_ORG_MIGRATION_DATABASE_URL=<owner's address> uv run row-bot-org migrate
ROW_BOT_ORG_DATABASE_URL=<app role's address> uv run row-bot-org check-config  # 0 when it would start
ROW_BOT_ORG_DATABASE_URL=<app role's address> uv run row-bot-org serve  # http://127.0.0.1:8100/readyz
```

The image runs the same commands (`docker build -f deploy/org/Dockerfile -t row-bot-org:dev .` from the
repository root; the settings below as variables, or as files in `/run/secrets`).

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
Compose does it that way (`initdb/10-row-bot-org.sql`): the owner has no `CREATEROLE`, and the group, the
membership, pgvector and the database's `CONNECT` rights are made by the superuser before `migrate` runs.

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
   `row-bot-org principals grant-admin <your email or principal id>` (with Compose:
   `docker compose run --rm migrate principals grant-admin ...`).

`row-bot-org principals revoke-admin <email or id>` takes the stored role away; it refuses to remove the last
stored admin while `admin_groups` is unset, so the server always keeps one. An email two people share is
refused: name one by its id (`/api/me` shows yours). Suspended admins don't count as the one kept. Compose's
`migrate` service has the owner's address and none of `org.env`, so there it always keeps the last stored
admin, `admin_groups` or not.

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

## Company brain

The server keeps the company's shared knowledge in **spaces**: one company space, read by everyone signed in,
and group spaces, read by the groups an admin names (`/api/admin/brain/spaces`). Each space has owners
(people or groups), who also read it, rename it, and add its documents and facts; admins can do that in any
space from a browser session.

- Documents are text (`.txt`), Markdown (`.md`) and Word (`.docx`) files, uploaded to
  `/api/brain/spaces/{id}/documents`. PDF isn't read yet. They are kept in the database (the image has no data
  folder), so size Postgres and its backups for them.
- `ROW_BOT_ORG_BRAIN_MAX_UPLOAD_MB`: the largest file, 1 to 100 MB, 25 by default.
- The server splits each document into parts and embeds them with the catalog's **default embeddings model**
  (Models), as model calls of the person who uploaded it: their usage and budgets count them. Without one,
  documents wait ("Add an embeddings model in Models") and are searched by their words only.
- Row-Bots ask with `POST /api/brain/recall`, as their person: answers come only from spaces that person reads,
  each with where it came from. An admin is a member there too.
- **The knowledge graph.** Once a document is ready, the server reads its first 40 parts, and each fact, with the
  catalog's **default chat model**, for the people, teams, projects, products, customers, systems, policies and
  terms they name and how these relate. These are model calls of the uploader (or the fact's writer), counted
  against their usage and budgets: about one call per part. **The uploader or writer pays for extraction,
  backfills included**: when a chat model is first added (or the default changes after a failure), every ready
  document and fact not yet read is read then, each billed to whoever uploaded or wrote it. Without a chat model
  documents wait ("Add a chat model in Models"); with a budget spent, or the model's endpoint failing, they wait
  and try again an hour later. A deleted document takes what only it mentioned out of the graph. Each space has its own graph, read
  by the space's readers (`GET /api/brain/graph`); owners correct it (rename, merge, retire, relate by hand) from a
  browser session.
- **In the admin console**, Knowledge lists the spaces (who reads and owns each, and its documents), and in a
  space its documents (upload, replace, delete, each one's status), facts and knowledge graph. A space's graph
  follows who reads it, admins included: an admin who doesn't read a group space makes themself an owner to
  see and correct it. Space owners who aren't admins can't open the console.

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
  `npm --prefix frontend run console:build`. The image carries the console and sets it
  (`/opt/row-bot-org/console`). The console needs sign-in too: without `ROW_BOT_ORG_IDP` the server starts
  and answers health checks, and nothing else. Unset, there is no console and
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

**How a Row-Bot joins.** In Row-Bot's settings the person enters the server's address. Row-Bot opens the system
browser at `/auth/device`; the person signs in with the company's identity provider as usual and sees "Connect
Row-Bot on <device> to <server>?". Connect registers the device (`device.joined`) and hands Row-Bot, through its
own loopback address on the person's computer (`http://127.0.0.1:<port>/...`, nothing else is accepted), a
one-time code that it trades for its own credential (OAuth's native-app flow with PKCE, RFC 8252). The browser
session plays no further part: Row-Bot's calls to the person's API, the models and audit carry the device's
bearer token. A device's token never opens an admin route or the console: administration stays in a browser.

**What stops a device.** Every request with a device's token checks that the device isn't revoked and its person
is active, so each takes effect on the device's next request:
- an admin revokes the device (above): it's done for good; the person joins again to get a new device;
- an admin suspends the person: all their devices stop, and start working again if they're resumed (a device
  whose refresh token lapsed meanwhile signs in again);
- the person leaves in Row-Bot (`POST /api/device/leave`): the device is revoked with its tokens
  (`device.removed`, reason `left`).

**Offboarding means suspending the person in Row-Bot too** (People and admins). Disabling them at the identity
provider alone stops their devices only when each next signs in through the browser, which happens within 30
days. Groups, too, are refreshed at each browser sign-in.

**Token lifetimes** (fixed in this version): an access token lasts 1 hour, and Row-Bot then rotates its refresh
token. A device's refresh tokens end 30 days after it last signed in through the browser, however much they're
used, so every device goes back through the company sign-in (and the identity provider) at least monthly. Signing
in again keeps the same device record: Row-Bot sends its device id, its old tokens end, and no new
`device.joined` is recorded. A refresh token presented a second time is treated as a copy and revokes every token
of its device (`device.credential_reused`), with one allowance: within 60 seconds of its use, and while nothing
issued from it has been used, the server assumes its answer was lost and issues a new pair instead. A one-time code
presented a second time revokes its device's tokens the same way. The server keeps only HMAC verifiers of codes
and tokens.

**Limits per client address** (`ROW_BOT_ORG_SIGN_IN_RATE_LIMIT` a minute, each counted apart from sign-in's):
`/auth/device` counts every request; `/api/device/token` counts only refused ones, so an office of Row-Bots
refreshing behind one address is never held up, while an address that keeps presenting bad codes or tokens waits
out the minute.
