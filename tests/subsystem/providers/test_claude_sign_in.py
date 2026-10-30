"""Claude's own sign-in panel (H15): one command, owner only, carried like SSH and never kept.

The behaviour tests run in process: a fake pseudo-terminal plays ``claude auth login`` (Claude's banner, link and
paste prompt, carrying ``SECRET``), and a fake ``claude auth status`` answers afterwards. One real-process test
(slow) runs the fake Claude Code (``tests.helpers.fake_claude``) in a real pseudo-terminal.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import pytest

from row_bot.providers import claude_sign_in as sign_in
from row_bot.providers.claude_subscription import ClaudePlanStatus, claude_code_workdir
from tests.helpers.fake_claude import install_fake_claude
from tests.helpers.open_audit import watch_opens
from tests.subsystem.client_protocol.test_protocol_security import bootstrap, client_app

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

SECRET = "SYNTHETIC-claude-terminal-secret-7f3a"
CODE = "SYNTHETIC-claude-code-91c2#state"


class FakeLogin:
    """``claude auth login`` in a pseudo-terminal, played in process."""

    def __init__(self, argv, *, env, cwd, cols, rows, account) -> None:
        self.argv, self.env, self.cwd, self.size = argv, env, cwd, (cols, rows)
        self.account = account
        self.output = [f"Welcome to Claude Code {SECRET}\r\n",
                       f"https://claude.ai/oauth/authorize?state={SECRET}\r\n", "Paste code here if prompted > "]
        self.line = ""
        self.alive = True
        self.closed = False

    def read(self) -> str:
        text = "".join(self.output)
        self.output.clear()
        return text

    def write(self, data: str) -> None:
        self.output.append(data)  # the terminal echoes what is typed
        self.line += data
        if "\r" not in self.line:
            return
        code, self.line = self.line.split("\r", 1)[0], ""
        if code == CODE:
            self.account["signed_in"] = True
            self.output.append("\r\nLogin successful.\r\n")
            self.alive = False
        else:
            self.output.append("\r\nOAuth error: Invalid code\r\nPaste code here if prompted > ")

    def resize(self, cols: int, rows: int) -> None:
        self.size = (cols, rows)

    def is_alive(self) -> bool:
        return self.alive

    def close(self) -> None:
        self.closed = True
        self.alive = False


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def panel(tmp_path, monkeypatch):
    """A fake Claude Code on PATH (never run here), the in-process login and a fake ``claude auth status``."""
    claude = install_fake_claude(tmp_path, monkeypatch)
    account = {"signed_in": False}
    started: list[FakeLogin] = []

    def factory(argv, **options):
        started.append(FakeLogin(argv, account=account, **options))
        return started[-1]

    def status() -> ClaudePlanStatus:
        if account["signed_in"]:
            return ClaudePlanStatus("ready", version="2.1.300", plan="Claude Max", program="claude")
        return ClaudePlanStatus("signed_out", version="2.1.300", program="claude")

    from row_bot.providers import claude_subscription, model_catalog_cache

    # Settings asks the same fake account, and a model listing it would start is only recorded.
    monkeypatch.setattr(claude_subscription, "check_claude_code", status)
    monkeypatch.setattr(model_catalog_cache, "start_model_catalog_refresh_background", lambda **_: True)
    clock = Clock()
    manager = sign_in.ClaudeSignIns(pty_factory=factory, status=status, clock=clock, sweep_every=None)
    monkeypatch.setattr(sign_in, "_sign_ins", manager)
    return type("Panel", (), {"manager": manager, "started": started, "account": account, "clock": clock,
                              "claude": claude})


def _open(client, headers, **body):
    return client.post("/api/v1/settings/providers/claude-plan/sign-in", json={"cols": 100, "rows": 30, **body},
                       headers=headers)


def _read(client, headers, sign_in_id, cursor=0):
    return client.get(f"/api/v1/settings/providers/claude-plan/sign-in/{sign_in_id}?cursor={cursor}",
                      headers=headers)


def _type(client, headers, sign_in_id, data):
    return client.post(f"/api/v1/settings/providers/claude-plan/sign-in/{sign_in_id}/input", json={"data": data},
                       headers=headers)


def _screen(client, headers, sign_in_id, cursor=0) -> tuple[str, dict]:
    text = ""
    while True:
        answer = _read(client, headers, sign_in_id, cursor)
        assert answer.status_code == 200, answer.text
        value = answer.json()
        text += "".join(frame["data"] for frame in value["frames"])
        cursor = value["cursor"]
        if value["latest"] <= cursor:
            return text, value


def test_the_panel_runs_only_claude_auth_login_without_row_bots_secrets(panel, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-row-bot-secret")
    monkeypatch.setenv("ROW_BOT_SETUP_CODE", "ABCD-EFGH-JKLM")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-the-plan")
    client, _, _ = client_app(remote=True)
    with client:
        _, headers = bootstrap(client)
        opened = _open(client, headers)
        assert opened.status_code == 200, opened.text
        assert opened.json()["browser_opens"] is False  # a remote browser: the person opens Claude's link

    [login] = panel.started
    program = str(Path(panel.claude.bin_dir) / ("claude.cmd" if os.name == "nt" else "claude"))
    assert login.argv == [program, "auth", "login"]
    assert login.cwd == str(claude_code_workdir())
    assert login.size == (100, 30)
    assert not {"OPENAI_API_KEY", "ROW_BOT_SETUP_CODE", "ANTHROPIC_API_KEY"} & set(login.env)
    assert login.env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"


def test_nothing_but_claudes_sign_in_can_be_started(panel, monkeypatch) -> None:
    client, _, _ = client_app(remote=True)
    with client:
        _, headers = bootstrap(client)
        # The request names no program, and one that tries is refused before anything starts.
        for extra in ({"command": "/bin/sh"}, {"argv": ["bash"]}, {"program": "cmd.exe"}):
            assert _open(client, headers, **extra).status_code == 422
        # Whatever the lookup finds, only a program named claude runs, and only with auth login.
        for found in ("/bin/sh", r"C:\Windows\System32\cmd.exe", "/usr/bin/python3", ""):
            monkeypatch.setattr(sign_in, "find_claude_code", lambda found=found: found)
            refused = _open(client, headers)
            assert refused.status_code == 409 and refused.json()["code"] == "claude_code_missing"
    assert panel.started == []
    with pytest.raises(sign_in.SignInRefused):
        sign_in.sign_in_command("/usr/bin/bash")
    assert sign_in.sign_in_command("/data/.local/bin/claude") == ["/data/.local/bin/claude", "auth", "login"]


def test_wrong_code_keeps_claudes_prompt_and_the_right_one_connects_then_it_closes(panel) -> None:
    client, _, _ = client_app(remote=True)
    with client:
        _, headers = bootstrap(client)
        identifier = _open(client, headers).json()["sign_in_id"]
        text, value = _screen(client, headers, identifier)
        assert "Paste code here if prompted > " in text and value["status"] == "running"

        assert _type(client, headers, identifier, "wrong-code\r").status_code == 200
        text, value = _screen(client, headers, identifier, value["cursor"])
        # Claude's own words, carried as they are; Row-Bot adds nothing and the prompt stays open.
        assert text == "wrong-code\r\r\nOAuth error: Invalid code\r\nPaste code here if prompted > "
        assert value["status"] == "running" and value["plan"] is None

        assert _type(client, headers, identifier, CODE + "\r").status_code == 200
        text, value = _screen(client, headers, identifier, value["cursor"])
        assert text.endswith("Login successful.\r\n")
        # Closed when Claude Code exits; whether it worked comes from claude auth status.
        assert value["status"] == "ended" and value["ended"] == "exited"
        assert value["plan"]["state"] == "ready" and value["plan"]["plan"] == "Claude Max"
        assert panel.started[0].closed
        ended = _type(client, headers, identifier, "more\r")
        assert ended.status_code == 409 and ended.json()["code"] == "claude_sign_in_ended"


def test_no_terminal_content_reaches_logs_transcripts_or_storage(panel, caplog, tmp_path) -> None:
    caplog.set_level(logging.DEBUG)
    data_dir = Path(os.environ["ROW_BOT_DATA_DIR"])
    client, service, _ = client_app(remote=True)
    with client:
        _, headers = bootstrap(client)
        identifier = _open(client, headers).json()["sign_in_id"]
        _type(client, headers, identifier, "not-it\r")
        _type(client, headers, identifier, CODE + "\r")
        text, value = _screen(client, headers, identifier)
        assert SECRET in text and "not-it" in text and value["ended"] == "exited"
        assert client.get("/api/v1/settings/providers/live", headers=headers).status_code == 200
        # Until closed, its last output waits in memory only; closing drops it.
        assert panel.manager._current.frames
        closed = client.delete(f"/api/v1/settings/providers/claude-plan/sign-in/{identifier}", headers=headers)
        assert closed.status_code == 200 and panel.manager._current is None
        assert _read(client, headers, identifier).status_code == 404

    for marker in (SECRET, CODE, "not-it"):
        assert marker not in caplog.text
        assert marker not in repr(service.__dict__)  # nothing reached the conversation service
        for path in [*data_dir.rglob("*"), *tmp_path.rglob("*")]:
            if path.is_file() and "fake-claude-bin" not in path.parts:
                assert marker.encode() not in path.read_bytes(), path


def test_a_sign_in_never_outlives_the_server(panel) -> None:
    client, _, _ = client_app(remote=True)
    with client:
        _, headers = bootstrap(client)
        _open(client, headers)
        assert panel.manager.running()
    assert panel.started[0].closed and not panel.manager.running()


def test_only_the_device_session_that_opened_it_and_never_without_one(panel) -> None:
    client, _, active = client_app(remote=True)
    with client:
        assert _open(client, {"Origin": "http://localhost"}).status_code == 401
        _, owner = bootstrap(client)
        _, other = bootstrap(client)
        identifier = _open(client, owner).json()["sign_in_id"]
        for answer in (_read(client, other, identifier), _type(client, other, identifier, "x"),
                       client.delete(f"/api/v1/settings/providers/claude-plan/sign-in/{identifier}",
                                     headers=other)):
            assert answer.status_code == 404
        assert panel.started[0].output  # untouched by the other session
        active["value"] = False  # signed out
        assert _read(client, owner, identifier).status_code == 401
        assert _type(client, owner, identifier, "x").status_code == 401
        assert _open(client, owner).status_code == 401
    assert len(panel.started) == 1


def test_one_sign_in_at_a_time(panel) -> None:
    client, _, _ = client_app(remote=True)
    with client:
        _, headers = bootstrap(client)
        first = _open(client, headers).json()["sign_in_id"]
        second = _open(client, headers).json()["sign_in_id"]
        assert first != second
        assert panel.started[0].closed and not panel.started[1].closed
        assert _read(client, headers, first).status_code == 404
        assert _read(client, headers, second).json()["status"] == "running"
        assert panel.manager.running()
        # Installing Claude Code waits until the sign-in is closed, and the other way round.
        busy = client.post("/api/v1/settings/providers/claude-plan/install", json={"update": True},
                           headers=headers)
        assert busy.status_code == 409 and busy.json()["code"] == "claude_sign_in_running"
        closed = client.delete(f"/api/v1/settings/providers/claude-plan/sign-in/{second}", headers=headers)
        assert closed.status_code == 200 and panel.started[1].closed
        assert not panel.manager.running()


def test_an_idle_sign_in_closes_after_ten_minutes(panel) -> None:
    manager, clock = panel.manager, panel.clock
    opened = manager.open("device-session")
    manager.read(opened.id, "device-session")
    clock.now += sign_in.IDLE_SECONDS - 1
    manager.sweep()
    assert manager.running()
    manager.input(opened.id, "device-session", "a")  # the person typed: the ten minutes start again
    manager.read(opened.id, "device-session")  # the panel reads the echo straight away
    clock.now += sign_in.IDLE_SECONDS - 1
    manager.sweep()
    assert manager.running()
    clock.now += 1
    manager.sweep()
    assert not manager.running() and panel.started[0].closed
    value = manager.read(opened.id, "device-session")
    assert (value["status"], value["ended"], value["frames"]) == ("ended", "idle", [])
    clock.now += sign_in.ENDED_SECONDS
    manager.sweep()
    with pytest.raises(sign_in.SignInRefused):
        manager.read(opened.id, "device-session")


def test_output_is_bounded_in_memory(panel) -> None:
    manager = panel.manager
    opened = manager.open("device-session")
    panel.started[0].output.append("x" * (sign_in.OUTPUT_LIMIT * 2))
    first = manager.read(opened.id, "device-session")
    assert first["truncated"] is True
    assert opened.output_bytes <= sign_in.OUTPUT_LIMIT
    assert len(first["frames"]) == 16


def test_claudes_credential_file_is_never_opened(panel, tmp_path) -> None:
    """Claude Code keeps its sign-in in ~/.claude/.credentials.json (here written as if by Claude). Row-Bot reads
    the result from ``claude auth status`` and never opens that file, through the panel, Settings or a backup."""
    home = Path(os.environ["HOME"])
    credentials = home / ".claude" / ".credentials.json"
    credentials.parent.mkdir(parents=True)
    credentials.write_text('{"claudeAiOauth": {"accessToken": "' + SECRET + '"}}', encoding="utf-8")
    from row_bot.application import profile_backup

    client, _, _ = client_app(remote=True)
    with watch_opens(home / ".claude") as opened, client:
        _, headers = bootstrap(client)
        identifier = _open(client, headers).json()["sign_in_id"]
        _type(client, headers, identifier, CODE + "\r")
        _, value = _screen(client, headers, identifier)
        assert value["plan"]["state"] == "ready"
        assert client.get("/api/v1/settings/providers/live", headers=headers).status_code == 200
        profile_backup.create_backup(home, tmp_path / "backup-out")
    assert opened == []


@pytest.mark.slow
def test_the_real_pseudo_terminal_carries_claudes_sign_in(tmp_path, monkeypatch) -> None:
    """The fake Claude Code in a real pseudo-terminal: its prompt shows, the pasted code reaches it, it writes
    its own credential file (in its own process) and Row-Bot learns the result from ``claude auth status``."""
    install_fake_claude(tmp_path, monkeypatch, auth={"loggedIn": False}, auth_exit=1, login_code=CODE,
                        login_marker=SECRET)
    manager = sign_in.ClaudeSignIns(sweep_every=None)
    home = Path(os.environ["HOME"])
    with watch_opens(home / ".claude") as opened:
        session = manager.open("device-session", cols=100, rows=30)
        text, deadline = "", time.monotonic() + 30
        while "Paste code here" not in text and time.monotonic() < deadline:
            text += "".join(frame["data"] for frame in manager.read(session.id, "device-session",
                                                                      session.sequence)["frames"])
            time.sleep(0.05)
        assert SECRET in text
        manager.input(session.id, "device-session", CODE + "\r")
        value: dict = {}
        while time.monotonic() < deadline:
            value = manager.read(session.id, "device-session", session.sequence)
            if value["status"] == "ended":
                break
            time.sleep(0.05)
    assert value["ended"] == "exited" and value["plan"]["state"] == "ready"
    assert (home / ".claude" / ".credentials.json").exists()  # written by Claude Code's process
    assert opened == []  # never opened by Row-Bot's
    manager.close_all()
