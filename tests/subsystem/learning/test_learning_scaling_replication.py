"""The replication (PREREG-REPLICATION.md v1): the fresh block (days 291-370) and its repeated-wording flag, the arms in
their fixed order at their contexts (the 32K arms served as the study served them), SA2's overflow rule at both
contexts, R2 only on tasks counted in all four arms with Holm across R1 and R2, the model files' hash refusal, the
build pin's refusal, the token cap's stop and a resumed child that reruns nothing."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.e2e.learning.scaling import explore_worker, replicate, replicate_analysis, replicate_worker, runner, study
from tests.e2e.learning.scaling import worker as study_worker
from tests.e2e.learning.seeded import scratch

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

OVERFLOW_64K = ("the model call failed: ⚠️ API error: Personal model (Row-Bot) rejected the chat request (HTTP 400). The "
                "endpoint profile or selected model may not accept this message/tool payload. Details: request (70112 "
                "tokens) exceeds the available context size (65536 tokens)")
OVERFLOW_32K = ("the model call failed: ⚠️ API error: Personal model (Row-Bot) rejected the chat request (HTTP 400). The "
                "endpoint profile or selected model may not accept this message/tool payload. Details: request (34012 "
                "tokens) exceeds the available context size (32768 tokens)")
OUTAGE = "the model call failed: ⚠️ The AI provider had a server error — please try again shortly."
NAMES = {"start": "row-bot-personal-base", "sft-1000": "row-bot-personal-v3", "dagger-1000": "row-bot-personal-v7"}
PROVIDER = "custom_openai_row-bot-personal-row-bot"


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
    """The dry design's fresh block without generating it (the generator's own checks are the slow test's)."""

    fresh = {"mara-d291-0": {"person": "mara", "day": 291, "cluster": "mara/d291", "template": "arithmetic",
                             "repeated": False}}
    monkeypatch.setattr(replicate, "unchanged_days", lambda design: {"unchanged": True, "by_person": {}})
    monkeypatch.setattr(replicate, "fresh_block", lambda design: dict(fresh))
    return fresh


def _dry(tmp_path: Path, *extra: str) -> SimpleNamespace:
    return replicate.parse(["--dry-run", "--out", str(tmp_path / "out"), "--scratch", str(tmp_path / "scratch"),
                            *extra])


def _case(status: str, detail: str = "", cause: str = "", *, tokens: int = 40_000, local: int = 40_000):
    from row_bot.learning.evaluation import CaseResult

    return CaseResult("mara-d301-0", "pack", "", status, "tests", tokens, local, 5, 12.0, detail, cause)


REQUEST = SimpleNamespace(id="mara-d301-0", person="mara", day=301, task=SimpleNamespace(id="mara-d301-0"))


class _Runner:
    """The study's runner as the loop sees it: each run's case in turn, its tokens seen by ``on_run``."""

    def __init__(self, cases=()) -> None:
        self.cases = list(cases)
        self.calls: list[tuple[str, str]] = []
        self.gpu_seconds: dict[str, float] = {}
        self.arms: dict[str, runner.Spec] = {}
        self.on_run = None
        self.unload = lambda _arm: None
        self.finished = 0
        self._arm: str | None = None

    def run_task(self, task, arm):
        self.calls.append((task.id, arm))
        self._arm = arm
        self.gpu_seconds[arm] = self.gpu_seconds.get(arm, 0.0) + 1.0
        case = self.cases.pop(0) if self.cases else _case("passed")
        if self.on_run is not None:
            self.on_run(arm, case)
        return runner.Run(case, "thread")

    def finish(self) -> None:
        self.finished += 1
        if self._arm:
            self.unload(self._arm)
        self._arm = None


def _arm_runs(tmp_path: Path, task: str, *, design=None, cap: int = 200_000_000, spent: int = 0,
              kit=None) -> replicate_worker.ArmRuns:
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    (out / "design.json").write_text(json.dumps({
        "replication": (design or replicate.ReplicationDesign(runs=2, dry_run=True)).to_dict(),
        "study_design": {}, "study_out": str(tmp_path / "study" / "out"),
        "study_data": str(tmp_path / "study" / "data")}), encoding="utf-8")
    args = SimpleNamespace(out=str(out), data=str(tmp_path / "data"), cap_tokens=cap, spent_before=spent)
    return replicate_worker.ArmRuns(args, kit, task)


# ── The fresh block ──────────────────────────────────────────────────────────


@pytest.mark.slow  # the generator makes 370 days and 290 days for each of the three people (about 6 s)
def test_the_plan_runs_nothing_and_counts_the_fresh_block_with_days_1_to_290_unchanged(tmp_path, home, monkeypatch,
                                                                                         capsys):
    def refuse(*_args, **_kwargs):
        raise AssertionError("--plan started a process")

    monkeypatch.setattr(study.subprocess, "Popen", refuse)

    assert replicate.main(["--out", str(tmp_path / "out"), "--scratch", str(tmp_path / "scratch"), "--plan"]) == 0

    plan = json.loads(capsys.readouterr().out)
    block = plan["fresh_block"]
    assert (block["days"], block["requests"], block["person_days"]) == ([291, 370], 479, 240)
    assert block["by_person"] == {"idris": 160, "mara": 160, "wren": 159}
    assert (block["repeated_wording"], block["new_wording"]) == (125, 354)
    assert plan["study_days"]["unchanged"] is True
    assert {person: item["requests"] for person, item in plan["study_days"]["by_person"].items()} == {
        "idris": 580, "mara": 591, "wren": 569}  # the study's 1,483 training and 257 held-out requests
    for person in plan["people"].values():
        assert {int(item.split()[1][1:]) for item in person["ids"]} <= set(range(291, 371))
    assert [item["arm"] for item in plan["arms"]] == list(replicate.ARMS)
    assert [item.get("context") for item in plan["arms"][1:]] == [65_536, 65_536, 65_536, 32_768, 32_768]
    assert plan["runs"]["per_arm"] == 479 * 3 and plan["runs"]["total"] == 479 * 3 * 6
    assert 70e6 < plan["chatgpt_lane_tokens"]["teacher"] < 150e6
    assert 8 < plan["gpu_hours"]["total"] < 10
    assert not (tmp_path / "out").exists()


def test_a_request_has_repeated_wording_only_when_its_prompt_text_was_asked_before():
    earlier = [SimpleNamespace(id="mara-d12-0", prompt="What's the balance of the petty cash?"),
               SimpleNamespace(id="wren-d200-1", prompt="Label issue 14 as a bug.")]
    fresh = [SimpleNamespace(id="mara-d301-0", prompt="What's the balance of the petty cash?"),
             SimpleNamespace(id="mara-d301-1", prompt="What's the balance of the petty cash? "),
             SimpleNamespace(id="wren-d330-0", prompt="Label issue 14 as a bug."),
             SimpleNamespace(id="wren-d331-0", prompt="Label issue 15 as a bug.")]

    assert replicate.repeated_wording(earlier, fresh) == {"mara-d301-0": True, "mara-d301-1": False,
                                                          "wren-d330-0": True, "wren-d331-0": False}


# ── The arms, their order and their contexts ─────────────────────────────────


class _Kit:
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
        self.windows[ref] = context  # the study's ref: one name per model, its window the last one served
        return {"model_ref": ref, "name": name, "gguf": str(gguf), "context": context}

    def unload(self, name) -> None:
        self.unloaded.append(name)

    def finish(self) -> None:
        return None


def _study_models(tmp_path: Path) -> dict[str, tuple[str, str, Path]]:
    found = {}
    for model, relative in replicate.GGUFS.items():
        path = tmp_path / "study" / "data" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"stand-in {model}".encode())
        found[model] = (f"model:{PROVIDER}:{NAMES[model]}", NAMES[model], path)
    return found


@pytest.fixture
def local_arms(tmp_path, monkeypatch):
    """The local child with a fake kit and runner: the teacher done, the three stand-in files logged."""

    from row_bot import agent

    monkeypatch.setattr(agent, "SMALL_PROFILE_TOKENS", agent.SMALL_PROFILE_TOKENS)  # restored after the test
    monkeypatch.setattr(replicate_worker, "_PRODUCT_SMALL_PROFILE", None)
    models = _study_models(tmp_path)
    kit = _Kit()
    runs = _arm_runs(tmp_path, "local", design=replicate.ReplicationDesign(runs=2, dry_run=True), kit=kit)
    (runs.out / "teacher").mkdir()
    (runs.out / "teacher" / "state.json").write_text(json.dumps({"done": True}), encoding="utf-8")
    replicate.check_models(runs.out, {model: item[2] for model, item in models.items()}, before="evaluate")
    fake = _Runner()

    def start(self, arms, gpu):
        self.runner = fake
        return {REQUEST.id: REQUEST}, [REQUEST.id]

    monkeypatch.setattr(explore_worker, "study_arms", lambda out, data, arms: dict(models))
    monkeypatch.setattr(study_worker, "prepare_profile", lambda data, args: None)
    monkeypatch.setattr(replicate_worker.ArmRuns, "start", start)
    monkeypatch.setattr(replicate_worker, "sees", lambda ref: (kit.windows[ref], True))
    return SimpleNamespace(runs=runs, kit=kit, runner=fake, models=models)


def test_the_local_arms_run_in_order_each_in_full_at_its_context_with_the_studys_own_models(local_arms):
    assert local_arms.runs.local() == 0

    assert local_arms.kit.served == [("dagger-64k", 65_536, "row-bot-personal-v7"),
                                     ("sft-64k", 65_536, "row-bot-personal-v3"),
                                     ("start-64k", 65_536, "row-bot-personal-base"),
                                     ("dagger-32k", 32_768, "row-bot-personal-v7"),
                                     ("sft-32k", 32_768, "row-bot-personal-v3")]
    assert [arm for _task, arm in local_arms.runner.calls] == [arm for arm in replicate.LOCAL for _ in range(2)]
    # Each arm's model is unloaded before the next one is served.
    assert local_arms.kit.unloaded == ["row-bot-personal-v7", "row-bot-personal-v3", "row-bot-personal-base",
                                       "row-bot-personal-v7", "row-bot-personal-v3"]
    state = json.loads((local_arms.runs.folder / "state.json").read_text(encoding="utf-8"))
    # The 64K arms keep the study's small-context prompt to 65,536; the 32K arms get the product's own threshold.
    assert {arm: state["serving"][arm]["small_profile_tokens"] for arm in replicate.LOCAL} == {
        "dagger-64k": 65_536, "sft-64k": 65_536, "start-64k": 65_536, "dagger-32k": 32_768, "sft-32k": 32_768}
    assert {arm: state["serving"][arm]["effective_context"] for arm in replicate.LOCAL} == replicate.CONTEXT_OF
    assert state["done"] is True

    local_arms.runner.calls.clear()
    local_arms.kit.served.clear()
    assert local_arms.runs.local() == 0  # started again: nothing reruns, nothing is served again
    assert local_arms.runner.calls == [] and local_arms.kit.served == []


def test_a_32k_arm_whose_window_row_bot_sees_larger_refuses_before_its_runs(local_arms, monkeypatch):
    monkeypatch.setattr(replicate_worker, "sees", lambda ref: (65_536, True))

    with pytest.raises(scratch.ScratchError, match="65,536-token window"):
        local_arms.runs.local()

    assert {arm for _task, arm in local_arms.runner.calls} == {"dagger-64k", "sft-64k", "start-64k"}


def test_the_local_arms_wait_for_the_teacher(local_arms):
    (local_arms.runs.out / "teacher" / "state.json").write_text(json.dumps({"done": False}), encoding="utf-8")

    with pytest.raises(scratch.ScratchError, match="after the teacher"):
        local_arms.runs.local()
    assert local_arms.kit.served == []


def test_an_arm_is_served_alone_on_the_servers_list_at_its_context_under_the_studys_name(tmp_path, monkeypatch):
    from row_bot.learning.training import llama_server, serving

    gguf = tmp_path / "study" / "v7.gguf"
    listed = [llama_server.ModelEntry(name, str(tmp_path / f"{name}.gguf"), 65_536) for name in NAMES.values()]
    server = SimpleNamespace(listed=listed, unregistered=[])
    server.models = lambda: list(server.listed)

    def unregister(name):
        server.unregistered.append(name)
        server.listed = [entry for entry in server.listed if entry.name != name]

    server.unregister = unregister
    asked: list[dict] = []

    def serve(runtime, **options):
        asked.append({"runtime": runtime, "listed": [entry.name for entry in server.listed], **options})
        server.listed = [entry for entry in server.listed if entry.name != options["tag"]] + [
            llama_server.ModelEntry(options["tag"], str(options["gguf"]), options["serve_context"])]
        return serving.ServeOutcome("ready", runtime, model_ref=f"model:{PROVIDER}:{options['tag']}",
                                    probe={"agent_ready": True})

    monkeypatch.setattr(llama_server, "get", lambda root: server)
    monkeypatch.setattr(serving, "serve", serve)
    monkeypatch.setattr(replicate_worker, "card_memory_mib", lambda: None)
    monkeypatch.setattr(explore_worker, "card_memory_mib", lambda: None)
    monkeypatch.setattr(explore_worker, "instance_running", lambda name, folder: False)
    kit = replicate_worker.LiveKit(SimpleNamespace(data=str(tmp_path / "data"), gpu_booking="", lane="lane-paper",
                                                   eval_profile=""))
    gate = scratch.GpuGate(None, "lane-paper", training=lambda: False, quiet=lambda: None, card=lambda: None,
                           after_quiet=lambda: False, say=lambda _text: None)

    found = kit.serve_arm("dagger-32k", (f"model:{PROVIDER}:row-bot-personal-v7", "row-bot-personal-v7", gguf),
                          32_768, gate)

    assert server.unregistered == ["row-bot-personal-base", "row-bot-personal-v3"]
    assert len(asked) == 1 and asked[0]["listed"] == ["row-bot-personal-v7"]
    assert (asked[0]["runtime"], asked[0]["tag"], asked[0]["serve_context"], asked[0]["gguf"]) == (
        serving.ROW_BOT, "row-bot-personal-v7", 32_768, gguf)
    assert asked[0]["tool_turn"] is False
    assert found["listed"] == [{"name": "row-bot-personal-v7", "gguf": str(gguf), "context": 32_768}]
    assert gate.held is None  # the card goes back after serving


@pytest.mark.slow  # starts the stand-in llama-server and a process per loaded model
def test_on_the_real_model_server_row_bots_window_follows_each_arms_context(tmp_path, monkeypatch):
    import sys

    from row_bot.learning.training import llama_server, server_files
    from row_bot.providers.custom import custom_provider_id

    root, children = tmp_path / "data", tmp_path / "children"
    root.mkdir()
    children.mkdir()
    monkeypatch.setenv("FAKE_LLAMA_CHILDREN", str(children))
    monkeypatch.setenv("FAKE_LLAMA_RECORD", str(tmp_path / "record.json"))
    python = getattr(sys, "_base_executable", "") or sys.executable
    stand_in = Path(__file__).resolve().parents[2] / "fixtures" / "learning_fake_llama_server.py"

    def environment(folder: Path, home: Path) -> dict[str, str]:
        found = server_files.server_environment(folder, home)
        found.update(FAKE_LLAMA_CHILDREN=str(children), FAKE_LLAMA_RECORD=str(tmp_path / "record.json"))
        return found

    windows: list[int] = []
    server = llama_server.ManagedServer(
        root, files=lambda: server_files.ServerFiles("installed", "cu130", tmp_path, Path(python), 1, "b11669"),
        command=lambda exe: [str(exe), "-I", str(stand_in)], environment=environment, card_bytes=lambda: None,
        endpoint=lambda connection, context: windows.append(int(context)), server_idle_seconds=60, monitor=False)

    class Verifier:
        memory = None

        def probe(self, *_args, **_kwargs):
            return {"agent_ready": True}

    provider = custom_provider_id(llama_server.ENDPOINT_ID)
    models = {model: (f"model:{provider}:{name}", name, tmp_path / f"{name}-Q8_0.gguf") for model, name in NAMES.items()}
    for _ref, _name, gguf in models.values():
        gguf.write_bytes(b"GGUF")
    monkeypatch.setattr(llama_server, "get", lambda _root=None: server)
    monkeypatch.setattr(explore_worker, "_MeasuringVerifier", Verifier)
    monkeypatch.setattr(explore_worker, "instance_running", lambda name, folder: False)
    monkeypatch.setattr(replicate_worker, "card_memory_mib", lambda: None)
    monkeypatch.setattr(explore_worker, "card_memory_mib", lambda: None)
    kit = replicate_worker.LiveKit(SimpleNamespace(data=str(root), gpu_booking="", lane="lane-paper", eval_profile=""))
    gate = scratch.GpuGate(None, "lane-paper", training=lambda: False, quiet=lambda: None, card=lambda: None,
                           after_quiet=lambda: False, say=lambda _text: None)
    seen = []
    try:
        for arm in replicate.LOCAL:
            found = kit.serve_arm(arm, models[replicate.MODEL_OF[arm]], replicate.CONTEXT_OF[arm], gate)
            seen.append((arm, windows[-1], [(entry.name, entry.context) for entry in server.models()]))
            assert found["model_ref"] == models[replicate.MODEL_OF[arm]][0]
    finally:
        server.shutdown()

    assert seen == [("dagger-64k", 65_536, [("row-bot-personal-v7", 65_536)]),
                    ("sft-64k", 65_536, [("row-bot-personal-v3", 65_536)]),
                    ("start-64k", 65_536, [("row-bot-personal-base", 65_536)]),
                    ("dagger-32k", 32_768, [("row-bot-personal-v7", 32_768)]),
                    ("sft-32k", 32_768, [("row-bot-personal-v3", 32_768)])]


def test_between_runs_the_card_goes_back_to_another_lanes_exclusive_window(tmp_path):
    booking = tmp_path / "gpu-booking.txt"
    gate = scratch.GpuGate(booking, "lane-paper", training=lambda: False, quiet=lambda: None)

    booking.write_text("09:00 lane-paper START replication: dagger-64k runs\n", encoding="utf-8")
    assert replicate_worker.gives_back(gate) is False
    booking.write_text("09:00 lane-paper START replication: dagger-64k runs\n"
                       "23:00 lane-graph EXCLUSIVE START the graph lane's night\n", encoding="utf-8")
    assert replicate_worker.gives_back(gate) is True
    booking.write_text(booking.read_text(encoding="utf-8") + "07:00 lane-graph EXCLUSIVE END\n", encoding="utf-8")
    assert replicate_worker.gives_back(gate) is False
    assert replicate_worker.gives_back(scratch.GpuGate(booking, "lane-paper", training=lambda: True,
                                                       quiet=lambda: None)) is True


def test_a_local_arm_gives_the_card_back_with_its_model_unloaded(tmp_path):
    """19 Nov: the graph lane books its night only when the card holds at most 6,500 MiB. An arm that gave back its
    booking for quiet hours with its model still loaded would cost that lane the night."""

    booking = tmp_path / "gpu-booking.txt"
    booking.write_text("09:00 lane-paper START replication: dagger-64k runs\n", encoding="utf-8")
    events: list[str] = []

    class Gate(scratch.GpuGate):
        def release(self) -> None:
            events.append("release")
            super().release()

        def acquire(self, what: str, expect: str = "") -> None:
            events.append("acquire")

    quiet = {"now": None}
    gate = Gate(booking, "lane-paper", training=lambda: False, quiet=lambda: quiet["now"])
    gate.held = "replication: dagger-64k runs"
    runner_like = SimpleNamespace(_arm="dagger-64k", arms={"dagger-64k": runner.Spec("dagger-64k", "model:x", local=True)},
                                  gpu=gate, unload=lambda arm: events.append(f"unload {arm}"))

    replicate_worker.ReplicationRunner.card(runner_like, "dagger-64k")  # not giving back: the model stays
    assert events == ["acquire"]

    events.clear()
    quiet["now"] = "quiet hours"
    replicate_worker.ReplicationRunner.card(runner_like, "dagger-64k")
    assert events == ["unload dagger-64k", "release", "acquire"]


# ── SA2, R1, R2 and Holm ─────────────────────────────────────────────────────


def test_an_overflow_at_64k_or_32k_is_out_of_budget_and_an_outage_stays_outside(tmp_path):
    runs = _arm_runs(tmp_path, "local", design=replicate.ReplicationDesign(runs=1, dry_run=True))
    runs.runner = _Runner([_case("error", OVERFLOW_64K, "provider_error"), _case("error", OVERFLOW_64K, "provider_error"),
                           _case("error", OVERFLOW_32K, "provider_error"), _case("error", OVERFLOW_32K, "provider_error"),
                           _case("error", OUTAGE, "provider_error"), _case("error", OUTAGE, "provider_error")])

    for arm in ("dagger-64k", "dagger-32k", "sft-32k"):
        runs.held_out(arm, {REQUEST.id: REQUEST}, [REQUEST.id])

    kept = {item["arm"]: item for item in study_worker.read_lines(runs.folder / "runs.jsonl")}
    assert [(kept[arm]["status"], kept[arm]["overflow"], kept[arm]["overflowed_attempts"], kept[arm]["reran_after_outage"])
            for arm in ("dagger-64k", "dagger-32k", "sft-32k")] == [
        ("budget", True, 2, True), ("budget", True, 2, True), ("error", False, 0, True)]
    from tests.e2e.learning.scaling import analysis

    assert analysis.scores([kept["dagger-64k"]]) == {REQUEST.id: 0.0}  # in the denominator, failed
    assert analysis.scores([kept["sft-32k"]]) == {}  # an outage after its rerun: outside


TASKS = [f"mara-d{301 + number}-0" for number in range(10)]
FRESH = {task: {"person": "mara", "day": 301 + number, "cluster": f"mara/d{301 + number}", "template": "arithmetic",
                "repeated": number < 3} for number, task in enumerate(TASKS)}


def _record(arm: str, number: int, status: str, **extra) -> dict:
    return {"arm": arm, "task": TASKS[number], "repeat": 0, "cluster": f"mara/d{301 + number}", "status": status,
            "cause": extra.pop("cause", "" if status == "passed" else "wrong_answer"), "detail": "", "tokens": 1_000,
            "local_tokens": 1_000, "steps": 3, "seconds": 36.0, "reran_after_outage": False, "unrecovered": [],
            **extra}


def _arm(arm: str, passing: set[int]) -> list[dict]:
    return [_record(arm, number, "passed" if number in passing else "failed") for number in range(10)]


def _report(runs, **design):
    found = replicate.ReplicationDesign(resamples=200, permutations=200, **design)
    return replicate_analysis.build_report(runs, design=found, fresh=FRESH, states={})


def _runs_for_hypotheses() -> list[dict]:
    runs = [*_arm("teacher", set(range(9))), *_arm("start-64k", {0, 1}), *_arm("sft-64k", {0, 1, 2, 3}),
            *_arm("dagger-64k", {0, 1, 2, 3, 4, 5, 6, 7}), *_arm("sft-32k", {0, 1, 2, 3}),
            *_arm("dagger-32k", {0, 1, 2, 3, 4})]
    # One task has no counted run in dagger-32k (an outage after its rerun): it leaves R2, not R1.
    runs[[item["arm"] for item in runs].index("dagger-32k") + 9] = _record(
        "dagger-32k", 9, "error", detail=OUTAGE, cause="provider_error")
    return runs


def test_r2_uses_only_the_tasks_counted_in_all_four_arms_and_holm_covers_r1_and_r2():
    from row_bot.learning.evaluation import holm

    report = _report(_runs_for_hypotheses(), min_tasks=5)

    r1, r2 = report["confirmatory"]["R1"], report["confirmatory"]["R2"]
    assert r1["analysis"]["problems"] == 10 and r1["analysis"]["difference"] == pytest.approx(0.4)
    assert r2["analysis"]["problems"] == 9  # mara-d310-0 isn't counted in dagger-32k
    # e_i on the nine: 64K's difference (tasks 4-7: +1) minus 32K's (task 4: +1) is +1 on tasks 5-7.
    assert r2["analysis"]["difference"] == pytest.approx(3 / 9)
    assert r2["behind"]["runs"] == {"dagger-64k": 9, "sft-64k": 9, "dagger-32k": 9, "sft-32k": 9}
    assert r2["behind"]["clusters"] == 9
    adjusted = holm({"R1": r1["analysis"]["p_value"], "R2": r2["analysis"]["p_value"]}, family=["R1", "R2"])
    assert (r1["holm_p"], r2["holm_p"]) == (adjusted["R1"], adjusted["R2"])
    assert r1["verdict"] == ("holds: dagger-64k higher" if r1["holm_p"] < 0.05 else "does not hold")
    # Secondary: the same comparisons on the seven new-wording tasks, unadjusted.
    words = report["secondary"]["new_wording"]
    assert (words["R1"]["analysis"]["problems"], words["R2"]["analysis"]["problems"]) == (7, 6)
    assert "holm_p" not in words["R1"]
    assert report["secondary"]["paired"]["dagger-32k − sft-32k"]["problems"] == 9
    assert report["secondary"]["gap_closed"]["dagger-64k"]["gap_closed"] == pytest.approx(6 / 7)
    assert report["fresh_block"] == {"requests": 10, "person_days": 10, "repeated_wording": 3, "new_wording": 7}


def test_a_hypothesis_with_too_few_tasks_is_not_tested_and_enters_holm_as_1():
    report = _report(_runs_for_hypotheses(), min_tasks=10)

    r1, r2 = report["confirmatory"]["R1"], report["confirmatory"]["R2"]
    assert (r1["tested"], r2["tested"]) == (True, False)
    assert r2["verdict"] == "not tested" and r2["holm_p"] is None and r2["entered_holm_as"] == 1.0
    assert r1["holm_p"] == pytest.approx(min(1.0, 2 * r1["analysis"]["p_value"]), abs=1e-6)


def test_the_report_counts_overflows_and_the_skill_load_habit_per_arm():
    runs = _runs_for_hypotheses()
    runs[0] = _record("teacher", 0, "failed", cause="bad_call",
                      unrecovered=[["skill_load", "invalid_reference"]] * 3 + [["mail_read", "invalid_arguments"]])
    runs[11] = _record("start-64k", 1, "passed", unrecovered=[["skill_load", "invalid_reference"]])
    runs[25] = _record("sft-64k", 5, "error", detail=OVERFLOW_64K, cause="provider_error")

    report = _report(runs, min_tasks=5)

    assert report["arms"]["sft-64k"]["overflows"] == 1 and report["arms"]["sft-64k"]["out_of_budget"] == 1
    assert report["arms"]["sft-64k"]["tasks"] == 10  # the overflow stays in the denominator (SA2)
    habit = report["secondary"]["skill_load_habit"]
    assert habit["teacher"] == {"calls": 3, "runs": 1, "runs_passed": 0, "runs_three_or_more": 1,
                                "calls_in_bad_call_runs": 3, "of_runs": 10}
    assert (habit["start-64k"]["runs"], habit["start-64k"]["runs_passed"]) == (1, 1)
    text = replicate_analysis.markdown({**report, "build": {}, "prereg": {}})
    assert "| skill_load | invalid_reference | 3 / 3 | 0 / 0 | 0 / 0 | 0 / 1 |" in text


# ── Stops, refusals and resuming ─────────────────────────────────────────────


def test_a_resumed_arm_reruns_nothing_and_an_error_is_rerun_once(tmp_path):
    runs = _arm_runs(tmp_path, "teacher")
    runs.runner = _Runner([_case("error", OUTAGE, "provider_error", local=0), _case("passed", local=0),
                           _case("failed", local=0)])

    runs.held_out("teacher", {REQUEST.id: REQUEST}, [REQUEST.id])

    kept = study_worker.read_lines(runs.folder / "runs.jsonl")
    assert [(item["repeat"], item["status"], item["reran_after_outage"]) for item in kept] == [
        (0, "passed", True), (1, "failed", False)]
    runs.runner = _Runner([])
    runs.held_out("teacher", {REQUEST.id: REQUEST}, [REQUEST.id])
    assert runs.runner.calls == []


def test_the_token_cap_stops_the_teacher_cleanly_before_its_next_run(tmp_path):
    runs = _arm_runs(tmp_path, "teacher", design=replicate.ReplicationDesign(runs=3, dry_run=True), cap=100_000,
                     spent=30_000)
    runs.runner = _Runner([_case("passed", tokens=40_000, local=0) for _ in range(3)])
    runs.runner.on_run = runs.spend

    with pytest.raises(SystemExit) as stop:
        runs.held_out("teacher", {REQUEST.id: REQUEST}, [REQUEST.id])

    assert stop.value.code == replicate_worker.EXIT_TOKEN_CAP
    assert len(study_worker.read_lines(runs.folder / "runs.jsonl")) == 2  # 30K + 2 × 40K reaches the cap
    state = json.loads((runs.folder / "state.json").read_text(encoding="utf-8"))
    assert state["capped"]["spent"] == 110_000 and state["cloud_tokens"] == 80_000


@pytest.mark.parametrize(("code", "word"), [(75, "token cap"), (76, "usage or rate limit")])
def test_a_cap_or_limit_stop_ends_the_replication_before_the_local_arms(tmp_path, home, quick_block, code, word):
    started: list[list[str]] = []
    said: list[str] = []

    def run_child(command, env, log):
        started.append(command)
        return code

    found = replicate.Replication(_dry(tmp_path), runner=run_child, say=said.append,
                                  build=lambda: {"commit": "a" * 40, "code_hash": "1" * 64})

    assert found.run(["evaluate", "analyse"]) == code

    assert [command[3] for command in started] == ["teacher"]
    assert Path(started[0][started[0].index("--data") + 1]) == (tmp_path / "scratch" / "teacher" / "data").resolve()
    assert started[0][started[0].index("--spent-before") + 1] == "0"
    assert "--gpu-booking" not in started[0]
    assert any(word in line for line in said)


def test_a_changed_model_file_stops_the_replication_before_its_next_run(tmp_path, home, quick_block):
    started: list[list[str]] = []
    found = replicate.Replication(_dry(tmp_path), runner=lambda command, env, log: started.append(command) or 0,
                                  build=lambda: {"commit": "a" * 40, "code_hash": "1" * 64})
    found.pin()
    found.check_models("evaluate")  # logged before the first run
    gguf = found.study_data / replicate.GGUFS["dagger-1000"]
    gguf.write_bytes(gguf.read_bytes() + b"!")

    with pytest.raises(replicate.ModelChanged, match="dagger-1000"):
        found.stage_evaluate()
    assert started == []
    with pytest.raises(replicate.ModelChanged, match="no logged hashes"):
        replicate.check_models(tmp_path / "elsewhere", {"start": gguf}, before="serving start-64k", record=False)


def test_the_local_child_checks_its_arms_file_before_serving_it(local_arms):
    gguf = local_arms.models["sft-1000"][2]
    gguf.write_bytes(b"retrained")

    with pytest.raises(replicate.ModelChanged, match="sft-1000"):
        local_arms.runs.local()
    assert [arm for arm, _context, _name in local_arms.kit.served] == ["dagger-64k"]


def test_the_build_pin_refuses_another_build_and_a_stage_on_it_runs_nothing(tmp_path, home, quick_block):
    def build(name: str, code: str = "1"):
        return lambda: {"commit": name * 40, "code_hash": code * 64}

    replicate.Replication(_dry(tmp_path), build=build("a")).pin()

    assert replicate.Replication(_dry(tmp_path), build=build("a")).pin()["commit"] == "a" * 40
    with pytest.raises(scratch.ScratchError, match="commit"):
        replicate.Replication(_dry(tmp_path), build=build("b")).pin()
    with pytest.raises(scratch.ScratchError, match="code_hash"):
        replicate.Replication(_dry(tmp_path), build=build("a", "2")).pin()
    started: list[list[str]] = []
    later = replicate.Replication(_dry(tmp_path), build=build("b"),
                                  runner=lambda command, env, log: started.append(command) or 0)
    with pytest.raises(scratch.ScratchError):
        later.run(["evaluate"])
    assert started == []


def test_it_never_writes_in_the_studys_folders_and_a_real_run_names_its_inputs(tmp_path, home):
    root = tmp_path / "study"
    (root / "out").mkdir(parents=True)
    (root / "out" / "design.json").write_text("{}", encoding="utf-8")
    (root / "scratch" / "study" / "data").mkdir(parents=True)
    profile = tmp_path / "eval-profile"
    profile.mkdir()

    def args(out: Path, *extra: str):
        return replicate.parse(["--study-out", str(root / "out"), "--study-data",
                                str(root / "scratch" / "study" / "data"), "--eval-profile", str(profile),
                                "--out", str(out), "--scratch", str(tmp_path / "replication" / "scratch"), *extra])

    with pytest.raises(scratch.ScratchError, match="study's own folders"):
        replicate.Replication(args(root / "out" / "replication", "--gpu-booking", str(tmp_path / "b.txt"))).pin()
    with pytest.raises(scratch.ScratchError, match="--gpu-booking"):
        replicate.Replication(args(tmp_path / "replication" / "out")).pin()
    with pytest.raises(scratch.ScratchError):
        replicate.Replication(args(profile / "replication"))
