"""The host's secret-store key at ``row-bot serve``'s start: only Row-Bot's server holds it (H2).

The launcher takes it out of its environment before it starts anything and hands it to its server
alone; the server takes it out of its own environment first thing. No program either of them starts
inherits it, and a malformed key stops the start without ever being echoed. Security-sensitive:
the first two stay in the pull-request lane although they start processes; the real server's own
start, which loads the whole app, runs nightly.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import row_bot.launcher as launcher
from row_bot import environment_secrets, secret_store
from row_bot.brand import LAUNCHER_APP_LOG_FILENAME

pytestmark = pytest.mark.platform

KEY_NAME = secret_store.PERSISTENT_SERVER_SECRET_KEY_NAME
HOST_KEY = "3c" * 32  # stands for the value a host such as PikaPods generates per pod
REPO_ROOT = Path(__file__).resolve().parents[3]
PROBE = [sys.executable, "-c", f"import os; print(os.environ.get({KEY_NAME!r}, 'absent'))"]
# A stand-in for row_bot/app.py, started the way the launcher starts it: it does what the real
# server does first, then starts a program the plain way (a shell, MCP server, browser, git, pip).
SERVER = f"""
import json, os, subprocess, sys
handed = {KEY_NAME!r} in os.environ
from row_bot import secret_store
secret_store.take_environment_key()
program = subprocess.run({PROBE!r}, capture_output=True, text=True, check=True).stdout.strip()
print(json.dumps({{
    "handed": handed,
    "left_in_environment": {KEY_NAME!r} in os.environ,
    "program": program,
    "saved": secret_store.get_secret("refresh_token", namespace="providers:codex"),
}}))
"""

# A server that has taken its key and waits, so another program can look at it.
HOLDER = """
import sys
from row_bot import secret_store
secret_store.take_environment_key()
print("ready", flush=True)
sys.stdin.readline()
"""

# The real server, row_bot/app.py, up to where it would listen: uvicorn.run reports instead.
REAL_SERVER = textwrap.dedent(f"""
    import json, os, runpy, subprocess, sys
    import uvicorn
    def report(app, **kwargs):
        from row_bot import secret_store
        program = subprocess.run({PROBE!r}, capture_output=True, text=True, check=True).stdout.strip()
        print(json.dumps({{"left_in_environment": {KEY_NAME!r} in os.environ, "program": program,
                           "holds_key": secret_store.persistent_server_store_configured()}}))
        raise SystemExit(0)
    uvicorn.run = report
    runpy.run_path("app.py", run_name="__main__")
""")


@pytest.fixture
def serve(tmp_path, monkeypatch, reload_for_data_dir):
    """`row-bot serve` in the published image: a container on a fresh volume, the host's key set."""
    data = tmp_path / "data"
    run_secrets = tmp_path / "run-secrets"
    run_secrets.mkdir()
    monkeypatch.setenv("ROW_BOT_DEPLOYMENT_MODE", "server")
    monkeypatch.setenv("ROW_BOT_CONTAINERIZED", "1")
    monkeypatch.setenv("ROW_BOT_SECRETS_DIR", str(run_secrets))
    monkeypatch.setenv(KEY_NAME, HOST_KEY)
    reload_for_data_dir(data, "row_bot.secret_store")
    return data


def test_only_the_server_gets_the_key_and_no_program_inherits_it(serve, tmp_path, monkeypatch, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    # `row-bot serve`'s step, before it starts anything.
    secret_store.prepare_server_store_at_start()
    secret_store.set_secret("refresh_token", "fake-saved-token", namespace="providers:codex")
    # A program the launcher starts itself, such as Ollama.
    from_launcher = subprocess.run(PROBE, capture_output=True, text=True, check=True, timeout=60)
    app = tmp_path / "app"
    app.mkdir()
    (app / "app.py").write_bytes(SERVER.encode("utf-8"))
    monkeypatch.setattr(launcher, "app_root", lambda: app)

    server = launcher._RowBotProcess(port=8151)
    server.start()
    try:
        assert server._proc.wait(timeout=60) == 0
    finally:
        server._close_log_handle()
    log = (serve / LAUNCHER_APP_LOG_FILENAME).read_text(encoding="utf-8")
    report = json.loads(log.strip().splitlines()[-1])

    assert KEY_NAME not in os.environ
    assert from_launcher.stdout.strip() == "absent"
    assert report == {
        "handed": True,
        "left_in_environment": False,
        "program": "absent",
        "saved": "fake-saved-token",  # the server's own copy of the key opens what was saved
    }
    at_rest = b"".join(path.read_bytes() for path in serve.rglob("*") if path.is_file())
    assert HOST_KEY.encode() not in at_rest
    assert HOST_KEY not in caplog.text and HOST_KEY not in log


def test_serve_refuses_a_malformed_key_before_starting_anything_and_never_echoes_it(
    serve, monkeypatch, capsys, caplog
) -> None:
    caplog.set_level(logging.DEBUG)
    malformed = "not-a-key-" + "7f" * 27
    monkeypatch.setenv(KEY_NAME, malformed)
    monkeypatch.setattr(launcher, "_LAUNCH_FILE_LOGGING", True)
    monkeypatch.setattr(launcher, "_claim_instance", lambda: True)
    started: list[object] = []
    monkeypatch.setattr(launcher, "_run_direct", started.append)
    private: list[bool] = []
    monkeypatch.setattr(environment_secrets, "keep_environment_private", lambda: private.append(True))

    with pytest.raises(SystemExit) as stopped:
        launcher.main(["--server", "--no-open", "--no-splash", "--no-ollama", "--port", "8151"])

    message = str(stopped.value.code)
    assert message == (
        f"Row-Bot didn't start: {KEY_NAME} must contain exactly 64 hexadecimal characters"
    )
    assert started == []
    assert private == [True]  # its starting environment, which still holds the key, is root's alone
    assert KEY_NAME not in os.environ
    printed = capsys.readouterr()
    on_disk = "".join(
        path.read_text(encoding="utf-8", errors="replace") for path in serve.rglob("*") if path.is_file()
    )
    for text in (message, printed.out, printed.err, caplog.text, on_disk):
        assert malformed not in text


@pytest.mark.skipif(
    not sys.platform.startswith("linux") or os.geteuid() == 0,
    reason="/proc and its same-user rule are Linux's; root reads every process",
)
def test_a_program_of_the_same_user_cannot_read_the_key_from_the_servers_starting_environment(
    tmp_path,
) -> None:
    # The server holds the key and waits; a shell command the agent runs reads /proc as the same user.
    holder = subprocess.Popen(
        [sys.executable, "-c", HOLDER],
        env={**os.environ, "ROW_BOT_DEPLOYMENT_MODE": "server", "ROW_BOT_DATA_DIR": str(tmp_path), KEY_NAME: HOST_KEY},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "ready"
        with pytest.raises(PermissionError):
            Path(f"/proc/{holder.pid}/environ").read_bytes()
    finally:
        holder.stdin.close()
        holder.wait(timeout=60)


@pytest.mark.slow
def test_the_real_server_takes_the_key_out_before_it_listens(serve) -> None:
    result = subprocess.run(
        [sys.executable, "-c", REAL_SERVER], cwd=REPO_ROOT, env=dict(os.environ), capture_output=True,
        text=True, timeout=300,
    )

    assert result.returncode == 0, result.stderr[-4000:]
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report == {"left_in_environment": False, "program": "absent", "holds_key": True}
