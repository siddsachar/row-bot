"""Engine-bound network enforcement for Row-Bot's managed Browser.

This module is intentionally specific to the managed Playwright engine. It is
not a general network-policy language and does not claim to isolate native
Computer Use or arbitrary operating-system processes.
"""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import posixpath
import socket
import threading
import time
from typing import Any, Callable, Iterable
from urllib.parse import unquote, urlsplit
import uuid


Resolver = Callable[[str, int], Iterable[str]]
_SAFE_NON_NETWORK_SCHEMES = frozenset({"about", "blob", "data"})
_METADATA_HOSTS = frozenset(
    {
        "metadata",
        "metadata.aws.internal",
        "metadata.google.internal",
        "instance-data",
    }
)
_METADATA_ADDRESSES = frozenset(
    {
        "100.100.100.200",
        "168.63.129.16",
        "169.254.169.254",
        "169.254.170.2",
        "192.0.0.192",
        "fd00:ec2::254",
        "fd20:ce::254",
    }
)
_ROW_BOT_MANAGEMENT_PATHS = (
    "/connect",
    "/api/access",
    "/api/launcher",
    "/api/v1/native",
    "/api/v1/resources/folder-selection",
    "/api/v1/settings",
    "/launcher",
    "/mobile",
)
MAX_LOCAL_DEVELOPMENT_GRANT_SECONDS = 60 * 60


@dataclass(frozen=True, slots=True)
class NetworkDecision:
    allowed: bool
    code: str
    reason: str
    destinations: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class LocalDevelopmentGrant:
    id: str
    browser_session_id: str
    origin: str
    destinations: frozenset[str]
    intended_use: str
    expires_at: float


def _default_resolver(host: str, port: int) -> tuple[str, ...]:
    return tuple(
        str(record[4][0]).split("%", 1)[0]
        for record in socket.getaddrinfo(
            host,
            port,
            type=socket.SOCK_STREAM,
        )
    )


def _canonical_address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    address = ipaddress.ip_address(str(value).split("%", 1)[0])
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return address.ipv4_mapped
    return address


def _is_public(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(address.is_global)


def _is_metadata_address(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    return address.is_link_local or str(address) in _METADATA_ADDRESSES


def _origin_parts(url: str) -> tuple[str, str, int, str] | None:
    try:
        parsed = urlsplit(str(url))
        scheme = parsed.scheme.casefold()
        host = (parsed.hostname or "").rstrip(".").casefold()
        if scheme not in {"http", "https", "ws", "wss"} or not host:
            return None
        if parsed.username is not None or parsed.password is not None:
            return None
        port = parsed.port or (443 if scheme in {"https", "wss"} else 80)
        if not 1 <= port <= 65535:
            return None
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError:
            return None
        default_port = 443 if scheme in {"https", "wss"} else 80
        rendered_host = f"[{host}]" if ":" in host else host
        origin = f"{scheme}://{rendered_host}"
        if port != default_port:
            origin += f":{port}"
        return origin, host, port, parsed.path or "/"
    except (TypeError, ValueError):
        return None


class BrowserNetworkSecurity:
    """Validate every managed-engine request and own exact dev grants."""

    def __init__(
        self,
        *,
        resolver: Resolver | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._resolver = resolver or _default_resolver
        self._clock = clock or time.monotonic
        self._lock = threading.RLock()
        self._grants: dict[str, LocalDevelopmentGrant] = {}
        self._resolved_origins: dict[tuple[str, str], frozenset[str]] = {}

    def _resolve(self, host: str, port: int) -> frozenset[str]:
        try:
            literal = _canonical_address(host)
        except ValueError:
            values = self._resolver(host, port)
        else:
            values = (str(literal),)
        resolved: set[str] = set()
        for value in values:
            resolved.add(str(_canonical_address(value)))
        if not resolved:
            raise ValueError("host resolved to no addresses")
        return frozenset(resolved)

    @staticmethod
    def _metadata_host(host: str) -> bool:
        return host in _METADATA_HOSTS or host.endswith(".metadata.google.internal")

    @staticmethod
    def _management_path(path: str) -> bool:
        decoded = str(path or "/")
        for _ in range(3):
            expanded = unquote(decoded)
            if expanded == decoded:
                break
            decoded = expanded
        decoded = decoded.replace("\\", "/")
        normalized = posixpath.normpath("/" + decoded.lstrip("/")).casefold()
        return any(
            normalized == prefix or normalized.startswith(prefix + "/")
            for prefix in _ROW_BOT_MANAGEMENT_PATHS
        )

    def grant_local_development(
        self,
        *,
        browser_session_id: str,
        origin: str,
        intended_use: str,
        ttl_seconds: float,
    ) -> LocalDevelopmentGrant:
        """Grant one exact private origin and its current destination set."""

        identity = str(browser_session_id or "").strip()
        purpose = " ".join(str(intended_use or "").split())[:200]
        parts = _origin_parts(origin)
        if not identity or not purpose or parts is None:
            raise ValueError(
                "an exact origin, browser session, and intended use are required"
            )
        canonical_origin, host, port, path = parts
        if path != "/" or "?" in str(origin) or "#" in str(origin):
            raise ValueError("local-development grants require an exact origin")
        if self._metadata_host(host) or self._management_path(path):
            raise ValueError(
                "metadata and Row-Bot management targets cannot be granted"
            )
        # Chromium resolves hostnames again after the request interception
        # callback. Without a connection-pinning proxy, a private hostname
        # could therefore rebind between this policy check and the socket.
        # Keep the explicit local-development path useful and auditable by
        # requiring its exact destination to be a literal IP address.
        try:
            _canonical_address(host)
        except ValueError as exc:
            raise ValueError(
                "local-development grants require a literal IP origin"
            ) from exc
        lifetime = float(ttl_seconds)
        if lifetime <= 0 or lifetime > MAX_LOCAL_DEVELOPMENT_GRANT_SECONDS:
            raise ValueError("local-development grant lifetime is out of bounds")
        try:
            destinations = self._resolve(host, port)
            addresses = tuple(_canonical_address(value) for value in destinations)
        except (OSError, ValueError) as exc:
            raise ValueError("local-development origin could not be resolved") from exc
        if any(_is_metadata_address(address) for address in addresses):
            raise ValueError("metadata destinations cannot be granted")
        if all(_is_public(address) for address in addresses):
            raise ValueError(
                "local-development grants are only for restricted destinations"
            )
        grant = LocalDevelopmentGrant(
            id=uuid.uuid4().hex,
            browser_session_id=identity,
            origin=canonical_origin,
            destinations=destinations,
            intended_use=purpose,
            expires_at=self._clock() + lifetime,
        )
        with self._lock:
            self._grants[grant.id] = grant
            self._resolved_origins[(identity, canonical_origin)] = destinations
        return grant

    def revoke_grant(self, grant_id: str) -> bool:
        with self._lock:
            return self._grants.pop(str(grant_id), None) is not None

    def revoke_session(self, browser_session_id: str) -> int:
        identity = str(browser_session_id)
        with self._lock:
            ids = [
                grant_id
                for grant_id, grant in self._grants.items()
                if grant.browser_session_id == identity
            ]
            for grant_id in ids:
                self._grants.pop(grant_id, None)
            for key in tuple(self._resolved_origins):
                if key[0] == identity:
                    self._resolved_origins.pop(key, None)
            return len(ids)

    def _matching_grant(
        self,
        *,
        browser_session_id: str,
        origin: str,
        destinations: frozenset[str],
        now: float,
    ) -> bool:
        expired: list[str] = []
        matched = False
        with self._lock:
            for grant_id, grant in self._grants.items():
                if grant.expires_at <= now:
                    expired.append(grant_id)
                    continue
                if (
                    grant.browser_session_id == browser_session_id
                    and grant.origin == origin
                    and grant.destinations == destinations
                ):
                    matched = True
            for grant_id in expired:
                self._grants.pop(grant_id, None)
        return matched

    def authorize(
        self,
        url: str,
        *,
        browser_session_id: str,
        method: str = "GET",
    ) -> NetworkDecision:
        """Authorize one actual request URL at the engine interception point."""

        try:
            scheme = urlsplit(str(url)).scheme.casefold()
        except Exception:
            scheme = ""
        if scheme in _SAFE_NON_NETWORK_SCHEMES:
            return NetworkDecision(True, "non_network", "Non-network browser URL.")
        parts = _origin_parts(url)
        if parts is None:
            return NetworkDecision(
                False,
                "invalid_url",
                "Only explicit HTTP(S) and WebSocket URLs without credentials are allowed.",
            )
        origin, host, port, path = parts
        if self._metadata_host(host):
            return NetworkDecision(
                False, "metadata_blocked", "Cloud metadata targets are unavailable."
            )
        if self._management_path(path):
            return NetworkDecision(
                False,
                "management_blocked",
                "Row-Bot management endpoints are unavailable to Browser automation.",
            )
        try:
            destinations = self._resolve(host, port)
            addresses = tuple(_canonical_address(value) for value in destinations)
        except (OSError, ValueError):
            return NetworkDecision(
                False, "dns_failed", "The destination could not be resolved safely."
            )

        if any(_is_metadata_address(address) for address in addresses):
            return NetworkDecision(
                False,
                "metadata_blocked",
                "Cloud metadata targets are unavailable.",
                destinations,
            )

        identity = str(browser_session_id or "")
        key = (identity, origin)
        with self._lock:
            previous = self._resolved_origins.get(key)
            if previous is None:
                self._resolved_origins[key] = destinations
        if previous is not None and previous != destinations:
            return NetworkDecision(
                False,
                "dns_changed",
                "The destination address set changed; review and grant the exact destination again.",
                destinations,
            )

        if all(_is_public(address) for address in addresses):
            return NetworkDecision(
                True, "public", "Public destination allowed.", destinations
            )
        if self._matching_grant(
            browser_session_id=identity,
            origin=origin,
            destinations=destinations,
            now=self._clock(),
        ):
            if str(method or "GET").upper() not in {"GET", "HEAD", "OPTIONS"}:
                return NetworkDecision(
                    False,
                    "consequential_private_request",
                    "A local-development navigation grant cannot authorize form submissions, uploads, or other writes.",
                    destinations,
                )
            return NetworkDecision(
                True,
                "local_development_grant",
                "Exact local-development grant allowed.",
                destinations,
            )
        return NetworkDecision(
            False,
            "restricted_destination",
            "Private, loopback, link-local, unspecified, multicast, and reserved destinations are blocked by default.",
            destinations,
        )

    def install_context(
        self,
        context: Any,
        *,
        browser_session_id: str,
        on_allowed: Callable[[str, str], None] | None = None,
    ) -> None:
        """Install actual request, WebSocket, and download engine hooks."""

        identity = str(browser_session_id)

        def route_request(route: Any, request: Any | None = None) -> None:
            selected_request = request if request is not None else route.request
            url = str(getattr(selected_request, "url", "") or "")
            method = str(getattr(selected_request, "method", "GET") or "GET")
            decision = self.authorize(
                url,
                browser_session_id=identity,
                method=method,
            )
            if decision.allowed:
                if on_allowed is not None:
                    on_allowed(url, method)
                route.continue_()
            else:
                route.abort("blockedbyclient")

        def route_websocket(websocket_route: Any) -> None:
            url = str(getattr(websocket_route, "url", "") or "")
            decision = self.authorize(
                url,
                browser_session_id=identity,
            )
            if decision.allowed:
                if on_allowed is not None:
                    on_allowed(url, "GET")
                websocket_route.connect_to_server()
            else:
                websocket_route.close(code=1008, reason="Destination blocked")

        context.route("**/*", route_request)
        # WebSockets are unavailable when the engine cannot expose a dispatch
        # hook; this is safer than silently falling back to page preflight.
        route_ws = getattr(context, "route_web_socket", None)
        if not callable(route_ws):
            raise RuntimeError(
                "Managed Browser engine lacks the required WebSocket dispatch hook."
            )
        route_ws("**/*", route_websocket)

        def on_page(page: Any) -> None:
            self.attach_page(page, browser_session_id=identity)

        context.on("page", on_page)
        for page in tuple(getattr(context, "pages", ()) or ()):
            on_page(page)

    def attach_page(self, page: Any, *, browser_session_id: str) -> None:
        """Cancel a download if its final URL fails current network policy."""

        identity = str(browser_session_id)

        def on_download(download: Any) -> None:
            decision = self.authorize(
                str(getattr(download, "url", "") or ""),
                browser_session_id=identity,
            )
            if not decision.allowed:
                download.cancel()

        try:
            page.on("download", on_download)
        except Exception:
            # Minimal deterministic page fakes may omit event support. Actual
            # engine contexts are required to expose it in runtime evidence.
            return
