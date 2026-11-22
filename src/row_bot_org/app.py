"""The Org server's ASGI application: ``create_app(settings)``."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from row_bot_org.health import Readiness, health_router
from row_bot_org.settings import OrgSettings

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

# The server answers JSON to programs, never pages to browsers.
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


class _SecurityHeaders:
    """Adds ``SECURITY_HEADERS`` to every HTTP response, errors and 404s included."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    headers[name] = value
            await send(message)

        await self.app(scope, receive, send_with_headers)


def create_app(settings: OrgSettings, pool: ConnectionPool | None = None) -> FastAPI:
    """The server for ``settings``: health and readiness only, so far. Any other path is a JSON 404.

    With ``pool`` (``row-bot-org serve`` always passes one), the app opens it at start and closes it
    at shutdown, and ``/readyz`` checks the database: reachable, and no migration pending.
    """

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if pool is None:
            yield
            return
        # Not waiting: a database that is down makes the server not ready, not stop.
        pool.open(wait=False)
        try:
            yield
        finally:
            pool.close()

    app = FastAPI(
        title="Row-Bot Org",
        docs_url=None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.openapi else None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.readiness = Readiness()
    if pool is not None:
        from row_bot_org.db.checks import database_readiness
        from row_bot_org.db.migrations import load_migrations

        app.state.pool = pool
        app.state.readiness.add("database", database_readiness(pool, load_migrations()))
    app.include_router(health_router(app.state.readiness))
    app.add_middleware(_SecurityHeaders)
    return app


__all__ = ["SECURITY_HEADERS", "create_app"]
