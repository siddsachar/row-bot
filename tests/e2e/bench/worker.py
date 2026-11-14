"""``python -m tests.e2e.bench.worker --spec <file>``: one benchmark task in a fresh process.

The orchestrator starts it with :func:`runtime.child_env` (a scratch data folder, test
mode, the install count off, Learning's preview unset), so Row-Bot binds that folder when it
is first imported. Two kinds of task:

- ``memory``: ingest a :class:`~memory.datasets.MemoryTask`'s sessions, optionally Dream,
  ask its questions; writes the trajectory and the answers.
- ``judge``: judge answers with one judge model and protocol; writes the judgments.

Exit codes: 0 done, 75 a cap stopped it, 76 the provider's usage limit, 77 the stop file.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

EXIT_BUDGET = 75
EXIT_PROVIDER_LIMIT = 76
EXIT_STOPPED = 77


def _append(path: Path, rows: list[dict[str, Any]], redact: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        for row in rows:
            handle.write((redact(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)) + "\n")
                         .encode("utf-8"))


def _log_to(path: str) -> None:
    from tests.e2e.live_eval.report import redact

    class Redacting(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            return redact(super().format(record))

    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setLevel(logging.INFO)
    handler.setFormatter(Redacting("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger("row_bot")
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def _judge_call(runtime: Any, model_ref: str, protocol: Any) -> Any:
    """The judge as one Row-Bot model call. The protocol's temperature and token limit apply only when
    the judge is the published model itself; any other judge (GPT-6.1 Sol on the plan, a thinking local
    model, which a 10-token limit would silence) runs with Row-Bot's own settings, and the run says so."""
    from langchain_core.messages import HumanMessage, SystemMessage

    from row_bot import models
    from row_bot.providers.usage_ledger import with_usage_purpose

    llm = models.get_llm_for(model_ref)
    if model_ref == f"model:{protocol.published_judge}":
        settings: dict[str, Any] = {} if protocol.temperature is None else {"temperature": protocol.temperature}
        if protocol.max_tokens:
            settings["max_tokens"] = protocol.max_tokens
        llm = llm.bind(**settings)
    runnable = with_usage_purpose(llm, "judge")

    def call(messages: list[tuple[str, str]]) -> str:
        return _limited(lambda: runnable.invoke([SystemMessage(content=text) if role == "system"
                                                 else HumanMessage(content=text) for role, text in messages]))

    return call


def _context_window(model_ref: str) -> int:
    """The reader's context window from Row-Bot's catalog; the run refuses to guess one."""
    from row_bot import models

    size = models.get_model_max_context(model_ref)
    if not size:
        raise RuntimeError(f"no context window for {model_ref} in the catalog; pass --context-tokens")
    return int(size)


def _prompt_call(model_ref: str, purpose: str) -> Any:
    """One prompt in, the reply text out, through Row-Bot's own model for *model_ref* (its reasoning
    setting as Row-Bot ships it, no tools, no memory)."""
    from langchain_core.messages import HumanMessage

    from row_bot import models
    from row_bot.providers.usage_ledger import with_usage_purpose

    runnable = with_usage_purpose(models.get_llm_for(model_ref), purpose)

    def call(prompt: str) -> str:
        return _limited(lambda: runnable.invoke([HumanMessage(content=prompt)]))

    return call


def _limited(invoke: Any) -> str:
    """One call's reply text; a usage or rate limit becomes ``ProviderLimit`` (the run stops, resumable)."""
    from .runtime import ProviderLimit, limit_text

    try:
        return _text(invoke())
    except ProviderLimit:
        raise
    except Exception as exc:
        if limit_text(str(exc)):
            raise ProviderLimit(f"{type(exc).__name__}: {exc}"[:300]) from exc
        raise


def _text(reply: Any) -> str:
    content = getattr(reply, "content", reply)
    if isinstance(content, list):
        content = "".join(str(part.get("text") or "") if isinstance(part, dict) else str(part) for part in content)
    return str(content or "")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True)
    args = parser.parse_args(argv)
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    run_dir = Path(spec["run_dir"])

    from .runtime import check_environment

    check_environment()
    if spec.get("log"):
        _log_to(spec["log"])
    from tests.e2e.live_eval.pricing import load_rates
    from tests.e2e.live_eval.report import redact

    from .ledger import BudgetStop, CostLedger, LedgerCaps, UsageBook
    from .runtime import BenchConfig, BenchRuntime, ProviderLimit
    from .trajectory import Step, Trajectory, append_jsonl

    caps = LedgerCaps(**spec["caps"])
    ledger = CostLedger(caps, UsageBook(Path(spec["usage_book"])), rates=load_rates(), path=run_dir / "ledger.jsonl")
    config = BenchConfig(**{**spec["config"], "tools": tuple(spec["config"].get("tools") or ()),
                            "blocked_hosts": tuple(spec["config"].get("blocked_hosts") or ()),
                            "canaries": tuple(spec["config"].get("canaries") or ())})
    profile = os.environ.get("ROW_BOT_LIVE_EVAL_CODEX_PROFILE", "").strip()
    runtime = BenchRuntime(config, ledger, task_id=spec["task_id"], eval_profile=Path(profile) if profile else None)
    trajectory = Trajectory(task_id=spec["task_id"], benchmark=spec["benchmark"])
    code = 0
    try:
        runtime.setup()
        trajectory.config = {**config.to_dict(), "kind": spec["kind"], "mode": spec.get("mode") or "memory",
                             "dream": bool(spec.get("dream")), "caps": asdict(caps),
                             "network": runtime.policy.to_dict(), "setup_notes": runtime.notes,
                             **({"local_model": runtime.local_model} if runtime.local_model else {}),
                             **spec.get("record", {})}
        if spec["kind"] == "memory":
            code = _memory(spec, runtime, trajectory, run_dir, redact)
        elif spec["kind"] == "probe":
            code = _probe(runtime, trajectory)
        else:
            code = _judge(spec, runtime, trajectory, run_dir, redact)
        trajectory.status = trajectory.status if trajectory.status != "running" else "done"
    except BudgetStop as exc:
        trajectory.status, trajectory.reason, code = "budget", str(exc)[:500], EXIT_BUDGET
    except ProviderLimit as exc:
        trajectory.status, trajectory.reason, code = "provider_limit", str(exc)[:500], EXIT_PROVIDER_LIMIT
    except Exception as exc:  # noqa: BLE001 - the trajectory records it; the orchestrator reads the code
        trajectory.status, trajectory.reason, code = "error", f"{type(exc).__name__}: {exc}"[:500], 1
        logging.getLogger("row_bot.bench").exception("bench worker failed")
    finally:
        trajectory.finished_at = time.time()
        if runtime.sign_in is not None:
            trajectory.config["credentials_write_backs"] = runtime.sign_in.write_backs
        trajectory.config["ledger_refusals"] = len(ledger.refused)
        trajectory.steps.append(Step(kind="ledger", name="totals", metrics={"refused": ledger.refused[:5]}))
        runtime.close()
        append_jsonl(run_dir / "trajectories.jsonl", trajectory, redact=redact)
    return code


def _memory(spec: dict[str, Any], runtime: Any, trajectory: Any, run_dir: Path, redact: Any) -> int:
    from .memory import adapter
    from .memory.datasets import MemoryTask
    from .memory.judge import Answer

    task = MemoryTask.from_dict(spec["task"])
    stop = adapter.stop_file(run_dir)

    def answered(step: Any) -> None:
        # Each answer is written as soon as it exists: a crash or a stop keeps every one before it.
        trajectory.steps.append(step)
        _append(run_dir / "answers.jsonl", [asdict(Answer(
            question_id=step.name, hypothesis=step.answer, status=step.status, seconds=step.seconds,
            input_tokens=sum(int(call.input_tokens or 0) for call in step.calls),
            output_tokens=sum(int(call.output_tokens or 0) for call in step.calls), task_id=task.task_id))], redact)

    try:
        if spec.get("mode") == "full-context":
            from .memory import full_context

            context = int(spec.get("context_tokens") or 0) or _context_window(runtime.config.model_ref)
            trajectory.config["context_tokens"] = context
            trajectory.config["full_context_template_sha256"] = full_context.TEMPLATE_SHA256
            full_context.ask(runtime, task, _prompt_call(runtime.config.model_ref, "full_context"),
                             context_tokens=context, stop=stop, on_step=answered)
            return 0
        adapter.ingest(runtime, task, stop=stop, on_step=trajectory.steps.append)
        if spec.get("dream"):
            trajectory.steps.append(adapter.dream(runtime, task))
        adapter.ask(runtime, task, template=spec.get("question_template") or adapter.QUESTION_TEMPLATE,
                    stop=stop, on_step=answered)
    except adapter.StopRequested as exc:
        trajectory.status, trajectory.reason = "stopped", str(exc)
        return EXIT_STOPPED
    return 0


def _probe(runtime: Any, trajectory: Any) -> int:
    """Before a benchmark starts: one tiny call, to see the plan is not throttled (its limit is shared
    with the owner's everyday Row-Bot). A limit raises ``ProviderLimit`` (exit 76) and nothing starts."""
    from .trajectory import Step

    step = Step(kind="probe", name="throttle check", prompt="Reply with exactly: ok")
    started = time.monotonic()
    step.answer = _prompt_call(runtime.config.model_ref, "bench_probe")(step.prompt)[:50]
    step.seconds = round(time.monotonic() - started, 3)
    runtime.drain_calls(step, phase="probe")
    trajectory.steps.append(step)
    runtime.limits.check()
    return 0


def _judge(spec: dict[str, Any], runtime: Any, trajectory: Any, run_dir: Path, redact: Any) -> int:
    from .memory import judge as scoring
    from .memory.datasets import Question
    from .memory.protocols import protocol_from_json
    from .trajectory import Step

    protocol = protocol_from_json(spec["protocol"])
    questions = {item["question_id"]: Question(**item) for item in spec["questions"]}
    answers = [scoring.Answer(**item) for item in spec["answers"]]
    call = _judge_call(runtime, runtime.config.model_ref, protocol)
    judge_ref = runtime.config.model_ref
    trajectory.config["judge_settings"] = ("the protocol's" if judge_ref == f"model:{protocol.published_judge}"
                                           else "Row-Bot's own (the judge is not the published model)")
    trajectory.config["protocol"] = protocol.describe()
    for answer in answers:
        if (run_dir / "STOP").exists():
            trajectory.status, trajectory.reason = "stopped", f"stopped before judging {answer.question_id}"
            return EXIT_STOPPED
        step = Step(kind="judge", name=answer.question_id)
        started = time.monotonic()
        found = scoring.judge_answers([answer], questions, protocol, call, judge=judge_ref,
                                      keys=spec["keys"], runs=int(spec.get("runs") or 1))
        step.seconds = round(time.monotonic() - started, 3)
        step.metrics = {"scores": {f"{item.key}#{item.run}": item.score for item in found}}
        runtime.drain_calls(step, phase="judge")
        trajectory.steps.append(step)
        _append(run_dir / "judgments.jsonl", scoring.to_dicts(found), redact)
        runtime.limits.check()
    return 0


if __name__ == "__main__":
    sys.exit(main())
