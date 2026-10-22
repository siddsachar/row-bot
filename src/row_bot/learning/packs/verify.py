"""Verifiers for pack tasks: executable checks of what a run produced (E11).

:func:`verify` checks an :class:`Outcome` (the final answer, the workspace's files and
the fake apps' final state) against a task's :class:`~row_bot.learning.packs.Verifier`.
Code tests run in a fresh sandbox with the network off (``learning.sandbox``).
:func:`solve` plays a :class:`~row_bot.learning.packs.Solution` (a task's reference or
decoy) in a sandbox, so every verifier can be shown to pass the reference and fail the
decoy.
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from row_bot.learning.packs import PackTask, Solution
from row_bot.learning.packs.fake_apps import FakeApps

_CHECK_TESTS = "_row_bot_sandbox_check_tests.py"
_NUMBER = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?(?![\w])")
MAX_FILE_BYTES = 256 * 1024


@dataclass(frozen=True)
class Outcome:
    """What a run left: its final answer, the workspace's text files and the fake apps' state."""

    answer: str = ""
    files: Mapping[str, str] = field(default_factory=dict)
    apps: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Verdict:
    passed: bool
    kind: str
    detail: str = ""
    backend: str = ""


def normalize(text: Any) -> str:
    """Lower case, markdown emphasis and surrounding punctuation removed, spaces collapsed."""

    cleaned = re.sub(r"[`*_]", "", str(text if text is not None else "")).strip().casefold()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(" .!\"'")


def numbers_in(text: str) -> list[float]:
    found: list[float] = []
    for match in _NUMBER.finditer(str(text or "")):
        try:
            found.append(float(match.group(0).replace(",", "")))
        except ValueError:
            continue
    return found


def parse_json(text: str) -> Any:
    """A JSON value from an answer (a fenced block allowed)."""

    body = str(text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", body, re.S)
    if fenced:
        body = fenced.group(1).strip()
    try:
        return json.loads(body)
    except ValueError:
        starts = [index for index in (body.find("{"), body.find("[")) if index >= 0]
        if not starts:
            raise
        start = min(starts)
        end = max(body.rfind("}"), body.rfind("]"))
        return json.loads(body[start:end + 1])


def _same(left: Any, right: Any) -> bool:
    """JSON values equal: numbers by value, strings trimmed, objects by key, lists in order."""

    if isinstance(left, bool) or isinstance(right, bool):
        return left is right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isclose(float(left), float(right), rel_tol=1e-9, abs_tol=1e-9)
    if isinstance(left, str) and isinstance(right, str):
        return left.strip() == right.strip()
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return set(left) == set(right) and all(_same(left[key], right[key]) for key in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right))
    return left == right


def _rows(text: str) -> list[list[str]]:
    reader = csv.reader(io.StringIO(str(text or "").strip()))
    return [[cell.strip() for cell in row] for row in reader if any(cell.strip() for cell in row)]


def _text_lines(text: str) -> list[str]:
    return [line.rstrip() for line in str(text or "").replace("\r\n", "\n").strip("\n").split("\n")]


# ── Answer, JSON and file checks ────────────────────────────────────────────


def _check_answer(task: PackTask, answer: str) -> Verdict:
    verifier = task.verifier
    expected = verifier.expected
    if verifier.match == "exact":
        ok = normalize(answer) == normalize(expected)
    elif verifier.match == "contains":
        ok = normalize(expected) in normalize(answer)
    elif verifier.match == "any_of":
        ok = normalize(answer) in {normalize(item) for item in expected}
    else:  # number: the answer's numbers are all the expected one
        found = numbers_in(answer)
        target = float(expected)
        ok = bool(found) and all(abs(value - target) <= verifier.tolerance + 1e-9 for value in found)
    return Verdict(ok, "answer", "" if ok else f"expected {expected!r}")


def _check_json(task: PackTask, answer: str) -> Verdict:
    try:
        value = parse_json(answer)
    except ValueError:
        return Verdict(False, "json_answer", "the answer is not JSON")
    ok = _same(value, task.verifier.expected)
    return Verdict(ok, "json_answer", "" if ok else "the JSON differs")


def _check_file(task: PackTask, files: Mapping[str, str]) -> Verdict:
    verifier = task.verifier
    actual = files.get(verifier.path)
    if actual is None:
        return Verdict(False, "file", f"{verifier.path} was not written")
    expected = verifier.expected
    if verifier.match == "json":
        try:
            ok = _same(json.loads(actual), expected if not isinstance(expected, str) else json.loads(expected))
        except ValueError:
            return Verdict(False, "file", f"{verifier.path} is not JSON")
    elif verifier.match == "csv":
        ok = _rows(actual) == _rows(expected)
    elif verifier.match == "csv_rows":
        got, want = _rows(actual), _rows(expected)
        ok = bool(got) and bool(want) and got[0] == want[0] and Counter(map(tuple, got[1:])) == Counter(
            map(tuple, want[1:]))
    elif verifier.match == "lines":
        ok = [line.strip() for line in _text_lines(actual) if line.strip()] == [
            line.strip() for line in _text_lines(expected) if line.strip()]
    else:
        ok = _text_lines(actual) == _text_lines(expected)
    return Verdict(ok, "file", "" if ok else f"{verifier.path} differs")


# ── Fake-app checks ─────────────────────────────────────────────────────────


def _matches(value: Any, wanted: Any) -> bool:
    if isinstance(wanted, Mapping) and "contains" in wanted:
        return normalize(wanted["contains"]) in normalize(value)
    if isinstance(wanted, list):
        if not isinstance(value, list):
            return False
        return sorted(normalize(item) for item in value) == sorted(normalize(item) for item in wanted)
    if isinstance(wanted, (int, float)) and not isinstance(wanted, bool):
        try:
            return float(value) == float(wanted)
        except (TypeError, ValueError):
            return False
    return normalize(value) == normalize(wanted)


def _check_apps(task: PackTask, apps: Mapping[str, Any]) -> Verdict:
    for check in task.verifier.expected:
        app, _, collection = str(check["in"]).partition(".")
        items = list(((apps.get(app) or {}).get(collection)) or [])
        where = dict(check.get("where") or {})
        found = [item for item in items if all(_matches(item.get(key), wanted) for key, wanted in where.items())]
        count = int(check.get("count", 1))
        if len(found) != count:
            return Verdict(False, "app_state", f"{check['in']} matching {where}: {len(found)}, expected {count}")
    return Verdict(True, "app_state")


# ── Code tests ──────────────────────────────────────────────────────────────


def _check_code(task: PackTask, files: Mapping[str, str], *, backend: str, timeout: float) -> Verdict:
    from row_bot.learning.sandbox import Sandbox, run_python

    if task.verifier.path not in files:
        return Verdict(False, "code_tests", f"{task.verifier.path} was not written")
    with Sandbox() as box:
        for name, content in files.items():
            if name.endswith(".py") or name in task.files:
                box.write(name, content)
        box.write(_CHECK_TESTS, str(task.verifier.expected))
        result = run_python(box, _CHECK_TESTS, timeout=timeout, backend=backend)  # type: ignore[arg-type]
    if result.timed_out:
        return Verdict(False, "code_tests", "the tests ran out of time", result.backend)
    detail = "" if result.passed else (
        f"{result.failures + result.errors} of {result.tests} tests failed" if result.tests else "the tests did not run")
    return Verdict(result.passed, "code_tests", detail, result.backend)


def verify(task: PackTask, outcome: Outcome, *, backend: str = "auto", timeout: float = 20.0) -> Verdict:
    """Check what a run produced against the task's verifier."""

    kind = task.verifier.kind
    if kind == "answer":
        return _check_answer(task, outcome.answer)
    if kind == "json_answer":
        return _check_json(task, outcome.answer)
    if kind == "file":
        return _check_file(task, outcome.files)
    if kind == "app_state":
        return _check_apps(task, outcome.apps)
    return _check_code(task, outcome.files, backend=backend, timeout=timeout)


# ── Playing a solution ──────────────────────────────────────────────────────


def workspace_files(folder: Path) -> dict[str, str]:
    """A folder's text files (task-relative, ``/`` separators); large files are skipped."""

    found: dict[str, str] = {}
    for item in sorted(Path(folder).rglob("*")):
        if not item.is_file() or item.is_symlink() or item.name.startswith("_row_bot_sandbox"):
            continue
        relative = item.relative_to(folder).as_posix()
        if "__pycache__" in relative or item.stat().st_size > MAX_FILE_BYTES:
            continue
        found[relative] = item.read_bytes().decode("utf-8", "replace")
    return found


def solve(task: PackTask, solution: Solution) -> Outcome:
    """What the task's workspace and apps look like after ``solution`` (no model involved)."""

    files = dict(task.files)
    files.update(solution.files)
    apps = FakeApps(task.apps)
    for call in solution.calls:
        apps.call(str(call["tool"]), dict(call.get("args") or {}))
    return Outcome(solution.answer, files, apps.state())


__all__ = ["Outcome", "Verdict", "normalize", "numbers_in", "parse_json", "solve", "verify", "workspace_files"]
