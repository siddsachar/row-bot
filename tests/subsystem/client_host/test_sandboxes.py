"""Sandboxes (5.3.1 B4): a second Row-Bot on its own data folder, port and workspace, on made-up data.

Nothing of the real profile reaches one: its environment carries no keys, tokens or Row-Bot
settings of this one, its data folder and workspace are its own, and the real data folder is
private to it. The deterministic tests run a fake process; one slow test starts a real sandbox.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from row_bot import sandboxes
from tests.subsystem.client_protocol.test_protocol_application import _client, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv(sandboxes.SANDBOX_ENV, raising=False)
    monkeypatch.delenv(sandboxes.SANDBOX_OF_ENV, raising=False)
    return tmp_path


class _Process:
    """A sandbox process that answers at once and ends when it's told to."""

    def __init__(self, *args, **kwargs):
        self.args, self.env, self.cwd = args[0], kwargs["env"], kwargs["cwd"]
        self.pid, self.ended = os.getpid(), False

    def poll(self):
        return 0 if self.ended else None

    def terminate(self):
        self.ended = True

    def wait(self, timeout=None):
        return 0


@pytest.fixture
def fake_process(home, monkeypatch):
    started: list[_Process] = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: started.append(_Process(*a, **k)) or started[-1])
    monkeypatch.setattr(sandboxes, "_ping", lambda _port, _secret: True)
    monkeypatch.setattr("row_bot.process_cancellation.kill_process_tree", lambda _process: True)
    yield started
    sandboxes._PROCESSES.clear()
    sandboxes._SECRETS.clear()


def test_a_sandbox_gets_none_of_this_row_bots_keys_settings_or_folders(home):
    sandbox = sandboxes.create("Demo shop", "shop")
    env = sandboxes.environment(sandbox, 51234, "x" * 43, base={
        "PATH": "/usr/bin", "OPENAI_API_KEY": "sk-real", "GITHUB_TOKEN": "ghp", "AWS_SECRET_ACCESS_KEY": "a",
        "MY_PASSWORD": "p", "ROW_BOT_LEARNING_PREVIEW": "1", "ROW_BOT_DATA_DIR": str(home / "data"),
        "ROW_BOT_SECRET_STORE_KEY": "k", "HOME": str(home)})

    folder = home / "data" / "sandboxes" / sandbox.sandbox_id
    assert env["PATH"] == "/usr/bin" and env["HOME"] == str(home)
    for gone in ("OPENAI_API_KEY", "GITHUB_TOKEN", "AWS_SECRET_ACCESS_KEY", "MY_PASSWORD", "ROW_BOT_LEARNING_PREVIEW",
                 "ROW_BOT_SECRET_STORE_KEY"):
        assert gone not in env, gone
    assert (env["ROW_BOT_DATA_DIR"], env["ROW_BOT_WORKSPACE"]) == (str(folder / "data"), str(folder / "workspace"))
    assert (env["ROW_BOT_HOST"], env["ROW_BOT_PORT"]) == ("127.0.0.1", "51234"), "this computer only"
    assert env["ROW_BOT_UPDATES"] == "external" and env["ROW_BOT_INSTALL_COUNT"] == "0"
    assert env[sandboxes.SANDBOX_ENV] == "1" and env[sandboxes.SANDBOX_OF_ENV] == str(home / "data")


def test_inside_a_sandbox_the_real_data_folder_is_private_and_the_workspace_is_its_own(home, monkeypatch):
    from row_bot.data_paths import default_workspace_root, is_private_data_path
    from row_bot.tools.shell_tool import names_private_data

    real = home / "data"
    (real / "memory").mkdir(parents=True)
    assert not is_private_data_path(home / "elsewhere")
    sandbox = sandboxes.create("Demo shop", "shop")
    env = sandboxes.environment(sandbox, 51234, "x" * 43, base={})
    for name in ("ROW_BOT_DATA_DIR", "ROW_BOT_WORKSPACE", sandboxes.SANDBOX_OF_ENV):
        monkeypatch.setenv(name, env[name])
    workspace = Path(env["ROW_BOT_WORKSPACE"])
    other = real / "sandboxes" / "box-other" / "workspace"

    assert default_workspace_root() == workspace
    assert is_private_data_path(real / "memory"), "its file tools never read or change the real profile"
    assert not is_private_data_path(workspace / "docs" / "orders-october.csv"), "its own made-up files are its own"
    assert is_private_data_path(Path(env["ROW_BOT_DATA_DIR"]) / "threads.db")
    assert is_private_data_path(other / "notes.md"), "another sandbox's files are not"
    assert not names_private_data(f'python -c "print(1)" "{workspace / "docs" / "orders-october.csv"}"')
    assert names_private_data(f'type "{real / "memory" / "graph.db"}"')
    assert names_private_data(f'type "{workspace}/../../box-other/workspace/notes.md"'), "no climbing out"
    assert names_private_data(f'type "{other / "notes.md"}"')


def test_a_sandbox_starts_from_its_pack_runs_stops_resets_and_goes(fake_process, home):
    from row_bot.sandbox_packs import FILES

    sandbox = sandboxes.create("", "shop")
    workspace = home / "data" / "sandboxes" / sandbox.sandbox_id / "workspace"
    assert sandbox.name == "A small shop, with its own web app"
    assert sorted(str(path.relative_to(workspace)).replace("\\", "/") for path in workspace.rglob("*")
                  if path.is_file()) == sorted(FILES["shop"])

    started = sandboxes.start(sandbox.sandbox_id)
    assert started.status == "starting" and started.port
    [process] = fake_process
    assert process.args == [sys.executable, "-m", "row_bot.app"] and process.cwd == str(workspace)
    running = sandboxes.wait_running(sandbox.sandbox_id, 10)
    assert running.status == "running" and running.url == f"http://127.0.0.1:{running.port}/app-v2/"
    assert sandboxes.running_ports() == {running.port}

    (workspace / "notes.txt").write_text("made in the sandbox", encoding="utf-8")
    assert sandboxes.reset(sandbox.sandbox_id).status == "stopped" and process.ended
    assert not (workspace / "notes.txt").exists() and (workspace / "app" / "index.html").is_file()
    assert sandboxes.running_ports() == set()

    sandboxes.delete(sandbox.sandbox_id)
    assert sandboxes.get(sandbox.sandbox_id) is None and not workspace.exists()
    with pytest.raises(sandboxes.SandboxError, match="no longer here"):
        sandboxes.start(sandbox.sandbox_id)


def test_at_most_five_sandboxes_and_only_known_packs(home):
    with pytest.raises(sandboxes.SandboxError, match="one of"):
        sandboxes.create("x", "bank-accounts")
    for index in range(sandboxes.MAX_SANDBOXES):
        sandboxes.create(f"Box {index}")
    with pytest.raises(sandboxes.SandboxError, match="At most 5"):
        sandboxes.create("One more")


def test_settings_list_create_start_and_delete_sandboxes_but_a_sandbox_starts_none(service, fake_process,  # noqa: F811
                                                                                  monkeypatch):
    with _client(service) as client:
        _, headers = bootstrap(client)
        page = client.get("/api/v1/row-bot-sandboxes", headers=headers)
        assert page.status_code == 200, page.text
        assert page.json()["available"] and page.json()["items"] == []
        assert [pack["pack"] for pack in page.json()["packs"]] == ["empty", "shop", "charity", "paperwork", "code"]
        created = client.post("/api/v1/row-bot-sandboxes", headers=headers, json={"name": "Demo", "pack": "code"})
        assert created.status_code == 200, created.text
        box = created.json()["sandbox_id"]
        started = client.post(f"/api/v1/row-bot-sandboxes/{box}/start", headers=headers)
        assert started.json()["status"] in {"starting", "running"}
        assert client.post(f"/api/v1/row-bot-sandboxes/{box}/explode", headers=headers).status_code == 404
        assert client.post("/api/v1/row-bot-sandboxes/box-00000000/start", headers=headers).status_code == 404
        gone = client.delete(f"/api/v1/row-bot-sandboxes/{box}", headers=headers)
        assert gone.status_code == 200 and gone.json()["items"] == []

        monkeypatch.setenv(sandboxes.SANDBOX_ENV, "1")
        inside = client.get("/api/v1/row-bot-sandboxes", headers=headers).json()
        assert inside["available"] is False and inside["items"] == []
        refused = client.post("/api/v1/row-bot-sandboxes", headers=headers, json={"pack": "empty"})
        assert (refused.status_code, refused.json()["code"]) == (409, "row_bot_sandbox_refused")


def test_a_sandboxs_windows_carry_its_mark(service, monkeypatch):  # noqa: F811
    with _client(service) as client:
        handshake, _headers = bootstrap(client)
        assert handshake["sandbox"] is False
    monkeypatch.setenv(sandboxes.SANDBOX_ENV, "1")
    with _client(service) as client:
        handshake, _headers = bootstrap(client)
        assert handshake["sandbox"] is True


def test_row_bot_starts_a_sandbox_only_after_asking_and_opens_it_signed_in(fake_process, monkeypatch):
    from row_bot.tools import approval_gate, browser_tool

    asked: list[dict] = []
    monkeypatch.setattr(approval_gate, "current_approval_mode", lambda: "approve")
    monkeypatch.setattr("langgraph.types.interrupt", lambda payload: asked.append(payload) or False)
    assert "didn't want a sandbox" in browser_tool.sandbox("create", name="Demo", pack="shop")
    assert asked[0]["label"] == "Start a sandbox" and sandboxes.list_sandboxes() == []

    monkeypatch.setattr("langgraph.types.interrupt", lambda payload: True)
    made = browser_tool.sandbox("create", name="Demo", pack="shop")
    assert "running at http://127.0.0.1:" in made and "Open it with action open" in made
    box = sandboxes.list_sandboxes()[0].sandbox_id
    opened: list[str] = []
    monkeypatch.setattr(sandboxes, "sign_in_url", lambda _box: "http://127.0.0.1:1/app-v2/#sign-in=t")
    monkeypatch.setattr(browser_tool, "browser_navigate", lambda url: opened.append(url) or "Opened.")
    assert browser_tool.sandbox("open", sandbox=box).startswith("Opened Demo in this chat's browser, signed in.")
    assert opened == ["http://127.0.0.1:1/app-v2/#sign-in=t"]
    monkeypatch.setenv(sandboxes.SANDBOX_ENV, "1")
    assert "starts no sandboxes" in browser_tool.sandbox("list")


@pytest.mark.slow
def test_a_real_sandbox_starts_signs_a_browser_in_and_stops(home):
    sandbox = sandboxes.create("Real", "empty")
    try:
        sandboxes.start(sandbox.sandbox_id)
        running = sandboxes.wait_running(sandbox.sandbox_id, sandboxes.START_SECONDS)
        log = Path(home / "data" / "sandboxes" / sandbox.sandbox_id / "sandbox.log")
        assert running.status == "running", log.read_text(encoding="utf-8", errors="replace")[-2000:]
        link = sandboxes.sign_in_url(sandbox.sandbox_id)
        assert link.startswith(f"http://127.0.0.1:{running.port}/app-v2/#sign-in=")
    finally:
        sandboxes.stop(sandbox.sandbox_id)
    assert sandboxes.get(sandbox.sandbox_id).status == "stopped"


def test_a_sandbox_a_crash_left_running_is_ended_before_it_starts_again(fake_process, home, monkeypatch):
    """Never two Row-Bots on one sandbox's data folder: one a crash of this Row-Bot left running is ended."""
    from row_bot import background_processes

    sandbox = sandboxes.create("Demo shop", "shop")
    started = sandboxes.start(sandbox.sandbox_id)
    assert (started.pid, started.pid_birth) != (0, 0.0), "its process is recorded"
    sandboxes._PROCESSES.clear()  # This Row-Bot crashed: it no longer holds the process.
    ended: list[object] = []
    monkeypatch.setattr("row_bot.process_cancellation.kill_process_tree", lambda process: ended.append(process))
    monkeypatch.setattr(background_processes, "_same_process",
                        lambda pid, birth: f"pid {pid}" if (pid, birth) == (started.pid, started.pid_birth) else None)

    assert sandboxes.end_left_overs() == 1 and ended == [f"pid {started.pid}"]
    assert sandboxes.get(sandbox.sandbox_id).pid == 0
    assert sandboxes.end_left_overs() == 0, "nothing left"


def test_a_sandbox_says_when_it_has_no_model_yet(home):
    import json

    sandbox = sandboxes.create("Demo shop", "shop")
    assert sandboxes.view(sandbox)["needs_model"] is True, "a new sandbox signs in to a provider of its own once"
    settings = home / "data" / "sandboxes" / sandbox.sandbox_id / "data" / "model_settings.json"
    settings.write_text(json.dumps({"model": "model:codex:gpt-6.1-sol"}), encoding="utf-8")
    assert sandboxes.view(sandbox)["needs_model"] is False


def _sign_in(home, sandbox_id: str, model: str = "model:codex:gpt-6.1-sol") -> Path:
    import json

    data = home / "data" / "sandboxes" / sandbox_id / "data"
    (data / "model_settings.json").write_text(json.dumps({"model": model}), encoding="utf-8")
    (data / "providers.json").write_text("{}", encoding="utf-8")
    (data / "secure-secrets").mkdir(exist_ok=True)
    (data / "secure-secrets" / "codex.bin").write_bytes(b"sealed")
    return data


def test_a_reset_brings_the_pack_back_but_keeps_the_sandboxs_sign_in(fake_process, home):
    sandbox = sandboxes.create("Demo shop", "shop")
    data = _sign_in(home, sandbox.sandbox_id)
    (data / "threads.db").write_bytes(b"chats")

    sandboxes.reset(sandbox.sandbox_id)

    assert not (data / "threads.db").exists(), "its chats go"
    assert sandboxes.model(sandbox.sandbox_id) == "model:codex:gpt-6.1-sol", "it doesn't ask for a sign-in again"
    assert (data / "providers.json").is_file() and (data / "secure-secrets" / "codex.bin").read_bytes() == b"sealed"


def test_a_sandboxs_new_chats_start_at_the_level_of_the_chat_that_made_it_never_hands_off(home):
    from row_bot.approval_policy import SANDBOX_LEVEL_ENV, starting_level

    for made_at, starts in (("auto", "auto"), ("full", "auto"), ("approve", "approve"), ("block", "block"),
                            ("", "approve")):
        sandbox = sandboxes.create(f"From {made_at or 'settings'}", "shop", approval=made_at)
        env = sandboxes.environment(sandbox, 51234, "x" * 43, base={SANDBOX_LEVEL_ENV: "full"})
        assert starting_level(env) == starts, made_at
        sandboxes.delete(sandbox.sandbox_id)
    assert starting_level({SANDBOX_LEVEL_ENV: "auto"}) == "approve", "only a sandbox reads it"


def test_creating_a_sandbox_reuses_a_signed_in_one_with_that_pack(fake_process, home, monkeypatch):
    from row_bot.approval_review import ReviewVerdict
    from row_bot.tools import approval_gate, browser_tool

    asked: list[dict] = []
    monkeypatch.setattr(approval_gate, "current_approval_mode", lambda: "auto")
    monkeypatch.setattr("row_bot.approval_review.review",
                        lambda payload, **_kw: asked.append(payload) or ReviewVerdict("approve", "You asked for it."))
    browser_tool.sandbox("create", name="Video 1", pack="shop")
    [first] = sandboxes.list_sandboxes()
    assert first.approval == "auto", "made from a chat on Auto"
    _sign_in(home, first.sandbox_id)
    sandboxes.wait_running(first.sandbox_id, 10)
    asked.clear()

    again = browser_tool.sandbox("create", name="Video 2", pack="shop")

    assert again.startswith("Reusing it") and "signed in (gpt-6.1-sol)" in again
    assert [item.sandbox_id for item in sandboxes.list_sandboxes()] == [first.sandbox_id]
    assert asked == [], "a running one is used as it is"
    assert "no model yet" in browser_tool.sandbox("create", name="Charity", pack="charity")
    browser_tool.sandbox("create", name="Second shop", pack="shop", new=True)
    assert len([item for item in sandboxes.list_sandboxes() if item.pack == "shop"]) == 2
    assert "signed in (gpt-6.1-sol)" in browser_tool.sandbox("list")
