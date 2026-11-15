"""Local development addresses for Row-Bot's managed browser (F4, decision D5).

An address on this computer (``localhost``, ``*.localhost``, ``127.0.0.1``,
``[::1]``) opens only after the person allows it for one conversation, one
origin at a time, through the approval gate; a server Row-Bot itself started
in that conversation and is listening on the port is allowed without asking.
Grants expire after an hour and the person can remove them sooner.

Every other private-network address (another computer on the network, a
router page, cloud metadata) stays blocked: no grant ever covers one.
``browser.guard`` enforces both on every page's requests, not only on the
addresses the agent opens.
"""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
from typing import Any, Literal
from urllib.parse import urlsplit

from row_bot.browser.addresses import is_loopback_name

GrantSource = Literal["you", "reviewer", "hands_off", "row_bot_server"]
GRANT_SOURCES: tuple[GrantSource, ...] = ("you", "reviewer", "hands_off", "row_bot_server")
# What the risk is, in plain words, for the reviewer under Auto.
LOCAL_ADDRESS_RISK = "opens an address on this computer in Row-Bot's browser"


@dataclass(frozen=True)
class LocalAddressGrant:
    """One local address one conversation may open, as the person sees it."""

    id: str
    conversation_id: str
    origin: str
    address: str
    source: GrantSource
    granted_at: float
    expires_at: float

    def view(self) -> dict[str, Any]:
        from datetime import datetime, timezone

        def stamp(value: float) -> str:
            return datetime.fromtimestamp(value, timezone.utc).isoformat()

        return {
            "grant_id": self.id,
            "address": self.address,
            "origin": self.origin,
            "source": self.source,
            "granted_at": stamp(self.granted_at),
            "expires_at": stamp(self.expires_at),
        }


def _loopback_literal(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_loopback


def _unspecified_literal(host: str) -> bool:
    try:
        return ipaddress.ip_address(host.strip("[]")).is_unspecified
    except ValueError:
        return False


def local_development_origin(url: str) -> str | None:
    """The exact origin of an http(s) address on this computer or the person's network, else None.

    ``http://localhost:5173/app?x=1`` gives ``http://localhost:5173``; a router, a NAS, another
    computer, a Tailscale address or an intranet name count too (5.3.1 B3). Cloud metadata never does.
    """
    from row_bot.browser.addresses import url_host_kind

    try:
        parsed = urlsplit(str(url or "").strip())
        scheme = parsed.scheme.casefold()
        host = (parsed.hostname or "").rstrip(".").casefold()
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
        return None
    if not (is_loopback_name(host) or _loopback_literal(host)) and (
            url_host_kind(str(url).strip()) != "local_network" or _unspecified_literal(host)):
        return None
    default = 443 if scheme == "https" else 80
    port = port or default
    rendered = f"[{host}]" if ":" in host else host
    return f"{scheme}://{rendered}" + (f":{port}" if port != default else "")


def socket_origin(origin: str) -> str:
    """The WebSocket origin of the same server (``ws://localhost:5173``), which dev servers use for live reload."""

    return "wss://" + origin[len("https://"):] if origin.startswith("https://") else "ws://" + origin[len("http://"):]


def address_label(origin: str) -> str:
    """How people write it: ``localhost:5173`` (``https://`` kept, since it's a different address)."""

    return origin[len("http://"):] if origin.startswith("http://") else origin


def origin_port(origin: str) -> int:
    parsed = urlsplit(origin)
    return parsed.port or (443 if parsed.scheme == "https" else 80)


def on_this_computer(origin: str) -> bool:
    host = (urlsplit(origin).hostname or "").casefold()
    return is_loopback_name(host) or _loopback_literal(host)


def is_row_bot_origin(origin: str) -> bool:
    """Whether the address is Row-Bot's own app, which its browser must never open (it could answer its own approvals)."""

    from row_bot.app_port import get_app_port

    return on_this_computer(origin) and origin_port(origin) == get_app_port()


def private_network_target(url: str) -> bool:
    """Whether the address is a cloud metadata service (link-local IPs, metadata names), which never opens.

    Addresses on this computer and the person's network open once a chat allows them (5.3.1 B3). A public
    name that resolves to a metadata address (DNS rebinding) isn't caught here; Chromium's own
    local-network protection covers that.
    """

    from row_bot.browser.addresses import url_host_kind

    return url_host_kind(str(url or "").strip()) == "metadata"


def unspecified_refusal(url: str) -> str:
    """A server's "every address" bind (0.0.0.0, [::]) isn't an address to open: say to use localhost.

    Empty when *url* isn't one.
    """

    try:
        parsed = urlsplit(str(url or "").strip())
        address = ipaddress.ip_address((parsed.hostname or "").strip("[]"))
        port = parsed.port
    except (TypeError, ValueError):
        return ""
    if not address.is_unspecified:
        return ""
    where = f"localhost:{port}" if port else "localhost"
    return (
        f"BLOCKED: {parsed.hostname} means every address on this computer, not one to open. "
        f"Open http://{where} instead."
    )


PRIVATE_NETWORK_REFUSAL = (
    "BLOCKED: Row-Bot's browser never opens a cloud metadata address: it hands out a computer's own "
    "credentials. Addresses on this computer and your network can be allowed in a chat."
)


def row_bot_refusal(origin: str) -> str:
    return (
        f"BLOCKED: {address_label(origin)} is Row-Bot itself. Its browser never opens Row-Bot's own app; "
        "ask the person to look at it."
    )


def question(origin: str) -> str:
    return f"Allow Row-Bot's browser to open {address_label(origin)} in this chat?"


def _where(origin: str) -> str:
    return "on this computer" if on_this_computer(origin) else "on your network"


def approval_request(origin: str, *, tool: str = "browser_navigate") -> dict[str, Any]:
    """The approval for opening one local address in this conversation."""

    label = address_label(origin)
    return {
        "tool": tool,
        "label": "Open a local address" if on_this_computer(origin) else "Open an address on your network",
        "action": "open_local_address",
        "question": question(origin),
        "description": (
            f"{label} is {_where(origin)}. Allowing it lets Row-Bot's browser open only this address, "
            "only in this chat, for up to an hour. You can remove it sooner in the browser panel."
        ),
        "origin_and_path": origin,
        "args": {"address": label},
    }


def source_of(approval_line: str) -> GrantSource:
    """Who allowed it, from the approval line the gate recorded."""

    from row_bot.tools.approval_gate import APPROVAL_FULL_AUTONOMY

    if approval_line.startswith(APPROVAL_FULL_AUTONOMY):
        return "hands_off"
    if approval_line.startswith("Approval: approved by the reviewer"):
        return "reviewer"
    return "you"


def row_bot_server(origin: str, conversation_id: str) -> bool:
    """Whether a server Row-Bot started in this conversation listens at the address now.

    Only a port the process actually listens on counts: a printed address
    alone isn't proof (``background_processes.owns_local_origin``).
    """

    try:
        from row_bot import sandboxes

        # A sandbox this Row-Bot started (5.3.1 B4) is one of its servers too.
        if on_this_computer(origin) and origin_port(origin) in sandboxes.running_ports():
            return True
    except Exception:
        pass
    try:
        from row_bot.background_processes import owns_local_origin
    except Exception:
        return False
    try:
        return bool(owns_local_origin(origin, conversation_id))
    except Exception:
        return False
