"""An exploratory follow-up to the controlled scaling study, **not pre-registered**: the study's ``start``,
``sft-1000`` and ``dagger-1000`` on its held-out requests again, served at 65,536 tokens instead of 32,768.

Neither question is in ``PREREG-SCALING.md`` or its amendments, so everything here is labelled exploratory and its
comparisons are unadjusted and descriptive:

1. **Does DAgger's edge come back when its runs fit?** At 32K, ``dagger-1000`` ran out of context in 53 of its 771
   runs (``sft-1000`` in 3), and SA2 counts an overflow as out of budget.
2. **Which tool calls fail after training?** Every run record also keeps the calls the turn never recovered (the ones
   ``bad_call`` is decided from, ``eval_worker.TurnRecord.unrecovered``) as (tool name, problem class) pairs: no
   arguments, outputs or task text (``runner.unrecovered_calls``).

**Everything else as the study ran it**, read from its pinned ``design.json`` and never written: the same held-out
requests (checked against its ``runs.jsonl``), 3 runs each, the budget per run (150K tokens, the task's step limit,
900 s), the trained versions' own GGUFs in Row-Bot's own llama.cpp server (one slot, reasoning off, one model loaded
at a time), the study's runner (``runner.StudyRunner``), a model-call error rerun once, and SA2's rule applied as each
run is recorded (:data:`OVERFLOW_MARKERS`).

**The prompt envelope.** At 32K Row-Bot serves a model its small-context profile (core tools bound, the rest through
``tool_search``/``tool_invoke``, the tool guides as an index, no state block, a small recall): the shape the versions
were trained and evaluated in. At 64K the product's own rule would switch to a larger profile (most tools bound
directly, the guides in full, the state block). By default (``--envelope study``) the worker keeps the small-context
profile up to 65,536 tokens (``agent.SMALL_PROFILE_TOKENS``, in its own process; no product change), so only the room
grows: the history budget, the tool results kept inline and the context a request may fill. ``--envelope product``
uses the product's own rule instead (a separate ``--out``: the design is pinned).

    python -m tests.e2e.learning.scaling.explore --study-out <study>/out --study-data <study>/scratch/study/data \\
        --out <folder> --scratch <folder> --gpu-booking <file> [--lane lane-explore] [--smoke] evaluate report
    python -m tests.e2e.learning.scaling.explore --dry-run --out <folder> --scratch <folder>

- ``evaluate``: a child process (``explore_worker.py``) in its own scratch profile ``<scratch>/data``; it continues from
  its own ``runs.jsonl`` when started again.
- ``report``: ``<out>/report/report.json`` and ``report.md``, the 64K results beside the study's 32K ones under SA2
  (computed from the study's ``runs.jsonl`` by the same code, and checked against ``analyse-sa2`` when it is there).
- ``--smoke``: the first held-out request, one run per arm (give it its own ``--out``).
- ``--dry-run``: a made-up study (one person, two held-out days) and the scripted model on loopback: no GPU.

It writes only under ``--out`` and ``--scratch`` (refusing the everyday profile, its backups, a checkout and the
study's own folders) and books the card as its own lane: no new GPU work from 00:25 to 08:00 (the person's quiet hours,
then their own Row-Bot's morning), and it yields to the person's own training and to any other lane's booking.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from tests.e2e.learning.scaling.study import Runner, Stopped, _run_child, code_hash, commit, read_json, write_json
from tests.e2e.learning.seeded.scratch import PROTECT_ENV, ScratchError, check_scratch

STAGES = ("evaluate", "report")
ARMS = ("start", "sft-1000", "dagger-1000")
#: The paired differences the questions ask for: (arm, against).
COMPARISONS = (("dagger-1000", "sft-1000"), ("sft-1000", "start"))
SERVE_CONTEXT = 65_536
STUDY_CONTEXT = 32_768
ENVELOPES = ("study", "product")
#: SA2: a model call refused because the conversation exceeded the serving context counts as out of budget, not as an
#: outage: Row-Bot's llama.cpp server's HTTP 400, Row-Bot's own estimate before it sends, or a local model that filled
#: its window. The markers SA2's recount used, so both contexts are read alike.
OVERFLOW_MARKERS = ("rejected the chat request (HTTP 400)", "filled its whole", "requires an estimated")
LABEL = "Exploratory, not pre-registered: unadjusted and descriptive"
FORMAT = "row_bot.learning.scaling_explore_report"
LANE = "lane-explore"


@dataclass(frozen=True)
class ExploreDesign:
    """What the exploratory stage runs (pinned with the build; the study's own numbers come from its design)."""

    arms: tuple[str, ...] = ARMS
    serve_context: int = SERVE_CONTEXT
    #: ``study``: the small-context profile the study served at 32K, kept up to ``serve_context``; ``product``: the
    #: product's own rule at ``serve_context``.
    envelope: str = "study"
    #: Held-out requests run, in the study's order (0: all of them), and runs of each (0: the study's own).
    tasks: int = 0
    runs: int = 0
    dry_run: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ExploreDesign:
        known = {item.name for item in fields(cls)}
        return cls(**{key: tuple(value) if isinstance(value, list) else value for key, value in data.items()
                      if key in known})


def is_overflow(item: Mapping[str, Any]) -> bool:
    """A run kept as a model-call error whose call the serving context refused (SA2)."""

    return item.get("status") == "error" and any(mark in str(item.get("detail") or "") for mark in OVERFLOW_MARKERS)


def recode(item: Mapping[str, Any]) -> dict[str, Any]:
    """A run record under SA2: an overflow is out of budget (``budget``) and stays in the denominator. A record the
    exploratory stage wrote is already recoded; this marks it the same way for the study's own records."""

    if is_overflow(item):
        return {**item, "status": "budget", "cause": "budget", "overflow": True}
    return {**item, "overflow": bool(item.get("overflow"))}


def read_lines(path: Path) -> list[dict[str, Any]]:
    try:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return []


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


#: The dry run's made-up study: its arms' names on Row-Bot's server, as the real study named them.
DRY_NAMES = {"start": "row-bot-personal-base", "sft-1000": "row-bot-personal-v3", "dagger-1000": "row-bot-personal-v7"}
DRY_PROVIDER = "custom_openai_row-bot-personal-row-bot"
DRY_OVERFLOW = ("the model call failed: ⚠️ API error: Personal model (Row-Bot) rejected the chat request (HTTP 400). "
                "The endpoint profile or selected model may not accept this message/tool payload.")


def dry_study(folder: Path) -> tuple[Path, Path]:
    """A made-up finished study for the dry run (its ``out`` and its profile): the dry design (one person, two held-out
    days, one run each), the three arms' models with stand-in GGUFs on its server list, and 32K runs with an overflow,
    so every part of the stage and its report runs without a GPU. Made once; its numbers mean nothing."""

    from tests.e2e.learning.scaling import design as design_module
    from tests.e2e.learning.scaling.dry_run import DESIGN

    out, data = folder / "out", folder / "data"
    if (out / "design.json").is_file():
        return out.resolve(), data.resolve()
    models = []
    for arm, name in DRY_NAMES.items():
        gguf = data / "learning_models" / arm / f"{name}-Q8_0.gguf"
        gguf.parent.mkdir(parents=True, exist_ok=True)
        gguf.write_bytes(b"GGUF")
        models.append({"name": name, "gguf": str(gguf), "context": STUDY_CONTEXT})
    write_json(data / "runtimes" / "learning" / "server" / "run" / "models.json", {"models": models})
    write_json(out / "build.json", {"commit": "dry-study", "code_hash": "dry-study"})
    write_json(out / "train" / "state.json", {
        "start": {"ref": f"model:{DRY_PROVIDER}:{DRY_NAMES['start']}"},
        "arms": {arm: {"state": "ready", "model_ref": f"model:{DRY_PROVIDER}:{name}"} for arm, name in DRY_NAMES.items()
                 if arm != "start"}})
    runs = []
    for number, request in enumerate(design_module.blocks(DESIGN, "mara")[1]):
        for arm in DRY_NAMES:
            passed = (number + len(arm)) % 2 == 1
            item = {"arm": arm, "task": request.id, "repeat": 0, "cluster": f"{request.person}/d{request.day}",
                    "status": "passed" if passed else "failed", "cause": "" if passed else "wrong_answer",
                    "detail": "", "tokens": 30_000, "local_tokens": 30_000, "steps": 3, "seconds": 4.0,
                    "reran_after_outage": False}
            if arm == "dagger-1000" and number == 0:
                item.update(status="error", cause="provider_error", detail=DRY_OVERFLOW, reran_after_outage=True)
            runs.append(item)
    (out / "evaluate").mkdir(parents=True, exist_ok=True)
    (out / "evaluate" / "runs.jsonl").write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in runs),
                                                 encoding="utf-8", newline="\n")
    write_json(out / "design.json", DESIGN.to_dict())  # last: it marks the study as made
    return out.resolve(), data.resolve()


def child_env(data: Path) -> dict[str, str]:
    """The child's environment: nothing of Row-Bot's own settings but its scratch profile (as the study's children)."""

    protect = os.environ.get(PROTECT_ENV, "")
    env = {key: value for key, value in os.environ.items() if not key.startswith("ROW_BOT_")}
    if protect:
        env[PROTECT_ENV] = protect
    env.update({"ROW_BOT_DATA_DIR": str(data), "ROW_BOT_LEARNING_PREVIEW": "1",
                "ROW_BOT_DESKTOP_NOTIFICATIONS": "off", "ROW_BOT_AUTO_START_OLLAMA": "0",
                "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"})
    return env


class Explore:
    """The exploratory stage (see the module's docstring)."""

    def __init__(self, args: argparse.Namespace, *, runner: Runner = _run_child,
                 say: Callable[[str], None] = lambda text: print(text, flush=True),
                 build: Callable[[], dict[str, str]] | None = None) -> None:
        self.args = args
        self.out = check_scratch(Path(args.out))
        self.scratch = check_scratch(Path(args.scratch))
        # Anything of Row-Bot this process imports (the generator's tasks, the analysis) binds a scratch folder.
        os.environ["ROW_BOT_DATA_DIR"] = str(check_scratch(self.scratch / "orchestrator"))
        if args.dry_run:
            self.study_out, self.study_data = dry_study(self.scratch / "dry-study")
        else:
            if not args.study_out or not args.study_data:
                raise ScratchError("--study-out and --study-data name the finished study's out folder and its "
                                   "evaluation profile (read only)")
            self.study_out, self.study_data = Path(args.study_out).resolve(), Path(args.study_data).resolve()
        root = Path(os.path.commonpath([str(self.study_out), str(self.study_data)]))
        for folder in self.folders():
            check_scratch(folder)
            if _inside(folder, root):
                raise ScratchError(f"{folder} is inside the study's own folders ({root}): they are read, never "
                                   "written")
        self.study_design = read_json(self.study_out / "design.json")
        if not self.study_design:
            raise ScratchError(f"{self.study_out} holds no design.json: name the study's out folder")
        self.design = ExploreDesign(envelope=args.envelope, tasks=1 if args.smoke else 0, runs=1 if args.smoke else 0,
                                    dry_run=bool(args.dry_run))
        self.runner = runner
        self.say = say
        self.build = build or (lambda: {"commit": commit(), "code_hash": code_hash()})

    def folders(self) -> list[Path]:
        """Every folder the stage writes."""

        return [self.out, self.data, self.scratch / "orchestrator"]

    @property
    def data(self) -> Path:
        return self.scratch / "data"

    # ── The build pin ───────────────────────────────────────────────────

    def pin(self) -> dict[str, Any]:
        """Record the build, the design and the study it reads before the first run; refuse another one afterwards."""

        study_build = read_json(self.study_out / "build.json")
        current = json.loads(json.dumps({
            **self.build(), "design": self.design.to_dict(),
            "study": {"out": str(self.study_out), "data": str(self.study_data), "commit": study_build.get("commit"),
                      "code_hash": study_build.get("code_hash"), "design": self.study_design}}))
        path = self.out / "build.json"
        saved = read_json(path)
        if saved:
            changed = [key for key in ("commit", "code_hash", "design", "study") if saved.get(key) != current[key]]
            if changed:
                raise ScratchError(f"this exploratory run is pinned to build {str(saved.get('commit', ''))[:12]}; this "
                                   f"one differs in {', '.join(changed)}: start another one with its own --out")
            return saved
        write_json(path, {**current, "label": LABEL, "pinned_at": time.strftime("%Y-%m-%d %H:%M:%S")})
        write_json(self.out / "design.json", {"explore": self.design.to_dict(), "study_design": self.study_design,
                                              "study_out": str(self.study_out), "study_data": str(self.study_data)})
        return current

    # ── Stages ──────────────────────────────────────────────────────────

    def check_tasks(self) -> None:
        """The held-out requests the generator gives are the ones the study ran for every arm (a full run only)."""

        from tests.e2e.learning.scaling import design as design_module

        if self.design.tasks:
            return
        study = design_module.Design.from_dict(self.study_design)
        held = {request.id for person in study.people for request in design_module.blocks(study, person)[1]}
        ran: dict[str, set[str]] = {}
        for item in read_lines(self.study_out / "evaluate" / "runs.jsonl"):
            ran.setdefault(item["arm"], set()).add(item["task"])
        for arm in self.design.arms:
            if ran.get(arm) != held:
                raise Stopped(f"evaluate: the study's {arm} ran {len(ran.get(arm) or ())} held-out requests, not the "
                              f"{len(held)} the generator gives: not the same tasks")

    def stage_evaluate(self) -> None:
        self.check_tasks()
        self.data.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, "-m", "tests.e2e.learning.scaling.explore_worker", "--out", str(self.out),
                   "--data", str(self.data), "--lane", self.args.lane]
        if self.args.gpu_booking:
            command += ["--gpu-booking", str(self.args.gpu_booking)]
        self.say(f"── evaluate ({', '.join(self.design.arms)} at {self.design.serve_context:,} tokens, envelope "
                 f"{self.design.envelope})")
        code = self.runner(command, child_env(self.data), self.out / "logs" / "evaluate.log")
        if code != 0:
            raise Stopped(f"evaluate stopped (exit {code}): see {self.out / 'logs' / 'evaluate.log'}; run it again to "
                          "continue")

    def stage_report(self) -> dict[str, Any]:
        state = read_json(self.out / "evaluate" / "state.json")
        if not state.get("done"):
            raise Stopped("report: the evaluation hasn't finished")
        published = read_json(self.study_out / "analyse-sa2" / "report.json")
        report = build_report(read_lines(self.out / "evaluate" / "runs.jsonl"),
                              read_lines(self.study_out / "evaluate" / "runs.jsonl"), design=self.design,
                              study_design=self.study_design, state=state, published=published or None)
        report["build"] = read_json(self.out / "build.json")
        write_json(self.out / "report" / "report.json", report)
        (self.out / "report" / "report.md").write_text(markdown(report), encoding="utf-8", newline="\n")
        self.say(f"Report: {self.out / 'report' / 'report.md'}")
        return report

    def run(self, stages: Sequence[str]) -> int:
        unknown = [stage for stage in stages if stage not in STAGES]
        if unknown or not stages:
            raise ScratchError(f"name the stages to run, from: {', '.join(STAGES)}")
        build = self.pin()
        self.say(f"{LABEL}. Build {str(build.get('commit', ''))[:12]}, code {str(build.get('code_hash', ''))[:12]}")
        try:
            for stage in STAGES:
                if stage in stages:
                    self.say(f"stage {stage}")
                    getattr(self, f"stage_{stage}")()
        except Stopped as stopped:
            self.say(f"stopped: {stopped}")
            return 3
        return 0


# ── The report ───────────────────────────────────────────────────────────────


def _mean(values: Sequence[float]) -> float | None:
    return round(sum(values) / len(values), 6) if values else None


def _side(runs: Sequence[Mapping[str, Any]], arms: Sequence[str], cluster_of: Mapping[str, str],
          study: Any) -> dict[str, Any]:
    """One serving context's results: per arm its success, overflows, causes, tokens and run hours; the comparisons."""

    from tests.e2e.learning.scaling import analysis

    scores: dict[str, dict[str, float]] = {}
    found: dict[str, Any] = {}
    for arm in arms:
        mine = [item for item in runs if item["arm"] == arm]
        scores[arm] = analysis.scores(mine)
        attempts = [item for item in mine if "overflowed_attempts" in item]
        found[arm] = {
            "tasks": len(scores[arm]), "rate": _mean(list(scores[arm].values())),
            **analysis._runs_summary(mine),  # noqa: SLF001 - the study's own run summary
            "overflows": sum(1 for item in mine if item.get("overflow")),
            "overflowed_attempts": sum(int(item["overflowed_attempts"]) for item in attempts) if attempts else None,
            "reran_after_outage": sum(1 for item in mine if item.get("reran_after_outage")),
            "causes": dict(sorted(Counter(str(item.get("cause") or "") for item in mine
                                          if item["status"] in ("failed", "budget")).items())),
            "mean_tokens": round(_mean([float(item.get("tokens") or 0) for item in mine]) or 0),
            "mean_steps": round(_mean([float(item.get("steps") or 0) for item in mine]) or 0.0, 1),
            "run_hours": round(sum(float(item.get("seconds") or 0) for item in mine) / 3600, 3)}
    paired = {f"{arm} − {against}": {"arm": arm, "against": against,
                                     **analysis.paired(scores[arm], scores[against], cluster_of, study)}
              for arm, against in COMPARISONS if arm in arms and against in arms}
    return {"arms": found, "paired": paired}


def unrecovered_table(runs: Sequence[Mapping[str, Any]], arms: Sequence[str]) -> dict[str, Any]:
    """The unrecovered calls by (tool, problem class) per arm: in the runs that failed as ``bad_call``, and in every
    run (a run that passed may still have left a call it never got right)."""

    rows: dict[tuple[str, str], dict[str, dict[str, int]]] = {}
    bad = Counter()
    for item in runs:
        if item["arm"] not in arms:
            continue
        failed_as_bad_call = item.get("cause") == "bad_call"
        bad[item["arm"]] += int(failed_as_bad_call)
        for tool, problem in item.get("unrecovered") or ():
            entry = rows.setdefault((str(tool), str(problem)), {"bad_call_runs": Counter(), "every_run": Counter()})
            entry["every_run"][item["arm"]] += 1
            if failed_as_bad_call:
                entry["bad_call_runs"][item["arm"]] += 1
    ordered = sorted(rows.items(), key=lambda pair: (-sum(pair[1]["every_run"].values()), pair[0]))
    return {"bad_call_runs": {arm: bad[arm] for arm in arms},
            "bad_call_runs_without_a_pair": {arm: sum(1 for item in runs if item["arm"] == arm
                                                      and item.get("cause") == "bad_call"
                                                      and not item.get("unrecovered")) for arm in arms},
            "rows": [{"tool": tool, "problem": problem,
                      "in_bad_call_runs": {arm: entry["bad_call_runs"][arm] for arm in arms},
                      "in_every_run": {arm: entry["every_run"][arm] for arm in arms}}
                     for (tool, problem), entry in ordered]}


def build_report(runs: Sequence[Mapping[str, Any]], study_runs: Sequence[Mapping[str, Any]], *, design: ExploreDesign,
                 study_design: Mapping[str, Any], state: Mapping[str, Any],
                 published: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The exploratory report: the 64K runs beside the study's 32K runs on the same tasks, both under SA2."""

    from tests.e2e.learning.scaling.design import Design

    study = Design.from_dict(dict(study_design))
    arms = list(design.arms)
    explored = [recode(item) for item in runs if item["arm"] in arms]
    tasks = {item["task"] for item in explored}
    before = [recode(item) for item in study_runs if item["arm"] in arms and item["task"] in tasks]
    cluster_of = {item["task"]: item["cluster"] for item in (*before, *explored)}
    gpu = dict(state.get("gpu_seconds") or {})
    found = {
        "format": FORMAT, "version": 1, "label": LABEL,
        "questions": ["Does dagger-1000's edge over sft-1000 return when its runs fit the serving context?",
                      "Which tool calls fail, unrecovered, after training?"],
        "design": design.to_dict(), "contexts": {"explored": design.serve_context, "study": STUDY_CONTEXT},
        "budget": {"tokens": study.task_tokens, "steps": "the task's own step limit", "seconds": study.seconds,
                   "runs": design.runs or study.runs},
        "rule": "SA2: a context overflow counts as out of budget (in the denominator, failed)",
        "tasks": len(tasks),
        "explored": _side(explored, arms, cluster_of, study),
        "study": _side(before, arms, cluster_of, study),
        "unrecovered": unrecovered_table(explored, arms),
        "gpu_hours": {"by_arm": {arm: round(float(gpu.get(arm) or 0.0) / 3600, 3) for arm in arms},
                      "serving_checks": round(float(gpu.get("serving") or 0.0) / 3600, 3)},
        "serving": dict(state.get("serving") or {}),
        "local_tokens": dict(state.get("tokens") or {}),
    }
    if published:
        rates = (published.get("analysis") or {}).get("rates") or {}
        found["study_as_published"] = {
            "report": "analyse-sa2", "rates": {arm: (rates.get(arm) or {}).get("rate") for arm in arms},
            "recomputed_match": all(abs(float((rates.get(arm) or {}).get("rate") or -1)
                                        - float(found["study"]["arms"][arm]["rate"] or -2)) < 5e-6 for arm in arms)
            if len(tasks) == len({item["task"] for item in study_runs if item["arm"] == arms[0]}) else None}
    return found


def _cells(values: Iterable[Any]) -> list[Any]:
    from tests.e2e.learning.scaling.analysis import _number

    return [_number(value) for value in values]


def markdown(report: Mapping[str, Any]) -> str:
    """The report for people: the 64K numbers with their 32K ones beside them; every comparison labelled."""

    from tests.e2e.learning.scaling.analysis import _interval, _number, _rate, _table

    design, contexts = report["design"], report["contexts"]
    explored, study = report["explored"], report["study"]
    arms = list(design["arms"])
    build = report.get("build") or {}
    k = f"{contexts['explored'] // 1024}K"
    runs = report["budget"]["runs"]
    lines = [f"# The scaling study's arms at a {k} serving context (exploratory)", "",
             f"**{report['label']}.** Neither question is in `PREREG-SCALING.md` or its amendments: "
             + " ".join(f"({number}) {question}" for number, question in enumerate(report["questions"], 1)), "",
             f"Build `{str(build.get('commit', ''))[:12]}`, code `{str(build.get('code_hash', ''))[:12]}`; the study's "
             f"build `{str((build.get('study') or {}).get('commit', ''))[:12]}`. {report['tasks']} held-out tasks × "
             f"{runs} run{'' if runs == 1 else 's'} per arm, budget {_number(report['budget']['tokens'])} tokens, the task's "
             f"step limit and {_number(report['budget']['seconds'], 0)} s. Served at {contexts['explored']:,} tokens "
             "in Row-Bot's own llama.cpp server (one slot, reasoning off); the prompt envelope: "
             + ("the small-context profile the study served at 32K, kept (only the room grows)"
                if design["envelope"] == "study" else "the product's own rule at this context")
             + f". {report['rule']}; a model-call error is rerun once, as in the study. The {contexts['study'] // 1024}K "
             "columns are the study's own runs on the same tasks, recounted under SA2 by the same code.", ""]
    published = report.get("study_as_published")
    if published and published.get("recomputed_match") is not None:
        lines += [f"The 32K rates match `analyse-sa2/report.json`: {published['recomputed_match']}.", ""]
    lines += ["## Success on the held-out tasks", ""]
    lines += _table(["Arm", f"{k} mean s(i, a)", f"{k} runs passed", "32K mean s(i, a)", "32K runs passed"],
                    [(arm, _number(explored["arms"][arm]["rate"]), _rate(explored["arms"][arm]["runs_passed"]),
                      _number(study["arms"][arm]["rate"]), _rate(study["arms"][arm]["runs_passed"])) for arm in arms])
    lines += ["## Paired differences (exploratory, unadjusted: the study's cluster bootstrap and permutation test)", ""]
    lines += _table(["Comparison", "Context", "Tasks", "Difference", "95% interval", "p"],
                    [(name, context, item.get("problems"), _number(item.get("difference")), _interval(item),
                      _number(item.get("p_value")))
                     for name in explored["paired"]
                     for context, item in ((k, explored["paired"][name]), ("32K", study["paired"][name]))])
    lines += ["## Context overflows (out of budget under SA2)", ""]
    lines += _table(["Arm", f"{k}: runs ending in an overflow", f"{k}: attempts that overflowed",
                     f"{k}: reruns after an error", "32K: runs ending in an overflow", "32K: reruns after an error"],
                    [(arm, *_cells((explored["arms"][arm]["overflows"], explored["arms"][arm]["overflowed_attempts"],
                                    explored["arms"][arm]["reran_after_outage"], study["arms"][arm]["overflows"],
                                    study["arms"][arm]["reran_after_outage"]))) for arm in arms])
    lines += ["## Failure causes (counted runs that didn't pass)", ""]
    lines += _table(["Arm", k, "32K"], [(arm, *(", ".join(f"{key or 'unknown'} {value}" for key, value in
                                                         side["arms"][arm]["causes"].items()) or "—"
                                               for side in (explored, study))) for arm in arms])
    table = report["unrecovered"]
    lines += [f"## Unrecovered tool calls ({k} only: the study's records don't keep them)", "",
              "Each cell: calls in the runs that failed as `bad_call` / calls in every run. Runs that failed as "
              "`bad_call`: " + ", ".join(f"{arm} {table['bad_call_runs'][arm]}" for arm in arms)
              + "; of them with no pair kept: " + ", ".join(f"{arm} {table['bad_call_runs_without_a_pair'][arm]}"
                                                         for arm in arms) + ".", ""]
    lines += _table(["Tool", "Problem", *arms],
                    [(row["tool"], row["problem"], *(f"{row['in_bad_call_runs'][arm]} / {row['in_every_run'][arm]}"
                                                     for arm in arms)) for row in table["rows"]]
                    or [("—", "—", *("—" for _ in arms))])
    gpu = report["gpu_hours"]
    lines += ["## Tokens, steps and time", ""]
    lines += _table(["Arm", f"{k} mean tokens a run", "32K mean tokens a run", f"{k} mean tool calls",
                     "32K mean tool calls", f"{k} GPU-hours", f"{k} run hours", "32K run hours"],
                    [(arm, _number(explored["arms"][arm]["mean_tokens"]), _number(study["arms"][arm]["mean_tokens"]),
                      _number(explored["arms"][arm]["mean_steps"], 1), _number(study["arms"][arm]["mean_steps"], 1),
                      *_cells((gpu["by_arm"][arm], explored["arms"][arm]["run_hours"], study["arms"][arm]["run_hours"])))
                     for arm in arms])
    lines += [f"GPU-hours: the card held for each arm's runs (every attempt), plus {_number(gpu['serving_checks'])} h "
              "for serving and probing the three models; run hours: the kept runs' own time (the study's report counts "
              "its replay, hidden tasks and training too, so its GPU-hours are not these).", ""]
    serving = report.get("serving") or {}
    if serving:
        lines += [f"## Serving at {contexts['explored']:,} tokens", ""]
        lines += _table(["Arm", "Model", "Context", "Card memory with it loaded (MiB)", "Before loading (MiB)",
                         "Agent ready"],
                        [(arm, item.get("name"), _number(item.get("context")), _number(item.get("vram_mib")),
                          _number(item.get("baseline_mib")), (item.get("probe") or {}).get("agent_ready"))
                         for arm, item in ((arm, serving[arm]) for arm in arms if arm in serving)])
    return "\n".join(lines).rstrip() + "\n"


# ── Running ──────────────────────────────────────────────────────────────────


def parse(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="The scaling study's exploratory 64K follow-up (not pre-registered)")
    parser.add_argument("stages", nargs="*", help=f"the stages to run: {', '.join(STAGES)}")
    parser.add_argument("--study-out", default="", help="the finished study's out folder (read only)")
    parser.add_argument("--study-data", default="", help="the study's evaluation profile, whose server lists the "
                                                         "versions' GGUFs (read only)")
    parser.add_argument("--out", required=True, help="where this stage's state, runs and report go")
    parser.add_argument("--scratch", required=True, help="where its scratch profile goes")
    parser.add_argument("--gpu-booking", default="", help="the shared GPU booking file")
    parser.add_argument("--lane", default=LANE)
    parser.add_argument("--envelope", choices=ENVELOPES, default="study",
                        help="study: keep the small-context profile the study served (default); product: the "
                             "product's own rule at 64K")
    parser.add_argument("--smoke", action="store_true", help="the first held-out request, one run per arm")
    parser.add_argument("--dry-run", action="store_true", help="a made-up study and the scripted model (no GPU)")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # a log file on Windows is cp1252 otherwise
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    args = parse(argv)
    try:
        explore = Explore(args)
        return explore.run(args.stages or (list(STAGES) if args.dry_run else []))
    except ScratchError as error:
        print(f"scaling explore: {error}", file=sys.stderr)
        return 2


__all__ = ["ARMS", "COMPARISONS", "Explore", "ExploreDesign", "LABEL", "OVERFLOW_MARKERS", "STAGES", "build_report",
           "is_overflow", "main", "markdown", "parse", "recode", "unrecovered_table"]


if __name__ == "__main__":
    sys.exit(main())
