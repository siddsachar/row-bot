"""The Org server's ASGI application: ``create_app(settings)``."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from row_bot_org.health import Readiness, health_router
from row_bot_org.identity.gate import OPEN_PATHS, AccessLog, Gate
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


async def _invalid_request(_request: Request, error: Exception) -> JSONResponse:
    """FastAPI's 422 without what it echoes of the body (``input``, ``ctx``, an unexpected field's name): a
    refused body may carry a key."""
    errors = error.errors() if isinstance(error, RequestValidationError) else []
    detail = []
    for e in errors:
        loc = list(e.get("loc", ()))
        if e.get("type") == "extra_forbidden" and loc:
            loc[-1] = "(unexpected field)"
        detail.append({"loc": loc, "msg": e.get("msg", ""), "type": e.get("type", "")})
    return JSONResponse({"detail": detail}, status_code=422)


def create_app(settings: OrgSettings, pool: ConnectionPool | None = None) -> FastAPI:
    """The server for ``settings``: health, readiness and, with identity providers, sign-in.

    With ``pool`` (``row-bot-org serve`` always passes one), the app opens it at start and closes it
    at shutdown, and ``/readyz`` checks the database: reachable, and no migration pending. With a pool
    and ``settings.idp`` it serves sign-in (``row_bot_org.identity``), and with ``settings.gateway_key`` too
    the model catalog and gateway (``row_bot_org.gateway``). Every other path needs a server
    session (``identity.gate``): 401 without one, then the route or a JSON 404.

    ``X-Forwarded-For`` and ``X-Forwarded-Proto`` count only from ``settings.trusted_proxies`` (uvicorn's
    own proxy-header handling is off in ``serve``); from any other peer they are ignored.
    """
    service = None
    if pool is not None and settings.idp:
        from row_bot_org.identity.web import IdentityService

        service = IdentityService(settings, pool)
    gateway = None
    if service is not None and settings.gateway_key is not None:
        from row_bot_org.gateway.web import GatewayService

        gateway = GatewayService(settings, pool)

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
            if service is not None:
                service.close()
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
    app.add_exception_handler(RequestValidationError, _invalid_request)
    if pool is not None:
        from row_bot_org.db.checks import database_readiness
        from row_bot_org.db.migrations import load_migrations

        app.state.pool = pool
        app.state.readiness.add("database", database_readiness(pool, load_migrations()))
    routers = [health_router(app.state.readiness)]
    if service is not None:
        from row_bot_org.identity.web import identity_router

        app.state.identity = service
        routers.append(identity_router(service))
    if gateway is not None:
        from row_bot_org.gateway.web import gateway_router

        routers.append(gateway_router(gateway))
    for router in routers:
        app.include_router(router)
    open_paths = OPEN_PATHS | ({"/openapi.json"} if settings.openapi else set())
    # Outermost last: the headers on every response, the access log of every request (refusals
    # included), then the gate. The log matches the routers' own routes: the app's are wrapped.
    app.add_middleware(Gate, open_paths=open_paths, origin=settings.public_url,
                       resolve=service.resolve if service is not None else None)
    app.add_middleware(AccessLog, routes=[route for router in routers for route in router.routes])
    app.add_middleware(_SecurityHeaders)
    if settings.trusted_proxies:
        # Outermost: the log, the sign-in limit and the session's address see the forwarded client.
        app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=list(settings.trusted_proxies))
    return app


__all__ = ["SECURITY_HEADERS", "create_app"]
