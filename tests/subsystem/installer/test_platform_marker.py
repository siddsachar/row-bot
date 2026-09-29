"""Tests of OS-sensitive code carry the `platform` marker.

CI runs `pytest -m platform` on Windows and macOS for every pull request and
everything else on Linux only. A test module that imports one of the modules
below (process trees, secret storage, launcher and updater, packaged plugin and
MCP runtimes, descriptor-backed document storage) without the marker would
never run on the operating systems where that code behaves differently.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = [pytest.mark.subsystem, pytest.mark.installer]

TESTS = Path(__file__).resolve().parents[2]
PLATFORM_SENSITIVE = frozenset({
    "row_bot.developer.client_processes",
    "row_bot.developer.process_worker",
    "row_bot.document_jobs",
    "row_bot.file_ownership",
    "row_bot.launcher",
    "row_bot.mcp_client.requirements",
    "row_bot.native_client",
    "row_bot.plugins.sandbox",
    "row_bot.plugins.worker",
    "row_bot.process_cancellation",
    "row_bot.secret_store",
    "row_bot.terminal_pty",
    "row_bot.tools.shell_tool",
    "row_bot.update_handoff",
    "row_bot.updater",
})
# Not app lanes: docs tooling runs in docs.yml, e2e is opt-in, browser holds harnesses.
OUTSIDE_APP_LANES = ("docs", "marketing", "e2e", "browser")


def _imported_modules(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def _carries_platform_marker(tree: ast.Module) -> bool:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "pytestmark" for target in node.targets
        ):
            return any(
                isinstance(mark, ast.Attribute) and mark.attr == "platform"
                and isinstance(mark.value, ast.Attribute) and mark.value.attr == "mark"
                for mark in ast.walk(node.value)
            )
    return False


def _app_lane_test_modules() -> list[Path]:
    return [
        path for path in sorted(TESTS.rglob("test_*.py"))
        if path.relative_to(TESTS).parts[0] not in OUTSIDE_APP_LANES
    ]


def test_tests_of_platform_sensitive_modules_are_marked_platform() -> None:
    unmarked = []
    leaf_names = {name.rsplit(".", 1)[1] for name in PLATFORM_SENSITIVE}
    for path in _app_lane_test_modules():
        source = path.read_text(encoding="utf-8")
        if not any(name in source for name in leaf_names):
            continue
        tree = ast.parse(source)
        sensitive = sorted(_imported_modules(tree) & PLATFORM_SENSITIVE)
        if sensitive and not _carries_platform_marker(tree):
            unmarked.append(f"{path.relative_to(TESTS).as_posix()} ({', '.join(sensitive)})")

    assert not unmarked, (
        "Add pytest.mark.platform to the module's pytestmark so these run on Windows "
        "and macOS too:\n" + "\n".join(unmarked)
    )
