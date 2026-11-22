# Row-Bot Org server (pre-release)

**Pre-release.** Row-Bot Org will hold a team's and a company's shared brain. This version starts,
checks its database and answers health checks: it stores nothing yet and has no sign-in. Don't run it
for real data.

It is a separate deployment from the Row-Bot app. The desktop app never loads it, and desktop
installers and updates don't include it.

## Run it

It needs PostgreSQL 17 with the pgvector extension, and two roles (below).

```sh
uv sync --group org
ROW_BOT_ORG_DATABASE_URL=<migrating role's address> uv run row-bot-org migrate
uv run row-bot-org check-config   # 0 when the settings work and the database is reachable, 1 when not
uv run row-bot-org serve          # http://127.0.0.1:8100/healthz and /readyz
```

In a container (uid 10001, read-only root, all capabilities dropped), from the repository root: put the
runtime role's address in `deploy/org/secrets/ROW_BOT_ORG_DATABASE_URL` (ignored by git, readable by
uid 10001), then `docker compose -f deploy/org/compose.yaml up --build`.

## The database and its two roles

The server's tables use row-level security, so one person's rows stay theirs even if a query forgets
its `WHERE`. That only holds for a role that isn't a superuser, doesn't own the tables and hasn't
`BYPASSRLS`, so the server runs as one, and a second role migrates and owns. As an administrator:

```sql
CREATE ROLE row_bot_org_owner LOGIN PASSWORD '...';              -- migrates; owns the schema and tables
CREATE DATABASE row_bot_org OWNER row_bot_org_owner;
\c row_bot_org
CREATE EXTENSION vector;                                          -- pgvector needs an administrator
CREATE ROLE row_bot_org_runtime NOLOGIN;                          -- what the server's grants go to
CREATE ROLE row_bot_org_app LOGIN PASSWORD '...' IN ROLE row_bot_org_runtime;  -- what serve connects as
```

`row-bot-org migrate`, run with the owner's address, makes the schema `row_bot_org`, grants
`row_bot_org_runtime` reading and writing rows (no more). It creates that group itself when it doesn't
exist yet and the owner has `CREATEROLE`; otherwise make it first, as above. Serve
with the app role's address. `check-config` warns when the address it's given is a superuser, has
`BYPASSRLS`, or owns the schema or its tables. Keep one Org database per cluster: the group role's name
is fixed.

Migrations only go forward. `migrate` takes a lock, so two at once are safe, and it refuses a database
whose applied migrations changed, or that a newer server migrated. `serve` refuses to start while a
migration is pending, unless `ROW_BOT_ORG_AUTO_MIGRATE=1` (then it migrates with its own address, which
is only right when one role does both, and `check-config` will warn about it).

## Settings

Each is an environment variable, or a file of the same name in `ROW_BOT_SECRETS_DIR`; a variable wins
over a file. Any other `ROW_BOT_ORG_*` variable stops the server from starting.

- `ROW_BOT_ORG_DATABASE_URL`: the database address, `postgresql://...`. Required. A secret: the server
  takes it out of its environment once read, and no message or log shows any part of it.
- `ROW_BOT_ORG_AUTO_MIGRATE`: `1` lets `serve` apply pending migrations. Off by default.
- `ROW_BOT_ORG_HOST`: the address to listen on, `127.0.0.1` by default (`0.0.0.0` in the image).
- `ROW_BOT_ORG_PORT`: `8100` by default.
- `ROW_BOT_ORG_LOG_LEVEL`: `DEBUG`, `INFO` (default), `WARNING` or `ERROR`. Logs are JSON lines on stdout.
- `ROW_BOT_ORG_OPENAPI`: `1` publishes `/openapi.json`. Off by default. There is no docs page.

`/readyz` answers `ready` once the database is reachable and no migration is pending, and only says
`ok` or `failing` for it, never where it is.
