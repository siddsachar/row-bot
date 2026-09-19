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
