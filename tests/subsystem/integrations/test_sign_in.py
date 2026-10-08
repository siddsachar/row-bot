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


def until_waiting(plan_id, kind="sign_in"):
    for _ in range(200):
        plan = plans.read_plan(context(), plan_id)
        step = next(s for s in plan["steps"] if s["type"] == kind)
        if step["sign_in"]["authorization_url"]:
            return plan
        time.sleep(0.02)
    raise AssertionError("no authorization page")


def test_adding_an_app_while_another_waits_for_its_sign_in_says_why_it_cannot_yet(hosted):
    """Live: Stripe waited for a sign-in in the browser, and adding Atlassian failed with "This step could
    not finish". Nothing is saved while another change is unfinished; the person is told which."""
    hosted(dcr=True)
    _, plan = api.read_item(owner_id="owner", item_id=item_id())
    waiting = str(uuid4())
    api.start_plan(context(), plan_id=waiting, item_id=item_id(), digest=plan["digest"])
    until_waiting(waiting)
    other = "mcp:curated:linear-mcp"
    _, second = api.read_item(owner_id="owner", item_id=other)
    try:
        failed = api.start_plan(context(), plan_id=str(uuid4()), item_id=other, digest=second["digest"])
        assert failed["state"] == "failed" and "Another app is still being set up or signed in to" in failed["message"]
        assert "Linear" not in json.dumps(config.read_saved_configuration().document["servers"])  # Nothing saved.
    finally:
        plans.cancel(context(), waiting)  # The first sign-in stops; its browser wait ends with it.


def test_a_check_that_times_out_says_so(hosted, monkeypatch):
    """Live: the MCP Registry didn't answer while Atlassian's entry was checked before saving (it fails
    closed), and the setup said only "This step could not finish"."""
    hosted(dcr=True)
    from row_bot.application import capability_configuration_controls as configuration

    def unanswered(**_):
        raise httpx.ReadTimeout("The read operation timed out")
    monkeypatch.setattr(configuration, "execute_mcp_configuration_command", unanswered)
    other = "mcp:curated:linear-mcp"
    _, plan = api.read_item(owner_id="owner", item_id=other)
    failed = api.start_plan(context(), plan_id=str(uuid4()), item_id=other, digest=plan["digest"])
    assert failed["state"] == "failed" and "didn't answer in time" in failed["message"]


def test_retry_ends_a_sign_in_a_restart_interrupted_so_other_apps_can_be_added(hosted):
    """Live: Row-Bot restarted while Stripe waited for its browser sign-in. The sign-in could never finish,
    Retry left it unfinished, and every other app's setup was refused until it was cancelled."""
    hosted(dcr=True)
    _, plan = api.read_item(owner_id="owner", item_id=item_id())
    waiting = str(uuid4())
    api.start_plan(context(), plan_id=waiting, item_id=item_id(), digest=plan["digest"])
    until_waiting(waiting)
    with client_mcp_auth._LOCK:
        flows = dict(client_mcp_auth._FLOWS)
        client_mcp_auth._FLOWS.clear()  # A restart: the browser step's flow is gone with the old process.
    try:
        assert config.configuration_recovery_required()  # Its admission is still unfinished.
        api.settle_item(context(), item_id=item_id())  # The person presses Retry.
        assert not config.configuration_recovery_required()
        assert not config.read_saved_configuration().document["servers"]["Notes"].get("auth")  # Nothing saved.
    finally:
        for flow in flows.values():  # The old process's browser wait ends with it.
            flow.state = "cancelled"
            flow.event.set()


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


def test_looking_things_up_asks_only_for_reads_and_allowing_changes_signs_in_once_more(hosted):
    """Live: Atlassian listed some 40 scopes and Row-Bot asked for every one, writes and deletes too, though
    the person only wanted to look things up. Now that choice, made before signing in, asks for the reads;
    changes stay off until they're allowed from its Access, which asks the service once more."""
    hosted(dcr=True, scopes=["read:jira", "write:jira", "search:jira", "delete:jira", "offline_access"])
    _, plan = api.read_item(owner_id="owner", item_id=item_id())
    plan_id = str(uuid4())
    api.start_plan(context(), plan_id=plan_id, item_id=item_id(), digest=plan["digest"], preset="read_only")
    assert approve(until_waiting(plan_id))["scope"] == ["read:jira search:jira offline_access"]
    assert signed_in(plan_id)["pause"] == "resume"
    paused = plans.resume(context(), plan_id)
    access = next(s for s in paused["steps"] if s["type"] == "access")["access"]
    assert paused["pause"] == "access" and access["limited"] and "asked for read access only" in access["note"]
    refused = plans.resume(context(tools_digest=access["tools_digest"]), plan_id, preset="ask")
    assert refused["pause"] == "access"  # Its sign-in can't make changes: it waits for Read only.
    done = plans.resume(context(tools_digest=access["tools_digest"]), plan_id, preset="read_only")
    assert done["state"] == "completed", done
    assert config.read_saved_configuration().document["servers"]["Notes"]["auth"]["read_only"] is True

    detail, change = api.read_item(owner_id="owner", item_id=item_id(), intent="access")
    assert detail["about"]["access"]["limited"]
    changing = str(uuid4())
    started = api.start_plan(context(tools_digest=detail["about"]["access"]["tools_digest"]), plan_id=changing,
                             item_id=item_id(), intent="access", digest=change["digest"], preset="ask")
    try:
        assert started["pause"] == "sign_in" and started["current_step"] == "allow_changes"
        step = next(s for s in until_waiting(changing, "allow_changes")["steps"] if s["type"] == "allow_changes")
        query = parse_qs(urlsplit(step["sign_in"]["authorization_url"]).query)
        assert query["scope"] == ["read:jira write:jira search:jira delete:jira offline_access"]  # Everything it lists.
        client_mcp_auth.accept_callback(state=query["state"][0], code="synthetic-code")
        assert signed_in(changing)["pause"] == "resume"
        allowed = plans.resume(context(), changing)  # Checked again with the new sign-in, then reconnected.
        assert allowed["state"] == "completed", (allowed["pause"], allowed["message"], [(s["id"], s["state"], s["message"]) for s in allowed["steps"]])
    finally:
        plans.cancel(context(), changing)  # A sign-in left waiting would hold the test process for its 5 minutes.
    saved = config.read_saved_configuration().document["servers"]["Notes"]
    assert "read_only" not in saved["auth"] and api.read_item(owner_id="owner", item_id=item_id())[0]["about"]["access"]["preset"] == "ask"


def test_a_choice_to_make_changes_asks_for_everything_and_a_read_only_setting_follows_the_choice(hosted):
    hosted(dcr=True, scopes=["read:jira", "write:jira"])
    _, plan = api.read_item(owner_id="owner", item_id=item_id())
    plan_id = str(uuid4())
    api.start_plan(context(), plan_id=plan_id, item_id=item_id(), digest=plan["digest"], preset="ask")
    assert approve(until_waiting(plan_id))["scope"] == ["read:jira write:jira"]
    plans.cancel(context(), plan_id)
    _, supabase = api.read_item(owner_id="owner", item_id="mcp:curated:supabase-mcp")  # Its own Read only setting.
    assert next(f for s in supabase["steps"] if s["type"] == "inputs" for f in s["inputs"] if f["key"] == "read_only")["default"] == "true"
    paused = api.start_plan(context(), plan_id=str(uuid4()), item_id="mcp:curated:supabase-mcp",
                            digest=supabase["digest"], preset="ask")
    fields = next(s for s in paused["steps"] if s["type"] == "inputs")["inputs"]
    assert paused["pause"] == "inputs" and next(f for f in fields if f["key"] == "read_only")["default"] == "false"
    plans.cancel(context(), paused["plan_id"])


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
