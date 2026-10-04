"""Cancelled or failed Hermes cache publication cleans only its own temp file."""
import json
import os
from pathlib import Path

import pytest

from row_bot.plugins import hermes_catalog, hermes_mcp

pytestmark = pytest.mark.platform


@pytest.mark.parametrize("module,filename", [(hermes_catalog, "hermes_catalog_cache.json"), (hermes_mcp, "hermes_mcp_catalog_cache.json")])
@pytest.mark.parametrize("failure", ["cancelled", "write", "replace"])
def test_failed_refresh_preserves_cache_and_cleans_unique_temporary(tmp_path, monkeypatch, module, filename, failure):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    path = tmp_path / filename
    saved = {"entries": [], "removed": [], "pin": "a" * 40, "names": ["old"], "fetched_at": 1}
    path.write_text(json.dumps(saved))
    before = path.read_bytes()
    unrelated = tmp_path / (filename + ".unrelated.tmp")
    unrelated.write_text("keep")
    def fetch(url, **kwargs):
        if url.endswith("commits/HEAD"): return json.dumps({"sha": "b" * 40}).encode()
        if "/git/trees/" in url: return json.dumps({"tree": [{"path": "optional-mcps/new/manifest.yaml"}]}).encode()
        return json.dumps({"entries": [], "removed": []}).encode()
    monkeypatch.setattr(module, "_public_bytes", fetch)
    fsync, replace = os.fsync, os.replace
    def write_temp(descriptor):
        if failure == "write":
            raise OSError("synthetic partial write failure")
        return fsync(descriptor)
    def replace_temp(source, target, *args, **kwargs):
        if failure == "replace" and Path(source).name.startswith(filename + "."):
            raise OSError("synthetic replace failure")
        return replace(source, target, *args, **kwargs)
    monkeypatch.setattr(os, "fsync", write_temp)
    monkeypatch.setattr(os, "replace", replace_temp)
    checks = []
    def cancelled():
        checks.append(True)
        return failure == "cancelled" and len(checks) == 2
    result = module.read_catalog(refresh=True, cancelled=cancelled)
    assert result["status"] == "stale"
    assert path.read_bytes() == before
    assert list(tmp_path.glob(filename + ".*.tmp")) == [unrelated]
    assert unrelated.read_text() == "keep"
