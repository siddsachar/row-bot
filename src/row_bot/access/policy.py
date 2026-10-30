"""Transport-neutral route authorization policy for the single owner."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Mapping
from row_bot.access.request_context import (
    ACCESS_CONTEXT_SCOPE_KEY,
    AccessContext,
)


class RouteKind(StrEnum):
    PUBLIC = "public"
    AUTHENTICATED = "authenticated"
    DELEGATED = "delegated"
    LAUNCHER = "launcher"


@dataclass(frozen=True, slots=True)
class RouteClassification:
    kind: RouteKind
    browser_navigation: bool = False
    require_same_origin: bool = False
    # A change: on this computer only a person's window or browser may make it.
    changes_state: bool = False


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    allowed: bool
    status_code: int
    reason: str


PUBLIC_HTTP_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/connect"),
        ("POST", "/api/access/invitations/claim"),
        # The first sign-in with a setup code, and recovery (H1).
        ("GET", "/setup"),
        ("POST", "/api/access/setup"),
        ("GET", "/api/access/session"),
        ("GET", "/healthz"),
        ("GET", "/readyz"),
        # Legacy mobile/PWA compatibility. None of these routes grants a session.
        ("GET", "/mobile/pair"),
        ("POST", "/api/mobile/pair/confirm"),
        ("GET", "/api/mobile/session"),
        ("GET", "/mobile/manifest.webmanifest"),
        ("GET", "/mobile/offline"),
        ("GET", "/mobile/service-worker.js"),
        ("GET", "/static/row_bot_glyph_256.png"),
    }
)
# Callers with no Row-Bot session that prove themselves to the route instead:
# Twilio signs every inbound SMS, each plugin webhook handler checks its
# service's own credentials, and an OAuth callback (MCP, Google, X) brings
# the one-time state of the sign-in waiting for it. The plugin id and name
# follow plugins.webhooks.
OAUTH_CALLBACK_PATHS: tuple[str, ...] = (
    "/api/v1/settings/mcp/auth/callback",
    "/api/v1/accounts/google/auth/callback",
    "/api/v1/accounts/x/auth/callback",
)
ROUTE_AUTHENTICATED_HTTP_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {("POST", "/sms"), *(("GET", path) for path in OAUTH_CALLBACK_PATHS)}
)
# Requests that change nothing although they are not GETs, so a view-only caller on this
# computer may make them: starting a client session, following a conversation live, and
# the native window's helper, whose routes each check the one-time token or grant that
# only a person's window receives.
_VIEW_ONLY_CHANGES = re.compile(
    r"/api/v1/(?:handshake"
    r"|conversations/[^/]+/subscriptions"
    r"|subscriptions/[^/]+(?:/ack)?"
    r"|native/(?:attest|authorize|revoke|selections/complete|terminal/open|terminal/external"
    r"|attachments/[^/]+))"
)
PLUGIN_WEBHOOK_PATH = re.compile(
    r"/plugin-webhooks/[a-z][a-z0-9\-]{1,63}/[a-z0-9][a-z0-9_-]{0,63}"
)
LAUNCHER_ONLY_PATHS = frozenset(
    {
        "/api/launcher-ping",
        "/api/launcher-shutdown",
        "/api/launcher-restart",
        "/api/launcher-apply-update",
        "/api/startup-state",
    }
)
AUTHENTICATED_ROUTE_PREFIXES: tuple[str, ...] = (
    "/api/access/status",
    "/api/access/invitations",
    "/api/access/devices",
    "/api/access/routes",
    "/api/access/diagnostics",
    "/api/mobile/pair/start",
    "/api/mobile/devices",
    "/api/mobile/access-events",
    "/api/providers",
    "/api/channels",
    "/api/plugins",
    "/api/mcp",
    "/api/developer",
    "/api/designer",
    "/api/shell",
    "/api/terminal",
)


def _method(scope: Mapping[str, object]) -> str:
    return str(scope.get("method") or "GET").upper()


def _path(scope: Mapping[str, object]) -> str:
    path = str(scope.get("path") or "/")
    return path if path.startswith("/") else "/"


def _header_values(scope: Mapping[str, object], name: bytes) -> tuple[bytes, ...]:
    return tuple(
        bytes(value)
        for raw_name, value in scope.get("headers", []) or []
        if bytes(raw_name).lower() == name
    )


def is_browser_navigation(scope: Mapping[str, object]) -> bool:
    if scope.get("type") != "http" or _method(scope) not in {"GET", "HEAD"}:
        return False
    path = _path(scope)
    if path.startswith(("/api/", "/published/")):
        return False
    accept = b",".join(_header_values(scope, b"accept")).decode(
        "latin-1", errors="ignore"
    )
    return "text/html" in accept.lower() or path == "/"


def is_safe_method(method: object) -> bool:
    return str(method or "").upper() in {"GET", "HEAD", "OPTIONS", "TRACE"}


class AccessPolicy:
    """Central route policy shared by HTTP and WebSockets."""

    def classify(self, scope: Mapping[str, object]) -> RouteClassification:
        scope_type = str(scope.get("type") or "")
        path = _path(scope)
        method = _method(scope)
        if scope_type == "websocket":
            return RouteClassification(
                RouteKind.AUTHENTICATED,
                browser_navigation=False,
                require_same_origin=True,
            )
        if scope_type != "http":
            return RouteClassification(RouteKind.PUBLIC)
        if path in LAUNCHER_ONLY_PATHS or path.startswith("/api/launcher/"):
            return RouteClassification(RouteKind.LAUNCHER)
        if path.startswith("/api/webhook/"):
            # Webhook task secrets remain authoritative at the route itself.
            return RouteClassification(RouteKind.DELEGATED)
        if (method, path) in ROUTE_AUTHENTICATED_HTTP_ROUTES or PLUGIN_WEBHOOK_PATH.fullmatch(path):
            return RouteClassification(RouteKind.DELEGATED)
        if (method, path) in PUBLIC_HTTP_ROUTES:
            return RouteClassification(
                RouteKind.PUBLIC,
                require_same_origin=(method == "POST"),
            )
        changes_state = not is_safe_method(method) and not _VIEW_ONLY_CHANGES.fullmatch(path)
        for prefix in AUTHENTICATED_ROUTE_PREFIXES:
            if path == prefix or path.startswith(f"{prefix}/"):
                return RouteClassification(
                    RouteKind.AUTHENTICATED,
                    require_same_origin=not is_safe_method(method),
                    changes_state=changes_state,
                )
        return RouteClassification(
            RouteKind.AUTHENTICATED,
            browser_navigation=is_browser_navigation(scope),
            require_same_origin=not is_safe_method(method),
            changes_state=changes_state,
        )

    def authorize(
        self,
        context: AccessContext,
        classification: RouteClassification,
    ) -> AuthorizationDecision:
        if classification.kind in {RouteKind.PUBLIC, RouteKind.DELEGATED}:
            return AuthorizationDecision(True, 200, "allowed")
        if classification.kind is RouteKind.LAUNCHER:
            if context.direct_loopback and not context.forwarding_headers:
                return AuthorizationDecision(True, 200, "local_launcher")
            return AuthorizationDecision(False, 403, "launcher_local_only")
        if not context.authenticated:
            return AuthorizationDecision(False, 401, "authentication_required")
        if classification.changes_state and not context.may_act:
            # Being on this computer is not enough to change anything: a program Row-Bot
            # started, or its own browser, could otherwise approve Row-Bot's own actions.
            return AuthorizationDecision(False, 403, "person_required")
        return AuthorizationDecision(True, 200, "allowed")


def context_from_scope(scope: Mapping[str, object]) -> AccessContext | None:
    value = scope.get(ACCESS_CONTEXT_SCOPE_KEY)
    return value if isinstance(value, AccessContext) else None


def require_authenticated_owner(
    context_or_scope: AccessContext | Mapping[str, object],
) -> AccessContext:
    """Return the authenticated owner context or raise ``PermissionError``."""

    context = (
        context_or_scope
        if isinstance(context_or_scope, AccessContext)
        else context_from_scope(context_or_scope)
    )
    if context is None or not context.authenticated:
        raise PermissionError("authentication_required")
    return context
