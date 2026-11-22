"""Read aloud on a saved reply: the server reads the reply's words and speaks them part by part with Kokoro."""
# ruff: noqa: F811 -- reused pytest fixture.
from __future__ import annotations

import io
from types import SimpleNamespace
import wave

import pytest

from row_bot.voice.coordinator import VoiceSessionCoordinator
from tests.subsystem.client_protocol.test_protocol_application import _client, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = pytest.mark.subsystem

REPLY = (
    "## Plan\n\nQwen runs **locally**. It reads your notes first.\n\n"
    "```python\nprint('never read aloud')\n```\n\n"
    + " ".join(f"Step {n} keeps the work moving and checks what changed before going on." for n in range(1, 13))
)


def _wav() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24_000)
        audio.writeframes(b"\0\0" * 240)
    return output.getvalue()


class _Kokoro:
    """Stands in for Kokoro: records exactly what it was asked to say."""

    def __init__(self) -> None:
        self.ready = True
        self.said: list[str] = []

    def is_installed(self) -> bool:
        return self.ready

    def reload_settings(self) -> None:
        pass

    def synthesize_wav_bytes(self, text: str, **_options) -> bytes:
        self.said.append(text)
        return _wav()


@pytest.fixture
def api(service, monkeypatch):
    from langchain_core.messages import AIMessage, HumanMessage
    from row_bot import sound, threads
    from row_bot.tts import TTSService
    from row_bot.voice.browser_local import BrowserLocalVoiceService

    sound.remember_pronunciations({"Qwen": "Kwen"})
    kokoro = _Kokoro()
    # The capability's readiness check reads the same stand-in.
    monkeypatch.setattr(TTSService, "is_installed", lambda self: kokoro.ready)
    browser = BrowserLocalVoiceService(voice_service=SimpleNamespace(), tts_service=kokoro, ffmpeg_path="unused")
    service.bind_voice(VoiceSessionCoordinator(), browser_service=lambda: browser)
    conversation = threads.create_thread("Read aloud", seed_default_skills=False)
    assert threads.append_checkpoint_messages(conversation, [
        HumanMessage(id="ask", content="What is the plan?"), AIMessage(id="reply", content=REPLY)])
    with _client(service) as client:
        _, headers = bootstrap(client)
        yield SimpleNamespace(client=client, headers=headers, conversation=conversation, kokoro=kokoro)
    service.close_voice()


def _read(api, message_id: str, part: int, **extra):
    return api.client.post(f"/api/v1/conversations/{api.conversation}/read-aloud/{message_id}",
                           headers=api.headers, json={"part": part, **extra})


def test_a_reply_is_spoken_in_order_from_the_saved_words(api):
    from row_bot.application.read_aloud import speech_parts

    first = _read(api, "reply", 0)
    assert first.status_code == 200, first.text
    assert first.headers["content-type"] == "audio/wav" and first.content.startswith(b"RIFF")
    parts = int(first.headers["X-Read-Aloud-Parts"])
    assert parts > 2 and first.headers["X-Read-Aloud-Part"] == "0"
    for part in range(1, parts):
        response = _read(api, "reply", part)
        assert response.status_code == 200 and response.headers["X-Read-Aloud-Part"] == str(part)

    said = api.kokoro.said
    # Every part in order, with the code left out.
    assert said[0].startswith("Plan. Kwen runs locally. It reads your notes first.") and len(said[0]) <= 160
    assert " ".join(said).endswith("Step 12 keeps the work moving and checks what changed before going on.")
    assert "never read aloud" not in " ".join(said) and "**" not in " ".join(said)
    assert len(said[0]) < len(said[1])
    assert len(said) == parts == len(speech_parts(REPLY))
    assert _read(api, "reply", parts).status_code == 404


def test_the_voice_says_saved_pronunciations_while_the_reply_keeps_its_words(api):
    assert _read(api, "reply", 0).status_code == 200
    assert api.kokoro.said[0].startswith("Plan. Kwen runs")
    page = api.client.get(f"/api/v1/conversations/{api.conversation}/transcript", headers=api.headers)
    [reply] = [row for row in page.json()["rows"] if row["message_id"] == "reply"]
    assert "Qwen runs" in str(reply["blocks"]) and "Kwen" not in str(reply["blocks"])


def test_the_browser_cannot_supply_the_words(api):
    response = _read(api, "reply", 0, text="Say this instead")
    assert response.status_code == 422
    assert not api.kokoro.said


@pytest.mark.parametrize("message_id", ["missing", "ask"])
def test_only_a_saved_reply_is_read(api, message_id):
    response = _read(api, message_id, 0)
    assert response.status_code == 404 and response.json()["code"] == "not_found"
    assert not api.kokoro.said


def test_without_kokoro_read_aloud_says_what_it_needs(api):
    capability = api.client.get("/api/v1/voice/dictation/capability", headers=api.headers)
    assert capability.json()["read_aloud_reason"] == "available"

    api.kokoro.ready = False
    capability = api.client.get("/api/v1/voice/dictation/capability", headers=api.headers)
    assert capability.json()["read_aloud_reason"] == "kokoro_model_missing"
    response = _read(api, "reply", 0)
    assert response.status_code == 409 and response.json()["code"] == "kokoro_model_missing"
    assert not api.kokoro.said


def test_a_long_sentence_is_cut_between_words_and_no_part_runs_long():
    from row_bot.application.read_aloud import FIRST_PART_CHARS, PART_CHARS, speech_parts

    words = " ".join(f"word{n}" for n in range(400)) + "."
    parts = speech_parts(f"Short start. {words}")
    assert parts[0] == "Short start." and len(parts[0]) <= FIRST_PART_CHARS
    assert all(len(part) <= PART_CHARS for part in parts)
    assert " ".join(parts[1:]) == words
    assert speech_parts("```\ncode only\n```") == []
