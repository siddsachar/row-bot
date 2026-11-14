"""Plan, run, judge and report a memory benchmark: the code behind ``scripts/bench.py``.

``plan`` and ``report`` import nothing from Row-Bot and make no network call. ``run`` and
``judge`` need ``ROW_BOT_BENCH=1``; they start one worker per task (``worker.py``), one at
a time, each in its own scratch data folder inside the run folder (``.tmp/bench/<run>/``,
which git ignores), and:

- refuse a paid provider while the paid cap is $0 (``ledger``); plan and local calls are
  counted, not capped;
- use the ChatGPT plan only through the eval profile, and only the stock local model
  (``runtime.LOCAL_MODEL``); book the eval profile and the graphics card in
  ``ROW_BOT_BENCH_BOOKING_FILE`` and wait while another lane holds them (``booking``);
- check ``<run>/STOP`` between tasks (and the workers between sessions and questions);
- resume: a task whose trajectory is done is not run again.

Two modes: ``memory`` (Row-Bot's memory path; ``--dream`` adds one Dream Cycle pass) and
``full-context`` (the same model reads the whole history, no memory; ``memory.full_context``).
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import booking as booking_module
from .ledger import API, LOCAL, PLAN, LedgerCaps, billing_for, usage_book_path
from .memory import judge as scoring
from .memory.datasets import (
    BEAM_1M,
    LOCOMO,
    LONGMEMEVAL_S,
    LONGMEMEVAL_V2,
    MemoryTask,
    Question,
    audit_review,
    beam_tasks,
    locomo_tasks,
    longmemeval_tasks,
    longmemeval_v2_tasks,
)
from .memory.protocols import JudgeProtocol, load as load_protocol, protocol_from_json, protocol_to_json
from .runtime import LOCAL_MODEL, BenchConfig, child_env
from .trajectory import read_jsonl

REPO_ROOT = Path(__file__).resolve().parents[3]
ENABLE_ENV = "ROW_BOT_BENCH"
BOOKING_ENV = "ROW_BOT_BENCH_BOOKING_FILE"
OUT_ENV = "ROW_BOT_BENCH_OUT"
BENCHMARKS = (LOCOMO, LONGMEMEVAL_S, BEAM_1M, LONGMEMEVAL_V2)
MEMORY, FULL_CONTEXT = "memory", "full-context"
PUBLISHED = Path(__file__).resolve().parent / "memory" / "published.json"
TASK_TIMEOUT_MIN = 24 * 60
# Rough planning figures (tokens); the smoke run measures the real ones.
EXTRACTION_PROMPT_TOKENS = 2_500
EXTRACTION_WINDOW_CHARS = 6_000
EXTRACTION_OUTPUT_TOKENS = 600
# Extraction reads the person's messages (at most 2,000 characters each) and 200 characters of each reply.
PERSON_CHARS, REPLY_CHARS = 2_000, 200
# A question turn's first call with the memory tools is about 7,600 tokens (measured offline,
# Nov 2026: 16.9K characters of messages and 9.8K of tool schemas); a turn makes two or three.
TURN_TOKENS = 20_000
CHARS_PER_TOKEN = 3.5


def enabled(env: Mapping[str, str] | None = None) -> bool:
    return str((os.environ if env is None else env).get(ENABLE_ENV, "")).strip().lower() in {"1", "true", "yes"}


def split_ref(ref: str) -> tuple[str, str]:
    """``codex:auto`` / ``ollama:qwen3.8:27b`` -> (provider, model)."""
    provider, _, model = str(ref or "").strip().partition(":")
    if not provider or not model:
        raise ValueError(f"model {ref!r} must be provider:model (e.g. codex:auto, ollama:{LOCAL_MODEL})")
    return provider, model


# ── Tasks ──────────────────────────────────────────────────────────────────


def _numbers(text: str) -> list[int] | None:
    return [int(item) for item in text.split(",")] if text else None


def build_tasks(args: argparse.Namespace) -> list[MemoryTask]:
    mode = getattr(args, "mode", MEMORY)
    if args.benchmark == LOCOMO:
        # The full-context reader reads the whole conversation, both speakers by name: no perspectives.
        tasks = locomo_tasks(Path(args.data), Path(args.key) if args.key else None,
                             conversations=_numbers(args.conversations),
                             categories=tuple(int(item) for item in args.categories.split(",")),
                             perspectives="a" if mode == FULL_CONTEXT else args.perspectives,
                             questions_per_conversation=args.questions)
    elif args.benchmark == LONGMEMEVAL_S:
        tasks = longmemeval_tasks(Path(args.data), question_ids=args.question_ids.split(",") if args.question_ids
                                  else None, limit=args.questions)
    elif args.benchmark == BEAM_1M:
        tasks = beam_tasks(Path(args.data), conversations=_numbers(args.conversations),
                           questions_per_category=args.questions)
    elif args.benchmark == LONGMEMEVAL_V2:
        tasks = longmemeval_v2_tasks(Path(args.data), tier=args.tier,
                                     domains=args.domains.split(",") if args.domains else None)
        if args.questions is not None:
            for task in tasks:
                task.questions = task.questions[:args.questions]
    else:
        raise ValueError(f"unknown benchmark {args.benchmark!r}")
    return [task for task in tasks if task.questions]


def estimate(task: MemoryTask, mode: str = MEMORY, context_tokens: int = 400_000) -> dict[str, int]:
    """Rough reader tokens for one task: extraction windows over what extraction reads, one turn per
    question; for the full-context baseline, the history (cut to the window) once per question."""
    chars_all = sum(len(turn.text) for session in task.sessions for turn in session.turns)
    history = int(chars_all / CHARS_PER_TOKEN)
    if mode == FULL_CONTEXT:
        per_question = min(history, context_tokens, 200_000 if task.benchmark == LONGMEMEVAL_V2 else context_tokens)
        return {"sessions": len(task.sessions), "windows": 0, "chars": chars_all, "questions": len(task.questions),
                "ingest_tokens": 0, "question_tokens": len(task.questions) * per_question}
    windows = 0
    read = 0
    for session in task.sessions:
        chars = sum(min(len(turn.text), PERSON_CHARS if turn.role == "user" else REPLY_CHARS) + 12
                    for turn in session.turns)
        read += chars
        windows += max(1, -(-chars // EXTRACTION_WINDOW_CHARS))
    ingest = windows * (EXTRACTION_PROMPT_TOKENS + EXTRACTION_OUTPUT_TOKENS) + int(read / CHARS_PER_TOKEN)
    return {"sessions": len(task.sessions), "windows": windows, "chars": chars_all, "questions": len(task.questions),
            "ingest_tokens": ingest, "question_tokens": len(task.questions) * TURN_TOKENS}


def render_plan(tasks: Sequence[MemoryTask], args: argparse.Namespace) -> str:
    mode = getattr(args, "mode", MEMORY)
    rows = [estimate(task, mode) for task in tasks]
    lines = [f"{args.benchmark} ({mode}): {len(tasks)} task(s), {sum(row['questions'] for row in rows)} question(s)",
             "task                     sessions  windows       chars  questions  ~ingest tok  ~question tok"]
    for task, row in zip(tasks, rows):
        lines.append(f"{task.task_id:<24} {row['sessions']:>8} {row['windows']:>8} {row['chars']:>11} "
                     f"{row['questions']:>10} {row['ingest_tokens']:>12,} {row['question_tokens']:>14,}")
    total = sum(row["ingest_tokens"] + row["question_tokens"] for row in rows)
    lines.append(f"total ~{total:,} tokens for the reader model (rough: the smoke run measures the real figure),"
                 " plus the judge")
    lines.append(f"caps: {LedgerCaps.from_env().describe()}")
    return "\n".join(lines)


# ── Lanes ──────────────────────────────────────────────────────────────────


def lane_problem(provider: str, caps: LedgerCaps, env: Mapping[str, str] | None = None, *, model: str = "") -> str:
    """Why *provider* (and *model*) can't run now, or ''. Decided from local files and settings only."""
    source = os.environ if env is None else env
    billing = billing_for(provider)
    if billing == API and caps.max_usd <= 0:
        return (f"{provider} is a paid API and the paid cap is $0 (ROW_BOT_BENCH_MAX_USD); paid runs need the "
                "owner's key and go-ahead")
    if billing == LOCAL and provider == "ollama" and model and model != LOCAL_MODEL:
        return f"the local model is {LOCAL_MODEL} (stock weights) only, not {model}"
    if billing == PLAN:
        from tests.e2e.live_eval import eval_profile

        ready, why = eval_profile.availability(source)
        if not ready:
            return why
    if billing in (PLAN, LOCAL) and not str(source.get(BOOKING_ENV, "") or "").strip():
        return f"set {BOOKING_ENV} to the shared booking file: the eval profile and the graphics card are booked"
    return ""


def booking_kind(provider: str) -> str | None:
    billing = billing_for(provider)
    return booking_module.EVAL_PROFILE if billing == PLAN else booking_module.GPU if billing == LOCAL else None


# ── Running ────────────────────────────────────────────────────────────────


def new_run_dir(label: str, env: Mapping[str, str] | None = None) -> Path:
    source = os.environ if env is None else env
    base = Path(str(source.get(OUT_ENV, "") or "").strip() or REPO_ROOT / ".tmp" / "bench")
    folder = base / f"{label}-{time.strftime('%Y%m%d-%H%M%S')}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def done_tasks(run_dir: Path) -> set[str]:
    return {item.task_id for item in read_jsonl(run_dir / "trajectories.jsonl") if item.status == "done"}


def _git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True,
                              check=False, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def run_worker(run_dir: Path, spec: dict[str, Any], *, timeout_min: float = TASK_TIMEOUT_MIN) -> int:
    """One task in a worker process; returns its exit code. Every attempt gets a new, empty scratch
    folder: a task resumed after a stop or a crash starts its memory from nothing, never on top of a
    half-ingested one."""
    task_stem = spec["task_id"].replace(":", "_").replace("/", "_")
    attempt = 1 + sum(1 for _item in (run_dir / "data").glob(f"{task_stem}-*")) if (run_dir / "data").is_dir() else 1
    stem = f"{task_stem}-{attempt}"
    data_dir, workspace = run_dir / "data" / stem, run_dir / "workspace" / stem
    for folder in (data_dir, workspace, run_dir / "specs", run_dir / "logs"):
        folder.mkdir(parents=True, exist_ok=True)
    spec = {**spec, "run_dir": str(run_dir), "log": str(run_dir / "logs" / f"{stem}.log")}
    spec_path = run_dir / "specs" / f"{stem}.json"
    spec_path.write_text(json.dumps(spec, indent=1, default=str), encoding="utf-8")
    try:
        completed = subprocess.run([sys.executable, "-m", "tests.e2e.bench.worker", "--spec", str(spec_path)],
                                   cwd=REPO_ROOT, env=child_env(data_dir, workspace), timeout=timeout_min * 60,
                                   check=False)
        return completed.returncode
    except subprocess.TimeoutExpired:
        return 124


def _book_and_run(run_dir: Path, spec: dict[str, Any], provider: str, what: str, expect: str) -> int:
    kind = booking_kind(provider)
    stop = lambda: (run_dir / "STOP").exists()  # noqa: E731
    if kind is None:
        return 77 if stop() else run_worker(run_dir, spec)
    book = booking_module.BookingFile(Path(os.environ[BOOKING_ENV]))
    if not book.wait_for(kind, stop=stop):
        return 77
    book.start(kind, what, expect)
    try:
        return run_worker(run_dir, spec)
    finally:
        book.end(kind, what)


def run(args: argparse.Namespace) -> int:
    if not enabled():
        print(f"set {ENABLE_ENV}=1 to run (it uses the model you name)")
        return 2
    caps = LedgerCaps.from_env()
    provider, model = split_ref(args.model)
    problem = lane_problem(provider, caps, model=model)
    if problem:
        print(f"not running: {problem}")
        return 2
    protocol = load_protocol(args.benchmark, args.protocol)
    tasks = build_tasks(args)
    label = f"{args.benchmark}-{args.mode}{'-dream' if args.dream else ''}"
    run_dir = Path(args.run_dir) if args.run_dir else new_run_dir(label)
    run_dir.mkdir(parents=True, exist_ok=True)
    questions = [vars(question) for task in tasks for question in task.questions]
    (run_dir / "questions.json").write_text(json.dumps(questions, indent=1), encoding="utf-8")
    (run_dir / "protocol.json").write_text(protocol_to_json(protocol), encoding="utf-8")
    if args.benchmark == LOCOMO and args.key:
        review = audit_review(Path(args.data), Path(args.key))
        asked = {item["question_id"] for item in questions}
        (run_dir / "audit-review.json").write_text(json.dumps(
            [row for row in review if row["question_id"] in asked], indent=1), encoding="utf-8")
    config = BenchConfig(provider=provider, model=model, tools=tuple(args.tools.split(",")) if args.tools else (),
                         reasoning_effort=args.reasoning_effort or "")
    record = {"run": {"benchmark": args.benchmark, "mode": args.mode, "data": Path(args.data).name,
                      "key": Path(args.key).name if args.key else "", "dream": bool(args.dream),
                      "perspectives": args.perspectives if args.benchmark == LOCOMO and args.mode == MEMORY else "",
                      "tier": args.tier if args.benchmark == LONGMEMEVAL_V2 else "",
                      "question_template": args.question_template, "git_commit": _git_commit(), "label": "self-run"}}
    (run_dir / "run.json").write_text(json.dumps({**record["run"], "config": config.to_dict(), "caps": asdict(caps),
                                                  "protocol": protocol.describe()}, indent=1), encoding="utf-8")
    finished = done_tasks(run_dir)
    print(f"run folder: {run_dir}")
    for task in tasks:
        if task.task_id in finished:
            print(f"{task.task_id}: done earlier")
            continue
        figures = estimate(task, args.mode)
        spec = {"kind": "memory", "mode": args.mode, "task_id": task.task_id, "benchmark": task.benchmark,
                "task": task.to_dict(), "config": asdict(config), "caps": asdict(caps),
                "usage_book": str(usage_book_path()), "dream": bool(args.dream), "context_tokens": args.context_tokens,
                "question_template": args.question_template, "record": record}
        expect = f"{max(10, figures['windows'] + figures['questions'])} min"
        code = _book_and_run(run_dir, spec, provider, f"{label} {task.task_id}", expect)
        print(f"{task.task_id}: exit {code}")
        if code in (75, 76, 77):
            print({75: "a cap stopped the run", 76: "the provider's usage limit stopped the run",
                   77: "stopped on request"}[code])
            return code
    if args.judge:
        return judge(args, run_dir=run_dir)
    write_report(run_dir)
    return 0


def _judged_by(judge_ref: str, row_judge: str) -> bool:
    """Whether a judgment row came from *judge_ref* (``model:codex:auto`` matches the plan model it chose)."""
    return row_judge == judge_ref or (judge_ref.endswith(":auto") and row_judge.startswith(judge_ref[:-len("auto")]))


def judge(args: argparse.Namespace, *, run_dir: Path | None = None) -> int:
    if not enabled():
        print(f"set {ENABLE_ENV}=1 to judge (it uses the judge model you name)")
        return 2
    run_dir = run_dir or Path(args.run_dir)
    caps = LedgerCaps.from_env()
    protocol = protocol_from_json((run_dir / "protocol.json").read_text(encoding="utf-8"))
    questions = [Question(**item) for item in json.loads((run_dir / "questions.json").read_text(encoding="utf-8"))]
    answers = _latest_answers(run_dir)
    keys = scoring.keys_for(questions)
    rows = _rows(run_dir / "judgments.jsonl")
    passes = [(args.judge, list(answers.values()))]
    if args.second_judge:
        ids = set(scoring.subset(answers, args.second_judge_subset))
        passes.append((args.second_judge, [answer for key, answer in answers.items() if key in ids]))
    for ref, chosen in passes:
        provider, model = split_ref(ref)
        problem = lane_problem(provider, caps, model=model)
        if problem:
            print(f"not judging with {ref}: {problem}")
            return 2
        canonical = f"model:{provider}:{model}"
        judged = {row["question_id"] for row in rows if _judged_by(canonical, row["judge"])}
        todo = [answer for answer in chosen if answer.question_id not in judged]
        if not todo:
            continue
        wanted = {answer.question_id for answer in todo}
        spec = {"kind": "judge", "task_id": f"judge-{provider}-{model}", "benchmark": protocol.name,
                "config": asdict(BenchConfig(provider=provider, model=model, tools=())), "caps": asdict(caps),
                "usage_book": str(usage_book_path()), "protocol": protocol_to_json(protocol),
                "questions": [vars(item) for item in questions if item.question_id in wanted],
                "answers": [asdict(item) for item in todo], "keys": list(keys), "runs": args.judge_runs}
        code = _book_and_run(run_dir, spec, provider, f"{protocol.name} judge {ref}",
                             f"{max(5, len(todo) // 20)} min")
        print(f"judge {ref}: exit {code}")
        if code:
            return code
    write_report(run_dir)
    return 0


# ── Report (no Row-Bot, no network) ────────────────────────────────────────


def _rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _latest_answers(run_dir: Path) -> dict[str, scoring.Answer]:
    """The newest answer per question (a resumed run may have answered a question twice)."""
    found: dict[str, scoring.Answer] = {}
    for row in _rows(run_dir / "answers.jsonl"):
        found[row["question_id"]] = scoring.Answer(**row)
    return found


def _spread(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"mean": None, "p50": None, "p95": None}
    ordered = sorted(values)
    return {"mean": round(statistics.fmean(ordered), 2), "p50": round(statistics.median(ordered), 2),
            "p95": round(ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))], 2)}


def _tokens(steps: list[Any]) -> dict[str, Any]:
    calls = [call for step in steps for call in step.calls]
    return {"calls": len(calls), "input_tokens": sum(int(call.input_tokens or 0) for call in calls),
            "cache_read_tokens": sum(int(call.cache_read_tokens or 0) for call in calls),
            "output_tokens": sum(int(call.output_tokens or 0) for call in calls),
            "cost_usd": round(sum(float(call.cost_usd or 0) for call in calls), 6)}


def published_rows(benchmark: str | None, path: Path = PUBLISHED) -> list[dict[str, Any]]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [row for row in document.get("rows") or [] if row.get("benchmark") == benchmark]


def build_scores(run_dir: Path) -> dict[str, Any]:
    run = json.loads((run_dir / "run.json").read_text(encoding="utf-8")) if (run_dir / "run.json").is_file() else {}
    protocol = run.get("protocol") or {}
    judgments = [scoring.Judgment(**row) for row in _rows(run_dir / "judgments.jsonl")]
    questions = [Question(**item) for item in json.loads((run_dir / "questions.json").read_text(encoding="utf-8"))] \
        if (run_dir / "questions.json").is_file() else []
    keys = scoring.keys_for(questions) if questions else ("original",)
    judges = sorted({item.judge for item in judgments})
    published = f"model:{protocol.get('published_judge', '')}"
    headline = protocol.get("headline", "questions")
    scores = []
    for name in judges:
        for key in keys:
            item = scoring.accuracy(judgments, key=key, judge=name)
            item["headline"] = item["mean_over_categories"] if headline == "categories" else item["accuracy"]
            item["published_judge"] = name == published
            scores.append(item)
    # The newest attempt of each task (a resumed task's earlier attempt is in the file too).
    every = read_jsonl(run_dir / "trajectories.jsonl")
    trajectories = list({item.task_id: item for item in every}.values())
    memory_runs = [item for item in trajectories if item.config.get("kind") == "memory"]
    ingest_steps = [step for item in memory_runs for step in item.steps if step.kind in {"ingest", "dream"}]
    turn_steps = [step for item in memory_runs for step in item.steps if step.kind == "turn"]
    sensitivity = []
    if len(judges) > 1:
        primary = next((name for name in judges if name == published), judges[0])
        sensitivity = [scoring.sensitivity(judgments, key=key, primary=primary, secondary=other)
                       for other in judges if other != primary for key in keys]
    config = run.get("config") or {}
    readers = sorted({str(item.config.get("model") or "") for item in memory_runs})
    local = next((item.config.get("local_model") for item in memory_runs if item.config.get("local_model")), None)
    judge_settings = sorted({str(item.config.get("judge_settings")) for item in every
                             if item.config.get("kind") == "judge" and item.config.get("judge_settings")})
    return {
        "label": "self-run", "benchmark": run.get("benchmark"), "mode": run.get("mode", MEMORY),
        "git_commit": run.get("git_commit"),
        "reader": {"provider": config.get("provider"), "models": readers, "local_model": local,
                   "reasoning_effort": config.get("reasoning_effort"), "tools": config.get("tools")},
        "dream": run.get("dream"), "protocol": protocol, "judge_settings": judge_settings,
        "scores": scores, "judge_sensitivity": sensitivity,
        "tasks": {"done": sum(item.status == "done" for item in memory_runs),
                  "other": [{"task_id": item.task_id, "status": item.status, "reason": item.reason}
                            for item in trajectories if item.status != "done"]},
        "ingestion": {"sessions": sum(step.kind == "ingest" for step in ingest_steps),
                      "seconds": round(sum(step.seconds for step in ingest_steps), 1), **_tokens(ingest_steps),
                      "memories_saved": sum(int(step.metrics.get("saved") or 0) for step in ingest_steps)},
        "questions": {"asked": len(turn_steps), "not_done": sum(step.status != "done" for step in turn_steps),
                      "with_image_not_sent": sum(bool(item.image) for item in questions),
                      "truncated_history": sum(bool(step.metrics.get("truncated")) for step in turn_steps),
                      "seconds": _spread([step.seconds for step in turn_steps]), **_tokens(turn_steps),
                      "tokens_per_question": _spread([float(sum(int(call.input_tokens or 0) + int(call.output_tokens
                                                     or 0) for call in step.calls)) for step in turn_steps])},
        # Every judge attempt judged other answers, so all of them count.
        "judging": {"judgments": len(judgments),
                    **_tokens([step for item in every for step in item.steps if step.kind == "judge"])},
        "published": published_rows(run.get("benchmark")),
    }


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{100 * value:.1f}%"


def render_report(scores: Mapping[str, Any]) -> str:
    reader = scores["reader"]
    local = reader.get("local_model") or {}
    model = ", ".join(reader.get("models") or []) + (f" (digest {str(local.get('digest'))[:19]})" if local else "")
    mode = "full context, no memory" if scores.get("mode") == FULL_CONTEXT else \
        f"Row-Bot memory, Dream Cycle {'on' if scores.get('dream') else 'off'}"
    lines = [f"# {scores.get('benchmark')}: {mode} (self-run, {model})", "",
             f"Reader: {reader.get('provider')} {model}; reasoning {reader.get('reasoning_effort')}; tools "
             f"{reader.get('tools') if scores.get('mode') != FULL_CONTEXT else 'none'}; commit "
             f"{str(scores.get('git_commit'))[:12]}.",
             f"Judge protocol: {scores['protocol'].get('name')} (source SHA-256 "
             f"{str(scores['protocol'].get('sha256'))[:16]}...); published judge "
             f"{scores['protocol'].get('published_judge')}; judge settings: "
             f"{'; '.join(scores.get('judge_settings') or ['n/a'])}.",
             "", "| key | judge | questions | score | over categories | matched judge |", "|---|---|---|---|---|---|"]
    for item in scores["scores"]:
        lines.append(f"| {item['key']} | {item['judge']} | {item['questions']} | {_pct(item['headline'])} | "
                     f"{_pct(item['mean_over_categories'])} | "
                     f"{'yes' if item['published_judge'] else 'no: labelled, not the published judge'} |")
    for item in scores["scores"]:
        cells = ", ".join(f"{name} {_pct(value['accuracy'])} (n={value['questions']})"
                          for name, value in item["by_category"].items())
        lines.append(f"- {item['key']} / {item['judge']}: {cells}; abstention {_pct(item['abstention'])}, "
                     f"non-abstention {_pct(item['non_abstention'])}; unparsed {item['unparsed_verdicts']}")
    for item in scores["judge_sensitivity"]:
        if item.get("questions"):
            lines.append(f"- judge agreement ({item['key']}, n={item['questions']}): {item['primary']} "
                         f"{_pct(item['primary_accuracy'])} vs {item['secondary']} {_pct(item['secondary_accuracy'])}"
                         f", agreement {_pct(item['agreement'])}, kappa {item['cohen_kappa']}")
    ingest, asked, judging = scores["ingestion"], scores["questions"], scores["judging"]
    lines += ["", f"Ingestion: {ingest['sessions']} sessions in {ingest['seconds']} s; {ingest['calls']} calls, "
              f"{ingest['input_tokens']:,} input ({ingest['cache_read_tokens']:,} cached) + {ingest['output_tokens']:,} "
              f"output tokens; {ingest['memories_saved']} memories saved; ${ingest['cost_usd']:.4f}.",
              f"Questions: {asked['asked']} asked ({asked['not_done']} not finished, {asked['truncated_history']} with "
              f"a cut history, {asked['with_image_not_sent']} with a screenshot not sent); seconds {asked['seconds']}; "
              f"tokens per question {asked['tokens_per_question']}; {asked['input_tokens']:,} input + "
              f"{asked['output_tokens']:,} output tokens; ${asked['cost_usd']:.4f}.",
              f"Judging: {judging['judgments']} judgments; {judging['calls']} calls, {judging['input_tokens']:,} input "
              f"+ {judging['output_tokens']:,} output tokens; ${judging['cost_usd']:.4f}."]
    for item in scores["tasks"]["other"]:
        lines.append(f"- task {item['task_id']}: {item['status']} ({item['reason']})")
    if scores.get("published"):
        lines += ["", "Published by others (context, not head-to-head: readers, judges and dates differ):", "",
                  "| system | reader | judge | key | score | source | date |", "|---|---|---|---|---|---|---|"]
        for row in scores["published"]:
            lines.append(f"| {row['system']} | {row['reader']} | {row['judge']} | {row.get('key', '')} | "
                         f"{_pct(row['score'])} | {row['source']} | {row['date']} |")
    return "\n".join(lines) + "\n"


def write_report(run_dir: Path) -> dict[str, Any]:
    scores = build_scores(run_dir)
    (run_dir / "scores.json").write_text(json.dumps(scores, indent=1), encoding="utf-8")
    (run_dir / "report.md").write_text(render_report(scores), encoding="utf-8")
    return scores


# ── CLI ────────────────────────────────────────────────────────────────────


def _task_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--benchmark", required=True, choices=BENCHMARKS)
    parser.add_argument("--data", required=True, help="locomo10.json / longmemeval_s_cleaned.json / BEAM's chats/1M "
                                                      "folder / the LongMemEval-V2 data folder")
    parser.add_argument("--mode", default=MEMORY, choices=(MEMORY, FULL_CONTEXT))
    parser.add_argument("--key", default="", help="LoCoMo: the audit's errors.json (the audited key)")
    parser.add_argument("--conversations", default="", help="LoCoMo (from 0) / BEAM (from 1): comma list")
    parser.add_argument("--categories", default="1,2,3,4", help="LoCoMo: categories to ask (5 is adversarial)")
    parser.add_argument("--perspectives", default="both", choices=("both", "a"))
    parser.add_argument("--question-ids", default="", help="LongMemEval-S: comma list of question ids")
    parser.add_argument("--tier", default="small", choices=("small", "medium"), help="LongMemEval-V2's haystacks")
    parser.add_argument("--domains", default="", help="LongMemEval-V2: web, enterprise (default both)")
    parser.add_argument("--questions", type=int, default=None,
                        help="LoCoMo: per conversation; LongMemEval-S: instances; BEAM: per category; "
                             "LongMemEval-V2: per haystack")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bench.py", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan", help="tasks and rough tokens; no Row-Bot, no network")
    _task_flags(plan)
    run_parser = sub.add_parser("run", help="ingest and ask (needs ROW_BOT_BENCH=1); then judge with --judge")
    _task_flags(run_parser)
    run_parser.add_argument("--protocol", required=True,
                            help="the judge's source file(s): LoCoMo prompts.yaml; LongMemEval evaluate_qa.py; BEAM "
                                 "src/prompts.py,src/evaluation/compute_metrics.py; LongMemEval-V2 qa_eval_metrics.py")
    run_parser.add_argument("--model", required=True, help=f"the reader, provider:model (codex:auto, ollama:{LOCAL_MODEL})")
    run_parser.add_argument("--tools", default="memory,conversation_search")
    run_parser.add_argument("--reasoning-effort", default="", help="'' is Row-Bot's own default (recorded)")
    run_parser.add_argument("--dream", action="store_true", help="one Dream Cycle pass after the last session")
    run_parser.add_argument("--context-tokens", type=int, default=0,
                            help="full-context: the reader's window (default: Row-Bot's catalog)")
    run_parser.add_argument("--question-template", default="{question}")
    run_parser.add_argument("--run-dir", default="", help="resume this run folder")
    run_parser.add_argument("--judge", default="", help="judge right after the run with this model, provider:model")
    judge_parser = sub.add_parser("judge", help="judge a run's answers")
    judge_parser.add_argument("--run-dir", required=True)
    judge_parser.add_argument("--judge", required=True, help="the judge, provider:model")
    for item in (run_parser, judge_parser):
        item.add_argument("--judge-runs", type=int, default=1)
        item.add_argument("--second-judge", default="", help=f"judge agreement: e.g. ollama:{LOCAL_MODEL} on a subset")
        item.add_argument("--second-judge-subset", type=int, default=100)
    report = sub.add_parser("report", help="scores.json and report.md from a run folder; no Row-Bot, no network")
    report.add_argument("--run-dir", required=True)
    verify = sub.add_parser("protocol", help="load a judge protocol and print its SHA-256 and templates' keys")
    verify.add_argument("--benchmark", required=True, choices=BENCHMARKS)
    verify.add_argument("--protocol", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "plan":
        print(render_plan(build_tasks(args), args))
        return 0
    if args.command == "run":
        return run(args)
    if args.command == "judge":
        return judge(args)
    if args.command == "report":
        print(render_report(write_report(Path(args.run_dir))))
        return 0
    protocol: JudgeProtocol = load_protocol(args.benchmark, args.protocol)
    print(json.dumps({**protocol.describe(), "templates": sorted(protocol.templates)}, indent=1))
    return 0
