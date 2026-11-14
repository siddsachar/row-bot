"""The paper's report (P1, pre-registration §3.4, §7, §10): the participant's own script. These tests are
its privacy guarantee, so they run in the pull-request lane.

On a profile seeded with canary strings (in requests, replies, tool arguments, Teach notes, lessons, file
names, thread ids, model names and version files) neither what the script prints nor the JSON it writes holds
a canary, a path or an id; the JSON follows a strict schema and an extra key stops the run; ``--health``
holds no rate and nothing a comparison decided; the run works on a checked copy and the profile is
byte-for-byte untouched; it refuses while Row-Bot holds the profile's lock; it asks before writing; the
freeze applies the minimum size (30 problems) and Holm across {H1, H2}; the thinking recheck keeps only
aggregates of the ablation's results."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage

# The serving shape imports the Agent, which opens its conversations database where the data folder is when
# it is first imported: here, the session's test folder, never a copy the script deletes.
import row_bot.agent  # noqa: F401
from row_bot.learning import evaluation
from row_bot.learning.evaluation import Action, Arm, CaseResult, ReplayCase
from row_bot.learning.packs import visible_tasks
from row_bot.learning.spending import TokenMeter
from row_bot.learning.store import LearningStore
from row_bot.prompt_cache import ModelUsage
from row_bot.providers.usage_ledger import UsageRecord, record_model_usage
from scripts import learning_paper_report as report
from tests.fixtures.learning import CapturedWork, lesson, signal
from tests.fixtures.learning_eval import ScriptedClient

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

CANARY = "zq7canary"
# A number only a comparison holds: it must never reach the health check's output.
COMPARED = 7_654_321


# ── A profile seeded with canaries ──────────────────────────────────────────


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _version(root: Path, number: int, *, rendering: str = "serving-v1@32768", phase: str = "ready") -> None:
    folder = root / "learning_models" / "versions" / f"v{number}"
    gguf = f"{CANARY}-personal-v{number}-Q8_0.gguf"
    (folder).mkdir(parents=True, exist_ok=True)
    (folder / gguf).write_bytes(b"GGUF" + CANARY.encode())
    _write_json(folder / "state.json", {
        "version": number, "phase": phase, "job_id": f"job_{CANARY}{number}", "release_id": f"rel_{CANARY}{number}",
        "runtime": "ollama", "model_ref": f"model:ollama:row-bot-personal-v{number}:{CANARY}", "gguf": gguf,
        "trained_at": 1_792_000_000.0 + number, "examples": 90 + number, "verified_at": 1_792_000_100.0,
        "kept_pass": {"pass": 2, "of": 3, "failed": [3], "losses": [0.4, 0.3, 0.2]}})
    _write_json(folder / "dataset.json", {"rendering": {"name": rendering, "serve_context": 32768}, "historical": 60,
                                          "example_ids": [f"ex_{CANARY}"], "conversations": [f"thread-{CANARY}"]})
    _write_json(folder / "release.json", {"identity": {"base": {"name": "Qwen/Qwen3.5-2B"}},
                                          "source_thread_ids": [f"thread-{CANARY}"], "label": CANARY})
    _write_json(folder / "comparison.json", {"rows": [{"passed": {"v1": COMPARED}, "label": CANARY}],
                                             "overall": {"line": f"Better at {CANARY}"}})
    (folder / "MODEL_CARD.md").write_text(f"# {CANARY}\n", encoding="utf-8")


@pytest.fixture
def profile(writer, chats, clock, learning_on):
    """A Row-Bot profile with captured work, corrections, lessons, files, versions and Learning's ledger
    rows, every one carrying the canary."""

    work = CapturedWork(writer, chats, clock)
    answers = []
    for index in range(8):
        answers.append(work.turn(f"thread-{CANARY}-{index}", f"What is in C:\\Users\\{CANARY}\\notes-{index}.txt?",
                                 f"It says {CANARY} {index}.",
                                 args={"expression": f"{CANARY} * {index}", "path": f"/home/{CANARY}/f{index}"},
                                 model_ref=f"gpt-{CANARY}")[-1])
    store = LearningStore(learning_on)
    for number in range(2):
        store.put(signal(f"thread-{CANARY}-{number}", f"sig_{CANARY}{number}", decision_id=answers[number],
                         created_at=clock() + number, note=f"Use {CANARY}.xlsx instead"))
    store.put(lesson(f"les_{CANARY}", title=f"{CANARY} lesson", text=f"Always open {CANARY}.pdf first."))
    evaluation.refresh_audit(store=store, asked_by="test seeding")
    _version(learning_on, 1, rendering="")  # a pilot: trained before the serving shape (§8.4)
    _version(learning_on, 2)
    base = learning_on / "learning_models" / "base" / "qwen3.5-2b"
    base.mkdir(parents=True)
    (base / "base-Q8_0.gguf").write_bytes(b"GGUF")
    _write_json(base / "state.json", {"model_id": "qwen3.5-2b", "phase": "ready", "gguf": "base-Q8_0.gguf",
                                      "model_ref": f"model:ollama:row-bot-personal-base:{CANARY}"})
    (learning_on / "documents").mkdir()
    (learning_on / "documents" / f"{CANARY}-contract.docx").write_bytes(b"secret")
    for purpose, provider, tokens in (("learning_test", "codex", 1_200), ("learning_test", "ollama", 800),
                                      ("learning_diagnosis", "codex", 300)):
        record_model_usage(UsageRecord(purpose, provider, f"gpt-{CANARY}", ModelUsage(tokens, 0),
                                       thread_id=f"thread-{CANARY}", created_at=clock()))
    return learning_on


@pytest.fixture
def folders(tmp_path, monkeypatch):
    """--scratch and --out, and the process environment put back after the script changes it."""

    for name in ("ROW_BOT_LEARNING_PREVIEW", "ROW_BOT_DESKTOP_NOTIFICATIONS", "ROW_BOT_AUTO_START_OLLAMA"):
        monkeypatch.delenv(name, raising=False)
    import os

    monkeypatch.setenv("ROW_BOT_DATA_DIR", os.environ["ROW_BOT_DATA_DIR"])
    return tmp_path / "scratch", tmp_path / "out"


def _hashes(root: Path) -> dict[str, str]:
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*")) if path.is_file()}


def _ids(root: Path) -> set[str]:
    store = LearningStore(root)
    found = set()
    for kind in ("episode", "decision", "signal", "lesson"):
        for item in store.find(kind, limit=100_000):
            found |= {item.id, getattr(item, "thread_id", "") or ""}
    return {item for item in found if item}


def _assert_private(text: str, profile: Path, scratch: Path, ids: set[str]) -> None:
    assert CANARY not in text
    for path in (profile, scratch, profile.parent):
        assert str(path) not in text and path.as_posix() not in text
    for item in ids:
        assert item not in text
    assert str(COMPARED) not in text


def _run(argv: list[str], *, hooks: report.Hooks | None = None) -> int:
    return report.main(argv, hooks=hooks or report.Hooks(ask=_never_asked, installed=lambda: False))


def _never_asked(_prompt: str) -> str:
    raise AssertionError("asked although --yes was given")


def _written(out: Path) -> dict[str, Any]:
    files = list(out.glob("*.json"))
    assert len(files) == 1
    return json.loads(files[0].read_text(encoding="utf-8"))


# ── --health ────────────────────────────────────────────────────────────────


def test_health_prints_its_hash_first_then_counts_only_from_a_checked_copy(profile, folders, capsys, monkeypatch):
    scratch, out = folders
    before = _hashes(profile)
    ids = _ids(profile)
    seen: dict[str, Any] = {}
    counted = report.health_report

    def health(copy):
        import os

        from row_bot import learning

        seen.update(copy=copy, env=os.environ["ROW_BOT_DATA_DIR"], learning=learning.data_dir())
        return counted(copy)

    monkeypatch.setattr(report, "health_report", health)

    assert _run(["--health", "--profile", str(profile), "--scratch", str(scratch), "--out", str(out), "--yes"]) == 0

    printed = capsys.readouterr()
    text = printed.out + printed.err
    lines = text.splitlines()
    script_sha = hashlib.sha256(Path(report.__file__).read_bytes()).hexdigest()
    assert lines[1].split()[-1] == script_sha and "Product SHA-256" in lines[2]
    assert text.index("Script SHA-256") < text.index("Copying")
    written = _written(out)
    _assert_private(text + json.dumps(written), profile, scratch, ids)
    # It worked on the copy, and the profile is byte for byte what it was.
    assert Path(seen["copy"]).parent.parent == scratch and seen["env"] == str(seen["copy"])
    assert Path(seen["learning"]) == Path(seen["copy"])
    assert _hashes(profile) == before
    assert list(scratch.iterdir()) == [] and "The scratch copy was deleted." in text
    report.validate(written, report.HEALTH)
    assert written["decisions"]["fidelity"] == {"exact": 24}  # 8 turns: 2 model calls and a tool call each
    assert written["decisions"]["kind"] == {"model_call": 16, "tool_call": 8}
    assert written["signals"]["kind"] == {"teach": 2}
    assert written["hidden_problems"]["uncorrected"] >= 1 and written["hidden_problems"]["pack_tasks"] == 32
    assert written["versions"]["pilots"] == 1 and written["versions"]["list"][1]["kept_pass"] == 2
    assert [(row["examples"], row["historical"], row["targets"]) for row in written["versions"]["list"]] == [
        (91, 60, None), (92, 60, None)]  # no kept rows in these versions
    month = next(iter(written["learning_tokens"].values()))
    assert (month["cloud"], month["local"]) == (1_500, 800)
    assert written["lessons"] == {"total": 1, "in_use": 0}


def test_health_holds_no_rate_and_nothing_a_comparison_decided(profile, folders):
    scratch, out = folders

    assert _run(["--health", "--profile", str(profile), "--scratch", str(scratch), "--out", str(out), "--yes"]) == 0

    def keys(value: Any) -> set[str]:
        if isinstance(value, dict):
            return set(value) | {key for item in value.values() for key in keys(item)}
        if isinstance(value, list):
            return {key for item in value for key in keys(item)}
        return set()

    found = keys(_written(out))
    assert not found & {"mean", "rate", "passed", "difference", "low", "high", "p_value", "baseline", "arm",
                        "interval_verdict", "verdict", "comparison", "compared"}
    assert not keys_in_schema(report.HEALTH) & {"mean", "difference", "p_value", "passed", "rate"}


def keys_in_schema(spec: Any) -> set[str]:
    if isinstance(spec, report.Obj):
        return set(spec.fields) | {key for item in spec.fields.values() for key in keys_in_schema(item)}
    if isinstance(spec, (report.Map, report.Maybe, report.Seq)):
        return keys_in_schema(getattr(spec, "value", None) or getattr(spec, "spec", None) or getattr(spec, "item"))
    return set()


def test_it_refuses_while_row_bot_holds_the_profile(profile, folders, capsys):
    from row_bot.launcher import _InstanceLock

    scratch, out = folders
    lock = _InstanceLock(profile / "launcher.lock")
    assert lock.acquire()
    try:
        code = _run(["--health", "--profile", str(profile), "--scratch", str(scratch), "--out", str(out), "--yes"])
    finally:
        lock.release()

    assert code == 2
    assert "Row-Bot is running on this profile" in capsys.readouterr().out
    assert not scratch.exists() or list(scratch.iterdir()) == []
    assert not out.exists()
    assert report.profile_in_use(profile) is False  # released: the next run may go ahead


def test_it_asks_before_writing_and_writes_nothing_without_a_yes(profile, folders, capsys):
    scratch, out = folders
    asked: list[str] = []

    def answer(prompt: str) -> str:
        asked.append(prompt)
        return "n"

    code = report.main(["--health", "--profile", str(profile), "--scratch", str(scratch), "--out", str(out)],
                       hooks=report.Hooks(ask=answer, installed=lambda: False))

    printed = capsys.readouterr().out
    assert code == 0 and len(asked) == 1 and not out.exists()
    assert "This is exactly what will be written:" in printed and "Nothing was written." in printed
    assert printed.index('"mode": "health"') < printed.index("Nothing was written.")


def test_an_extra_key_stops_the_run_and_its_name_is_never_printed(profile, folders, capsys, monkeypatch):
    scratch, out = folders
    counted = report.health_report
    monkeypatch.setattr(report, "health_report", lambda copy: {**counted(copy), f"{CANARY}-key": 1})

    code = _run(["--health", "--profile", str(profile), "--scratch", str(scratch), "--out", str(out), "--yes"])

    printed = capsys.readouterr().out
    assert code == 1 and not out.exists() and CANARY not in printed
    assert "didn't pass its own schema" in printed and list(scratch.iterdir()) == []


def test_the_schema_allows_only_fixed_keys_and_fixed_words():
    good = {"kind": {"teach": 1}, "total": 1, "withdrawn": 0}
    report.validate(good, report.HEALTH.fields["signals"])
    for bad in ({**good, "note": "x"}, {**good, "kind": {f"{CANARY}": 1}}, {**good, "total": "1"},
                {**good, "total": True}, {**good, "total": -1}):
        with pytest.raises(report.SchemaError) as raised:
            report.validate(bad, report.HEALTH.fields["signals"])
        assert CANARY not in str(raised.value)
    with pytest.raises(report.SchemaError):
        report.validate({"first_day": f"{CANARY}", "last_day": None, "days": 0, "live_episodes": 0,
                         "imported_episodes": 0, "sources": {}}, report.HEALTH.fields["recording"])


def test_a_failure_inside_the_run_names_only_its_kind(profile, folders, capsys, monkeypatch):
    scratch, out = folders
    monkeypatch.delenv("ROW_BOT_PAPER_TRACEBACK", raising=False)

    def broken(copy):
        raise OSError(f"can't open {copy}\\{CANARY}.db")

    monkeypatch.setattr(report, "health_report", broken)

    code = _run(["--health", "--profile", str(profile), "--scratch", str(scratch), "--out", str(out), "--yes"])

    printed = capsys.readouterr()
    assert code == 1 and "stopped: OSError" in printed.out and not out.exists()
    _assert_private(printed.out + printed.err, profile, scratch, set())


# ── --freeze ────────────────────────────────────────────────────────────────


class Packs:
    signature = "fake-packs-1"

    def run(self, task, arm, *, budget, meter, arm_left):
        return CaseResult(task.id, "pack", task.kind_of_work, "passed" if arm.name == "personal" else "failed",
                          "answer", tokens=400, local_tokens=400, steps=2)


def test_freeze_runs_the_newest_non_pilot_version_from_the_copy_and_reports_aggregates(profile, folders, capsys):
    scratch, out = folders
    before = _hashes(profile)
    ids = _ids(profile)
    served: dict[str, Any] = {}

    def prepare(copy, version):
        served.update(copy=copy, version=version["version"], gguf=version["_gguf"])
        return report.Prepared({"start": "model:fake:start", "personal": "model:fake:personal"}, 32768, "ollama",
                               cleanup=lambda: served.setdefault("cleaned", True))

    hooks = report.Hooks(prepare=prepare, runner=lambda arms: Packs(), container=lambda: False,
                         client=ScriptedClient(lambda *_: AIMessage(content="no idea"), local=True),
                         ask=_never_asked, installed=lambda: False)

    code = report.main(["--freeze", "--as-of", "2030-01-01", "--profile", str(profile), "--scratch", str(scratch),
                        "--out", str(out), "--yes"], hooks=hooks)

    printed = capsys.readouterr()
    assert code == 0, printed.out
    written = _written(out)
    _assert_private(printed.out + printed.err + json.dumps(written), profile, scratch, ids)
    report.validate(written, report.FREEZE)
    assert served["version"] == 2 and served["cleaned"]  # v1 is a pilot (§8.4)
    assert Path(served["gguf"]).is_relative_to(served["copy"])  # the copy's files, never the profile's
    assert _hashes(profile) == before and list(scratch.iterdir()) == []
    assert written["versions"] == {"total": 2, "pilots": 1, "candidates": 1, "evaluated": 2,
                                   "trained_on": written["versions"]["trained_on"], "kept_pass": 2, "passes": 3,
                                   "examples": 92, "historical": 60, "targets": None}
    assert written["problems"]["packs"] == 24 and written["design"]["container"] is False  # no coding without one
    h1 = written["hypotheses"]["H1"]
    assert (h1["state"], h1["reason"]) == ("not_tested", "too_few_problems")  # fewer than 30 hidden problems
    assert h1["non_inferiority"]["met"] is True  # the personal arm passes every pack task here
    h2 = written["hypotheses"]["H2"]
    assert (h2["state"], h2["reason"]) == ("not_testable", "no_lessons_or_ways_of_working")
    assert written["holm"]["adjusted"] == {"H1": None, "H2": None}
    assert written["arms"]["personal"]["splits"]["packs"]["runs"] == {"passed": 72}


def test_freeze_with_no_version_says_so(profile, folders, capsys):
    scratch, out = folders
    import shutil

    shutil.rmtree(profile / "learning_models" / "versions" / "v2")

    code = report.main(["--freeze", "--as-of", "2030-01-01", "--profile", str(profile), "--scratch", str(scratch),
                        "--out", str(out), "--yes"],
                       hooks=report.Hooks(prepare=lambda *_: pytest.fail("nothing to serve"), container=lambda: False,
                                          ask=_never_asked, installed=lambda: False))

    assert code == 0
    h1 = _written(out)["hypotheses"]["H1"]
    assert (h1["state"], h1["reason"], h1["superiority"]) == ("not_testable", "no_version", None)


def _problems(count: int, *, personal_passes: int) -> list[dict[str, Any]]:
    found = []
    for number in range(count):
        question = f"Problem {number}: {CANARY}"
        case = ReplayCase(f"dec_{number}", f"chat-{number}", f"chat-{number}", 10.0 + number, "Document lookups",
                          "exact", False, ({"type": "human", "data": {"content": question}},), None, "model:x:y",
                          Action(text="yes"), "captured", "exact_action", question)
        found.append({"id": case.decision_id, "kind_of_work": "Document lookups", "thread_id": case.thread_id,
                      "conversation_id": case.thread_id, "created_at": case.created_at, **case.to_content()})
    return found[:count]


def _freeze_result(monkeypatch, count: int, *, personal_passes: int) -> dict[str, Any]:
    class Vault:
        def hidden_tasks(self, *, asked_by, packs=None):
            return [task for task in visible_tasks() if task.pack == "document_lookups"][:4]

        def replay_tasks(self, *, asked_by):
            return _problems(count, personal_passes=personal_passes)

    monkeypatch.setattr(evaluation, "_vault", lambda _store=None: Vault())

    def respond(messages, _tools, model):
        number = int(str(messages[-1].content).split()[1].rstrip(":"))
        return AIMessage(content="yes" if model == "personal" and number < personal_passes else "no")

    return evaluation.freeze_check([Arm("start", model_ref="start"), Arm("personal", model_ref="personal")],
                                   as_of=1e9, asked_by="test", contrasts=[("start", "personal")],
                                   client=ScriptedClient(respond, local=True), runner=Packs(), meter=TokenMeter(10**9),
                                   store=LearningStore())


def _assembled(result: dict[str, Any]) -> dict[str, Any]:
    built = report.assemble_freeze(result, as_of=date(2026, 11, 2), versions=[], newest=None,
                                   prepared=report.Prepared({"start": "s", "personal": "p"}, 32768, "ollama"),
                                   refreshed={}, container=False, main_reference=False, lessons=0, behaviours=0,
                                   hours=0.1)
    built["run"] = {"on": "2026-11-03", "build": None, "build_clean": True, "script_sha256": "0" * 64,
                    "code_sha256": "1" * 64, "code_files": 1, "copy": {"files": 1, "bytes": 1, "links_skipped": 0},
                    "minutes": 1.0}
    report.validate(built, report.FREEZE)
    return built


def test_h1_is_tested_only_with_thirty_counted_problems(learning_on, monkeypatch):
    short = _assembled(_freeze_result(monkeypatch, 29, personal_passes=29))["hypotheses"]["H1"]
    enough = _assembled(_freeze_result(monkeypatch, 30, personal_passes=30))["hypotheses"]["H1"]

    assert (short["state"], short["reason"], short["supported"]) == ("not_tested", "too_few_problems", None)
    assert short["superiority"]["problems"] == 29 and short["superiority"]["p_holm"] is None
    assert (enough["state"], enough["reason"]) == ("tested", None)
    assert enough["superiority"]["problems"] == 30


def test_h1_is_judged_on_its_holm_adjusted_p_value_across_h1_and_h2(learning_on, monkeypatch):
    clear = _assembled(_freeze_result(monkeypatch, 30, personal_passes=30))
    h1 = clear["hypotheses"]["H1"]

    # Every problem better: p = 1/10,001 from the sign flips; H2 untested enters as 1, so H1's p doubles.
    assert h1["superiority"]["p_value"] == round(1 / 10_001, 6)
    assert h1["superiority"]["p_holm"] == clear["holm"]["adjusted"]["H1"] == round(2 / 10_001, 6)
    assert h1["superiority"]["met"] is True and h1["non_inferiority"]["met"] is True and h1["supported"] is True
    assert clear["holm"]["family"] == ["H1", "H2"] and clear["holm"]["adjusted"]["H2"] is None

    weak = _assembled(_freeze_result(monkeypatch, 30, personal_passes=4))["hypotheses"]["H1"]
    assert weak["state"] == "tested" and weak["superiority"]["met"] is False and weak["supported"] is False
    assert weak["superiority"]["p_holm"] == min(1.0, round(2 * weak["superiority"]["p_value"], 6))


# ── --thinking ──────────────────────────────────────────────────────────────


def _ablation_results() -> dict[str, Any]:
    pair = {"overall": {"problems": 40, "clusters": 30, "resamples": 10_000, "permutations": 10_000, "seed": 1,
                        "difference": -0.07, "low": -0.15, "high": -0.01, "p_value": 0.13, "exact": False,
                        "baseline": 0.2, "arm": 0.13, "enough": True, "interval_verdict": "worse",
                        "not_worse": False},
            "by_kind": {}}
    pair["by_kind"]["own_work"] = dict(pair["overall"])
    return {
        "build": CANARY, "started_at": f"{CANARY} 21:00",
        "rows": {"A": {"fit": 0, "rows_measured": 100, "too_long": 100, "examples": 0, "providers": [CANARY]},
                 "B": {"fit": 100, "rows_measured": 100, "too_long": 0, "examples": 90},
                 "seen_visible": [f"{CANARY}-task"]},
        "train": {"B": {"version": 3, "job": f"job_{CANARY}", "state": "evaluating", "examples": 90, "steps": 23,
                        "gpu_hours": 0.1, "error": None}},
        "serve": {"B": {"off": f"model:ollama:row-bot-personal-v3:{CANARY}"}},
        "compare": {"arms": {"B-off": f"model:ollama:row-bot-personal-v3:{CANARY}"}, "container": True,
                    "seconds": 3600, "repeats": {"visible": 3, "hidden": 3},
                    "per_arm": {"B-off": {"visible": {"seconds_per_run": 2.8, "tokens": 100, "by_kind": {CANARY: [1, 2]}}},
                                "B-on": {"visible": {"seconds_per_run": 4.5, "tokens": 120}},
                                f"{CANARY}-on": {"visible": {"seconds_per_run": 1, "tokens": 1}}},
                    "analysis": {"visible": {"pairs": {"B-on vs B-off": pair}},
                                 "hidden": {"pairs": {"B-on vs B-off": pair, f"{CANARY} vs B-off": pair},
                                            "combined_pairs": {"B-on vs B-off": pair}}}},
    }


def test_the_thinking_recheck_keeps_only_aggregates_of_the_ablation(profile, folders, capsys, monkeypatch):
    scratch, out = folders
    ran: dict[str, Any] = {}

    def ablation(copy, where):
        ran.update(copy=copy)
        (where / "l2w_ablation.json").write_text(json.dumps(_ablation_results()), encoding="utf-8")
        return 0

    monkeypatch.setattr(report, "run_ablation", ablation)

    code = _run(["--thinking", "--profile", str(profile), "--scratch", str(scratch), "--out", str(out), "--yes"])

    printed = capsys.readouterr()
    assert code == 0
    written = _written(out)
    _assert_private(printed.out + printed.err + json.dumps(written), profile, scratch, _ids(profile))
    report.validate(written, report.THINKING)
    assert Path(ran["copy"]).parent.parent == scratch
    own = written["thinking_on_vs_off"]["B"]["hidden"]["by_kind"]["own_work"]
    assert (own["difference"], own["low"], own["p_value"]) == (-0.07, -0.15, 0.13)
    assert set(written["arms"]) == {"B-off", "B-on"} and written["training"]["B"]["steps"] == 23


# ── P2: pictures, the sweep's counts, and the main model's sign-in ──────────


def test_health_counts_pictures_and_what_the_sweep_removed_or_would_remove(profile, folders, writer, chats, tmp_path,
                                                                          monkeypatch):
    from row_bot.browser import model_view
    from tests.fixtures.learning import jpeg_bytes, picture_turn

    monkeypatch.setattr(model_view, "model_sees_images", lambda model=None: True)
    picture = jpeg_bytes(CANARY)
    picture_turn(writer, chats, f"thread-{CANARY}-web", picture, question=f"Open {CANARY}.example",
                 caption=f"The {CANARY} page after that action.")
    store = LearningStore(profile)
    leftover = json.dumps({"type": "human", "data": {"content": f"{CANARY} from a chat switched off"}}).encode()
    store.put_blob(leftover)  # what an earlier build left behind
    scratch, out = folders
    ids = _ids(profile)

    assert _run(["--health", "--profile", str(profile), "--scratch", str(scratch), "--out", str(out), "--yes"]) == 0

    first = _written(out)
    _assert_private(json.dumps(first), profile, scratch, ids)
    assert first["pictures"] == {"stored": 1, "bytes": len(picture), "dropped": 0, "cap_bytes": 10_000_000_000}
    assert first["blob_sweep"]["done"] is False
    assert first["blob_sweep"]["orphans"] == {"files": 1, "bytes": len(leftover),
                                              "kinds": {"message": {"files": 1, "bytes": len(leftover)}}}
    assert first["hidden_problems"]["with_picture"] + first["hidden_problems"]["without_picture"] == \
        first["hidden_problems"]["uncorrected"] + first["hidden_problems"]["corrected"]

    # The build's first start sweeps the profile once; the next health check says what it removed.
    import time as _time

    LearningStore(profile, clock=lambda: _time.time() + 7_200).sweep_once()
    again = tmp_path / "out-again"
    assert _run(["--health", "--profile", str(profile), "--scratch", str(scratch), "--out", str(again), "--yes"]) == 0
    second = _written(again)
    assert second["blob_sweep"] == {"done": True, "removed_files": 1, "removed_bytes": len(leftover),
                                    "orphans": {"files": 0, "bytes": 0, "kinds": {}}}


def test_the_main_reference_signs_in_with_the_eval_profile_never_the_copys(profile, folders, tmp_path, monkeypatch):
    from row_bot import secret_store
    from row_bot.learning import eval_worker
    from row_bot.providers import codex

    # The script swaps both for the run; they are put back after this test.
    monkeypatch.setattr(codex, "save_codex_oauth_tokens", codex.save_codex_oauth_tokens)
    monkeypatch.setattr(secret_store, "_backend_override", secret_store._backend_override)
    scratch, out = folders
    before = _hashes(profile)
    eval_dir = tmp_path / "eval-profile"
    _write_json(eval_dir / "providers.json", {"providers": {"codex": {
        "oauth_bundle_ref": {"values": {"access_token": "ref"}}, "auth_method": "oauth_device",
        "source": "oauth_device"}}})
    tokens = {"access_token": "EVAL-ACCESS-1", "refresh_token": "EVAL-REFRESH-1", "id_token": "", "account": "acct",
              "expires_at": "", "plan_type": "plus", "revision": "rev-1"}
    asked: list[str] = []
    written: list[dict[str, Any]] = []

    def bridge(where, command, payload=None):  # the eval profile's own process, faked: a token store
        assert Path(where) == eval_dir.resolve()
        asked.append(command)
        if command == "read":
            return {"signed_in": True, **tokens}
        written.append(dict(payload))
        tokens.update({key: payload[key] for key in ("access_token", "refresh_token")}, revision="rev-2")
        return {"written": True}

    seen: dict[str, Any] = {}

    def runner(arms):
        worker_arms = [eval_worker.WorkerArm(arm.name, str(arm.model_ref), arm.serve_context is not None)
                       for arm in arms]
        credentials, _providers, unsupported = eval_worker.gather(worker_arms)
        seen.update(credentials={item.provider: dict(item.values) for item in credentials}, unsupported=unsupported,
                    main=next(arm for arm in arms if arm.name == "main"))
        # The sign-in refreshes during the run: the new tokens go back to the eval profile at once.
        codex.save_codex_oauth_tokens(codex.CodexTokenSet(access_token="EVAL-ACCESS-2",
                                                          refresh_token="EVAL-REFRESH-2"))
        return Packs()

    hooks = report.Hooks(prepare=lambda copy, version: report.Prepared(
        {"start": "model:fake:start", "personal": "model:fake:personal"}, 32768, "ollama"),
        runner=runner, container=lambda: False, main_ref=lambda: "model:codex:gpt-5.6-sol",
        client=ScriptedClient(lambda *_: AIMessage(content="no idea"), local=True), bridge=bridge,
        ask=_never_asked, installed=lambda: False)

    code = report.main(["--freeze", "--as-of", "2030-01-01", "--main-reference", "--eval-profile", str(eval_dir),
                        "--profile", str(profile), "--scratch", str(scratch), "--out", str(out), "--yes"], hooks=hooks)

    assert code == 0
    assert seen["credentials"]["codex"]["access_token"] == "EVAL-ACCESS-1" and "main" not in seen["unsupported"]
    assert seen["main"].sees_images is True  # asked with the page's picture, as it was served
    assert written and written[-1]["access_token"] == "EVAL-ACCESS-2" and written[-1]["revision"] == "rev-1"
    assert asked[0] == "read" and "write" in asked
    assert _hashes(profile) == before  # the profile, and its own sign-in, untouched
    assert _written(out)["design"]["main_reference"] is True
    assert "EVAL-ACCESS" not in json.dumps(_written(out))


def test_the_main_reference_needs_the_eval_profile(profile, folders, capsys):
    scratch, out = folders

    with pytest.raises(SystemExit):
        report.main(["--freeze", "--as-of", "2030-01-01", "--main-reference", "--profile", str(profile),
                     "--scratch", str(scratch), "--out", str(out), "--yes"])
    assert "--eval-profile" in capsys.readouterr().err
    code = report.main(["--freeze", "--as-of", "2030-01-01", "--main-reference", "--eval-profile", str(profile),
                        "--profile", str(profile), "--scratch", str(scratch), "--out", str(out), "--yes"],
                       hooks=report.Hooks(ask=_never_asked, installed=lambda: False))
    assert code == 2 and "must be the eval profile" in capsys.readouterr().out and not out.exists()


# ── A10: the exploratory 27B on Hugging Face (opt-in, typed agreement) ─────────


def _trained_on_the_profiles_rows(profile: Path, chats: Any) -> tuple[int, int]:
    """v2's record of its rows, as the planner writes it when it trains (the product's compiler, 16 GB recipe)."""

    from row_bot.learning.training import dataset as dataset_module
    from row_bot.learning.training import recipes

    compiled = dataset_module.compile_dataset(LearningStore(profile), recipes.load("16gb"), lookup=chats,
                                              measure=lambda rows, _max: [(100, 10)] * len(rows), refresh_vault=False)
    _write_json(profile / "learning_models" / "versions" / "v2" / "dataset.json", compiled.manifest())
    return compiled.examples, len(compiled.validation)


class _Exploratory:
    """A fake Hugging Face for the copy: a fake Hub and sign-in; the job finishes while the script waits."""

    def __init__(self) -> None:
        from tests.fixtures.hugging_face import FakeHub

        self.hub = FakeHub()
        self.trainer = None
        self.asked: list[str] = []

    def factory(self, copy: Path):
        from row_bot.learning.remote import hub as hub_module
        from row_bot.learning.remote.training import RemoteTrainer
        from row_bot.learning.training import jobs as jobs_module
        from tests.fixtures.hugging_face import FakeFetcher, FakeSignIn

        self.trainer = RemoteTrainer(copy, jobs=jobs_module.TrainingJobs(copy), hub=self.hub, sign_in=FakeSignIn(),
                                     fetcher=FakeFetcher(self.hub), card_mib=lambda: 32_607, start_threads=False,
                                     flavors=lambda: [hub_module.Flavor("a100-large", "1x Nvidia A100", 80, 2.5 / 60), hub_module.Flavor("l40sx1", "1x Nvidia L40S", 48, 0.03)])
        return self.trainer

    def sleep(self, _seconds: float) -> None:
        from row_bot.learning.remote import training as remote_training

        for remote in self.hub.jobs.values():
            if remote.stage != "COMPLETED":
                job_id = remote.labels["row-bot-job"]
                record = remote_training.read_record(job_id, self.trainer.root)
                self.hub.finish(remote.id, output_repo=record.output_repo, base=True, running_seconds=5_400)

    def ask(self, answer: str):
        def asked(prompt: str) -> str:
            self.asked.append(prompt)
            return answer
        return asked


def _exploratory_hooks(fake: _Exploratory, answer: str, chats: Any = None) -> report.Hooks:
    from row_bot.learning.training import serving

    def prepare(copy, version):
        number = version["version"]
        return report.Prepared({"start": f"model:fake:start-{number}", "personal": f"model:fake:personal-{number}"},
                               32768, "row_bot")

    def release_server(runtime, **options):
        return serving.ServeOutcome("ready", runtime, model_ref="model:fake:ready", probe={"agent_ready": True},
                                    tool_turn={"ok": True})

    return report.Hooks(prepare=prepare, runner=lambda arms: Packs(), container=lambda: False,
                        client=ScriptedClient(lambda *_: AIMessage(content="no idea"), local=True),
                        ask=fake.ask(answer), installed=lambda: False, hugging_face=fake.factory,
                        release_server=release_server, sleep=fake.sleep, lookup=chats)


def test_the_exploratory_27b_trains_on_exactly_v2s_rows_after_a_typed_upload_and_is_reported_apart(
        profile, folders, capsys, chats):
    scratch, out = folders
    rows, validation = _trained_on_the_profiles_rows(profile, chats)
    assert rows > 0
    before = _hashes(profile)
    ids = _ids(profile)
    fake = _Exploratory()

    code = report.main(["--freeze", "--as-of", "2030-01-01", "--hugging-face-27b", "--profile", str(profile),
                        "--scratch", str(scratch), "--out", str(out), "--yes"],
                       hooks=_exploratory_hooks(fake, "upload", chats))

    printed = capsys.readouterr()
    assert code == 0, printed.out
    written = _written(out)
    _assert_private(printed.out + printed.err + json.dumps(written), profile, scratch, ids)
    report.validate(written, report.FREEZE)
    assert fake.asked and "upload" in fake.asked[0]  # asked even with --yes: the upload is typed, never skipped
    found = written["exploratory_27b"]
    assert (found["state"], found["model"], found["recipe"]) == ("run", "qwen3.8-27b", "hf-27b-v1")
    assert found["rows"] == {"examples": rows, "validation": validation, "matched": True} and found["upload_bytes"] > 0
    assert (found["uploads_deleted"], found["minutes"], found["cost_usd"]) == (True, 90, 3.75)  # 90 min at $2.50/h
    assert set(found["contrasts"]) == {"personal27 vs start27", "personal27 vs personal"}
    assert set(found["arms"]) == {"start27", "personal27", "personal"}
    # Apart from the confirmatory family: Holm still covers H1 and H2 only.
    assert written["holm"]["family"] == ["H1", "H2"] and "personal27" not in json.dumps(written["hypotheses"])
    uploaded = b"".join(value for repo in fake.hub.repos.values() for value in repo["files"].values())
    assert not fake.hub.repos and uploaded == b""  # everything uploaded was deleted
    assert fake.hub.deleted  # the private dataset and the run's model repository
    assert _hashes(profile) == before


def test_without_the_typed_upload_nothing_leaves_and_the_27b_is_not_run(profile, folders, capsys, chats):
    scratch, out = folders
    _trained_on_the_profiles_rows(profile, chats)
    fake = _Exploratory()

    code = report.main(["--freeze", "--as-of", "2030-01-01", "--hugging-face-27b", "--profile", str(profile),
                        "--scratch", str(scratch), "--out", str(out), "--yes"],
                       hooks=_exploratory_hooks(fake, "y", chats))

    assert code == 0
    found = _written(out)["exploratory_27b"]
    assert (found["state"], found["reason"]) == ("not_run", "declined")
    assert fake.hub.calls == [] and not fake.hub.repos  # nothing uploaded, no job


def test_rows_that_cant_be_rebuilt_exactly_are_never_uploaded(profile, folders):
    scratch, out = folders
    fake = _Exploratory()  # v2's record names rows the copy doesn't hold (the profile fixture's own)

    code = report.main(["--freeze", "--as-of", "2030-01-01", "--hugging-face-27b", "--profile", str(profile),
                        "--scratch", str(scratch), "--out", str(out), "--yes"],
                       hooks=_exploratory_hooks(fake, "upload"))

    assert code == 0
    found = _written(out)["exploratory_27b"]
    assert (found["state"], found["reason"], found["rows"]["matched"]) == ("not_run", "rows_not_reproducible", False)
    assert fake.asked == [] and fake.hub.calls == []
# ── H2 and H3 (L4a) ─────────────────────────────────────────────────────────


def _corrected(count: int) -> list[dict[str, Any]]:
    found = []
    for number in range(count):
        question = f"Corrected {number}: {CANARY}"
        case = ReplayCase(f"cor_{number}", f"chat-c{number}", f"chat-c{number}", 10.0 + number, "Document lookups",
                          "exact", False, ({"type": "human", "data": {"content": question}},), None, "model:x:y",
                          Action(text="right"), "corrected", "exact_action", question)
        found.append({"id": case.decision_id, "kind_of_work": "Document lookups", "thread_id": case.thread_id,
                      "conversation_id": case.thread_id, "created_at": case.created_at, **case.to_content()})
    return found


class MainPacks:
    signature = "fake-packs-main"

    def run(self, task, arm, *, budget, meter, arm_left):
        return CaseResult(task.id, "pack", task.kind_of_work, "passed", "answer", tokens=400, steps=2)


def _h2_result(monkeypatch, count: int) -> dict[str, Any]:
    uncorrected = _problems(30, personal_passes=0)

    class Vault:
        def hidden_tasks(self, *, asked_by, packs=None):
            return [task for task in visible_tasks() if task.pack == "document_lookups"][:4]

        def replay_tasks(self, *, asked_by):
            return uncorrected + _corrected(count)

    monkeypatch.setattr(evaluation, "_vault", lambda _store=None: Vault())

    def respond(messages, _tools, model):
        # The main model repeats its own uncorrected work; only with the lessons in use does it get the corrected
        # problems right.
        guided = any("Guidance from earlier work" in str(item.content) for item in messages)
        if model != "main":
            return AIMessage(content="no")
        corrected = str(messages[-1].content).startswith("Corrected")
        return AIMessage(content="right" if corrected and guided else "yes")

    arms = [Arm("start", model_ref="start"), Arm("personal", model_ref="personal"), Arm("main", model_ref="main"),
            Arm("learning", model_ref="main", guidance=("Answer from the current rota.",))]
    return evaluation.freeze_check(arms, as_of=1e9, asked_by="test",
                                   contrasts=[("start", "personal"), ("main", "learning")],
                                   client=ScriptedClient(respond, local=True), runner=MainPacks(),
                                   meter=TokenMeter(10**9), store=LearningStore())


def _run_line(built: dict[str, Any]) -> dict[str, Any]:
    built["run"] = {"on": "2026-11-03", "build": None, "build_clean": True, "script_sha256": "0" * 64,
                    "code_sha256": "1" * 64, "code_files": 1, "copy": {"files": 1, "bytes": 1, "links_skipped": 0},
                    "minutes": 1.0}
    report.validate(built, report.FREEZE)
    return built


def test_h2_compares_the_main_model_with_and_without_what_learning_put_in_use(learning_on, monkeypatch):
    built = _run_line(report.assemble_freeze(
        _h2_result(monkeypatch, 30), as_of=date(2026, 11, 2), versions=[], newest=None,
        prepared=report.Prepared({"start": "s", "personal": "p"}, 32768, "ollama"), refreshed={}, container=False,
        main_reference=False, lessons=2, behaviours=1, hours=0.1))

    h2 = built["hypotheses"]["H2"]
    assert (h2["state"], h2["reason"]) == ("tested", None)
    assert h2["superiority"]["split"] == "real_corrected" and h2["superiority"]["problems"] == 30
    assert h2["superiority"]["difference"] == 1.0 and h2["superiority"]["met"] is True
    assert h2["non_inferiority"]["split"] == "real_uncorrected" and h2["non_inferiority_packs"]["split"] == "packs"
    assert h2["non_inferiority"]["met"] is True and h2["non_inferiority_packs"]["met"] is True and h2["supported"]
    # §7: H2 also under version 2 of the checker; Holm across {H1, H2} in v1.
    assert h2["sensitivity"]["checker_versions"] == 2 and h2["sensitivity"]["metric_v2"]["difference"] == 1.0
    assert built["holm"]["family"] == ["H1", "H2"] and built["holm"]["adjusted"]["H2"] is not None
    assert built["hypotheses"]["H1"]["sensitivity"]["metric_v2"] is not None

    few = report.assemble_freeze(_h2_result(monkeypatch, 29), as_of=date(2026, 11, 2), versions=[], newest=None,
                                 prepared=report.Prepared({"start": "s", "personal": "p"}, 32768, "ollama"),
                                 refreshed={}, container=False, main_reference=False, lessons=2, behaviours=0,
                                 hours=0.1)["hypotheses"]["H2"]
    assert (few["state"], few["reason"]) == ("not_tested", "too_few_problems")
    unrun = report.assemble_freeze({}, as_of=date(2026, 11, 2), versions=[], newest=None,
                                   prepared=report.Prepared({}, None, "ollama", reason="no_version"), refreshed={},
                                   container=False, main_reference=False, lessons=1, behaviours=0,
                                   hours=0.1)["hypotheses"]["H2"]
    assert (unrun["state"], unrun["reason"]) == ("not_testable", "not_run")


def _h3_world(monkeypatch, generations: int, *, final: bool = True, problems: int = 30) -> LearningStore:
    from row_bot.learning.improver import archive

    store = LearningStore()
    first = archive.ensure_first(store)
    judged: dict[str, dict[str, list]] = {}

    class Vault:
        def judgments(self, trial):
            return judged.get(trial, {})

    monkeypatch.setattr(evaluation, "_vault", lambda _store=None: Vault())
    rows = [(f"p{index}", f"chat{index % 10}", "document_lookups", 0.0, 1.0) for index in range(problems)]
    side = {"changes": [["better", "document_lookups"]], "tokens": 500_000, "release": first.id}
    control = {"changes": [], "tokens": 1_000_000, "release": first.id}
    arm = {"role": "incumbent", "release": first.id, "version": 1, "accepted": 1, "better": 1, "tokens": 500_000,
           "yield": 2.0}
    for number in range(generations + 1 - int(final)):
        summary = {"id": f"t{number}", "at": 1.0, "incumbent": first.id, "control": first.id, "winner": first.id,
                   "arms": [arm], "arm_tokens": 2_000_000, "problems": problems}
        if number:
            summary["judged"] = {"inherited": side, "control": control, "same": False}
            judged[f"t{number}"] = {"better": rows}
        archive.record_trial(store, summary)
    if final:
        archive.record_trial(store, {"id": "final", "final": True, "at": 1.0, "problems": problems,
                                     "arm_tokens": 2_000_000, "incumbent": first.id, "control": first.id,
                                     "judged": {"inherited": side, "control": control, "same": False}, "arms": [arm]})
        judged["final"] = {"better": rows}
    return store


def test_h3_needs_three_generations_each_judged_on_thirty_fresh_problems_and_joins_holm_in_v2(learning_on,
                                                                                            monkeypatch):
    h3 = report.h3_report(_h3_world(monkeypatch, 3), final_state="done", ran_final=True)

    assert (h3["state"], h3["reason"], h3["trials"]) == ("tested", None, 3)
    assert [row["difference"] for row in h3["generations"]] == [2.0, 2.0, 2.0]
    assert h3["estimate"] == 2.0 and h3["low"] == 2.0 and h3["p_value"] == 0.0
    assert h3["final_set"]["problems"] == 30 and h3["final_set"]["arms"][0]["yield"] == 2.0

    nothing = report.Prepared({}, None, "ollama", reason="no_version")
    v2 = _run_line(report.assemble_freeze({}, as_of=date(2026, 11, 12), versions=[], newest=None, prepared=nothing,
                                          refreshed={}, container=False, main_reference=False, lessons=0,
                                          behaviours=0, hours=0.1, paper="v2", h3=h3))
    assert v2["holm"]["family"] == ["H1", "H2", "H3"] and v2["holm"]["adjusted"]["H3"] == 0.0
    assert v2["hypotheses"]["H3"]["met"] is True and v2["design"]["paper"] == "v2"
    v1 = _run_line(report.assemble_freeze({}, as_of=date(2026, 11, 2), versions=[], newest=None, prepared=nothing,
                                          refreshed={}, container=False, main_reference=False, lessons=0,
                                          behaviours=0, hours=0.1, h3=h3))
    assert v1["holm"]["family"] == ["H1", "H2"] and v1["hypotheses"]["H3"]["p_holm"] is None


@pytest.mark.parametrize(("generations", "final", "problems", "reason"), [
    (2, True, 30, "too_few_generations"), (3, True, 29, "too_few_problems"), (3, False, 30, "final_set_not_run"),
])
def test_h3_below_the_minimum_is_not_tested(learning_on, monkeypatch, generations, final, problems, reason):
    h3 = report.h3_report(_h3_world(monkeypatch, generations, final=final, problems=problems), final_state=None,
                          ran_final=False)

    assert (h3["state"], h3["reason"], h3["p_value"]) == ("not_tested", reason, None)


# ── A10's rows: the version's own kept rows, never a rebuild by a later build (C1, 28 Oct) ──────────


def _kept(profile: Path, chats: Any) -> tuple[int, int]:
    """v2 trained as the release now keeps it: its record and its exact rows beside it."""

    from row_bot.learning.training import dataset as dataset_module
    from row_bot.learning.training import kept_rows, recipes

    compiled = dataset_module.compile_dataset(LearningStore(profile), recipes.load("16gb"), lookup=chats,
                                              measure=lambda rows, _max: [(100, 10)] * len(rows), refresh_vault=False)
    folder = profile / "learning_models" / "versions" / "v2"
    _write_json(folder / "dataset.json", compiled.manifest())
    data = profile / "learning_models" / "runs" / "job_v2_rows" / "data"
    dataset_module.write(compiled, data)
    assert kept_rows.keep(data, folder, compiled.manifest())
    return compiled.examples, len(compiled.validation)


def test_health_counts_a_versions_training_targets_from_its_kept_rows(profile, folders, chats):
    scratch, out = folders
    _version(profile, 2)
    rows, _validation = _kept(profile, chats)
    targets = 0
    for line in (profile / "learning_models" / "versions" / "v2" / "rows" / "examples.jsonl").read_text(
            encoding="utf-8").splitlines():
        targets += sum(1 for message in json.loads(line)["messages"]
                       if message["role"] == "assistant" and message.get("weight"))

    assert _run(["--health", "--profile", str(profile), "--scratch", str(scratch), "--out", str(out), "--yes"]) == 0
    found = _written(out)["versions"]["list"][-1]
    assert found["version"] == 2 and found["targets"] == targets >= rows > 0


class _Gone:
    """The copy's chats with one of them deleted since v2 trained."""

    def __init__(self, chats: Any, deleted: str) -> None:
        self.chats, self.deleted = chats, deleted

    def thread(self, thread_id: str):
        from row_bot.learning.scope import ThreadInfo

        return ThreadInfo(exists=False) if thread_id == self.deleted else self.chats.thread(thread_id)

    def __getattr__(self, name: str):
        return getattr(self.chats, name)


def test_the_27b_uploads_the_versions_own_kept_rows_even_when_a_later_build_cant_rebuild_them(
        profile, folders, chats, monkeypatch):
    scratch, out = folders
    rows, validation = _kept(profile, chats)
    monkeypatch.setattr(report, "rebuild_rows", lambda *_a, **_k: ([], [], False))  # a later build's compiler
    fake = _Exploratory()

    code = report.main(["--freeze", "--as-of", "2030-01-01", "--hugging-face-27b", "--profile", str(profile),
                        "--scratch", str(scratch), "--out", str(out), "--yes"],
                       hooks=_exploratory_hooks(fake, "upload", chats))

    assert code == 0
    found = _written(out)["exploratory_27b"]
    assert (found["state"], found["rows"]) == ("run", {"examples": rows, "validation": validation, "matched": True})


@pytest.mark.parametrize("case", ["deleted_chat", "tampered"])
def test_kept_rows_from_a_deleted_chat_or_that_changed_are_never_uploaded(profile, folders, chats, case):
    from row_bot.learning.training import kept_rows

    scratch, out = folders
    _kept(profile, chats)
    folder = profile / "learning_models" / "versions" / "v2"
    lookup = chats
    if case == "deleted_chat":
        lookup = _Gone(chats, json.loads((folder / "dataset.json").read_text(encoding="utf-8"))["conversations"][0])
    else:
        rows = folder / kept_rows.FOLDER / kept_rows.EXAMPLES
        rows.write_text(rows.read_text(encoding="utf-8").replace("a", "b", 1), encoding="utf-8")
    fake = _Exploratory()

    code = report.main(["--freeze", "--as-of", "2030-01-01", "--hugging-face-27b", "--profile", str(profile),
                        "--scratch", str(scratch), "--out", str(out), "--yes"],
                       hooks=_exploratory_hooks(fake, "upload", lookup))

    assert code == 0
    found = _written(out)["exploratory_27b"]
    assert (found["state"], found["reason"]) == ("not_run", {"deleted_chat": "source_deleted",
                                                             "tampered": "rows_changed"}[case])
    assert fake.asked == [] and fake.hub.calls == []  # never asked, nothing uploaded


def test_a_newest_version_trained_by_an_older_build_without_kept_rows_says_why_it_is_not_run(profile, folders):
    """The participant's case on a freeze day after a switch: rows weren't kept yet and today's build renders
    them differently, so they can't be rebuilt exactly; the report says so (never an upload of other rows)."""

    scratch, out = folders
    fake = _Exploratory()

    code = report.main(["--freeze", "--as-of", "2030-01-01", "--hugging-face-27b", "--profile", str(profile),
                        "--scratch", str(scratch), "--out", str(out), "--yes"],
                       hooks=_exploratory_hooks(fake, "upload"))

    assert code == 0
    found = _written(out)["exploratory_27b"]
    assert (found["state"], found["reason"]) == ("not_run", "rows_not_reproducible") and fake.hub.calls == []


# ── A12: v2's window, the combined H1, the judge and the larger local reference ──


def _eval_profile(tmp_path: Path, monkeypatch) -> tuple[Path, Any]:
    """An eval profile whose ChatGPT sign-in is read through a fake bridge (the script's own swaps put back)."""

    from row_bot import secret_store
    from row_bot.providers import codex

    monkeypatch.setattr(codex, "save_codex_oauth_tokens", codex.save_codex_oauth_tokens)
    monkeypatch.setattr(secret_store, "_backend_override", secret_store._backend_override)
    eval_dir = tmp_path / "eval-profile"
    _write_json(eval_dir / "providers.json", {"providers": {"codex": {
        "oauth_bundle_ref": {"values": {"access_token": "ref"}}, "auth_method": "oauth_device",
        "source": "oauth_device"}}})
    tokens = {"access_token": "EVAL-ACCESS-1", "refresh_token": "EVAL-REFRESH-1", "id_token": "", "account": "acct",
              "expires_at": "", "plan_type": "plus", "revision": "rev-1"}

    def bridge(where, command, payload=None):
        return {"signed_in": True, **tokens} if command == "read" else {"written": True}

    return eval_dir, bridge


def _grading(messages, _tools, _model):
    """A judge that passes exactly the reference (so it calibrates as reliable); every other call: "no idea"."""

    if "You grade answers" in str(messages[0].content):
        prompt = str(messages[-1].content)
        reference = prompt.split("A correct answer:\n", 1)[1].split("\n\nAnswer to grade:\n", 1)[0]
        candidate = prompt.split("\n\nAnswer to grade:\n", 1)[1].split("\n\nDoes the answer", 1)[0]
        return AIMessage(content="pass" if candidate == reference else "fail")
    return AIMessage(content="no idea")


def _v1_report(path: Path) -> dict[str, Any]:
    """A v1 freeze report as the script wrote it before A12 (H1 with a real interval), saved at ``path``."""

    with pytest.MonkeyPatch.context() as patch:
        found = _assembled(_freeze_result(patch, 8, personal_passes=5))
    path.write_text(json.dumps(found), encoding="utf-8")
    return found


def test_a_v2_freeze_reports_its_window_the_combined_h1_and_the_judges_calibration(profile, folders, tmp_path,
                                                                                   capsys, monkeypatch):
    scratch, out = folders
    v1 = _v1_report(tmp_path / "v1-freeze.json")
    eval_dir, bridge = _eval_profile(tmp_path, monkeypatch)
    before = _hashes(profile)
    ids = _ids(profile)
    hooks = report.Hooks(prepare=lambda copy, version: report.Prepared(
        {"start": "model:fake:start", "personal": "model:fake:personal"}, 32768, "ollama"),
        runner=lambda arms: Packs(), container=lambda: False, main_ref=lambda: "model:codex:gpt-5.6-sol",
        client=ScriptedClient(_grading, local=True), bridge=bridge, ask=_never_asked, installed=lambda: False)

    code = report.main(["--freeze", "--as-of", "2030-01-01", "--paper", "v2", "--main-reference", "--eval-profile",
                        str(eval_dir), "--combine-with", str(tmp_path / "v1-freeze.json"), "--judge-long-answers",
                        "--profile", str(profile), "--scratch", str(scratch), "--out", str(out), "--yes"], hooks=hooks)

    printed = capsys.readouterr()
    assert code == 0, printed.out
    written = _written(out)
    _assert_private(printed.out + printed.err + json.dumps(written), profile, scratch, ids)
    report.validate(written, report.FREEZE)
    assert "after 2026-11-02 (the v1 freeze)" in printed.out
    assert written["design"]["since"] == "2026-11-02" and written["problems"]["before_since"] == 0
    assert written["holm"]["family"] == ["H1", "H2", "H3"]
    combined = written["hypotheses"]["H1"]["combined_v1_v2"]
    assert combined["v1"] == {key: v1["hypotheses"]["H1"]["superiority"][key] for key in ("difference", "low", "high")}
    assert combined["state"] == "not_run"  # here v2's arms tie on every problem: no interval to weigh
    judged = written["judged_long_answers"]
    assert judged["calibration"]["reliable"] is True and judged["calibration"]["cases"] >= 10
    assert judged["state"] == "run" and set(judged["runs"]) == {"start", "personal", "main"}
    assert set(written["gap_closed"]) == {"real_uncorrected"}
    assert _hashes(profile) == before


def test_the_v1_report_to_combine_with_must_be_the_v1_freeze_report(learning_on, tmp_path):
    good = tmp_path / "v1.json"
    v1 = _v1_report(good)
    superiority = v1["hypotheses"]["H1"]["superiority"]

    assert report.read_v1_report(good) == {key: superiority[key] for key in ("difference", "low", "high")}

    def changed(**parts: Any) -> dict[str, Any]:
        found = json.loads(json.dumps(v1))
        found["design"].update(parts.pop("design", {}))
        found["hypotheses"]["H1"].update(parts.pop("h1", {}))
        return {**found, **parts}

    for bad in ("not a report", changed(**{f"{CANARY}-note": 1}), changed(design={"paper": "v2"}),
                changed(design={"as_of": "2026-11-03"}), changed(h1={"superiority": None}),
                changed(h1={"superiority": {**superiority, "low": 0.5, "high": 0.5}})):
        path = tmp_path / "bad.json"
        path.write_text(bad if isinstance(bad, str) else json.dumps(bad), encoding="utf-8")
        with pytest.raises(report.Refused) as refused:
            report.read_v1_report(path)
        assert CANARY not in refused.value.message and str(tmp_path) not in refused.value.message


def test_a_bad_v1_report_stops_the_run_before_anything_is_copied(profile, folders, tmp_path, capsys, monkeypatch):
    scratch, out = folders
    eval_dir, _bridge = _eval_profile(tmp_path, monkeypatch)
    (tmp_path / "v1.json").write_text("{}", encoding="utf-8")

    code = report.main(["--freeze", "--as-of", "2026-11-12", "--paper", "v2", "--main-reference", "--eval-profile",
                        str(eval_dir), "--combine-with", str(tmp_path / "v1.json"), "--profile", str(profile),
                        "--scratch", str(scratch), "--out", str(out), "--yes"],
                       hooks=report.Hooks(ask=_never_asked, installed=lambda: False))

    assert code == 2 and "--combine-with" in capsys.readouterr().out
    assert not scratch.exists() and not out.exists()


def test_h1_combined_over_v1_and_v2_is_a_fixed_effect_inverse_variance_estimate():
    error = math.sqrt(1 / 200)  # two standard errors of 0.1 (95% intervals 0.392 wide)
    equal = report.combine_h1({"difference": 0.1, "low": -0.096, "high": 0.296},
                              {"difference": 0.3, "low": 0.104, "high": 0.496, "problems": 40})
    assert equal["state"] == "run" and equal["reason"] is None
    assert equal["estimate"] == pytest.approx(0.2, abs=1e-6) and equal["v1_weight"] == pytest.approx(0.5, abs=1e-6)
    assert equal["se"] == pytest.approx(error, abs=1e-6)
    assert (equal["low"], equal["high"]) == (pytest.approx(0.2 - 1.96 * error, abs=1e-6),
                                             pytest.approx(0.2 + 1.96 * error, abs=1e-6))
    assert equal["z"] == pytest.approx(0.2 / error, abs=1e-5)
    assert equal["p_value"] == pytest.approx(math.erfc(2.0), abs=1e-6)  # |z| / sqrt(2) = 2 exactly
    assert equal["v2"] == {"difference": 0.3, "low": 0.104, "high": 0.496}

    # v1's interval half as wide: four times the weight.
    weighted = report.combine_h1({"difference": 0.1, "low": 0.002, "high": 0.198},
                                 {"difference": 0.3, "low": 0.104, "high": 0.496})
    assert weighted["v1_weight"] == pytest.approx(0.8, abs=1e-6)
    assert weighted["estimate"] == pytest.approx(0.14, abs=1e-6)

    first = {"difference": 0.1, "low": -0.096, "high": 0.296}
    assert report.combine_h1(first, None)["reason"] == "no_v2_estimate"
    assert report.combine_h1(first, {"difference": None, "low": None, "high": None})["reason"] == "no_v2_estimate"
    assert report.combine_h1(first, {"difference": 0.0, "low": 0.0, "high": 0.0})["reason"] == "no_interval"


def test_v2_needs_the_main_reference_and_a_freeze_after_v1s(capsys):
    folders = ["--profile", "p", "--scratch", "s", "--out", "o"]
    for argv, message in ((["--as-of", "2026-11-12", "--paper", "v2"], "--main-reference"),
                          (["--as-of", "2026-11-02", "--paper", "v2", "--main-reference", "--eval-profile", "e"],
                           "after the v1 freeze"),
                          (["--as-of", "2026-11-12", "--combine-with", "v1.json"], "--paper v2"),
                          (["--as-of", "2026-11-12", "--judge-long-answers"], "--eval-profile")):
        with pytest.raises(SystemExit):
            report.parse(["--freeze", *argv, *folders])
        assert message in capsys.readouterr().err
    with pytest.raises(SystemExit):
        report.parse(["--health", "--local-reference", "qwen3.8:27b", *folders])
    assert "--local-reference goes with --freeze" in capsys.readouterr().err


def _local_hooks(seen: dict[str, Any], local: Any) -> report.Hooks:
    def runner(arms):
        seen["arms"] = {arm.name: arm for arm in arms}
        return Packs()

    return report.Hooks(prepare=lambda copy, version: report.Prepared(
        {"start": "model:fake:start", "personal": "model:fake:personal"}, 32768, "ollama",
        cleanup=lambda: seen.setdefault("withdrawn", True)), runner=runner, container=lambda: False,
        client=ScriptedClient(lambda *_: AIMessage(content="no idea"), local=True), ask=_never_asked,
        installed=lambda: False, local_reference=local)


def test_the_local_reference_arm_is_served_like_the_personal_arms_and_runs_model_by_model(profile, folders, capsys,
                                                                                       monkeypatch):
    scratch, out = folders
    seen: dict[str, Any] = {}
    orders: list[str] = []
    measured = evaluation.freeze_check

    def freeze_check(arms, **options):
        orders.append(options["order"])
        return measured(arms, **options)

    monkeypatch.setattr(evaluation, "freeze_check", freeze_check)

    def local(copy, model, context):
        seen.update(copy=copy, model=model, context=context)
        return report.Prepared({"local_large": f"model:ollama:{model}"}, context, "ollama",
                               cleanup=lambda: seen.setdefault("unloaded", True))

    code = report.main(["--freeze", "--as-of", "2030-01-01", "--local-reference", "qwen3.8:27b", "--profile",
                        str(profile), "--scratch", str(scratch), "--out", str(out), "--yes"],
                       hooks=_local_hooks(seen, local))

    printed = capsys.readouterr()
    assert code == 0, printed.out
    written = _written(out)
    _assert_private(printed.out + printed.err + json.dumps(written), profile, scratch, _ids(profile))
    report.validate(written, report.FREEZE)
    arm = seen["arms"]["local_large"]
    assert (arm.model_ref, arm.serve_context, arm.sees_images) == ("model:ollama:qwen3.8:27b", 32768, False)
    assert seen["context"] == 32768 and Path(seen["copy"]).parent.parent == scratch
    assert orders == ["model"] and seen["unloaded"] and seen["withdrawn"]  # nothing stays loaded or registered
    assert (written["design"]["local_reference"], written["design"]["order"]) == ("qwen3.8:27b", "model")
    assert {"local_large vs start", "personal vs local_large"} <= set(written["contrasts"])
    assert written["arms"]["local_large"]["splits"]["packs"]["runs"] == {"failed": 72}
    assert written["holm"]["family"] == ["H1", "H2"] and "local_large" not in json.dumps(written["hypotheses"])


def test_a_local_reference_ollama_doesnt_have_stops_the_run_and_nothing_is_written(profile, folders, capsys):
    scratch, out = folders
    seen: dict[str, Any] = {}

    def missing(copy, model, context):
        raise report._ModeFailed("--local-reference names a model this computer's Ollama doesn't have")

    code = report.main(["--freeze", "--as-of", "2030-01-01", "--local-reference", "qwen3.8:27b", "--profile",
                        str(profile), "--scratch", str(scratch), "--out", str(out), "--yes"],
                       hooks=_local_hooks(seen, missing))

    printed = capsys.readouterr().out
    assert code == 1 and "doesn't have" in printed and "Nothing was written." in printed
    assert not out.exists() and seen["withdrawn"] and "arms" not in seen


class _FakeOllama:
    """This computer's Ollama, faked: which models it has and what was unloaded. It can't pull or create."""

    base_url = "http://127.0.0.1:11434"

    def __init__(self, models: set[str], *, running: bool = True) -> None:
        self.models, self.running, self.unloaded = models, running, []

    def version(self):
        return "0.13.0" if self.running else None

    def show(self, name):
        if name not in self.models:
            raise KeyError(name)
        return {"capabilities": ["completion", "tools"]}

    def unload(self, name):
        self.unloaded.append(name)
        return True


def test_the_local_reference_is_a_model_already_in_ollama_served_at_the_personal_context_thinking_off(
        monkeypatch, tmp_path):
    from row_bot import models
    from row_bot.learning.training import serving

    saved: list[tuple[str, dict[str, Any]]] = []
    cleared: list[str] = []
    monkeypatch.setattr(serving, "refresh_ollama_catalog", lambda: 0)
    monkeypatch.setattr(models, "local_model_settings", lambda name: {})
    monkeypatch.setattr(models, "set_local_model_settings", lambda name, **settings: saved.append((name, settings)))
    monkeypatch.setattr(models, "clear_local_model_settings", cleared.append)

    for ollama, message in ((_FakeOllama(set(), running=False), "needs Ollama running"),
                            (_FakeOllama({"qwen3.5:9b"}), "doesn't have")):
        monkeypatch.setattr(serving, "OllamaRuntime", lambda found=ollama: found)
        with pytest.raises(report._ModeFailed, match=message):
            report.serve_local_reference(tmp_path, "qwen3.8:27b", 32768)
    assert saved == []

    ollama = _FakeOllama({"qwen3.8:27b"})
    monkeypatch.setattr(serving, "OllamaRuntime", lambda: ollama)
    prepared = report.serve_local_reference(tmp_path, "qwen3.8:27b", 32768)

    assert prepared.refs == {"local_large": "model:ollama:qwen3.8:27b"} and prepared.serve_context == 32768
    assert saved == [("qwen3.8:27b", {"context": 32768, "reasoning": "off"})]
    prepared.cleanup()
    assert ollama.unloaded == ["qwen3.8:27b"] and cleared == ["qwen3.8:27b"]


class _Judge:
    model_ref = "main"
    tokens = 0

    def judge(self, task, reference, candidate):
        return candidate == reference


def _a12_result(monkeypatch) -> dict[str, Any]:
    """A freeze with every A12 part: the window, the main and local reference arms, a judge, a long answer."""

    problems = _problems(8, personal_passes=0)
    early = dict(problems[0], id="dec_early", created_at=1.0)
    question = "Problem 99: tell me"
    long_answer = ReplayCase("dec_long", "chat-long", "chat-long", 50.0, "Document lookups", "exact", False,
                             ({"type": "human", "data": {"content": question}},), None, "model:x:y",
                             Action(text="yes " + "and more " * 30), "captured", "exact_action", question)

    class Vault:
        def hidden_tasks(self, *, asked_by, packs=None):
            return [task for task in visible_tasks() if task.pack == "document_lookups"][:4]

        def replay_tasks(self, *, asked_by):
            return [early, *problems, {"id": "dec_long", "kind_of_work": "Document lookups", "thread_id": "chat-long",
                                       "conversation_id": "chat-long", "created_at": 50.0, **long_answer.to_content()}]

    monkeypatch.setattr(evaluation, "_vault", lambda _store=None: Vault())

    def respond(messages, _tools, model):
        number = int(str(messages[-1].content).split()[1].rstrip(":"))
        if number == 99:
            return AIMessage(content="no " + "and more " * 30)
        good = model in ("main", "local_large") or (model == "personal" and number % 2 == 0)
        return AIMessage(content="yes" if good else "no")

    arms = [Arm(name, model_ref=name) for name in ("start", "personal", "main", "local_large")]
    contrasts = [("start", "personal"), ("start", "main"), ("start", "local_large"), ("local_large", "personal")]
    return evaluation.freeze_check(arms, as_of=1e9, since=5.0, asked_by="test", contrasts=contrasts,
                                   client=ScriptedClient(respond, local=True), runner=Packs(), meter=TokenMeter(10**9),
                                   store=LearningStore(), judge=_Judge(),
                                   calibration=evaluation.Calibration(10, 10, 0, 0), order="model")


def test_the_schema_takes_a12s_parts_and_a_v1_report_is_unchanged(learning_on, monkeypatch):
    v1 = _assembled(_freeze_result(monkeypatch, 8, personal_passes=5))
    assert not {"gap_closed", "judged_long_answers"} & set(v1)
    assert not {"since", "local_reference", "order"} & set(v1["design"]) and "before_since" not in v1["problems"]
    assert "combined_v1_v2" not in v1["hypotheses"]["H1"]

    built = _run_line(report.assemble_freeze(
        _a12_result(monkeypatch), as_of=date(2026, 11, 12), versions=[], newest=None,
        prepared=report.Prepared({"start": "s", "personal": "p"}, 32768, "ollama"), refreshed={}, container=False,
        main_reference=True, lessons=0, behaviours=0, hours=0.1, paper="v2",
        combine_with={"difference": 0.1, "low": -0.1, "high": 0.3}, local_reference="qwen3.8:27b"))

    assert (built["design"]["since"], built["problems"]["before_since"]) == ("2026-11-02", 1)
    assert built["problems"]["real_uncorrected"] == 9  # 8 short problems after the window opened, and the long one
    gap = built["gap_closed"]["real_uncorrected"]
    assert (gap["problems"], gap["estimate"]) == (8, 0.5)  # half the main model's lead; the long answer isn't counted
    assert built["judged_long_answers"]["runs"]["main"]["undecided"] == 3
    h1 = built["hypotheses"]["H1"]
    assert {"teacher_agreement", "right_tool", "judged_long_answers"} <= set(h1["sensitivity"])
    assert h1["combined_v1_v2"]["state"] == "run" and built["holm"]["family"] == ["H1", "H2", "H3"]
    assert {"local_large vs start", "personal vs local_large"} <= set(built["contrasts"])
    assert built["design"]["local_reference"] == "qwen3.8:27b"
    with pytest.raises(report.SchemaError):
        report.validate({**built, "design": {**built["design"], "local_reference": "qwen3.8:27b-canary"}},
                        report.FREEZE)


def test_the_27b_sign_in_finishes_when_the_participant_authorizes(data_dir, tmp_path, monkeypatch):
    """13 Nov: the v2 freeze stopped at "the Hugging Face sign-in didn't finish" though the participant had
    authorized. Here Hugging Face answers the first poll "pending" and the next one with the token; a second poller
    that took that answer would leave the script's loop with nothing pending."""

    import functools
    import threading

    import httpx

    from row_bot import secret_store
    from row_bot.learning.remote import auth

    main = threading.current_thread()
    other_took_it = threading.Event()
    asked = {"main": 0}

    def hugging_face(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/oauth/register":
            return httpx.Response(201, json={"client_id": "client-123"})
        if path == "/oauth/device":
            return httpx.Response(200, json={"device_code": "device-xyz", "user_code": "ABCD-EFGH",
                                             "verification_uri": "https://hf.co/oauth/device", "expires_in": 300})
        if path == "/oauth/token":
            on_main = threading.current_thread() is main
            if not asked["main"]:  # the participant authorizes after the script's first poll
                asked["main"] += on_main
                return httpx.Response(400, json={"error": "authorization_pending"})
            if not on_main:
                other_took_it.set()
            return httpx.Response(200, json={"access_token": "hf_oauth_marker", "refresh_token": "hf_refresh_marker",
                                             "expires_in": 3600})
        if path == "/api/whoami-v2":
            return httpx.Response(200, json={"name": "ada"})
        return httpx.Response(404)

    real_sleep = report.time.sleep

    def sleep(seconds: float) -> None:  # a poll interval: the script waits briefly, any other poller doesn't
        if threading.current_thread() is main:
            other_took_it.wait(0.2)
        else:
            real_sleep(0.001)

    monkeypatch.setattr(auth, "HuggingFaceSignIn",
                        functools.partial(auth.HuggingFaceSignIn, transport=httpx.MockTransport(hugging_face)))
    monkeypatch.setattr(report.time, "sleep", sleep)
    copy = tmp_path / "copy"
    copy.mkdir()
    try:
        trainer = report._hugging_face_trainer(copy)  # noqa: SLF001 - the freeze's sign-in step
    finally:
        secret_store._set_backend_for_tests(None)  # noqa: SLF001

    assert trainer is not None and not other_took_it.is_set()


def test_an_arm_that_outlasts_its_first_readiness_check_is_served_on_the_second(data_dir, tmp_path, monkeypatch):
    """13 Nov: after an Ollama update the starting model's first load took 20.3 s against the readiness check's 20 s,
    so the v2 freeze served no starting model and evaluated nothing. A second load reads the cached file."""

    from row_bot.learning.training import base_model, serving, weights

    model = weights.MODELS["qwen3.5-9b"]
    folder = base_model.folder(model.id, tmp_path)
    folder.mkdir(parents=True)
    (folder / "base.gguf").write_bytes(b"gguf")
    base_model.write(base_model.BaseState(model.id, "ready", gguf="base.gguf"), tmp_path)
    personal = tmp_path / "learning_models" / "versions" / "v6" / "personal.gguf"
    personal.parent.mkdir(parents=True)
    personal.write_bytes(b"gguf")
    version = {"version": 6, "_runtime": "ollama", "_serve_context": 32768, "_base": model.repo, "_gguf": personal}
    asked: list[str] = []

    def serve(runtime, *, tag=None, **_arguments):
        arm = "start" if tag else "personal"
        asked.append(arm)
        if arm == "start" and asked.count("start") == 1:
            return serving.ServeOutcome("failed", runtime, reason="probe_failed")
        return serving.ServeOutcome("ready", runtime, model_ref=f"model:ollama:{arm}")

    monkeypatch.setattr(serving, "serve", serve)
    prepared = report.serve_arms(tmp_path, version)
    assert prepared.refs == {"start": "model:ollama:start", "personal": "model:ollama:personal"}
    assert asked == ["start", "start", "personal"]

    asked.clear()
    monkeypatch.setattr(serving, "serve", lambda runtime, **_: asked.append("ask") or serving.ServeOutcome(
        "failed", runtime, reason="not_running"))
    prepared = report.serve_arms(tmp_path, version)
    assert (prepared.refs, prepared.reason) == ({}, "runtime_unavailable")
    assert asked == ["ask", "ask"]  # the runtime isn't there: each arm is asked once


def test_a_freeze_that_serves_no_arm_says_why_in_its_report(profile, folders, capsys, chats):
    scratch, out = folders
    _trained_on_the_profiles_rows(profile, chats)
    fake = _Exploratory()
    hooks = _exploratory_hooks(fake, "upload", chats)
    hooks.prepare = lambda copy, version: report.Prepared({}, 32768, "ollama", reason="no_starting_model",
                                                          failures={"start": "probe_failed"})

    code = report.main(["--freeze", "--as-of", "2030-01-01", "--hugging-face-27b", "--profile", str(profile),
                        "--scratch", str(scratch), "--out", str(out), "--yes"], hooks=hooks)

    printed = capsys.readouterr()
    assert code == 0, printed.out
    written = _written(out)
    report.validate(written, report.FREEZE)
    assert written["hypotheses"]["H1"]["reason"] == "no_starting_model"
    assert written["design"]["serve_failures"] == {"start": "probe_failed"}
    found = written["exploratory_27b"]
    assert (found["state"], found["reason"]) == ("not_run", "no_starting_model")
    assert found["serve_failures"] == {"start27": "probe_failed"}


def _finished_run(fake: _Exploratory, repo: str = "ada/row-bot-personal-0123456789ab") -> tuple[str, str]:
    """A 27B run that already finished on Hugging Face (its job, logs and model repository), as the v2 freeze's
    third run left it on 14 Nov."""

    fake.hub.create_private_repo(repo, "model")
    remote = fake.hub.run_job(image="row-bot", command=[], env={}, secrets={}, flavor="a100-large",
                              timeout_seconds=36_000, labels={"row-bot-job": "job_gone"})
    fake.hub.finish(remote.id, output_repo=repo, base=True, running_seconds=5_400)
    fake.hub.calls.clear()
    return remote.id, repo


def test_a_finished_27b_run_is_downloaded_measured_and_deleted_without_uploading(profile, folders, capsys, chats):
    scratch, out = folders
    rows, validation = _trained_on_the_profiles_rows(profile, chats)
    before = _hashes(profile)
    fake = _Exploratory()
    remote_id, repo = _finished_run(fake)

    code = report.main(["--exploratory-27b", "--as-of", "2030-01-01", "--hugging-face-run", remote_id,
                        "--output-repo", repo, "--profile", str(profile), "--scratch", str(scratch), "--out", str(out),
                        "--yes"], hooks=_exploratory_hooks(fake, "download", chats))

    printed = capsys.readouterr()
    assert code == 0, printed.out
    written = _written(out)
    report.validate(written, report.EXPLORATORY_REPORT)
    assert fake.asked and "download" in fake.asked[0]  # asked even with --yes
    found = written["exploratory_27b"]
    assert (found["state"], found["reason"], found["model"]) == ("run", None, "qwen3.8-27b")
    assert found["rows"] == {"examples": rows, "validation": validation, "matched": True} and found["upload_bytes"] > 0
    assert (found["uploads_deleted"], found["minutes"], found["cost_usd"]) == (True, 90, 3.75)  # 90 min at $2.50/h
    assert set(found["arms"]) == {"start27", "personal27", "personal"}
    assert not {"upload", "run_job", "create_private_repo"} & set(fake.hub.calls)  # nothing uploaded or trained
    assert fake.hub.deleted == [repo] and not fake.hub.repos
    assert remote_id not in printed.out + json.dumps(written)
    assert _hashes(profile) == before


def test_a_run_that_isnt_finished_is_left_as_it_is(profile, folders, chats):
    scratch, out = folders
    _trained_on_the_profiles_rows(profile, chats)
    fake = _Exploratory()
    repo = "ada/row-bot-personal-0123456789ab"
    fake.hub.create_private_repo(repo, "model")
    remote = fake.hub.run_job(image="row-bot", command=[], env={}, secrets={}, flavor="a100-large",
                              timeout_seconds=36_000, labels={"row-bot-job": "job_gone"})

    code = report.main(["--exploratory-27b", "--as-of", "2030-01-01", "--hugging-face-run", remote.id,
                        "--output-repo", repo, "--profile", str(profile), "--scratch", str(scratch), "--out", str(out),
                        "--yes"], hooks=_exploratory_hooks(fake, "download", chats))

    assert code == 0
    found = _written(out)["exploratory_27b"]
    assert (found["state"], found["reason"]) == ("not_run", "remote_error")
    assert fake.asked == [] and repo in fake.hub.repos and not fake.hub.deleted


def test_a_sign_in_that_lapses_while_the_run_trains_ends_the_wait_and_keeps_its_files(profile, folders, chats):
    """14 Nov: the v2 freeze's third run lost its Hugging Face sign-in during its nine-hour evaluation and then
    waited for its 27B for good, printing nothing."""

    scratch, out = folders
    _trained_on_the_profiles_rows(profile, chats)
    fake = _Exploratory()
    hooks = _exploratory_hooks(fake, "upload", chats)
    hooks.sleep = lambda _seconds: fake.trainer.sign_in().sign_out()  # the run never finishes; the sign-in goes

    code = report.main(["--freeze", "--as-of", "2030-01-01", "--hugging-face-27b", "--profile", str(profile),
                        "--scratch", str(scratch), "--out", str(out), "--yes"], hooks=hooks)

    assert code == 0
    found = _written(out)["exploratory_27b"]
    assert (found["state"], found["reason"]) == ("not_run", "signed_out")
    assert fake.hub.repos and not fake.hub.deleted  # nothing deletes the run's files without the sign-in


def test_the_exploratory_mode_needs_the_run_and_its_repository(profile, folders, capsys):
    scratch, out = folders
    for extra in ([], ["--hugging-face-run", "abc123def456"], ["--output-repo", "ada/row-bot-personal-0123"],
                  ["--hugging-face-run", "abc123def456", "--output-repo", "ada/someone-elses-model"]):
        with pytest.raises(SystemExit):
            report.main(["--exploratory-27b", "--as-of", "2030-01-01", *extra, "--profile", str(profile),
                         "--scratch", str(scratch), "--out", str(out)])
    assert "--exploratory-27b needs" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        report.main(["--freeze", "--as-of", "2030-01-01", "--output-repo", "ada/row-bot-personal-0123",
                     "--profile", str(profile), "--scratch", str(scratch), "--out", str(out)])
