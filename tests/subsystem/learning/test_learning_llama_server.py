"""Row-Bot's own llama.cpp server for personal models (L2x, O75): started on demand on loopback with a key
it makes at each start, nothing of the person's in its environment and none of its own tools; owned
like a plugin worker (its model processes end with it, a crash leaves nothing behind); models unloaded
when idle and the server stopped after; training takes the card back unless a comparison holds it.

The server here is a stand-in (``tests/fixtures/learning_fake_llama_server.py``) that speaks the real
one's router endpoints, so nothing loads a model or needs a graphics card."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import psutil
import pytest

from row_bot.learning.training import llama_server, server_files
from row_bot.learning.training.llama_server import ManagedServer, ModelEntry, ServerError
from tests.fixtures.learning import process_alive, wait_for_file

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

FAKE = Path(__file__).resolve().parents[2] / "fixtures" / "learning_fake_llama_server.py"


def _python() -> str:
    base = getattr(sys, "_base_executable", "") or sys.executable
    return base if Path(base).is_file() else sys.executable


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _installed(folder: Path) -> server_files.ServerFiles:
    return server_files.ServerFiles("installed", "cu130", folder, Path(_python()), 1, "b11669")


@pytest.fixture
def world(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    children = tmp_path / "children"
    children.mkdir()
    record = tmp_path / "record.json"
    monkeypatch.setenv("FAKE_LLAMA_CHILDREN", str(children))
    monkeypatch.setenv("FAKE_LLAMA_RECORD", str(record))
    gguf = tmp_path / "row-bot-personal-v1-Q8_0.gguf"
    gguf.write_bytes(b"GGUF")
    saved: list[tuple[str, int]] = []
    clock = Clock()

    def environment(folder: Path, home: Path) -> dict[str, str]:
        found = server_files.server_environment(folder, home)
        # The stand-in reports to the test through these two (the real server gets neither).
        found.update(FAKE_LLAMA_CHILDREN=str(children), FAKE_LLAMA_RECORD=str(record))
        return found

    server = ManagedServer(root, files=lambda: _installed(tmp_path), command=lambda exe: [str(exe), "-I", str(FAKE)],
                           environment=environment, clock=clock, card_bytes=lambda: None,
                           endpoint=lambda connection, context: saved.append((connection.base_url, context)),
                           server_idle_seconds=60, monitor=False)
    server.register("row-bot-personal-v1", gguf, 32_768)
    yield server, root, children, record, clock, saved
    server.shutdown()


def _until(condition, timeout: float = 5.0) -> bool:
    """Poll ``condition`` until it holds (another process acts; no fixed sleeps)."""

    import threading

    deadline, tick = time.monotonic() + timeout, threading.Event()
    while not condition():
        if time.monotonic() > deadline:
            return False
        tick.wait(0.02)
    return True


def _get(connection, path: str, *, key: str | None = None) -> httpx.Response:
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    return httpx.get(f"http://127.0.0.1:{connection.port}{path}", headers=headers, timeout=5, trust_env=False)


def _post(connection, path: str, body: dict) -> httpx.Response:
    return httpx.post(f"http://127.0.0.1:{connection.port}{path}", json=body, timeout=10, trust_env=False,
                      headers={"Authorization": f"Bearer {connection.api_key}"})


def test_it_starts_on_demand_on_loopback_with_its_own_key_offline_and_without_tools(world, monkeypatch):
    server, root, _children, record, _clock, saved = world
    assert server.connection(start=False) is None  # nothing runs until something asks

    connection = server.connection()

    assert connection is not None and connection.base_url == f"http://127.0.0.1:{connection.port}/v1"
    started = json.loads(record.read_text(encoding="utf-8"))
    argv = started["argv"]
    assert argv[argv.index("--host") + 1] == "127.0.0.1"
    assert {"--offline", "--no-webui", "--models-preset"} <= set(argv)
    assert not {"--tools", "--agent", "--api-key", "--api-key-file", "-hf", "--hf-repo", "--mcp-servers-config",
                "--ui-mcp-proxy"} & set(argv)
    assert connection.api_key not in " ".join(argv)  # the key is in its environment, never on its command line
    assert not any(connection.api_key in path.read_text(encoding="utf-8", errors="ignore")
                   for path in root.rglob("*") if path.is_file())  # nor on disk
    # Only Row-Bot's key opens it (a web page or another program on this computer gets nothing).
    assert _get(connection, "/v1/models").status_code == 401
    assert _get(connection, "/v1/models", key="guess").status_code == 401
    listed = _get(connection, "/v1/models", key=connection.api_key).json()
    assert [item["id"] for item in listed["data"]] == ["row-bot-personal-v1"]
    # Row-Bot's endpoint for it now points at it.
    assert saved[-1] == (connection.base_url, 32_768)
    # A request finds it running; a second start gets a new key.
    assert server.connection() == connection
    server.stop("test")
    again = server.connection()
    assert again is not None and again.api_key != connection.api_key


def test_its_environment_carries_nothing_of_the_persons(tmp_path, monkeypatch):
    for name, value in {"OPENAI_API_KEY": "sk-secret", "HF_TOKEN": "hf_secret", "LLAMA_ARG_TOOLS": "all",
                        "LLAMA_ARG_AGENT": "1", "LLAMA_API_KEY": "someone-elses", "ROW_BOT_DATA_DIR": "x"}.items():
        monkeypatch.setenv(name, value)

    found = server_files.server_environment(tmp_path / "bin", tmp_path / "home")

    assert not {"OPENAI_API_KEY", "HF_TOKEN", "LLAMA_ARG_TOOLS", "LLAMA_ARG_AGENT", "LLAMA_API_KEY",
                "ROW_BOT_DATA_DIR"} & set(found)
    assert found["LLAMA_CACHE"] == str(tmp_path / "home" / "cache")  # an empty cache: only Row-Bot's models
    assert found["HOME"] == str(tmp_path / "home")


def test_stop_ends_the_server_and_every_model_process_it_started(world):
    server, root, children, _record, _clock, _saved = world
    connection = server.connection()
    assert _post(connection, "/models/load", {"model": "row-bot-personal-v1"}).status_code == 200
    child = int(wait_for_file(children / "row-bot-personal-v1.pid"))
    pid = server.pid
    assert process_alive(child) and process_alive(pid)
    recorded = json.loads(llama_server.ledger_path(root).read_text(encoding="utf-8"))["agents"]
    # Recorded while it runs, under the name it runs as (a later Row-Bot acts only on a record that matches).
    assert [entry["name"] for entry in recorded] == [psutil.Process(pid).name()]

    assert server.stop("test")

    assert _until(lambda: not process_alive(child) and not process_alive(pid))
    assert not llama_server.ledger_path(root).is_file()  # its record goes with it
    assert server.connection(start=False) is None


def test_quitting_row_bot_stops_it(world):
    server, _root, _children, _record, _clock, _saved = world
    server.connection()
    pid = server.pid

    server.shutdown()

    assert not process_alive(pid)
    assert server.connection() is None  # nothing starts while Row-Bot quits


def test_an_idle_model_is_unloaded_and_then_the_server_stops(world):
    server, _root, children, _record, clock, _saved = world
    connection = server.connection()
    _post(connection, "/v1/chat/completions", {"model": "row-bot-personal-v1", "messages": []})
    child = int(wait_for_file(children / "row-bot-personal-v1.pid"))
    server.check()
    assert server.model_status("row-bot-personal-v1") == "loaded" and server.state == "running"

    _post(connection, "/__sleep", {"model": "row-bot-personal-v1"})  # unused for --sleep-idle-seconds
    server.check()  # sleeping: its memory goes back now
    assert server.model_status("row-bot-personal-v1") == "unloaded" and not process_alive(child)

    clock.now += 59
    server.check()
    assert server.state == "running"
    clock.now += 2
    server.check()  # no model loaded for a minute: the server itself goes
    assert server.state == "stopped" and server.connection(start=False) is None


def test_training_takes_the_card_back_unless_a_comparison_holds_the_server(world):
    server, _root, _children, _record, clock, _saved = world

    with server.hold() as connection:
        assert not server.release_card()
        clock.now += 3600
        server.check()  # held: never idle
        assert server.connection(start=False) == connection
    assert server.release_card()
    assert server.state == "stopped"
    assert not server.release_card()  # nothing running: nothing to give back


def test_a_crash_is_noticed_and_the_next_request_starts_it_again(world):
    server, _root, _children, _record, _clock, _saved = world
    connection = server.connection()
    pid = server.pid
    with pytest.raises(httpx.HTTPError):
        _post(connection, "/__crash", {})
    assert _until(lambda: not process_alive(pid))

    server.check()

    assert server.state == "failed" and server.last_failure == "crashed"
    again = server.connection()
    assert again is not None and server.state == "running" and server.pid != pid


def test_a_server_a_crashed_row_bot_left_running_is_stopped_before_the_next_starts(tmp_path, monkeypatch):
    from row_bot import owned_processes

    root = tmp_path / "data"
    (root / "runtime").mkdir(parents=True)
    gone = subprocess.Popen([_python(), "-c", "pass"])
    gone_created = owned_processes.started(gone.pid)
    gone.wait()
    running = tmp_path / "running.json"
    left = subprocess.Popen([_python(), "-I", str(FAKE), "--host", "127.0.0.1", "--port", "0"],
                            env={**os.environ, "LLAMA_API_KEY": "x", "FAKE_LLAMA_RECORD": str(running)})
    try:
        # Its own code runs, so it has replaced itself (a macOS framework Python starts as python3.x and
        # becomes Python): the name recorded is the one it keeps, as Row-Bot records it once it answers.
        wait_for_file(running)
        ledger = llama_server.ledger_path(root)
        ledger.write_text(json.dumps({"agents": [{
            "pid": left.pid, "created": owned_processes.started(left.pid), "name": psutil.Process(left.pid).name(),
            "owner_pid": gone.pid, "owner_created": gone_created or 1.0, "server": "learning"}]}), encoding="utf-8")

        assert llama_server.cleanup_orphans(root) == 1

        left.wait(5)
        assert not ledger.is_file()
    finally:
        if left.poll() is None:
            left.kill()


def test_nothing_starts_where_the_server_isnt_installed(tmp_path):
    missing = ManagedServer(tmp_path, files=lambda: server_files.ServerFiles("not_installed"), monitor=False)
    elsewhere = ManagedServer(tmp_path, files=lambda: server_files.ServerFiles("unavailable"), monitor=False)

    assert missing.connection() is None and missing.last_failure == "not_installed"
    assert elsewhere.connection() is None and elsewhere.last_failure == "unavailable"
    with pytest.raises(ServerError):
        missing.ensure()


def test_its_model_list_is_the_versions_files_and_can_be_changed_while_it_runs(world, tmp_path):
    server, _root, _children, _record, _clock, saved = world
    connection = server.connection()
    base = tmp_path / "row-bot-personal-base-Q8_0.gguf"
    base.write_bytes(b"GGUF")

    server.register("row-bot-personal-base", base, 32_768)
    listed = [item["id"] for item in _get(connection, "/v1/models", key=connection.api_key).json()["data"]]
    server.unregister("row-bot-personal-v1")
    after = [item["id"] for item in _get(connection, "/v1/models", key=connection.api_key).json()["data"]]

    assert listed == ["row-bot-personal-base", "row-bot-personal-v1"] and after == ["row-bot-personal-base"]
    assert [entry.name for entry in server.models()] == ["row-bot-personal-base"]


def test_a_model_name_or_file_cant_write_into_the_presets_file():
    with pytest.raises(ServerError):
        llama_server.presets_text([ModelEntry("v1]\n[evil", "x.gguf", 32_768)])
    with pytest.raises(ServerError):
        llama_server.presets_text([ModelEntry("v1", "x.gguf\ntools = all", 32_768)])
    text = llama_server.presets_text([ModelEntry("row-bot-personal-v1", "C:/v1/v1.gguf", 32_768)])
    assert "reasoning = off" in text and "ctx-size = 32768" in text and "tools" not in text
    assert "cache-ram = 2048" in text  # not llama.cpp's 8 GB of the computer's memory per model


def test_two_models_stay_loaded_only_where_the_card_holds_both():
    two_billion, twenty_seven_billion = 2_012_012_128, 16_500_000_000  # Q8_0 2B, Q4_K_M 27B

    assert llama_server.models_max([two_billion, two_billion], 32 * 1024**3) == 2
    assert llama_server.models_max([two_billion], 8 * 1024**3) == 1  # an 8 GB card: one at a time
    assert llama_server.models_max([two_billion], None) == 1  # unknown card: one at a time
    assert llama_server.models_max([twenty_seven_billion], 32 * 1024**3) == 1
