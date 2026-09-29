"""Open in your terminal: the folder, the app and its environment, all faked.

No terminal window or process is ever started: ``which`` and ``Popen`` are
replaced by recorders, so each platform's argument list is checked on any OS.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest

from row_bot.application import external_terminal
from row_bot.application.client_platform import ClientPlatformError
from tests.subsystem.client_protocol.test_protocol_application import service  # noqa: F401
from tests.subsystem.developer.test_conversation_creation import _draft, creation  # noqa: F401

pytestmark = pytest.mark.subsystem

FOLDER = Path("/synthetic/Tiny date app; calc &")


def _which(*available: str):
    return lambda name: f"/fixture/bin/{name}" if name in available else None


class _Popen:
    def __init__(self, failure: Exception | None = None) -> None:
        self.calls: list[tuple[list[str], dict]] = []
        self.failure = failure

    def __call__(self, argv, **options):
        self.calls.append((list(argv), options))
        if self.failure is not None:
            raise self.failure
        return object()


def _open(platform: str, which, popen: _Popen, environ: dict | None = None) -> bool:
    return external_terminal.open_terminal_at(
        FOLDER, platform=platform, which=which, popen=popen, environ=environ or {"PATH": "/fixture/bin"})


def test_windows_terminal_opens_in_the_folder_without_the_path_in_its_command_line():
    popen = _Popen()
    assert _open("win32", _which("wt", "cmd"), popen)
    [(argv, options)] = popen.calls
    assert argv == ["/fixture/bin/wt", "-d", "."]
    assert options["cwd"] == str(FOLDER)
    assert options["shell"] is False
    assert all(str(FOLDER) not in part for part in argv)


def test_windows_without_windows_terminal_opens_a_new_command_prompt_there():
    popen = _Popen()
    assert _open("win32", _which("cmd"), popen)
    [(argv, options)] = popen.calls
    assert argv == ["/fixture/bin/cmd"]
    assert options["cwd"] == str(FOLDER)
    assert options["creationflags"] == getattr(subprocess, "CREATE_NEW_CONSOLE", 0x10)
    assert options["shell"] is False
    # A new console keeps its own input and output.
    assert not {"stdin", "stdout", "stderr"} & set(options)


def test_macos_opens_terminal_at_the_folder():
    popen = _Popen()
    assert _open("darwin", _which("open"), popen)
    [(argv, options)] = popen.calls
    assert argv == ["/fixture/bin/open", "-a", "Terminal", str(FOLDER)]
    assert options["shell"] is False and options["start_new_session"] is True


@pytest.mark.parametrize(("available", "expected"), [
    (("x-terminal-emulator", "xterm"), "x-terminal-emulator"),
    (("konsole", "xterm"), "konsole"),
    (("xterm",), "xterm"),
])
def test_linux_uses_the_first_terminal_app_found(available, expected):
    popen = _Popen()
    assert _open("linux", _which(*available), popen)
    [(argv, options)] = popen.calls
    assert argv == [f"/fixture/bin/{expected}"]
    assert options["cwd"] == str(FOLDER)
    assert options["shell"] is False and options["start_new_session"] is True


def test_no_terminal_app_or_a_failed_start_opens_nothing():
    popen = _Popen()
    assert not _open("linux", _which(), popen)
    assert popen.calls == []
    assert not _open("win32", _which("wt"), _Popen(OSError("fixture")))


def test_the_terminal_gets_no_row_bot_secrets_or_settings():
    popen = _Popen()
    environ = {
        "PATH": "/fixture/bin", "HOME": "/home/person", "SSH_AUTH_SOCK": "/tmp/agent",
        "XAUTHORITY": "/home/person/.Xauthority", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/bus",
        "ROW_BOT_LAUNCH_SECRET": "fixture-launch-secret", "ROW_BOT_DATA_DIR": "/fixture/data",
        "OPENAI_API_KEY": "fixture-openai", "SLACK_BOT_TOKEN": "fixture-slack",
        "SMTP_PASSWORD": "fixture-smtp", "PYTHONNOUSERSITE": "1", "PYTHONIOENCODING": "utf-8",
        "CUSTOM_PROVIDER": "fixture-saved-under-an-unusual-name",
    }
    kept = external_terminal.terminal_environment(environ, saved={"CUSTOM_PROVIDER"})
    assert kept == {
        "PATH": "/fixture/bin", "HOME": "/home/person", "SSH_AUTH_SOCK": "/tmp/agent",
        "XAUTHORITY": "/home/person/.Xauthority", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/bus",
    }
    assert _open("linux", _which("xterm"), popen, environ)
    sent = popen.calls[0][1]["env"]
    assert "ROW_BOT_LAUNCH_SECRET" not in sent and "OPENAI_API_KEY" not in sent
    assert sent["PATH"] == "/fixture/bin"


def test_keys_row_bot_saved_under_any_name_stay_out_of_the_terminal(tmp_path, monkeypatch):
    from row_bot import api_keys

    monkeypatch.setattr(api_keys, "KEYS_PATH", tmp_path / "api_keys.json")
    (tmp_path / "api_keys.json").write_text(
        '{"version": 2, "storage": "keyring", "keys": {"FIXTURE_CUSTOM": {"configured": true}}}',
        encoding="utf-8")
    monkeypatch.setattr(api_keys, "_session_keys", {"FIXTURE_SESSION": "fixture-value"})
    assert external_terminal.terminal_environment(
        {"FIXTURE_CUSTOM": "fixture-saved", "FIXTURE_SESSION": "fixture-value", "PATH": "/fixture/bin"}
    ) == {"PATH": "/fixture/bin"}


def test_open_external_terminal_rechecks_authority_and_says_when_nothing_opened(monkeypatch):
    checks = []
    monkeypatch.setattr(external_terminal, "terminal_folder", lambda conversation: FOLDER)
    with pytest.raises(ClientPlatformError, match="capability_unavailable"):
        external_terminal.open_external_terminal(None, validate=lambda: checks.append("ok"),
                                                 opener=lambda folder: False)
    assert checks == ["ok", "ok"]

    def revoked():
        raise ClientPlatformError("action_denied")

    opened = []
    with pytest.raises(ClientPlatformError, match="action_denied"):
        external_terminal.open_external_terminal(None, validate=revoked, opener=opened.append)
    assert opened == []


def test_a_conversation_without_a_code_folder_opens_at_home(creation):  # noqa: F811
    _, conversation, _ = creation
    assert external_terminal.terminal_folder(None) == Path.home()
    assert external_terminal.terminal_folder(conversation) == Path.home()


def test_a_conversation_opens_at_its_bound_code_folder(creation):  # noqa: F811
    from row_bot.conversation_resources import list_bindings
    from row_bot.developer.storage import get_workspace

    service, conversation, root = creation
    _draft(service, conversation)
    workspace = get_workspace(list_bindings(conversation).bindings[0].resource_id)
    folder = external_terminal.terminal_folder(conversation)
    assert folder == Path(workspace.path)
    assert folder.parent == root / "Drafts"

    shutil.rmtree(folder)
    with pytest.raises(ClientPlatformError, match="resource_unavailable"):
        external_terminal.terminal_folder(conversation)


def test_an_unknown_conversation_is_not_found(creation):  # noqa: F811
    with pytest.raises(ClientPlatformError, match="not_found"):
        external_terminal.terminal_folder("conversation-that-does-not-exist")
