"""Custom endpoints on a hosted Row-Bot, and the networks around its server (H16).

On a server a hosting company runs (``hosting.hosted_by()`` names it), the addresses that aren't on
the internet are the host's: its private network (10/8, 172.16/12, 192.168/16, 100.64/10, fc00::/7)
and link-local addresses (169.254/16, fe80::/10), where cloud metadata services hand out the host's
own credentials. A custom endpoint reaches such an address only after its owner allowed it, for that
endpoint's address, where the endpoint is added or edited: asked per address, like the local
addresses of Row-Bot's browser (F4). A cloud metadata address is never reached, as in the browser.

- **This server** (127.0.0.0/8, ::1, 0.0.0.0) needs no OK: on a pod it is the pod itself, the
  owner's own container, so a model server there is one the owner or Row-Bot started.
- **Row-Bot's own servers** (``managed`` endpoints, such as Learning's) are never checked.
- **Without a known host** (the desktop app, a self-hosted server) nothing changes, for any address.

Checked twice. When an endpoint is saved, its name is resolved and every address it gives is
checked, so a name that leads to a private address asks too. On every connection, the address the
socket actually connected to is checked before anything is sent, so a name that answers
differently later (DNS rebinding) is refused as well. Nothing asks in the middle of a request:
such a request fails with a plain reason, which the owner lifts by editing the endpoint.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
import ipaddress
import logging
import socket
from typing import Any, Literal
from urllib.parse import urlsplit

from row_bot import hosting
from row_bot.providers.config import ProviderConfigError

logger = logging.getLogger(__name__)

# Refusal codes: a private address the owner may allow, and a metadata address nobody may.
PRIVATE_ADDRESS = "endpoint_private_address"
METADATA_ADDRESS = "endpoint_address_blocked"
# The stored endpoint field: the origin (``http://10.0.0.5:11434``) the owner allowed.
ALLOWED_ORIGIN = "private_address_allowed"

Kind = Literal["public", "this_server", "private", "metadata"]
Address = ipaddress.IPv4Address | ipaddress.IPv6Address

# IPv6 forms that carry an IPv4 address: NAT64 and the deprecated IPv4-compatible block.
_EMBEDDED = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("::/96"))
# Cloud metadata services (the same list as Row-Bot's browser): AWS, ECS, Alibaba, Azure, Oracle, Google.
_METADATA = frozenset(ipaddress.ip_address(value) for value in (
    "169.254.169.254", "169.254.170.2", "100.100.100.200", "168.63.129.16", "192.0.0.192",
    "fd00:ec2::254", "fd20:ce::254",
))


class EndpointAddressRefused(ProviderConfigError):
    """A custom endpoint's request that would reach the host's private network, refused before anything
    is sent. ``code`` is ``PRIVATE_ADDRESS`` or ``METADATA_ADDRESS``; the text is the plain reason a chat
    shows."""

    def __init__(self, code: str, address: str = "") -> None:
        super().__init__(code)
        self.address = address

    def __str__(self) -> str:
        where = f" ({self.address})" if self.address else ""
        if self.code == METADATA_ADDRESS:
            return (f"This custom endpoint's address{where} belongs to the hosting company's own computer. "
                    "Row-Bot never connects to it.")
        return (f"This custom endpoint's address{where} is on your host's private network. To use it, "
                "edit the endpoint in Settings › Providers and allow it.")


def address_kind(value: str) -> Kind:
    """Where an IP address points, from a hosted server: ``public``, ``this_server``, ``private`` or ``metadata``."""

    address: Address = ipaddress.ip_address(str(value).strip("[]").split("%", 1)[0])
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            address = address.ipv4_mapped
        elif not (address.is_loopback or address.is_unspecified) and any(address in net for net in _EMBEDDED):
            address = ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
    if address in _METADATA:
        return "metadata"
    if address.is_loopback or address.is_unspecified:
        return "this_server"
    return "public" if address.is_global else "private"


def endpoint_origin(url: str) -> str:
    """``scheme://host:port`` of an endpoint's address, the unit the owner allows; '' when it has none."""

    try:
        parsed = urlsplit(str(url or "").strip())
        host = (parsed.hostname or "").rstrip(".").casefold()
        port = parsed.port or (443 if parsed.scheme.casefold() == "https" else 80)
    except ValueError:
        return ""
    if not host:
        return ""
    return f"{parsed.scheme.casefold()}://{f'[{host}]' if ':' in host else host}:{port}"


def allowed(endpoint: Mapping[str, Any]) -> bool:
    """Whether the owner allowed this endpoint's current address to be private."""

    origin = str(endpoint.get(ALLOWED_ORIGIN) or "")
    return bool(origin) and origin == endpoint_origin(str(endpoint.get("base_url") or ""))


def _refusal(address: str, may_allow: bool) -> EndpointAddressRefused | None:
    kind = address_kind(address)
    if kind == "metadata":
        return EndpointAddressRefused(METADATA_ADDRESS, address)
    if kind == "private" and not may_allow:
        return EndpointAddressRefused(PRIVATE_ADDRESS, address)
    return None


def check_new_address(base_url: str, *, allow_private: bool) -> None:
    """Refuse saving an endpoint whose address leads to the host's private network, on a hosted server.

    Every address the name resolves to is checked. A name that doesn't resolve now is left to the
    check at each connection.
    """

    if hosting.hosted_by() is None:
        return
    try:
        parsed = urlsplit(base_url)
        host, port = parsed.hostname or "", parsed.port or (443 if parsed.scheme == "https" else 80)
        found = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, ValueError, UnicodeError):
        return
    refusals = [refused for item in found if (refused := _refusal(str(item[4][0]), allow_private)) is not None]
    if refusals:
        # A metadata address is refused even when a private one is allowed, so it goes first.
        raise min(refusals, key=lambda refused: refused.code != METADATA_ADDRESS)


class ConnectionCheck:
    """Checks the address each connection of one custom endpoint reached, before anything is sent."""

    def __init__(self, *, may_allow: bool) -> None:
        self.may_allow = may_allow

    def _check(self, stream: Any) -> EndpointAddressRefused | None:
        peer = stream.get_extra_info("server_addr")
        try:
            refused = _refusal(str(peer[0]), self.may_allow)
        except (TypeError, IndexError, ValueError):
            # Nothing says where this connection went, so it isn't trusted.
            refused = EndpointAddressRefused(PRIVATE_ADDRESS)
        if refused is not None:
            logger.warning("A custom endpoint's connection was refused on a hosted server: %s", refused)
        return refused

    def trace(self, earlier: Callable[[str, dict], Any] | None) -> Callable[[str, dict], None]:
        """An httpcore ``trace`` extension (sync clients), passing every event on to *earlier*."""

        def trace(event: str, info: dict) -> None:
            if event.endswith(".connect_tcp.complete"):
                stream = info["return_value"]
                if (refused := self._check(stream)) is not None:
                    stream.close()
                    raise refused
            if earlier is not None:
                earlier(event, info)

        return trace

    def atrace(self, earlier: Callable[[str, dict], Awaitable[Any]] | None) -> Callable[[str, dict], Awaitable[None]]:
        """The same for async clients."""

        async def trace(event: str, info: dict) -> None:
            if event.endswith(".connect_tcp.complete"):
                stream = info["return_value"]
                if (refused := self._check(stream)) is not None:
                    await stream.aclose()
                    raise refused
            if earlier is not None:
                await earlier(event, info)

        return trace

    def event_hooks(self, *, asynchronous: bool = False) -> dict[str, list[Callable[..., Any]]]:
        """httpx event hooks that put the check on each request a client sends."""

        if asynchronous:
            async def ahook(request: Any) -> None:
                request.extensions["trace"] = self.atrace(request.extensions.get("trace"))

            return {"request": [ahook]}

        def hook(request: Any) -> None:
            request.extensions["trace"] = self.trace(request.extensions.get("trace"))

        return {"request": [hook]}


def connection_check(endpoint: Mapping[str, Any] | None) -> ConnectionCheck | None:
    """The check for a custom endpoint's connections on a hosted server; None when nothing is checked:
    no known host, not a custom endpoint, or one of Row-Bot's own servers."""

    endpoint = endpoint or {}
    if hosting.hosted_by() is None or endpoint.get("managed"):
        return None
    if not str(endpoint.get("provider_id") or "").startswith("custom_openai_"):
        return None
    return ConnectionCheck(may_allow=allowed(endpoint))


def check_clients(endpoint: Mapping[str, Any] | None, *clients: Any) -> None:
    """Put the connection check on httpx clients that send a custom endpoint's requests (an SDK's)."""

    import httpx

    check = connection_check(endpoint)
    if check is None:
        return
    for client in clients:
        hooks = check.event_hooks(asynchronous=isinstance(client, httpx.AsyncClient))
        client.event_hooks = {**client.event_hooks,
                              "request": [*client.event_hooks.get("request", []), *hooks["request"]]}


__all__ = [
    "ALLOWED_ORIGIN", "METADATA_ADDRESS", "PRIVATE_ADDRESS", "ConnectionCheck", "EndpointAddressRefused",
    "address_kind", "allowed", "check_clients", "check_new_address", "connection_check", "endpoint_origin",
]
