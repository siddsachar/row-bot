"""Trajectories: what one benchmark task did, as JSONL, plus an ATIF exporter stub.

A trajectory holds the task's pinned configuration, its steps (an ingestion phase, a
turn, a judge call) and every model call with tokens, cost, latency, model id and
settings. Released traces are these files; nothing in them comes from the everyday
profile, and provider tokens are never written (the worker redacts its output).
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from .ledger import CallEntry, totals

SCHEMA = "row-bot-bench-trajectory/1"


@dataclass
class Step:
    """One step: ``ingest`` (a session into memory), ``turn`` (a Row-Bot turn) or ``judge``."""

    kind: str
    name: str = ""
    started_at: float = field(default_factory=time.time)
    seconds: float = 0.0
    status: str = "done"
    prompt: str = ""
    answer: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    calls: list[CallEntry] = field(default_factory=list)


@dataclass
class Trajectory:
    task_id: str
    benchmark: str
    config: dict[str, Any] = field(default_factory=dict)
    steps: list[Step] = field(default_factory=list)
    status: str = "running"
    reason: str = ""
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    def calls(self) -> list[CallEntry]:
        return [call for step in self.steps for call in step.calls]

    def summary(self) -> dict[str, Any]:
        by_kind: dict[str, dict[str, Any]] = {}
        for kind in sorted({step.kind for step in self.steps}):
            steps = [step for step in self.steps if step.kind == kind]
            calls = [call for step in steps for call in step.calls]
            by_kind[kind] = {
                "steps": len(steps),
                "seconds": round(sum(step.seconds for step in steps), 2),
                "calls": len(calls),
                "input_tokens": sum(int(call.input_tokens or 0) for call in calls),
                "output_tokens": sum(int(call.output_tokens or 0) for call in calls),
            }
        return {"by_step_kind": by_kind, "by_billing": totals(self.calls())}

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["schema"] = SCHEMA
        data["summary"] = self.summary()
        return data


def _step(data: dict[str, Any]) -> Step:
    calls = [CallEntry(**call) for call in data.pop("calls", [])]
    return Step(**data, calls=calls)


def from_dict(data: dict[str, Any]) -> Trajectory:
    values = {key: value for key, value in data.items() if key in Trajectory.__dataclass_fields__}
    values["steps"] = [_step(dict(step)) for step in values.get("steps", [])]
    return Trajectory(**values)


def append_jsonl(path: Path, trajectory: Trajectory, *, redact: Callable[[str], str] = lambda text: text) -> None:
    """One trajectory per line; a crash keeps every line written before it. *redact* scrubs the
    line (the worker passes the live eval's redactor, which knows the sign-in's tokens)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(trajectory.to_dict(), ensure_ascii=False, sort_keys=True, default=str)
    with path.open("ab") as handle:
        handle.write((redact(line) + "\n").encode("utf-8"))


def read_jsonl(path: Path) -> list[Trajectory]:
    if not path.is_file():
        return []
    return [from_dict(json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ── ATIF (Terminal-Bench / Harbor) ─────────────────────────────────────────

ATIF_VERSION = "ATIF-v1.2"


def to_atif(trajectory: Trajectory, *, agent_name: str = "row-bot", agent_version: str = "") -> dict[str, Any]:
    """A stub of the Agent Trajectory Interchange Format for a Harbor adapter (Terminal-Bench, later).

    It maps what a trajectory already holds (turn steps, tool calls, per-call token
    counts and cost) onto ATIF's ``steps`` and ``final_metrics``. Check the field names
    against Harbor's published ATIF schema, and add observations from tool results,
    before the first Terminal-Bench submission; this is not yet a validated export.
    """
    steps: list[dict[str, Any]] = []
    for index, step in enumerate((item for item in trajectory.steps if item.kind == "turn"), start=1):
        if step.prompt:
            steps.append({"step_id": len(steps) + 1, "timestamp": _iso(step.started_at), "source": "user",
                          "message": step.prompt})
        steps.append({
            "step_id": len(steps) + 1,
            "timestamp": _iso(step.started_at + step.seconds),
            "source": "agent",
            "model_name": step.calls[-1].model if step.calls else "",
            "message": step.answer,
            "tool_calls": [{"tool_call_id": f"{index}-{number}", "function_name": str(call.get("name") or ""),
                            "arguments": call.get("args") or {}} for number, call in enumerate(step.tool_calls)],
            "metrics": _metrics(step.calls),
        })
    return {
        "schema_version": ATIF_VERSION,
        "session_id": trajectory.task_id,
        "agent": {"name": agent_name, "version": agent_version,
                  "model_name": str(trajectory.config.get("model") or "")},
        "steps": steps,
        "final_metrics": _metrics(trajectory.calls()),
    }


def _metrics(calls: Iterable[CallEntry]) -> dict[str, Any]:
    calls = list(calls)
    return {
        "prompt_tokens": sum(int(call.input_tokens or 0) for call in calls),
        "completion_tokens": sum(int(call.output_tokens or 0) for call in calls),
        "cached_tokens": sum(int(call.cache_read_tokens or 0) for call in calls),
        "cost_usd": round(sum(float(call.cost_usd or 0.0) for call in calls), 6),
    }


def _iso(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))
