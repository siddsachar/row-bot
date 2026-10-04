"""Integrity-pinned preparation, with synthetic archives and no npm execution."""
import base64
import hashlib
import io
import json
import tarfile

import pytest

from row_bot.integrations.safe import TtlCache
from row_bot.mcp_client import packages
from row_bot.plugins import hermes_mcp

pytestmark = pytest.mark.platform


def archive(files):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as output:
        for name, value in files.items():
            data = value.encode()
            info = tarfile.TarInfo("package/" + name)
            info.size = len(data)
            output.addfile(info, io.BytesIO(data))
    return stream.getvalue()


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(packages, "_PREVIEWS", TtlCache(1200, 32, full="mcp_package_preview_capacity"))
    cfg = {"transport": "stdio", "command": "npx", "args": ["-y", "fixture-mcp@1.0.0"], "environment_mode": "minimal"}
    def serve(extra=None, corrupt=False):
        manifest = {"name": "fixture-mcp", "version": "1.0.0", "license": "MIT", "bin": "main.js", **(extra or {})}
        raw = archive({"package.json": json.dumps(manifest), "main.js": "process.exit(0)"})
        integrity = "sha512-" + base64.b64encode(hashlib.sha512(raw).digest()).decode()
        metadata = {**manifest, "dist": {"tarball": "https://registry.npmjs.org/fixture-mcp/-/fixture-mcp-1.0.0.tgz", "integrity": integrity}}
        calls = []
        def fetch(url, **_):
            calls.append(url)
            return (raw + b"changed" if corrupt else raw) if url.endswith(".tgz") else json.dumps(metadata).encode()
        monkeypatch.setattr(packages, "_fetch", fetch)
        return calls
    return cfg, serve


def test_prepare_binds_exact_bytes_connection_and_owner(source):
    cfg, serve = source
    calls = serve()
    reviewed = packages.inspect("owner", cfg)
    assert len(calls) == 2 and reviewed["dependencies"] == 0
    launch = packages.reviewed_launch("owner", reviewed["preview_id"], cfg, reviewed["digest"])
    assert launch["version"] == "1.0.0" and launch["integrity"].startswith("sha512-")
    with pytest.raises(ValueError, match="preview_expired"):
        packages.reviewed_launch("other", reviewed["preview_id"], cfg, reviewed["digest"])
    with pytest.raises(ValueError, match="integrity_changed"):
        packages.reviewed_launch("owner", reviewed["preview_id"], {**cfg, "args": ["other"]}, reviewed["digest"])
    assert len(calls) == 2  # Acceptance never downloads again.


@pytest.mark.parametrize("extra,corrupt,reason", [
    ({"scripts": {"postinstall": "arbitrary-bootstrap"}}, False, "install_scripts_unsupported"),
    ({"dependencies": {"unpinned": "*"}}, False, "locked_dependencies_required"),
    ({}, True, "integrity_changed"),
])
def test_preparation_refuses_unreviewed_effects(source, extra, corrupt, reason):
    cfg, serve = source
    serve(extra, corrupt)
    with pytest.raises(ValueError, match=reason):
        packages.inspect("owner", cfg)


def test_launch_never_uses_npx_without_explicit_preparation(source):
    cfg, _ = source
    with pytest.raises(ValueError, match="preparation_required"):
        packages.resolve_launch(cfg)


def test_hermes_recipe_maps_https_and_refuses_bootstrap():
    recipe = {"manifest_version": 1, "name": "fixture", "transport": {"type": "http", "url": "https://example.test/mcp"}, "auth": {"type": "oauth"}}
    result = hermes_mcp.normalize_recipe(recipe, name="fixture", pin="a" * 40, source_url="https://example.test/manifest")
    cfg = json.loads(result["import_json"])["mcpServers"]["fixture"]
    assert not cfg["enabled"] and result["requires_auth"] and cfg["source"]["pin"] == "a" * 40
    with pytest.raises(ValueError, match="recipe_unsupported"):
        hermes_mcp.normalize_recipe({**recipe, "install": {"bootstrap": ["run-me"]}}, name="fixture", pin="a" * 40, source_url="")
