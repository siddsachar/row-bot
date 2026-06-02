from __future__ import annotations

import asyncio

from voice.actions import ActiveVoiceSurfaceBinding, append_dictation_text, route_voice_transcript


def test_dictation_appends_to_existing_composer_text():
    assert append_dictation_text("", "first thought") == "first thought"
    assert append_dictation_text("first thought", "second thought") == "first thought second thought"
    assert append_dictation_text("heading\n", "detail") == "heading\ndetail"


def test_dictate_routes_to_composer_without_sending():
    sent: list[str] = []
    composer = {"text": "draft"}

    routed = route_voice_transcript(
        "dictate",
        "spoken note",
        get_composer_text=lambda: composer["text"],
        set_composer_text=lambda value: composer.__setitem__("text", value),
        send_talk_text=sent.append,
    )

    assert routed == "dictated"
    assert composer["text"] == "draft spoken note"
    assert sent == []


def test_talk_routes_to_send_path():
    sent: list[str] = []
    composer = {"text": "draft"}

    routed = route_voice_transcript(
        "talk",
        "send this",
        get_composer_text=lambda: composer["text"],
        set_composer_text=lambda value: composer.__setitem__("text", value),
        send_talk_text=sent.append,
    )

    assert routed == "sent"
    assert composer["text"] == "draft"
    assert sent == ["send this"]


def test_active_voice_binding_appends_dictation_without_sending():
    sent: list[tuple[str, bool]] = []
    composer = {"text": "designer draft"}

    async def send_fn(text: str, *, voice_mode: bool = False) -> None:
        sent.append((text, voice_mode))

    binding = ActiveVoiceSurfaceBinding(
        surface="designer",
        thread_id="thread-1",
        get_composer_text=lambda: composer["text"],
        set_composer_text=lambda value: composer.__setitem__("text", value),
        send_talk_text=send_fn,
    )

    binding.append_dictation("new instruction")

    assert binding.is_current("thread-1") is True
    assert composer["text"] == "designer draft new instruction"
    assert sent == []


def test_active_voice_binding_sends_talk_with_voice_mode():
    sent: list[tuple[str, bool]] = []

    async def send_fn(text: str, *, voice_mode: bool = False) -> None:
        sent.append((text, voice_mode))

    binding = ActiveVoiceSurfaceBinding(
        surface="developer",
        thread_id="thread-1",
        get_composer_text=lambda: "",
        set_composer_text=lambda _value: None,
        send_talk_text=send_fn,
    )

    asyncio.run(binding.send_talk("inspect the repo"))

    assert sent == [("inspect the repo", True)]


def test_active_voice_binding_stale_after_clear_or_thread_change():
    binding = ActiveVoiceSurfaceBinding(
        surface="normal_chat",
        thread_id="thread-1",
        get_composer_text=lambda: "",
        set_composer_text=lambda _value: None,
        send_talk_text=lambda _text: None,
    )

    assert binding.is_current("thread-2") is False
    binding.clear()
    assert binding.is_current("thread-1") is False
