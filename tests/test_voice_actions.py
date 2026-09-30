from __future__ import annotations

from row_bot.voice.actions import append_dictation_text, route_voice_transcript


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
