"""Personal-model versions (L2c, O6, D14, D7): a finished run becomes a version in its own folder: the
adapter checked and copied, a Release with its full identity and lineage saved in ``learning.db`` and
as a manifest next to the files, the GGUF exported, a model card in plain words, then made ready
only when the runtime's checks pass; failures wait for Try again; a runtime the person starts is
checked again; a restart picks up where it was."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from row_bot.learning import contracts
from row_bot.learning.contracts import Release
from row_bot.learning.store import LearningStore
from row_bot.learning.training import export, jobs as jobs_module, recipes, releases, serving, weights
from tests.fixtures.learning import FakeClock

pytestmark = [pytest.mark.subsystem]

DATASET = {
    "format": "row-bot-dataset", "version": 1, "id": "ds_" + "1" * 32, "sha256": "2" * 64, "recipe": "8gb-v1",
    "examples": 120, "validation": 12, "historical": 0, "left_out": {"held_out": 3, "tool_failed": 2},
    "providers": ["ollama", "openai"], "terms_note": True, "conversations": ["chat-1", "chat-2"],
    "example_ids": ["ep_1", "ep_2"], "first_at": 1_800_000_000.0, "last_at": 1_800_086_400.0,
}


def _finished_run(jobs, root: Path, *, version: int | None = None, reported: str | None = None):
    """A training run that finished: its adapter, what the engine reported, its leased dataset."""

    job = jobs.plan(steps_total=1, checkpoint_every=1, version=version)
    for state in ("preparing", "running", "evaluating"):
        jobs.move(job.id, state)
    run = jobs_module.run_dir(job.id, root)
    (run / "checkpoints").mkdir(parents=True)
    (run / "checkpoints" / "adapter_model.safetensors").write_bytes(b"lora-" + str(job.version).encode())
    (run / "checkpoints" / "adapter_config.json").write_text('{"r": 16}', encoding="utf-8")
    sha = hashlib.sha256((run / "checkpoints" / "adapter_model.safetensors").read_bytes()).hexdigest()
    (run / releases.ARTIFACTS_FILE).write_text(json.dumps({"files": [
        {"name": "adapter_model.safetensors", "bytes": 6, "sha256": reported or sha}]}), encoding="utf-8")
    (run / "data").mkdir()
    (run / "data" / "dataset.json").write_text(json.dumps(DATASET), encoding="utf-8")
    return jobs.get(job.id)


class Exporter:
    def __init__(self, error: export.ExportError | None = None) -> None:
        self.error = error
        self.calls: list[dict] = []

    def __call__(self, **options):
        self.calls.append(options)
        options["on_step"]("convert")
        if self.error is not None:
            raise self.error
        gguf = Path(options["folder"]) / f"{options['name']}-{options['quantization']}.gguf"
        gguf.write_bytes(b"GGUF" * 64)
        return export.ExportResult(gguf=gguf, quantization=options["quantization"], gguf_bytes=256,
                                   gguf_sha256="3" * 64, adapter=options["adapter"], adapter_bytes=15,
                                   adapter_sha256="4" * 64, seconds=1.0)


class Server:
    def __init__(self, *outcomes: serving.ServeOutcome) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple] = []

    def __call__(self, runtime, **options):
        self.calls.append((runtime, options))
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        return outcome


def _ready(runtime: str = "ollama") -> serving.ServeOutcome:
    return serving.ServeOutcome("ready", runtime, model_ref=f"model:ollama:row-bot-personal-v1:{'a' * 8}",
                                runtime_version="0.40.4", probe={"agent_ready": True}, tool_turn={"ok": True})


@pytest.fixture
def starting_model(tmp_path):
    folder = tmp_path / "weights"
    folder.mkdir()
    model = weights.QWEN35_2B
    main = next(item.name for item in model.files if item.name.endswith(".safetensors"))
    for name in (main, "tokenizer.json", "chat_template.jinja"):
        (folder / name).write_bytes(name.encode())
    return model, folder


@pytest.fixture
def maker_for(learning_on, tmp_path, starting_model):
    LearningStore(learning_on).create()
    engine_folder = tmp_path / "engine"
    engine_folder.mkdir()
    (engine_folder / "engine.json").write_text(json.dumps({"packages": {"torch": "2.10.0", "unsloth": "2026.4.1"}}),
                                               encoding="utf-8")
    clock = FakeClock()
    jobs = jobs_module.TrainingJobs(learning_on, clock=clock)

    withdrawn: list[tuple] = []

    def make(*, exporter=None, server=None, runtime="ollama"):
        return releases.ReleaseMaker(
            learning_on, jobs=jobs, clock=clock, exporter=exporter or Exporter(), server=server or Server(_ready()),
            engine_status=lambda: SimpleNamespace(folder=engine_folder, python=None, site=None, llama=None),
            weights_folder=lambda: (starting_model[0], starting_model[1], 4_550_000_000),
            recipe_for=lambda: recipes.load("8gb"), runtime=lambda: runtime, start_threads=False,
            withdraw=lambda runtime, version, **_: withdrawn.append((runtime, version)))

    make.jobs, make.clock, make.root, make.withdrawn = jobs, clock, learning_on, withdrawn
    return make


def test_a_finished_run_becomes_a_ready_version_with_its_identity_beside_its_files(maker_for, starting_model):
    exporter, server = Exporter(), Server(_ready())
    maker = maker_for(exporter=exporter, server=server)
    job = _finished_run(maker_for.jobs, maker_for.root)

    assert maker.tick() is True

    folder = releases.version_dir(1, maker_for.root)
    state = releases.read_state(folder)
    assert state.phase == releases.READY and state.gguf == "row-bot-personal-v1-Q8_0.gguf"
    assert state.model_ref.startswith("model:ollama:row-bot-personal-v1:") and state.checks["tool_turn"] == {"ok": True}
    assert sorted(item.name for item in folder.iterdir()) == [
        "MODEL_CARD.md", "adapter", "dataset.json", "release.json", "row-bot-personal-v1-Q8_0.gguf", "state.json"]
    # The job moves to ready: the version still waits for L2c2's comparison before anyone is asked.
    finished = maker_for.jobs.get(job.id)
    assert finished.state == "ready" and finished.release_id == state.release_id
    release = LearningStore(maker_for.root).get("release", state.release_id)
    assert isinstance(release, Release) and (release.kind, release.version, release.status) == ("model", 1, "candidate")
    identity = release.identity
    model, weights_folder = starting_model
    assert (identity.base.name, identity.base.version) == (model.repo, model.revision) and identity.base.sha256
    assert identity.tokenizer.sha256 and identity.template.sha256
    assert identity.adapter.sha256 == hashlib.sha256(b"lora-1").hexdigest()
    assert identity.quantization == "Q8_0" and identity.recipe.name == "8gb-v1"
    assert identity.recipe.sha256 == recipes.load("8gb").sha256
    assert identity.runtime == contracts.Pin("ollama", "0.40.4")
    assert identity.data.dataset_id == DATASET["id"] and identity.data.providers == ("ollama", "openai")  # D7
    assert release.source_thread_ids == ("chat-1", "chat-2") and release.parent_ids == ()
    # O6: the manifest next to the files is the release itself, readable without learning.db.
    assert contracts.from_manifest((folder / "release.json").read_text(encoding="utf-8")) == release
    [(runtime, options)] = server.calls
    assert runtime == "ollama" and options["serve_context"] == 32768 and options["gguf"] == folder / state.gguf
    assert exporter.calls[0]["quantization"] == "Q8_0" and exporter.calls[0]["weights"] == weights_folder


def test_the_model_card_says_what_it_was_trained_on_in_plain_words(maker_for):
    maker = maker_for()
    _finished_run(maker_for.jobs, maker_for.root)

    maker.tick()

    card = (releases.version_dir(1, maker_for.root) / releases.MODEL_CARD_FILE).read_text(encoding="utf-8")
    assert card.startswith("# Personal model v1\n")
    assert "120 examples of your own work with Row-Bot, from 2 conversations" in card
    assert "12 newer examples were held back to check it." in card
    assert "Left out: 3 kept back as hidden tests, 2 a tool failed." in card
    assert "Answers from: Ollama (on this computer), OpenAI." in card
    assert "may be limited by their terms" in card  # D7
    assert "Starting model: Qwen/Qwen3.5-2B" in card and "LoRA rank 16, 16,384-token examples, 3 passes" in card
    assert "Training engine: torch 2.10.0, unsloth 2026.4.1." in card
    assert "Runs in Ollama: Row-Bot's check and one real task with a tool passed." in card
    assert "Not yet compared with your main model." in card
    assert "chat-1" not in card and "ep_1" not in card  # counts, never ids or content


def _passes(jobs, root: Path, job, losses=(0.9, 0.85, 0.84)) -> list[str]:
    """The run trained three passes: each one's adapter saved at its end, as the engine reported them."""

    run = jobs_module.run_dir(job.id, root)
    shas, files = [], []
    for number in (1, 2, 3):
        folder = run / "checkpoints" / f"pass-{number}"
        folder.mkdir(parents=True)
        (folder / "adapter_model.safetensors").write_bytes(f"lora-pass-{number}".encode())
        (folder / "adapter_config.json").write_text('{"r": 16}', encoding="utf-8")
        sha = hashlib.sha256((folder / "adapter_model.safetensors").read_bytes()).hexdigest()
        config = hashlib.sha256(b'{"r": 16}').hexdigest()
        shas.append(sha)
        files.append([{"name": "adapter_model.safetensors", "bytes": 11, "sha256": sha},
                      {"name": "adapter_config.json", "bytes": 9, "sha256": config}])
    (run / jobs_module.PASSES_FILE).write_text(json.dumps({"losses": list(losses), "files": files}), encoding="utf-8")
    return shas


def _looping(calls: int = 90) -> serving.ServeOutcome:
    """The ready check's tool turn didn't finish: it called tools until the step budget ended it."""

    return serving.ServeOutcome("failed", "ollama", reason="tool_turn_failed", probe={"agent_ready": True},
                                tool_turn={"ok": False, "answered": False, "tool_calls": calls})


def test_a_pass_that_cant_finish_a_short_task_falls_back_to_the_pass_before(maker_for):
    exporter = Exporter()
    maker = maker_for(exporter=exporter, server=Server(_looping(), _ready()))
    job = _finished_run(maker_for.jobs, maker_for.root)
    shas = _passes(maker_for.jobs, maker_for.root, job)

    maker.tick()

    folder = releases.version_dir(1, maker_for.root)
    state = releases.read_state(folder)
    assert state.phase == releases.READY
    assert (state.kept_pass["pass"], state.kept_pass["failed"]) == (2, [{"pass": 3, "tool_calls": 90}])
    assert len(exporter.calls) == 2  # pass 3, then pass 2
    assert (folder / "adapter" / "adapter_model.safetensors").read_bytes() == b"lora-pass-2"
    card = (folder / releases.MODEL_CARD_FILE).read_text(encoding="utf-8")
    assert "Pass 3 couldn't finish a short task (90 tool calls); kept pass 2 of 3." in card
    assert "Loss on the examples held back, by pass (for information): 0.900, 0.850, 0.840." in card
    release = LearningStore(maker_for.root).get("release", state.release_id)
    assert (release.identity.adapter.version, release.identity.adapter.sha256) == ("lora_sft, pass 2 of 3", shas[1])
    assert json.loads((folder / releases.MANIFEST_FILE).read_text(encoding="utf-8"))["identity"]["adapter"][
        "version"] == "lora_sft, pass 2 of 3"
    assert maker_for.jobs.get(job.id).state == "ready"


def test_the_last_pass_that_is_ready_is_kept(maker_for):
    exporter = Exporter()
    maker = maker_for(exporter=exporter, server=Server(_ready()))
    job = _finished_run(maker_for.jobs, maker_for.root)
    shas = _passes(maker_for.jobs, maker_for.root, job)

    maker.tick()

    folder = releases.version_dir(1, maker_for.root)
    state = releases.read_state(folder)
    assert (state.phase, state.kept_pass["pass"], state.kept_pass["failed"]) == (releases.READY, 3, [])
    assert len(exporter.calls) == 1  # the last pass made ready at once: nothing to fall back from
    card = (folder / releases.MODEL_CARD_FILE).read_text(encoding="utf-8")
    assert "Kept pass 3 of 3, the last." in card
    release = LearningStore(maker_for.root).get("release", state.release_id)
    assert (release.identity.adapter.version, release.identity.adapter.sha256) == ("lora_sft, pass 3 of 3", shas[2])


@pytest.mark.parametrize("outcome", [
    serving.ServeOutcome("failed", "ollama", reason="not_installed"),  # no runtime: every pass would fail alike
    serving.ServeOutcome("failed", "ollama", reason="probe_failed", probe={"agent_ready": False}),
])
def test_a_failure_that_isnt_the_models_doesnt_fall_back(maker_for, outcome):
    exporter = Exporter()
    maker = maker_for(exporter=exporter, server=Server(outcome))
    job = _finished_run(maker_for.jobs, maker_for.root)
    _passes(maker_for.jobs, maker_for.root, job)

    maker.tick()

    state = releases.read_state(releases.version_dir(1, maker_for.root))
    assert (state.phase, state.failure, state.kept_pass["pass"]) == (releases.RUNTIME_FAILED, outcome.reason, 3)
    assert len(exporter.calls) == 1


def test_when_no_pass_can_finish_a_short_task_the_version_couldnt_start(maker_for):
    exporter = Exporter()
    maker = maker_for(exporter=exporter, server=Server(_looping()))
    job = _finished_run(maker_for.jobs, maker_for.root)
    _passes(maker_for.jobs, maker_for.root, job)

    maker.tick()

    folder = releases.version_dir(1, maker_for.root)
    state = releases.read_state(folder)
    assert (state.phase, state.failure) == (releases.RUNTIME_FAILED, "tool_turn_failed")  # couldn't start (O106)
    assert [item["pass"] for item in state.kept_pass["failed"]] == [3, 2, 1] and len(exporter.calls) == 3
    card = (folder / releases.MODEL_CARD_FILE).read_text(encoding="utf-8")
    assert ("Pass 3 couldn't finish a short task (90 tool calls); pass 2 couldn't finish a short task (90 tool "
            "calls); pass 1 couldn't finish a short task (90 tool calls).") in card
    assert maker_for.jobs.get(job.id).state == "evaluating"  # it waits with its ways out, and doesn't block


def test_each_version_names_the_one_before_it(maker_for):
    maker = maker_for()
    first = _finished_run(maker_for.jobs, maker_for.root)
    maker.tick()
    second = _finished_run(maker_for.jobs, maker_for.root)

    maker.tick()

    store = LearningStore(maker_for.root)
    one = store.get("release", maker_for.jobs.get(first.id).release_id)
    two = store.get("release", maker_for.jobs.get(second.id).release_id)
    assert (one.version, two.version) == (1, 2) and two.parent_ids == (one.id,)


def test_an_export_that_fails_waits_for_try_again_and_the_run_stays_finished(maker_for):
    failing = Exporter(export.ExportError("not_enough_disk", needs=9 * 10**9, free=2 * 10**9))
    maker = maker_for(exporter=failing)
    job = _finished_run(maker_for.jobs, maker_for.root)

    maker.tick()

    folder = releases.version_dir(1, maker_for.root)
    state = releases.read_state(folder)
    assert (state.phase, state.failure, state.needs_bytes, state.free_bytes) == (
        releases.EXPORT_FAILED, "not_enough_disk", 9 * 10**9, 2 * 10**9)
    assert maker_for.jobs.get(job.id).state == "evaluating"
    assert maker.tick() is False  # a failure waits for the person

    again = maker_for(exporter=Exporter())
    assert again.start(maker_for.jobs.get(job.id)) is True  # Try again
    assert releases.read_state(folder).phase == releases.READY


def test_an_adapter_that_isnt_the_one_trained_is_never_exported(maker_for):
    exporter = Exporter()
    maker = maker_for(exporter=exporter)
    _finished_run(maker_for.jobs, maker_for.root, reported="9" * 64)

    maker.tick()

    state = releases.read_state(releases.version_dir(1, maker_for.root))
    assert (state.phase, state.failure) == (releases.EXPORT_FAILED, "adapter_missing") and exporter.calls == []


def test_a_runtime_that_fails_its_checks_leaves_the_version_unoffered(maker_for):
    server = Server(serving.ServeOutcome("failed", "ollama", reason="tool_turn_failed", tool_turn={"ok": False}))
    maker = maker_for(server=server)
    job = _finished_run(maker_for.jobs, maker_for.root)

    maker.tick()

    state = releases.read_state(releases.version_dir(1, maker_for.root))
    assert (state.phase, state.failure, state.model_ref) == (releases.RUNTIME_FAILED, "tool_turn_failed", None)
    assert maker_for.jobs.get(job.id).state == "evaluating"
    release = LearningStore(maker_for.root).get("release", state.release_id)
    assert release.identity.runtime is None


def test_a_runtime_the_person_starts_is_checked_again_until_it_answers(maker_for):
    command = 'llama-server -m "v1.gguf" -a row-bot-personal-v1 --host 127.0.0.1 --port 8181'
    server = Server(serving.ServeOutcome("waiting_for_you", "llama_cpp", reason="not_running", command=command),
                    serving.ServeOutcome("ready", "llama_cpp", model_ref="model:custom_openai_x:row-bot-personal-v1"))
    maker = maker_for(server=server, runtime="llama_cpp")
    job = _finished_run(maker_for.jobs, maker_for.root)

    maker.tick()
    folder = releases.version_dir(1, maker_for.root)
    waiting = releases.read_state(folder)
    assert (waiting.phase, waiting.command, waiting.runtime) == (releases.WAITING, command, "llama_cpp")
    assert maker.tick() is False  # not yet: checked every couple of minutes

    maker_for.clock.advance(releases.RECHECK_SECONDS)
    assert maker.tick() is True

    assert releases.read_state(folder).phase == releases.READY and maker_for.jobs.get(job.id).state == "ready"
    assert len(server.calls) == 2  # exported once, checked twice


def test_a_version_cut_short_by_a_restart_runs_again(maker_for):
    exporter = Exporter()
    maker = maker_for(exporter=exporter)
    job = _finished_run(maker_for.jobs, maker_for.root)
    folder = releases.version_dir(1, maker_for.root)
    releases.write_state(folder, releases.VersionState(version=1, phase=releases.EXPORTING, job_id=job.id,
                                                       step="convert"))

    assert maker.due() is not None and maker.tick() is True

    assert releases.read_state(folder).phase == releases.READY and len(exporter.calls) == 1


def test_nothing_is_made_while_learning_is_off(maker_for):
    from tests.fixtures.learning import save_learning_settings

    maker = maker_for()
    _finished_run(maker_for.jobs, maker_for.root)
    save_learning_settings(maker_for.root, enabled=False)

    assert maker.tick() is False
    assert not releases.versions_dir(maker_for.root).exists()


def test_the_newest_versions_examples_are_what_new_ones_are_counted_against(maker_for):
    maker = maker_for()
    _finished_run(maker_for.jobs, maker_for.root)
    maker.tick()

    assert releases.trained_example_ids(maker_for.root) == {"ep_1", "ep_2"}
    assert releases.latest_state(maker_for.root).version == 1


def test_moving_a_ready_version_to_another_runtime_takes_it_out_of_ollama(maker_for):
    maker = maker_for()
    job = _finished_run(maker_for.jobs, maker_for.root)
    maker.tick()
    exporter = Exporter()
    command = 'lms import "v1.gguf"'
    moved = maker_for(exporter=exporter, server=Server(serving.ServeOutcome(
        "waiting_for_you", "lmstudio", reason="not_running", command=command)))

    assert moved.start(maker_for.jobs.get(job.id), runtime="lmstudio") is True

    state = releases.read_state(releases.version_dir(1, maker_for.root))
    assert (state.phase, state.runtime, state.command) == (releases.WAITING, "lmstudio", command)
    assert maker_for.withdrawn == [("ollama", 1)] and exporter.calls == []  # the same file, not exported again


def test_try_again_still_makes_a_version_that_couldnt_start_ready_while_the_next_one_waits(maker_for):
    failing = maker_for(server=Server(serving.ServeOutcome("failed", "ollama", reason="tool_turn_failed")))
    job = _finished_run(maker_for.jobs, maker_for.root)
    failing.tick()
    folder = releases.version_dir(1, maker_for.root)
    assert releases.couldnt_start(releases.read_state(folder))
    assert not releases.holds_back(maker_for.jobs.get(job.id), maker_for.root)
    assert releases.trained_example_ids(maker_for.root) == set()  # not counted as trained
    nxt = maker_for.jobs.plan(steps_total=1, checkpoint_every=1)  # the runner planned v2
    assert nxt.version == 2 and failing.due() is None

    maker_for(server=Server(_ready())).start(maker_for.jobs.get(job.id))  # Try again

    state = releases.read_state(folder)
    assert state.phase == releases.READY and maker_for.jobs.get(job.id).state == "ready"
    assert not releases.couldnt_start(state) and releases.trained_example_ids(maker_for.root) == {"ep_1", "ep_2"}
