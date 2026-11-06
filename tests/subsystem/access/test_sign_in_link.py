"""A one-click sign-in link (A1): ``/setup#code=…``.

The setup page takes the code from the link's fragment, which a browser never sends, and posts it
exactly as Continue does: the code signs in once, through the same checks, refusals and pauses. The
link is printed where the code is, the console, only while nobody owns this Row-Bot and only when its
public address is known; never once someone does, never for a recovery code, and never in Row-Bot's
own log files or database.
"""

from __future__ import annotations

import json
import logging
import re
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from row_bot.access.config import AccessConfig
from row_bot.access.middleware import AccessMiddleware
from row_bot.access.routes import register_access_routes
from row_bot.access.service import AccessService
from row_bot.access.setup_code import (
    PUBLIC_URL_ENV,
    SETUP_CODE_ENV,
    SetupCodes,
    prepare_setup_code_at_start,
)
from row_bot.access.store import AccessStore

pytestmark = pytest.mark.subsystem

ORIGIN = "http://localhost:8080"
# Railway's template: ROW_BOT_PUBLIC_URL=https://${{RAILWAY_PUBLIC_DOMAIN}}.
PUBLIC = "https://row-bot-production.up.railway.app"
CODE_LINE = re.compile(r"^Row-Bot setup code: ([0-9A-Z-]+) ", re.MULTILINE)
LINK_LINE = re.compile(r"^Open this link to sign in: (\S+)$", re.MULTILINE)
WRONG = "That code doesn't match. Check it and try again."
USED = (
    "This code has already been used or has expired. To make a new one, set a new ROW_BOT_SETUP_CODE "
    "in your service's Variables and deploy the change."
)


class Server:
    """A server-mode Row-Bot as ``row-bot serve`` runs it on Railway, and the browsers that open it."""

    def __init__(self, tmp_path) -> None:
        self.store = AccessStore(tmp_path / "mobile.db")
        self.service = AccessService(self.store)
        config = AccessConfig.build(deployment_mode="server", allowed_hosts=("localhost",))
        self.app = FastAPI()
        self.registration = register_access_routes(
            self.app, service=self.service, config=config, setup_codes=SetupCodes(self.service)
        )
        self.app.add_middleware(
            AccessMiddleware, config=config, session_authenticator=self.registration.authenticator
        )

    def start(self, capsys, environ: dict[str, str]) -> tuple[str | None, str | None]:
        """One start of ``row-bot serve``: the code and the link it printed, if it printed them."""
        prepare_setup_code_at_start(dict(environ), store=self.store)
        out, err = capsys.readouterr()
        code, link = CODE_LINE.search(out + err), LINK_LINE.search(out + err)
        return (code.group(1) if code else None), (link.group(1) if link else None)

    def browser(self, address: str = "198.51.100.7") -> TestClient:
        return TestClient(self.app, base_url=ORIGIN, client=(address, 51000), follow_redirects=False)

    def pair(self) -> None:
        """Another browser the owner added from Settings › Devices (an invitation)."""
        created = self.service.create_invitation(intended_origin=ORIGIN)
        self.service.claim_invitation(created.token, intended_origin=ORIGIN, display_name="Laptop")


def code_in(link: str) -> str:
    """What the setup page's script takes from the link: the fragment's code."""
    parts = urlsplit(link)
    assert (parts.path, parts.query) == ("/setup", "")
    return parse_qs(parts.fragment)["code"][0]


def continue_with(browser: TestClient, code: str):
    """The page's POST, the same for a typed code and a link's."""
    return browser.post("/api/access/setup", json={"code": code}, headers={"origin": ORIGIN})


def owned_view(browser: TestClient) -> bool:
    page = browser.get("/setup")
    found = re.search(r'<script id="setup-state" type="application/json">(.*?)</script>', page.text)
    assert page.status_code == 200 and found is not None
    return json.loads(found.group(1))["owned"]


@pytest.fixture(autouse=True)
def _on_railway(monkeypatch):
    monkeypatch.setenv("ROW_BOT_HOSTED_BY", "railway")


def test_the_links_code_signs_in_once(tmp_path, capsys) -> None:
    server = Server(tmp_path)
    code, link = server.start(capsys, {PUBLIC_URL_ENV: PUBLIC})
    assert code is not None and link == f"{PUBLIC}/setup#code={code}"
    browser = server.browser()

    # The browser asks for /setup alone; the code goes only in the page's POST.
    assert owned_view(browser) is False
    signed_in = continue_with(browser, code_in(link))

    assert signed_in.status_code == 200
    assert signed_in.json()["authenticated"] is True and signed_in.json()["next"] == "/app-v2/"
    assert server.service.validate_session(
        browser.cookies.get(server.registration.cookies.names.http), touch=False
    ) is not None
    # Opened again, in this browser or another, the link no longer signs in.
    for again in (browser, server.browser("203.0.113.9")):
        reused = continue_with(again, code_in(link))
        assert reused.status_code == 410
        assert reused.json() == {"ok": False, "error": "code_used", "detail": USED}


def test_a_wrong_code_in_a_link_is_refused_and_counts_toward_the_pause(tmp_path, capsys) -> None:
    server = Server(tmp_path)
    _, link = server.start(capsys, {PUBLIC_URL_ENV: PUBLIC})
    browser = server.browser()

    wrong = [continue_with(browser, "ZZZZ-ZZZZ-ZZZZ") for _ in range(5)]

    assert [answer.status_code for answer in wrong] == [400, 400, 400, 400, 429]
    assert wrong[0].json() == {"ok": False, "error": "wrong_code", "detail": WRONG, "tries_left": None}
    assert wrong[4].json()["detail"] == "Too many tries. Try again in 15 minutes."
    # Paused, the right link waits too.
    assert continue_with(browser, code_in(link)).status_code == 429


def test_a_claimed_server_refuses_the_link_and_shows_its_owner_page(tmp_path, capsys) -> None:
    server = Server(tmp_path)
    _, link = server.start(capsys, {PUBLIC_URL_ENV: PUBLIC})
    server.pair()  # Someone owns this Row-Bot now.
    stranger = server.browser("203.0.113.9")

    late = continue_with(stranger, code_in(link))

    assert late.status_code == 410 and late.json()["detail"] == USED
    assert owned_view(stranger) is True
    assert "This Row-Bot already has an owner" in stranger.get("/setup").text


def test_the_link_is_printed_only_while_unclaimed_and_with_a_public_address(tmp_path, capsys) -> None:
    server = Server(tmp_path)

    # No public address, or none Railway could fill in: the code alone, as before.
    assert server.start(capsys, {})[1] is None
    assert server.start(capsys, {PUBLIC_URL_ENV: "https://"})[1] is None
    code, link = server.start(capsys, {PUBLIC_URL_ENV: PUBLIC + "/"})
    assert code is not None and link == f"{PUBLIC}/setup#code={code}"
    assert continue_with(server.browser(), code_in(link)).status_code == 200

    # Owned: nothing at the next start, nor for a new host code, which would recover it.
    assert server.start(capsys, {PUBLIC_URL_ENV: PUBLIC}) == (None, None)
    assert server.start(capsys, {PUBLIC_URL_ENV: PUBLIC, SETUP_CODE_ENV: "RECO-VERY-C0DE"}) == (None, None)


def test_the_link_never_reaches_row_bots_log_files_or_database(
    tmp_path, capsys, caplog, reload_for_data_dir
) -> None:
    data = tmp_path / "data"
    (logging_config,) = reload_for_data_dir(data, "row_bot.logging_config")
    logging_config.setup_file_logging()  # Row-Bot's own log file, on the root logger, at DEBUG.
    caplog.set_level(logging.DEBUG)
    try:
        server = Server(data)
        code, link = server.start(capsys, {PUBLIC_URL_ENV: PUBLIC, SETUP_CODE_ENV: "R0B0-T1S1-GN1N"})
        logging.getLogger("row_bot.access").info("Row-Bot started")  # The log file is written.
    finally:
        handler = logging_config._file_handler
        logging.getLogger().removeHandler(handler)
        handler.close()
        logging_config._file_handler = None

    assert (code, link) == ("R0B0-T1S1-GN1N", f"{PUBLIC}/setup#code=R0B0-T1S1-GN1N")
    log_files = list((data / "logs").iterdir())
    assert log_files
    persisted = "".join(path.read_text(encoding="utf-8") for path in log_files) + caplog.text
    assert "Row-Bot started" in persisted
    stored = b"".join(path.read_bytes() for path in data.glob("mobile.db*"))
    for secret in (link, "/setup#code=", "R0B0-T1S1-GN1N", "R0B0T1S1GN1N"):
        assert secret.lower() not in persisted.lower()
        assert secret.encode() not in stored
