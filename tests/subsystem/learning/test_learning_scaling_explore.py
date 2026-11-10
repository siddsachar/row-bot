"""The scaling study's exploratory 64K follow-up (not pre-registered): each run keeps its unrecovered tool calls as
(tool name, problem class) pairs and nothing else, an overflow counts as out of budget (SA2) after the study's one
rerun, a resumed stage reruns nothing, the report puts 64K beside the study's 32K, and the stage never writes in the
study's own folders or starts GPU work in the person's hours."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.e2e.learning.scaling import explore, explore_worker, runner
from tests.e2e.learning.scaling.design import Design
from tests.e2e.learning.scaling.study import Stopped
from tests.e2e.learning.seeded import scratch

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

#: What Row-Bot's llama.cpp server answered when a conversation outgrew its context (the study's 32K runs).
OVERFLOW = ("the model call failed: ⚠️ API error: Personal model (Row-Bot) rejected the chat request (HTTP 400). The "
            "endpoint profile or selected model may not accept this message/tool payload. Details: request (34012 "
            "tokens) exceeds the available context size (32768 tokens)")
OUTAGE = "the model call failed: ⚠️ The AI provider had a server error — please try again shortly."


@pytest.fixture
def home(tmp_path, monkeypatch):
    folder = tmp_path / "home"
    (folder / ".row-bot").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: folder))
    monkeypatch.setenv(scratch.PROTECT_ENV, str(tmp_path / "backups"))
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "row-bot-data"))  # restored after the test
    return folder


def _call(record, name: str, call_id: str, args: dict, content: str) -> None:
    record.add("tool_call", SimpleNamespace(raw_name=name, runtime_name=name, call_id=call_id, args=args))
    record.add("tool_done", {"name": name, "tool_call_id": call_id, "content": content})


def test_an_unrecovered_call_keeps_only_its_tool_and_problem_class():
    from row_bot.learning import eval_worker

    record = eval_worker.TurnRecord()
    _call(record, "workspace_read_file", "a", {"path": "notes/private-plans.md"},
          json.dumps({"ok": False, "error": {"code": "outside_workspace", "message": "notes/private-plans.md"}}))
    _call(record, "learning_mail", "b", {"to": "pat@example.com"},
          json.dumps({"ok": False, "error": "No message to pat@example.com about the invoice"}))
    _call(record, "calculator", "c", {"expression": "2/0"}, "Error: division by zero")
    _call(record, "calculator", "d", {"expression": "2/1"}, "2")  # the same tool worked later: recovered

    pairs = runner.unrecovered_calls(record)

    assert pairs == [("workspace_read_file", "outside_workspace"), ("learning_mail", "refused")]
    assert not any(word in json.dumps(pairs) for word in ("private", "pat@", "invoice", "notes/"))
    assert eval_worker.failure_cause("failed", "2 of 3 tests failed", record, check="tests") == "bad_call"


def _case(status: str, detail: str = "", cause: str = ""):
    from row_bot.learning.evaluation import CaseResult

    return CaseResult("mara-d260-0", "pack", "", status, "tests", 40_000, 40_000, 5, 12.0, detail, cause)


REQUEST = SimpleNamespace(id="mara-d260-0", person="mara", day=260, task=SimpleNamespace(id="mara-d260-0"))


def test_a_run_record_counts_an_overflow_as_out_of_budget_and_a_provider_error_as_an_outage():
    overflowed = runner.Run(_case("error", OVERFLOW, "provider_error"), "thread",
                            unrecovered=[("workspace_write_file", "invalid_arguments")])
    outage = runner.Run(_case("error", OUTAGE, "provider_error"), "thread")

    found = explore_worker.run_record("dagger-1000", REQUEST, 1, overflowed, reran=True, overflowed=2)
    kept = explore_worker.run_record("start", REQUEST, 0, outage, reran=True, overflowed=0)

    assert (found["status"], found["cause"], found["overflow"]) == ("budget", "budget", True)
    assert found["overflowed_attempts"] == 2 and found["reran_after_outage"] is True
    assert found["unrecovered"] == [["workspace_write_file", "invalid_arguments"]]
    assert found["cluster"] == "mara/d260" and found["repeat"] == 1
    assert (kept["status"], kept["overflow"]) == ("error", False)


class _Runner:
    def __init__(self, runs) -> None:
        self.runs = list(runs)
        self.calls: list[tuple[str, str]] = []
        self.gpu_seconds: dict[str, float] = {}

    def run_task(self, task, arm):
        self.calls.append((task.id, arm))
        self.gpu_seconds[arm] = self.gpu_seconds.get(arm, 0.0) + 1.0
        return self.runs.pop(0)


def _evaluation(tmp_path: Path) -> explore_worker.ExploreEvaluation:
    out = tmp_path / "out"
    out.mkdir()
    (out / "design.json").write_text(json.dumps({
        "explore": explore.ExploreDesign(runs=2).to_dict(), "study_design": Design().to_dict(),
        "study_out": str(tmp_path / "study" / "out"), "study_data": str(tmp_path / "study" / "data")}),
        encoding="utf-8")
    return explore_worker.ExploreEvaluation(SimpleNamespace(out=str(out), data=str(tmp_path / "data")), kit=None)


def test_an_error_is_rerun_once_and_a_resumed_stage_reruns_nothing(tmp_path):
    evaluation = _evaluation(tmp_path)
    overflowed = runner.Run(_case("error", OVERFLOW, "provider_error"), "thread")
    evaluation.runner = _Runner([overflowed, overflowed, runner.Run(_case("passed"), "thread")])

    evaluation.held_out("dagger-1000", {REQUEST.id: REQUEST}, [REQUEST.id])

    runs = explore.read_lines(tmp_path / "out" / "evaluate" / "runs.jsonl")
    assert [(item["repeat"], item["status"], item["overflowed_attempts"], item["reran_after_outage"])
            for item in runs] == [(0, "budget", 2, True), (1, "passed", 0, False)]
    saved = json.loads((tmp_path / "out" / "evaluate" / "state.json").read_text(encoding="utf-8"))
    assert saved["gpu_seconds"]["dagger-1000"] == 3.0  # every attempt held the card

    evaluation.runner = _Runner([])
    evaluation.held_out("dagger-1000", {REQUEST.id: REQUEST}, [REQUEST.id])
    assert evaluation.runner.calls == []


TASKS = [f"mara-d{260 + number}-0" for number in range(8)]


def _run(arm: str, number: int, status: str, *, cause: str = "", detail: str = "", unrecovered=(), **extra) -> dict:
    return {"arm": arm, "task": TASKS[number], "repeat": 0, "cluster": f"mara/d{260 + number}", "status": status,
            "cause": cause or ("" if status == "passed" else "wrong_answer"), "detail": detail, "tokens": 1_000,
            "steps": 3, "seconds": 1_800.0, "reran_after_outage": False,
            "unrecovered": [list(pair) for pair in unrecovered], **extra}


def _side(passing: dict[str, int]) -> list[dict]:
    return [_run(arm, number, "passed" if number < count else "failed") for arm, count in passing.items()
            for number in range(8)]


def test_the_report_puts_64k_beside_the_studys_32k_under_sa2_with_the_unrecovered_calls():
    explored = _side({"start": 2, "sft-1000": 4, "dagger-1000": 6})
    explored[-1] = _run("dagger-1000", 7, "failed", cause="bad_call",
                        unrecovered=[("workspace_write_file", "outside_workspace")])
    explored[8] = _run("sft-1000", 0, "passed", unrecovered=[("workspace_write_file", "outside_workspace")])
    explored[13] = _run("sft-1000", 5, "budget", cause="budget", overflow=True, overflowed_attempts=2)
    study = _side({"start": 2, "sft-1000": 4, "dagger-1000": 4})
    study[20] = _run("dagger-1000", 4, "error", cause="provider_error", detail=OVERFLOW, reran_after_outage=True)
    study[21] = _run("dagger-1000", 5, "error", cause="provider_error", detail=OVERFLOW, reran_after_outage=True)
    state = {"done": True, "gpu_seconds": {"start": 3_600.0, "sft-1000": 3_600.0, "dagger-1000": 7_200.0,
                                           "serving": 360.0}}

    report = explore.build_report(explored, study, design=explore.ExploreDesign(),
                                  study_design=Design(resamples=200, permutations=200).to_dict(), state=state)

    assert report["label"].startswith("Exploratory, not pre-registered")
    assert report["explored"]["arms"]["dagger-1000"]["rate"] == 0.75
    assert report["study"]["arms"]["dagger-1000"]["rate"] == 0.5  # the two overflows count as failed (SA2)
    assert report["study"]["arms"]["dagger-1000"]["overflows"] == 2
    assert report["study"]["arms"]["dagger-1000"]["causes"]["budget"] == 2
    assert report["explored"]["arms"]["sft-1000"]["overflowed_attempts"] == 2
    assert report["explored"]["paired"]["dagger-1000 − sft-1000"]["difference"] == pytest.approx(0.25)
    assert report["study"]["paired"]["dagger-1000 − sft-1000"]["difference"] == pytest.approx(0.0)
    assert report["unrecovered"]["rows"] == [
        {"tool": "workspace_write_file", "problem": "outside_workspace",
         "in_bad_call_runs": {"start": 0, "sft-1000": 0, "dagger-1000": 1},
         "in_every_run": {"start": 0, "sft-1000": 1, "dagger-1000": 1}}]
    assert report["gpu_hours"] == {"by_arm": {"start": 1.0, "sft-1000": 1.0, "dagger-1000": 2.0},
                                   "serving_checks": 0.1}
    text = explore.markdown(report)
    assert "Exploratory, not pre-registered" in text
    assert "| workspace_write_file | outside_workspace | 0 / 0 | 0 / 1 | 1 / 1 |" in text


@pytest.fixture
def study_folders(tmp_path):
    out = tmp_path / "study" / "out"
    out.mkdir(parents=True)
    (out / "design.json").write_text(json.dumps(Design().to_dict()), encoding="utf-8")
    (tmp_path / "study" / "scratch" / "study" / "data").mkdir(parents=True)
    return tmp_path / "study"


def _args(tmp_path: Path, *extra: str, out: Path | None = None):
    return explore.parse(["--study-out", str(tmp_path / "study" / "out"),
                          "--study-data", str(tmp_path / "study" / "scratch" / "study" / "data"),
                          "--out", str(out or tmp_path / "explore" / "out"),
                          "--scratch", str(tmp_path / "explore" / "scratch"), *extra])


def test_it_never_writes_in_the_studys_own_folders(tmp_path, home, study_folders):
    for out in (study_folders / "out" / "explore", study_folders / "scratch" / "explore"):
        with pytest.raises(scratch.ScratchError, match="study's own folders"):
            explore.Explore(_args(tmp_path, out=out))


def test_the_pin_refuses_another_build_or_envelope(tmp_path, home, study_folders):
    def build(name: str):
        return lambda: {"commit": name * 40, "code_hash": "1" * 64}

    explore.Explore(_args(tmp_path), build=build("a")).pin()

    assert explore.Explore(_args(tmp_path), build=build("a")).pin()["commit"] == "a" * 40
    with pytest.raises(scratch.ScratchError, match="commit"):
        explore.Explore(_args(tmp_path), build=build("b")).pin()
    with pytest.raises(scratch.ScratchError, match="design"):
        explore.Explore(_args(tmp_path, "--envelope", "product"), build=build("a")).pin()


def test_a_full_run_refuses_when_the_study_ran_other_tasks(tmp_path, home):
    found = explore.Explore(explore.parse(["--dry-run", "--out", str(tmp_path / "out"),
                                           "--scratch", str(tmp_path / "scratch")]))
    found.check_tasks()  # the made-up study ran every held-out request for every arm

    runs = found.study_out / "evaluate" / "runs.jsonl"
    runs.write_text("".join(runs.read_text(encoding="utf-8").splitlines(keepends=True)[1:]), encoding="utf-8")
    with pytest.raises(Stopped, match="not the same tasks"):
        found.check_tasks()


def test_each_arm_is_the_studys_own_model_and_gguf(tmp_path):
    out, data = explore.dry_study(tmp_path / "study")

    found = explore_worker.study_arms(out, data, explore.ARMS)

    assert {arm: item[1] for arm, item in found.items()} == explore.DRY_NAMES
    assert all(item[0].endswith(":" + item[1]) and item[2].is_relative_to(data) for item in found.values())
    elsewhere = tmp_path / "elsewhere.gguf"
    elsewhere.write_bytes(b"GGUF")
    listed = data / "runtimes" / "learning" / "server" / "run" / "models.json"
    models = json.loads(listed.read_text(encoding="utf-8"))
    models["models"][-1]["gguf"] = str(elsewhere)
    listed.write_text(json.dumps(models), encoding="utf-8")
    with pytest.raises(scratch.ScratchError, match="isn't in the study's own profile"):
        explore_worker.study_arms(out, data, explore.ARMS)


def test_the_servers_files_are_linked_but_never_its_run_folder(tmp_path):
    source = tmp_path / "study" / "runtimes" / "learning" / "server"
    (source / "b1" / "bin").mkdir(parents=True)
    (source / "b1" / "bin" / "llama-server.exe").write_bytes(b"server")
    (source / "installed.json").write_text('{"folder": "b1"}', encoding="utf-8")
    (source / "run").mkdir()
    (source / "run" / "models.ini").write_text("[the study's]", encoding="utf-8")
    (source / "run" / "server.log").write_text("the study's log", encoding="utf-8")

    explore_worker.link_server(tmp_path / "study", tmp_path / "data")

    target = tmp_path / "data" / "runtimes" / "learning" / "server"
    assert os.path.samefile(source / "b1" / "bin" / "llama-server.exe", target / "b1" / "bin" / "llama-server.exe")
    assert (target / "installed.json").read_text(encoding="utf-8") == '{"folder": "b1"}'
    assert not os.path.samefile(source / "installed.json", target / "installed.json")
    assert not (target / "run").exists()


def test_only_this_profiles_server_instance_of_the_model_counts_as_still_running(tmp_path):
    ours = tmp_path / "data" / "runtimes" / "learning" / "server"
    exe = str(ours / "b1" / "bin" / "llama-server.exe")
    theirs = str(tmp_path / "home" / ".row-bot" / "runtimes" / "learning" / "server" / "b1" / "bin" / "llama-server.exe")
    router = (exe, [exe, "--models-preset", "models.ini", "--models-max", "1"])
    stopping = (exe, [exe, "--alias", "row-bot-personal-v3", "--ctx-size", "65536"])
    persons = (theirs, [theirs, "--alias", "row-bot-personal-v3", "--ctx-size", "32768"])

    assert explore_worker.instance_running("row-bot-personal-v3", ours, lambda: [router, stopping, persons])
    assert not explore_worker.instance_running("row-bot-personal-v3", ours, lambda: [router, persons])
    assert not explore_worker.instance_running("row-bot-personal-v7", ours, lambda: [router, stopping])


@pytest.mark.parametrize(("moment", "waits"), [((0, 24), False), ((0, 25), True), ((4, 59), True), ((5, 0), True),
                                               ((7, 59), True), ((8, 0), False), ((14, 0), False)])
def test_new_gpu_work_waits_from_0025_until_0800(moment, waits):
    assert (explore_worker.new_work_quiet(datetime(2026, 11, 11, *moment)) is not None) is waits
