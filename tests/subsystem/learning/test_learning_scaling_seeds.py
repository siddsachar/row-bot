"""The training-seed study (PREREG-SEEDS.md, draft v0): the fresh block (days 371-450) and its repeated-wording flag; the
new models trained in §4's order with only their seed changed (the study's own subset, recipe, DAgger rounds and
rollouts) and §4's single retraining; the six arms evaluated in their fixed order at 65,536 tokens; the model files'
hash stop; T1's arithmetic and its permutation's label swap; the card's rules; the draft's refusal of a real run; and
the dry run end to end with its resume."""

from __future__ import annotations

import dataclasses
import itertools
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.e2e.learning.scaling import replicate, replicate_worker, runner, seeds, seeds_analysis, seeds_worker, study
from tests.e2e.learning.scaling import worker as study_worker
from tests.e2e.learning.scaling.design import Design
from tests.e2e.learning.seeded import scratch

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

PROVIDER = "custom_openai_row-bot-personal-row-bot"
NAMES = {"sft-a": "row-bot-personal-v3", "sft-b": "row-bot-personal-v4", "dagger-a": "row-bot-personal-v7",
         "sft-c": "row-bot-personal-v8", "dagger-b": "row-bot-personal-v10", "dagger-c": "row-bot-personal-v12"}
BUILD = {"commit": "a" * 40, "code_hash": "1" * 64}


@pytest.fixture
def home(tmp_path, monkeypatch):
    folder = tmp_path / "home"
    (folder / ".row-bot").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: folder))
    monkeypatch.setenv(scratch.PROTECT_ENV, str(tmp_path / "backups"))
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "row-bot-data"))  # restored after the test
    return folder


@pytest.fixture
def quick_block(monkeypatch):
    """The dry design's fresh block without generating it (the generator's own counts are the slow test's)."""

    fresh = {"mara-d371-0": {"person": "mara", "day": 371, "cluster": "mara/d371", "template": "arithmetic",
                             "repeated": False}}
    monkeypatch.setattr(replicate, "unchanged_days", lambda design: {"unchanged": True, "by_person": {}})
    monkeypatch.setattr(replicate, "fresh_block", lambda design: dict(fresh))
    return fresh


def _dry(tmp_path: Path, *extra: str) -> SimpleNamespace:
    return seeds.parse(["--dry-run", "--out", str(tmp_path / "out"), "--scratch", str(tmp_path / "scratch"), *extra])


# ── The fresh block and the plan ─────────────────────────────────────────────


@pytest.mark.slow  # the generator makes 450 days and 370 days for each of the three people (about 9 s)
def test_the_plan_runs_nothing_and_counts_the_fresh_block_with_days_1_to_370_unchanged(tmp_path, home, monkeypatch,
                                                                                         capsys):
    def refuse(*_args, **_kwargs):
        raise AssertionError("--plan started a process")

    monkeypatch.setattr(study.subprocess, "Popen", refuse)

    assert seeds.main(["--out", str(tmp_path / "out"), "--scratch", str(tmp_path / "scratch"), "--plan"]) == 0

    plan = json.loads(capsys.readouterr().out)
    block = plan["fresh_block"]
    assert (block["days"], block["requests"], block["person_days"]) == ([371, 450], 485, 240)
    assert block["by_person"] == {"idris": 154, "mara": 165, "wren": 166}
    assert (block["repeated_wording"], block["new_wording"]) == (148, 337)
    assert plan["study_days"]["unchanged"] is True
    for person in plan["people"].values():
        assert {int(item.split()[1][1:]) for item in person["ids"]} <= set(range(371, 451))
    assert [item["arm"] for item in plan["arms"]] == list(seeds.ARMS)
    assert {item["arm"]: item["training_seed"] for item in plan["arms"]} == {
        "sft-a": 3407, "sft-b": 3408, "sft-c": 20261121, "dagger-a": 3407, "dagger-b": 20261122, "dagger-c": 20261123}
    assert plan["order"] == {"train": ["sft-c", "dagger-b", "dagger-c"],
                             "evaluate": ["dagger-a", "sft-a", "dagger-b", "sft-b", "dagger-c", "sft-c"]}
    assert plan["runs"]["per_arm"] == 485 * 3 and plan["runs"]["total"] == 485 * 3 * 6
    assert plan["chatgpt_lane_tokens"]["total"] < plan["chatgpt_lane_tokens"]["cap"]
    assert 20 < plan["gpu_hours"]["total"] < 25
    assert not (tmp_path / "out").exists() and not (tmp_path / "scratch").exists()


# ── Training: only the seed changes, in §4's order ───────────────────────────


class _Trainer:
    """The study's trainer as the training loop sees it: each model's rows and recipe recorded, ready unless told."""

    def __init__(self, outcomes=None) -> None:
        self.calls: list[dict] = []
        self.entries: dict[str, dict] = {}
        self.outcomes = dict(outcomes or {})
        self.version = 8
        self.gpu = scratch.GpuGate(None, "lane-paper", training=lambda: False, quiet=lambda: None, card=lambda: None,
                                   after_quiet=lambda: False, say=lambda _text: None)
        self.started = 0

    def start_model(self) -> dict:
        self.started += 1
        return {"ref": f"model:{PROVIDER}:row-bot-personal-base"}

    def next_version(self) -> int:
        return self.version

    def compiled(self, examples, recipe):
        return study_worker.Trainer.compiled(None, examples, recipe)

    def train(self, name, examples, recipe, *, version=None):
        if name in self.entries:
            return self.entries[name]
        outcome = self.outcomes.get(name, "ready")
        self.calls.append({"name": name, "rows": [item.id for item in examples], "recipe": recipe, "version": version})
        entry = {"state": outcome, "version": version,
                 "model_ref": f"model:{PROVIDER}:row-bot-personal-v{version}" if outcome == "ready" else None,
                 "why": None if outcome == "ready" else f"{name} {outcome}"}
        self.version += 1
        self.entries[name] = entry
        return entry


class _Rollouts:
    """The study's runner and relabeller as DAgger's rounds see them: each request's run by the round's student, and
    ``rows_of`` rows (default 1) relabelled from it, up to what the round still wants."""

    def __init__(self, rows_of=None) -> None:
        self.rows_of = dict(rows_of or {})
        self.runs: list[tuple[str, str, str]] = []
        self.options: list[dict] = []
        self.budgets: list[SimpleNamespace] = []
        self.booted = 0

    def runner(self, **options):
        rollouts = self
        rollouts.options.append(options)

        class Runner:
            arms: dict = {}
            after = None

            def boot(self, people) -> None:
                rollouts.booted += 1

            def run_task(self, task, arm):
                rollouts.runs.append((task.id, arm, self.arms[arm].model_ref))
                self.after(arm, task, "thread")
                return runner.Run(SimpleNamespace(status="passed", seconds=2.0), "thread")

            def finish(self) -> None:
                return None

        return Runner()

    def relabeller(self, runner_, teacher_ref, design, measure, state, budget):
        rollouts = self
        rollouts.budgets.append(budget)

        class Relabeller:
            wanted, rows, counts = 0, [], {}

            def __call__(self, arm, task, thread) -> None:
                for number in range(min(self.wanted, rollouts.rows_of.get(task.id, 1))):
                    self.rows.append({"id": f"dagger-{task.id}-{arm}-{number}", "decision": f"{task.id}-{number}",
                                      "request": task.id, "started_at": 1.0, "tokens": 900, "measured": True,
                                      "row": {"messages": [{"role": "user", "content": task.id},
                                                           {"role": "assistant", "content": "ok", "weight": 1}]}})

        return Relabeller()


class _TrainKit:
    def __init__(self) -> None:
        self.unloaded: list[str] = []

    def prepare(self, study_data) -> None:
        return None

    def boot(self, data) -> None:
        return None

    def teacher(self, out) -> str:
        return "model:codex:gpt-6.1-sol"

    def measure(self, data):
        return None

    def gpu(self, label=""):
        return scratch.GpuGate(None, "lane-paper", training=lambda: False, quiet=lambda: None, card=lambda: None,
                               after_quiet=lambda: False, say=lambda _text: None)

    def unload_model(self, name) -> None:
        self.unloaded.append(name)

    def finish(self) -> None:
        return None


def _out(tmp_path: Path, design: seeds.SeedsDesign = seeds.DRY_DESIGN) -> tuple[Path, Path, Path]:
    """A pinned study's out folder over the dry run's made-up study (one person, five training days, N = 9, DAgger
    rounds of 2 rows)."""

    study_out, study_data = seeds.dry_study(tmp_path / "study")
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    study.write_json(out / "design.json", {"seeds": design.to_dict(), "study_design": study.read_json(
        study_out / "design.json"), "study_out": str(study_out), "study_data": str(study_data),
        "train_data": str(tmp_path / "train" / "data")})
    return out, study_out, study_data


@pytest.fixture
def training(tmp_path, home, monkeypatch):
    """The train child over the made-up study, with a fake trainer, runner and relabeller."""

    out, study_out, _study_data = _out(tmp_path)
    trainer, rollouts, kit = _Trainer(), _Rollouts(), _TrainKit()
    made: list[int] = []

    def make_trainer(*_args, first_version, **_options):
        made.append(first_version)
        return trainer

    monkeypatch.setattr(seeds_worker, "SeedTrainer", make_trainer)
    monkeypatch.setattr(seeds_worker, "SeedsRunner", rollouts.runner)
    monkeypatch.setattr(study_worker, "Relabeller", rollouts.relabeller)
    monkeypatch.setattr(study_worker, "prepare_profile", lambda data, args: None)
    args = SimpleNamespace(out=str(out), data=str(tmp_path / "train" / "data"), cap_tokens=150_000_000, spent_before=0)
    return SimpleNamespace(child=lambda: seeds_worker.SeedTraining(args, kit), trainer=trainer, rollouts=rollouts,
                           kit=kit, out=out, study_out=study_out, made=made)


def _only_seed(recipe, seed: int):
    """The recipe with its training seed put back to ``seed``: equal to the study's when nothing else changed."""

    return dataclasses.replace(recipe, settings={**recipe.settings, "seed": seed})


def _set_rounds(study_out: Path, rounds: dict[int, list[str]]) -> None:
    state = study.read_json(study_out / "dagger" / "state.json")
    state["requests"] = [{"round": number, "request": request, "rows": 1, "status": "passed", "left_out": {},
                          "seconds": 1.0} for number, requests in rounds.items() for request in requests]
    study.write_json(study_out / "dagger" / "state.json", state)


def _order(study_out: Path) -> list[str]:
    """The study's DAgger order over its training block (``dagger.order``, its seed)."""

    from tests.e2e.learning.scaling import dagger

    design = Design.from_dict(study.read_json(study_out / "design.json"))
    requests, _owned, _people = study_worker.owners(design, "training")
    in_subset = {row["request"] for row in study_worker.subset_rows(study_out, design.sizes[2])}
    return dagger.order({person: [item.id for item in requests.values() if item.person == person]
                         for person in design.people}, in_subset, f"{design.subset_seed}/dagger")


def test_each_new_model_is_the_studys_training_with_only_its_seed_changed_in_section_4s_order(training):
    study_design = Design.from_dict(study.read_json(training.study_out / "design.json"))
    study_recipe = study_worker.recipe_for(study_design)

    assert training.child().run() == 0

    calls = training.trainer.calls
    assert [call["name"] for call in calls] == ["sft-c", "dagger-b-r1", "dagger-b", "dagger-c-r1", "dagger-c"]
    assert [call["recipe"].settings["seed"] for call in calls] == [20261121, 20261122, 20261122, 20261123, 20261123]
    for call in calls:  # the study's recipe in everything but the seed
        assert _only_seed(call["recipe"], study_recipe.settings["seed"]) == study_recipe
    # The study's sft-1000b is the same recipe with its second seed, 3408: sft-c differs from both in the seed alone.
    assert _only_seed(study_worker.recipe_for(study_design, seed=3408), 3407) == study_recipe
    assert calls[0]["rows"] == [row["id"] for row in study_worker.subset_rows(training.study_out, 9)]
    # Versions after the study's (its highest is 7), so no served name is the study's.
    assert training.made == [8] and [call["version"] for call in calls] == [8, 9, 10, 11, 12]
    # DAgger: the study's rollout settings, round 1's student start, round 2's the arm's own round-1 model.
    assert {(item["task_tokens"], item["seconds"]) for item in training.rollouts.options} == {
        (study_design.task_tokens, study_design.seconds)}
    assert [(arm, ref.rsplit(":", 1)[-1]) for _task, arm, ref in training.rollouts.runs] == [
        ("student-1", "row-bot-personal-base"), ("student-1", "row-bot-personal-base"),
        ("student-2", "row-bot-personal-v9"), ("student-2", "row-bot-personal-v9"),
        ("student-1", "row-bot-personal-base"), ("student-1", "row-bot-personal-base"),
        ("student-2", "row-bot-personal-v11"), ("student-2", "row-bot-personal-v11")]
    # The same requests for both models: their rollouts differ only through their own round-1 student.
    first, second = training.rollouts.runs[:4], training.rollouts.runs[4:]
    assert [task for task, _arm, _ref in first] == [task for task, _arm, _ref in second]
    # Each model trains on its own two rounds of 2 rows (the dry design's 500), round 1's student on round 1's.
    assert [len(call["rows"]) for call in calls[1:]] == [2, 4, 2, 4]
    assert training.rollouts.booted == 1  # one runner for the process: it patches the runtime once
    state = study.read_json(training.out / "train" / "state.json")
    assert state["models"] == {"sft-c": "sft-c", "dagger-b-r1": "dagger-b-r1", "dagger-b": "dagger-b",
                               "dagger-c-r1": "dagger-c-r1", "dagger-c": "dagger-c"}
    assert state["done"] is True


def test_a_resumed_training_trains_and_runs_nothing_again(training):
    assert training.child().run() == 0
    calls, runs = list(training.trainer.calls), list(training.rollouts.runs)

    assert training.child().run() == 0

    assert training.trainer.calls == calls and training.rollouts.runs == runs


def test_a_dagger_round_runs_the_studys_requests_first_then_requests_neither_round_ran(training):
    order = _order(training.study_out)
    _set_rounds(training.study_out, {1: order[:2], 2: order[2:3]})
    training.rollouts.rows_of = {order[0]: 1, order[1]: 3, order[2]: 1}  # round 1 needs both; round 2 one more

    assert training.child().run() == 0

    ran = [task for task, arm, _ref in training.rollouts.runs[:4]]
    assert ran == [order[0], order[1], order[2], order[3]]  # order[3]: run by neither of the study's rounds
    state = study.read_json(training.out / "train" / "dagger-b" / "state.json")
    assert state["round_1"] | {"student": None} == {"rows": 2, "requests": 2, "student": None, "the_studys_requests": 2,
                                                    "same": 2, "not_run": 0, "added": 0}
    assert (state["round_2"]["same"], state["round_2"]["not_run"], state["round_2"]["added"]) == (1, 0, 1)
    assert len({item["request"] for item in state["requests"]}) == len(state["requests"])  # never twice


def test_the_study_procedure_is_refused_when_its_recorded_rounds_arent_what_its_order_gives(training):
    order = _order(training.study_out)
    _set_rounds(training.study_out, {1: [order[1]], 2: [order[0]]})

    with pytest.raises(scratch.ScratchError, match="not the study's procedure"):
        training.child().run()
    assert training.rollouts.runs == []


def test_sft_c_refuses_a_subset_that_isnt_the_one_the_studys_sft_1000_trained_on(training):
    state = study.read_json(training.study_out / "train" / "state.json")
    state["arms"]["sft-1000"]["dataset"] = "ds_another"
    study.write_json(training.study_out / "train" / "state.json", state)

    with pytest.raises(scratch.ScratchError, match="not the same subset"):
        training.child().run()
    assert training.trainer.calls == []


def test_a_model_that_fails_the_ready_check_is_retrained_once_with_seed_20261124(training):
    training.trainer.outcomes = {"dagger-b": "couldnt_start"}

    assert training.child().run() == 0

    names = [call["name"] for call in training.trainer.calls]
    assert names == ["sft-c", "dagger-b-r1", "dagger-b", "dagger-b@20261124", "dagger-c-r1", "dagger-c"]
    retrained = training.trainer.calls[3]
    assert retrained["recipe"].settings["seed"] == 20261124
    assert retrained["rows"] == training.trainer.calls[2]["rows"]  # the same rows: only the seed changes
    state = study.read_json(training.out / "train" / "state.json")
    assert state["models"]["dagger-b"] == "dagger-b@20261124"
    assert state["retrained"]["dagger-b"]["why"] == "dagger-b couldnt_start"
    records = seeds.training_records(training.out, training.study_out)
    assert records["dagger-b"]["evaluated_as"] == "dagger-b@20261124"
    assert list(records["dagger-b"]["retrained"]) == ["dagger-b"]


@pytest.mark.parametrize(("outcomes", "word"), [({"sft-c": "couldnt_start", "sft-c@20261124": "couldnt_start"},
                                                 "retrained once"),
                                                ({"sft-c": "failed"}, "training run failed")])
def test_a_second_failure_or_a_failed_training_run_stops_the_study(training, outcomes, word):
    training.trainer.outcomes = outcomes

    with pytest.raises(scratch.ScratchError, match=word):
        training.child().run()
    assert [call["name"] for call in training.trainer.calls][-1].startswith("sft-c")
    assert training.rollouts.runs == []


def test_the_training_profile_links_the_engine_but_never_the_servers_run_folder_or_caches(tmp_path):
    source = tmp_path / "study" / "runtimes" / "learning"
    for relative, content in (("engine/lib/torch.dll", b"dll"), ("engine/engine.json", b"{}"),
                              ("models/qwen/model.safetensors", b"weights"), ("server/bin/llama-server.exe", b"exe"),
                              ("server/run/server.log", b"log"), ("server/run/models.json", b"{}"),
                              ("cache/compiled.bin", b"cache")):
        (source / relative).parent.mkdir(parents=True, exist_ok=True)
        (source / relative).write_bytes(content)
    data = tmp_path / "train" / "data"

    seeds_worker.link_runtimes(tmp_path / "study", data)

    target = data / "runtimes" / "learning"
    assert os.path.samefile(target / "engine" / "lib" / "torch.dll", source / "engine" / "lib" / "torch.dll")
    assert os.path.samefile(target / "models" / "qwen" / "model.safetensors", source / "models" / "qwen" /
                            "model.safetensors")
    assert not os.path.samefile(target / "engine" / "engine.json", source / "engine" / "engine.json")  # a copy
    assert not (target / "server" / "run").exists() and not (target / "cache").exists()
    assert (target / "server" / "bin" / "llama-server.exe").is_file()


# ── Evaluation: the six arms in their fixed order ────────────────────────────


class _Runner:
    """The study's runner as the evaluation loop sees it: each run passes, its arm recorded."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.gpu_seconds: dict[str, float] = {}
        self.arms: dict[str, runner.Spec] = {}
        self.on_run = None
        self.unload = lambda _arm: None
        self._arm: str | None = None

    def run_task(self, task, arm):
        from row_bot.learning.evaluation import CaseResult

        self.calls.append((task.id, arm))
        self._arm = arm
        case = CaseResult(task.id, "pack", "", "passed", "tests", 1_000, 1_000, 3, 4.0, "", "")
        if self.on_run is not None:
            self.on_run(arm, case)
        return runner.Run(case, "thread")

    def finish(self) -> None:
        if self._arm:
            self.unload(self._arm)
        self._arm = None


class _EvalKit:
    def __init__(self) -> None:
        self.served: list[tuple[str, int, str]] = []
        self.windows: dict[str, int] = {}
        self.unloaded: list[str] = []

    def prepare(self, study_data) -> None:
        return None

    def boot(self, data) -> None:
        return None

    def gpu(self):
        return None

    def serve_arm(self, arm, model, context, gpu):
        ref, name, gguf = model
        self.served.append((arm, context, name))
        self.windows[ref] = context
        return {"model_ref": ref, "name": name, "gguf": str(gguf), "context": context}

    def unload(self, name) -> None:
        self.unloaded.append(name)

    def finish(self) -> None:
        return None


REQUEST = SimpleNamespace(id="mara-d401-0", person="mara", day=401, task=SimpleNamespace(id="mara-d401-0"))


@pytest.fixture
def evaluation(tmp_path, home, monkeypatch):
    """The local child with a fake kit and runner: training done, the six stand-in files logged."""

    from row_bot import agent

    monkeypatch.setattr(agent, "SMALL_PROFILE_TOKENS", agent.SMALL_PROFILE_TOKENS)  # restored after the test
    monkeypatch.setattr(replicate_worker, "_PRODUCT_SMALL_PROFILE", None)
    out = tmp_path / "out"
    study.write_json(out / "design.json", {"seeds": dataclasses.replace(seeds.DRY_DESIGN, runs=2).to_dict(),
                                           "study_design": {}, "study_out": str(tmp_path / "study" / "out"),
                                           "study_data": str(tmp_path / "study" / "data"),
                                           "train_data": str(tmp_path / "train" / "data")})
    study.write_json(out / "train" / "state.json", {"done": True})
    models = {}
    for arm, name in NAMES.items():
        path = tmp_path / "models" / f"{name}-Q8_0.gguf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"stand-in {arm}".encode())
        models[arm] = (f"model:{PROVIDER}:{name}", name, path)
    seeds.check_models(out, {arm: item[2] for arm, item in models.items()}, before="evaluate", record=True)
    kit, fake = _EvalKit(), _Runner()

    def start(self, arms, gpu):
        self.runner = fake
        return {REQUEST.id: REQUEST}, [REQUEST.id]

    monkeypatch.setattr(seeds_worker, "arm_models", lambda *_args: dict(models))
    monkeypatch.setattr(study_worker, "prepare_profile", lambda data, args: None)
    monkeypatch.setattr(seeds_worker.SeedsRuns, "start", start)
    monkeypatch.setattr(replicate_worker, "sees", lambda ref: (kit.windows[ref], True))
    args = SimpleNamespace(out=str(out), data=str(tmp_path / "local" / "data"), cap_tokens=150_000_000, spent_before=0)
    return SimpleNamespace(runs=seeds_worker.SeedsRuns(args, kit), kit=kit, runner=fake, models=models, out=out)


def test_the_arms_are_evaluated_in_order_each_in_full_at_65536_tokens_and_a_resume_reruns_nothing(evaluation):
    assert evaluation.runs.local() == 0

    order = ["dagger-a", "sft-a", "dagger-b", "sft-b", "dagger-c", "sft-c"]
    assert evaluation.kit.served == [(arm, 65_536, NAMES[arm]) for arm in order]
    assert [arm for _task, arm in evaluation.runner.calls] == [arm for arm in order for _ in range(2)]
    assert evaluation.kit.unloaded == [NAMES[arm] for arm in order]  # each one unloaded before the next is served
    state = study.read_json(evaluation.out / "local" / "state.json")
    assert {arm: state["serving"][arm]["small_profile_tokens"] for arm in order} == dict.fromkeys(order, 65_536)
    assert state["done"] is True

    evaluation.runner.calls.clear()
    evaluation.kit.served.clear()
    assert evaluation.runs.local() == 0
    assert evaluation.runner.calls == [] and evaluation.kit.served == []


def test_the_evaluation_waits_for_the_new_models(evaluation):
    study.write_json(evaluation.out / "train" / "state.json", {"done": False})

    with pytest.raises(scratch.ScratchError, match="trained"):
        evaluation.runs.local()
    assert evaluation.kit.served == []


# ── The model files' hashes ──────────────────────────────────────────────────


def test_the_local_child_stops_at_a_changed_file_before_serving_it(evaluation):
    evaluation.models["sft-b"][2].write_bytes(b"retrained")

    with pytest.raises(replicate.ModelChanged, match="sft-b"):
        evaluation.runs.local()
    assert [arm for arm, _context, _name in evaluation.kit.served] == ["dagger-a", "sft-a", "dagger-b"]


def test_a_changed_model_file_stops_the_study_before_its_next_run(tmp_path, home, quick_block, monkeypatch):
    started: list[list[str]] = []

    def run_child(command, env, log):
        started.append(command)
        if command[3] == "train":
            study.write_json(tmp_path / "out" / "train" / "state.json", {"done": True})
        return 0

    new = {}
    for arm in seeds.NEW:
        path = tmp_path / "trained" / f"{NAMES[arm]}-Q8_0.gguf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"stand-in {arm}".encode())
        new[arm] = (f"model:{PROVIDER}:{NAMES[arm]}", NAMES[arm], path)
    monkeypatch.setattr(seeds, "new_files", lambda out, data: dict(new))
    found = seeds.Seeds(_dry(tmp_path), runner=run_child, build=lambda: dict(BUILD), say=lambda _text: None)
    found.pin()

    found.stage_train()  # the study's three logged before training, the new three once they are ready

    logged = study.read_json(tmp_path / "out" / "models.json")
    assert sorted(logged["models"]) == sorted(seeds.ARMS)
    assert [(item["before"], item["logged"]) for item in logged["checks"]] == [
        ("train", ["sft-a", "sft-b", "dagger-a"]), ("evaluate", ["sft-c", "dagger-b", "dagger-c"])]
    new["dagger-c"][2].write_bytes(b"another file")
    with pytest.raises(replicate.ModelChanged, match="dagger-c"):
        found.stage_evaluate()
    assert [command[3] for command in started] == ["train"]
    with pytest.raises(replicate.ModelChanged, match="no logged hash"):
        seeds.check_models(tmp_path / "elsewhere", {"sft-a": new["sft-c"][2]}, before="serving sft-a")


# ── T1 and its permutation ───────────────────────────────────────────────────

#: Per task: its cluster, whether its wording is repeated, and each arm's one run (1 passed, 0 failed, None an outage).
#: d_i = (dagger passes - sft passes) / 3: T0 +2/3, T1 -1/3, T2 +1/3, T3 +2/3, T4 -1/3; T5 isn't counted in dagger-c.
TABLE = {
    "mara-d401-0": ("mara/d401", True, {"sft-a": 0, "sft-b": 0, "sft-c": 1, "dagger-a": 1, "dagger-b": 1, "dagger-c": 1}),
    "mara-d401-1": ("mara/d401", True, {"sft-a": 1, "sft-b": 1, "sft-c": 1, "dagger-a": 1, "dagger-b": 1, "dagger-c": 0}),
    "mara-d402-0": ("mara/d402", False, {"sft-a": 0, "sft-b": 0, "sft-c": 0, "dagger-a": 1, "dagger-b": 0,
                                         "dagger-c": 0}),
    "wren-d401-0": ("wren/d401", False, {"sft-a": 0, "sft-b": 1, "sft-c": 0, "dagger-a": 1, "dagger-b": 1,
                                         "dagger-c": 1}),
    "idris-d410-0": ("idris/d410", False, {"sft-a": 1, "sft-b": 0, "sft-c": 0, "dagger-a": 0, "dagger-b": 0,
                                           "dagger-c": 0}),
    "idris-d411-0": ("idris/d411", False, {"sft-a": 1, "sft-b": 1, "sft-c": 1, "dagger-a": 1, "dagger-b": 1,
                                           "dagger-c": None}),
}
FRESH = {task: {"person": task.split("-")[0], "day": int(cluster.split("/d")[1]), "cluster": cluster,
                "template": "arithmetic", "repeated": repeated} for task, (cluster, repeated, _arms) in TABLE.items()}
OUTAGE = "the model call failed: ⚠️ The AI provider had a server error — please try again shortly."


def _runs(swapped: set[str] = frozenset(), swap=None) -> list[dict]:
    """Every arm's run of every task; the clusters in ``swapped`` with ``swap``'s arm labels exchanged (default: every
    dagger-k with sft-k, the three pairs together)."""

    swap = swap or {**{f"dagger-{k}": f"sft-{k}" for k in "abc"}, **{f"sft-{k}": f"dagger-{k}" for k in "abc"}}
    found = []
    for task, (cluster, _repeated, arms) in TABLE.items():
        for arm, passed in arms.items():
            label = swap.get(arm, arm) if cluster in swapped else arm
            status = "error" if passed is None else ("passed" if passed else "failed")
            found.append({"arm": label, "task": task, "repeat": 0, "cluster": cluster, "status": status,
                          "cause": "" if passed else "wrong_answer", "detail": OUTAGE if passed is None else "",
                          "tokens": 1_000, "local_tokens": 1_000, "steps": 3, "seconds": 4.0,
                          "reran_after_outage": passed is None, "unrecovered": []})
    return found


def _report(runs: list[dict], **design) -> dict:
    found = dataclasses.replace(seeds.DESIGN, resamples=200, permutations=200, **design)
    return seeds_analysis.build_report(runs, design=found, fresh=FRESH, local={}, training={})


def test_t1_is_the_mean_of_the_dagger_arms_minus_the_mean_of_the_sft_arms_on_tasks_counted_in_all_six():
    report = _report(_runs(), min_tasks=5)

    entry = report["confirmatory"]["T1"]
    assert entry["analysis"]["problems"] == 5 and entry["analysis"]["clusters"] == 4  # idris-d411-0 isn't in all six
    assert entry["analysis"]["difference"] == pytest.approx(0.2)  # (2/3 - 1/3 + 1/3 + 2/3 - 1/3) / 5
    # Cluster sums 1/3, 1/3, 2/3 and -1/3: 8 of the 16 sign patterns reach |total| >= 1, listed exactly.
    assert entry["analysis"]["exact"] is True and entry["analysis"]["p_value"] == pytest.approx(0.5)
    assert entry["behind"]["runs"] == dict.fromkeys(seeds.ARMS, 5) and entry["behind"]["clusters"] == 4
    assert entry["tested"] is True and entry["verdict"] == "does not hold"
    spread = report["secondary"]["spread"]
    assert spread["sft"]["rates"] == {"sft-a": 0.4, "sft-b": 0.4, "sft-c": 0.4} and spread["sft"]["range"] == 0
    assert spread["dagger"]["rates"] == {"dagger-a": 0.8, "dagger-b": 0.6, "dagger-c": 0.4}
    assert (spread["dagger"]["range"], spread["dagger"]["sd"]) == (pytest.approx(0.4), pytest.approx(0.2))
    assert spread["between_methods"] == pytest.approx(0.2)
    pairs = report["secondary"]["pairings"]
    assert len(pairs) == 9 and [name for name, item in pairs.items() if item["original"]] == ["dagger-a − sft-a"]
    assert pairs["dagger-a − sft-a"]["problems"] == 6 and pairs["dagger-a − sft-a"]["difference"] == pytest.approx(
        2 / 6)  # each pairing on the tasks both arms counted
    words = report["secondary"]["new_wording"]["T1"]
    assert words["analysis"]["problems"] == 3 and words["analysis"]["difference"] == pytest.approx(2 / 9)
    assert "verdict" not in words
    assert report["fresh_block"] == {"requests": 6, "person_days": 5, "repeated_wording": 2, "new_wording": 4}
    assert _report(_runs(), min_tasks=6)["confirmatory"]["T1"]["verdict"] == "not tested"


def test_t1s_permutation_swaps_a_clusters_three_method_labels_together():
    observed = _report(_runs(), min_tasks=1)["confirmatory"]["T1"]["analysis"]
    clusters = sorted({cluster for cluster, _repeated, _arms in TABLE.values()} - {"idris/d411"})
    scores = {arm: seeds_analysis.analysis.scores([item for item in _runs() if item["arm"] == arm]) for arm in seeds.ARMS}
    common = seeds_analysis.counted_in_all(scores)
    dagger, sft = seeds_analysis.method_means(scores, common)
    d = {task: dagger[task] - sft[task] for task in common}

    totals = []
    for count in range(len(clusters) + 1):
        for swapped in itertools.combinations(clusters, count):
            found = _report(_runs(set(swapped)), min_tasks=1)["confirmatory"]["T1"]["analysis"]
            total = found["difference"] * found["problems"]
            # Swapping a cluster's labels flips the sign of its d_i, and only its own.
            assert total == pytest.approx(sum(-d[task] if FRESH[task]["cluster"] in swapped else d[task]
                                               for task in common), abs=1e-4)  # the report rounds to 6 places
            totals.append(total)
    total = observed["difference"] * observed["problems"]
    assert len(totals) == 16
    assert observed["p_value"] == pytest.approx(sum(abs(item) >= abs(total) - 1e-4 for item in totals) / len(totals))
    # One pair swapped alone is not that: mara/d401's first task goes from +2/3 to 0, not to -2/3.
    alone = _report(_runs({"mara/d401"}, swap={"dagger-a": "sft-a", "sft-a": "dagger-a"}), min_tasks=1)
    assert alone["confirmatory"]["T1"]["analysis"]["difference"] * 5 == pytest.approx(total - 2 / 3, abs=1e-4)


# ── The card ─────────────────────────────────────────────────────────────────


def test_training_gives_the_card_back_to_another_lanes_exclusive_window(tmp_path):
    booking = tmp_path / "gpu-booking.txt"
    booking.write_text("09:00 lane-paper START seeds: training sft-c\n"
                       "09:30 lane-graph EXCLUSIVE START the graph lane's night\n", encoding="utf-8")
    stopped: list[str] = []
    trainer = seeds_worker.SeedTrainer.__new__(seeds_worker.SeedTrainer)
    trainer.gpu = scratch.GpuGate(booking, "lane-paper", training=lambda: False, quiet=lambda: None)
    trainer.supervisor = SimpleNamespace(jobs=SimpleNamespace(get=lambda _job: SimpleNamespace(state="running")),
                                         stop_now=stopped.append)

    assert trainer._watch("job_1") is True  # noqa: SLF001
    assert stopped == ["local_gpu"]


def test_a_rollout_or_an_arm_gives_the_card_back_with_its_model_unloaded(tmp_path):
    booking = tmp_path / "gpu-booking.txt"
    booking.write_text("09:00 lane-paper START seeds: student-1 runs\n", encoding="utf-8")
    events: list[str] = []

    class Gate(scratch.GpuGate):
        def release(self) -> None:
            events.append("release")
            super().release()

        def acquire(self, what: str, expect: str = "") -> None:
            events.append(f"acquire {what}")

    quiet = {"now": None}
    gate = Gate(booking, "lane-paper", training=lambda: False, quiet=lambda: quiet["now"])
    gate.held = "seeds: student-1 runs"
    found = SimpleNamespace(_arm="student-1", arms={"student-1": runner.Spec("student-1", "model:x", local=True)},
                            gpu=gate, unload=lambda arm: events.append(f"unload {arm}"))

    seeds_worker.SeedsRunner.card(found, "student-1")
    assert events == ["acquire seeds: student-1 runs"]
    events.clear()
    quiet["now"] = "quiet hours"
    seeds_worker.SeedsRunner.card(found, "student-1")
    assert events == ["unload student-1", "release", "acquire seeds: student-1 runs"]


# ── The pin, the draft and the folders ───────────────────────────────────────


def test_a_real_run_is_refused_while_the_pre_registration_is_a_draft(tmp_path, home):
    profile = tmp_path / "eval-profile"
    profile.mkdir()
    started: list[list[str]] = []
    args = seeds.parse(["--study-out", str(tmp_path / "study" / "out"), "--study-data", str(tmp_path / "study" / "data"),
                        "--eval-profile", str(profile), "--gpu-booking", str(tmp_path / "booking.txt"),
                        "--out", str(tmp_path / "seeds" / "out"), "--scratch", str(tmp_path / "seeds" / "scratch")])

    assert seeds.PREREG_VERSION == 0
    with pytest.raises(scratch.ScratchError, match="draft"):
        seeds.Seeds(args, runner=lambda command, env, log: started.append(command) or 0).run(["train"])
    assert started == [] and not (tmp_path / "seeds" / "out").exists()


def test_the_build_pin_refuses_another_build_and_a_stage_on_it_runs_nothing(tmp_path, home, quick_block):
    def build(name: str, code: str = "1"):
        return lambda: {"commit": name * 40, "code_hash": code * 64}

    seeds.Seeds(_dry(tmp_path), build=build("a")).pin()

    assert seeds.Seeds(_dry(tmp_path), build=build("a")).pin()["commit"] == "a" * 40
    with pytest.raises(scratch.ScratchError, match="code_hash"):
        seeds.Seeds(_dry(tmp_path), build=build("a", "2")).pin()
    started: list[list[str]] = []
    with pytest.raises(scratch.ScratchError, match="commit"):
        seeds.Seeds(_dry(tmp_path), build=build("b"),
                    runner=lambda command, env, log: started.append(command) or 0).run(["train"])
    assert started == []


def test_it_never_writes_in_the_studys_folders(tmp_path, home):
    root = tmp_path / "study"
    (root / "out").mkdir(parents=True)
    (root / "scratch" / "study" / "data").mkdir(parents=True)

    def args(out: Path):
        return seeds.parse(["--study-out", str(root / "out"), "--study-data", str(root / "scratch" / "study" / "data"),
                            "--eval-profile", str(tmp_path / "eval"), "--gpu-booking", str(tmp_path / "b.txt"),
                            "--out", str(out), "--scratch", str(tmp_path / "seeds" / "scratch")])

    with pytest.raises(scratch.ScratchError, match="study's own folders"):
        seeds.Seeds(args(root / "out" / "seeds")).find_study()


# ── The dry run ──────────────────────────────────────────────────────────────


@pytest.mark.slow  # two child processes, five fake trainings and 18 scripted Agent turns (about a minute)
def test_the_dry_run_trains_evaluates_and_analyses_every_arm_and_a_second_run_reruns_nothing(tmp_path, home):
    def make() -> seeds.Seeds:
        return seeds.Seeds(_dry(tmp_path), build=lambda: dict(BUILD), say=lambda _text: None)

    assert make().run(list(seeds.STAGES)) == 0

    out = tmp_path / "out"
    runs = study_worker.read_lines(out / "local" / "runs.jsonl")
    assert sorted({item["arm"] for item in runs}) == sorted(seeds.ARMS) and len(runs) == 6 * 3
    trained = study.read_json(out / "train" / "state.json")
    assert {key: entry["seed"] for key, entry in trained["arms"].items()} == {
        "sft-c": 20261121, "dagger-b-r1": 20261122, "dagger-b": 20261122, "dagger-c-r1": 20261123, "dagger-c": 20261123}
    report = study.read_json(out / "analyse" / "report.json")
    assert report["confirmatory"]["T1"]["analysis"]["problems"] == 3
    assert sorted(study.read_json(out / "models.json")["models"]) == sorted(seeds.ARMS)

    assert make().run(list(seeds.STAGES)) == 0
    assert study_worker.read_lines(out / "local" / "runs.jsonl") == runs
    assert study.read_json(out / "train" / "state.json")["arms"] == trained["arms"]
