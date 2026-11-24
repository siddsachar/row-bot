from __future__ import annotations

from pathlib import Path

import pytest

from tests.browser.client_workspace import run_browser


pytestmark = pytest.mark.subsystem


def test_fixture_build_is_isolated_and_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[tuple[str, ...], Path, dict[str, str], bool]] = []
    environment = {"PATH": "documented-node-path", "PRIVATE": "retained"}
    destination = tmp_path / "fixture-assets"
    monkeypatch.setattr(
        run_browser.shutil,
        "which",
        lambda name, *, path: "C:/documented/node.exe"
        if name == "node" and path == environment["PATH"]
        else None,
    )
    monkeypatch.setattr(
        run_browser.subprocess,
        "run",
        lambda command, *, cwd, env, check: calls.append(
            (tuple(command), cwd, dict(env), check)
        ),
    )

    run_browser._build_fixture_assets(destination, environment)

    assert destination.is_dir()
    assert len(calls) == 2
    assert calls[0][0][-3:] == ("--outDir", str(destination), "--emptyOutDir")
    assert calls[1][0][-1] == str(destination)
    assert all(call[1] == run_browser.ROOT / "frontend" for call in calls)
    assert all(call[2]["VITE_ENABLE_FIXTURES"] == "1" for call in calls)
    assert all(call[3] is True for call in calls)
    assert "VITE_ENABLE_FIXTURES" not in environment


def test_a_failed_runs_summary_names_the_tests_that_did_not_pass() -> None:
    from tests.browser.client_foundation.run_browser import failure_summary

    log = "\n".join([
        "Running 3 tests using 1 worker",
        "  ✓  1 [chromium-desktop] › tests/browser/a.spec.ts:5:1 › opens (2.1s)",
        "  ✘  2 [chromium-desktop] › tests/browser/wake-word.spec.ts:102:1 › listens (9.0s)",
        "  Error: Unexplained console errors",
        "",
        "  1 failed",
        "    [chromium-desktop] › tests/browser/wake-word.spec.ts:102:1 › listens",
        "  1 flaky",
        "    [chromium-desktop] › tests/browser/b.spec.ts:7:1 › saves",
        "  1 passed (21.3s)",
    ])

    summary = failure_summary(log)

    assert summary[0] == "  1 failed"
    assert "    [chromium-desktop] › tests/browser/wake-word.spec.ts:102:1 › listens" in summary
    assert summary[-1] == "  1 passed (21.3s)"


def test_a_run_that_died_without_a_summary_shows_its_last_lines() -> None:
    from tests.browser.client_foundation.run_browser import failure_summary

    log = "\n".join(f"line {n}" for n in range(100))

    assert failure_summary(log) == [f"line {n}" for n in range(70, 100)]
