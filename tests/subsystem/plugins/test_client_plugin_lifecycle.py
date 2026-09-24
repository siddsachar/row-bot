"""Marketplace lifecycle uses explicit, duplicate-safe local-owner actions."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4
import zipfile

import pytest

from row_bot.application.client_platform import ClientPlatformError
from row_bot.application import client_plugin_lifecycle as owner
from row_bot.application import plugin_commands
from row_bot.plugins import installer, loader
from row_bot.plugins.marketplace import MarketplaceEntry
from tests.subsystem.client_protocol.test_empty_workspace_setup import (
    service, workspace_api,
)

# ruff: noqa: F401, F811 -- imported pytest fixtures are requested by name.


@pytest.fixture
def lifecycle(monkeypatch):
    item = {
        "plugin_id": "synthetic-plugin",
        "name": "Synthetic plugin",
        "version": "1.0.0",
        "description": "Synthetic test fixture",
        "installed": False,
        "update_version": None,
        "permissions": ["filesystem_read"],
    }
    entry = SimpleNamespace(
        id="synthetic-plugin", version="1.0.0",
        archive_url="https://example.test/plugin.zip",
        checksum="sha256:" + "a" * 64,
    )
    monkeypatch.setattr(plugin_commands, "_catalog", lambda validate: ([item], "rev"))
    monkeypatch.setattr(owner, "_entry", lambda plugin_id: entry)
    calls = []
    monkeypatch.setattr(installer, "install_plugin", lambda plugin_id, **kwargs: calls.append((plugin_id, kwargs)) or SimpleNamespace(success=True))
    monkeypatch.setattr(loader, "refresh_plugin_runtime", lambda _reason: None)
    records = {}

    def metadata(_owner, command_id):
        row = records.get(command_id)
        return {"target": row["target"], "type": row["wire"]["type"]} if row else None

    def claim(_owner, key, wire, target, **_kwargs):
        row = records.get(key)
        if row:
            if row["wire"] != wire:
                raise ValueError("idempotency_mismatch")
            return row["result"]
        records[key] = {"wire": wire, "target": target, "result": None}
        return None

    def complete(_owner, key, result):
        records[key]["result"] = result
        return result

    monkeypatch.setattr(owner.admissions, "read_command_metadata", metadata)
    monkeypatch.setattr(owner.admissions, "claim_command", claim)
    monkeypatch.setattr(owner.admissions, "complete_command", complete)
    return item, entry, calls


def test_install_is_explicit_and_replay_does_not_download(lifecycle):
    _item, entry, calls = lifecycle
    review = owner.review_plugin_lifecycle("install", entry.id, validate=lambda: None)
    assert calls == []
    assert review["source"] == entry.archive_url
    assert review["checksum"] == entry.checksum
    command = {
        "command_id": str(uuid4()), "action": "install",
        "plugin_id": entry.id, "revision": review["revision"],
    }
    receipt = owner.execute_plugin_lifecycle(command, owner_id="owner", validate=lambda: None)
    assert receipt["status"] == "completed"
    assert calls == [(entry.id, {
        "source": "marketplace", "source_ref": entry.archive_url,
        "source_dir": None,
        "archive_url": entry.archive_url, "expected_checksum": entry.checksum,
    })]
    assert owner.execute_plugin_lifecycle(command, owner_id="owner", validate=lambda: None) == receipt
    assert len(calls) == 1


def test_stale_review_and_unsafe_source_are_denied(lifecycle):
    _item, entry, calls = lifecycle
    with pytest.raises(ClientPlatformError, match="plugin_lifecycle_changed"):
        owner.execute_plugin_lifecycle(
            {"command_id": str(uuid4()), "action": "install", "plugin_id": entry.id, "revision": "0" * 64},
            owner_id="owner", validate=lambda: None,
        )
    entry.archive_url = "http://localhost/private.zip"
    with pytest.raises(ClientPlatformError, match="plugin_source_unavailable"):
        owner.review_plugin_lifecycle("install", entry.id, validate=lambda: None)
    assert calls == []


def test_local_directory_index_entry_is_bound_to_its_reviewed_tree(lifecycle, tmp_path, monkeypatch):
    _item, _entry, calls = lifecycle
    source = tmp_path / "plugin-source"
    source.mkdir()
    (source / "plugin.json").write_text('{"id":"synthetic-plugin"}', encoding="utf-8")
    entry = MarketplaceEntry(
        id="synthetic-plugin", name="Synthetic", version="1.0.0",
        description="Local synthetic plugin", path=str(source),
        index_source="local",
    )
    monkeypatch.setattr(owner, "_entry", lambda _id: entry)
    reviewed = owner.review_plugin_lifecycle("install", entry.id, validate=lambda: None)
    assert reviewed["source"] == "Local directory: plugin-source"
    assert reviewed["checksum"].startswith("sha256:")
    (source / "plugin.json").write_text('{"id":"synthetic-plugin","version":"2"}', encoding="utf-8")
    with pytest.raises(ClientPlatformError, match="plugin_lifecycle_changed"):
        owner.execute_plugin_lifecycle(
            {"command_id": str(uuid4()), "action": "install", "plugin_id": entry.id, "revision": reviewed["revision"]},
            owner_id="owner", validate=lambda: None,
        )
    assert calls == []


def test_archive_extraction_rejects_symlinks_and_oversize_members(tmp_path):
    archive = tmp_path / "plugin.zip"
    link = zipfile.ZipInfo("plugin/link")
    link.create_system = 3
    link.external_attr = 0o120777 << 16
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(link, "../outside")
    with zipfile.ZipFile(archive) as zf:
        with pytest.raises(ValueError, match="symbolic link"):
            installer._safe_extract_zip(zf, tmp_path / "extract")
    # Exercise the bounded metadata gate without allocating a large fixture.
    with zipfile.ZipFile(archive, "w") as zf:
        for index in range(2049):
            zf.writestr(f"plugin/file-{index}", b"")
    with zipfile.ZipFile(archive) as zf:
        with pytest.raises(ValueError, match="extraction limit"):
            installer._safe_extract_zip(zf, tmp_path / "many")


def test_lifecycle_api_requires_command_identity(workspace_api, monkeypatch):
    _, _, _, client, headers, _, _ = workspace_api
    review = {
        "action": "install", "plugin_id": "synthetic-plugin",
        "name": "Synthetic", "version": "1.0.0",
        "source": "https://example.test/plugin.zip", "checksum": "",
        "permissions": [], "disclosures": [], "revision": "a" * 64,
    }
    calls = []
    monkeypatch.setattr(owner, "review_plugin_lifecycle", lambda action, plugin_id, *, validate: review)

    def execute(command, *, owner_id, validate):
        validate()
        calls.append(command)
        return {
            "command_id": command["command_id"], "status": "completed",
            "action": command["action"], "plugin_id": command["plugin_id"],
            "message": "Installed synthetic plugin.",
        }

    monkeypatch.setattr(owner, "execute_plugin_lifecycle", execute)
    response = client.post(
        "/api/v1/settings/plugins/lifecycle/review", headers=headers,
        json={"action": "install", "plugin_id": "synthetic-plugin"},
    )
    assert response.status_code == 200, response.text
    command_id = str(uuid4())
    command = {
        "command_id": command_id,
        "client_session_id": headers["X-Client-Session"],
        "action": "install", "plugin_id": "synthetic-plugin", "revision": "a" * 64,
    }
    denied = client.post(
        "/api/v1/settings/plugins/lifecycle/commands", headers=headers, json=command
    )
    assert denied.status_code == 409
    assert not calls
    accepted = client.post(
        "/api/v1/settings/plugins/lifecycle/commands",
        headers={**headers, "idempotency-key": command_id}, json=command,
    )
    assert accepted.status_code == 200, accepted.text
    assert len(calls) == 1


def test_interrupted_lifecycle_receipt_does_not_claim_success(monkeypatch):
    command_id = str(uuid4())
    monkeypatch.setattr(owner.admissions, "read_command_metadata", lambda *_args: {
        "type": "plugin.lifecycle.install", "status": "admitting",
    })
    monkeypatch.setattr(owner.admissions, "read_command_receipt", lambda *_args: {
        "command_id": command_id, "status": "accepted",
        "action": "install", "plugin_id": "synthetic-plugin",
    })
    receipt = owner.read_plugin_lifecycle_receipt(
        command_id, owner_id="owner", validate=lambda: None
    )
    assert receipt["status"] == "uncertain"
    assert "unconfirmed" in receipt["message"]
