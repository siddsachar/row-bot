"""Personal-model versions the person acts on (L2c2): Export… to a folder (D14), Delete version,
Needs you (ready to compare, Keep …?, a deleted chat it was trained on, O6), Retrain without it, the
picker entry (D11), Advanced with Reset (D9), freeing the card for training (O51) and the untouched
starting model a first version is compared with."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from row_bot.learning import model_compare, use, versions
from row_bot.learning.store import LearningStore
from row_bot.learning.training import advanced, base_model, freeing, recipes, releases

pytestmark = [pytest.mark.subsystem]

PERSONAL = "model:ollama:row-bot-personal-v1:abcd1234"


def _version(root, number: int = 1, *, phase=releases.READY, release_id: str = "", runtime: str = "ollama"):
    folder = releases.version_dir(number, root)
    (folder / "adapter").mkdir(parents=True, exist_ok=True)
    (folder / "adapter" / "adapter_model.safetensors").write_bytes(b"a" * 4000)
    (folder / "adapter" / "adapter_config.json").write_text("{}", encoding="utf-8")
    gguf = f"row-bot-personal-v{number}-Q8_0.gguf"
    (folder / gguf).write_bytes(b"g" * 20_000)
    (folder / releases.MODEL_CARD_FILE).write_text("# Personal model", encoding="utf-8")
    (folder / releases.MANIFEST_FILE).write_text("{}", encoding="utf-8")
    releases.write_state(folder, releases.VersionState(
        version=number, phase=phase, job_id=f"job_{number}", release_id=release_id, runtime=runtime,
        model_ref=f"model:ollama:row-bot-personal-v{number}:abcd1234" if phase == releases.READY else None,
        gguf=gguf, gguf_bytes=20_000, adapter_bytes=4000))
    return folder


def test_export_copies_the_adapter_the_gguf_the_card_and_the_manifest(data_dir, tmp_path):
    _version(data_dir)
    target = tmp_path / "exports"

    report = versions.export_version(1, str(target), root=data_dir)

    folder = target / "row-bot-personal-v1"
    assert report["folder"] == str(folder)
    names = sorted(str(path.relative_to(folder)).replace("\\", "/") for path in folder.rglob("*") if path.is_file())
    assert names == ["MODEL_CARD.md", "adapter/adapter_config.json", "adapter/adapter_model.safetensors",
                     "release.json", "row-bot-personal-v1-Q8_0.gguf"]
    assert report["bytes"] == sum(item["bytes"] for item in report["files"])
    again = versions.export_version(1, str(target), root=data_dir)
    assert again["folder"].endswith("row-bot-personal-v1 (2)")  # never over an earlier export


@pytest.mark.parametrize(("folder", "code"), [("relative/folder", "invalid_folder"), ("", "invalid_folder")])
def test_export_refuses_a_folder_it_cant_use(data_dir, folder, code):
    _version(data_dir)
    with pytest.raises(versions.VersionError) as refused:
        versions.export_version(1, folder, root=data_dir)
    assert refused.value.code == code


def test_export_never_writes_inside_row_bots_own_data(data_dir):
    _version(data_dir)
    with pytest.raises(versions.VersionError) as refused:
        versions.export_version(1, str(data_dir / "elsewhere"), root=data_dir)
    assert refused.value.code == "inside_data_folder"


def test_export_says_when_there_isnt_room(data_dir, tmp_path):
    _version(data_dir)
    with pytest.raises(versions.VersionError) as refused:
        versions.export_version(1, str(tmp_path / "out"), root=data_dir, disk_free=lambda path: 10)
    assert refused.value.code == "not_enough_disk" and refused.value.needs > refused.value.free


def test_delete_version_undoes_it_everywhere_takes_it_out_of_ollama_and_frees_its_files(learning_on):
    store = LearningStore(learning_on)
    store.create()
    from tests.fixtures.learning import release

    store.put(release("rel_1", version=1, status="trial"))
    folder = _version(learning_on, release_id="rel_1")
    use.start_trial(1, "document_lookups", PERSONAL, root=learning_on)
    withdrawn = []

    report = versions.delete_version(1, root=learning_on,
                                     withdraw=lambda runtime, version, **kw: withdrawn.append((runtime, version)))

    assert report["undone"] == ["document_lookups"] and report["freed"] > 20_000
    assert use.read(learning_on).assignments == {} and withdrawn == [("ollama", 1)]
    assert not folder.exists() and versions.all_versions(learning_on) == []
    assert store.get("release", "rel_1").status == "retired"  # kept for the archive


def test_every_ready_version_is_offered_in_the_picker_as_personal_model(data_dir):
    _version(data_dir, 1)
    _version(data_dir, 2, phase=releases.RUNTIME_FAILED)

    assert versions.offered(data_dir) == {PERSONAL: "Personal model v1"}


def test_the_picker_offers_a_ready_version_as_personal_model_and_never_the_starting_model(data_dir, monkeypatch):
    from row_bot import learning
    from row_bot.providers import client_status
    from row_bot.providers.model_catalog import CatalogModelRow

    _version(data_dir, 1)
    learning.invalidate()

    def row(model_id: str) -> CatalogModelRow:
        return CatalogModelRow(provider_id="ollama", model_id=model_id, display_name=model_id, categories=("chat",),
                               capabilities_snapshot={"tool_calling": True}, selection_ref=f"model:ollama:{model_id}",
                               provider_display_name="Ollama")

    built = [row("row-bot-personal-v1:abcd1234"), row("row-bot-personal-base:abcd1234"), row("qwen3:8b")]
    monkeypatch.setattr(client_status, "build_saved_model_catalog_rows", lambda **kw: list(built))

    choices = {option["selection_ref"]: option for option in client_status.picker_choices("chat")}

    assert PERSONAL in choices and choices[PERSONAL]["label"].startswith("Personal model v1")
    assert "model:ollama:row-bot-personal-base:abcd1234" not in choices  # the comparison's baseline, never offered
    releases.write_state(releases.version_dir(1, data_dir), releases.VersionState(
        version=1, phase=releases.RUNTIME_FAILED, job_id="job_1"))
    assert PERSONAL not in {option["selection_ref"] for option in client_status.picker_choices("chat")}


# ── Needs you and Retrain without it ────────────────────────────────────────


def test_needs_you_says_a_version_is_ready_to_compare_until_it_is_reviewed(data_dir):
    _version(data_dir)
    assert versions.needs_you(data_dir) == []  # not compared yet
    model_compare._write_json(model_compare.comparison_path(1, data_dir), {  # noqa: SLF001
        "format": model_compare.FORMAT, "compared_at": 1.0, "reviewed_at": None})

    [item] = versions.needs_you(data_dir)
    assert (item["type"], item["title"]) == ("compare", "Personal model v1 is ready to compare")
    model_compare.mark_reviewed(1, root=data_dir)
    assert versions.needs_you(data_dir) == []


def test_needs_you_asks_keep_when_a_trial_is_due(data_dir):
    _version(data_dir)
    use.start_trial(1, "document_lookups", PERSONAL, root=data_dir, clock=lambda: 1_000.0)
    for _ in range(use.TRIAL_MIN_REQUESTS):
        use.record("document_lookups", 1, "answered", 2.0, root=data_dir)

    [item] = versions.needs_you(data_dir, now=1_000.0 + use.TRIAL_DAYS * 86_400)

    assert (item["type"], item["kind"], item["title"]) == ("keep", "document_lookups",
                                                           "Keep v1 for document lookups?")


def test_a_deleted_chat_a_version_was_trained_on_asks_to_retrain_without_it(learning_on):
    store = LearningStore(learning_on)
    store.create()
    from tests.fixtures.learning import release

    store.put(release("rel_1", version=1, source_thread_ids=("chat-trained-on",)))
    _version(learning_on, release_id="rel_1")
    assert versions.needs_you(learning_on) == []

    store.purge_thread("chat-trained-on")

    [item] = versions.needs_you(learning_on)
    assert (item["type"], item["title"]) == ("retrain", "A deleted chat was used to train v1.")

    planned = []

    class Planner:
        def plan(self, *, force=False, retrain=False):
            planned.append((force, retrain))
            return SimpleNamespace(job=SimpleNamespace(version=2), reason=None)

    outcome = versions.retrain_without(1, planner=Planner(), root=learning_on)
    assert outcome.job.version == 2 and planned == [(True, True)]
    assert versions.needs_you(learning_on) == []  # handled


def test_retrain_without_plans_even_without_new_examples(learning_on):
    from tests.fixtures.learning import FakeEngine
    from row_bot.learning.training.jobs import TrainingJobs
    from row_bot.learning.training.planner import Planner

    LearningStore(learning_on).create()
    calls = []
    planner = Planner(learning_on, jobs=TrainingJobs(learning_on), engine=FakeEngine(),
                      trained_ids=lambda: {"ex_1"})
    summary = SimpleNamespace(enough=True, new_examples=0, examples=200)
    planner.summary = lambda compute=True: summary  # type: ignore[method-assign]
    import row_bot.learning.training.dataset as dataset_module

    compiled = SimpleNamespace(examples=200, id="ds_x")

    def compile_dataset(*args, **kwargs):
        calls.append(kwargs.get("refresh_vault"))
        return compiled

    def write(compiled, folder):
        folder.mkdir(parents=True, exist_ok=True)
        return 200

    original = dataset_module.compile_dataset
    dataset_module.compile_dataset = compile_dataset
    original_summarize = dataset_module.summarize
    dataset_module.summarize = lambda *a, **k: summary
    original_write = dataset_module.write
    dataset_module.write = write
    try:
        assert planner.plan(force=True).reason == "up_to_date"
        outcome = planner.plan(force=True, retrain=True)
    finally:
        dataset_module.compile_dataset = original
        dataset_module.summarize = original_summarize
        dataset_module.write = original_write
    assert outcome.job is not None and calls == [True]


# ── Advanced (D9) ───────────────────────────────────────────────────────────


def test_advanced_starts_at_the_recommended_values(data_dir):
    recipe = recipes.load("8gb")
    found = advanced.view(recipe, data_dir)

    assert found["values"] == found["recommended"] == {
        "starting_model": "qwen3.5-2b", "rank": 16, "learning_rate": 0.0002, "epochs": 3, "context": 16384,
        "method": "standard"}
    assert found["changed"] is False and found["choices"]["context"] == [16384]  # 32K didn't fit 8 GB
    assert [item["available"] for item in found["data_mix"]] == [True, False, False, False]


def test_advanced_values_are_clamped_to_the_engines_limits_and_the_recipes_bounds(data_dir):
    recipe = recipes.load("8gb")
    low, high = advanced.engine_limits()["rank"]

    chosen = advanced.clamp({"rank": 10_000, "learning_rate": 5.0, "epochs": 99, "context": 131072,
                             "method": "dpo", "starting_model": "llama-70b", "report_to": "wandb"}, recipe)

    assert chosen.rank == max(advanced.RANKS) <= high and chosen.learning_rate == max(advanced.LEARNING_RATES)
    assert chosen.epochs == max(advanced.EPOCHS) and chosen.context == 16384  # never past what the tier measured
    assert chosen.method == "standard" and chosen.starting_model == "qwen3.5-2b"
    assert advanced.clamp({"rank": -5, "learning_rate": 0}, recipe).rank == min(advanced.RANKS) >= low


def test_saved_choices_change_the_next_runs_recipe_and_reset_goes_back(data_dir):
    recipe = recipes.load("8gb")

    advanced.save({"rank": 32, "learning_rate": 1e-4, "epochs": 2}, recipe, data_dir)
    changed = advanced.apply(recipe, data_dir)

    assert (changed.settings["rank"], changed.settings["alpha"], changed.settings["learning_rate"]) == (32, 32, 1e-4)
    assert changed.epochs == 2 and changed.sha256 != recipe.sha256 and changed.steps_for(100) == 50
    saved = json.loads(advanced.path(data_dir).read_text(encoding="utf-8"))
    assert saved["values"] == {"epochs": 2, "learning_rate": 0.0001, "rank": 32}  # only what changed
    advanced.reset(data_dir)
    assert advanced.apply(recipe, data_dir) is recipe and not advanced.path(data_dir).exists()


def test_the_planner_trains_with_the_saved_choices(data_dir):
    from row_bot.learning.training.planner import Planner

    recipe = recipes.load("8gb")
    advanced.save({"rank": 64}, recipe, data_dir)
    planner = Planner(data_dir, jobs=SimpleNamespace(), engine=SimpleNamespace(), recipe_for=lambda: recipe)

    assert planner.recipe().settings["rank"] == 64 and planner.recipe().spec()["settings"]["rank"] == 64


# ── O51: freeing the card ───────────────────────────────────────────────────


class FakeOllama:
    def __init__(self, loaded):
        self._loaded = loaded
        self.unloaded: list[str] = []

    def loaded(self):
        return list(self._loaded)

    def unload(self, name):
        self.unloaded.append(name)
        return True


MIB = 1024 * 1024


def _unloader(loaded, used_mib, own=frozenset({"qwen3:8b"})):
    ollama = FakeOllama(loaded)
    return freeing.OwnModelUnloader(ollama=ollama, memory=lambda: (used_mib, 8151), own=lambda: set(own)), ollama


def test_rowbots_own_resident_model_is_unloaded_after_30_minutes_without_use():
    unloader, ollama = _unloader([{"name": "qwen3:8b", "size_vram": 5500 * MIB}], 5800)

    assert not unloader.free_card(idle_seconds=29 * 60) and ollama.unloaded == []
    assert unloader.free_card(idle_seconds=31 * 60) and ollama.unloaded == ["qwen3:8b"]


def test_a_model_another_app_loaded_is_never_touched():
    unloader, ollama = _unloader([{"name": "qwen3:8b", "size_vram": 3000 * MIB},
                                  {"name": "llama3:70b", "size_vram": 2000 * MIB}], 5300)

    assert not unloader.free_card(idle_seconds=3600) and ollama.unloaded == []


def test_nothing_is_unloaded_when_another_program_holds_the_card():
    unloader, ollama = _unloader([{"name": "qwen3:8b", "size_vram": 1000 * MIB}], 6000)

    assert not unloader.free_card(idle_seconds=3600) and ollama.unloaded == []


def test_a_personal_model_left_loaded_is_rowbots_own():
    unloader, ollama = _unloader([{"name": "row-bot-personal-v1:abcd1234", "size_vram": 2500 * MIB}], 2700,
                                 own=frozenset())

    assert unloader.free_card(idle_seconds=3600) and ollama.unloaded == ["row-bot-personal-v1:abcd1234"]


def test_rowbots_own_models_are_its_default_and_what_its_ledger_shows_it_called(data_dir, monkeypatch):
    import sqlite3

    from row_bot import models

    monkeypatch.setattr(models, "get_current_model", lambda: "model:ollama:qwen3:8b")
    ledger = data_dir / "usage.db"
    with sqlite3.connect(ledger) as connection:
        connection.execute("CREATE TABLE model_usage (created_at REAL, provider TEXT, model TEXT)")
        connection.execute("INSERT INTO model_usage VALUES (?, 'ollama', 'gemma3')", (1_000_000.0,))
        connection.execute("INSERT INTO model_usage VALUES (?, 'ollama', 'mistral:7b')", (1.0,))  # long ago
        connection.execute("INSERT INTO model_usage VALUES (?, 'openai', 'gpt-6.1-sol')", (1_000_000.0,))

    found = freeing.own_models(now=1_000_100.0, ledger=ledger)

    assert {"qwen3:8b", "gemma3", "gemma3:latest"} <= found and "mistral:7b" not in found
    assert "gpt-6.1-sol" not in found


def test_the_runner_frees_the_card_only_in_quiet_hours_for_a_quiet_hours_run(learning_on):
    from row_bot.learning.runner import LearningRunner

    calls = []
    supervisor = SimpleNamespace(gpu_free=lambda: False, lease=SimpleNamespace(read=lambda: SimpleNamespace(
        since_activity=3600.0)), start=lambda job, trigger: calls.append(("start", trigger)))
    unloader = SimpleNamespace(free_card=lambda idle_seconds: calls.append(("free", idle_seconds)) or True)
    hours = SimpleNamespace(inside=lambda: True)
    runner = LearningRunner(supervisor, root=learning_on, hours=hours, unloader=unloader)

    runner._start(SimpleNamespace(id="job"), "quiet_hours", act=True)  # noqa: SLF001
    runner._start(SimpleNamespace(id="job"), "train_now", act=True)  # noqa: SLF001
    hours.inside = lambda: False
    runner._start(SimpleNamespace(id="job"), "quiet_hours", act=True)  # noqa: SLF001

    assert calls == [("free", 3600.0), ("start", "quiet_hours"), ("start", "train_now"), ("start", "quiet_hours")]


# ── The untouched starting model ────────────────────────────────────────────


def test_the_starting_model_is_exported_once_and_served_with_only_the_probe(data_dir, tmp_path):
    exported, served = [], []
    model = SimpleNamespace(id="qwen3.5-2b")

    def exporter(**kwargs):
        exported.append(kwargs["untouched"])
        gguf = kwargs["folder"] / f"{kwargs['name']}-Q8_0.gguf"
        gguf.write_bytes(b"base")
        return SimpleNamespace(gguf=gguf)

    def server(runtime, **kwargs):
        served.append((runtime, kwargs["tag"], kwargs["tool_turn"]))
        return SimpleNamespace(state="ready", model_ref=f"model:ollama:{kwargs['tag']}", reason=None)

    class Ollama:
        def show(self, name):
            return {"model": name}

    options = dict(root=data_dir, engine_status=object(), model=model, weights_folder=tmp_path, weights_bytes=10,
                   recipe=recipes.load("8gb"), exporter=exporter, server=server, ollama=Ollama())
    ref, reason = base_model.ensure(**options)
    again, _ = base_model.ensure(**options)

    assert reason is None and ref == again and ref.startswith("model:ollama:row-bot-personal-base:")
    assert exported == [True] and served == [("ollama", base_model.tag(data_dir), False)]


def test_deleting_a_version_that_never_became_ready_settles_it_and_keeps_the_starting_model(learning_on):
    from dataclasses import replace

    from row_bot.learning.training import jobs as jobs_module

    LearningStore(learning_on).create()
    jobs = jobs_module.TrainingJobs(learning_on)
    job = jobs.plan(steps_total=1, checkpoint_every=1)
    for state in ("preparing", "running", "evaluating"):
        jobs.move(job.id, state)
    folder = _version(learning_on, phase=releases.RUNTIME_FAILED)
    releases.write_state(folder, replace(releases.read_state(folder), job_id=job.id, failure="tool_turn_failed"))
    base = base_model.folder("qwen3.5-2b", learning_on)
    base.mkdir(parents=True)
    (base / "state.json").write_text(json.dumps({"model_id": "qwen3.5-2b", "gguf": "base.gguf"}), encoding="utf-8")
    (base / "base.gguf").write_bytes(b"g" * 100)
    maker = releases.ReleaseMaker(learning_on, jobs=jobs, start_threads=False)

    versions.delete_version(1, root=learning_on, withdraw=lambda *args, **kwargs: None)

    assert jobs.get(job.id).state == "not_used" and jobs.current() is None
    assert maker.due() is None and not folder.exists()  # never made again
    assert (base / "base.gguf").is_file()  # training in quiet hours is on: the next version is compared with it
