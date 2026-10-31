"""Where Row-Bot runs, from the reader's side (U7).

The handshake tells each browser its place: ``this_computer`` only for the person's own window on
the computer Row-Bot runs on; ``hosted`` for any other browser when a known host runs it; ``server``
otherwise (a container, or a phone reaching a desktop Row-Bot). Messages the server makes for any
reader use the server-wide place, so the desktop app reads exactly as before.
"""
from __future__ import annotations

import pytest

from row_bot import hosting
from tests.subsystem.client_protocol.test_protocol_security import bootstrap, client_app

pytestmark = pytest.mark.subsystem


@pytest.fixture(autouse=True)
def no_host(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("ROW_BOT_HOSTED_BY", raising=False)


def _place(remote: bool) -> dict:
    client, _, _ = client_app(remote=remote)
    with client:
        data, _ = bootstrap(client)
    return data["place"]


def test_only_the_window_on_the_computer_row_bot_runs_on_is_this_computer(monkeypatch):
    assert _place(remote=False) == {"kind": "this_computer", "host_name": None}
    # A known host doesn't change what a browser on that very computer is.
    monkeypatch.setenv("ROW_BOT_HOSTED_BY", "pikapods")
    assert _place(remote=False) == {"kind": "this_computer", "host_name": None}


def test_a_remote_browser_is_told_the_server_and_its_host(monkeypatch):
    assert _place(remote=True) == {"kind": "server", "host_name": None}
    monkeypatch.setenv("ROW_BOT_HOSTED_BY", "pikapods")
    assert _place(remote=True) == {"kind": "hosted", "host_name": "PikaPods"}
    # A host Row-Bot doesn't know names nobody.
    monkeypatch.setenv("ROW_BOT_HOSTED_BY", "somebody-else")
    assert _place(remote=True) == {"kind": "server", "host_name": None}


@pytest.mark.parametrize(
    ("environ", "place", "words"),
    [
        ({}, "this_computer", "on this computer"),
        ({"ROW_BOT_DEPLOYMENT_MODE": "desktop"}, "this_computer", "on this computer"),
        ({"ROW_BOT_DEPLOYMENT_MODE": "server"}, "server", "on the computer Row-Bot runs on"),
        ({"ROW_BOT_DEPLOYMENT_MODE": "server", "ROW_BOT_HOSTED_BY": "pikapods"}, "hosted",
         "on your Row-Bot server at PikaPods"),
        ({"ROW_BOT_DEPLOYMENT_MODE": "server", "ROW_BOT_HOSTED_BY": "somebody-else"}, "server",
         "on the computer Row-Bot runs on"),
    ],
)
def test_a_server_message_says_where_row_bot_runs(environ, place, words):
    assert hosting.server_place(environ) == place
    assert hosting.on_server(environ) == words
    assert hosting.where_row_bot_runs(environ) == words.removeprefix("on ")
