"""`row-bot mcp` (E4): an editor asks Row-Bot through the real app, with Row-Bot's approvals intact.

The bridge runs in-process over byte streams and reaches the app's real editor routes (access
middleware included) through a test client; the model is scripted, so no provider is called. The
keychain is in memory, and every editor config lives in a fake home under tmp_path.
"""

from __future__ import annotations

import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from row_bot.access.config import AccessConfig, DeploymentMode
from row_bot.api.v1.routes import create_client_platform_app
from row_bot.api.v1.security import ClientSecurity, current_policy_snapshot
from row_bot.editors import bridge, catalog, store
from row_bot.editors.routes import register_editor_routes
from tests.contracts.client_platform.test_headless_lifecycle import platform  # noqa: F401
from tests.helpers.access import person_window
from tests.helpers.client_platform_fakes import (CheckpointCommit, FakeMonotonicClock, ScriptedAgentStream, StreamBarrier,
                                                 fixture_id)

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

ORIGIN = "http://localhost"
MODEL = "model:fixture:synthetic"


class Recording(ScriptedAgentStream):
    """Keeps the text and settings each turn ran with."""

    def __init__(self, *scripts) -> None:
        super().__init__(*scripts)
        self.prompts: list[str] = []
        self.levels: list[str] = []

    def stream(self, text, enabled_tools, config, *, stop_event=None):
        self.prompts.append(text)
        self.levels.append(config["configurable"].get("approval_mode"))
        yield from super().stream(text, enabled_tools, config, stop_event=stop_event)


def answer(text: str, label: str):
    native_id = fixture_id(label + ":answer")
    return (CheckpointCommit((AIMessage(content=text, id=native_id),), native_id), ("done", text))


def asks_to_delete(label: str):
    tool_id, ai_id = fixture_id(label + ":tool"), fixture_id(label + ":ai")
    return (
        CheckpointCommit((AIMessage(content="", id=ai_id, tool_calls=[
            {"id": tool_id, "name": "workspace_file_delete", "args": {}}]),), ai_id),
        ("tool_call", {"id": tool_id, "message_id": ai_id}),
        ("interrupt", [{"__interrupt_id": fixture_id(label + ":interrupt"), "tool": "workspace_file_delete",
                        "description": "Delete file: notes/old-draft.md", "args": {}}]),
    )


@pytest.fixture
def keychain():
    from row_bot import secret_store

    backend = secret_store.MemoryBackend()
    secret_store._set_backend_for_tests(backend)
    yield backend
    secret_store._set_backend_for_tests(None)


@pytest.fixture
def home(tmp_path, monkeypatch):
    folder = tmp_path / "home"
    folder.mkdir()
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(folder))
    for name in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "XDG_CONFIG_HOME", "APPDATA"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: folder))
    return folder


@pytest.fixture
def app(platform, keychain, home, monkeypatch):  # noqa: F811
    from row_bot import models

    monkeypatch.setattr(models, "get_current_model", lambda: MODEL)
    application = create_client_platform_app(
        platform, access_config=AccessConfig(deployment_mode=DeploymentMode.DESKTOP),
        security=ClientSecurity(platform.instance_id, clock=FakeMonotonicClock(), policy=current_policy_snapshot),
        choices=lambda: {"models": [], "capabilities": [], "catalog_stale": False},
        session_authenticator=person_window)
    register_editor_routes(application, platform)
    return application


@pytest.fixture
def settings(app):
    """The person's own Row-Bot window."""
    with TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 18181)) as client:
        client.headers.update({"Origin": ORIGIN})
        yield client


@pytest.fixture
def local(app):
    """A program on this computer, as row-bot mcp is: loopback, no browser Origin."""
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 18182)) as client:
        yield client


def connect(settings, editor_id: str = "claude-code") -> str:
    response = settings.post(f"/api/editors/{editor_id}/connect")
    assert response.status_code == 200, response.text
    token = store.read_token(editor_id)
    assert token
    return token


def caller(local):
    """The bridge's call to the app, through the real route (the token from the keychain)."""

    def call(editor_id: str, tool: str, arguments: dict) -> tuple[str, bool]:
        token = store.read_token(editor_id)
        if not token:
            return catalog.NOT_CONNECTED, True
        response = local.post("/api/editors/call", json={"tool": tool, "arguments": arguments},
                              headers={"Authorization": "Bearer " + token})
        if response.status_code == 401:
            return catalog.NOT_CONNECTED, True
        assert response.status_code == 200, response.text
        return response.json()["text"], response.json()["is_error"]

    return call


def session(editor_id: str, call, *messages: dict) -> list[dict]:
    """Run an MCP session over byte streams; returns every message the bridge wrote."""
    stdin = io.BytesIO(b"".join(json.dumps(message).encode() + b"\n" for message in messages))
    stdout = io.BytesIO()
    assert bridge.serve(stdin, stdout, editor_id, caller=call) == 0
    return [json.loads(line) for line in stdout.getvalue().splitlines()]


def tool_call(request_id: int, name: str, arguments: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": "tools/call",
            "params": {"name": name, "arguments": arguments}}


def ask(local, message: str, editor_id: str = "claude-code", **extra) -> tuple[str, bool]:
    return caller(local)(editor_id, "ask_row_bot", {"message": message, **extra})


def use_model(platform, fake):  # noqa: F811
    platform.stream_factory, platform.resume_factory = fake.stream, fake.resume


def chat_rows(platform, conversation_id: str) -> list[dict]:  # noqa: F811
    return platform.snapshot(conversation_id)["rows"]


def test_an_editor_sees_three_tools_and_ask_row_bot_runs_a_turn(platform, settings, local, tmp_path):  # noqa: F811
    connect(settings)
    fake = Recording(answer("The tests are in tests/subsystem.", "first"))
    use_model(platform, fake)
    folder = tmp_path / "projects" / "row-bot"
    folder.mkdir(parents=True)

    replies = session("claude-code", caller(local),
                      {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                       "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                  "clientInfo": {"name": "claude-code", "version": "2"}}},
                      {"jsonrpc": "2.0", "method": "notifications/initialized"},
                      {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                      tool_call(3, "ask_row_bot", {"message": "Where are the tests?", "folder": str(folder)}))

    by_id = {reply["id"]: reply for reply in replies if "id" in reply}
    assert by_id[1]["result"]["protocolVersion"] == "2025-06-18"
    assert by_id[1]["result"]["serverInfo"]["name"] == "row-bot"
    tools = {tool["name"]: tool for tool in by_id[2]["result"]["tools"]}
    assert set(tools) == {"ask_row_bot", "search_memory", "list_conversations"}
    assert tools["search_memory"]["annotations"]["readOnlyHint"] is True
    assert tools["list_conversations"]["annotations"]["readOnlyHint"] is True
    result = by_id[3]["result"]
    assert result["isError"] is False
    assert result["content"][0]["text"].startswith("The tests are in tests/subsystem.")
    # The model read exactly the editor's words, as the person's message: nothing added.
    assert fake.prompts == ["Where are the tests?"]
    chat = store.chat_for("claude-code", store_key(folder))
    assert platform.get_conversation(chat)["title"] == "From Claude Code · row-bot"
    person = next(row for row in chat_rows(platform, chat) if row["role"] == "user")
    assert person["from_editor"] == {"id": "claude-code", "name": "Claude Code"}
    assert "note" not in person  # The person's own request, never a note.


def store_key(folder: Path) -> str:
    import os

    return os.path.normcase(str(folder.resolve()))


# --- Tokens: per editor, in the keychain only, revocable, loopback only ---------------------------------

def test_no_token_a_wrong_token_or_a_revoked_token_is_refused(platform, settings, local):  # noqa: F811
    body = {"tool": "list_conversations", "arguments": {}}
    assert local.post("/api/editors/call", json=body).status_code == 401
    assert local.post("/api/editors/call", json=body,
                      headers={"Authorization": "Bearer rbe1_not-a-token"}).status_code == 401

    first = connect(settings, "cursor")
    assert local.post("/api/editors/call", json=body,
                      headers={"Authorization": "Bearer " + first}).status_code == 200
    second = connect(settings, "cursor")  # Connecting again replaces the token.
    assert second != first
    assert local.post("/api/editors/call", json=body,
                      headers={"Authorization": "Bearer " + first}).status_code == 401

    listed = {item["id"]: item for item in settings.get("/api/editors").json()["editors"]}
    assert listed["cursor"]["connected"] and listed["cursor"]["last_used_at"] is None
    assert not listed["codex"]["connected"]
    local.post("/api/editors/call", json=body, headers={"Authorization": "Bearer " + second})
    listed = {item["id"]: item for item in settings.get("/api/editors").json()["editors"]}
    assert listed["cursor"]["last_used_at"]

    assert settings.post("/api/editors/cursor/revoke").json()["revoked"] is True
    assert store.read_token("cursor") is None  # Gone from the keychain too.
    assert local.post("/api/editors/call", json=body,
                      headers={"Authorization": "Bearer " + second}).status_code == 401
    replies = session("cursor", caller(local), tool_call(1, "list_conversations", {}))
    assert replies[0]["result"] == {"content": [{"type": "text", "text": catalog.NOT_CONNECTED}], "isError": True}


def test_a_web_page_another_computer_or_a_proxy_can_not_call(app, settings):
    token = connect(settings)
    body = {"tool": "list_conversations", "arguments": {}}
    bearer = {"Authorization": "Bearer " + token}
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 18183)) as local:
        assert local.post("/api/editors/call", json=body, headers=bearer).status_code == 200
        # A page in a browser on this computer always says where it is from.
        assert local.post("/api/editors/call", json=body,
                          headers={**bearer, "Origin": "http://evil.example"}).status_code == 403
        assert local.post("/api/editors/call", json=body,
                          headers={**bearer, "X-Forwarded-For": "203.0.113.9"}).status_code in {400, 403}
    with TestClient(app, base_url="http://192.168.1.20", client=("192.168.1.30", 18184)) as other:
        assert other.post("/api/editors/call", json=body, headers=bearer).status_code in {400, 403}


class _Recorder(BaseHTTPRequestHandler):
    seen: list[tuple[str, str]] = []

    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        type(self).seen.append((self.path, self.headers.get("Authorization") or ""))
        body = json.dumps({"ok": True, "text": "from the app", "is_error": False}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_row_bot_mcp_calls_only_this_computer_never_a_proxy(platform, keychain, monkeypatch):  # noqa: F811
    from row_bot.data_paths import get_row_bot_data_dir

    class App(_Recorder):
        seen = []

    class Proxy(_Recorder):
        seen = []

    servers = [ThreadingHTTPServer(("127.0.0.1", 0), handler) for handler in (App, Proxy)]
    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        app_server, proxy = servers
        for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY"):
            monkeypatch.setenv(name, f"http://127.0.0.1:{proxy.server_port}")
        for name in ("NO_PROXY", "no_proxy"):
            monkeypatch.delenv(name, raising=False)
        state = get_row_bot_data_dir() / bridge.LAUNCHER_STATE
        assert bridge.call_app("codex", "list_conversations", {}) == (catalog.NOT_RUNNING, True)
        # A state naming another address is only ever read for its port.
        state.write_text(json.dumps({"port": app_server.server_port, "phase": "running", "host": "203.0.113.9"}),
                         encoding="utf-8")
        assert bridge.call_app("codex", "list_conversations", {}) == (catalog.NOT_CONNECTED, True)
        store.connect("codex")
        token = store.read_token("codex")
        assert bridge.call_app("codex", "list_conversations", {}) == ("from the app", False)
        assert App.seen == [(bridge.CALL_PATH, "Bearer " + token)]
        assert Proxy.seen == []
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
    # Closed: nothing listens on that port any more.
    assert bridge.call_app("codex", "list_conversations", {}) == (catalog.NOT_RUNNING, True)


def test_the_token_is_never_logged_or_written_outside_the_keychain(platform, settings, local, home, tmp_path,  # noqa: F811
                                                                   caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    use_model(platform, Recording(answer("Done.", "token")))
    response = settings.post("/api/editors/cursor/connect")
    token = store.read_token("cursor")
    assert token and token not in response.text
    assert ask(local, "Hello", "cursor") == ("Done.", False)
    preview = settings.get("/api/editors/cursor/config").json()["preview"]
    assert settings.post("/api/editors/cursor/config", json={"fingerprint": preview["fingerprint"]}).status_code == 200
    assert token not in settings.get("/api/editors/cursor/snippet").text
    assert token not in caplog.text
    secret = token.encode()
    leaked = [path for path in tmp_path.rglob("*") if path.is_file() and secret in path.read_bytes()]
    assert leaked == []
    assert (home / ".cursor" / "mcp.json").is_file()


# --- Approval levels and approvals ---------------------------------------------------------------------

def test_a_chat_set_to_hands_off_runs_as_auto_from_an_editor(platform, settings, local):  # noqa: F811
    from row_bot import full_autonomy, threads
    from row_bot.application.client_platform import ClientPlatformError
    from row_bot.application.workspace_setup import conversation_workspace

    full_autonomy.confirm()
    threads._set_thread_approval_mode("conversation-a", "full")
    assert conversation_workspace(platform, "conversation-a")["controls"]["approval_mode"] == "full"
    connect(settings)
    fake = Recording(answer("On it.", "hands-off"))
    use_model(platform, fake)

    assert ask(local, "Tidy the notes", conversation_id="conversation-a") == ("On it.", False)
    assert fake.levels == ["auto"]
    workspace = conversation_workspace(platform, "conversation-a")
    assert workspace["controls"]["approval_mode"] == "auto"
    assert workspace["approval"]["full_autonomy_available"] is False
    threads._set_thread_approval_mode("conversation-a", "auto")
    workspace = conversation_workspace(platform, "conversation-a")
    with pytest.raises(ClientPlatformError, match="full_autonomy_editor_conversation"):
        platform._execute({"command_id": fixture_id("full-again:command"), "client_session_id": "window",
                           "type": "conversation.controls", "expected_revision": workspace["revision"],
                           "payload": {"runtime_mode": "agent", "profile_id": "", "approval_mode": "full",
                                       "confirm_full_autonomy": True}}, "conversation-a")
    # A chat no editor reached keeps the person's Hands-off.
    threads._set_thread_approval_mode("conversation-b", "full")
    assert conversation_workspace(platform, "conversation-b")["controls"]["approval_mode"] == "full"


def test_a_long_turn_tells_the_editor_where_its_answer_will_be(platform, settings):  # noqa: F811
    from row_bot.editors.service import run_tool

    connect(settings, "zed")
    barrier = StreamBarrier()
    use_model(platform, Recording((barrier, *answer("Finally.", "long"))))
    result = run_tool("zed", "ask_row_bot", {"message": "Refactor everything"}, service=platform, wait_seconds=0.2)
    chat = store.chat_for("zed", "")
    assert result.text == catalog.still_working("From Zed", chat) and not result.is_error
    barrier.release.set()
    assert barrier.exited.wait(10)


def test_a_waiting_approval_returns_the_waiting_text_and_the_card_shows_in_row_bot(platform, settings, local):  # noqa: F811
    connect(settings)
    fake = Recording(asks_to_delete("waiting"))
    use_model(platform, fake)

    text, is_error = ask(local, "Delete my old draft")
    assert (text, is_error) == (catalog.WAITING + ": Delete file: notes/old-draft.md", False)
    chat = store.chat_for("claude-code", "")
    assert platform.get_conversation(chat)["title"] == "From Claude Code"
    assert platform.get_conversation(chat)["activity_phase"] == "waiting_approval"
    # Asking again while it waits sends nothing new: the person answers in Row-Bot first.
    assert ask(local, "Is it done?")[0].startswith(catalog.WAITING)
    assert len(fake.calls) == 1


# --- Read-only tools ---------------------------------------------------------------------------------------

def _stored_bytes(folder: Path) -> dict[str, bytes]:
    # SQLite's shared-memory index (-shm) changes as readers come and go; it holds no data.
    return {str(path): path.read_bytes() for path in sorted(folder.rglob("*"))
            if path.is_file() and not path.name.startswith(store.DB_NAME) and not path.name.endswith("-shm")}


def test_search_memory_and_list_conversations_only_read(platform, settings, local, tmp_path, monkeypatch,  # noqa: F811
                                                         reload_for_data_dir):
    from row_bot import tasks, threads

    (kg,) = reload_for_data_dir(None, "row_bot.knowledge_graph")
    kg._skip_reindex = True
    monkeypatch.setattr(kg, "semantic_search", lambda *_args, **_kwargs: [])
    (memory,) = reload_for_data_dir(None, "row_bot.memory")
    memory.save_memory("preference", "Editor keybindings", "Uses Vim keybindings in every editor.")
    threads.create_thread("A private chat", thread_id="conversation-incognito", incognito=True)
    threads.create_thread("Team thread", thread_id="conversation-shared")
    tasks.record_thread_channel_ref("conversation-shared", channel="slack", target="C1:1",
                                    external_conversation_id="C1:1", shared=True)
    connect(settings)
    before = _stored_bytes(tmp_path)

    replies = session("claude-code", caller(local), tool_call(1, "search_memory", {"query": "Vim"}),
                      tool_call(2, "list_conversations", {}))
    by_id = {reply["id"]: reply["result"] for reply in replies}
    memories = json.loads(by_id[1]["content"][0]["text"])
    assert memories[0]["subject"] == "Editor keybindings"
    assert "Vim keybindings" in memories[0]["content"]
    chats = {chat["conversation_id"] for chat in json.loads(by_id[2]["content"][0]["text"])}
    assert {"conversation-a", "conversation-b"} <= chats
    assert not chats & {"conversation-incognito", "conversation-shared"}
    # Nothing was written anywhere but editors.db, where the editor's last use is kept.
    after = _stored_bytes(tmp_path)
    assert [path for path in after if after[path] != before.get(path)] == []
    # Neither may be continued from an editor.
    assert ask(local, "Hi", conversation_id="conversation-shared") == (catalog.NOT_CONTINUABLE, True)
    assert ask(local, "Hi", conversation_id="conversation-incognito") == (catalog.NOT_CONTINUABLE, True)


# --- One chat per editor and folder, bound to its code folder ----------------------------------------------

def test_each_editor_and_folder_has_its_chat_bound_to_the_code_folder(platform, settings, local, tmp_path,  # noqa: F811
                                                                       monkeypatch):
    from row_bot.conversation_resources import list_bindings
    from row_bot.developer import storage

    monkeypatch.setattr(storage, "DEVELOPER_DIR", tmp_path / "developer")
    monkeypatch.setattr(storage, "WORKSPACES_PATH", tmp_path / "developer" / "workspaces.json")
    project = tmp_path / "code" / "row-bot"
    (project / "src").mkdir(parents=True)
    workspace = storage.add_or_update_local_workspace(str(project))
    elsewhere = tmp_path / "scratch"
    elsewhere.mkdir()
    connect(settings, "claude-code")
    connect(settings, "codex")
    use_model(platform, Recording(*(answer(f"Answer {n}", f"bind-{n}") for n in range(4))))

    assert ask(local, "One", folder=str(project / "src")) == ("Answer 0", False)
    in_src = store.chat_for("claude-code", store_key(project / "src"))
    assert platform.get_conversation(in_src)["title"] == "From Claude Code · src"
    assert [(item.kind, item.resource_id) for item in list_bindings(in_src).bindings] == [("workspace", workspace.id)]
    assert ask(local, "Two", folder=str(project / "src")) == ("Answer 1", False)
    assert store.chat_for("claude-code", store_key(project / "src")) == in_src  # The same chat goes on.

    assert ask(local, "Three", "codex", folder=str(project / "src")) == ("Answer 2", False)
    assert store.chat_for("codex", store_key(project / "src")) not in {None, in_src}

    text, is_error = ask(local, "Four", folder=str(elsewhere))
    assert not is_error and text.startswith("Answer 3")
    assert catalog.folder_not_used(str(elsewhere)) in text  # Never a folder Row-Bot wasn't given.
    assert list_bindings(store.chat_for("claude-code", store_key(elsewhere))).bindings == ()


# --- Writing an editor's config: shown first, backed up -----------------------------------------------------

def test_claude_codes_config_gets_only_row_bots_entry_after_a_preview_and_a_backup(settings, home):
    original = {"numStartups": 41, "oauthAccount": {"emailAddress": "person@example.com"},
                "projects": {"/work": {"mcpServers": {}}}, "mcpServers": {"other": {"command": "other"}}}
    path = home / ".claude.json"
    path.write_text(json.dumps(original, indent=2), encoding="utf-8")
    first = path.read_bytes()

    preview = settings.get("/api/editors/claude-code/config").json()["preview"]
    assert preview["path"] == str(path) and preview["action"] == "add" and preview["before"] is None
    assert json.loads(preview["after"])["row-bot"]["args"][-3:] == ["mcp", "--editor", "claude-code"]
    assert "person@example.com" not in json.dumps(preview)  # Only Row-Bot's entry is shown.
    assert path.read_bytes() == first  # A preview changes nothing.

    path.write_text(json.dumps({**original, "numStartups": 42}, indent=2), encoding="utf-8")
    stale = settings.post("/api/editors/claude-code/config", json={"fingerprint": preview["fingerprint"]})
    assert stale.status_code == 409 and stale.json()["code"] == "config_changed"
    seen = path.read_bytes()
    preview = settings.get("/api/editors/claude-code/config").json()["preview"]
    written = settings.post("/api/editors/claude-code/config", json={"fingerprint": preview["fingerprint"]}).json()
    backup = Path(written["written"]["backup_path"])
    assert backup.parent == home and backup.read_bytes() == seen
    after = json.loads(path.read_text(encoding="utf-8"))
    assert {key: value for key, value in after.items() if key != "mcpServers"} == {
        **{key: value for key, value in original.items() if key != "mcpServers"}, "numStartups": 42}
    assert after["mcpServers"]["other"] == {"command": "other"}
    assert after["mcpServers"]["row-bot"] == json.loads(preview["after"])["row-bot"]
    assert settings.get("/api/editors/claude-code/config").json()["preview"]["action"] == "unchanged"


def test_codex_and_cursor_configs_and_a_file_row_bot_will_not_change(settings, home):
    import tomllib

    codex = home / ".codex" / "config.toml"
    codex.parent.mkdir()
    codex.write_text('model = "o4"\n\n[mcp_servers.row-bot]\ncommand = "old"\nargs = []\n\n'
                     '[mcp_servers.other]\ncommand = "other"\n', encoding="utf-8")
    preview = settings.get("/api/editors/codex/config").json()["preview"]
    assert preview["action"] == "replace" and 'command = "old"' in preview["before"]
    assert "tool_timeout_sec = 600" in preview["after"]
    settings.post("/api/editors/codex/config", json={"fingerprint": preview["fingerprint"]})
    parsed = tomllib.loads(codex.read_text(encoding="utf-8"))
    assert parsed["model"] == "o4" and parsed["mcp_servers"]["other"] == {"command": "other"}
    assert parsed["mcp_servers"]["row-bot"]["args"][-3:] == ["mcp", "--editor", "codex"]
    assert parsed["mcp_servers"]["row-bot"]["tool_timeout_sec"] == 600

    preview = settings.get("/api/editors/cursor/config").json()["preview"]
    assert preview["exists"] is False and preview["action"] == "add"
    written = settings.post("/api/editors/cursor/config", json={"fingerprint": preview["fingerprint"]}).json()
    assert written["written"]["backup_path"] is None  # Nothing to back up: a new file.
    cursor = json.loads((home / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
    assert cursor["mcpServers"]["row-bot"]["type"] == "stdio"

    commented = home / ".cursor" / "mcp.json"
    commented.write_text('{\n  // mine\n  "mcpServers": {}\n}\n', encoding="utf-8")
    preview = settings.get("/api/editors/cursor/config").json()["preview"]
    assert preview["action"] == "unavailable" and preview["problem"] == "config_not_plain"
    refused = settings.post("/api/editors/cursor/config", json={"fingerprint": preview["fingerprint"]})
    assert refused.status_code == 409 and commented.read_text(encoding="utf-8").startswith("{\n  // mine")
    assert settings.get("/api/editors/zed/config").status_code == 409  # Zed's is shown, never written.
    snippet = settings.get("/api/editors/zed/snippet").json()["snippet"]
    assert "context_servers" in json.loads(snippet["text"])


# --- The bridge on its own -------------------------------------------------------------------------------

def test_the_bridge_keeps_a_long_call_posted_and_answers_only_protocol():
    release = threading.Event()
    lines: list[dict] = []
    guard = threading.Lock()

    def slow(editor_id, tool, arguments):
        assert release.wait(10)
        return "finished", False

    class Out(io.BytesIO):
        def write(self, data):
            with guard:
                lines.append(json.loads(data))
                if sum(line.get("method") == "notifications/progress" for line in lines) >= 2:
                    release.set()
            return len(data)

    live = bridge.Bridge("codex", Out(), caller=slow, progress_seconds=0.01)
    live.handle_line(json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                                 "params": {"name": "ask_row_bot", "arguments": {"message": "Go"},
                                            "_meta": {"progressToken": "p1"}}}).encode())
    live.handle_line(b"not json")
    live.handle_line(json.dumps({"jsonrpc": "2.0", "id": 8, "method": "resources/list"}).encode())
    live.handle_line(json.dumps({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                                 "params": {"name": "delete_memory", "arguments": {}}}).encode())
    live.wait(10)
    progress = [line for line in lines if line.get("method") == "notifications/progress"]
    assert progress and all(line["params"]["progressToken"] == "p1" for line in progress)
    replies = {line.get("id"): line for line in lines if "id" in line}
    assert replies[7]["result"] == {"content": [{"type": "text", "text": "finished"}], "isError": False}
    assert replies[None]["error"]["code"] == -32700
    assert replies[8]["error"]["code"] == -32601
    assert replies[9]["error"]["code"] == -32602
