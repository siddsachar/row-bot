from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tomllib

import pytest
from setuptools.errors import SetupError
import yaml

from row_bot.access.store import AccessStore
from row_bot.application.settings_snapshot import _mobile_access
from row_bot.client_assets import AssetValidationError, load_client_assets
from row_bot.mobile.store import MobileAuthStore
from scripts.client_build import select_client_payload


pytestmark = [pytest.mark.subsystem, pytest.mark.installer]

ROOT = Path(__file__).resolve().parents[3]
PWA_SHELL = frozenset(
    {"app.webmanifest", "service-worker.js", "icon-192.png", "icon-512.png"}
)


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


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


def test_all_installers_stage_and_verify_the_complete_pwa_and_native_payload() -> None:
    project = tomllib.loads(_source("pyproject.toml"))
    package_data = set(project["tool"]["setuptools"]["package-data"]["row_bot"])
    assert {f"static/client-v2/{name}" for name in PWA_SHELL} <= package_data
    runtime_data = set(
        project["tool"]["setuptools"]["package-data"]["row_bot.designer.runtime"]
    )
    assert {"runtime_bridge.js", "runtime_bridge.css"} <= runtime_data

    generator = _source("frontend/scripts/asset-manifest.mjs")
    for name in PWA_SHELL:
        assert f"'{name}'" in generator

    windows = _source("installer/row_bot_setup.iss")
    assert 'Source: "{#ClientAssetDir}\\*"' in windows
    assert 'Source: "..\\src\\row_bot\\*"' in windows
    for native_module in ("native_client.py", "terminal_bridge.py", "terminal_pty.py"):
        assert native_module not in windows.split("Excludes:", 1)[1].splitlines()[0]

    for script_name in ("installer/build_linux_app.sh", "installer/build_mac_app.sh"):
        script = _source(script_name)
        assert '--package-dir "$CLIENT_STAGE"' in script
        assert '--root "$CLIENT_STAGE" --compare "$CLIENT_BUILD" --strict' in script

    docker = _source("deploy/docker/Dockerfile")
    assert "node scripts/asset-manifest.mjs dist --package-dir /client-payload" in docker
    assert "COPY --from=client-build /client-payload ./src/row_bot/static/client-v2" in docker

    installer_verify = _source(".github/workflows/installer-verify.yml")
    assert installer_verify.count("verify_client_assets.py") == 4
    assert installer_verify.count("--strict") == 4


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


def test_docker_data_root_keeps_access_database_and_client_payload_persistent() -> None:
    compose = yaml.safe_load(_source("deploy/docker/compose.yaml"))
    service = compose["services"]["row-bot"]

    assert service["environment"]["ROW_BOT_DATA_DIR"] == "/data"
    assert "row_bot_data:/data" in service["volumes"]
    assert "row_bot_data" in compose["volumes"]

    dockerfile = _source("deploy/docker/Dockerfile")
    assert "ROW_BOT_DATA_DIR=/data" in dockerfile
    assert 'VOLUME ["/data"]' in dockerfile
    assert "./src/row_bot/static/client-v2" in dockerfile

    smoke = _source("scripts/smoke_docker_server.py")
    assert "sqlite3.connect('/data/mobile.db')" in smoke
    assert "access_sessions" in smoke


def test_release_signing_and_checksum_authority_stays_partitioned() -> None:
    workflow = yaml.load(
        _source(".github/workflows/release.yml"),
        Loader=yaml.BaseLoader,
    )
    jobs = workflow["jobs"]
    windows = str(jobs["build-windows"])
    macos = str(jobs["build-macos"])
    checksums = jobs["checksums-and-manifest"]
    checksum_source = str(checksums)

    assert "secrets." not in windows
    assert "signtool" not in windows.lower()
    assert "APPLE_APPLICATION_P12" in macos
    assert "APPLE_INSTALLER_P12" in macos

    assert checksums["needs"] == ["build-windows", "build-linux", "build-macos"]
    assert checksums["if"] == "${{ always() && !cancelled() }}"
    assert "find release-artifacts -type f -print0" in checksum_source
    assert "sort -z" in checksum_source
    assert "xargs -0 sha256sum" in checksum_source
    assert "secrets." not in checksum_source
