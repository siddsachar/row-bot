"""First sign-in with a setup code (H1) and the pages around it (U1, U2), over HTTP."""

from __future__ import annotations

import json
import re

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from row_bot.access.config import AccessConfig
from row_bot.access.middleware import AccessMiddleware
from row_bot.access.routes import register_access_routes
from row_bot.access.service import AccessService, InvitationClaimError
from row_bot.access.setup_code import SETUP_CODE_ENV, SetupCodes, prepare_setup_code_at_start
from row_bot.access.store import AccessStore

ORIGIN = "http://localhost:8080"
CODE_LINE = re.compile(r"Row-Bot setup code: ([0-9A-Z-]+)")
USED = (
    "This code has already been used or has expired. To make a new one, change "
    "the setup code in your pod's settings and restart it."
)


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


class Pod:
    """A server-mode Row-Bot as ``row-bot serve`` runs it, and the browsers that open it."""

    def __init__(self, tmp_path, *, mode: str = "server") -> None:
        self.store = AccessStore(tmp_path / "mobile.db")
        self.service = AccessService(self.store)
        self.clock = Clock()
        config = AccessConfig.build(deployment_mode=mode, allowed_hosts=("localhost",))
        self.app = FastAPI()
        self.registration = register_access_routes(
            self.app,
            service=self.service,
            config=config,
            setup_codes=SetupCodes(self.service, clock=self.clock) if mode == "server" else None,
        )
        self.app.add_middleware(
            AccessMiddleware,
            config=config,
            session_authenticator=self.registration.authenticator,
        )

    def start(self, capsys, code: str | None = None) -> str | None:
        """One start of ``row-bot serve``; returns the code it printed, if it printed one."""
        environ = {} if code is None else {SETUP_CODE_ENV: code}
        prepare_setup_code_at_start(environ, store=self.store)
        found = CODE_LINE.search(capsys.readouterr().err)
        return found.group(1) if found else None

    def browser(self, address: str = "198.51.100.7") -> TestClient:
        return TestClient(self.app, base_url=ORIGIN, client=(address, 51000), follow_redirects=False)

    def session(self, browser: TestClient) -> str | None:
        return browser.cookies.get(self.registration.cookies.names.http)

    def pair(self, name: str) -> str:
        """Another browser the owner added from Settings › Devices (an invitation)."""
        created = self.service.create_invitation(intended_origin=ORIGIN)
        return self.service.claim_invitation(
            created.token, intended_origin=ORIGIN, display_name=name
        ).session_token


def enter(browser: TestClient, code: str):
    return browser.post("/api/access/setup", json={"code": code}, headers={"origin": ORIGIN})


def page_state(response) -> dict:
    found = re.search(r'<script id="setup-state" type="application/json">(.*?)</script>', response.text)
    assert found is not None
    return json.loads(found.group(1))


@pytest.fixture(autouse=True)
def _hosted_by_pikapods(monkeypatch):
    monkeypatch.setenv("ROW_BOT_HOSTED_BY", "pikapods")


def test_opening_a_new_pod_leads_to_the_claim_page_and_its_code_signs_in(tmp_path, capsys) -> None:
    pod = Pod(tmp_path)
    assert pod.start(capsys, "R0B0-T1S1-GN1N") == "R0B0-T1S1-GN1N"
    browser = pod.browser()

    root = browser.get("/", headers={"accept": "text/html"})
    connect = browser.get(root.headers["location"])
    page = browser.get(connect.headers["location"])

    assert root.status_code == connect.status_code == 303
    assert connect.headers["location"] == "/setup"
    assert page.status_code == 200
    assert "Welcome to your Row-Bot" in page.text
    assert "Enter the setup code from PikaPods to make this browser yours." in page.text
    assert "Where&#x27;s my code?" in page.text
    assert (
        "In PikaPods, open your pod&#x27;s More › Show Logs and copy the setup code."
        in page.text
    )
    assert "Row-Bot is starting. This takes a few seconds." in page.text
    assert page.headers["cache-control"] == "no-store"
    assert "connect-src 'self'" in page.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert page_state(page)["owned"] is False

    # Dashes, spaces and case don't matter, and O, I and L read as 0, 1, 1.
    signed_in = enter(browser, " robo tisi gnin ")

    assert signed_in.status_code == 200
    assert signed_in.json()["recovered"] is False
    assert signed_in.json()["next"] == "/app-v2/"
    session = pod.service.validate_session(pod.session(browser), touch=False)
    assert session is not None
    assert session.device.display_name == "Connected browser"
    assert browser.get("/setup").headers["location"] == "/app-v2/"


def test_without_a_host_code_row_bot_prints_its_own_and_it_signs_in(tmp_path, capsys) -> None:
    pod = Pod(tmp_path)
    printed = pod.start(capsys)
    assert printed is not None and re.fullmatch(r"[0-9A-Z]{4}-[0-9A-Z]{4}-[0-9A-Z]{4}", printed)

    assert enter(pod.browser(), printed.lower()).status_code == 200


def test_wrong_codes_count_down_then_pause_only_that_address(tmp_path, capsys) -> None:
    pod = Pod(tmp_path)
    code = pod.start(capsys)
    browser = pod.browser("198.51.100.7")

    first = enter(browser, "ZZZZ-ZZZZ-ZZZZ")
    second = enter(browser, "nope")
    third = enter(browser, "nope")
    fourth = enter(browser, "nope")
    fifth = enter(browser, "nope")
    still = enter(browser, code)

    assert first.status_code == second.status_code == 400
    assert first.json()["error"] == "wrong_code"
    assert first.json()["detail"] == "That code doesn't match. Check it and try again."
    assert first.json()["tries_left"] is None
    assert third.json()["detail"] == (
        "That code doesn't match. Check it and try again. 2 tries left before a 15-minute pause."
    )
    assert fourth.json()["detail"].endswith("1 try left before a 15-minute pause.")
    assert fifth.status_code == still.status_code == 429
    assert fifth.json()["detail"] == "Too many tries. Try again in 15 minutes."
    assert fifth.headers["retry-after"] == "900"
    assert page_state(browser.get("/setup"))["paused"] == 900
    # Someone else's wrong tries never keep the owner out.
    assert enter(pod.browser("203.0.113.9"), "nope").status_code == 400

    pod.clock.now += 14 * 60
    assert enter(browser, code).json()["detail"] == "Too many tries. Try again in 1 minute."
    pod.clock.now += 60
    assert enter(browser, code).status_code == 200


def test_a_code_signs_in_once(tmp_path, capsys) -> None:
    pod = Pod(tmp_path)
    pod.start(capsys, "ABCD-EFGH-JKMN")
    assert enter(pod.browser(), "ABCD-EFGH-JKMN").status_code == 200

    again = enter(pod.browser("203.0.113.9"), "ABCD-EFGH-JKMN")
    # After a restart the host's code is still set, and still used.
    assert pod.start(capsys, "ABCD-EFGH-JKMN") is None
    after_restart = enter(pod.browser("203.0.113.9"), "ABCD-EFGH-JKMN")

    assert again.status_code == after_restart.status_code == 410
    assert again.json()["detail"] == after_restart.json()["detail"] == USED


def test_row_bots_own_code_stops_working_once_someone_owns_it(tmp_path, capsys) -> None:
    pod = Pod(tmp_path)
    code = pod.start(capsys)
    pod.pair("Laptop")

    late = enter(pod.browser(), code)
    page = pod.browser().get("/setup")

    assert late.status_code == 410
    assert late.json()["detail"] == USED
    assert "This Row-Bot already has an owner" in page.text
    assert page_state(page)["owned"] is True


def test_a_new_host_code_recovers_once_and_signs_out_every_other_browser(tmp_path, capsys) -> None:
    pod = Pod(tmp_path)
    pod.start(capsys, "FIRS-TC0D-E000")
    owner = pod.browser("198.51.100.7")
    assert enter(owner, "FIRS-TC0D-E000").status_code == 200
    phone = pod.pair("Phone")
    left_open = pod.service.create_invitation(intended_origin=ORIGIN)

    # The owner lost both browsers, set a new code in the pod's settings and restarted it.
    assert pod.start(capsys, "SEC0-ND0C-0DE0") is None
    stranger_view = pod.browser("203.0.113.9").get("/setup")
    recovered = enter(pod.browser("203.0.113.9"), "sec0 nd0c 0de0")

    assert "This Row-Bot already has an owner" in stranger_view.text
    assert (
        "If it&#x27;s yours, open it in a browser that&#x27;s already signed in, "
        "or add this browser from Settings › Devices there." in stranger_view.text
    )
    assert "Lost access?" in stranger_view.text
    assert (
        "Change the setup code in your pod&#x27;s settings and restart it, then enter the new code here."
        in stranger_view.text
    )
    assert "Using this code signs out every other browser." in stranger_view.text
    assert 'href="https://www.pikapods.com/pods"' in stranger_view.text
    assert recovered.status_code == 200
    assert recovered.json()["recovered"] is True
    assert pod.service.validate_session(pod.session(owner), touch=False) is None
    assert pod.service.validate_session(phone, touch=False) is None
    # An invitation made before the recovery no longer lets anyone in.
    with pytest.raises(InvitationClaimError):
        pod.service.claim_invitation(left_open.token, intended_origin=ORIGIN, display_name="Laptop")
    assert [device.display_name for device in pod.service.list_devices(include_revoked=False)] == [
        "Connected browser"
    ]
    assert owner.get("/setup").status_code == 200
    assert enter(pod.browser("192.0.2.1"), "SEC0-ND0C-0DE0").status_code == 410
    # Only the newest retired code is recognised; an older one is simply wrong.
    assert enter(pod.browser("192.0.2.1"), "FIRS-TC0D-E000").json()["error"] == "wrong_code"


def test_without_a_known_host_the_page_points_to_row_bots_log(tmp_path, capsys, monkeypatch) -> None:
    monkeypatch.delenv("ROW_BOT_HOSTED_BY")
    pod = Pod(tmp_path)
    pod.start(capsys)

    unclaimed = pod.browser().get("/setup")
    pod.pair("Laptop")
    owned = pod.browser().get("/setup")

    assert "Enter the setup code to make this browser yours." in unclaimed.text
    assert (
        "Row-Bot shows the setup code in its log when it starts. "
        "Look for “Row-Bot setup code”." in unclaimed.text
    )
    assert "Manage pod" not in unclaimed.text + owned.text
    assert "Give this Row-Bot a new setup code in its server settings and restart it" in owned.text


def test_invitation_links_keep_working_on_a_server(tmp_path) -> None:
    pod = Pod(tmp_path)
    created = pod.service.create_invitation(intended_origin=ORIGIN)

    page = pod.browser().get("/connect", params={"invitation": created.token})

    assert page.status_code == 200
    assert "Connect this browser to Row-Bot?" in page.text


def test_the_desktop_app_has_no_setup_code_and_keeps_its_connect_page(tmp_path) -> None:
    pod = Pod(tmp_path, mode="desktop")
    browser = pod.browser()

    assert browser.get("/setup").status_code == 404
    assert enter(browser, "ABCD-EFGH-JKMN").status_code == 404
    assert "Connect to this Row-Bot" in browser.get("/connect").text
