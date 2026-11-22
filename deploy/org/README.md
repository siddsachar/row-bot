# Row-Bot Org server (pre-release)

**Pre-release.** Row-Bot Org will hold a team's and a company's shared brain. This version starts,
checks its database and answers health checks: it stores nothing yet and has no sign-in. Don't run it
for real data.

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

`serve` and `check-config` refuse a role that is a superuser, has `BYPASSRLS`, or can `SET ROLE` to one
that is, and the server checks again on every new database connection and every `/readyz`. Owning the
schema or its tables is a warning: forced row-level security still applies to the owner, but an owner
could turn it off.

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
