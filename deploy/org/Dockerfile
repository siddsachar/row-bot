# syntax=docker/dockerfile:1.7
# Row-Bot Org server (pre-release), with its admin console. Build from the repository root:
#   docker build -f deploy/org/Dockerfile -t row-bot-org:dev .
# (deploy/org/compose.yaml builds it too.)

ARG PYTHON_IMAGE=python:3.13.5-slim-bookworm
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.7.22
ARG CONSOLE_NODE_IMAGE=node:24.15.0-bookworm-slim

FROM ${UV_IMAGE} AS uv

# The admin console (frontend/src/console, vite.console.config.ts). Only its built files leave this stage;
# the app's own client is never built or copied.
FROM ${CONSOLE_NODE_IMAGE} AS console-build

WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json frontend/.npmrc ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --ignore-scripts --no-audit --no-fund

COPY frontend ./
# The console reads the policy's settings and defaults from the published schema.
COPY contracts/policy/v1/CompanyPolicy.schema.json /build/contracts/policy/v1/CompanyPolicy.schema.json
RUN npm run console:build

FROM ${PYTHON_IMAGE} AS builder

COPY --from=uv /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/row-bot-org/.venv

WORKDIR /build

# The locked core dependencies and the `org` group; not `all`, and not the desktop app's package.
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY scripts/client_build.py ./scripts/client_build.py
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --group org --no-install-project

# Only the Org server's own package goes into the image, with the shared policy and audit models (standard library
# only) and its package marker; never the rest of the desktop app.
COPY src/row_bot_org /opt/row-bot-org/src/row_bot_org
COPY src/row_bot/__init__.py /opt/row-bot-org/src/row_bot/__init__.py
COPY src/row_bot/policy /opt/row-bot-org/src/row_bot/policy
COPY src/row_bot/audit /opt/row-bot-org/src/row_bot/audit
RUN /opt/row-bot-org/.venv/bin/python -m compileall -q /opt/row-bot-org/src

FROM ${PYTHON_IMAGE} AS runtime

ARG ROW_BOT_ORG_UID=10001
ARG ROW_BOT_ORG_GID=10001
ARG ROW_BOT_SOURCE_REVISION=unknown

LABEL org.opencontainers.image.title="Row-Bot Org server" \
      org.opencontainers.image.description="Row-Bot Org: the team and company brain server (pre-release)" \
      org.opencontainers.image.source="https://github.com/siddsachar/row-bot" \
      org.opencontainers.image.revision="${ROW_BOT_SOURCE_REVISION}"

ENV PATH=/opt/row-bot-org/.venv/bin:$PATH \
    PYTHONPATH=/opt/row-bot-org/src \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ROW_BOT_ORG_HOST=0.0.0.0 \
    ROW_BOT_ORG_PORT=8100 \
    ROW_BOT_ORG_CONSOLE_DIR=/opt/row-bot-org/console \
    ROW_BOT_SECRETS_DIR=/run/secrets

COPY --from=builder /opt/row-bot-org /opt/row-bot-org
# The built admin console only (ROW_BOT_ORG_CONSOLE_DIR).
COPY --from=console-build /build/frontend/dist-console /opt/row-bot-org/console

RUN groupadd --gid "${ROW_BOT_ORG_GID}" rowbot \
    && useradd --uid "${ROW_BOT_ORG_UID}" --gid "${ROW_BOT_ORG_GID}" \
        --no-create-home --shell /usr/sbin/nologin rowbot

WORKDIR /opt/row-bot-org
USER 10001:10001
EXPOSE 8100

# `env -i` gives the check none of the container's environment, secrets included.
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=5 \
    CMD ["env", "-i", "/opt/row-bot-org/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8100/healthz', timeout=2).read()"]

ENTRYPOINT ["python", "-m", "row_bot_org.cli"]
CMD ["serve"]
