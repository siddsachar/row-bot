"""Authenticated Dictate over the real lease owner with fake speech only."""
from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tests.subsystem.voice.test_dictation_lifecycle import runtime  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap, client_app

pytestmark = pytest.mark.subsystem
ROOT = "/api/v1/conversations/conversation-A/voice/dictation"


@pytest.fixture
def api(runtime, monkeypatch):  # noqa: F811
    from row_bot.runtime import admissions
    monkeypatch.setattr(admissions, "deletion_state", lambda _: "active")
    client, service, active = client_app(remote=True)
    service.dictation = runtime.adapter
    service._metadata = lambda _: {"thread_id": "conversation-A"}
    with client:
        handshake, headers = bootstrap(client)
        yield SimpleNamespace(client=client, service=service, active=active,
                              handshake=handshake, headers=headers, runtime=runtime)


def start(api):
    result = api.client.post(ROOT, headers=api.headers, json={"request_id": str(uuid4())})
    assert result.status_code == 200, result.text
    return result.json()


def audio_headers(api, snapshot, *, utterance=None, **extra):
    return {**api.headers, "Content-Type": "audio/webm",
            "X-Voice-Session-Id": str(snapshot["handle"]["voice_session_id"]),
            "X-Server-Epoch": snapshot["handle"]["server_epoch"],
            "X-Dictation-Utterance": utterance or str(uuid4()), **extra}


def url(snapshot, action="transcribe"):
    return ROOT + "/" + snapshot["handle"]["lease_id"] + ("/" + action if action else "")


def test_passive_capability_then_one_authenticated_utterance_and_replay(api):
    response = api.client.get("/api/v1/voice/dictation/capability", headers=api.headers)
    assert response.status_code == 200
    assert response.json()["browser_dictation_available"] is True
    assert not api.runtime.factories
    capture = start(api)
    headers = audio_headers(api, capture)
    first = api.client.post(url(capture), headers=headers, content=b"synthetic audio")
    assert first.status_code == 200, first.text
    assert first.headers["cache-control"] == "no-store"
    assert first.json()["text"] == "dictation result"
    assert first.json()["snapshot"]["state"] == "completed"
    assert first.json()["snapshot"]["quiesced"] is True
    assert len(api.runtime.calls) == 1
    assert not api.runtime.voice.effects and not api.service.commands
    assert "access_binding" not in first.text and "csrf" not in first.text
    repeated = api.client.post(url(capture), headers=headers, content=b"synthetic audio")
    assert repeated.status_code == 200 and repeated.json()["text"] == "dictation result"
    assert len(api.runtime.calls) == 1
    mismatch = api.client.post(url(capture), headers=headers, content=b"different bytes")
    assert mismatch.status_code == 409 and mismatch.json()["code"] == "idempotency_mismatch"
    assert len(api.runtime.calls) == 1


@pytest.mark.parametrize("fault", ["csrf", "origin", "revoked"])
def test_authentication_rejects_before_service_or_body_work(api, fault):
    capture = start(api)
    headers = audio_headers(api, capture)
    if fault == "csrf":
        headers.pop("X-CSRF-Token")
    elif fault == "origin":
        headers["Origin"] = "http://foreign.invalid"
    else:
        api.active["value"] = False
    consumed = []
    def body():
        consumed.append(True)
        yield b"private synthetic audio"
    response = api.client.post(url(capture), headers=headers, content=body())
    assert response.status_code == (401 if fault == "revoked" else 403)
    assert not consumed and not api.runtime.calls


@pytest.mark.parametrize("extra,status,code", [
    ({"Content-Length": "8388609"}, 413, "payload_too_large"),
    ({"Content-Length": "-1"}, 422, "invalid_command"),
    ({"Content-Length": "invalid"}, 422, "invalid_command"),
    ({"Content-Type": "text/plain"}, 415, "unsupported_audio_type"),
    ({"X-Voice-Session-Id": "0"}, 422, "invalid_command"),
    ({"X-Dictation-Utterance": "invalid"}, 422, "invalid_command"),
    ({"X-Server-Epoch": "other"}, 410, "voice_session_expired"),
])
def test_header_bounds_fail_before_audio_read(api, extra, status, code):
    capture = start(api)
    consumed = []
    def body():
        consumed.append(True)
        yield b"synthetic audio"
    response = api.client.post(url(capture), headers=audio_headers(api, capture, **extra), content=body())
    assert response.status_code == status, response.text
    assert response.json()["code"] == code
    assert not consumed and not api.runtime.calls


def test_actual_bytes_are_bounded_independently_of_declared_length(api):
    capture = start(api)
    response = api.client.post(url(capture), headers=audio_headers(api, capture, **{"Content-Length": "1"}),
                               content=b"x" * 8388609)
    assert response.status_code == 413 and not api.runtime.calls
    fresh = start(api)
    assert fresh["handle"]["lease_id"] != capture["handle"]["lease_id"]


def test_late_revocation_discards_completed_transcript_and_cached_result(api):
    def transcribe(key, audio, mime, *, validate):
        validate()
        api.active["value"] = False
        return "PRIVATE_TRANSCRIPT"
    api.runtime.service.transcribe = transcribe
    capture = start(api)
    response = api.client.post(url(capture), headers=audio_headers(api, capture), content=b"synthetic")
    assert response.status_code == 401 and "PRIVATE_TRANSCRIPT" not in response.text
    api.active["value"] = True
    inspected = api.client.get(url(capture, ""), headers=audio_headers(api, capture))
    assert inspected.status_code == 200, inspected.text
    assert inspected.json()["state"] == "stopped" and inspected.json()["quiesced"]


def test_another_presentation_cannot_upload_or_stop_a_lease(api):
    capture = start(api)
    _, other = bootstrap(api.client)
    headers = {**audio_headers(api, capture), **other}
    response = api.client.post(url(capture), headers=headers, content=b"synthetic")
    assert response.status_code == 403, response.text
    identity = {key: capture["handle"][key] for key in ("voice_session_id", "server_epoch")}
    stopped = api.client.post(url(capture, "stop"), headers=other, json=identity)
    assert stopped.status_code == 403
    own_stop = api.client.post(url(capture, "stop"), headers=api.headers, json=identity)
    assert own_stop.status_code == 200 and own_stop.json()["quiesced"]
    assert not api.runtime.calls


def test_same_host_binding_is_idempotent_and_passive(runtime):  # noqa: F811
    from row_bot.application.client_platform import ClientPlatformService
    from row_bot.voice.coordinator import VoiceSessionCoordinator
    from tests.subsystem.voice.test_dictation_lifecycle import Voice
    service = ClientPlatformService()
    coordinator = VoiceSessionCoordinator(Voice())
    factory = lambda: pytest.fail("Passive binding constructed speech service")
    service.bind_voice(coordinator, browser_service=factory)
    owner = service.dictation
    service.bind_voice(coordinator, browser_service=factory)
    assert service.dictation is owner and owner.coordinator is coordinator
    with pytest.raises(ValueError, match="voice_owner_already_bound"):
        service.bind_voice(VoiceSessionCoordinator(Voice()), browser_service=factory)
    assert service.close_voice()


def test_stalled_receive_holds_one_slot_through_stop_and_disconnect(runtime, monkeypatch):  # noqa: F811
    import httpx
    from row_bot.runtime import admissions
    monkeypatch.setattr(admissions, "deletion_state", lambda _: "active")
    fixture, service, active = client_app(remote=True)
    service.dictation = runtime.adapter
    service._metadata = lambda _: {}

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=fixture.app,
                client=("127.0.0.1", 12345)), base_url="http://localhost") as client:
            connected = (await client.post("/api/v1/handshake", json={}, headers={"Origin": "http://localhost"})).json()
            headers = {"Origin": "http://localhost", "X-Client-Session": connected["client_session_id"], "X-CSRF-Token": connected["csrf_token"]}
            capture = (await client.post(ROOT, json={"request_id": str(uuid4())}, headers=headers)).json()
            context = SimpleNamespace(headers=headers)
            entered, release = asyncio.Event(), asyncio.Event()
            advanced = []
            async def body():
                entered.set()
                await release.wait()
                yield b"late bytes"
            async def second_body():
                advanced.append(True)
                yield b"second bytes"
            pending = asyncio.create_task(client.post(url(capture), headers=audio_headers(context, capture), content=body()))
            await entered.wait()
            try:
                second = await client.post(url(capture), headers=audio_headers(context, capture), content=second_body())
                assert second.status_code == 429 and not advanced
                identity = {key: capture["handle"][key] for key in ("voice_session_id", "server_epoch")}
                stopped = await client.post(url(capture, "stop"), headers=headers, json=identity)
                assert stopped.json()["state"] == "stopping" and not stopped.json()["quiesced"]
                busy = await client.post(ROOT, headers=headers, json={"request_id": str(uuid4())})
                assert busy.status_code == 429
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
                assert not runtime.calls
                resumed = await client.post(ROOT, headers=headers, json={"request_id": str(uuid4())})
                assert resumed.status_code == 200, resumed.text
            finally:
                release.set()
                if not pending.done():
                    pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
                runtime.adapter.close()
    asyncio.run(scenario())


def test_http_cancellation_cannot_release_a_running_transcription(runtime, monkeypatch):  # noqa: F811
    import httpx
    from row_bot.runtime import admissions
    monkeypatch.setattr(admissions, "deletion_state", lambda _: "active")
    fixture, service, _ = client_app(remote=True)
    service.dictation = runtime.adapter
    service._metadata = lambda _: {}
    entered, release = threading.Event(), threading.Event()

    def transcribe(key, audio, mime, *, validate):
        validate()
        entered.set()
        assert release.wait(10), "Test did not release synthetic speech worker"
        return "PRIVATE_CANCELLED_TRANSCRIPT"
    runtime.service.transcribe = transcribe

    async def scenario():
        loop = asyncio.get_running_loop()
        finished = asyncio.Event()
        original = runtime.adapter.complete_receive
        def complete(*args, **kwargs):
            try:
                return original(*args, **kwargs)
            finally:
                loop.call_soon_threadsafe(finished.set)
        monkeypatch.setattr(runtime.adapter, "complete_receive", complete)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=fixture.app,
                client=("127.0.0.1", 12345)), base_url="http://localhost") as client:
            connected = (await client.post("/api/v1/handshake", json={}, headers={"Origin": "http://localhost"})).json()
            headers = {"Origin": "http://localhost", "X-Client-Session": connected["client_session_id"], "X-CSRF-Token": connected["csrf_token"]}
            capture = (await client.post(ROOT, json={"request_id": str(uuid4())}, headers=headers)).json()
            context = SimpleNamespace(headers=headers)
            pending = asyncio.create_task(client.post(url(capture), headers=audio_headers(context, capture), content=b"synthetic"))
            try:
                assert await asyncio.to_thread(entered.wait, 5), "Speech worker never started"
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
                inspected = await client.get(url(capture, ""), headers=audio_headers(context, capture))
                assert inspected.json()["state"] == "stopping" and not inspected.json()["quiesced"]
                busy = await client.post(ROOT, headers=headers, json={"request_id": str(uuid4())})
                assert busy.status_code == 429
                release.set()
                await finished.wait()
                inspected = await client.get(url(capture, ""), headers=audio_headers(context, capture))
                assert inspected.json()["state"] == "stopped" and inspected.json()["quiesced"]
                assert "PRIVATE_CANCELLED_TRANSCRIPT" not in inspected.text
                assert (await client.post(ROOT, headers=headers, json={"request_id": str(uuid4())})).status_code == 200
            finally:
                release.set()
                await asyncio.gather(pending, return_exceptions=True)
                if entered.is_set():
                    await finished.wait()
                runtime.adapter.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["start", "transcribe"])
def test_asgi_disconnect_without_handler_cancellation_retires_late_result(runtime, monkeypatch, phase):  # noqa: F811
    import httpx
    from row_bot.runtime import admissions
    monkeypatch.setattr(admissions, "deletion_state", lambda _: "active")
    fixture, service, _ = client_app(remote=True)
    service.dictation = runtime.adapter
    service._metadata = lambda _: {}
    entered, release = threading.Event(), threading.Event()
    def blocked_result(*args, **kwargs):
        if "validate" in kwargs:
            kwargs["validate"]()
        entered.set()
        assert release.wait(10), "Test did not release the synthetic voice owner"
        return True if phase == "start" else "PRIVATE_DISCONNECTED_TRANSCRIPT"

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=fixture.app,
                client=("127.0.0.1", 12345)), base_url="http://localhost") as client:
            connected = (await client.post("/api/v1/handshake", json={}, headers={"Origin": "http://localhost"})).json()
            headers = {"Origin": "http://localhost", "X-Client-Session": connected["client_session_id"], "X-CSRF-Token": connected["csrf_token"]}
            if phase == "transcribe":
                capture = (await client.post(ROOT, json={"request_id": str(uuid4())}, headers=headers)).json()
                route = url(capture)
                headers = audio_headers(SimpleNamespace(headers=headers), capture)
                body = b"synthetic audio"
                runtime.service.transcribe = blocked_result
            else:
                route = ROOT
                headers = {**headers, "Content-Type": "application/json"}
                body = json.dumps({"request_id": str(uuid4())}).encode()
                runtime.voice.whisper_model_available = blocked_result
            headers = {**headers, "Host": "localhost"}
            disconnected, observed = asyncio.Event(), asyncio.Event()
            sent_body, output = [], []
            async def receive():
                if not sent_body:
                    sent_body.append(True)
                    return {"type": "http.request", "body": body, "more_body": False}
                await disconnected.wait()
                observed.set()
                return {"type": "http.disconnect"}
            async def send(message):
                output.append(message)
            scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                "method": "POST", "scheme": "http", "path": route, "raw_path": route.encode(),
                "query_string": b"", "root_path": "", "server": ("localhost", 80),
                "client": ("127.0.0.1", 12345),
                "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()]}
            pending = asyncio.create_task(fixture.app(scope, receive, send))
            try:
                assert await asyncio.to_thread(entered.wait, 5), "Synthetic voice work never started"
                disconnected.set()
                await observed.wait()
                # The handler remains alive; TCP close is not task cancellation.
                assert not pending.done()
                lease = runtime.coordinator._dictation_lease
                assert lease is not None and (lease.starting or lease.operation is not None)
                release.set()
                await pending
                public = b"".join(message.get("body", b"") for message in output)
                assert b"PRIVATE_DISCONNECTED_TRANSCRIPT" not in public
                assert not any(message.get("status") == 200 for message in output)
                assert runtime.adapter.close()
            finally:
                release.set()
                await asyncio.gather(pending, return_exceptions=True)
                runtime.adapter.close()
    asyncio.run(scenario())
