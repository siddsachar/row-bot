from __future__ import annotations

from importlib import resources
from pathlib import Path


def test_computer_use_runtime_manifest_is_package_data_and_installer_source_is_recursive() -> None:
    manifest = resources.files("row_bot.computer_use").joinpath("cua_runtime_manifest.json")
    assert manifest.is_file()
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    installer = Path("installer/row_bot_setup.iss").read_text(encoding="utf-8")
    assert '"row_bot.computer_use" = ["*.json"]' in pyproject
    assert 'Source: "..\\src\\row_bot\\*"' in installer
    assert "recursesubdirs" in installer
