"""The gate every request passes, the access log, and ``request_principal`` for the routes behind the gate.

- **Same origin for anything that changes state.** A request that isn't GET, HEAD or OPTIONS needs an
  ``Origin`` equal to the server's public origin, or, without one, ``Sec-Fetch-Site: same-origin``.
  Otherwise 403, before anything else runs. Two exceptions carry no ambient credential, so no other site
  can borrow one: the device token endpoint (``CREDENTIAL_PATHS``), and a request with a device's bearer
  token (below).
- **A device's bearer token** (``Authorization: Bearer rboa_...``, ``identity.device``) authenticates the
  person's API (``/api/...``) but never an admin route (``/api/admin/...``), the console or a browser step
  (``/auth/...``): those answer 403 ``browser_session_required``. A request with both a session cookie and an
  ``Authorization`` header is refused (400 ``invalid_request``); a bearer that isn't live is 401
  ``invalid_token`` with ``WWW-Authenticate: Bearer error="invalid_token"``, so the app knows to refresh.
- **Deny by default.** Only health, readiness, the two sign-in steps (and ``/openapi.json`` when it is
  published) are open. Any other path, a route that doesn't exist included, needs a live server
  session: 401 without one, so nobody learns which routes exist without signing in.
  The one exception is the admin console's pages (``pages``, when the console is served): a browser
  opening one without a session is sent to sign in (``/auth/login?next=<the path>``) instead.
- **The access log (F-3):** one JSON line per request with the method, the route's template (never the
  path as sent, never the query string), the status, the duration and the principal's id. No cookie,
  code, state or token reaches it.

Nothing here imports the database or JOSE libraries: the gate also runs (refusing everything behind
it) on a server with no sign-in configured.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import quote

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.requests import cookie_parser
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import BaseRoute, Match
from starlette.types import ASGIApp, Message, Receive, Scope, Send

SESSION_COOKIE = "__Host-row_bot_org_session"
SIGN_IN_COOKIE = "__Host-row_bot_org_sign_in"
# The device sign-in's start and its stylesheet check the session themselves (identity.device).
OPEN_PATHS = frozenset({"/healthz", "/readyz", "/auth/login", "/auth/callback", "/auth/device",
                        "/auth/device.css", "/api/device/token"})
# Open, and outside the same-origin rule: a request here carries no cookie's authority (identity.device).
CREDENTIAL_PATHS = frozenset({"/api/device/token"})
API_PREFIX = "/api/"
ADMIN_API_PREFIX = "/api/admin/"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_SCOPE_KEY = "row_bot_org.principal"
access_logger = logging.getLogger("row_bot_org.access")


@dataclass(frozen=True)
class RequestPrincipal:
    """Who is calling: a signed-in principal, its groups (the server's copy), and either its browser session or,
    for a device's bearer token, its device."""

    principal_id: str
    groups: tuple[str, ...]
    session_id: str | None
    device_id: str | None = None


# From a session cookie's value (or a bearer token) to its principal, or None (runs in a worker thread: it reads
# the database).
SessionResolver = Callable[[str], RequestPrincipal | None]


def _header(scope: Scope, name: bytes) -> list[str]:
    return [value.decode("latin-1") for key, value in scope.get("headers", []) if key.lower() == name]


def same_origin(scope: Scope, origin: str | None) -> bool:
    """Whether the browser says this request comes from ``origin`` (the server's public origin)."""
    if origin is None:
        return False
    origins = _header(scope, b"origin")
    sites = _header(scope, b"sec-fetch-site")
    if len(origins) > 1 or len(sites) > 1:
        return False
    if sites and sites[0].strip().lower() != "same-origin":
        return False
    if origins:
        return origins[0].strip() == origin
    return bool(sites)


def session_cookie(scope: Scope) -> str | None:
    values = [cookie_parser(header).get(SESSION_COOKIE) for header in _header(scope, b"cookie")]
    found = [value for value in values if value]
    return found[0] if len(found) == 1 else None


def has_session_cookie(scope: Scope) -> bool:
    """Whether the request carries a session cookie at all (one, several, or an empty one)."""
    return any(SESSION_COOKIE in cookie_parser(header) for header in _header(scope, b"cookie"))


def bearer_token(scope: Scope) -> str | None:
    """The token of the one ``Authorization: Bearer <token>`` header, or None (no header, several, or another
    scheme)."""
    values = _header(scope, b"authorization")
    if len(values) != 1:
        return None
    scheme, _, token = values[0].strip().partition(" ")
    token = token.strip()
    return token if scheme.lower() == "bearer" and token and " " not in token else None


def accepts_bearer(path: str) -> bool:
    """The person's API takes a device's bearer token; admin routes, the console and the browser steps never do."""
    return path.startswith(API_PREFIX) and not path.startswith(ADMIN_API_PREFIX)


def _refuse(status: int, error: str, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse({"error": error}, status_code=status, headers=headers)


def _bearer_refusal(status: int, error: str) -> JSONResponse:
    return _refuse(status, error, {"WWW-Authenticate": f'Bearer error="{error}"'})


class Gate:
    """Pure ASGI middleware: the same-origin rule, then deny by default (see the module docstring)."""

    def __init__(self, app: ASGIApp, *, open_paths: frozenset[str], origin: str | None,
                 resolve: SessionResolver | None, pages: Callable[[str], bool] | None = None,
                 resolve_bearer: SessionResolver | None = None) -> None:
        self.app = app
        self.open_paths = open_paths
        self.origin = origin
        self.resolve = resolve
        # A device's access token to its principal (identity.device), when the server has sign-in.
        self.resolve_bearer = resolve_bearer
        # Paths a browser opens as a page, which send someone not signed in to sign in rather than 401.
        self.pages = pages

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        if path not in self.open_paths and _header(scope, b"authorization"):
            await self._with_bearer(scope, receive, send)
            return
        if (scope["method"] not in SAFE_METHODS and path not in CREDENTIAL_PATHS
                and not same_origin(scope, self.origin)):
            await _refuse(403, "same_origin_required")(scope, receive, send)
            return
        if path not in self.open_paths:
            token = session_cookie(scope)
            principal = None
            if token is not None and self.resolve is not None:
                principal = await run_in_threadpool(self.resolve, token)
            if principal is None:
                if scope["method"] in ("GET", "HEAD") and self.pages is not None and self.pages(scope["path"]):
                    path = quote(scope["path"], safe="/")
                    await RedirectResponse("/auth/login?next=" + quote(path, safe=""), status_code=303)(
                        scope, receive, send)
                    return
                await _refuse(401, "sign_in_required")(scope, receive, send)
                return
            scope[_SCOPE_KEY] = principal
        await self.app(scope, receive, send)

    async def _with_bearer(self, scope: Scope, receive: Receive, send: Send) -> None:
        """A request with an ``Authorization`` header: a device's bearer, never with a cookie, only on the
        person's API. It carries no ambient credential, so the same-origin rule doesn't apply."""
        if has_session_cookie(scope):
            await _bearer_refusal(400, "invalid_request")(scope, receive, send)
            return
        if not accepts_bearer(scope["path"]):
            await _refuse(403, "browser_session_required")(scope, receive, send)
            return
        token = bearer_token(scope)
        principal = None
        if token is not None and self.resolve_bearer is not None:
            principal = await run_in_threadpool(self.resolve_bearer, token)
        if principal is None:
            await _bearer_refusal(401, "invalid_token")(scope, receive, send)
            return
        scope[_SCOPE_KEY] = principal
        await self.app(scope, receive, send)


def request_principal(request: Request) -> RequestPrincipal:
    """The signed-in caller. Every route behind the gate depends on this (or on ``principal_connection``)."""
    principal = request.scope.get(_SCOPE_KEY)
    if not isinstance(principal, RequestPrincipal):
        raise HTTPException(status_code=401, detail="sign_in_required")
    return principal


def route_template(routes: list[BaseRoute], scope: Scope) -> str:
    """The template of the route ``scope`` is for (``/auth/callback``), or ``unmatched``: never the raw path."""
    for route in routes:
        match, _ = route.matches(scope)
        if match is not Match.NONE:
            return getattr(route, "path", "unmatched")
    return "unmatched"


class AccessLog:
    """One ``row_bot_org.access`` record per HTTP request; outside the gate, so refusals are logged too."""

    def __init__(self, app: ASGIApp, *, routes: list[BaseRoute]) -> None:
        self.app = app
        self.routes = routes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        status = 500
        # Matched now, before routing adds its own keys to the scope.
        route = route_template(self.routes, scope)

        async def send_and_note(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_and_note)
        finally:
            principal = scope.get(_SCOPE_KEY)
            access_logger.info("request", extra={"fields": {
                "method": scope["method"],
                "route": route,
                "status": status,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                "principal": principal.principal_id if isinstance(principal, RequestPrincipal) else None,
            }})


__all__ = ["ADMIN_API_PREFIX", "API_PREFIX", "CREDENTIAL_PATHS", "OPEN_PATHS", "SESSION_COOKIE", "SIGN_IN_COOKIE",
           "AccessLog", "Gate", "RequestPrincipal", "SessionResolver", "accepts_bearer", "bearer_token",
           "has_session_cookie", "request_principal", "route_template", "same_origin", "session_cookie"]
