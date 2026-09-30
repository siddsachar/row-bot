from __future__ import annotations

from pathlib import Path

import pytest
import yaml


pytestmark = [pytest.mark.contract, pytest.mark.installer]


def test_release_builds_only_a_commit_the_gate_passed() -> None:
    workflow = yaml.load(Path(".github/workflows/release.yml").read_text(encoding="utf-8"),
                         Loader=yaml.BaseLoader)
    jobs = workflow["jobs"]

    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert "scripts/release_gate.py" in str(jobs["release-gate"]["steps"])
    assert jobs["nightly"]["needs"] == "release-gate"
    assert jobs["nightly"]["if"] == "${{ needs.release-gate.outputs.nightly_green != 'true' }}"
    for name in ("build-windows", "build-linux", "build-macos"):
        condition = jobs[name]["if"]
        assert jobs[name]["needs"] == ["release-gate", "nightly"]
        assert "needs.release-gate.result == 'success'" in condition
        assert "(needs.nightly.result == 'success' || needs.nightly.result == 'skipped')" in condition


def test_live_e2e_workflow_is_manual_and_opt_in() -> None:
    live = Path(".github/workflows/live-e2e.yml").read_text(encoding="utf-8")

    assert "workflow_dispatch" in live
    assert "run_marked_live_tests" in live
    assert "run_real_mcp" in live
    assert "ROW_BOT_MCP_REAL_WORLD_E2E" in live
    assert 'live_provider or e2e' in live
