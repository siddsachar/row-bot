"""Hosted identity (H13): ``ROW_BOT_HOSTED_BY`` names a reviewed entry, links and all."""

from __future__ import annotations

import logging
from urllib.parse import urlsplit
import uuid

import pytest

from row_bot.hosting import HOSTED_BY_ENV, KNOWN_HOSTS, hosted_by, trial

pytestmark = pytest.mark.contract


def _site(url: str) -> str:
    return ".".join(urlsplit(url).hostname.split(".")[-2:])


@pytest.mark.parametrize("host_id", sorted(KNOWN_HOSTS))
def test_every_known_host_links_only_to_its_own_site_over_https(host_id: str) -> None:
    host = KNOWN_HOSTS[host_id]
    # With the IDs a host gives its service, too (A2).
    paged = hosted_by({HOSTED_BY_ENV: host_id, **RAILWAY_IDS})
    links = [host.home_url, host.support_url, host.status_url, host.manage_url, host.plans_url] + [
        paged.service_url, paged.variables_url, paged.settings_url, paged.usage_url,
    ]

    assert host.id == host_id and host.name
    for link in filter(None, links):
        assert urlsplit(link).scheme == "https"
        assert _site(link) == _site(host.home_url)


def test_pikapods_is_named_and_linked_from_its_reviewed_entry() -> None:
    host = hosted_by({HOSTED_BY_ENV: " PikaPods "})

    assert host is not None
    assert host.to_public_dict() == {
        "id": "pikapods",
        "name": "PikaPods",
        "home_url": "https://www.pikapods.com/",
        "support_url": "https://docs.pikapods.com/",
        "status_url": "https://docs.pikapods.com/faq/account#business-use-and-service-level",
        "manage_url": "https://www.pikapods.com/pods",
        "unit": "pod",
        "runs": "the server, billing and backups",
        "applies_updates": True,
        "updates_help": "They test each release first, so new versions usually arrive within a week.",
        "settings_url": None,
        "usage_url": None,
        "backups_note": "PikaPods can also keep its own backups of your server.",
    }


def test_railway_is_named_and_linked_from_its_reviewed_entry_and_leaves_updates_to_the_owner() -> None:
    host = hosted_by({HOSTED_BY_ENV: "railway"})

    assert host is not None
    assert (host.name, host.home_url, host.support_url, host.status_url, host.manage_url) == (
        "Railway",
        "https://railway.com/",
        "https://station.railway.com/",
        "https://status.railway.com/",
        "https://railway.com/dashboard",
    )
    # An image service on Railway gets a new version only when its owner updates it (or turns on auto updates).
    assert host.unit == "service" and host.applies_updates is False


def test_no_setting_means_no_host_and_no_warning(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="row_bot.hosting"):
        assert hosted_by({}) is None
        assert hosted_by({HOSTED_BY_ENV: "  "}) is None

    assert caplog.records == []


def test_an_unknown_host_shows_nothing_host_specific_and_warns_once(caplog) -> None:
    unknown = f"example-host-{uuid.uuid4().hex[:8]}"

    with caplog.at_level(logging.WARNING, logger="row_bot.hosting"):
        first = hosted_by({HOSTED_BY_ENV: unknown})
        second = hosted_by({HOSTED_BY_ENV: unknown})

    assert first is second is None
    assert [record.levelno for record in caplog.records] == [logging.WARNING]
    assert unknown in caplog.records[0].getMessage()


# What Railway gives every service (A2): its project, service and environment IDs.
PROJECT = "0f6e3c1a-5b2d-4c8e-9a71-2d4b6f8e1c30"
SERVICE = "7a1c9e42-3f5b-4d6a-8e20-b1c3d5e7f901"
ENVIRONMENT = "c2e4a6b8-1d3f-4a5c-9e7b-0f2d4c6e8a10"
RAILWAY_IDS = {
    "RAILWAY_PROJECT_ID": PROJECT,
    "RAILWAY_SERVICE_ID": SERVICE,
    "RAILWAY_ENVIRONMENT_ID": ENVIRONMENT,
}


def test_railways_ids_link_the_services_own_pages_on_railway_com() -> None:
    host = hosted_by({HOSTED_BY_ENV: "railway", **RAILWAY_IDS})
    service = f"https://railway.com/project/{PROJECT}/service/{SERVICE}"

    assert host is not None
    assert (host.service_url, host.variables_url, host.settings_url, host.usage_url) == (
        f"{service}?environmentId={ENVIRONMENT}",
        f"{service}/variables?environmentId={ENVIRONMENT}",
        f"{service}/settings?environmentId={ENVIRONMENT}",
        "https://railway.com/workspace/usage",
    )
    # Signed-in pages open the service itself; the public setup page keeps the dashboard, with no ID.
    assert host.to_public_dict()["manage_url"] == f"{service}?environmentId={ENVIRONMENT}"
    assert host.manage_url == "https://railway.com/dashboard"
    # IDs as Railway may spell them, upper case or padded, are the same IDs.
    shouted = hosted_by({HOSTED_BY_ENV: "railway", **{k: f" {v.upper()} " for k, v in RAILWAY_IDS.items()}})
    assert shouted == host
    for link in (host.service_url, host.variables_url, host.settings_url, host.usage_url):
        parts = urlsplit(link)
        assert (parts.scheme, parts.hostname, parts.username, parts.port) == ("https", "railway.com", None, None)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "not-an-id",
        PROJECT[:-1],
        PROJECT + "0",
        PROJECT.replace("-", ""),
        "{" + PROJECT + "}",
        f"urn:uuid:{PROJECT}",
        "../../evil.example/x",
        f"{PROJECT}?environmentId=x",
        f"{PROJECT}@evil.example",
        f"{PROJECT}/../../x",
    ],
)
@pytest.mark.parametrize("name", sorted(RAILWAY_IDS))
def test_anything_but_three_uuids_gives_no_service_links_and_the_entrys_own_words(name: str, value: str) -> None:
    host = hosted_by({HOSTED_BY_ENV: "railway", **RAILWAY_IDS, name: value})

    assert host == KNOWN_HOSTS["railway"]
    assert host.manage_url == "https://railway.com/dashboard"
    assert (host.service_url, host.variables_url, host.settings_url, host.usage_url) == (None, None, None, None)


def test_only_railway_reads_railways_ids() -> None:
    assert hosted_by({HOSTED_BY_ENV: "pikapods", **RAILWAY_IDS}) == KNOWN_HOSTS["pikapods"]


@pytest.mark.parametrize(
    ("limit", "on_trial"),
    [(999_997_440, True), (1_000_000_000, True), (2**30, True), (2**30 + 1, False), (2_000_000_000, False),
     (8_000_000_000, False), (None, False)],
)
def test_railways_trial_is_a_server_of_1_gb_or_less(limit, on_trial: bool) -> None:
    words = trial(hosted_by({HOSTED_BY_ENV: "railway"}), limit)

    assert (words is not None) is on_trial
    if on_trial:
        assert words == {
            "note": "Railway's trial gives Row-Bot 1 GB: chats, memory and schedules work. "
            "Browser tasks and local speech-to-text need Railway's Hobby plan.",
            "upgrade_url": "https://railway.com/workspace/plans",
        }
    # A host with no trial, or none at all, never says so.
    assert trial(KNOWN_HOSTS["pikapods"], limit) is None
    assert trial(None, limit) is None


def test_each_host_says_in_its_own_words_what_it_does_about_backups() -> None:
    """F3: PikaPods keeps backups of its own; Railway's volume backups are its Pro plan's alone."""
    assert KNOWN_HOSTS["pikapods"].backups_note == "PikaPods can also keep its own backups of your server."
    assert KNOWN_HOSTS["railway"].backups_note == (
        "Railway backs up your server only on its Pro plan. "
        "Download a backup from time to time to keep a copy somewhere else."
    )
