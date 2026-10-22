"""The personal model's version through the API (L2c): the snapshot shows where the newest version
stands (exporting, ready and waiting for its comparison, couldn't start, waiting for the person with
a command), where versions run and the example count; Try again and Runs in act on it; Learning's
personal models stay out of every model list until L2c2 offers one."""

from __future__ import annotations

import sys
from uuid import uuid4

import pytest

from row_bot import learning, threads  # noqa: F401 - bind threads to the session's folder first
from row_bot.api.v1.schemas import LearningReceipt, LearningSnapshot
from row_bot.learning import runner as runner_module
from row_bot.learning.training import releases, serving
from tests.fixtures.learning import FakeDataset, FakeEngine, FakeLease
from tests.subsystem.client_protocol.test_settings_snapshot_api import api

# ruff: noqa: F401, F811 -- the imported pytest fixture is requested by name.

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

LEARNING = "/api/v1/learning"
SETUP = {
    "sources": {"my_chats": True, "workflows_and_goals": True, "past_chats": False},
    "apply_mode": "ask_first", "monthly_token_limit": 2_000_000, "train_in_quiet_hours": True,
    "train_where": "this_computer",
}


class FakeMaker:
    """The release maker: records what it was asked to do."""

    def __init__(self) -> None:
        self.started: list[tuple[str, str | None]] = []
        self.is_busy = False

    def busy(self) -> bool:
        return self.is_busy

    def start(self, job, *, quiet: bool = False, runtime: str | None = None) -> bool:
        self.started.append((job.id, runtime))
        return True

    def tick(self) -> bool:
        return False

    def shutdown(self) -> None:
        return None

    def wait(self, timeout: float = 30.0) -> bool:
        return True


def _snapshot(client, headers) -> dict:
    response = client.get(LEARNING, headers=headers)
    assert response.status_code == 200, response.text
    LearningSnapshot.model_validate_json(response.text)
    return response.json()


def _command(client, headers, action: str, **fields):
    identity = str(uuid4())
    revision = _snapshot(client, headers)["revision"]
    return client.post(LEARNING + "/commands", headers={**headers, "Idempotency-Key": identity},
                       json={"command_id": identity, "client_session_id": headers["X-Client-Session"],
                             "revision": revision, "action": action, **fields})


def _ok(response) -> dict:
    assert response.status_code == 200, response.text
    LearningReceipt.model_validate_json(response.text)
    receipt = response.json()
    assert receipt["status"] == "completed", receipt
    return receipt


@pytest.fixture
def on(api, monkeypatch):
    client, headers, data, _ = api
    monkeypatch.setattr(serving.OllamaRuntime, "executable", lambda self: None)  # this computer's Ollama aside
    monkeypatch.setattr(sys, "platform", "win32")
    assert _ok(_command(client, headers, "turn_on", setup=SETUP))
    maker = FakeMaker()
    runner_module.configure(engine=FakeEngine(), dataset=FakeDataset(), lease=FakeLease(since_activity=3600.0),
                            releases=maker)
    return client, headers, data, maker


def _version(data, job_id: str, **fields) -> releases.VersionState:
    fields.setdefault("phase", releases.EXPORTING)
    state = releases.VersionState(version=1, job_id=job_id, **fields)
    return releases.write_state(releases.version_dir(1, data), state)


def _finished_job():
    jobs = runner_module.get().supervisor.jobs
    job = jobs.plan(steps_total=1, checkpoint_every=1)
    for state in ("preparing", "running", "evaluating"):
        jobs.move(job.id, state)
    return jobs.get(job.id)


def test_the_snapshot_says_where_versions_run_and_has_no_version_yet(on):
    client, headers, _data, _maker = on

    model = _snapshot(client, headers)["personal_model"]

    # No NVIDIA card here: Row-Bot's own server is listed but can't run, so versions run in Ollama.
    assert model["runtime"] == "ollama" and model["version"] is None
    assert [item["id"] for item in model["runtimes"]] == ["row_bot", "ollama", "lmstudio", "llama_cpp"]  # vLLM: Linux
    assert model["runtimes"][0] == {"id": "row_bot", "name": "Row-Bot", "automated": True, "installed": None,
                                    "available": False}
    assert model["runtimes"][1] == {"id": "ollama", "name": "Ollama", "automated": True, "installed": False,
                                    "available": True}


def test_with_an_nvidia_card_personal_models_run_in_row_bots_own_server_by_default(on, monkeypatch):
    from row_bot.learning.training import hardware, lockfile

    client, headers, data, _maker = on
    card = hardware.classify([hardware.Gpu("NVIDIA GeForce RTX 5090", 32_607, "610.60")])
    monkeypatch.setattr(lockfile, "tools_platform", lambda platforms=None: "windows-x64")  # any CI computer
    monkeypatch.setattr(hardware, "peek", lambda: card)
    monkeypatch.setattr(hardware, "check", lambda **_kwargs: card)

    model = _snapshot(client, headers)["personal_model"]

    assert model["runtime"] == "row_bot"
    assert model["runtimes"][0] == {"id": "row_bot", "name": "Row-Bot", "automated": True, "installed": False,
                                    "available": True}
    moved = _ok(_command(client, headers, "choose_runtime", runtime="ollama"))
    assert moved["summary"] == "Personal models run in Ollama."
    assert _snapshot(client, headers)["personal_model"]["runtime"] == "ollama"
    assert learning.read_settings(data).serve_runtime == "ollama"


def test_the_newest_version_is_shown_as_it_is_made(on):
    client, headers, data, _maker = on
    job = _finished_job()

    _version(data, job.id, step="quantize")
    exporting = _snapshot(client, headers)["personal_model"]["version"]
    assert (exporting["version"], exporting["phase"], exporting["step"]) == (1, "exporting", "quantize")

    _version(data, job.id, phase=releases.READY, gguf="row-bot-personal-v1-Q8_0.gguf", gguf_bytes=2_012_012_128,
             examples=154, model_ref="model:ollama:row-bot-personal-v1:abcd1234", trained_at=1_800_000_000.0)
    ready = _snapshot(client, headers)["personal_model"]["version"]
    assert ready["phase"] == "ready" and ready["runtime"] == "ollama" and ready["examples"] == 154
    assert ready["file_name"] == "row-bot-personal-v1-Q8_0.gguf" and ready["file_bytes"] == 2_012_012_128
    assert ready["folder"].endswith("v1") and ready["trained_at"].startswith("2027-01-15")
    assert "model_ref" not in ready  # nothing offers it before L2c2's comparison

    _version(data, job.id, phase=releases.EXPORT_FAILED, failure="not_enough_disk", needs_bytes=9_000_000_000,
             free_bytes=2_000_000_000)
    failed = _snapshot(client, headers)["personal_model"]["version"]
    assert (failed["failure"], failed["needs_bytes"], failed["free_bytes"]) == (
        "not_enough_disk", 9_000_000_000, 2_000_000_000)


def test_try_again_makes_the_version_again_and_says_so(on):
    client, headers, data, maker = on
    job = _finished_job()
    _version(data, job.id, phase=releases.EXPORT_FAILED, failure="export_failed", step="convert")

    receipt = _ok(_command(client, headers, "make_ready"))

    assert receipt["summary"] == "Preparing v1 again." and maker.started == [(job.id, None)]
    _version(data, job.id, phase=releases.RUNTIME_FAILED, failure="tool_turn_failed", runtime="ollama")
    assert _ok(_command(client, headers, "make_ready"))["summary"] == "Making v1 ready in Ollama."
    maker.is_busy = True
    busy = _command(client, headers, "make_ready")
    assert busy.status_code == 409 and busy.json()["code"] == "learning_version_busy"


def test_try_again_with_no_version_is_refused(on):
    client, headers, _data, maker = on

    refused = _command(client, headers, "make_ready")

    assert refused.status_code == 409 and refused.json()["code"] == "learning_version_changed"
    assert maker.started == []


def test_runs_in_saves_the_choice_and_moves_a_made_version_there(on):
    client, headers, data, maker = on

    receipt = _ok(_command(client, headers, "choose_runtime", runtime="llama_cpp"))
    assert receipt["summary"] == "Personal models run in llama.cpp server." and maker.started == []
    assert learning.read_settings(data).serve_runtime == "llama_cpp"
    assert receipt["snapshot"]["personal_model"]["runtime"] == "llama_cpp"

    job = _finished_job()
    _version(data, job.id, phase=releases.READY, gguf="row-bot-personal-v1-Q8_0.gguf", runtime="llama_cpp")
    moved = _ok(_command(client, headers, "choose_runtime", runtime="lmstudio"))
    assert moved["summary"] == "Personal models run in LM Studio. Making v1 ready there."
    assert maker.started == [(job.id, "lmstudio")]


@pytest.mark.parametrize("runtime", ["vllm", "koboldcpp", "", "row_bot"])
def test_a_runtime_this_computer_cant_offer_is_refused(on, runtime):
    client, headers, data, _maker = on

    before = learning.read_settings(data).serve_runtime
    refused = _command(client, headers, "choose_runtime", runtime=runtime)

    assert refused.status_code in (409, 422)
    assert learning.read_settings(data).serve_runtime == before


def test_personal_models_stay_out_of_model_lists_until_offered():
    from row_bot.providers import model_visibility

    assert model_visibility.withheld("ollama", "row-bot-personal-v1:abcd1234")
    assert model_visibility.withheld("custom_openai_row-bot-personal-llama-cpp", "row-bot-personal-v1")
    assert not model_visibility.withheld("ollama", "qwen3:8b")
    assert not model_visibility.withheld("openai", "gpt-5.6-sol")
