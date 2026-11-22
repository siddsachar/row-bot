# Row-Bot Org server (pre-release)

**Pre-release.** Row-Bot Org will hold a team's and a company's shared brain. This version only starts
and answers health checks: it stores nothing and has no sign-in yet. Don't run it for real data.

It is a separate deployment from the Row-Bot app. The desktop app never loads it, and desktop
installers and updates don't include it.

## Run it

```sh
uv sync --extra org
uv run row-bot-org check-config   # exit 0 when the settings are valid, 1 when not
uv run row-bot-org serve          # http://127.0.0.1:8100/healthz and /readyz
```

In a container (uid 10001, read-only root, all capabilities dropped), from the repository root:
`docker compose -f deploy/org/compose.yaml up --build`.

## Settings

Each is an environment variable, or a file of the same name in `ROW_BOT_SECRETS_DIR`; a variable wins
over a file. Any other `ROW_BOT_ORG_*` variable stops the server from starting.

- `ROW_BOT_ORG_HOST`: the address to listen on, `127.0.0.1` by default (`0.0.0.0` in the image).
- `ROW_BOT_ORG_PORT`: `8100` by default.
- `ROW_BOT_ORG_LOG_LEVEL`: `DEBUG`, `INFO` (default), `WARNING` or `ERROR`. Logs are JSON lines on stdout.
- `ROW_BOT_ORG_OPENAPI`: `1` publishes `/openapi.json`. Off by default. There is no docs page.
