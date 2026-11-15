from __future__ import annotations

import io
import json
import os
import zipfile

import pytest

from row_bot.memory_import import scan_files
from tests.fixtures.memory_exports import (
    chatgpt_simple,
    claude_conversation,
    claude_memories_old,
    write_zip,
    zip_bytes,
)


pytestmark = pytest.mark.subsystem


def test_zip_slip_paths_refuse_the_whole_archive(tmp_path) -> None:
    export = tmp_path / "export.zip"
    with zipfile.ZipFile(export, "w") as archive:
        archive.writestr("memories.json", json.dumps(claude_memories_old()))
        archive.writestr("../../outside/conversations.json", json.dumps([claude_conversation()]))

    scan = scan_files([export])

    assert scan.facts == [] and scan.conversation_count == 0
    assert any("unsafe paths" in warning for warning in scan.warnings)
    assert not (tmp_path.parent / "outside").exists()


def test_absolute_member_paths_are_refused(tmp_path) -> None:
    export = tmp_path / "export.zip"
    with zipfile.ZipFile(export, "w") as archive:
        archive.writestr("/etc/memories.json", json.dumps(claude_memories_old()))

    scan = scan_files([export])

    assert scan.facts == []
    assert any("unsafe paths" in warning for warning in scan.warnings)


def test_a_zip_bomb_is_refused_before_anything_is_unpacked(tmp_path) -> None:
    export = tmp_path / "export.zip"
    with zipfile.ZipFile(export, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr("conversations.json", b"[" + b" " * (20 * 1024 * 1024) + b"]")

    scan = scan_files([export])

    assert scan.conversation_count == 0
    assert any("packed suspiciously tightly" in warning for warning in scan.warnings)


def test_a_privacy_portal_zip_inside_a_zip_is_opened_but_attachments_and_deeper_zips_are_not(tmp_path) -> None:
    deepest = zip_bytes({"conversations.json": [chatgpt_simple("deep", 1762000000.0, "too deep")]})
    inner = zip_bytes({"conversations.json": [chatgpt_simple("inner", 1762000000.0, "from the inner zip")],
                       "nested.zip": deepest})
    files = zip_bytes({"conversations.json": [chatgpt_simple("attachment", 1762000000.0, "an attachment")]})
    export = write_zip(tmp_path / "privacy-request.zip", {
        "Conversations__2026-11-01.zip": inner,
        "Files__2026-11-01.zip": files,
    })

    scan = scan_files([export])

    assert [conversation.id for conversation in scan.iter_conversations()] == ["inner"]
    assert any(item.name.endswith("Files__2026-11-01.zip") for item in scan.skipped)
    assert any(item.name.endswith("nested.zip") for item in scan.skipped)


def test_files_are_recognised_by_content_when_misnamed(tmp_path) -> None:
    claude_file = tmp_path / "notes.txt"
    claude_file.write_text(json.dumps([claude_conversation()]), encoding="utf-8")
    chatgpt_zip = write_zip(tmp_path / "holiday-photos.bin", {
        "data/stuff.json": [chatgpt_simple("misnamed", 1762000000.0, "hello")],
    })
    memory_named_like_chats = tmp_path / "conversations.json"
    memory_named_like_chats.write_text(json.dumps(claude_memories_old()), encoding="utf-8")

    scan = scan_files([claude_file, chatgpt_zip, memory_named_like_chats])

    providers = sorted(conversation.provider for conversation in scan.iter_conversations())
    assert providers == ["chatgpt", "claude"]
    assert scan.counts["claude"].facts > 0
    assert scan.counts["claude"].conversations == 1


def test_unrecognised_damaged_and_missing_files_give_plain_warnings(tmp_path) -> None:
    unknown = tmp_path / "settings.json"
    unknown.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
    broken = tmp_path / "conversations.json"
    broken.write_text('[{"mapping": {}, "title": "cut', encoding="utf-8")
    damaged_zip = tmp_path / "export.zip"
    damaged_zip.write_bytes(b"PK\x03\x04" + b"\x00" * 64)

    scan = scan_files([unknown, broken, damaged_zip, tmp_path / "missing.zip"])

    assert scan.facts == [] and scan.conversation_count == 0
    assert any(item.name == "settings.json" for item in scan.skipped)
    assert any("not valid JSON" in warning for warning in scan.warnings)
    assert any("could not be read" in warning for warning in scan.warnings)
    assert any("missing.zip" in warning for warning in scan.warnings)


def test_a_file_cut_short_keeps_the_chats_before_the_damage(tmp_path) -> None:
    whole = json.dumps([chatgpt_simple(f"c{n}", 1762000000.0 + n, f"question {n}") for n in range(3)])
    cut = tmp_path / "conversations.json"
    cut.write_text(whole[: whole.rindex('{"id": "c2"') + 40], encoding="utf-8")

    scan = scan_files([cut])

    assert [conversation.id for conversation in scan.iter_conversations()] == ["c0", "c1"]
    assert any("could not be read to the end" in warning for warning in scan.warnings)


def test_scanning_writes_nothing_anywhere(tmp_path, monkeypatch) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    export = write_zip(inputs / "export.zip", {
        "conversations.json": [claude_conversation()],
        "memories.json": claude_memories_old(),
    })
    workdir = tmp_path / "cwd"
    workdir.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.chdir(workdir)
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(data_dir))
    monkeypatch.setenv("TMP", str(tmp_path / "tmp"))
    monkeypatch.setenv("TEMP", str(tmp_path / "tmp"))
    before = {path: path.stat().st_mtime_ns for path in tmp_path.rglob("*")}
    written: list[str] = []
    real_open = io.open

    def watching_open(file, mode="r", *args, **kwargs):
        if any(flag in mode for flag in "wax+"):
            written.append(os.fspath(file))
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", watching_open)
    monkeypatch.setattr(io, "open", watching_open)

    scan = scan_files([export])
    list(scan.iter_conversations())

    assert scan.facts and scan.conversation_count == 1
    assert written == []
    assert {path: path.stat().st_mtime_ns for path in tmp_path.rglob("*")} == before
    assert not (tmp_path / "tmp").exists()
