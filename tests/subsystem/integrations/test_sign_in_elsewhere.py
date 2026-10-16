"""Signing in to a hosted app from another device (F27), against a fake authorization server.

A phone or remote browser can't reach Row-Bot's loopback callback. With Row-Bot's public address and a client
Row-Bot registers, its browser returns there by itself; otherwise it ends on an address that doesn't load, which
the person pastes back, checked exactly as the callback route checks one; or, where the service offers it, the
person enters a code on the service's own page (RFC 8628). Fakes only; nothing leaves this machine.
"""
# ruff: noqa: F811 -- shared isolated fixtures
import asyncio
import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

import httpx
import pytest

from row_bot.application import client_integrations as api
from row_bot.application import client_mcp_auth
from row_bot.integrations import plans
from row_bot.mcp_client import auth, config, device_code
from tests.subsystem.integrations.test_sign_in import (  # noqa: F401 -- fixtures
    CALLBACK, ISSUER, MCP, FakeAuthorizationServer, hosted, item_id, owner)

pytestmark = [pytest.mark.platform, pytest.mark.mcp_transport]
PUBLIC = "https://rowbot.example.test" + auth.CALLBACK_PATH
PASTE = auth.PASTE_CALLBACK


class DeviceServer(FakeAuthorizationServer):
    """The fake service, also offering the device grant: each token poll takes the next of ``answers``."""

    def __init__(self, *, answers=("ok",), refuse_codes=False, page="https://auth.fake.example/device", **fields):
        super().__init__(**fields)
        self.answers, self.refuse_codes, self.page = list(answers), refuse_codes, page

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        if url == ISSUER + "/.well-known/oauth-authorization-server":
            self.seen.append(request)
            document = json.loads(super().handle(request).content)
            self.seen.pop()
            return httpx.Response(200, json={**document, "device_authorization_endpoint": ISSUER + "/device",
                                             "grant_types_supported": ["authorization_code", "refresh_token", auth.DEVICE_GRANT]})
        if url == ISSUER + "/device":
            self.seen.append(request)
            if self.refuse_codes:  # GitHub, when an OAuth app has device flow switched off.
                return httpx.Response(400, json={"error": "device_flow_disabled"})
            return httpx.Response(200, json={"device_code": "synthetic-device-code", "user_code": "WDJB-MJHT",
                                             "verification_uri": self.page, "expires_in": 900, "interval": 5})
        if url == ISSUER + "/token" and parse_qs(request.content.decode()).get("grant_type") == [auth.DEVICE_GRANT]:
            self.seen.append(request)
            answer = self.answers.pop(0) if self.answers else "authorization_pending"
            if answer != "ok":
                return httpx.Response(400, json={"error": answer})
            return httpx.Response(200, json={"access_token": self.issued, "token_type": "Bearer", "expires_in": 3600,
                                             "refresh_token": "synthetic-refresh"})
        return super().handle(request)


def phone(redirect_uri="", owner_id="owner", **fields):
    """Another device: Row-Bot's public callback when it has one, else none."""
    return plans.Context(owner_id=owner_id, mcp_owner_id="owner", validate=lambda: None, redirect_uri=redirect_uri,
                         remote=True, **fields)


def desktop(**fields):
    return plans.Context(owner_id="owner", mcp_owner_id="owner", validate=lambda: None, redirect_uri=CALLBACK, **fields)


def start(ctx, server_name="Notes", **fields):
    _, plan = api.read_item(owner_id=ctx.owner_id, item_id=item_id(server_name))
    plan_id = str(uuid4())
    api.start_plan(ctx, plan_id=plan_id, item_id=item_id(server_name), digest=plan["digest"], **fields)
    return plan_id


def sign_in_step(plan):
    return next(s for s in plan["steps"] if s["type"] == "sign_in")


def waiting(plan_id, ctx, *, field="authorization_url"):
    for _ in range(250):
        plan = plans.read_plan(ctx, plan_id)
        if sign_in_step(plan)["sign_in"].get(field):
            return plan
        if plan["state"] in {"failed", "expired", "cancelled"}:
            raise AssertionError(plan["message"])
        time.sleep(0.02)
    raise AssertionError("the sign-in never showed its " + field)


def flow_of(owner_id="owner"):
    with client_mcp_auth._LOCK:
        return next(f for f in client_mcp_auth._FLOWS.values() if f.owner_id == owner_id and f.state == "waiting")


def settled(plan_id, ctx):
    for _ in range(250):
        plan = plans.read_plan(ctx, plan_id)
        if plan["pause"] == "resume" or plan["state"] in {"failed", "expired", "cancelled"}:
            return plan
        time.sleep(0.02)
    raise AssertionError("the sign-in did not finish")


def landed(plan, **extra):
    """The address the phone's browser ends on after allowing access: the callback, with the code and state."""
    query = parse_qs(urlsplit(sign_in_step(plan)["sign_in"]["authorization_url"]).query)
    return query, PASTE + "?" + urlencode({"code": "synthetic-code", "state": query["state"][0], **extra})


def tokens_asked(server):
    return [r for r in server.seen if str(r.url).startswith(ISSUER + "/token")]


def refused(ctx, plan_id, address):
    with pytest.raises(auth.McpAuthError) as caught:
        plans.finish_sign_in(ctx, plan_id, address)
    return caught.value.code


def test_a_phone_without_a_public_address_signs_in_by_pasting_the_address_its_browser_ends_on(hosted):
    server = hosted(dcr=True)
    ctx = phone()
    plan_id = start(ctx)
    try:
        plan = waiting(plan_id, ctx)
        step = sign_in_step(plan)["sign_in"]
        assert (plan["pause"], step["delivery"], step["method"]) == ("sign_in", "paste", "oauth_dcr")
        assert 290 <= step["expires_in"] <= 300 and step["received"] is False  # The link lasts five minutes.
        query, address = landed(plan, iss=ISSUER, scope="read")  # Extra answers a service adds are never read.
        assert query["redirect_uri"] == [PASTE] and query["code_challenge_method"] == ["S256"]
        assert json.loads(next(r for r in server.seen if str(r.url) == ISSUER + "/register").content)["redirect_uris"] == [PASTE]
        assert not tokens_asked(server)  # Nothing is exchanged until the address comes back.
        pasted = plans.finish_sign_in(ctx, plan_id, "  " + address + "\n")  # Copied with spaces round it.
        assert sign_in_step(pasted)["sign_in"]["received"] or pasted["pause"] == "resume"
        assert settled(plan_id, ctx)["pause"] == "resume"
        [exchange] = tokens_asked(server)
        form = {key: values[0] for key, values in parse_qs(exchange.content.decode()).items()}
        assert (form["code"], form["redirect_uri"]) == ("synthetic-code", PASTE)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest()).rstrip(b"=").decode()
        assert challenge == query["code_challenge"][0]  # PKCE: only this sign-in could redeem the code.
        assert plans.resume(ctx, plan_id)["pause"] == "access"
        saved = config.read_saved_configuration().document["servers"]["Notes"]["auth"]
        assert auth.read_credentials(saved["credential_ref"])["tokens"]["access_token"] == "synthetic-access-token"
        assert refused(ctx, plan_id, address) == "mcp_auth_address_used"  # One answer per sign-in.
    finally:
        plans.cancel(ctx, plan_id)


@pytest.mark.parametrize(("case", "code"), [
    ("wrong state", "mcp_auth_address_other"),
    ("another host", "mcp_auth_address_invalid"),
    ("another port", "mcp_auth_address_invalid"),
    ("https", "mcp_auth_address_invalid"),
    ("another path", "mcp_auth_address_invalid"),
    ("a user in front", "mcp_auth_address_invalid"),
    ("two codes", "mcp_auth_address_invalid"),
    ("two states", "mcp_auth_address_invalid"),
    ("its own redirect", "mcp_auth_address_invalid"),
    ("its own verifier", "mcp_auth_address_invalid"),
    ("no code", "mcp_auth_address_invalid"),
    ("empty", "mcp_auth_address_invalid"),
    ("not an address", "mcp_auth_address_invalid"),
    ("a script", "mcp_auth_address_invalid"),
    ("too long", "mcp_auth_address_invalid"),
    ("a line break", "mcp_auth_address_invalid"),
])
def test_a_pasted_address_that_isnt_this_sign_ins_own_callback_is_refused_and_changes_nothing(hosted, case, code):
    server = hosted(dcr=True)
    ctx = phone()
    plan_id = start(ctx)
    try:
        plan = waiting(plan_id, ctx)
        query, good = landed(plan)
        state = query["state"][0]
        path = auth.CALLBACK_PATH
        address = {
            "wrong state": PASTE + "?" + urlencode({"code": "c", "state": "s" * 43}),
            "another host": "http://evil.example" + path + "?" + urlencode({"code": "c", "state": state}),
            "another port": "http://127.0.0.1:8080" + path + "?" + urlencode({"code": "c", "state": state}),
            "https": "https://127.0.0.1" + path + "?" + urlencode({"code": "c", "state": state}),
            "another path": "http://127.0.0.1/api/v1/accounts/google/auth/callback?" + urlencode({"code": "c", "state": state}),
            "a user in front": "http://127.0.0.1@evil.example" + path + "?" + urlencode({"code": "c", "state": state}),
            "two codes": good + "&code=injected",
            "two states": good + "&state=" + "s" * 43,
            "its own redirect": good + "&" + urlencode({"redirect_uri": "https://evil.example/cb"}),
            "its own verifier": good + "&code_verifier=" + "v" * 43,
            "no code": PASTE + "?" + urlencode({"state": state}),
            "empty": "   ",
            "not an address": "I signed in",
            "a script": "javascript:alert(document.cookie)",
            "too long": good + "&pad=" + "x" * 9000,
            "a line break": good.replace("?", "?\n"),
        }[case]
        assert refused(ctx, plan_id, address) == code
        assert sign_in_step(plans.read_plan(ctx, plan_id))["state"] == "waiting" and not tokens_asked(server)
        assert flow_of().state == "waiting" and not flow_of().callback_used  # Still waiting for its own address.
        plans.finish_sign_in(ctx, plan_id, good)  # The right address still finishes it.
        assert settled(plan_id, ctx)["pause"] == "resume"
    finally:
        plans.cancel(ctx, plan_id)


def test_a_pasted_address_never_finishes_another_sign_in(hosted):
    """Another owner's sign-in waiting at the same time: its address, pasted here, is refused and left alone."""
    server = hosted(dcr=True)
    ctx = phone()
    plan_id = start(ctx)
    other = client_mcp_auth.Flow("someone-else", str(uuid4()), "server", "Notes", None, "r", {}, PASTE, lambda: None,
                                 delivery="paste", state="waiting", oauth_state="o" * 43)
    with client_mcp_auth._LOCK:
        client_mcp_auth._FLOWS[(other.owner_id, other.command_id)] = other
    try:
        waiting(plan_id, ctx)
        theirs = PASTE + "?" + urlencode({"code": "their-code", "state": other.oauth_state})
        assert refused(ctx, plan_id, theirs) == "mcp_auth_address_other"
        assert not other.callback_used and other.code == "" and not other.event.is_set()
        assert not tokens_asked(server)
    finally:
        with client_mcp_auth._LOCK:
            client_mcp_auth._FLOWS.pop((other.owner_id, other.command_id), None)
        plans.cancel(ctx, plan_id)


def test_an_earlier_sign_ins_address_is_another_sign_in(hosted):
    """Stopped and started again: the first link's address can't finish the second sign-in."""
    hosted(dcr=True)
    ctx = phone()
    first = start(ctx)
    _, old = landed(waiting(first, ctx))
    plans.cancel(ctx, first)
    assert refused(ctx, first, old) == "mcp_auth_address_expired"  # Its own sign-in has ended.
    _, plan = api.read_item(owner_id="owner", item_id=item_id())
    second = str(uuid4())
    api.start_plan(ctx, plan_id=second, item_id=item_id(), digest=plan["digest"])
    try:
        waiting(second, ctx)
        assert refused(ctx, second, old) == "mcp_auth_address_other"
    finally:
        plans.cancel(ctx, second)


def test_an_address_pasted_after_its_five_minutes_is_refused_and_the_setup_offers_to_start_again(hosted):
    """B338 holds for a pasted address too: past its time the sign-in expires, and connecting again is offered."""
    server = hosted(dcr=True)
    ctx = phone()
    plan_id = start(ctx)
    _, address = landed(waiting(plan_id, ctx))
    flow = flow_of()
    flow.created -= client_mcp_auth._TTL + 1  # Five minutes pass before the address comes back.
    assert refused(ctx, plan_id, address) == "mcp_auth_address_expired"
    flow.event.set()  # Its own wait ends.
    stopped = settled(plan_id, ctx)
    assert stopped["state"] == "expired" and "wasn't finished in time" in stopped["message"]
    assert not tokens_asked(server)
    found, fresh = api.read_item(owner_id="owner", item_id=item_id())
    assert found["entry"]["next_action"]["kind"] == "continue_setup" and fresh is not None
    assert not config.configuration_recovery_required()
    assert refused(ctx, plan_id, address) == "mcp_auth_address_expired"


def test_a_pasted_refusal_ends_the_sign_in_as_the_callback_would(hosted):
    server = hosted(dcr=True)
    ctx = phone()
    plan_id = start(ctx)
    query, _ = landed(waiting(plan_id, ctx))
    plans.finish_sign_in(ctx, plan_id, PASTE + "?" + urlencode({"error": "access_denied", "state": query["state"][0]}))
    assert settled(plan_id, ctx)["state"] == "failed"
    assert not tokens_asked(server)


def test_the_callback_route_never_takes_a_pasted_sign_ins_state(hosted):
    """What the session-less callback accepts doesn't widen: a pasted sign-in's state never finishes there."""
    server = hosted(dcr=True)
    ctx = phone()
    plan_id = start(ctx)
    try:
        query, _ = landed(waiting(plan_id, ctx))
        with pytest.raises(auth.McpAuthError, match="mcp_auth_callback_invalid"):
            client_mcp_auth.accept_callback(state=query["state"][0], code="synthetic-code")
        with pytest.raises(auth.McpAuthError, match="mcp_auth_callback_invalid"):
            client_mcp_auth.accept_callback(state="n" * 43, code="synthetic-code")  # No sign-in waits for it.
        assert flow_of().state == "waiting" and not flow_of().callback_used and not tokens_asked(server)
    finally:
        plans.cancel(ctx, plan_id)


def test_a_phone_with_a_public_address_signs_in_there_when_row_bot_registers_its_client(hosted):
    server = hosted(dcr=True, cimd=True)
    ctx = phone(PUBLIC)
    plan_id = start(ctx)
    plan = waiting(plan_id, ctx)
    step = sign_in_step(plan)["sign_in"]
    assert (step["delivery"], step["method"]) == ("public", "oauth_dcr")  # Registered: the document lists no public address.
    query = parse_qs(urlsplit(step["authorization_url"]).query)
    assert query["redirect_uri"] == [PUBLIC]
    assert json.loads(next(r for r in server.seen if str(r.url) == ISSUER + "/register").content)["redirect_uris"] == [PUBLIC]
    with pytest.raises(auth.McpAuthError, match="mcp_auth_address_expired"):  # Nothing to paste: it comes back by itself.
        plans.finish_sign_in(ctx, plan_id, PUBLIC + "?" + urlencode({"code": "c", "state": query["state"][0]}))
    client_mcp_auth.accept_callback(state=query["state"][0], code="synthetic-code")  # The browser, with no session.
    assert settled(plan_id, ctx)["pause"] == "resume"
    with pytest.raises(auth.McpAuthError, match="mcp_auth_callback_invalid"):
        client_mcp_auth.accept_callback(state=query["state"][0], code="synthetic-code")  # Once.


@pytest.mark.parametrize(("where", "signs_in", "own", "delivery", "method", "redirect"), [
    ("public", {"cimd": True}, False, "paste", "oauth_cimd", PASTE),  # The document lists only loopback.
    ("public", {"dcr": True}, True, "paste", "oauth_client", PASTE),  # The person's own app lists one address.
    ("none", {"dcr": True}, False, "paste", "oauth_dcr", PASTE),
    ("none", {"cimd": True, "dcr": True}, False, "paste", "oauth_cimd", PASTE),
    ("desktop", {"dcr": True}, False, "this_computer", "oauth_dcr", CALLBACK),  # Unchanged on this computer.
])
def test_the_public_address_is_used_only_by_a_client_row_bot_registers_there(hosted, where, signs_in, own, delivery,
                                                                              method, redirect):
    hosted(**signs_in)
    if own:
        document = json.loads(config.CONFIG_PATH.read_text(encoding="utf-8"))
        document["servers"]["Notes"]["source"]["oauth_client"] = "required"
        config.CONFIG_PATH.write_text(json.dumps(document), encoding="utf-8")
        from row_bot.integrations import facts
        facts.invalidate()
    ctx = desktop() if where == "desktop" else phone(PUBLIC if where == "public" else "")
    plan_id = start(ctx)
    try:
        if own:
            paused = plans.read_plan(ctx, plan_id)
            assert paused["pause"] == "inputs"
            assert f"Use {PASTE} as its callback URL" in sign_in_step(paused)["inputs"][0]["description"]
            plans.resume(phone(PUBLIC, inputs={"client_id": "my-own-app"}), plan_id)
        step = sign_in_step(waiting(plan_id, ctx))["sign_in"]
        assert (step["delivery"], step["method"]) == (delivery, method)
        assert parse_qs(urlsplit(step["authorization_url"]).query)["redirect_uri"] == [redirect]
    finally:
        plans.cancel(ctx, plan_id)


# --- A code to enter on the service's own page (RFC 8628) ------------------------------------------------------

def device_storage():
    return auth.TokenStorage(uuid4().hex, "b" * 64, staged=True)


def run_device(server, storage, *, waits=None, deadline=None):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(server.handle)) as http:
            code = await device_code.start(http, MCP, storage, callback_uri=PASTE)
            if deadline is not None:
                code.deadline = deadline
            paused = waits if waits is not None else []

            async def wait(seconds):
                paused.append(seconds)
            await device_code.finish(http, code, storage, wait=wait, check=lambda: None)
            return code
    return asyncio.run(go())


def test_a_code_is_asked_about_at_the_services_pace_until_it_is_allowed():
    server = DeviceServer(dcr=True, answers=["authorization_pending", "slow_down", "authorization_pending", "ok"])
    storage, waits = device_storage(), []
    code = run_device(server, storage, waits=waits)
    assert (code.user_code, code.verification_uri) == ("WDJB-MJHT", "https://auth.fake.example/device")
    assert waits == [5, 5, 10, 10]  # Its interval, and five seconds more after "slow down".
    assert storage.data["tokens"]["access_token"] == "synthetic-access-token"
    registered = json.loads(next(r for r in server.seen if str(r.url) == ISSUER + "/register").content)
    assert auth.DEVICE_GRANT in registered["grant_types"] and registered["redirect_uris"] == [PASTE]
    asked = parse_qs(next(r for r in server.seen if str(r.url) == ISSUER + "/device").content.decode())
    assert asked["client_id"] == ["registered-client"] and asked["resource"] == [MCP]
    polls = [parse_qs(r.content.decode()) for r in tokens_asked(server)]
    assert len(polls) == 4 and all(p["device_code"] == ["synthetic-device-code"] for p in polls)
    assert {str(r.url).split("?")[0] for r in server.seen} <= {  # Only the service's own published addresses.
        MCP, "https://mcp.fake.example/.well-known/oauth-protected-resource/mcp",
        ISSUER + "/.well-known/oauth-authorization-server", ISSUER + "/register", ISSUER + "/device", ISSUER + "/token"}


@pytest.mark.parametrize(("answer", "code"), [("access_denied", "mcp_auth_denied"), ("expired_token", "mcp_auth_expired"),
                                              ("invalid_grant", "mcp_auth_not_completed")])
def test_a_denied_or_expired_code_ends_the_sign_in_and_saves_nothing(answer, code):
    storage = device_storage()
    with pytest.raises(auth.McpAuthError, match=code):
        run_device(DeviceServer(dcr=True, answers=["authorization_pending", answer]), storage)
    assert "tokens" not in storage.data


def test_a_code_past_its_own_time_is_never_asked_about():
    server, storage = DeviceServer(dcr=True), device_storage()
    with pytest.raises(auth.McpAuthError, match="mcp_auth_expired"):
        run_device(server, storage, deadline=time.monotonic() - 1)
    assert not tokens_asked(server) and "tokens" not in storage.data


@pytest.mark.parametrize("why", ["no grant", "refused", "no registration", "page not https", "page on loopback"])
def test_no_code_when_the_service_offers_none_or_refuses_this_client(why, monkeypatch):
    server = DeviceServer(dcr=why != "no registration", refuse_codes=why == "refused",
                          page={"page not https": "http://auth.fake.example/device",
                                "page on loopback": "https://127.0.0.1/device"}.get(why, "https://auth.fake.example/device"))
    if why == "no grant":
        monkeypatch.setattr(auth, "device_endpoint", lambda document: "")
    with pytest.raises(device_code.DeviceUnavailable):
        run_device(server, device_storage())
    assert not tokens_asked(server)


def test_a_phone_signs_in_with_a_code_where_the_service_offers_one(hosted, monkeypatch):
    server = DeviceServer(dcr=True, answers=["authorization_pending", "ok"])
    hosted_server = hosted(dcr=True)  # The fixture's connection; the service offering codes answers every request.
    monkeypatch.setattr(auth, "PublicTransport", lambda: httpx.MockTransport(server.handle))
    ctx = phone()
    plan_id = start(ctx)
    try:
        plan = waiting(plan_id, ctx, field="device")
        step = sign_in_step(plan)["sign_in"]
        assert (step["delivery"], step["authorization_url"]) == ("device", None)
        assert step["device"] == {"user_code": "WDJB-MJHT", "verification_uri": "https://auth.fake.example/device",
                                  "verification_uri_complete": ""}
        assert 290 <= step["expires_in"] <= 300  # The sign-in's own five minutes, though the code lasts longer.
        flow_of().event.set()  # Skip the service's interval: the person has allowed access meanwhile.
        assert settled(plan_id, ctx)["pause"] == "resume"
        saved = config.read_saved_configuration().document["servers"]["Notes"]["auth"]
        stored = auth.read_credentials(saved["credential_ref"])
        assert stored["tokens"]["access_token"] == "synthetic-access-token" and stored["client"]["client_id"] == "registered-client"
        assert stored["issuer"].rstrip("/") == ISSUER and not hosted_server.seen
    finally:
        plans.cancel(ctx, plan_id)


def test_without_a_code_from_the_service_the_phone_pastes_the_address_instead(hosted, monkeypatch):
    server = DeviceServer(dcr=True, refuse_codes=True)
    hosted(dcr=True)
    monkeypatch.setattr(auth, "PublicTransport", lambda: httpx.MockTransport(server.handle))
    ctx = phone()
    plan_id = start(ctx)
    try:
        plan = waiting(plan_id, ctx)
        step = sign_in_step(plan)["sign_in"]
        assert (step["delivery"], step["device"]) == ("paste", None)
        _, address = landed(plan)
        plans.finish_sign_in(ctx, plan_id, address)
        assert settled(plan_id, ctx)["pause"] == "resume"
    finally:
        plans.cancel(ctx, plan_id)


@pytest.mark.parametrize("ending", ["denied", "five minutes"])
def test_a_code_sign_in_that_is_denied_or_runs_out_ends_and_the_setup_can_start_again(hosted, monkeypatch, ending):
    server = DeviceServer(dcr=True, answers=["access_denied"] if ending == "denied" else [])
    hosted(dcr=True)
    monkeypatch.setattr(auth, "PublicTransport", lambda: httpx.MockTransport(server.handle))
    ctx = phone()
    plan_id = start(ctx)
    waiting(plan_id, ctx, field="device")
    flow = flow_of()
    if ending == "five minutes":
        flow.created -= client_mcp_auth._TTL + 1
    flow.event.set()
    stopped = settled(plan_id, ctx)
    assert stopped["state"] == ("failed" if ending == "denied" else "expired")
    for _ in range(250):
        if flow.finished:
            break
        time.sleep(0.02)
    assert flow.device is None and not config.read_saved_configuration().document["servers"]["Notes"].get("auth")
    found, fresh = api.read_item(owner_id="owner", item_id=item_id())
    assert found["entry"]["next_action"]["kind"] == "continue_setup" and fresh is not None
    assert not config.configuration_recovery_required()
