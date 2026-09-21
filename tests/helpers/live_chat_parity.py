"""Fail-closed policy helpers for the opt-in owner-profile chat parity run.

This module is deliberately import-light.  In particular it does not import
``row_bot.data_paths`` or the tool registry: the runner must prove the explicit
opt-in before it resolves or reads the owner's normal profile.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
from typing import Callable, Mapping


LIVE_OPT_IN = "ROW_BOT_LIVE_CHAT_PARITY"
MAX_GENERATION_ATTEMPTS = 4
VALIDATION_TITLE = "React Chat Live Parity Validation"
FINAL_COMBINED_SCENARIO = "safe_calculator_stop"

SCENARIO_PROMPTS = {
    "shared_markdown": (
        "Synthetic UI validation only. Reply with a short heading, a two-item "
        "bulleted list, and one fenced code block containing the word parity. "
        "Do not use tools or inspect any files, memories, accounts, channels, "
        "prior conversations, or network resources."
    ),
    "stop_interruption": (
        "Synthetic UI validation only. Write a long harmless explanation of "
        "alternating blue and gold geometric tiles. Do not use tools or inspect "
        "files, memories, accounts, channels, prior conversations, or networks."
    ),
    "safe_calculator": (
        "Synthetic UI validation only. Use the calculate tool exactly once to "
        "evaluate 17 * 19, then state the result briefly. Do not use any other "
        "tool or inspect files, memories, accounts, channels, prior conversations, "
        "browser/computer controls, or network resources."
    ),
    "safe_calculator_stop": (
        "Synthetic UI validation only. Use the calculate tool exactly once to "
        "evaluate 17 * 19. After the tool result, write a long harmless explanation "
        "of alternating blue and gold geometric tiles so the response can be stopped. "
        "Do not use any other tool or inspect files, memories, accounts, channels, "
        "prior conversations, browser/computer controls, or network resources."
    ),
}


class LiveParitySafetyError(RuntimeError):
    """Raised when the live lane cannot proceed without owner intervention."""


def require_live_opt_in(environ: Mapping[str, str]) -> None:
    """Fail before profile resolution unless the exact live flag is present."""
    if environ.get(LIVE_OPT_IN) != "1":
        raise LiveParitySafetyError(
            f"live opt-in missing: set {LIVE_OPT_IN}=1 only for the authorized run"
        )


def resolve_profile_after_opt_in(
    environ: Mapping[str, str], resolver: Callable[[], Path]
) -> Path:
    """Resolve the launcher-equivalent profile only after checking opt-in."""
    require_live_opt_in(environ)
    return resolver().expanduser().resolve(strict=True)


@dataclass
class AttemptBudget:
    """One-shot generation admissions with no retry after uncertain outcomes."""

    limit: int = MAX_GENERATION_ATTEMPTS
    used_before: int = 0
    admitted: list[str] = field(default_factory=list)
    uncertain: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        if not 0 <= self.used_before <= self.limit:
            raise LiveParitySafetyError("invalid prior live attempt count")

    def admit(self, scenario_id: str) -> int:
        if scenario_id not in SCENARIO_PROMPTS:
            raise LiveParitySafetyError("unregistered live scenario")
        if scenario_id in self.admitted:
            raise LiveParitySafetyError("live scenario replay is forbidden")
        if self.used_before + len(self.admitted) >= self.limit:
            raise LiveParitySafetyError("live generation attempt cap reached")
        self.admitted.append(scenario_id)
        return self.used_before + len(self.admitted)

    def mark_uncertain(self, scenario_id: str) -> None:
        if scenario_id not in self.admitted:
            raise LiveParitySafetyError("cannot mark an unadmitted scenario uncertain")
        self.uncertain.add(scenario_id)

    def can_retry(self, scenario_id: str) -> bool:
        return scenario_id not in self.admitted and scenario_id not in self.uncertain


def transient_hash(value: str) -> str:
    """Return a stable evidence-safe digest, never the transient identifier."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


_FORBIDDEN_REPORT_KEYS = re.compile(
    r"(?:prompt|output|body|content|cookie|authorization|secret|token|credential|data_dir|path)",
    re.IGNORECASE,
)
_WINDOWS_ABSOLUTE_PATH = re.compile(r"(?i)\b[a-z]:[\\/]")
_POSIX_OWNER_PATH = re.compile(r"/(?:home|Users)/[^/\s]+/")


def validate_redacted_report(value: object) -> None:
    """Reject report payloads capable of retaining owner content or secrets."""

    def visit(item: object, location: str) -> None:
        if item is None or isinstance(item, (bool, int, float)):
            return
        if isinstance(item, str):
            if _WINDOWS_ABSOLUTE_PATH.search(item) or _POSIX_OWNER_PATH.search(item):
                raise LiveParitySafetyError(f"absolute path in report at {location}")
            for prompt in SCENARIO_PROMPTS.values():
                if prompt in item:
                    raise LiveParitySafetyError(f"prompt body in report at {location}")
            return
        if isinstance(item, list):
            for index, child in enumerate(item):
                visit(child, f"{location}[{index}]")
            return
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str) or _FORBIDDEN_REPORT_KEYS.search(key):
                    raise LiveParitySafetyError(f"forbidden report key at {location}")
                visit(child, f"{location}.{key}")
            return
        raise LiveParitySafetyError(f"unsupported report value at {location}")

    visit(value, "report")


def write_redacted_json(path: Path, payload: dict) -> None:
    validate_redacted_report(payload)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


@dataclass(frozen=True)
class CalculatorQualification:
    enabled: bool
    local_read_only: bool
    reason: str

    @property
    def qualified(self) -> bool:
        return self.enabled and self.local_read_only


def _saved_calculator_enabled(document: object) -> bool:
    if not isinstance(document, dict):
        raise LiveParitySafetyError("tool policy is not a JSON object")
    tools = document.get("tools")
    source = tools if isinstance(tools, dict) else document
    value = source.get("calculator", True)
    if type(value) is not bool:
        raise LiveParitySafetyError("calculator enablement is not boolean")
    return value


def qualify_saved_calculator(config_path: Path, implementation_path: Path) -> CalculatorQualification:
    """Prove the existing calculator policy and checked-in implementation.

    Absence from the saved map means the registered tool's documented
    ``enabled_by_default`` value applies; the AST proof below requires that
    value to remain exactly ``True``.
    """
    try:
        document = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
        enabled = _saved_calculator_enabled(document)
        tree = ast.parse(implementation_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, SyntaxError) as exc:
        raise LiveParitySafetyError("calculator policy could not be verified") from exc

    imports: set[str] = set()
    calls: set[str] = set()
    literals: set[str] = set()
    default_true = False
    calculator_class = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                calls.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                calls.add(node.func.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            literals.add(node.value)
        elif isinstance(node, ast.ClassDef) and node.name == "CalculatorTool":
            calculator_class = True
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == "enabled_by_default":
                    default_true = any(
                        isinstance(grandchild, ast.Return)
                        and isinstance(grandchild.value, ast.Constant)
                        and grandchild.value.value is True
                        for grandchild in ast.walk(child)
                    )

    forbidden_imports = {
        "aiohttp", "ftplib", "http", "imaplib", "paramiko", "requests", "smtplib",
        "socket", "subprocess", "urllib", "webbrowser",
    }
    forbidden_calls = {
        "open", "remove", "rename", "replace", "rmdir", "run", "system", "unlink",
        "urlopen", "write_text", "write_bytes",
    }
    exact_contract = (
        calculator_class
        and default_true
        and "calculator" in literals
        and "calculate" in literals
        and "simple_eval" in calls
        and not (imports & forbidden_imports)
        and not (calls & forbidden_calls)
    )
    reason = "qualified" if enabled and exact_contract else (
        "calculator_disabled" if not enabled else "calculator_implementation_unverified"
    )
    return CalculatorQualification(enabled=enabled, local_read_only=exact_contract, reason=reason)


def assert_localhost_url(url: str) -> None:
    if not re.fullmatch(r"http://127\.0\.0\.1:\d+(?:/.*)?", url):
        raise LiveParitySafetyError("live runner URL is not IPv4 localhost")
