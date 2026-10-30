"""Hosted identity (H13): ``ROW_BOT_HOSTED_BY`` names a reviewed entry, links and all."""

from __future__ import annotations

import logging
from urllib.parse import urlsplit
import uuid

import pytest

from row_bot.hosting import HOSTED_BY_ENV, KNOWN_HOSTS, hosted_by

pytestmark = pytest.mark.contract


def _site(url: str) -> str:
    return ".".join(urlsplit(url).hostname.split(".")[-2:])


@pytest.mark.parametrize("host_id", sorted(KNOWN_HOSTS))
def test_every_known_host_links_only_to_its_own_site_over_https(host_id: str) -> None:
    host = KNOWN_HOSTS[host_id]
    links = [host.home_url, host.support_url, host.status_url, host.manage_url]

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
    }


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
