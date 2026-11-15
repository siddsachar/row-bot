"""Library › Media (5.3.1 B3): every chat's pictures, videos and sounds, found by words, kind and tags.

Real attachment storage in an isolated data folder; nothing leaves the machine.
"""
from __future__ import annotations

import pytest

from tests.contracts.client_platform.test_headless_lifecycle import platform  # noqa: F401

pytestmark = pytest.mark.subsystem

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"0" * 64
WAV = b"RIFF\x00\x00\x00\x00WAVE" + b"0" * 64


def _files(register):
    return (register("conversation-a", "launch-card.png", PNG), register("conversation-b", "demo.mp4", MP4),
            register("conversation-b", "line-01.wav", WAV), register("conversation-a", "notes.pdf", b"%PDF-1.4 x"))


def test_media_lists_every_chats_pictures_videos_and_sounds_found_by_words_kind_and_tags(platform):  # noqa: F811
    from row_bot import media_library
    from row_bot.application.attachments import list_conversation_files, register_attachment

    shot, clip, voice, _pdf = _files(register_attachment)

    listed = media_library.items()
    assert sorted((item["name"], item["kind"], item["conversation_title"]) for item in listed) == [
        ("demo.mp4", "video", "conversation-b"), ("launch-card.png", "image", "conversation-a"),
        ("line-01.wav", "audio", "conversation-b")]
    assert media_library.set_tags(clip["attachment_ref"], ["X post", "demo", " x  post "]) == ["x post", "demo"]
    assert [item["name"] for item in media_library.items(query="x post")] == ["demo.mp4"]
    assert [item["name"] for item in media_library.items(kind="audio")] == ["line-01.wav"]
    assert [item["name"] for item in media_library.items(query="conversation-a")] == ["launch-card.png"]
    with pytest.raises(media_library.MediaError, match="up to 12 tags"):
        media_library.set_tags(clip["attachment_ref"], [f"tag {n}" for n in range(13)])

    copied = media_library.bring(shot["attachment_ref"], "conversation-b")
    assert copied["name"] == "launch-card.png" and copied["attachment_ref"].startswith("conversation-b:")
    assert "launch-card.png" in [entry["name"] for entry in list_conversation_files("conversation-b")]
    assert voice["attachment_ref"] in {item["ref"] for item in media_library.items()}


def test_an_incognito_chats_files_never_show_in_media(platform):  # noqa: F811
    from row_bot import media_library, threads
    from row_bot.application.attachments import register_attachment

    _shot, clip, _voice, _pdf = _files(register_attachment)
    threads.set_thread_incognito("conversation-b", True)

    assert [item["name"] for item in media_library.items()] == ["launch-card.png"]
    with pytest.raises(media_library.MediaError):
        media_library.set_tags(clip["attachment_ref"], ["demo"])
    with pytest.raises(media_library.MediaError):
        media_library.bring(clip["attachment_ref"], "conversation-a")


def test_the_agent_finds_tags_and_brings_media_into_its_chat(platform, monkeypatch):  # noqa: F811
    from row_bot import tool_outputs
    from row_bot.application.attachments import list_conversation_files, register_attachment
    from row_bot.tools import media_tool

    shot, _clip, _voice, _pdf = _files(register_attachment)
    monkeypatch.setattr(tool_outputs, "current_thread_id", lambda: "conversation-b")

    tagged = media_tool._media_tag(shot["attachment_ref"], ["launch", "x post"])
    found = media_tool._media_find("launch")
    brought = media_tool._media_bring(shot["attachment_ref"])

    assert tagged == f"Tags for {shot['attachment_ref']}: launch, x post."
    assert found.startswith("1 item(s), newest first:\n- launch-card.png (image, 1 KB) · " + shot["attachment_ref"])
    assert found.endswith("· tags: launch, x post")
    assert brought.startswith("launch-card.png is in this chat's files (conversation-b:")
    assert [entry["name"] for entry in list_conversation_files("conversation-b")].count("launch-card.png") == 1
    assert media_tool._media_find("nothing like this") == "Nothing in the media library matches."
