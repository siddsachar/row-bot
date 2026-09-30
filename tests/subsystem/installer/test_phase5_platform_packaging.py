from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from setuptools.errors import SetupError

from row_bot.access.store import AccessStore
from row_bot.application.settings_snapshot import _mobile_access
from row_bot.client_assets import AssetValidationError, load_client_assets
from row_bot.mobile.store import MobileAuthStore
from scripts.client_build import select_client_payload


pytestmark = [pytest.mark.subsystem, pytest.mark.installer]

PWA_SHELL = frozenset(
    {"app.webmanifest", "service-worker.js", "icon-192.png", "icon-512.png"}
)


def _payload(root: Path) -> Path:
    (root / "assets").mkdir(parents=True)
    (root / ".vite").mkdir()
    files = {
        "index.html": b'<script src="/app-v2/assets/index-abcdef12.js"></script>',
        "assets/index-abcdef12.js": b"export const fixture = true;",
        "app.webmanifest": b'{"name":"Row-Bot"}',
        "service-worker.js": b"self.addEventListener('fetch', () => {});",
        "icon-192.png": b"fixture 192 icon",
        "icon-512.png": b"fixture 512 icon",
    }
    for name, content in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    inventory = {
        "version": 1,
        "files": {
            name: {"size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
            for name, content in files.items()
        },
    }
    (root / "asset-manifest.json").write_text(json.dumps(inventory), encoding="utf-8")
    (root / ".vite/manifest.json").write_text(
        json.dumps(
            {
                "index.html": {
                    "isEntry": True,
                    "file": "assets/index-abcdef12.js",
                }
            }
        ),
        encoding="utf-8",
    )
    return root


@pytest.mark.parametrize("missing", sorted(PWA_SHELL))
def test_installed_client_payload_rejects_incomplete_pwa_shell(
    tmp_path: Path,
    missing: str,
) -> None:
    root = _payload(tmp_path / "client-v2")
    inventory_path = root / "asset-manifest.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    inventory["files"].pop(missing)
    inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
    (root / missing).unlink()

    with pytest.raises(SetupError, match="missing or invalid"):
        select_client_payload(root)
    with pytest.raises(AssetValidationError, match="client_build_unavailable"):
        load_client_assets(root)


@pytest.mark.parametrize("custom", [False, True], ids=["canonical", "custom"])
def test_current_and_compatibility_readers_share_physical_mobile_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    custom: bool,
) -> None:
    selected = tmp_path / ("custom-data" if custom else "canonical-data")
    # Keep both layout variants physically isolated from the real user-data
    # directory.  Resolving the paths after setting the environment also
    # avoids stale function bindings when aggregate tests reload data_paths.
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(selected))
    from row_bot import data_paths

    current = AccessStore(db_path=data_paths.get_access_db_path())
    compatibility = MobileAuthStore(db_path=data_paths.get_mobile_db_path())
    current.ensure_schema()

    expected = selected / "mobile.db"
    assert current.db_path == expected
    assert compatibility.db_path == expected
    assert data_paths.get_access_db_path() == expected
    assert data_paths.get_mobile_db_path() == expected
    assert _mobile_access(selected) == {
        "availability": "available",
        "active_devices": 0,
        "active_sessions": 0,
    }
