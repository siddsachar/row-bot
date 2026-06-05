from __future__ import annotations

import base64
import sys
from types import SimpleNamespace

import pytest


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMB/6X"
    "8z9sAAAAASUVORK5CYII="
)


def _set_workspace(tmp_path, monkeypatch):
    data_dir = tmp_path / ".row-bot"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(data_dir))
    inbox = tmp_path / "inbox"
    workspace = tmp_path / "workspace"
    import row_bot.channels.media as media
    from row_bot.tools import registry

    monkeypatch.setattr(media, "_INBOX_DIR", inbox)
    registry.set_tool_config("filesystem", "workspace_root", str(workspace))
    return workspace


def test_chat_attachment_materialization_copies_to_received_files(tmp_path, monkeypatch):
    from row_bot.ui.helpers import materialize_chat_attachments

    workspace = _set_workspace(tmp_path, monkeypatch)
    files = [
        {"name": "note.txt", "data": b"hello"},
        {"name": "../unsafe.bin", "data": b"raw"},
    ]

    manifest = materialize_chat_attachments(files)

    assert files[0]["workspace_path"] == "Received Files/note.txt"
    assert (workspace / "Received Files" / "note.txt").read_bytes() == b"hello"
    assert files[1]["workspace_path"] == "Received Files/unsafe.bin"
    assert (workspace / "Received Files" / "unsafe.bin").read_bytes() == b"raw"
    assert [item["workspace_path"] for item in manifest] == [
        "Received Files/note.txt",
        "Received Files/unsafe.bin",
    ]


def test_process_attached_files_includes_original_names_workspace_paths_and_cache(monkeypatch):
    from row_bot.ui.helpers import process_attached_files
    import row_bot.data_reader as data_reader

    class FakePage:
        def extract_text(self):
            return "pdf body"

    class FakePdfReader:
        def __init__(self, source):
            self.pages = [FakePage()]

    monkeypatch.setitem(sys.modules, "pypdf", SimpleNamespace(PdfReader=FakePdfReader))
    monkeypatch.setattr(
        data_reader,
        "read_data_file",
        lambda source, *, name="", max_chars=None, sheet="": f"summary for {name}",
    )

    files = [
        {"name": "note.txt", "data": b"hello", "workspace_path": "Received Files/note.txt"},
        {"name": "rows.csv", "data": b"a,b\n1,2\n", "workspace_path": "Received Files/rows.csv"},
        {"name": "doc.json", "data": b'{"a": 1}', "workspace_path": "Received Files/doc.json"},
        {"name": "book.xlsx", "data": b"xlsx", "workspace_path": "Received Files/book.xlsx"},
        {"name": "legacy.xls", "data": b"xls", "workspace_path": "Received Files/legacy.xls"},
        {"name": "paper.pdf", "data": b"%PDF", "workspace_path": "Received Files/paper.pdf"},
        {"name": "photo.png", "data": PNG_1X1, "workspace_path": "Received Files/photo.png"},
    ]
    cache = {}

    context, images, warnings = process_attached_files(files, None, cache, model_name="qwen")

    assert warnings == []
    for f in files:
        assert f["name"] in context
        assert f"Workspace path: {f['workspace_path']}" in context
    assert "workspace_read_file with this exact workspace-relative path" in context
    assert "pdf body" in context
    assert "summary for book.xlsx" in context
    assert images
    assert set(cache) == {"rows.csv", "doc.json", "book.xlsx", "legacy.xls"}


def test_workspace_read_file_reads_received_file_paths_and_bare_alias(tmp_path, monkeypatch):
    workspace = _set_workspace(tmp_path, monkeypatch)
    from row_bot.tools.filesystem_tool import _make_pdf_aware_read_tool, get_and_clear_displayed_image
    received = workspace / "Received Files"
    received.mkdir(parents=True)
    (received / "note.txt").write_text("hello", encoding="utf-8")
    (received / "rows.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (received / "doc.json").write_text('[{"a": 1, "b": 2}]', encoding="utf-8")
    (received / "photo.png").write_bytes(PNG_1X1)

    class FakePage:
        def extract_text(self):
            return "pdf body"

    class FakePdfReader:
        def __init__(self, source):
            self.pages = [FakePage()]

    monkeypatch.setitem(sys.modules, "pypdf", SimpleNamespace(PdfReader=FakePdfReader))
    (received / "paper.pdf").write_bytes(b"%PDF")

    read_tool = _make_pdf_aware_read_tool(str(workspace))

    assert read_tool.invoke({"file_path": "Received Files/note.txt"}) == "hello"
    assert read_tool.invoke({"file_path": "note.txt"}) == "hello"
    assert "rows.csv" in read_tool.invoke({"file_path": "Received Files/rows.csv"})
    assert "doc.json" in read_tool.invoke({"file_path": "Received Files/doc.json"})
    assert "pdf body" in read_tool.invoke({"file_path": "Received Files/paper.pdf"})
    assert "Displayed image" in read_tool.invoke({"file_path": "Received Files/photo.png"})
    assert get_and_clear_displayed_image()["name"] == "photo.png"


def test_workspace_read_file_reads_xlsx_when_dependency_available(tmp_path, monkeypatch):
    pytest.importorskip("openpyxl")
    import pandas as pd

    workspace = _set_workspace(tmp_path, monkeypatch)
    from row_bot.tools.filesystem_tool import _make_pdf_aware_read_tool
    received = workspace / "Received Files"
    received.mkdir(parents=True)
    path = received / "book.xlsx"
    pd.DataFrame({"a": [1], "b": [2]}).to_excel(path, index=False)

    result = _make_pdf_aware_read_tool(str(workspace)).invoke({"file_path": "Received Files/book.xlsx"})

    assert "book.xlsx" in result
    assert "a" in result
    assert "b" in result


def test_workspace_read_file_reads_xls_when_dependency_available(tmp_path, monkeypatch):
    pytest.importorskip("xlrd")
    pytest.importorskip("xlwt")
    import pandas as pd

    workspace = _set_workspace(tmp_path, monkeypatch)
    from row_bot.tools.filesystem_tool import _make_pdf_aware_read_tool
    received = workspace / "Received Files"
    received.mkdir(parents=True)
    path = received / "legacy.xls"
    with pd.ExcelWriter(path, engine="xlwt") as writer:
        pd.DataFrame({"a": [1], "b": [2]}).to_excel(writer, index=False)

    result = _make_pdf_aware_read_tool(str(workspace)).invoke({"file_path": "Received Files/legacy.xls"})

    assert "legacy.xls" in result
    assert "a" in result
    assert "b" in result


def test_transcript_reload_strips_hidden_attachment_context():
    from langchain_core.messages import HumanMessage
    from row_bot.ui.helpers import langchain_messages_to_ui_messages, wrap_attachment_context

    hidden = wrap_attachment_context(
        "[Attached data file: rows.csv]\n"
        "Workspace path: Received Files/rows.csv\n"
        "For full file access, call workspace_read_file with this exact workspace-relative path.\n\n"
        "Columns:\n  a (int64)"
    )

    ui_messages = langchain_messages_to_ui_messages([
        HumanMessage(content=f"{hidden}\n\nplease total column a")
    ])

    assert ui_messages == [
        {"role": "user", "content": "\U0001f4ce rows.csv\n\nplease total column a"}
    ]
