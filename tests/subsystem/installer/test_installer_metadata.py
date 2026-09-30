from __future__ import annotations

import pathlib

import pytest
import yaml


pytestmark = [pytest.mark.subsystem, pytest.mark.installer]


def test_client_build_never_runs_npm_install_scripts_or_a_shared_cache() -> None:
    action = yaml.load(pathlib.Path(".github/actions/build-client/action.yml").read_text(encoding="utf-8"),
                       Loader=yaml.BaseLoader)
    steps = action["runs"]["steps"]
    setup = next(step for step in steps if step.get("uses", "").startswith("actions/setup-node@"))
    assert setup["with"]["package-manager-cache"] == "false"
    install = next(step for step in steps if "npm ci" in step.get("run", ""))
    assert "--ignore-scripts" in install["run"]
