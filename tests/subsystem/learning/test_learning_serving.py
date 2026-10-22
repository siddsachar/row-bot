"""Serving a version (L2c, D10, O26, O30, O33): Ollama is automated (its own Qwen3.5 renderer and
parser, the served context and thinking off saved as the model's own settings, the catalog refreshed
from Ollama's answer), the other runtimes wait for the person with one command; a version is ready
only after Row-Bot's probe and one real Agent turn with a tool, and nothing half-working is left
in Ollama."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from row_bot.learning.training import serving

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]  # processes, paths and the model settings file


class FakeOllama:
    def __init__(self, *, version: str | None = "0.40.4", executable: str | None = "ollama", create_ok: bool = True,
                 base_url: str = "http://127.0.0.1:11434") -> None:
        self._version = version
        self._executable = executable
        self.create_ok = create_ok
        self.base_url = base_url
        self.created: list[tuple[str, str]] = []
        self.deleted: list[str] = []
        self.unloaded: list[str] = []

    def version(self):
        return self._version

    def executable(self):
        return self._executable

    def create(self, name, modelfile):
        self.created.append((name, Path(modelfile).read_text(encoding="utf-8")))
        return self.create_ok

    def unload(self, name):
        self.unloaded.append(name)
        return True

    def delete(self, name):
        self.deleted.append(name)
        return True


class FakeVerifier:
    def __init__(self, *, ready: bool = True, turn_ok: bool = True) -> None:
        self.ready = ready
        self.turn_ok = turn_ok
        self.calls: list[tuple] = []

    def probe(self, model_ref, *, runtime, endpoint=None, model_id=None):
        self.calls.append(("probe", model_ref, runtime, endpoint, model_id))
        return {"agent_ready": self.ready, "context": 32768, "errors": [] if self.ready else ["no tools"]}

    def tool_turn(self, model_ref):
        self.calls.append(("tool_turn", model_ref))
        return {"ok": self.turn_ok, "tool_calls": 1 if self.turn_ok else 0, "answered": self.turn_ok}


@pytest.fixture
def model_settings(tmp_path, monkeypatch):
    """Row-Bot's model settings file, isolated (the per-model settings are saved there)."""

    from row_bot import models

    monkeypatch.setattr(models, "_SETTINGS_PATH", tmp_path / "model_settings.json")
    monkeypatch.setattr(models, "_local_model_settings", {})
    return models


@pytest.fixture
def version_files(tmp_path):
    folder = tmp_path / "versions" / "v1"
    folder.mkdir(parents=True)
    gguf = folder / "row-bot-personal-v1-Q8_0.gguf"
    gguf.write_bytes(b"GGUF")
    return folder, gguf


def _serve_ollama(version_files, data_dir, *, ollama, verifier, catalog=None, saved=None):
    folder, gguf = version_files
    refreshed = catalog if catalog is not None else []
    return serving.serve("ollama", version=1, gguf=gguf, serve_context=32768, folder=folder, ollama=ollama,
                         verifier=verifier, catalog=lambda: refreshed.append(1) or 3,
                         save_model_settings=(lambda name, **values: saved.append((name, values)))
                         if saved is not None else None, root=data_dir)


def test_ollama_registers_the_gguf_with_its_renderer_and_context_and_is_ready_after_both_checks(
        version_files, data_dir, model_settings):
    ollama, verifier, refreshed, saved = FakeOllama(), FakeVerifier(), [], []

    outcome = _serve_ollama(version_files, data_dir, ollama=ollama, verifier=verifier, catalog=refreshed, saved=saved)

    tag = serving.ollama_tag(1, data_dir)
    assert outcome.state == "ready" and outcome.model_ref == f"model:ollama:{tag}"
    assert outcome.runtime_version == "0.40.4" and tag.startswith("row-bot-personal-v1:")
    [(name, modelfile)] = ollama.created
    folder, gguf = version_files
    assert name == tag and modelfile == (f"FROM {gguf.resolve()}\nRENDERER qwen3.5\nPARSER qwen3.5\n"
                                         "PARAMETER num_ctx 32768\n")
    assert (folder / "Modelfile").read_text(encoding="utf-8") == modelfile
    assert saved == [(tag, {"context": 32768, "reasoning": "off"})]  # O30, O24
    assert refreshed == [1]  # capabilities come from Ollama's own /api/show (O33)
    assert [call[0] for call in verifier.calls] == ["probe", "tool_turn"]
    assert ollama.deleted == [] and ollama.unloaded == [tag]  # no graphics memory held after the checks


def test_a_named_model_is_served_under_its_own_name_and_never_replaces_the_version(
        version_files, data_dir, model_settings):
    # The untouched starting model a first version is compared with (L2c2): its own Ollama model,
    # never written over the version's (the 5070 e2e caught that once).
    folder, gguf = version_files
    ollama, verifier, saved = FakeOllama(), FakeVerifier(), []

    outcome = serving.serve("ollama", version=1, gguf=gguf, serve_context=32768, folder=folder, ollama=ollama,
                            verifier=verifier, catalog=lambda: 3, root=data_dir, tag="row-bot-personal-base:abcd1234",
                            tool_turn=False, save_model_settings=lambda name, **values: saved.append(name))

    assert outcome.state == "ready" and outcome.model_ref == "model:ollama:row-bot-personal-base:abcd1234"
    assert [name for name, _ in ollama.created] == ["row-bot-personal-base:abcd1234"]
    assert serving.ollama_tag(1, data_dir) not in {name for name, _ in ollama.created}
    assert saved == ["row-bot-personal-base:abcd1234"] and ollama.unloaded == ["row-bot-personal-base:abcd1234"]
    assert [call[0] for call in verifier.calls] == ["probe"]  # it never learned the tool bridge


@pytest.mark.parametrize("ready, turn_ok, reason", [(False, True, "probe_failed"), (True, False, "tool_turn_failed")])
def test_a_version_that_fails_a_check_is_never_ready_and_is_taken_back_out_of_ollama(
        version_files, data_dir, model_settings, ready, turn_ok, reason):
    ollama, verifier, refreshed = FakeOllama(), FakeVerifier(ready=ready, turn_ok=turn_ok), []

    outcome = _serve_ollama(version_files, data_dir, ollama=ollama, verifier=verifier, catalog=refreshed)

    tag = serving.ollama_tag(1, data_dir)
    assert (outcome.state, outcome.reason) == ("failed", reason)
    assert ollama.deleted == [tag] and refreshed == [1, 1] and tag in ollama.unloaded
    assert model_settings.local_model_settings(tag) == {}  # its saved settings go with it
    assert ("tool_turn", outcome.model_ref) in verifier.calls if ready else len(verifier.calls) == 1
    assert version_files[1].is_file()  # the version's file stays: Try again needs no new export


@pytest.mark.parametrize("ollama, reason", [
    (FakeOllama(version=None), "not_running"),
    (FakeOllama(version=None, executable=None), "not_installed"),
    (FakeOllama(base_url="http://192.168.1.20:11434"), "not_local"),
    (FakeOllama(create_ok=False), "create_failed"),
])
def test_ollama_problems_are_said_and_nothing_is_checked(version_files, data_dir, model_settings, ollama, reason):
    verifier = FakeVerifier()

    outcome = _serve_ollama(version_files, data_dir, ollama=ollama, verifier=verifier)

    assert (outcome.state, outcome.reason) == ("failed", reason) and verifier.calls == []
    if reason == "not_local":
        assert ollama.created == []  # the files never leave this computer


def test_a_runtime_the_person_starts_waits_for_them_with_one_command_then_is_checked(version_files, data_dir):
    folder, gguf = version_files
    listed: list = [None]
    registered: list = []
    verifier = FakeVerifier()

    def serve():
        return serving.serve("llama_cpp", version=1, gguf=gguf, serve_context=32768, folder=folder,
                             verifier=verifier, list_models=lambda url: listed[0],
                             register=lambda runtime, *, serve_context: registered.append((runtime, serve_context))
                             or "custom_openai_row-bot-personal-llama-cpp", root=data_dir)

    waiting = serve()
    assert (waiting.state, waiting.reason) == ("waiting_for_you", "not_running")
    assert waiting.command == (f'llama-server -m "{gguf.resolve()}" -a row-bot-personal-v1 --host 127.0.0.1 '
                               "--port 8181 -c 32768 -ngl 99 --jinja --offline --no-webui --reasoning off")
    listed[0] = ["some-other-model"]
    assert serve().reason == "not_loaded" and registered == [] and verifier.calls == []

    listed[0] = ["row-bot-personal-v1"]
    ready = serve()

    assert ready.state == "ready" and registered == [("llama_cpp", 32768)]
    assert ready.model_ref == "model:custom_openai_row-bot-personal-llama-cpp:row-bot-personal-v1"
    assert verifier.calls[0] == ("probe", ready.model_ref, "llama_cpp", "custom_openai_row-bot-personal-llama-cpp",
                                 "row-bot-personal-v1")


def test_each_runtimes_command_and_which_this_computer_offers(tmp_path, monkeypatch):
    gguf = tmp_path / "v1.gguf"
    lms = serving.command_for("lmstudio", gguf=gguf, version=2, serve_context=32768)
    vllm = serving.command_for("vllm", gguf=gguf, version=2, serve_context=32768, base_model=tmp_path / "base",
                               adapter=tmp_path / "adapter")

    assert lms == (f'lms import "{gguf.resolve()}" && lms load row-bot-personal-v2 --identifier row-bot-personal-v2 '
                   "--context-length 32768")
    assert "--enable-lora" in vllm and f"row-bot-personal-v2=\"{(tmp_path / 'adapter').resolve()}\"" in vllm
    assert "--host 127.0.0.1" in vllm
    monkeypatch.setattr(sys, "platform", "win32")
    assert serving.available_runtimes() == ("ollama", "lmstudio", "llama_cpp")
    monkeypatch.setattr(sys, "platform", "linux")
    assert serving.available_runtimes() == ("ollama", "lmstudio", "llama_cpp", "vllm")


def test_two_data_folders_never_share_an_ollama_model(tmp_path):
    one, two = tmp_path / "a", tmp_path / "b"

    assert serving.ollama_tag(3, one) != serving.ollama_tag(3, two)
    assert serving.ollama_tag(3, one) == serving.ollama_tag(3, one)
    assert serving.ollama_tag(3, one).split(":")[0] == "row-bot-personal-v3"


def test_only_a_server_on_this_computer_is_asked_for_its_models():
    assert serving.served_models("http://192.168.1.20:1234/v1") is None
    assert serving.served_models("https://example.com/v1") is None


def test_ollama_create_gets_no_secrets_from_row_bots_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-for-ollama")
    monkeypatch.setenv("OLLAMA_MODELS", str(tmp_path / "models"))
    seen = {}

    def runner(argv, *, cwd, env, timeout, check):
        seen.update(argv=argv, env=env, cwd=cwd)
        return SimpleNamespace(code=0)

    modelfile = tmp_path / "Modelfile"
    modelfile.write_text("FROM x\n", encoding="utf-8")
    runtime = serving.OllamaRuntime("http://127.0.0.1:11434", executable="ollama", runner=runner)

    assert runtime.create("row-bot-personal-v1:abcd1234", modelfile) is True
    assert seen["argv"] == ["ollama", "create", "row-bot-personal-v1:abcd1234", "-f", str(modelfile)]
    assert "OPENAI_API_KEY" not in seen["env"] and seen["env"]["OLLAMA_HOST"] == "http://127.0.0.1:11434"
    assert seen["env"]["OLLAMA_MODELS"] == str(tmp_path / "models")  # the person's own Ollama store


def test_a_saved_context_and_thinking_off_are_how_row_bot_serves_that_model(model_settings):
    models = model_settings

    models.set_local_model_settings("row-bot-personal-v1:abcd1234", context=32768, reasoning="off")

    assert models.local_model_settings("row-bot-personal-v1:abcd1234") == {"context": 32768, "reasoning": "off"}
    assert models.local_model_settings("qwen3:8b") == {}
    assert models._local_num_ctx_for("row-bot-personal-v1:abcd1234") <= 32768  # noqa: SLF001 - O30
    import json

    saved = json.loads(models._SETTINGS_PATH.read_text(encoding="utf-8"))  # noqa: SLF001
    assert saved["local_model_settings"] == {"row-bot-personal-v1:abcd1234": {"context": 32768, "reasoning": "off"}}
    with pytest.raises(ValueError):
        models.set_local_model_settings("qwen3:8b", reasoning="high")
    models.clear_local_model_settings("row-bot-personal-v1:abcd1234")
    assert models.local_model_settings("row-bot-personal-v1:abcd1234") == {}


def test_the_check_turn_passes_only_with_a_tool_call_and_its_result_and_leaves_no_chat(monkeypatch):
    from row_bot import agent, thread_cleanup, threads

    removed: list[str] = []
    monkeypatch.setattr(thread_cleanup, "delete_thread", lambda thread_id: removed.append(thread_id))
    monkeypatch.setattr(threads, "_purge_thread_rows", lambda thread_id: removed.append(thread_id))
    script: list = []
    seen: dict = {}

    def stream(prompt, tools, config, stop_event=None):
        seen.update(prompt=prompt, tools=tools, config=config["configurable"])
        yield from script

    monkeypatch.setattr(agent, "stream_agent", stream)
    script[:] = [("tool_call", {"name": "calculate"}), ("done", "1234 * 5678 = 7,006,652")]

    passed = serving.agent_tool_turn("model:ollama:row-bot-personal-v1:abcd1234")

    assert passed["ok"] and passed["tool_calls"] == 1 and passed["answered"]
    assert seen["prompt"] == serving.CHECK_PROMPT and seen["tools"] == ["calculator"]
    assert seen["config"]["thread_id"].startswith("learning-check-") and seen["config"]["approval_mode"] == "approve"
    assert removed == [seen["config"]["thread_id"]] * 2  # never a chat in the list

    script[:] = [("done", "7006652")]  # answered without the tool
    assert serving.agent_tool_turn("model:x").get("ok") is False
    script[:] = [("tool_call", {"name": "calculate"}), ("done", "It is about seven million.")]
    assert serving.agent_tool_turn("model:x").get("ok") is False
    script[:] = [("tool_call", {"name": "shell"}), ("interrupt", {}), ("done", "7006652")]
    asked = serving.agent_tool_turn("model:x")
    assert asked["ok"] is False and asked["errors"] == ["asked_for_approval"]
