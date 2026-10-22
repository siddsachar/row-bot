"""O46: every Row-Bot install carries what Learning needs to download its training engine and
train: the engine's own locked project and entry (``learning_engine``: its ``pyproject.toml``,
``uv.lock``, ``entry.py`` and ``trainer.py``) and the training recipes. Read from the real
``pyproject.toml`` with setuptools' own file selection; nothing is built or written in the
checkout."""

from __future__ import annotations

from pathlib import Path

import pytest
from setuptools import Distribution
from setuptools.command.build_py import build_py
from setuptools.config.pyprojecttoml import apply_configuration

pytestmark = [pytest.mark.subsystem]

ROOT = Path(__file__).resolve().parents[3]


def _wheel_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> set[str]:
    monkeypatch.chdir(ROOT)
    distribution = Distribution()
    distribution.script_name = "setup.py"
    apply_configuration(distribution, str(ROOT / "pyproject.toml"))
    distribution.include_package_data = False
    distribution.command_options["egg_info"] = {"egg_base": ("test", str(tmp_path))}  # never in the checkout
    command = build_py(distribution)
    command.ensure_finalized()
    files = {f"{package}/{name}" for package, _source, _build, names in command.data_files for name in names}
    files |= {f"{package}/{module}.py" for package, module, _path in command.find_all_modules()}
    return files


def test_installs_carry_the_training_engines_project_and_the_recipes(tmp_path, monkeypatch):
    files = _wheel_files(tmp_path, monkeypatch)

    engine = {name for name in files if name.startswith("row_bot.learning_engine/")}
    assert {"row_bot.learning_engine/pyproject.toml", "row_bot.learning_engine/uv.lock",
            "row_bot.learning_engine/entry.py", "row_bot.learning_engine/trainer.py"} <= engine
    recipes = {name for name in files if name.startswith("row_bot.learning.training.recipes/")}
    assert {f"row_bot.learning.training.recipes/{tier}.json" for tier in ("8gb", "16gb", "24gb", "32gb")} <= recipes
