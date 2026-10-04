"""Bounded Registry fixtures: refresh, integrity and publication revalidation."""
import copy
import json
from pathlib import Path

import pytest

from row_bot.mcp_client import marketplace, registry_snapshot as snapshot

pytestmark = pytest.mark.platform


@pytest.fixture
def metadata():
    return json.loads((Path(__file__).parents[2] / "fixtures/integrations/registry-v01.json").read_text())


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    return tmp_path


def test_snapshot_refresh_is_bounded_integrity_checked_and_failure_preserves_cache(local, metadata, monkeypatch):
    calls = []
    def fetch(url):
        calls.append(url)
        return {**metadata, "metadata": {"nextCursor": str(len(calls))}}
    monkeypatch.setattr(marketplace, "_fetch_json", fetch)
    result = snapshot.refresh_snapshot()
    assert len(calls) == 5 and all("search=" not in url for url in calls)
    assert result["digest"] and result["captured_at"] and not result["complete"]
    saved = (local / "mcp_registry_snapshot.json").read_bytes()
    monkeypatch.setattr(marketplace, "_fetch_json", lambda *a: {"wrong": []})
    with pytest.raises(ValueError):
        snapshot.refresh_snapshot()
    assert (local / "mcp_registry_snapshot.json").read_bytes() == saved
    changed = json.loads(saved)
    changed["entries"][0]["name"] = "tampered"
    (local / "mcp_registry_snapshot.json").write_text(json.dumps(changed))
    assert all(e.name != "tampered" for e in snapshot.read_snapshot()["entries"])


def test_cancelled_refresh_never_writes(local, metadata, monkeypatch):
    cancelled = [False]
    def fetch(url):
        cancelled[0] = True
        return metadata
    monkeypatch.setattr(marketplace, "_fetch_json", fetch)
    with pytest.raises(ValueError, match="cancelled"):
        snapshot.refresh_snapshot(cancelled=lambda: cancelled[0])
    assert not (local / "mcp_registry_snapshot.json").exists()


def test_registry_recipe_revalidated_at_publication(local, metadata, monkeypatch):
    entry = marketplace.registry_entries(metadata)[0]
    cfg = marketplace.entry_to_server_config(entry)
    monkeypatch.setattr(marketplace, "_fetch_json", lambda *a: metadata["servers"][0])
    snapshot.revalidate_configuration(cfg)
    metadata["servers"][0]["_meta"]["io.modelcontextprotocol.registry/official"]["status"] = "deleted"
    with pytest.raises(ValueError, match="removed"):
        snapshot.revalidate_configuration(cfg)


def test_recipe_identity_keeps_deployments_distinct(local, metadata, monkeypatch):
    from row_bot.application import client_integrations
    second = copy.deepcopy(metadata["servers"][0])
    second["server"]["name"] = "org.other/notes"
    second["server"]["remotes"][0]["type"] = "sse"
    entries = marketplace.registry_entries({"servers": [metadata["servers"][0], second]})
    monkeypatch.setattr(snapshot, "read_snapshot", lambda: {"entries": entries, "status": "cached"})
    page = client_integrations.search_integrations(owner_id="fixture", sources=["official"])
    assert len(page["items"]) == 2
    assert page["items"][0]["canonical_identity"] != page["items"][1]["canonical_identity"]
