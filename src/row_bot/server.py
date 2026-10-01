"""The one ASGI application: FastAPI, served by uvicorn.

``row_bot.app`` wires the routes, middleware and the start-up and shutdown
sequence into it. Channels and plugins that add a route after start-up (the
SMS webhook, plugin webhooks) import only this module, not the whole wiring.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

_startup: list[Callable[[], Awaitable[None]]] = []
_shutdown: list[Callable[[], Awaitable[None]]] = []


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    for handler in _startup:
        await handler()
    try:
        yield
    finally:
        for handler in _shutdown:
            await handler()


app = FastAPI(lifespan=_lifespan, openapi_url=None, docs_url=None, redoc_url=None)


def on_startup(handler: Callable[[], Awaitable[None]]) -> Callable[[], Awaitable[None]]:
    """Run *handler* when the server starts, before it accepts requests."""
    _startup.append(handler)
    return handler


def on_shutdown(handler: Callable[[], Awaitable[None]]) -> Callable[[], Awaitable[None]]:
    """Run *handler* when the server stops (after the routers' own lifespans)."""
    _shutdown.append(handler)
    return handler


def add_late_route(path: str, endpoint: Callable[..., Any], *, methods: Sequence[str]) -> None:
    """Add a route while the server runs (the SMS webhook, plugin webhooks)."""
    app.add_route(path, endpoint, methods=list(methods))
