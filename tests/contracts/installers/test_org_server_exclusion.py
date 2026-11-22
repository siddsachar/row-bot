"""Desktop installers and update parts never carry the Org server (src/row_bot_org)."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import app_payload_manifest

pytestmark = [pytest.mark.contract, pytest.mark.installer]

REPO = Path(__file__).resolve().parents[3]


def test_a_staged_desktop_payload_has_no_org_server(tmp_path: Path) -> None:
    assert (REPO / "src" / "row_bot_org" / "__init__.py").is_file()

    # Windows stages this way, macOS and Linux copy the same manifest's folders, and the update
    # parts are cut from the staged app.
    stage = app_payload_manifest.stage_app_payload(REPO, tmp_path / "app")

    staged = [path.relative_to(stage).as_posix() for path in stage.rglob("*")]
    assert "src/row_bot/__init__.py" in staged
    assert not [path for path in staged if "row_bot_org" in path]
