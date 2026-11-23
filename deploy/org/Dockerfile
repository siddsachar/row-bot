# syntax=docker/dockerfile:1.7
# Row-Bot Org server (pre-release). Build from the repository root:
#   docker build -f deploy/org/Dockerfile -t row-bot-org:dev .

ARG PYTHON_IMAGE=python:3.13.5-slim-bookworm
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.7.22

FROM ${UV_IMAGE} AS uv

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

# Only the Org server's own package goes into the image, with the shared policy model (standard library
# only) and its package marker; never the rest of the desktop app.
COPY src/row_bot_org /opt/row-bot-org/src/row_bot_org
COPY src/row_bot/__init__.py /opt/row-bot-org/src/row_bot/__init__.py
COPY src/row_bot/policy /opt/row-bot-org/src/row_bot/policy
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
    ROW_BOT_SECRETS_DIR=/run/secrets

COPY --from=builder /opt/row-bot-org /opt/row-bot-org

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
