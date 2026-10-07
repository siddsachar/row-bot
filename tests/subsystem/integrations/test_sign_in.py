"""Sign-in for any hosted server, against a fake authorization server: a 401 at test turns sign-in
on, and Row-Bot signs in with its published client metadata (CIMD), a registered client (DCR) or the
person's own OAuth app, whose secret stays in the keychain. Fakes only; nothing leaves this machine."""
# ruff: noqa: F811 -- shared isolated fixtures
import json
from pathlib import Path
import time
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest

from row_bot import secret_store
from row_bot.application import client_integrations as api
from row_bot.application import client_mcp_auth
from row_bot.integrations import facts, plans
from row_bot.mcp_client import auth, config
from row_bot.runtime import admissions
from tests.subsystem.mcp.test_capability_catalog_controls import owner  # noqa: F401
from tests.subsystem.plugins.conftest import MemoryKeyring

pytestmark = [pytest.mark.platform, pytest.mark.mcp_transport]
MCP = "https://mcp.fake.example/mcp"
ISSUER = "https://auth.fake.example"
CALLBACK = "http://127.0.0.1:8080" + auth.CALLBACK_PATH
SECRET = "synthetic-client-secret-0123"


class FakeAuthorizationServer:
    """The MCP endpoint and its authorization service, behind one fake transport."""

    def __init__(self, *, signs_in=True, cimd=False, dcr=False, scopes=None):
        self.signs_in, self.cimd, self.dcr, self.scopes = signs_in, cimd, dcr, scopes
        self.seen: list[httpx.Request] = []
        self.issued = "synthetic-access-token"

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        url = str(request.url).split("?")[0]
        if url == MCP:
            if not self.signs_in or request.headers.get("authorization") == "Bearer " + self.issued:
                return httpx.Response(200, json={"jsonrpc": "2.0", "id": 0, "result": {}})
            return httpx.Response(401, headers={"www-authenticate":
                'Bearer resource_metadata="https://mcp.fake.example/.well-known/oauth-protected-resource/mcp"'})
        if url == "https://mcp.fake.example/.well-known/oauth-protected-resource/mcp":
            return httpx.Response(200, json={"resource": MCP, "authorization_servers": [ISSUER],
                                             **({"scopes_supported": self.scopes} if self.scopes else {})})
        if url == ISSUER + "/.well-known/oauth-authorization-server":
            return httpx.Response(200, json={"issuer": ISSUER, "authorization_endpoint": ISSUER + "/authorize",
                "token_endpoint": ISSUER + "/token", "response_types_supported": ["code"],
                "code_challenge_methods_supported": ["S256"],
                **({"client_id_metadata_document_supported": True} if self.cimd else {}),
                **({"registration_endpoint": ISSUER + "/register"} if self.dcr else {})})
        if url == ISSUER + "/register" and self.dcr:
            body = json.loads(request.content)
            return httpx.Response(201, json={**body, "client_id": "registered-client"})
        if url == ISSUER + "/token":
            return httpx.Response(200, json={"access_token": self.issued, "token_type": "Bearer", "expires_in": 3600,
                                             "refresh_token": "synthetic-refresh"})
        return httpx.Response(404)

    def form(self, path: str) -> dict:
        request = next(r for r in self.seen if str(r.url).startswith(ISSUER + path))
        return {key: values[0] for key, values in parse_qs(request.content.decode()).items()}


@pytest.fixture
def hosted(owner, monkeypatch):
    document = {"enabled": False, "servers": {"Notes": {"enabled": False, "transport": "streamable_http", "url": MCP,
                                                       "source": {"marketplace": "custom"}}}}
    config.CONFIG_PATH.write_text(json.dumps(document), encoding="utf-8")
    secret_store._set_backend_for_tests(MemoryKeyring())
    facts.invalidate()
    servers = {}

    def install(**fields):
        server = servers["fake"] = FakeAuthorizationServer(**fields)
        monkeypatch.setattr(auth, "PublicTransport", lambda: httpx.MockTransport(server.handle))
        return server
    yield install
    secret_store._set_backend_for_tests(None)


def context(redirect_uri=CALLBACK, **fields):
    return plans.Context(owner_id="owner", mcp_owner_id="owner", validate=lambda: None, local_owner=True,
                         redirect_uri=redirect_uri, **fields)


def item_id():
    return next(row for row in facts.inventory()[0] if row["name"] == "Notes")["id"]


def approve(plan):
    """The person allows access in their browser: the provider calls Row-Bot back with a code."""
    url = next(s for s in plan["steps"] if s["type"] == "sign_in")["sign_in"]["authorization_url"]
    query = parse_qs(urlsplit(url).query)
    client_mcp_auth.accept_callback(state=query["state"][0], code="synthetic-code")
    return query


def signed_in(plan_id):
    for _ in range(200):
        plan = plans.read_plan(context(), plan_id)
        if plan["pause"] == "resume" or plan["state"] == "failed":
            return plan
        time.sleep(0.02)
    raise AssertionError("sign-in did not finish")


def until_waiting(plan_id):
    for _ in range(200):
        plan = plans.read_plan(context(), plan_id)
        step = next(s for s in plan["steps"] if s["type"] == "sign_in")
        if step["sign_in"]["authorization_url"]:
            return plan
        time.sleep(0.02)
    raise AssertionError("no authorization page")


@pytest.mark.parametrize(("mode", "client"), [("cimd", auth.CLIENT_METADATA_URL), ("dcr", "registered-client")])
def test_a_401_at_test_turns_sign_in_on_and_cimd_is_preferred_over_registration(hosted, mode, client):
    server = hosted(cimd=mode == "cimd", dcr=True)
    _, plan = api.read_item(owner_id="owner", item_id=item_id())
    sign_in = next(s for s in plan["steps"] if s["type"] == "sign_in")
    assert sign_in["state"] == "skipped" and "Only if" in sign_in["message"]
    plan_id = str(uuid4())
    paused = api.start_plan(context(), plan_id=plan_id, item_id=item_id(), digest=plan["digest"])
    assert paused["pause"] == "sign_in"
    step = next(s for s in until_waiting(plan_id)["steps"] if s["type"] == "sign_in")
    assert step["state"] == "waiting" and step["sign_in"]["method"] == "oauth_" + mode
    query = approve(until_waiting(plan_id))
    assert query["client_id"] == [client] and query["code_challenge_method"] == ["S256"]
    assert signed_in(plan_id)["pause"] == "resume"
    assert any(str(r.url).startswith(ISSUER + "/register") for r in server.seen) == (mode == "dcr")
    done = plans.resume(context(), plan_id)
    assert done["pause"] == "access", done
    saved = config.read_saved_configuration().document["servers"]["Notes"]
    assert saved["auth"]["mode"] == "oauth" and "synthetic-access-token" not in config.CONFIG_PATH.read_text()
    assert auth.read_credentials(saved["auth"]["credential_ref"])["tokens"]["access_token"] == "synthetic-access-token"


@pytest.mark.parametrize(("dcr", "client"), [(True, "registered-client"), (False, auth.CLIENT_METADATA_URL)])
def test_on_another_port_a_server_registers_row_bot_or_takes_the_documents_loopback_entry(hosted, dcr, client):
    """8080 was taken: the published document lists only Row-Bot's own port exactly, so a server that registers
    clients records Row-Bot's exact address; one that only takes the document gets its portless loopback entry
    (RFC 8252), never a request for the person's own OAuth app."""
    server = hosted(cimd=True, dcr=dcr)
    elsewhere = "http://127.0.0.1:8766" + auth.CALLBACK_PATH
    _, plan = api.read_item(owner_id="owner", item_id=item_id())
    plan_id = str(uuid4())
    assert api.start_plan(context(elsewhere), plan_id=plan_id, item_id=item_id(), digest=plan["digest"])["pause"] == "sign_in"
    step = next(s for s in until_waiting(plan_id)["steps"] if s["type"] == "sign_in")
    assert step["sign_in"]["method"] == ("oauth_dcr" if dcr else "oauth_cimd")
    query = approve(until_waiting(plan_id))
    assert query["client_id"] == [client] and query["redirect_uri"] == [elsewhere]
    assert any(str(r.url).startswith(ISSUER + "/register") for r in server.seen) == dcr


def test_a_server_that_never_asks_keeps_sign_in_skipped(hosted):
    server = hosted(signs_in=False)
    _, plan = api.read_item(owner_id="owner", item_id=item_id())
    done = api.start_plan(context(), plan_id=str(uuid4()), item_id=item_id(), digest=plan["digest"])
    assert done["pause"] == "access" and next(s for s in done["steps"] if s["type"] == "sign_in")["state"] == "skipped"
    assert [str(r.url) for r in server.seen] == [MCP]  # One unauthenticated look, nothing more.


def test_your_own_oauth_app_is_asked_for_when_nothing_else_works_and_its_secret_stays_in_the_keychain(hosted):
    server = hosted()  # Neither CIMD nor registration: like GitHub.
    _, plan = api.read_item(owner_id="owner", item_id=item_id())
    plan_id = str(uuid4())
    paused = api.start_plan(context(), plan_id=plan_id, item_id=item_id(), digest=plan["digest"])
    step = next(s for s in paused["steps"] if s["type"] == "sign_in")
    assert paused["pause"] == "inputs" and step["sign_in"]["method"] == "oauth_client"
    assert paused["next_action"]["label"] == "Add your OAuth app"  # Not "Add key": it asks for an app, not a key.
    assert [(i["key"], i["secret"]) for i in step["inputs"]] == [("client_id", False), ("client_secret", True)]
    assert "http://127.0.0.1" + auth.CALLBACK_PATH in step["inputs"][0]["description"]
    waiting = plans.resume(context(inputs={"client_id": "my-own-app", "client_secret": SECRET}), plan_id)
    assert waiting["pause"] == "sign_in"
    assert approve(until_waiting(plan_id))["client_id"] == ["my-own-app"]
    signed_in(plan_id)
    assert server.form("/token")["client_secret"] == SECRET  # Sent only to the token endpoint.
    saved = config.read_saved_configuration().document["servers"]["Notes"]
    stored = auth.read_credentials(saved["auth"]["credential_ref"])
    assert stored["client"]["client_id"] == "my-own-app" and stored["client"]["client_secret"] == SECRET
    assert SECRET not in config.CONFIG_PATH.read_text()
    assert SECRET not in json.dumps([admissions.receipt("owner", plan_id), plans.read_plan(context(), plan_id)])


def test_a_reviewed_recipe_asks_only_for_its_own_scopes(hosted):
    hosted(cimd=True, scopes=["repo", "read:user", "delete_repo", "admin:org"])  # Everything GitHub-like servers list.
    document = json.loads(config.CONFIG_PATH.read_text())
    document["servers"]["Notes"]["source"]["oauth_scope"] = "repo read:user"
    config.CONFIG_PATH.write_text(json.dumps(document), encoding="utf-8")
    facts.invalidate()
    _, plan = api.read_item(owner_id="owner", item_id=item_id())
    plan_id = str(uuid4())
    api.start_plan(context(), plan_id=plan_id, item_id=item_id(), digest=plan["digest"])
    assert approve(until_waiting(plan_id))["scope"] == ["repo read:user"]
    assert signed_in(plan_id)["pause"] == "resume"


def test_a_refresh_that_fails_asks_to_sign_in_again(hosted):
    from row_bot.mcp_client import runtime
    document = json.loads(config.CONFIG_PATH.read_text())
    ref = uuid4().hex
    document["servers"]["Notes"]["auth"] = {"mode": "oauth", "credential_ref": ref,
        "binding": auth.binding("Notes", document["servers"]["Notes"]), "callback_uri": CALLBACK, "label": "Notes"}
    document["servers"]["Notes"]["enabled"] = True
    config.CONFIG_PATH.write_text(json.dumps(document), encoding="utf-8")
    runtime._update_status("Notes", status="failed",
                           last_error="unhandled errors | OAuthFlowError: No redirect handler provided for authorization code grant")
    facts.invalidate()
    row = facts.read(item_id())
    assert row["readiness"] == "needs_sign_in" and row["next_action"]["label"] == "Sign in again"


def test_the_published_client_metadata_names_itself_and_only_this_computer():
    path = Path(__file__).parents[3] / "docs-site" / "static" / "oauth" / "client-metadata.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["client_id"] == auth.CLIENT_METADATA_URL == "https://row-bot.ai/oauth/client-metadata.json"
    assert urlsplit(document["client_id"]).path.endswith(path.relative_to(path.parents[1]).as_posix())
    assert document["token_endpoint_auth_method"] == "none" and document["application_type"] == "native"
    # Row-Bot treats as listed exactly the addresses the document lists; another port has only its loopback entry.
    assert {auth.document_redirect(uri) for uri in document["redirect_uris"]} == {"listed"}
    assert auth.document_redirect("http://127.0.0.1:8766" + auth.CALLBACK_PATH) == "loopback"
    assert auth.document_redirect("http://localhost:8080" + auth.CALLBACK_PATH) == ""
    assert not {"client_secret", "client_secret_expires_at", "jwks", "jwks_uri"} & set(document)
    for uri in document["redirect_uris"]:
        parts = urlsplit(uri)
        assert parts.scheme == "http" and parts.hostname == "127.0.0.1" and parts.path == auth.CALLBACK_PATH


def test_discovery_reads_no_more_than_its_cap(monkeypatch):
    """An endless answer, to the first look or to a metadata read, is never read past 64 KB."""
    pulled, reads = [], []

    async def endless():
        while True:
            pulled.append(1)
            yield b" " * 4096

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(401 if signs_in else 200, content=endless(), headers={"www-authenticate":
                'Bearer resource_metadata="https://mcp.fake.example/.well-known/oauth-protected-resource/mcp"'})
        if "oauth-protected-resource" in str(request.url):
            reads.append(1)
            return httpx.Response(200, content=endless())
        return httpx.Response(404)

    monkeypatch.setattr(auth, "PublicTransport", lambda: httpx.MockTransport(handle))
    signs_in = False
    assert auth.discover_sign_in(MCP) == {"required": False} and len(pulled) <= 1
    signs_in = True
    pulled.clear()
    assert auth.discover_sign_in(MCP) == {"required": True, "issuer": "", "cimd": False, "dcr": False}
    assert reads and len(pulled) <= len(reads) * (65536 // 4096 + 1)  # Each candidate address stopped at the cap.


def test_discovery_asks_for_and_reads_only_uncompressed_metadata(monkeypatch):
    """A compressed reply could grow far past the cap once unpacked, so discovery never unpacks one."""
    import gzip
    import json as _json
    asked = []
    document = gzip.compress(_json.dumps({"resource": MCP, "authorization_servers": ["https://auth.fake.example"]}).encode())

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(401, headers={"www-authenticate":
                'Bearer resource_metadata="https://mcp.fake.example/.well-known/oauth-protected-resource/mcp"'})
        asked.append((str(request.url), request.headers.get("accept-encoding")))
        if "oauth-protected-resource" in str(request.url):
            return httpx.Response(200, content=document, headers={"content-encoding": "gzip"})
        return httpx.Response(404)

    monkeypatch.setattr(auth, "PublicTransport", lambda: httpx.MockTransport(handle))
    assert auth.discover_sign_in(MCP) == {"required": True, "issuer": "", "cimd": False, "dcr": False}
    assert asked and all(encoding == "identity" for _, encoding in asked)
    assert not any("oauth-authorization-server" in url for url, _ in asked)  # The packed document was never read.
