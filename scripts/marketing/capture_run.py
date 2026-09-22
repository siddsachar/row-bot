"""Safety, provenance, and filesystem boundaries for landing-story runs."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable


RUN_SCHEMA = 1
SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")
SENSITIVE_PATTERNS = (
    re.compile(r"\b(?:sk|ghp|xox[baprs])[-_][A-Za-z0-9_-]{10,}\b"),
    re.compile(r"BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY"),
    re.compile(r"[A-Za-z]:\\Users\\[^\\\s]+", re.IGNORECASE),
    re.compile(r"/Users/[^/\s]+"),
    re.compile(r"\b[A-Za-z0-9._%+-]+@(?!example\.(?:com|test)\b)[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
)


class CaptureSafetyError(RuntimeError):
    """Raised before a capture operation can cross a safety boundary."""


class GenerationBudgetError(CaptureSafetyError):
    """Raised when preparation would exceed its explicit generation budget."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_contained(path: Path, root: Path, *, label: str = "output") -> Path:
    """Resolve *path* and reject symlink/traversal escape from *root*."""

    resolved_root = root.resolve()
    resolved = path.resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise CaptureSafetyError(f"{label} escapes the allowed root")
    return resolved


def require_exact_normal_profile(
    selected: Path | None,
    *,
    authorize: bool,
    normal_profile: Path,
) -> Path:
    """Allow live preparation only against the exact canonical profile."""

    normal = normal_profile.expanduser().resolve()
    candidate = (selected or normal).expanduser().resolve()
    if candidate != normal:
        if authorize:
            raise CaptureSafetyError(
                "--authorize-real-profile is valid only for the exact normal Row-Bot profile"
            )
        raise CaptureSafetyError("the selected profile is not the normal Row-Bot profile")
    if not authorize:
        raise CaptureSafetyError(
            "normal Row-Bot profile access requires --authorize-real-profile"
        )
    return normal


def _row_bot_process(command: str) -> bool:
    lowered = command.casefold().replace("\\", "/")
    if "capture_landing_story.py" in lowered:
        return False
    return any(
        marker in lowered
        for marker in (
            "/app.py",
            " app.py",
            "row_bot.app",
            "row-bot.exe",
            "row_bot.launcher",
        )
    )


def find_profile_mutators(
    normal_profile: Path,
    *,
    processes: Iterable[Any] | None = None,
    current_pid: int | None = None,
) -> list[dict[str, Any]]:
    """Conservatively identify other Row-Bot processes using the normal profile.

    Process environments are inspected when available. A Row-Bot process with
    no explicit data directory is assumed to use the normal profile. Failures
    to inspect an unrelated process are ignored; failures after Row-Bot identity
    is established are treated as busy rather than as permission to proceed.
    """

    if processes is None:
        try:
            import psutil
        except ImportError as exc:
            raise CaptureSafetyError("psutil is required for profile quiescence checks") from exc
        processes = psutil.process_iter(["pid", "name", "cmdline"])
    owner = int(current_pid if current_pid is not None else os.getpid())
    normal = normal_profile.resolve()
    busy: list[dict[str, Any]] = []
    for process in processes:
        try:
            info = getattr(process, "info", {}) or {}
            pid = int(info.get("pid") or getattr(process, "pid", 0) or 0)
            if not pid or pid == owner:
                continue
            cmdline = info.get("cmdline") or []
            command = " ".join(str(item) for item in cmdline)
            name = str(info.get("name") or "")
            identity = f"{name} {command}".strip()
            if not _row_bot_process(identity):
                continue
            configured = ""
            try:
                environment = process.environ()
                configured = str(environment.get("ROW_BOT_DATA_DIR") or "")
            except Exception:
                configured = ""
            if configured and Path(configured).expanduser().resolve() != normal:
                continue
            busy.append({"pid": pid, "name": name or "Row-Bot"})
        except Exception:
            continue
    return sorted(busy, key=lambda item: int(item["pid"]))


def require_profile_quiescent(
    normal_profile: Path,
    *,
    processes: Iterable[Any] | None = None,
    current_pid: int | None = None,
) -> None:
    busy = find_profile_mutators(
        normal_profile,
        processes=processes,
        current_pid=current_pid,
    )
    if busy:
        pids = ", ".join(str(item["pid"]) for item in busy)
        raise CaptureSafetyError(
            f"normal Row-Bot profile is busy in another process (PID {pids}); no process was stopped"
        )


@dataclass
class GenerationBudget:
    """One-way attempt ledger; uncertain outcomes stop the run."""

    maximum: int
    attempts: list[dict[str, Any]] = field(default_factory=list)
    terminal_status: str = "ready"

    def begin(self, *, model: str, purpose: str) -> int:
        if self.terminal_status != "ready":
            raise GenerationBudgetError(
                f"generation is stopped after terminal status {self.terminal_status}"
            )
        if len(self.attempts) >= self.maximum:
            raise GenerationBudgetError(
                f"generation attempt budget exhausted ({self.maximum})"
            )
        attempt = {
            "number": len(self.attempts) + 1,
            "model": model,
            "purpose": purpose,
            "status": "started",
            "started_at": utc_now(),
        }
        self.attempts.append(attempt)
        return int(attempt["number"])

    def finish(
        self,
        number: int,
        *,
        status: str,
        operation_id: str = "",
        conversation_id: str = "",
        provider_call_count: int | None = None,
        durable_assistant_turn_count: int | None = None,
    ) -> None:
        if status not in {
            "succeeded",
            "failed_safe",
            "artifact_retained",
            "uncertain",
        }:
            raise ValueError("unsupported generation outcome")
        try:
            attempt = self.attempts[number - 1]
        except IndexError as exc:
            raise GenerationBudgetError("unknown generation attempt") from exc
        if attempt["status"] != "started":
            raise GenerationBudgetError("generation attempt is already terminal")
        for label, value in (
            ("provider_call_count", provider_call_count),
            ("durable_assistant_turn_count", durable_assistant_turn_count),
        ):
            if value is not None and (isinstance(value, bool) or int(value) < 0):
                raise GenerationBudgetError(f"{label} must be a non-negative integer")
        attempt.update(
            status=status,
            finished_at=utc_now(),
            operation_id=_safe_identifier(operation_id, "operation_id"),
            conversation_id=_safe_identifier(conversation_id, "conversation_id"),
        )
        for label, value in (
            ("provider_call_count", provider_call_count),
            ("durable_assistant_turn_count", durable_assistant_turn_count),
        ):
            if value is None:
                continue
            attempt[label] = int(value)
        if status == "uncertain":
            self.terminal_status = "uncertain"


def _safe_identifier(value: str, label: str) -> str:
    selected = str(value or "").strip()
    if selected and not SAFE_IDENTIFIER.fullmatch(selected):
        raise CaptureSafetyError(f"{label} is not safe for a provenance receipt")
    return selected


def find_sensitive_text(value: Any, *, location: str = "receipt") -> list[str]:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True).replace("\\\\", "\\")
    return [f"{location} matches blocked private-data pattern" for pattern in SENSITIVE_PATTERNS if pattern.search(serialized)]


@dataclass
class RunReceipt:
    run_id: str
    story_id: str
    prompt_version: int
    git_commit: str
    client: str
    models: dict[str, str]
    max_generation_attempts: int
    created_at: str = field(default_factory=utc_now)
    phase: str = "preflight"
    status: str = "started"
    generation_attempts: list[dict[str, Any]] = field(default_factory=list)
    safety_events: list[dict[str, Any]] = field(default_factory=list)
    records: dict[str, str] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)
    captures: list[dict[str, Any]] = field(default_factory=list)
    review: dict[str, Any] = field(default_factory=lambda: {"status": "pending"})

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema": RUN_SCHEMA,
            "run_id": _safe_identifier(self.run_id, "run_id"),
            "story_id": _safe_identifier(self.story_id, "story_id"),
            "prompt_version": self.prompt_version,
            "git_commit": _safe_identifier(self.git_commit, "git_commit"),
            "client": _safe_identifier(self.client, "client"),
            "profile": "normal",
            "models": dict(self.models),
            "max_generation_attempts": self.max_generation_attempts,
            "created_at": self.created_at,
            "phase": self.phase,
            "status": self.status,
            "generation_attempts": list(self.generation_attempts),
            "safety_events": list(self.safety_events),
            "records": {
                str(key): _safe_identifier(str(value), f"record {key}")
                for key, value in self.records.items()
            },
            "sources": list(self.sources),
            "captures": list(self.captures),
            "review": dict(self.review),
        }
        errors = find_sensitive_text(payload)
        if errors:
            raise CaptureSafetyError(errors[0])
        return payload

    def write(self, run_dir: Path) -> Path:
        target = require_contained(run_dir / "run.json", run_dir, label="run receipt")
        run_dir.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return target

    @classmethod
    def read(cls, run_dir: Path) -> "RunReceipt":
        path = require_contained(run_dir / "run.json", run_dir, label="run receipt")
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("schema") != RUN_SCHEMA:
            raise CaptureSafetyError("unsupported run receipt schema")
        return cls(
            run_id=raw["run_id"],
            story_id=raw["story_id"],
            prompt_version=int(raw["prompt_version"]),
            git_commit=raw["git_commit"],
            client=raw["client"],
            models=dict(raw["models"]),
            max_generation_attempts=int(raw["max_generation_attempts"]),
            created_at=raw["created_at"],
            phase=raw["phase"],
            status=raw["status"],
            generation_attempts=list(raw.get("generation_attempts") or []),
            safety_events=list(raw.get("safety_events") or []),
            records=dict(raw.get("records") or {}),
            sources=list(raw.get("sources") or []),
            captures=list(raw.get("captures") or []),
            review=dict(raw.get("review") or {"status": "pending"}),
        )


def mutate_receipt(
    run_dir: Path,
    mutate: Callable[[RunReceipt], None],
) -> RunReceipt:
    receipt = RunReceipt.read(run_dir)
    mutate(receipt)
    receipt.write(run_dir)
    return receipt
