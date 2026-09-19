"""Same-origin SDP proxy uses fake HTTPS and exact disposable voice leases."""

# ruff: noqa: F811 -- shared isolated voice fixture.
from dataclasses import replace
import threading
from uuid import uuid4

import pytest

from row_bot.voice.client_transport import DictationError
from row_bot.voice.openai_realtime import OpenAIRealtimeProvider, CALLS_URL
from tests.subsystem.voice.test_client_realtime import runtime, start  # noqa: F401


def test_exchange_uses_exact_ephemeral_key_once_without_rereading_provider_config(
    runtime,
):
    r = runtime
    capture = start(r).snapshot
    calls = []

    def exchange(offer, *, client_secret, validate):
        validate()
        calls.append((offer, client_secret))
        return b"v=0\r\nsynthetic-answer"

    r.provider.exchange_sdp = exchange
    r.adapter._provider = lambda: pytest.fail("second provider selection")
    answer = r.adapter.exchange(
        r.owner, capture.handle, offer=b"v=0\r\nsynthetic-offer", validate=lambda: None
    )
    assert answer == b"v=0\r\nsynthetic-answer" and calls == [
        (b"v=0\r\nsynthetic-offer", "synthetic-ephemeral")
    ]
    assert r.coordinator._dictation_lease.realtime.secret == ""
    with pytest.raises(DictationError, match="voice_exchange_consumed"):
        r.adapter.exchange(
            r.owner,
            capture.handle,
            offer=b"v=0\r\nsynthetic-offer",
            validate=lambda: None,
        )
    assert len(calls) == 1


@pytest.mark.parametrize("cause", ["owner", "epoch", "expired", "oversize"])
def test_exchange_invalid_admission_has_no_https_effect(runtime, cause):
    r = runtime
    capture = start(r).snapshot
    r.provider.exchange_sdp = lambda *args, **kwargs: pytest.fail("unadmitted network")
    owner, handle, data = r.owner, capture.handle, b"v=0\r\n"
    if cause == "owner":
        owner = replace(owner, client_session_id=str(uuid4()))
    if cause == "epoch":
        handle = replace(handle, server_epoch="different")
    if cause == "expired":
        r.clock[0] = 61
    if cause == "oversize":
        data = b"v=0" + b"x" * (1024 * 1024)
    with pytest.raises(DictationError):
        r.adapter.exchange(owner, handle, offer=data, validate=lambda: None)


def test_stop_during_exchange_holds_slot_until_https_drains_and_discards_answer(
    runtime,
):
    r = runtime
    handle = start(r).snapshot.handle
    entered, release, failures = threading.Event(), threading.Event(), []

    def exchange(*args, validate, **kwargs):
        validate()
        entered.set()
        assert release.wait(5)
        validate()
        return b"v=0\r\nlate answer"

    r.provider.exchange_sdp = exchange

    def work():
        try:
            r.adapter.exchange(r.owner, handle, offer=b"v=0\r\n", validate=lambda: None)
        except DictationError as exc:
            failures.append(exc.code)

    worker = threading.Thread(target=work)
    worker.start()
    try:
        assert entered.wait(5)
        assert not r.adapter.stop(r.owner, handle, validate=lambda: None).quiesced
        with pytest.raises(DictationError, match="voice_session_busy"):
            start(r)
    finally:
        release.set()
        worker.join(5)
    assert failures == ["voice_session_expired"] and not worker.is_alive()
    assert r.adapter.snapshot(r.owner, handle, validate=lambda: None).quiesced


def test_uncertain_exchange_is_redacted_and_never_retried(runtime):
    r = runtime
    handle = start(r).snapshot.handle
    calls = []

    def exchange(*args, **kwargs):
        calls.append(1)
        raise TimeoutError("private SDP and credential")

    r.provider.exchange_sdp = exchange
    with pytest.raises(DictationError, match="realtime_provider_unavailable"):
        r.adapter.exchange(r.owner, handle, offer=b"v=0\r\n", validate=lambda: None)
    with pytest.raises(DictationError, match="voice_session_expired"):
        r.adapter.exchange(r.owner, handle, offer=b"v=0\r\n", validate=lambda: None)
    assert calls == [1]


def test_exchange_preserves_validator_exception_even_if_validation_recovers(runtime):
    r = runtime
    handle = start(r).snapshot.handle
    reject = [False]
    sentinel = PermissionError("synthetic revoked")

    def validate():
        if reject[0]:
            reject[0] = False
            raise sentinel

    def exchange(*args, validate, **kwargs):
        reject[0] = True
        validate()

    r.provider.exchange_sdp = exchange
    with pytest.raises(PermissionError) as caught:
        r.adapter.exchange(r.owner, handle, offer=b"v=0\r\n", validate=validate)
    assert caught.value is sentinel and r.coordinator._dictation_lease.revoked


class Response:
    status_code = 201
    headers = {"Content-Type": "application/sdp"}
    closed = False

    def __init__(self, chunks=(b"v=0\r\n", b"synthetic answer")):
        self.chunks = chunks

    def iter_content(self, *, chunk_size):
        assert chunk_size == 16384
        yield from self.chunks

    def close(self):
        self.closed = True


def test_provider_exact_https_no_redirect_api_key_lookup_or_unbounded_response(
    monkeypatch,
):
    from row_bot.voice import openai_realtime as module

    response, calls = Response(), []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return response

    monkeypatch.setattr(module.requests, "post", post)
    monkeypatch.setattr(
        module, "get_key", lambda *args: pytest.fail("durable key lookup")
    )
    result = OpenAIRealtimeProvider().exchange_sdp(
        b"v=0\r\nsynthetic offer",
        client_secret="synthetic-ephemeral",
        validate=lambda: None,
    )
    assert result == b"v=0\r\nsynthetic answer" and response.closed
    url, kwargs = calls[0]
    assert (
        url == CALLS_URL
        and kwargs["headers"]["Authorization"] == "Bearer synthetic-ephemeral"
    )
    assert (
        kwargs["stream"] is True
        and kwargs["allow_redirects"] is False
        and kwargs["timeout"] == (5, 5)
    )


@pytest.mark.parametrize(
    "failure", ["redirect", "type", "oversize", "deadline", "revoked"]
)
def test_provider_bounds_and_closes_unusable_response(monkeypatch, failure):
    from row_bot.voice import openai_realtime as module

    response = Response()
    if failure == "redirect":
        response.status_code = 302
    if failure == "type":
        response.headers = {"Content-Type": "text/html"}
    if failure == "oversize":
        response.chunks = [b"v=0", b"x" * (1024 * 1024)]
    clock, posted = [0], [False]

    def post(*args, **kwargs):
        posted[0] = True
        if failure == "deadline":
            clock[0] = 26
        return response

    def validate():
        if failure == "revoked" and posted[0]:
            raise PermissionError("synthetic revoked")

    monkeypatch.setattr(module.requests, "post", post)
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    with pytest.raises((DictationError, PermissionError)):
        OpenAIRealtimeProvider().exchange_sdp(
            b"v=0\r\n", client_secret="synthetic-ephemeral", validate=validate
        )
    assert response.closed
