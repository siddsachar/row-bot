from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.subsystem
ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("scenario", ["foreign", "malformed", "parent", "published", "declarative"])
def test_runtime_accepts_only_bounded_parent_control(scenario: str):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the deterministic Designer runtime behavior test")
    result = subprocess.run(
        [node, str(ROOT / "tests/fixtures/designer_runtime_bridge_security.cjs"), scenario,
         str(ROOT / "src/row_bot/designer/runtime/runtime_bridge.js")],
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == f"{scenario}: passed"
