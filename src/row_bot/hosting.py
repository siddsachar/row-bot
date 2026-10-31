"""The company that runs this Row-Bot for its owner, when a host does (H13).

A host sets ``ROW_BOT_HOSTED_BY`` to its id. Each known host is a reviewed entry
here, links included: the links live in code, never in a setting, so no setting
can point the owner anywhere else. A value Row-Bot doesn't know means "no known
host": one warning, and nothing host-specific is shown.
"""

from __future__ import annotations

from dataclasses import dataclass
import functools
import logging
import os
from types import MappingProxyType
from typing import Literal, Mapping

logger = logging.getLogger(__name__)

HOSTED_BY_ENV = "ROW_BOT_HOSTED_BY"
DEPLOYMENT_MODE_ENV = "ROW_BOT_DEPLOYMENT_MODE"

# Where Row-Bot runs, from the reader's side (U7): the browser is on its computer, a known host runs
# it, or it is some other server (a container, or a computer reached from a phone).
Place = Literal["this_computer", "hosted", "server"]


@dataclass(frozen=True, slots=True)
class Host:
    """A reviewed hosting company. A link nobody could confirm stays ``None``."""

    id: str
    name: str
    home_url: str
    support_url: str | None
    status_url: str | None
    manage_url: str | None

    def to_public_dict(self) -> dict[str, str | None]:
        return {
            "id": self.id,
            "name": self.name,
            "home_url": self.home_url,
            "support_url": self.support_url,
            "status_url": self.status_url,
            "manage_url": self.manage_url,
        }


KNOWN_HOSTS: Mapping[str, Host] = MappingProxyType(
    {
        # Confirmed with PikaPods, 30 Oct 2026: "My Pods" is the pods dashboard, the docs are their
        # support, and their FAQ's service-level section covers uptime and maintenance.
        "pikapods": Host(
            id="pikapods",
            name="PikaPods",
            home_url="https://www.pikapods.com/",
            support_url="https://docs.pikapods.com/",
            status_url="https://docs.pikapods.com/faq/account#business-use-and-service-level",
            manage_url="https://www.pikapods.com/pods",
        ),
    }
)


def hosted_by(environ: Mapping[str, str] | None = None) -> Host | None:
    """The host named by ``ROW_BOT_HOSTED_BY``, or ``None`` when it names none Row-Bot knows."""
    env = os.environ if environ is None else environ
    value = str(env.get(HOSTED_BY_ENV) or "").strip().lower()
    if not value:
        return None
    host = KNOWN_HOSTS.get(value)
    if host is None:
        _warn_unknown_host(value[:64])
    return host


@functools.lru_cache(maxsize=8)
def _warn_unknown_host(value: str) -> None:
    logger.warning(
        "%s=%r names a host Row-Bot doesn't know; nothing host-specific is shown",
        HOSTED_BY_ENV,
        value,
    )


def server_place(environ: Mapping[str, str] | None = None) -> Place:
    """The place for a message with no single reader (U7): a known host, a headless server, or the desktop app.

    A request knows its reader's side better (the handshake's ``place``); this is for what the server
    says to whoever reads it. The desktop app keeps "this computer", as it always said.
    """
    env = os.environ if environ is None else environ
    if hosted_by(env) is not None:
        return "hosted"
    if str(env.get(DEPLOYMENT_MODE_ENV) or "").strip().lower() == "server":
        return "server"
    return "this_computer"


def where_row_bot_runs(environ: Mapping[str, str] | None = None) -> str:
    """Where Row-Bot runs, in a sentence (U7): "this computer", "your Row-Bot server at PikaPods" or
    "the computer Row-Bot runs on"."""
    host = hosted_by(environ)
    if host is not None:
        return f"your Row-Bot server at {host.name}"
    return "the computer Row-Bot runs on" if server_place(environ) == "server" else "this computer"


def on_server(environ: Mapping[str, str] | None = None) -> str:
    """The same with "on": "on this computer", "on your Row-Bot server at PikaPods"…"""
    return "on " + where_row_bot_runs(environ)


__all__ = [
    "HOSTED_BY_ENV", "KNOWN_HOSTS", "Host", "Place", "hosted_by", "on_server", "server_place", "where_row_bot_runs",
]
