"""Run on this computer (E1): the llama.cpp server's life: started by a request, loopback only, its key
in its environment only, stopped when idle or asked, its whole tree ended, a crash cleaned up, and the
graphics card given back to Learning's training unless the person's chat is being answered.

The server is a Python stand-in (``tests/fixtures/local_fake_llama_server.py``) speaking llama-server's
router API; the build and model are fakes in a scratch data folder.
"""

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

# Imported at collection, before a test moves the data folder: patching its path then restores the real one.
import row_bot.providers.config as provider_config
from row_bot.local_runtime import install, models, server
from tests.fixtures.local_runtime import FakeFiles, fake_build, fake_model, python_command

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]


@pytest.fixture
def installed(tmp_path, monkeypatch):
    """A fake build and one fake model, installed in a scratch data folder."""

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    with FakeFiles() as files:
        build, model = fake_build(files), fake_model(files)
        archive = tmp_path / "archive.zip"
        archive.write_bytes(files.files[f"/{build.assets[0].name}"])
        data = files.files[f"/{model.file}"]
    install.publish_build(build, [archive], tmp_path, command=python_command)
    folder = install.model_dir(model, tmp_path)
    folder.mkdir(parents=True)
    (folder / model.file).write_bytes(data)
    install.publish_model(model, tmp_path)
    monkeypatch.setattr(models, "CATALOG", (model,))
    (tmp_path / "children").mkdir()
    monkeypatch.setenv("HF_TOKEN", "hf_person_secret")  # never reaches the server
    return {"root": tmp_path, "model": model, "record": tmp_path / "record.json", "children": tmp_path / "children"}


def _server(installed, **options) -> server.LocalServer:
    return server.LocalServer(installed["root"], command=python_command, monitor=False, environment={
        "FAKE_LLAMA_RECORD": str(installed["record"]), "FAKE_LLAMA_CHILDREN": str(installed["children"])},
        **options)


def _chat(connection: server.Connection, model_id: str, **extra) -> httpx.Response:
    return httpx.post(f"{connection.base_url}/chat/completions", trust_env=False, timeout=10,
                      headers={"Authorization": f"Bearer {connection.api_key}"},
                      json={"model": model_id, "messages": [{"role": "user", "content": "hi"}], **extra})


def _gone(pid: int) -> bool:
    try:
        process = psutil.Process(pid)
        process.wait(5)
    except (psutil.NoSuchProcess, psutil.TimeoutExpired):
        pass
    return not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE


def test_the_server_starts_on_the_first_request_on_loopback_with_its_key_only_in_its_environment(installed):
    local = _server(installed)
    assert local.connection(start=False) is None and local.state == "stopped"  # nothing runs before a request

    connection = local.connection()
    try:
        assert connection is not None and local.state == "running"
        record = json.loads(installed["record"].read_text(encoding="utf-8"))
        argv = record["argv"]
        assert argv[argv.index("--host") + 1] == "127.0.0.1" and record["address"][0] == "127.0.0.1"
        assert {"--offline", "--no-webui"} <= set(argv)
        assert argv[argv.index("--cors-origins") + 1] == "localhost"
        assert not {"--tools", "--agent", "--mcp-servers-config", "-hf", "--webui-mcp-proxy"} & set(argv)
        assert connection.api_key not in " ".join(argv)  # never on its command line
        assert "LLAMA_API_KEY" in record["env_keys"] and "HF_TOKEN" not in record["env_keys"]
        assert not server.reserved_port(connection.port)
        # Other programs on this computer can't use it without the key.
        refused = httpx.get(f"{connection.base_url}/models", trust_env=False, timeout=5)
        assert refused.status_code == 401
        assert _chat(connection, installed["model"].id).json()["choices"][0]["message"]["content"] == "ready"
    finally:
        local.stop()


@pytest.mark.slow
def test_a_chat_turn_routes_through_the_managed_endpoint_and_starts_the_server(installed, monkeypatch):
    from langchain_core.messages import HumanMessage

    from row_bot import local_runtime
    from row_bot.providers import custom
    from row_bot.providers.transports.openai_compatible import ChatOpenAICompatible

    local = _server(installed)
    monkeypatch.setattr(server, "get", lambda root=None: local)
    server.save_endpoint(installed["root"])
    endpoint = custom.get_custom_endpoint(local_runtime.PROVIDER_ID)
    assert endpoint["managed"] == "local" and endpoint["execution_location"] == "local"
    assert [item["id"] for item in endpoint["models"]] == [installed["model"].id]
    assert local.state == "stopped"  # saving the endpoint starts nothing
    model = ChatOpenAICompatible(model_name=installed["model"].id, base_url=endpoint["base_url"], endpoint=endpoint)
    try:
        answer = model.invoke([HumanMessage(content="hi")])
        assert answer.content == "ready" and local.state == "running"
    finally:
        local.stop()


def test_stop_ends_the_server_and_the_model_it_loaded(installed):
    local = _server(installed)
    connection = local.connection()
    _chat(connection, installed["model"].id)
    pid = local.pid
    child = int((installed["children"] / f"{installed['model'].id}.pid").read_text())

    assert local.stop() is True

    assert local.state == "stopped" and local.pid is None
    assert _gone(pid) and _gone(child)
    assert not local.ledger().is_file()  # its record went with it


def test_an_idle_server_stops_and_a_busy_one_keeps_going(installed):
    now = {"t": 0.0}
    local = _server(installed, clock=lambda: now["t"], idle_seconds=300)
    connection = local.connection()
    try:
        _chat(connection, installed["model"].id)
        httpx.post(f"http://127.0.0.1:{connection.port}/__busy", json={"busy": True}, trust_env=False,
                   headers={"Authorization": f"Bearer {connection.api_key}"})
        now["t"] = 1000.0
        local.check()
        assert local.state == "running"  # a reply is being written: never stopped under it

        httpx.post(f"http://127.0.0.1:{connection.port}/__busy", json={"busy": False}, trust_env=False,
                   headers={"Authorization": f"Bearer {connection.api_key}"})
        now["t"] = 1200.0
        local.check()
        assert local.state == "running"  # idle for 200 s only
        now["t"] = 1300.0
        local.check()
        assert local.state == "stopped"
    finally:
        local.stop()


def test_release_gpu_gives_the_card_back_unless_a_chat_is_being_answered(installed):
    local = _server(installed)
    connection = local.connection()
    try:
        _chat(connection, installed["model"].id)
        headers = {"Authorization": f"Bearer {connection.api_key}"}
        httpx.post(f"http://127.0.0.1:{connection.port}/__busy", json={"busy": True}, headers=headers,
                   trust_env=False)
        assert local.release_gpu() is False and local.state == "running"  # the person comes first

        httpx.post(f"http://127.0.0.1:{connection.port}/__busy", json={"busy": False}, headers=headers,
                   trust_env=False)
        assert local.release_gpu() is True and local.state == "stopped"
        assert local.release_gpu() is True  # nothing holds the card
    finally:
        local.stop()


def test_background_work_doesnt_load_a_model_while_training_holds_the_card(installed):
    holds = {"training": True, "chat": False}
    local = _server(installed, training_holds=lambda: holds["training"], chat_running=lambda: holds["chat"])

    assert local.connection() is None and local.last_failure == "card_busy" and local.pid is None
    holds["chat"] = True  # the person's own chat: it goes ahead (Learning's lease pauses training)
    connection = local.connection()
    try:
        assert connection is not None and local.state == "running"
    finally:
        local.stop()


def test_the_lease_is_read_only_when_learning_loaded_it(monkeypatch):
    monkeypatch.delitem(sys.modules, server.LEASE_MODULE, raising=False)
    assert server._training_holds() is False  # noqa: SLF001 - Learning never loaded: nothing to ask

    class Lease:
        @staticmethod
        def training_holds() -> bool:
            return True

    monkeypatch.setitem(sys.modules, server.LEASE_MODULE, Lease)
    assert server._training_holds() is True  # noqa: SLF001


@pytest.mark.slow
def test_a_crashed_server_is_noticed_and_starts_again_on_the_next_request(installed):
    local = _server(installed)
    connection = local.connection()
    try:
        with pytest.raises(httpx.HTTPError):
            httpx.post(f"http://127.0.0.1:{connection.port}/__crash", trust_env=False, timeout=5)
        local._process.process.wait(10)  # noqa: SLF001 - the stand-in ended itself
        local.check()
        assert local.state == "failed" and local.last_failure == "crashed"

        again = local.connection()
        assert again is not None and local.state == "running"
    finally:
        local.stop()


def test_a_server_a_crashed_row_bot_left_running_is_stopped_on_the_next_start(installed):
    ended = subprocess.Popen([sys.executable, "-c", "pass"])
    ended.wait(10)
    left = subprocess.Popen([sys.executable, "-c", "import time; print('ready', flush=True); time.sleep(600)"],
                            stdout=subprocess.PIPE, text=True)
    try:
        # Running its own code: macOS's framework Python has re-executed itself (python3.12 → Python) by
        # now, so the name recorded is the one the server keeps, as for a real record.
        assert left.stdout.readline().strip() == "ready"
        process = psutil.Process(left.pid)
        local = _server(installed)
        ledger = local.ledger()
        ledger.parent.mkdir(parents=True, exist_ok=True)
        # The record a Row-Bot that has since ended wrote for the server it started.
        ledger.write_text(json.dumps({"agents": [{
            "pid": left.pid, "created": process.create_time(), "name": process.name(),
            "owner_pid": ended.pid, "owner_created": time.time() - 3600, "server": "local_runtime"}]}),
            encoding="utf-8")

        assert local.cleanup_orphans() == 1
        assert left.wait(10) is not None
        assert not ledger.is_file()
    finally:
        if left.poll() is None:
            left.kill()
        left.stdout.close()


def test_nothing_starts_without_an_installed_build_and_model(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    local = server.LocalServer(tmp_path, monitor=False)

    assert local.connection() is None and local.last_failure == "not_installed"
    assert not Path(tmp_path, "runtimes").exists()


def test_the_server_never_takes_one_of_row_bots_own_ports():
    for port in (8080, 8099, 8121, 8130, 8139, 8151, 8159):
        assert server.reserved_port(port)
    assert not server.reserved_port(8140) and not server.reserved_port(9931)
    assert all(not server.reserved_port(server.free_port()) for _ in range(20))
    assert os.environ.get("LLAMA_API_KEY") is None  # the key never leaks into Row-Bot's own environment


def test_ensure_started_starts_the_server_once_and_returns_its_loopback_address(installed, monkeypatch):
    local = _server(installed)
    monkeypatch.setattr(server, "get", lambda root=None: local)
    ref = f"model:custom_openai_llamacpp:{installed['model'].id}"
    try:
        base_url = server.ensure_started(ref)
        assert base_url.startswith("http://127.0.0.1:") and base_url.endswith("/v1")
        assert local.state == "running"
        pid = local.pid
        assert server.ensure_started(ref) == base_url and local.pid == pid  # idempotent: the same server
        connection = server.ensure_connection(ref)
        assert httpx.get(f"{base_url}/models", headers={"Authorization": f"Bearer {connection.api_key}"},
                         trust_env=False, timeout=5).status_code == 200
    finally:
        local.stop()


def test_ensure_started_refuses_a_model_that_isnt_downloaded_here(installed, monkeypatch):
    local = _server(installed)
    monkeypatch.setattr(server, "get", lambda root=None: local)

    for ref in ("model:custom_openai_llamacpp:qwen3.8-27b-q4", "model:ollama:qwen3.8:27b", "qwen"):
        with pytest.raises(server.ServerError) as refused:
            server.ensure_started(ref)
        assert refused.value.code == "unknown_model"
    assert local.pid is None


def test_ensure_started_honours_training_holding_the_card_unless_the_caller_has_it(installed, monkeypatch):
    local = _server(installed, training_holds=lambda: True, chat_running=lambda: False)
    monkeypatch.setattr(server, "get", lambda root=None: local)
    ref = f"model:custom_openai_llamacpp:{installed['model'].id}"

    with pytest.raises(server.ServerError) as busy:
        server.ensure_started(ref)
    assert busy.value.code == "card_busy" and local.pid is None
    try:
        assert server.ensure_started(ref, while_training=True).startswith("http://127.0.0.1:")
    finally:
        local.stop()


def test_the_27b_builds_get_an_8_bit_context_cache_in_the_presets_file(tmp_path):
    text = server.presets_text([("qwen3.8-27b-q2", tmp_path / "a.gguf", 32768, "q8_0"),
                                ("qwen3.5-9b", tmp_path / "b.gguf", 32768, "f16")])

    big, small = text.split("[qwen3.8-27b-q2]")[1], text.split("[qwen3.5-9b]")[1].split("[")[0]
    assert "cache-type-k = q8_0" in big and "cache-type-v = q8_0" in big
    assert "cache-type" not in small and "ctx-size = 32768" in small


def test_each_request_says_the_built_in_model_thinks_and_the_chats_choice_overrides_it(tmp_path, monkeypatch):
    """D3: each request says whether the model thinks (every built-in model does, for now); the chat's own
    choice still decides."""
    from langchain_core.messages import HumanMessage

    from row_bot import local_runtime
    from row_bot.providers.reasoning import ReasoningRequestPlan, ReasoningSelection, resolve_reasoning_capabilities
    from row_bot.providers.runtime import create_chat_model
    from tests.helpers.fake_provider_http import Reply, fake_provider_http

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    built_in = [models.get("qwen3.8-27b-q4"), models.get("qwen3.5-9b"), models.get("qwen3.5-4b")]
    monkeypatch.setattr(install, "installed_models", lambda root=None: built_in)
    answer = Reply(body=json.dumps({"choices": [{"index": 0, "finish_reason": "stop",
                                                 "message": {"role": "assistant", "content": "4"}}]}).encode())

    with fake_provider_http([answer]) as fake:
        monkeypatch.setattr(server, "endpoint_connection", lambda start=True: (f"{fake.url}/v1", "key"))
        server.save_endpoint()

        def thinks(model_id: str, choice: str | None = None) -> bool:
            caps = resolve_reasoning_capabilities(local_runtime.PROVIDER_ID, model_id)
            plan = ReasoningRequestPlan(local_runtime.model_ref(model_id), ReasoningSelection(kind=choice), caps) \
                if choice else None
            create_chat_model(model_id, local_runtime.PROVIDER_ID, reasoning_plan=plan).invoke(
                [HumanMessage(content="What is 2 + 2?")])
            return json.loads(fake.seen[-1].body)["chat_template_kwargs"]["enable_thinking"]

        assert [resolve_reasoning_capabilities(local_runtime.PROVIDER_ID, model.id).default_enabled
                for model in built_in] == [True, True, True]
        assert [thinks(model.id) for model in built_in] == [True, True, True]
        assert [thinks(model.id, "off") for model in built_in] == [False, False, False]
        assert thinks("qwen3.5-9b", "on") is True
