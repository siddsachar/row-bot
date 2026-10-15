"""Readers page with fixed budgets and reach the end; capped tools keep the rest readable (WP D1)."""
from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest

from row_bot import tool_outputs
from tests.fixtures.public_web import PUBLIC_ADDRESS, public_web


pytestmark = pytest.mark.subsystem


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    folder = tmp_path / "data"
    folder.mkdir()
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(folder))
    monkeypatch.setattr(tool_outputs, "current_thread_id", lambda: "thread-paging")
    return folder


def _numbered(count: int, width: int = 40) -> str:
    return "".join(f"{f'line {number:06d}':<{width}}\n" for number in range(1, count + 1))


def _continue_offset(text: str) -> int | None:
    match = re.search(r"Continue with offset=(\d+)", text)
    return int(match.group(1)) if match else None


def _read_handle_to_the_end(shortened: str) -> tuple[str, str]:
    """The head shown in a shortened result, and the rest read through its handle."""
    match = re.search(r'read_tool_output\(handle="(out_[0-9a-f]+)", offset=(\d+)\)', shortened)
    assert match, shortened[:300]
    handle, offset = match.group(1), int(match.group(2))
    rest: list[str] = []
    while True:
        page = tool_outputs.read_tool_output(handle, offset=offset)
        header, _, body = page.partition("\n")
        rest.append(body)
        if "End of output." in header:
            break
        offset = _continue_offset(header)
        assert offset is not None, header
    return shortened.split("\n", 1)[1][: int(match.group(2))], "".join(rest)


# ── workspace_read_file ─────────────────────────────────────────────────────

@pytest.fixture
def workspace_tools(data_dir, tmp_path):
    from row_bot.tools import registry
    from row_bot.tools.filesystem_tool import FileSystemTool

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    registry.set_tool_config("filesystem", "workspace_root", str(workspace))
    return workspace, {tool.name: tool for tool in FileSystemTool().as_langchain_tools()}


def test_workspace_read_file_pages_a_large_file_to_its_last_line(workspace_tools):
    workspace, tools = workspace_tools
    text = _numbered(6_000)  # 246K characters: more than any single result
    (workspace / "big.log").write_bytes(text.encode("utf-8"))

    pages, offset = [], 0
    while offset is not None:
        result = tools["workspace_read_file"].invoke({"file_path": "big.log", "offset": offset})
        page, _, footer = result.rpartition("\n[Lines ")
        pages.append(page if footer else result)
        offset = _continue_offset(footer)

    reassembled = "".join(pages) == text
    assert reassembled
    assert len(pages) == 5  # 1,219 lines of 41 characters fit a 50K page


def test_workspace_read_file_budget_does_not_grow_with_the_context_window(workspace_tools, monkeypatch):
    import row_bot.models as models

    workspace, tools = workspace_tools
    (workspace / "big.log").write_bytes(_numbered(20_000).encode("utf-8"))
    monkeypatch.setattr(models, "get_context_size", lambda *args, **kwargs: 1_000_000)

    result = tools["workspace_read_file"].invoke({"file_path": "big.log"})

    assert len(result) <= tool_outputs.PAGE_CHARS + 200
    assert "Lines 1–1,219 of 20,000. Continue with offset=1220." in result


def test_workspace_read_file_summarises_csv_at_the_start_and_pages_its_raw_lines(workspace_tools):
    workspace, tools = workspace_tools
    rows = "".join(f"{index},item {index}\n" for index in range(1, 5_001))
    (workspace / "items.csv").write_bytes(("id,name\n" + rows).encode("utf-8"))

    summary = tools["workspace_read_file"].invoke({"file_path": "items.csv"})
    tail = tools["workspace_read_file"].invoke({"file_path": "items.csv", "offset": 4_990})

    assert "id" in summary and "name" in summary
    assert "5000,item 5000" in tail
    assert "End of text." in tail


# ── developer tools ─────────────────────────────────────────────────────────

@pytest.fixture
def developer(data_dir, tmp_path, monkeypatch):
    import row_bot.tools.developer_tool as developer_tool

    root = tmp_path / "repo"
    root.mkdir()
    workspace = SimpleNamespace(id="ws-1", name="repo", path=str(root))
    monkeypatch.setattr(developer_tool, "_active_workspace", lambda write=False: (workspace, root.resolve()))
    monkeypatch.setattr(developer_tool, "get_thread_id", lambda: "thread-paging")
    monkeypatch.setattr(developer_tool, "_active_approval_mode", lambda: "full")
    return developer_tool, root


def test_developer_read_file_reaches_the_tail_and_every_page_carries_the_file_hash(developer):
    import hashlib

    developer_tool, root = developer
    text = _numbered(5_000)
    (root / "module.py").write_bytes(text.encode("utf-8"))
    digest = hashlib.sha256((root / "module.py").read_bytes()).hexdigest()

    pages, offset = [], 1
    while offset is not None:
        result = developer_tool._read_file("module.py", offset=offset)
        first, _, rest = result.partition("\n")
        assert first == f"SHA256: {digest}"
        page, _, footer = rest.rpartition("\n[Lines ")
        pages.append(page if footer else rest)
        offset = _continue_offset(footer)

    reassembled = "".join(pages) == text
    assert reassembled


def test_developer_get_diff_summarises_with_counts_and_pages_one_files_diff(developer, monkeypatch):
    developer_tool, _root = developer
    diff = "diff --git a/app.py b/app.py\n@@ -1,4000 +1,4000 @@\n" + "".join(
        f"+added line {index:05d}\n" for index in range(4_000)
    )
    monkeypatch.setattr(developer_tool, "list_changed_files",
                        lambda path: [SimpleNamespace(status="M", path="app.py")])
    monkeypatch.setattr(developer_tool, "get_file_diff_stats", lambda path, file: (4000, 12))
    monkeypatch.setattr(developer_tool, "get_file_diff", lambda path, file, limit=0: diff)

    assert "- M app.py (+4000 -12)" in developer_tool._diff()

    pages, offset = [], 1
    while offset is not None:
        result = developer_tool._diff("app.py", offset=offset)
        header, _, rest = result.partition("\n")
        assert header == "app.py: +4000 -12"
        page, _, footer = rest.rpartition("\n[Lines ")
        pages.append(page if footer else rest)
        offset = _continue_offset(footer)

    reassembled = "".join(pages) == diff
    assert reassembled


def test_developer_search_shows_a_preview_of_a_minified_line(developer, monkeypatch):
    developer_tool, root = developer
    (root / "bundle.min.js").write_bytes(("var needle=1;" + "x" * 50_000 + "\n").encode("utf-8"))
    calls = []

    def no_ripgrep(args, **kwargs):
        calls.append(args)
        raise FileNotFoundError("rg")

    monkeypatch.setattr(developer_tool.subprocess, "run", no_ripgrep)

    result = developer_tool._search("needle")

    assert result.startswith("bundle.min.js:1:var needle=1;")
    assert len(result) < 500
    assert "more characters" in result
    # ripgrep, when present, is asked for the same bounded preview.
    assert "--max-columns-preview" in calls[0]


def test_developer_command_output_is_plain_text_and_its_rest_is_readable(developer, monkeypatch):
    from row_bot.developer.runtime import CommandResult

    developer_tool, root = developer
    stdout = _numbered(3_000)  # 123K characters
    monkeypatch.setattr(
        developer_tool,
        "run_workspace_shell_command",
        lambda *args, **kwargs: CommandResult(command="pytest -q", cwd=str(root), returncode=1,
                                              stdout=stdout, stderr="1 failed"),
    )

    result = developer_tool._run_command("pytest -q")

    assert result.startswith("$ pytest -q\nexit code 1")
    assert not result.lstrip().startswith("{") and "\\n" not in result
    assert len(result) < tool_outputs.COMMAND_OUTPUT_CHARS + 1_000
    head, rest = _read_handle_to_the_end(result.split("\n", 2)[2])
    reassembled = head + rest == "stdout:\n" + stdout.rstrip("\n") + "\nstderr:\n1 failed"
    assert reassembled


# ── read_url ────────────────────────────────────────────────────────────────

@pytest.fixture
def web(monkeypatch):
    """read_url opens public pages only: a public name served from a loopback server."""
    with public_web(monkeypatch) as site:
        site.dns["articles.example"] = [PUBLIC_ADDRESS]
        yield site


def test_read_url_pages_a_long_page_to_its_end(data_dir, web):
    from row_bot.tools.url_reader_tool import _read_url

    text = "".join(f"Paragraph {index:05d} of the long article.\n" for index in range(5_000))
    url = "http://articles.example/page"
    web.page(url, text.encode("utf-8"), "text/plain; charset=utf-8")
    parts, offset = [], 0
    while offset is not None:
        result = _read_url(url, offset=offset)
        assert result.startswith(f"SOURCE_URL: {url}\n\n")
        body = result.split("\n\n", 1)[1]
        page, _, footer = body.rpartition("\n\n[Characters ")
        parts.append(page if footer else body)
        offset = _continue_offset(footer)

    reassembled = "".join(parts) == text.strip()
    assert reassembled
    assert len(parts) == 5


# ── capped listings and articles ────────────────────────────────────────────

def test_list_conversations_keeps_newest_and_oldest_and_the_rest_by_handle(data_dir, monkeypatch):
    import row_bot.threads as threads
    from row_bot.tools.conversation_search_tool import _list_conversations

    rows = [(f"t{index}", f"Conversation {index:05d}", "2026-01-01T00:00", "2026-10-01T09:00")
            for index in range(3_000)]
    monkeypatch.setattr(threads, "_list_threads", lambda: rows)

    result = _list_conversations()

    assert len(result) < 21_000
    assert "Conversation 00000" in result and "Conversation 02999" in result
    head, rest = _read_handle_to_the_end(result)
    assert (head + rest).count("• \"Conversation ") == 3_000


def test_task_list_is_compact_json_and_stays_readable_when_long(data_dir, monkeypatch):
    import row_bot.tools.task_tool as task_tool

    tasks = [{"id": f"task-{index}", "icon": "⚡", "name": f"Task {index}", "enabled": True,
              "description": "", "schedule": "daily:09:00", "prompts": ["Check"]} for index in range(2)]
    monkeypatch.setattr(task_tool.tasks_db, "list_tasks", lambda: tasks)

    short = task_tool._task_list()

    assert json.loads(short)[0] == {"id": "task-0", "name": "⚡ Task 0", "enabled": True,
                                    "schedule": "daily:09:00", "steps": 1}
    assert "\n" not in short

    many = [dict(task, id=f"task-{index}", description="d" * 200) for index in range(400) for task in tasks[:1]]
    monkeypatch.setattr(task_tool.tasks_db, "list_tasks", lambda: many)
    head, rest = _read_handle_to_the_end(task_tool._task_list())
    assert len(json.loads(head + rest)) == 400


def test_wiki_read_keeps_a_long_article_readable_by_handle(data_dir, monkeypatch):
    import row_bot.wiki_vault as wiki_vault
    from row_bot.tools.wiki_tool import _read_article

    article = _numbered(3_000)
    monkeypatch.setattr(wiki_vault, "is_enabled", lambda: True)
    monkeypatch.setattr(wiki_vault, "read_article", lambda subject: article)

    result = _read_article("User")

    assert len(result) < 41_000
    head, rest = _read_handle_to_the_end(result)
    reassembled = head + rest == article
    assert reassembled


def test_gmail_search_shortens_bodies_and_a_message_reads_in_full(data_dir):
    from row_bot.tools.gmail_tool import _bounded_gmail_result

    long_body = "word " * 4_000
    found = json.loads(_bounded_gmail_result("search_gmail", [
        {"id": "m1", "threadId": "t1", "snippet": "Hi", "body": long_body, "subject": "S", "sender": "a@b"},
        {"id": "m2", "threadId": "t2", "snippet": "Yo", "body": "short", "subject": "", "sender": "c@d"},
    ]))

    assert len(found[0]["body"]) == 1_500
    assert "get_gmail_message(message_id='m1')" in found[0]["body_note"]
    assert found[1] == {"id": "m2", "threadId": "t2", "snippet": "Yo", "body": "short", "sender": "c@d"}

    huge = "line of the newsletter\n" * 3_000  # 69K characters
    message = json.loads(_bounded_gmail_result("get_gmail_message", {"id": "m1", "body": huge}))
    head, rest = _read_handle_to_the_end(message["body"])
    reassembled = head + rest == huge
    assert reassembled
