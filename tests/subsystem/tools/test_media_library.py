"""Library › Media (5.3.1 B3): every chat's pictures, videos and sounds, found by words, kind and tags.

Real attachment storage in an isolated data folder; nothing leaves the machine.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

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


def _settled(folder: Path, at: float) -> None:
    """A chat folder and its files as last changed at ``at``, as a library's older chats are."""
    for path in folder.iterdir():
        os.utime(path, (at - 1, at - 1))
    os.utime(folder, (at, at))


def _counted_listing(monkeypatch) -> list[str]:
    """The chats whose folders are read, in order."""
    from row_bot.application import attachments

    read: list[str] = []
    listing = attachments.list_conversation_files
    monkeypatch.setattr(attachments, "list_conversation_files",
                        lambda chat, **options: read.append(chat) or listing(chat, **options))
    return read


def test_media_reads_a_chat_folder_once_until_it_changes_and_stays_private(platform, monkeypatch):  # noqa: F811
    from row_bot import media_library, threads
    from row_bot.application.attachments import register_attachment

    _files(register_attachment)
    for folder in Path(threads._MEDIA_DIR).iterdir():
        _settled(folder, time.time() - 60)
    read = _counted_listing(monkeypatch)

    first = media_library.items()
    assert sorted(read) == ["conversation-a", "conversation-b"]
    read.clear()
    assert media_library.items() == first and read == []

    # A new file changes its folder: that folder alone is read again, and the file is listed.
    register_attachment("conversation-a", "poster.png", PNG)
    assert [item["name"] for item in media_library.items()][0] == "poster.png"
    assert read == ["conversation-a"]
    # What is private is checked on every listing, never kept.
    threads.set_thread_incognito("conversation-b", True)
    assert {item["conversation_id"] for item in media_library.items()} == {"conversation-a"}


def test_media_reads_only_the_folders_that_can_hold_the_newest_items(platform, monkeypatch):  # noqa: F811
    from row_bot import media_library, threads
    from row_bot.application.attachments import register_attachment

    _files(register_attachment)
    media = Path(threads._MEDIA_DIR)
    _settled(media / "conversation-a", time.time() - 60)
    _settled(media / "conversation-b", time.time() - 600)
    read = _counted_listing(monkeypatch)

    assert [item["name"] for item in media_library.items(limit=1)] == ["launch-card.png"]
    assert read == ["conversation-a"]
    assert {item["name"] for item in media_library.items(limit=3)} == {"launch-card.png", "demo.mp4", "line-01.wav"}


def test_the_outputs_list_shares_the_folder_listing_and_never_reads_a_files_bytes(platform, monkeypatch):  # noqa: F811
    from row_bot import media_library, threads
    from row_bot.application import attachments

    _files(attachments.register_attachment)
    for folder in Path(threads._MEDIA_DIR).iterdir():
        _settled(folder, time.time() - 60)
    monkeypatch.setattr(attachments, "read_attachment", lambda *_: pytest.fail("a file was read"))
    read = _counted_listing(monkeypatch)

    first = media_library.outputs(show="uploads")
    assert sorted(item["file_name"] for item in first["items"]) == ["demo.mp4", "launch-card.png", "notes.pdf"]
    read.clear()
    assert media_library.outputs(show="uploads") == first and media_library.items() and read == []
