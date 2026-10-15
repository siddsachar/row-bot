"""Saved API keys and Row-Bot's own secrets never reach a program the agent runs: they are read in-process,
not put in Row-Bot's environment, and the keys Row-Bot knows by name are taken out of a command's
environment even when a self-hosted install set them there."""

from __future__ import annotations

import json
import os
import sys

import pytest

pytestmark = pytest.mark.platform

SECRETS = {
    "OPENAI_API_KEY": "sk-external-synthetic",          # a self-hosted install's provider key
    "SLACK_BOT_TOKEN": "xoxb-external-synthetic",       # a channel token
    "ROW_BOT_LAUNCH_SECRET": "launch-secret-synthetic",  # Row-Bot's own
    "SAVED_SERVICE_TOKEN": "saved-synthetic",            # a key the person saved under their own name
}


class _Keyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, account: str) -> str | None:
        return self.values.get((service, account))

    def set_password(self, service: str, account: str, value: str) -> None:
        self.values[(service, account)] = value

    def delete_password(self, service: str, account: str) -> None:
        self.values.pop((service, account), None)


@pytest.fixture
def api_keys(tmp_path, monkeypatch):
    from row_bot import api_keys, secret_store

    monkeypatch.setattr(secret_store, "_backend_override", _Keyring())
    monkeypatch.setattr(api_keys, "KEYS_PATH", tmp_path / "api_keys.json")
    monkeypatch.setattr(api_keys, "_session_keys", {})
    for name in ("TAVILY_API_KEY", *SECRETS):
        monkeypatch.delenv(name, raising=False)
    return api_keys


@pytest.fixture
def environment(api_keys, monkeypatch):
    """Row-Bot's environment holding every kind of secret, and one ordinary variable."""
    api_keys.set_key("SAVED_SERVICE_TOKEN", SECRETS["SAVED_SERVICE_TOKEN"])
    for name, value in SECRETS.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("WORKSPACE_FLAVOUR", "kept")
    return api_keys


def test_a_saved_key_is_read_in_process_and_never_put_in_the_environment(api_keys):
    api_keys.set_key("TAVILY_API_KEY", "tvly-saved-synthetic")
    api_keys.apply_keys()

    assert "TAVILY_API_KEY" not in os.environ
    assert api_keys.get_key("TAVILY_API_KEY") == "tvly-saved-synthetic"

    api_keys.delete_key("TAVILY_API_KEY")
    assert api_keys.get_key("TAVILY_API_KEY") == ""


def test_a_saved_key_still_comes_before_the_environments(api_keys, monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-environment-synthetic")
    assert api_keys.get_key("TAVILY_API_KEY") == "tvly-environment-synthetic"

    api_keys.set_key("TAVILY_API_KEY", "tvly-saved-synthetic")

    assert api_keys.get_key("TAVILY_API_KEY") == "tvly-saved-synthetic"


def test_shell_commands_run_without_keys_or_row_bots_secrets(environment, monkeypatch, tmp_path):
    """Through the process manager, and through the plain subprocess it falls back to."""
    from row_bot.developer import runtime
    from row_bot.process_cancellation import ProcessRunResult
    from row_bot.tools import shell_tool

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    seen: list[dict[str, str]] = []

    def start(*_args, env=None, **_kwargs):
        seen.append(dict(env))
        raise OSError("synthetic: no worker here")

    def run(args, *, env=None, **_kwargs):
        seen.append(dict(env))
        return ProcessRunResult(args=args, returncode=0, stdout="hi\n")

    monkeypatch.setattr(runtime.subprocess, "Popen", start)
    monkeypatch.setattr(shell_tool, "run_cancellable_subprocess", run)

    result = shell_tool.ShellSession(str(tmp_path)).run_command("echo hi")

    assert result["exit_code"] == 0
    assert len(seen) == 2
    for environment_seen in seen:
        assert not set(SECRETS) & set(environment_seen)
        assert environment_seen["WORKSPACE_FLAVOUR"] == "kept"


def test_a_background_process_gets_its_extra_variables_and_still_no_secrets(environment, monkeypatch, tmp_path):
    from row_bot import background_processes
    from row_bot.developer import runtime

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    seen: list[dict[str, str]] = []

    def start(*_args, env=None, **_kwargs):
        seen.append(dict(env))
        raise OSError("synthetic: nothing starts")

    monkeypatch.setattr(runtime.subprocess, "Popen", start)

    with pytest.raises(background_processes.ProcessStartError):
        background_processes.start("conversation-1", ["server"], command="server", folder=str(tmp_path),
                                   env={"PORT": "3000"})

    assert seen[0]["PORT"] == "3000"
    assert not set(SECRETS) & set(seen[0])


def test_the_child_process_gets_exactly_that_environment(environment, tmp_path):
    from row_bot.api_keys import child_environment
    from row_bot.process_cancellation import run_cancellable_subprocess

    result = run_cancellable_subprocess(
        [sys.executable, "-I", "-c", "import json, os; print(json.dumps(sorted(os.environ)))"],
        cwd=str(tmp_path), timeout=60, env=child_environment())

    names = {name.upper() for name in json.loads(result.stdout)}
    assert result.returncode == 0
    assert not set(SECRETS) & names
    assert "WORKSPACE_FLAVOUR" in names


def test_an_explicit_opt_in_passes_a_named_key_on(environment):
    from row_bot.api_keys import child_environment

    kept = child_environment(keep=["OPENAI_API_KEY"])

    assert kept["OPENAI_API_KEY"] == SECRETS["OPENAI_API_KEY"]
    assert "ROW_BOT_LAUNCH_SECRET" not in kept and "SLACK_BOT_TOKEN" not in kept


def test_web_search_uses_the_saved_key_without_the_environment(api_keys, monkeypatch):
    import types

    from row_bot.tools.web_search_tool import WebSearchTool

    sent: list[str] = []

    class Tavily:
        def __init__(self, api_key):
            sent.append(api_key)

        def search(self, *_args, **_kwargs):
            return {"results": []}

    monkeypatch.setitem(sys.modules, "tavily", types.SimpleNamespace(TavilyClient=Tavily))
    api_keys.set_key("TAVILY_API_KEY", "tvly-saved-synthetic")

    WebSearchTool().execute("rent")

    assert sent == ["tvly-saved-synthetic"]


def test_wolfram_alpha_is_offered_with_its_saved_app_id(api_keys, monkeypatch):
    from row_bot.tools.wolfram_tool import WolframAlphaTool

    monkeypatch.delenv("WOLFRAM_ALPHA_APPID", raising=False)
    assert WolframAlphaTool().as_langchain_tools() == []

    api_keys.set_key("WOLFRAM_ALPHA_APPID", "saved-synthetic")

    assert WolframAlphaTool().as_langchain_tools()


def test_x_sees_its_saved_client_credentials(api_keys, monkeypatch):
    from row_bot.tools.x_tool import XTool

    for name in ("X_CLIENT_ID", "X_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    assert XTool().has_credentials() is False

    api_keys.set_key("X_CLIENT_ID", "client-synthetic")
    api_keys.set_key("X_CLIENT_SECRET", "secret-synthetic")

    assert XTool().has_credentials() is True
