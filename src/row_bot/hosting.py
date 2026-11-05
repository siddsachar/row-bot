"""The company that runs this Row-Bot for its owner, when a host does (H13).

A host sets ``ROW_BOT_HOSTED_BY`` to its id. Each known host is a reviewed entry
here, links included: the links live in code, never in a setting, so no setting
can point the owner anywhere else. A value Row-Bot doesn't know means "no known
host": one warning, and nothing host-specific is shown.

Railway also tells every service its own IDs (A2). Each checked as a UUID, they
fill in the fixed addresses of that service's own pages here (its Variables,
Settings and Backups), so a link lands on the right page; anything else in them
means no such links, and the entry's own words stand.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import functools
import logging
import os
import re
from types import MappingProxyType
from typing import Callable, Literal, Mapping

logger = logging.getLogger(__name__)

HOSTED_BY_ENV = "ROW_BOT_HOSTED_BY"
DEPLOYMENT_MODE_ENV = "ROW_BOT_DEPLOYMENT_MODE"

# Where Row-Bot runs, from the reader's side (U7): the browser is on its computer, a known host runs
# it, or it is some other server (a container, or a computer reached from a phone).
Place = Literal["this_computer", "hosted", "server"]


@dataclass(frozen=True, slots=True)
class Host:
    """A reviewed hosting company: its links (a link nobody could confirm stays ``None``), and its own
    words for what the owner does there. Only Row-Bot's pages show the words; the model never reads them."""

    id: str
    name: str
    home_url: str
    support_url: str | None
    status_url: str | None
    manage_url: str | None
    # What the host calls the thing that runs Row-Bot: "Manage pod", "Manage your pod in PikaPods".
    unit: str
    # Where the owner finds the setup code, after "In PikaPods, ".
    setup_code_where: str
    # How the owner sets a new setup code, as an instruction ("…, then enter the new code here").
    setup_code_change: str
    # How the owner gives Row-Bot 4 GB of memory, and more than it has.
    resize_to_4gb: str
    resize_bigger: str
    # What the host runs for the owner: "PikaPods runs the server, billing and backups."
    runs: str
    # Whether the host installs each new version by itself, and what Settings › Updates says about it.
    applies_updates: bool
    updates_help: str
    # The header the host's proxy sets to the visitor's own address, replacing any a visitor sent, checked on a
    # real deploy. Read only from a trusted proxy (ROW_BOT_TRUSTED_PROXY_CIDRS); None: X-Forwarded-For decides.
    client_address_header: str | None = None
    # The service's own pages (A2), filled in by ``pages`` from what the host tells the service about itself;
    # None without that. With them, ``manage_url`` is the service itself rather than the host's dashboard.
    variables_url: str | None = None
    settings_url: str | None = None
    backups_url: str | None = None
    usage_url: str | None = None
    pages: Callable[[Mapping[str, str]], dict[str, str]] | None = None
    # Where the start-up output with the sign-in link shows, after "or click the sign-in link in " (R4).
    sign_in_link_where: str | None = None
    # A trial's small server (A4): what works there, the step that unlocks the rest and the host's page for it.
    trial_note: str | None = None
    trial_resize: str | None = None
    plans_url: str | None = None

    def to_public_dict(self) -> dict[str, str | bool | None]:
        return {
            "id": self.id,
            "name": self.name,
            "home_url": self.home_url,
            "support_url": self.support_url,
            "status_url": self.status_url,
            "manage_url": self.manage_url,
            "unit": self.unit,
            "runs": self.runs,
            "applies_updates": self.applies_updates,
            "updates_help": self.updates_help,
            "settings_url": self.settings_url,
            "backups_url": self.backups_url,
            "usage_url": self.usage_url,
        }


# Railway's pages for one service (A2), as its dashboard addresses them on 5 Nov 2026. Only the IDs come from
# the environment Railway gives the service; a page Railway moves is a one-line change here.
_RAILWAY_SERVICE = "https://railway.com/project/{project}/service/{service}"
RAILWAY_PAGES: Mapping[str, str] = MappingProxyType(
    {
        "manage_url": _RAILWAY_SERVICE + "?environmentId={environment}",
        "variables_url": _RAILWAY_SERVICE + "/variables?environmentId={environment}",
        "settings_url": _RAILWAY_SERVICE + "/settings?environmentId={environment}",
        "backups_url": _RAILWAY_SERVICE + "/backups?environmentId={environment}",
        "usage_url": "https://railway.com/workspace/usage",
    }
)
_RAILWAY_IDS = MappingProxyType(
    {"project": "RAILWAY_PROJECT_ID", "service": "RAILWAY_SERVICE_ID", "environment": "RAILWAY_ENVIRONMENT_ID"}
)
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def railway_pages(environ: Mapping[str, str]) -> dict[str, str]:
    """This service's own Railway pages, or none unless all three of its IDs are UUIDs."""
    ids: dict[str, str] = {}
    for key, name in _RAILWAY_IDS.items():
        value = str(environ.get(name) or "").strip().lower()
        if not _UUID.fullmatch(value):
            return {}
        ids[key] = value
    return {field: address.format(**ids) for field, address in RAILWAY_PAGES.items()}


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
            unit="pod",
            setup_code_where="open your pod's More\u00a0›\u00a0Show Logs and copy the setup code",
            setup_code_change="Change the setup code in your pod's settings and restart it",
            resize_to_4gb="Make the pod 4 GB in PikaPods",
            resize_bigger="Make the pod bigger in PikaPods",
            runs="the server, billing and backups",
            # PikaPods tests each release, then rolls it out to every pod.
            applies_updates=True,
            updates_help="They test each release first, so new versions usually arrive within a week.",
        ),
        # Read in Railway's docs, 5 Nov 2026 (deploy/railway/README.md): Central Station is its support
        # forum, the dashboard lists the owner's projects, and the template's variables hold the setup code.
        # An image service doesn't update by itself: Railway offers a new image in the service's settings,
        # and its auto updates are the owner's choice.
        "railway": Host(
            id="railway",
            name="Railway",
            home_url="https://railway.com/",
            support_url="https://station.railway.com/",
            status_url="https://status.railway.com/",
            manage_url="https://railway.com/dashboard",
            unit="service",
            setup_code_where="open your Row-Bot service's Variables and copy ROW_BOT_SETUP_CODE",
            setup_code_change="Set a new ROW_BOT_SETUP_CODE in your service's Variables and deploy the change",
            resize_to_4gb="Raise the service's memory limit to 4 GB in Railway",
            resize_bigger="Raise the service's memory limit in Railway",
            runs="the server and billing",
            applies_updates=False,
            updates_help=(
                "When a new version is out, Railway offers it in your service's settings. "
                "Turn on auto updates there to get each one by itself."
            ),
            # Checked on a deploy, 5 Nov 2026: Railway's edge replaces a visitor's X-Real-IP and X-Forwarded-For.
            # Its X-Forwarded-For ends with the edge's own public address, which the walk from the trusted proxy
            # (100.64.0.0/10) can't pass, so every visitor would look like the edge.
            client_address_header="x-real-ip",
            # The service's Variables, Settings and Backups, from the IDs Railway gives it (A2).
            pages=railway_pages,
            sign_in_link_where="Railway's deploy logs",
            # Railway's trial gives a service 1 GB (RD2, A4); its Hobby plan lifts that.
            trial_note=(
                "Railway's trial gives Row-Bot 1 GB: chats, memory and schedules work. "
                "Browser tasks and local speech-to-text need Railway's Hobby plan."
            ),
            trial_resize="Move to Railway's Hobby plan and raise the service's memory limit to 4 GB",
            plans_url="https://railway.com/workspace/plans",
        ),
    }
)


def hosted_by(environ: Mapping[str, str] | None = None) -> Host | None:
    """The host named by ``ROW_BOT_HOSTED_BY``, or ``None`` when it names none Row-Bot knows.

    Its entry carries this service's own pages when the host told the service its IDs (A2).
    """
    env = os.environ if environ is None else environ
    value = str(env.get(HOSTED_BY_ENV) or "").strip().lower()
    if not value:
        return None
    host = KNOWN_HOSTS.get(value)
    if host is None:
        _warn_unknown_host(value[:64])
        return None
    pages = host.pages(env) if host.pages is not None else {}
    return replace(host, **pages) if pages else host


# "1 GB or less", in decimal GB or binary GiB: a trial's server (A4). Railway's trial is 1 GB.
TRIAL_MEMORY_BYTES = 2**30


def trial(host: Host | None, limit_bytes: int | None) -> dict[str, str] | None:
    """The host's trial words and upgrade page while its server has a trial's memory (A4), else None."""
    if host is None or not (host.trial_note and host.plans_url) or limit_bytes is None:
        return None
    if limit_bytes > TRIAL_MEMORY_BYTES:
        return None
    return {"note": host.trial_note, "upgrade_url": host.plans_url}


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


def request_place(remote: bool, environ: Mapping[str, str] | None = None) -> Place:
    """One request's place (U7): ``this_computer`` when the browser is on the computer Row-Bot runs on
    (``remote`` False: the handshake's ``on_this_computer``), else a known host's or another server."""
    if not remote:
        return "this_computer"
    return "hosted" if hosted_by(environ) is not None else "server"


def where_row_bot_runs(environ: Mapping[str, str] | None = None, *, remote: bool | None = None) -> str:
    """Where Row-Bot runs, in a sentence (U7): "this computer", "your Row-Bot server at PikaPods" or
    "the computer Row-Bot runs on". ``remote`` is one request's side; without it, the server-wide place."""
    place = server_place(environ) if remote is None else request_place(remote, environ)
    host = hosted_by(environ)
    if place == "hosted" and host is not None:
        return f"your Row-Bot server at {host.name}"
    return "the computer Row-Bot runs on" if place == "server" else "this computer"


def on_server(environ: Mapping[str, str] | None = None, *, remote: bool | None = None) -> str:
    """The same with "on": "on this computer", "on your Row-Bot server at PikaPods"…"""
    return "on " + where_row_bot_runs(environ, remote=remote)


__all__ = [
    "HOSTED_BY_ENV", "KNOWN_HOSTS", "RAILWAY_PAGES", "TRIAL_MEMORY_BYTES", "Host", "Place", "hosted_by", "on_server",
    "railway_pages", "request_place", "server_place", "trial", "where_row_bot_runs",
]
