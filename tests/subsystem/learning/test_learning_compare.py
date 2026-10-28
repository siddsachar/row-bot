"""Comparing a personal-model version (L2c2; UX §5 "Versions"): the new version, its baseline (the
untouched starting model for v1) and the main model, at the same budget (E12), through the
real-runtime worker (faked here), on the visible packs and the hidden split (aggregates only, E10);
coding tasks only with a container (O21); the main model's results kept for 30 days (O31); a clean
stop at the monthly limit; plain verdicts, including no clear difference and worse."""

from __future__ import annotations

import json
import threading

import pytest

from row_bot.learning import model_compare
from row_bot.learning.spending import TokenMeter
from row_bot.learning.store import LearningStore
from row_bot.learning.training import releases
from tests.fixtures.learning import save_learning_settings

pytestmark = [pytest.mark.subsystem]

PERSONAL = "model:ollama:row-bot-personal-v1:abcd1234"
BASE = "model:ollama:row-bot-personal-base:abcd1234"
MAIN = "model:openai:gpt-6.1-sol"
HIDDEN_IDS = ("hdoc_", "hst_", "hcode_", "htool_")


class FakeWorker:
    """The evaluation worker: the new version passes document lookups and tool workflows, the
    starting model passes a quarter of the tasks, the main model passes everything."""

    instances: list[FakeWorker] = []

    def __init__(self, spec, credentials, root):
        self.spec, self.credentials, self.root = spec, credentials, root
        self.asked: list[tuple[str, str, dict]] = []
        self.started = self.stopped = False
        self.notes: list[str] = []
        self.write_backs: list[str] = []
        FakeWorker.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True
        return True

    def run(self, task, arm, *, budget, timeout=0):
        self.asked.append((task["id"], arm, dict(budget)))
        pack = task["pack"]
        if arm == "main":
            passed = True
        elif arm.startswith("v"):
            passed = pack in ("document_lookups", "tool_workflows")
        else:
            passed = sum(map(ord, task["id"])) % 4 == 0
        cloud = arm == "main"
        return {"type": "result", "case": {
            "task_id": task["id"], "status": "passed" if passed else "failed", "check": "answer",
            "tokens": 2000, "local_tokens": 0 if cloud else 2000, "steps": 3, "seconds": 9.0 if cloud else 3.0,
            "detail": "", "cause": "" if passed else "wrong_answer"}, "usage": [{"provider": "openai" if cloud else "ollama", "model": arm, "input": 1800,
                                      "output": 200}]}


@pytest.fixture
def ready(learning_on):
    LearningStore(learning_on).create()
    releases.write_state(releases.version_dir(1, learning_on), releases.VersionState(
        version=1, phase=releases.READY, job_id="job_1", model_ref=PERSONAL))
    FakeWorker.instances.clear()
    return learning_on


def _comparer(root, *, container=False, limit=10_000_000, plan=None, recorded=None):
    # Hidden problems run once here (the product runs them three times, O88: the tests that need the
    # repeats ask for them), so these tests stay quick.
    return model_compare.Comparer(
        root, plan=plan or model_compare.Plan(visible_per_pack=2, hidden_repeats=1), worker_factory=FakeWorker,
        gather=lambda arms: ([], {"custom_endpoints": [], "local_model_settings": {}}, []),
        tools=lambda: ["filesystem", "calculator", "shell"], container=lambda: container,
        baseline_model=lambda runtime: (BASE, None), main_ref=lambda: MAIN, model_name=lambda ref: "GPT-6.1 Sol",
        meter=lambda: TokenMeter(limit), record_usage=(recorded.extend if recorded is not None else lambda rows: None),
    )


def test_a_version_is_compared_with_the_starting_model_and_the_main_model(ready):
    recorded: list[dict] = []
    result = _comparer(ready, recorded=recorded).run(1)

    assert [arm["key"] for arm in result["arms"]] == ["base", "v1", "main"]
    assert [arm["role"] for arm in result["arms"]] == ["baseline", "new", "main"]
    rows = {row["kind"]: row for row in result["rows"]}
    assert set(rows) == {"document_lookups", "structured_transforms", "tool_workflows"}  # no container: no coding
    docs = rows["document_lookups"]
    assert docs["total"] == 2 + 8  # two visible and the eight hidden document tasks
    assert docs["passed"]["main"] == 10 and docs["passed"]["v1"] == 10 and docs["verdict"] == "better"
    assert rows["structured_transforms"]["passed"]["v1"] == 0
    main = next(arm for arm in result["arms"] if arm["key"] == "main")
    new = next(arm for arm in result["arms"] if arm["key"] == "v1")
    assert (main["seconds_per_task"], main["cloud_tokens_per_task"]) == (9.0, 2000)
    assert (new["seconds_per_task"], new["cloud_tokens_per_task"]) == (3.0, 0)  # local models are free
    assert result["overall"]["verdict"] == "mixed" and result["overall"]["recommend"] in ("document_lookups",
                                                                                         "tool_workflows")
    assert model_compare.read_comparison(1, ready) == result
    assert recorded and all(row["provider"] in ("openai", "ollama") for row in recorded)  # the person's ledger
    worker = FakeWorker.instances[0]
    assert worker.started and worker.stopped
    assert "shell" not in worker.spec["tools"] and "filesystem" in worker.spec["tools"]


def test_a_main_model_that_cant_run_the_tasks_is_left_out_and_the_result_says_so(ready, monkeypatch):
    # The 5070 e2e met this: a local main model below Agent Mode's context ran no task at all.
    def run(self, task, arm, *, budget, timeout=0):
        if arm == "main":
            return {"type": "result", "case": {
                "task_id": task["id"], "status": "error", "check": "answer", "tokens": 0, "local_tokens": 0,
                "steps": 0, "seconds": 0.1, "detail": "the model call failed: not Agent-ready"}, "usage": []}
        return original(self, task, arm, budget=budget, timeout=timeout)

    original = FakeWorker.run
    monkeypatch.setattr(FakeWorker, "run", run)

    result = _comparer(ready).run(1)

    assert [arm["key"] for arm in result["arms"]] == ["base", "v1"]
    assert result["main_unavailable"] == "main_failed"
    assert all("main" not in row["passed"] for row in result["rows"])
    assert "main" not in result["details"]["visible"] and "main" not in result["details"]["hidden"]
    assert result["overall"]["verdict"] in ("better", "mixed")  # still v1 against its starting model


def test_every_model_runs_the_same_tasks_in_the_same_order_with_the_same_budget(ready):
    _comparer(ready).run(1)

    asked = FakeWorker.instances[0].asked
    by_arm: dict[str, list[str]] = {}
    for task, arm, budget in asked:
        by_arm.setdefault(arm, []).append(task)
        assert budget["task_tokens"] == model_compare.Plan().task_tokens
    assert by_arm["base"] == by_arm["v1"] == by_arm["main"]
    # Interleaved task by task, so a stop cuts every model at the same task.
    assert [arm for _task, arm, _budget in asked[:3]] == ["base", "v1", "main"]


def test_the_hidden_split_comes_back_as_counts_only(ready):
    result = _comparer(ready).run(1)

    text = json.dumps(result) + model_compare.comparison_path(1, ready).read_text(encoding="utf-8")
    assert not any(marker in text for marker in HIDDEN_IDS)
    assert result["hidden"] and result["details"]["hidden"]["v1"]["Document lookups"] == [8, 8]
    from row_bot.learning.evaluation import audit_looks

    looks = [look for look in audit_looks(store=LearningStore(ready)) if "personal model comparison" in look.asked_by]
    assert looks and all(look.caller == "row_bot.learning.evaluation" for look in looks)
    assert any("row_bot.learning.model_compare" in look.asked_by for look in looks)


def test_hidden_problems_run_three_times_and_only_the_paired_analysis_comes_back(ready):
    result = _comparer(ready, plan=model_compare.Plan(visible_per_pack=2)).run(1)

    runs: dict[tuple[str, str], int] = {}
    for task, arm, _budget in FakeWorker.instances[-1].asked:
        runs[(task, arm)] = runs.get((task, arm), 0) + 1
    hidden = {key: count for key, count in runs.items() if key[0].startswith(HIDDEN_IDS)}
    assert hidden and set(hidden.values()) == {3}  # every hidden problem, three times per model (O88)
    assert {count for key, count in runs.items() if key not in hidden} == {1}  # visible tasks once
    # A problem counts once in the table, as its pass fraction; the verdict rule is unchanged.
    docs = next(row for row in result["rows"] if row["kind"] == "document_lookups")
    assert docs["total"] == 2 + 8 and docs["verdict"] == "better" and docs["interval_verdict"] == "better"
    assert result["overall"]["interval_verdict"] in ("better", "unclear", "worse")
    # The fake answers the same every time, as a greedy runtime would: the result says so.
    assert result["repeats"]["visible"] == 1 and result["repeats"]["hidden"] == 3
    assert all(item["identical"] == item["problems"] > 0 for item in result["repeats"]["identical"].values())
    analysis = result["analysis"]
    assert set(analysis) >= {"visible", "hidden", "combined", "seed", "resamples", "permutations"}
    both = analysis["combined"]["v1"]
    assert both["overall"]["problems"] == 30 and both["by_kind"]["document_lookups"]["problems"] == 10
    assert both["by_kind"]["document_lookups"]["low"] > 0
    assert not any(marker in json.dumps(analysis) for marker in HIDDEN_IDS)


def test_each_model_says_why_its_tasks_didnt_pass_hidden_ones_as_counts(ready):
    result = _comparer(ready, plan=model_compare.Plan(visible_per_pack=2)).run(1)

    arms = {arm["key"]: arm for arm in result["arms"]}
    assert arms["main"]["causes"] == {}
    # Causes count runs: visible tasks ran once, hidden problems three times (O88).
    failed = {key: sum(total - passed for passed, total in result["details"]["visible"][key].values())
              + 3 * sum(total - passed for passed, total in result["details"]["hidden"][key].values())
              for key in ("base", "v1")}
    assert arms["v1"]["causes"] == {"wrong_answer": failed["v1"]} and failed["v1"] > 0
    assert arms["base"]["causes"] == {"wrong_answer": failed["base"]}  # visible and hidden together


def test_coding_tasks_run_only_with_a_container(ready):
    without = _comparer(ready, container=False).run(1)
    asked = {task for task, _arm, _budget in FakeWorker.instances[-1].asked}
    assert not any(task.startswith(("code_", "hcode_")) for task in asked)
    assert without["left_out"] == [model_compare.CODING_NOTE]

    model_compare.comparison_path(1, ready).unlink()
    with_container = _comparer(ready, container=True).run(1)
    asked = {task for task, _arm, _budget in FakeWorker.instances[-1].asked}
    assert any(task.startswith("code_") for task in asked) and any(task.startswith("hcode_") for task in asked)
    assert with_container["left_out"] == [] and "small_coding" in {row["kind"] for row in with_container["rows"]}


def test_the_main_models_results_are_kept_so_a_comparison_doesnt_spend_tokens_again(ready):
    _comparer(ready).run(1)
    first = FakeWorker.instances[-1].asked
    model_compare.comparison_path(1, ready).unlink()

    again = _comparer(ready).run(1)

    second = FakeWorker.instances[-1].asked
    assert [arm for _task, arm, _ in second if arm == "main"] == []  # nothing asked of the main model again
    assert len([item for item in first if item[1] == "main"]) > 0
    main = next(arm for arm in again["arms"] if arm["key"] == "main")
    assert main["cached"] == main["ran"] > 0 and "results were reused" in again["note"]
    assert next(row for row in again["rows"] if row["kind"] == "document_lookups")["passed"]["main"] == 10


def test_the_comparison_stops_cleanly_at_the_monthly_limit(ready):
    result = _comparer(ready, limit=5_000).run(1)

    assert "Stopped: Learning reached this month’s token limit" in result["note"] or \
        "Stopped: Learning reached this month's token limit" in result["note"]
    assert result["stopped"] and not result["hidden"]  # the hidden split isn't started after a stop
    asked = FakeWorker.instances[-1].asked
    main_tasks = [task for task, arm, _ in asked if arm == "main"]
    assert len(main_tasks) <= 2  # never past the limit


def test_a_running_comparison_says_how_far_it_got_with_a_real_denominator(ready):
    seen: list[dict] = []

    class Watching(FakeWorker):
        def run(self, task, arm, *, budget, timeout=0):
            seen.append(model_compare.read_progress(1, ready) or {})
            return super().run(task, arm, budget=budget, timeout=timeout)

    comparer = _comparer(ready, plan=model_compare.Plan(visible_per_pack=2))
    comparer._worker_factory = Watching  # noqa: SLF001
    comparer.run(1)

    # Three models, six visible tasks once and 24 hidden problems three times each (O88).
    assert seen[0]["state"] == "running" and seen[0]["total"] == 3 * (6 + 24 * 3)
    assert seen[-1]["done"] == 3 * (6 + 24 * 3) - 1
    assert model_compare.read_progress(1, ready)["state"] == "done"


def test_someone_using_row_bot_stops_the_comparison_and_it_runs_again_later(ready):
    comparer = _comparer(ready)
    comparer._busy = lambda: True  # noqa: SLF001

    with pytest.raises(model_compare.CompareError) as stopped:
        comparer.run(1)

    assert stopped.value.code == "in_use" and FakeWorker.instances[-1].stopped
    assert model_compare.read_comparison(1, ready) is None
    assert model_compare.read_progress(1, ready)["state"] == "paused"


def test_a_comparison_never_records_the_persons_work_or_writes_a_lesson(ready):
    store = LearningStore(ready)

    _comparer(ready).run(1)

    for kind in ("episode", "decision", "signal", "lesson"):
        assert store.find(kind, limit=10) == [], kind
    assert store.find("test_run", limit=100)  # its own test runs are saved


def test_a_version_that_isnt_ready_isnt_compared(learning_on):
    LearningStore(learning_on).create()
    with pytest.raises(model_compare.CompareError) as refused:
        _comparer(learning_on).run(1)
    assert refused.value.code == "not_ready"


@pytest.mark.parametrize(("new", "baseline", "total", "expected"), [
    (21, 15, 24, "better"), (16, 15, 24, "same"), (12, 15, 24, "worse"), (3, 1, 4, "too_few"), (9, 8, 10, "same"),
])
def test_a_difference_counts_only_when_it_is_clear(new, baseline, total, expected):
    assert model_compare.verdict(new, baseline, total) == expected


def _arms():
    return [{"key": "main", "label": "GPT-6.1 Sol", "role": "main"},
            {"key": "base", "label": "the starting model", "role": "baseline"},
            {"key": "v1", "label": "v1 (new)", "role": "new"}]


def test_no_clear_difference_and_worse_results_are_said_plainly():
    same_rows, same = model_compare.summarize(_arms(), {
        "main": {"document_lookups": [20, 24]}, "base": {"document_lookups": [15, 24]},
        "v1": {"document_lookups": [16, 24]}}, new="v1", baseline="base", baseline_label="the starting model")
    assert same == {"verdict": "no_difference", "recommend": None,
                    "line": "No clear difference from the starting model. Not used."}
    assert same_rows[0]["verdict"] == "same"

    _rows, worse = model_compare.summarize(_arms(), {
        "main": {"structured_transforms": [20, 24]}, "base": {"structured_transforms": [15, 24]},
        "v1": {"structured_transforms": [9, 24]}}, new="v1", baseline="base", baseline_label="the starting model")
    assert worse["verdict"] == "worse" and worse["recommend"] is None
    assert worse["line"] == "Worse than the starting model at structured transforms (9 of 24, against 15). Not used."


def test_better_only_at_the_persons_own_work_is_said_and_nothing_is_offered():
    # The 5070 e2e's result: v1 learned the person's own work (6 of 11 held-back turns against 0) and
    # no task pack moved.
    rows, overall = model_compare.summarize(_arms(), {
        "main": {"document_lookups": [1, 10], "own_work": [0, 11]},
        "base": {"document_lookups": [1, 10], "own_work": [0, 11]},
        "v1": {"document_lookups": [1, 10], "own_work": [6, 11]}}, new="v1", baseline="base",
        baseline_label="the starting model")
    assert [row["verdict"] for row in rows] == ["same", "better"]
    assert overall == {"verdict": "better", "recommend": None,
                       "line": "Better at your own work than the starting model: 6 of 11 passed, against 0. "
                               "No kind of work stands out yet, so it isn't used."}


def test_comparisons_start_when_the_computer_is_free_and_not_while_training(ready):
    from tests.fixtures.learning import FakeLease

    lease = FakeLease(since_activity=3600.0)
    training = [True]
    service = model_compare.Comparisons(ready, comparer=_comparer(ready), lease=lease,
                                        training=lambda: training[0], start_threads=False)

    assert service.due() == 1 and not service.tick()  # training holds the card
    training[0] = False
    lease.set(since_activity=10.0)
    assert not service.tick()  # someone used Row-Bot a moment ago
    lease.set(since_activity=3600.0)
    assert service.tick() and model_compare.read_comparison(1, ready) is not None
    assert service.due() is None  # compared once


def test_comparisons_wait_while_learning_is_paused(ready):
    from tests.fixtures.learning import FakeLease

    save_learning_settings(ready, enabled=True, paused=True)
    service = model_compare.Comparisons(ready, comparer=_comparer(ready), lease=FakeLease(since_activity=3600.0),
                                        start_threads=False)

    assert not service.tick() and FakeWorker.instances == []


def test_a_version_running_in_row_bot_is_compared_there_with_its_starting_model_held_running(learning_on):
    """Both arms serve through Row-Bot's own server (L2x): the starting model is asked for in the
    version's runtime, and the server is held running (so its key stays valid) for the whole run."""

    import contextlib

    LearningStore(learning_on).create()
    personal = "model:custom_openai_row-bot-personal-row-bot:row-bot-personal-v1"
    releases.write_state(releases.version_dir(1, learning_on), releases.VersionState(
        version=1, phase=releases.READY, job_id="job_1", model_ref=personal, runtime="row_bot"))
    FakeWorker.instances.clear()
    held: list[str] = []
    asked_runtimes: list[str] = []

    @contextlib.contextmanager
    def hold(runtime):
        held.append(f"enter {runtime}")
        yield
        held.append("exit")

    def starting(runtime):
        asked_runtimes.append(runtime)
        assert held == ["enter row_bot"]  # the server runs before the starting model is asked for
        return "model:custom_openai_row-bot-personal-row-bot:row-bot-personal-base", None

    comparer = _comparer(learning_on)
    comparer._server_hold = hold  # noqa: SLF001 - the comparer's own collaborator
    comparer._baseline_model = starting  # noqa: SLF001

    result = comparer.run(1)

    assert asked_runtimes == ["row_bot"] and held == ["enter row_bot", "exit"]
    arms = {arm["key"]: arm["model_ref"] for arm in result["arms"]}
    assert arms["v1"] == personal and arms["base"].endswith(":row-bot-personal-base")


def test_a_version_is_compared_with_its_own_starting_model_after_the_person_changed_it(learning_on, monkeypatch):
    """O124: the person moved from the 2B to the 9B after v1 trained; v1 is still compared with the 2B."""

    from pathlib import Path

    from row_bot.learning.training import base_model, weights

    LearningStore(learning_on).create()
    save_learning_settings(learning_on, enabled=True, starting_model="qwen3.5-9b")
    folder = releases.version_dir(1, learning_on)
    releases.write_state(folder, releases.VersionState(version=1, phase=releases.READY, job_id="job_1",
                                                       model_ref=PERSONAL, runtime="ollama"))
    (folder / releases.MANIFEST_FILE).write_text(json.dumps({"identity": {"base": {"name": "Qwen/Qwen3.5-2B"}}}),
                                                 encoding="utf-8")
    monkeypatch.setattr(weights, "status", lambda model, root=None: weights.WeightsStatus("downloaded", 1, Path(".")))
    served: list[str] = []
    monkeypatch.setattr(base_model, "ensure",
                        lambda **kwargs: served.append(kwargs["model"].id) or (BASE, None))
    comparer = _comparer(learning_on)
    comparer._baseline_model = None  # noqa: SLF001 - the product's own starting model this time

    result = comparer.run(1)

    assert served == ["qwen3.5-2b"]
    assert next(arm for arm in result["arms"] if arm["key"] == "base")["model_ref"] == BASE


@pytest.mark.parametrize(("built", "served_from"), [(True, None), (False, "missing")])
def test_a_version_trained_on_hugging_face_is_compared_with_the_starting_model_its_job_built(
        learning_on, monkeypatch, built, served_from):
    """C1, 28 Oct: a 9B trained on Hugging Face on a computer that never downloaded the 9B's own files said "The
    starting model couldn't run here", although the job had built and Row-Bot had downloaded its untouched GGUF."""

    from row_bot.learning.training import base_model, weights

    LearningStore(learning_on).create()
    save_learning_settings(learning_on, enabled=True, starting_model="qwen3.5-9b")
    folder = releases.version_dir(1, learning_on)
    releases.write_state(folder, releases.VersionState(version=1, phase=releases.READY, job_id="job_1",
                                                       model_ref=PERSONAL, runtime="row_bot"))
    monkeypatch.setattr(weights, "status", lambda model, root=None: weights.WeightsStatus("not_downloaded"))
    if built:
        target = base_model.folder("qwen3.5-9b", learning_on)
        target.mkdir(parents=True)
        (target / "qwen3.5-9b-Q8_0.gguf").write_bytes(b"GGUF untouched")
        base_model.write(base_model.BaseState("qwen3.5-9b", "exported", gguf="qwen3.5-9b-Q8_0.gguf",
                                              runtime="row_bot"), learning_on)
    served: list[tuple] = []
    monkeypatch.setattr(base_model, "ensure", lambda **kwargs: served.append(
        (kwargs["model"].id, kwargs["weights_folder"])) or (BASE, None))
    comparer = _comparer(learning_on)
    comparer._baseline_model = None  # noqa: SLF001 - the product's own starting model this time

    ref, reason = comparer._starting_model("row_bot", 1)  # noqa: SLF001

    if built:
        assert (ref, reason, served) == (BASE, None, [("qwen3.5-9b", None)])
    else:
        assert (ref, reason, served) == (None, "engine_missing", [])


def test_without_its_starting_model_the_verdict_never_claims_no_difference_from_it():
    arms = [{"key": "new", "label": "v2 (new)"}, {"key": "main", "label": "GPT-6.1 Sol"}]
    tables = {"new": {"document_lookups": (15, 16)}, "main": {"document_lookups": (16, 16)}}

    _rows, overall = model_compare.summarize(arms, tables, new="new", baseline="base",
                                             baseline_label="the starting model")

    assert overall == {"verdict": "unclear", "recommend": None,
                       "line": "Row-Bot couldn't run the starting model, so it can't tell yet whether training "
                               "helped. Not used."}



@pytest.mark.parametrize(("sizes", "card_mib", "order"), [
    ((16_810_714_592, 16_810_714_592), 32_607, "model"),  # a 27B and its 27B starting model on a 32 GB card
    ((9_786_060_672, 9_786_060_672), 32_607, "interleaved"),  # the 9B and its starting model fit together
    ((16_810_714_592, 16_810_714_592), 81_920, "interleaved"),
])
def test_models_that_cant_stay_loaded_together_run_one_after_the_other_with_the_same_results(
        ready, monkeypatch, sizes, card_mib, order):
    """C1, 28 Oct: a 27B and its starting model swapped on the card task by task (about 16 s a task). Each model's
    runs now come together when both can't stay loaded, and the results are exactly the interleaved ones."""

    def compared(card: int):
        FakeWorker.instances.clear()
        comparer = _comparer(ready)
        comparer._card_mib = lambda: card  # noqa: SLF001
        monkeypatch.setattr(comparer, "_local_sizes", lambda version, state, base_key: list(sizes))
        result = comparer.run(1)
        return result, [arm for _task, arm, _budget in FakeWorker.instances[0].asked]

    result, arms = compared(card_mib)
    reference, interleaved = compared(1_000_000)  # a card that holds both: the order as it always was

    local = [arm for arm in arms if arm != "main"]  # the main model's answers are kept, so asked once
    switches = sum(1 for before, after in zip(local, local[1:]) if before != after)
    if order == "model":
        assert switches == 3  # visible: all of base, then all of v1; hidden: base again, then v1
    else:
        assert local == [arm for arm in interleaved if arm != "main"] and switches > 3
    assert result["rows"] == reference["rows"] and result["overall"] == reference["overall"]


def test_stop_ends_the_worker_mid_task_and_reads_as_stopped_never_failed(ready, monkeypatch):
    """C1, 28 Oct: Stop ended the worker mid-task, its run had no result, and the version said "Row-Bot couldn't
    compare v25. It tries again later." """

    stop = threading.Event()

    def run(self, task, arm, *, budget, timeout=0):
        stop.set()  # the person clicked Stop: the worker is ended while this task runs
        raise RuntimeError("no_result")

    monkeypatch.setattr(FakeWorker, "run", run)

    with pytest.raises(model_compare.CompareError) as stopped:
        _comparer(ready).run(1, stop=stop)

    assert stopped.value.code == "stopped"
    assert model_compare.read_progress(1, ready)["state"] == "stopped"

