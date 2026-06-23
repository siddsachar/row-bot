from __future__ import annotations

import pathlib
import tomllib

import pytest


pytestmark = [pytest.mark.subsystem, pytest.mark.installer]


def test_project_entrypoint_points_to_launcher() -> None:
    data = tomllib.loads(pathlib.Path("pyproject.toml").read_text(encoding="utf-8"))

    assert data["project"]["scripts"]["row-bot"] == "row_bot.launcher:main"


def test_linux_installer_contract_preserves_user_data_and_verifies_dependencies() -> None:
    build_script = pathlib.Path("installer/build_linux_app.sh").read_text(encoding="utf-8")
    install_script = pathlib.Path("installer/install-linux.sh").read_text(encoding="utf-8")

    assert "scripts/verify_runtime_dependencies.py" in build_script
    assert "User data in ~/.row-bot was left untouched." in build_script
    assert "LAUNCH_CMD=\"row-bot\"" in install_script
    assert "mktemp -d" in install_script


def test_ci_declares_subsystem_and_smoke_lanes() -> None:
    ci = pathlib.Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "scripts/run_test_matrix.py contract-subsystem" in ci
    assert "scripts/run_test_matrix.py pr" in ci
    assert "scripts/run_test_matrix.py legacy-parity" not in ci
    assert "migrated-subsystem-coverage" in ci
    assert "scripts/smoke_app.py --port 8090 --timeout 120" in ci
