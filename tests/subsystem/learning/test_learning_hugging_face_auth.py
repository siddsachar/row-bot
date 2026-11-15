"""Sign in with Hugging Face (C1, O134): the device-code OAuth flow against a fake Hugging Face, the tokens kept
in the keychain only (here an in-memory one), refreshed before they expire, and removed by Sign out."""

from __future__ import annotations

import json
import logging

import httpx
import pytest

from row_bot import secret_store
from row_bot.learning.remote import auth

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

ACCESS = "hf_oauth_ACCESS_marker_1234"
FRESH = "hf_oauth_FRESH_marker_5678"
REFRESH = "hf_oauth_REFRESH_marker_9012"


class FakeHuggingFace:
    """Hugging Face's OAuth endpoints: registration, device codes, tokens, whoami."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.pending = 2  # polls answered "authorization_pending" before the person authorizes
        self.decision = "authorize"
        self.refresh_ok = True
        self.device_page = "https://hf.co/oauth/device"

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.url.host == "huggingface.co"
        path = request.url.path
        if path == "/oauth/register":
            body = json.loads(request.content)
            assert body["token_endpoint_auth_method"] == "none" and "jobs" in body["scope"]
            return httpx.Response(201, json={"client_id": "client-123", "scope": body["scope"]})
        form = dict(httpx.QueryParams(request.content.decode()))
        if path == "/oauth/device":
            assert form["client_id"] == "client-123"
            assert set(form["scope"].split()) == set(auth.SCOPES)
            # Hugging Face's real answer (28 Oct): its page on hf.co, no complete address, no interval.
            return httpx.Response(200, json={"device_code": "device-xyz", "user_code": "ABCD-EFGH",
                                             "verification_uri": self.device_page, "expires_in": 300})
        if path == "/oauth/token" and form.get("grant_type", "").endswith("device_code"):
            if self.decision == "deny":
                return httpx.Response(400, json={"error": "access_denied"})
            if self.pending:
                self.pending -= 1
                return httpx.Response(400, json={"error": "authorization_pending"})
            return httpx.Response(200, json={"access_token": ACCESS, "refresh_token": REFRESH, "expires_in": 3600,
                                             "scope": " ".join(auth.SCOPES)})
        if path == "/oauth/token" and form.get("grant_type") == "refresh_token":
            assert form["refresh_token"] == REFRESH
            if not self.refresh_ok:
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"access_token": FRESH, "expires_in": 3600})
        if path == "/api/whoami-v2":
            return httpx.Response(200, json={"name": "ada", "isPro": True})
        return httpx.Response(404)


@pytest.fixture
def keychain():
    backend = secret_store.use_memory_backend()
    yield backend
    secret_store._set_backend_for_tests(None)  # noqa: SLF001


@pytest.fixture
def hf(data_dir, keychain):
    fake = FakeHuggingFace()
    now = [1_000_000.0]
    sign_in = auth.HuggingFaceSignIn(transport=httpx.MockTransport(fake), clock=lambda: now[0], start_threads=False)
    return fake, sign_in, now


def test_sign_in_shows_a_code_and_finishes_when_the_person_authorizes(hf, data_dir, keychain, caplog):
    fake, sign_in, _now = hf
    caplog.set_level(logging.DEBUG)

    started = sign_in.start()

    assert started.state == "signing_in" and started.code.user_code == "ABCD-EFGH"
    assert started.code.verification_uri_complete == "https://hf.co/oauth/device?user_code=ABCD-EFGH"
    assert started.code.interval == 5.0  # none given: the standard five seconds
    assert sign_in.poll() == "waiting" and sign_in.poll() == "waiting"
    assert sign_in.poll() == "signed_in"
    assert sign_in.status().state == "signed_in" and sign_in.status().user == "ada"
    assert sign_in.token() == ACCESS
    # The tokens live in the keychain only: not in any file in the data folder, not in a log line.
    stored = "".join(keychain._values.values())  # noqa: SLF001
    assert ACCESS in stored and REFRESH in stored
    for path in data_dir.rglob("*"):
        if path.is_file():
            assert ACCESS.encode() not in path.read_bytes() and REFRESH.encode() not in path.read_bytes()
    assert ACCESS not in caplog.text and REFRESH not in caplog.text


def test_a_declined_sign_in_says_so_and_keeps_nothing(hf, keychain):
    fake, sign_in, _now = hf
    fake.decision = "deny"
    sign_in.start()

    assert sign_in.poll() == "denied"
    state = sign_in.status()
    assert (state.state, state.failure) == ("signed_out", "denied")
    with pytest.raises(auth.SignInError):
        sign_in.token()


def test_a_code_that_runs_out_ends_the_sign_in(hf):
    _fake, sign_in, now = hf
    sign_in.start()
    now[0] += 901

    assert sign_in.poll() == "expired" and sign_in.status().state == "signed_out"


def test_the_token_is_refreshed_before_it_expires_and_an_unrefreshable_one_asks_to_sign_in_again(hf):
    fake, sign_in, now = hf
    fake.pending = 0
    sign_in.start()
    assert sign_in.poll() == "signed_in"

    now[0] += 3600 - auth.REFRESH_MARGIN_SECONDS + 1
    assert sign_in.token() == FRESH
    now[0] += 3600
    fake.refresh_ok = False
    with pytest.raises(auth.SignInError) as error:
        sign_in.token()
    assert error.value.code == "expired" and sign_in.status().state == "expired"


def test_sign_out_removes_the_tokens_from_the_keychain(hf, keychain):
    fake, sign_in, _now = hf
    fake.pending = 0
    sign_in.start()
    sign_in.poll()

    sign_in.sign_out()

    assert sign_in.status().state == "signed_out"
    stored = "".join(keychain._values.values())  # noqa: SLF001
    assert ACCESS not in stored and REFRESH not in stored
    with pytest.raises(auth.SignInError):
        sign_in.token()
    # Signing in again reuses the registration (no second client made).
    sign_in.start()
    assert [request.url.path for request in fake.requests].count("/oauth/register") == 1


def test_every_request_goes_to_hugging_face_only(hf):
    fake, sign_in, _now = hf
    fake.pending = 0
    sign_in.start()
    sign_in.poll()

    assert {request.url.host for request in fake.requests} == {"huggingface.co"}
    assert all(request.url.scheme == "https" for request in fake.requests)


def test_a_jobs_token_is_refreshed_to_last_the_job(hf):
    fake, sign_in, _now = hf
    fake.pending = 0
    sign_in.start()
    sign_in.poll()

    assert sign_in.token() == ACCESS  # an hour left: fine for a quick call
    assert sign_in.token(valid_for=2 * 3600) == FRESH  # a two-hour job needs a fresh one


@pytest.mark.parametrize("page", ["https://huggingface.co/oauth/device", "https://hf.co/oauth/device"])
def test_the_device_page_may_be_on_either_of_hugging_face_s_own_hosts(hf, page):
    fake, sign_in, _now = hf
    fake.device_page = page

    started = sign_in.start()

    assert started.state == "signing_in" and started.code.verification_uri == page


@pytest.mark.parametrize("page", ["https://evil.example/oauth/device", "http://hf.co/oauth/device",
                                  "https://hf.co.evil.example/oauth/device"])
def test_a_device_page_anywhere_else_is_refused_and_logged_without_the_code(hf, page, caplog):
    fake, sign_in, _now = hf
    fake.device_page = page

    with pytest.raises(auth.SignInError) as raised:
        sign_in.start()

    assert raised.value.code == "refused"
    assert "ABCD-EFGH" not in caplog.text and "device-xyz" not in caplog.text
