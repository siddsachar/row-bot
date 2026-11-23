"""The admin console on the Company Server (S6): served from ``ROW_BOT_ORG_CONSOLE_DIR`` only, with its own strict
CSP; a visit without a session goes to sign-in and comes back (to this server's paths only); a member who isn't
an admin gets the plain "You need to be an admin" page; ``/api/me`` says whether the caller is an admin; and a
policy write naming catalog models that don't exist is warned about, not refused. The fake provider for sign-in
and a real (throwaway) Postgres as the runtime role. Security-sensitive: these run in the pull request lane's Org
job.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.fixtures.postgres import import_psycopg

import_psycopg()
pytest.importorskip("joserfc", reason="joserfc is not installed: uv sync --locked --all-extras --group test --group org")

from fastapi.testclient import TestClient  # noqa: E402

from row_bot_org import cli  # noqa: E402
from row_bot_org.app import create_app  # noqa: E402
from row_bot_org.console import CONSOLE_CSP  # noqa: E402
from row_bot_org.db.pool import create_pool  # noqa: E402
from row_bot_org.settings import load_settings  # noqa: E402
from tests.fixtures.oidc_idp import CLIENT_SECRET, PUBLIC_URL, FakeIdp, running_idp  # noqa: E402

pytestmark = pytest.mark.postgres

PEPPER = "a-test-pepper-that-is-long-enough-0123456789"
ORIGIN = {"Origin": PUBLIC_URL}
GATEWAY_KEY = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
CONSOLE_PAGE = "<!doctype html><title>Row-Bot admin console</title><script type=module src=assets/console.js>"
NOT_ADMIN_PAGE = "<!doctype html><title>Row-Bot admin console</title><h1>You need to be an admin</h1>"


@pytest.fixture(autouse=True)
def org_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith("ROW_BOT_ORG_") or name == "ROW_BOT_SECRETS_DIR":
            monkeypatch.delenv(name)


@pytest.fixture
def idp() -> Iterator[FakeIdp]:
    with running_idp("okta") as running:
        yield running


@pytest.fixture
def console_dir(tmp_path: Path) -> Path:
    """A built console's folder, as `npm run console:build` leaves it (the documents and their assets)."""
    folder = tmp_path / "dist-console"
    (folder / "assets").mkdir(parents=True)
    (folder / "console.html").write_text(CONSOLE_PAGE, encoding="utf-8")
    (folder / "console-not-admin.html").write_text(NOT_ADMIN_PAGE, encoding="utf-8")
    (folder / "assets" / "console.js").write_text("export {};", encoding="utf-8")
    (folder / "secret.txt").write_text("not an asset", encoding="utf-8")
    return folder


@contextmanager
def org_server(database, idp: FakeIdp, console: Path | None) -> Iterator[TestClient]:
    env = {"ROW_BOT_ORG_IDP": json.dumps(idp.config(group_map={"IT Admins": "it-admins"},
                                                    admin_groups=["it-admins"])),
           "ROW_BOT_ORG_IDP_CLIENT_SECRET": CLIENT_SECRET, "ROW_BOT_ORG_PUBLIC_URL": PUBLIC_URL,
           "ROW_BOT_ORG_HMAC_PEPPER": PEPPER, "ROW_BOT_ORG_GATEWAY_KEY": GATEWAY_KEY}
    if console is not None:
        env["ROW_BOT_ORG_CONSOLE_DIR"] = str(console)
    pool = create_pool(database.runtime_url, max_size=4)
    with TestClient(create_app(load_settings(env), pool), base_url=PUBLIC_URL) as client:
        yield client


def browser(server: TestClient) -> TestClient:
    return TestClient(server.app, base_url=PUBLIC_URL, headers=ORIGIN)


def sign_in(client: TestClient, idp: FakeIdp, subject: str, groups: list[str], login: str = "/auth/login") -> str:
    """Sign ``client`` in through ``login``; where the sign-in lands."""
    location = client.get(login, follow_redirects=False).headers["location"]
    done = client.get(idp.authorize(location, subject=subject, groups=groups, email=f"{subject}@example.com"),
                      follow_redirects=False)
    assert done.status_code == 303, done.text
    return done.headers["location"]


def person(server: TestClient, idp: FakeIdp, subject: str, groups: list[str] | None = None) -> TestClient:
    client = browser(server)
    sign_in(client, idp, subject, groups or [])
    return client


# -- serving it --------------------------------------------------------------------------------------

def test_without_the_console_folder_there_is_no_console(org_database, idp) -> None:
    with org_server(org_database, idp, None) as server:
        admin = person(server, idp, "it", ["IT Admins"])
        assert admin.get("/console/").status_code == 404
        assert admin.get("/admin/").json() == {"status": "signed_in"}
        # Without a session it is any other unknown path: refused, not sent to sign in.
        assert browser(server).get("/console/", follow_redirects=False).status_code == 401


def test_an_admin_gets_the_console_with_its_strict_csp_and_the_api_keeps_its_own(
        org_database, idp, console_dir) -> None:
    with org_server(org_database, idp, console_dir) as server:
        admin = person(server, idp, "it", ["IT Admins"])
        for path in ("/console/", "/console/policy", "/console/audit/anything"):
            page = admin.get(path)
            assert (page.status_code, page.text) == (200, CONSOLE_PAGE)
            assert page.headers["content-type"].startswith("text/html")
            assert page.headers["content-security-policy"] == CONSOLE_CSP
            assert page.headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in CONSOLE_CSP and "'unsafe-inline'" not in CONSOLE_CSP
        assert admin.get("/console", follow_redirects=False).headers["location"] == "/console/"

        script = admin.get("/console/assets/console.js")
        assert (script.status_code, script.headers["content-type"].split(";")[0]) == (200, "text/javascript")
        assert script.headers["content-security-policy"] == CONSOLE_CSP
        # Only the build's assets: nothing else in the folder, and nothing outside it.
        for path in ("/console/assets/missing.js", "/console/assets/secret.txt"):
            assert admin.get(path).status_code == 404
        for path in ("/console/assets/..%2Fsecret.txt", "/console/secret.txt"):
            assert admin.get(path).text == CONSOLE_PAGE

        api = admin.get("/api/me")
        assert api.headers["content-security-policy"].startswith("default-src 'none'")
        # A sign-in without a return path lands on /admin/, which sends an admin on to the console.
        assert admin.get("/admin/", follow_redirects=False).headers["location"] == "/console/"


def test_a_member_who_isnt_an_admin_gets_the_plain_admin_needed_page(org_database, idp, console_dir) -> None:
    with org_server(org_database, idp, console_dir) as server:
        ada = person(server, idp, "ada", ["Sales"])
        for path in ("/console/", "/console/people"):
            page = ada.get(path)
            assert (page.status_code, page.text) == (403, NOT_ADMIN_PAGE)
            assert page.headers["content-security-policy"] == CONSOLE_CSP
        assert ada.get("/api/admin/people").status_code == 403


def test_the_stored_admin_role_opens_the_console_too(org_database, idp, console_dir, monkeypatch) -> None:
    with org_server(org_database, idp, console_dir) as server:
        owner = person(server, idp, "owner")
        assert owner.get("/console/").status_code == 403
        monkeypatch.setenv("ROW_BOT_ORG_MIGRATION_DATABASE_URL", org_database.owner_url)
        assert cli.main(["principals", "grant-admin", "owner@example.com"]) == 0
        assert owner.get("/console/").status_code == 200


# -- signing in from the console ---------------------------------------------------------------------

def test_a_visit_without_a_session_signs_in_and_comes_back_to_the_same_page(
        org_database, idp, console_dir) -> None:
    with org_server(org_database, idp, console_dir) as server:
        client = browser(server)
        sent = client.get("/console/policy", follow_redirects=False)
        assert sent.status_code == 303
        login = urlsplit(sent.headers["location"])
        assert (login.path, parse_qs(login.query)) == ("/auth/login", {"next": ["/console/policy"]})
        # The assets aren't pages: they are refused like any API call.
        assert client.get("/console/assets/console.js", follow_redirects=False).status_code == 401

        landed = sign_in(client, idp, "it", ["IT Admins"], login=sent.headers["location"])
        assert landed == PUBLIC_URL + "/console/policy"
        assert client.get(landed).text == CONSOLE_PAGE

        # The return path was for that sign-in only: the next one lands on /admin/ again.
        assert sign_in(client, idp, "it", ["IT Admins"]) == PUBLIC_URL + "/admin/"


@pytest.mark.parametrize("target", ["//evil.example/console/", "https://evil.example/", "/\\evil.example",
                                    "console/", "/console/?then=//evil.example", "/console/\x00"])
def test_a_return_path_that_isnt_this_servers_own_is_refused(org_database, idp, console_dir, target) -> None:
    with org_server(org_database, idp, console_dir) as server:
        refused = browser(server).get("/auth/login", params={"next": target}, follow_redirects=False)
        assert (refused.status_code, refused.json()) == (400, {"error": "invalid_return_path"})


# -- the API's additions -----------------------------------------------------------------------------

def test_me_says_whether_the_caller_is_an_admin(org_database, idp) -> None:
    with org_server(org_database, idp, None) as server:
        admin = person(server, idp, "it", ["IT Admins"]).get("/api/me").json()
        member = person(server, idp, "ada", ["Sales"]).get("/api/me").json()
        assert (admin["admin"], member["admin"]) == (True, False)
        assert member["email"] == "ada@example.com" and member["status"] == "active"


def test_a_policy_naming_models_the_catalog_doesnt_have_is_kept_with_a_warning(org_database, idp) -> None:
    with org_server(org_database, idp, None) as server:
        admin = person(server, idp, "it", ["IT Admins"])
        endpoint = admin.post("/api/admin/endpoints", json={
            "name": "gpu-box", "api": "openai", "base_url": "http://gpu-box.example.test:8000/v1"}).json()
        assert admin.post("/api/admin/models", json={"endpoint_id": endpoint["id"], "name": "company-chat",
                                                     "kind": "chat"}).status_code == 201

        company = admin.put("/api/admin/policy", json={"version": 0, "models": {"allowed": ["company-chat",
                                                                                           "no-such-model"]}})
        assert company.status_code == 200
        assert company.json()["unknown_models"] == ["no-such-model"]
        assert company.json()["policy"]["models"]["allowed"] == ["company-chat", "no-such-model"]

        group = admin.put("/api/admin/policy/groups/Sales", json={"version": 0, "models": {"allowed": ["later"]}})
        assert (group.status_code, group.json()["unknown_models"]) == (200, ["later"])
        everything = admin.put("/api/admin/policy", json={"version": 1, "models": {"allowed": ["*"]}})
        assert everything.json()["unknown_models"] == []

