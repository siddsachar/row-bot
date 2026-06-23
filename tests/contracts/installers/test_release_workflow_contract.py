from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = [pytest.mark.contract, pytest.mark.installer]


def test_ci_uses_matrix_runner_for_subsystem_lane() -> None:
    ci = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "scripts/run_test_matrix.py contract-subsystem" in ci


def test_release_workflow_has_manual_trigger_and_installer_jobs() -> None:
    release = Path(".github/workflows/release.yml").read_text(encoding="utf-8")

    assert "workflow_dispatch" in release
    assert "scripts/run_test_matrix.py release" in release
    assert "build-windows" in release
    assert "build-linux" in release
    assert "build-macos" in release
    assert "checksums-and-manifest" in release
    assert "scripts/smoke_app.py" in release


def test_release_workflow_does_not_call_legacy_scripts_directly() -> None:
    release = Path(".github/workflows/release.yml").read_text(encoding="utf-8")

    assert "tests/test_suite.py" not in release
    assert "tests/integration_tests.py" not in release
    assert "tests/test_memory_e2e.py" not in release


def test_live_e2e_workflow_is_manual_and_opt_in() -> None:
    live = Path(".github/workflows/live-e2e.yml").read_text(encoding="utf-8")

    assert "workflow_dispatch" in live
    assert "run_marked_live_tests" in live
    assert "run_real_mcp" in live
    assert "ROW_BOT_MCP_REAL_WORLD_E2E" in live
    assert 'live_provider or e2e' in live
