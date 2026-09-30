"""Marketplace lifecycle uses explicit, duplicate-safe local-owner actions."""

from __future__ import annotations

import json
import logging
import shutil
import time
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
from tests.subsystem.plugins.conftest import manifest_payload, write_plugin

# ruff: noqa: F401, F811 -- imported pytest fixtures are requested by name.


def _valid() -> None:
    return None


def _fake_admissions(monkeypatch) -> dict:
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
    return records


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
    entry = MarketplaceEntry(
        id="synthetic-plugin", name="Synthetic plugin", version="1.0.0",
        description="Synthetic test fixture",
        archive_url="https://example.test/plugin.zip",
        checksum="sha256:" + "a" * 64,
    )
    monkeypatch.setattr(plugin_commands, "_catalog", lambda validate: ([item], "rev"))
    monkeypatch.setattr(owner, "_entry", lambda plugin_id: entry)
    calls = []
    monkeypatch.setattr(installer, "install_plugin", lambda plugin_id, **kwargs: calls.append((plugin_id, kwargs)) or SimpleNamespace(success=True))
    monkeypatch.setattr(loader, "refresh_plugin_runtime", lambda _reason: None)
    _fake_admissions(monkeypatch)
    return item, entry, calls


PLUGIN = "sample-plugin"
REPOSITORY = "https://github.com/example-owner/row-bot-plugins"
ARCHIVE = f"{REPOSITORY}/archive/refs/heads/main.zip"


@pytest.fixture
def remote_marketplace(plugin_modules, tmp_path, monkeypatch):
    """A remote marketplace in the real index shape (an https GitHub source,
    each entry a relative ``path``, a sha256 checksum and no ``archive_url``),
    its repository archive served from a local zip: nothing touches the network."""
    installer_module, marketplace = plugin_modules["installer"], plugin_modules["marketplace"]
    devtools = plugin_modules["devtools"]
    archive = tmp_path / "repository.zip"
    downloads: list[str] = []

    def download(ref, dest):
        downloads.append(ref)
        shutil.copyfile(archive, dest)

    monkeypatch.setattr(installer_module, "_download_to_file", download)
    monkeypatch.setattr(loader, "refresh_plugin_runtime", lambda _reason: [])
    monkeypatch.setattr(plugin_commands, "environment_needed", lambda _plugin_id: False)
    _fake_admissions(monkeypatch)

    def publish(version: str = "1.0.0", *, checksum: str | None = None) -> str:
        tree = tmp_path / f"repository-{version}"
        source = write_plugin(tree / "plugins", PLUGIN, manifest=manifest_payload(PLUGIN, version=version))
        # Another folder with the same plugin id: only the entry's path installs.
        decoy = write_plugin(tree / "examples", PLUGIN, manifest=manifest_payload(PLUGIN, version="0.0.1"))
        with zipfile.ZipFile(archive, "w") as zf:
            for folder in (source, decoy):
                for path in sorted(folder.rglob("*")):
                    if path.is_file():
                        zf.write(path, f"row-bot-plugins-main/{path.relative_to(tree).as_posix()}")
        digest = devtools.compute_plugin_checksum(source) if checksum is None else checksum
        marketplace._write_disk_cache({
            "schema_version": 2, "generated": "", "source": REPOSITORY,
            "plugins": [{
                "id": PLUGIN, "name": "Sample Plugin", "version": version,
                "description": "A deterministic test plugin.",
                "author": {"name": "Tester", "github": "tester"}, "tags": [],
                "path": f"plugins/{PLUGIN}", "archive_url": "", "checksum": digest,
                "provides": {"native_tools": 1, "mcp_servers": 0, "channels": 0, "skills": 0},
                "permissions": ["network"], "min_row_bot_version": "0.0.0", "changelog_url": "",
            }],
            "_fetched_at": time.time(),
        })
        marketplace._reset()
        return digest

    return SimpleNamespace(publish=publish, downloads=downloads, installer=installer_module)


def _run(action: str, plugin_id: str = PLUGIN) -> tuple[dict, dict]:
    review = owner.review_plugin_lifecycle(action, plugin_id, validate=_valid)
    receipt = owner.execute_plugin_lifecycle(
        {"command_id": str(uuid4()), "action": action, "plugin_id": plugin_id, "revision": review["revision"]},
        owner_id="owner", validate=_valid,
    )
    return review, receipt


def _installed_version(market) -> str:
    manifest = market.installer.PLUGINS_DIR / PLUGIN / "plugin.json"
    return json.loads(manifest.read_text(encoding="utf-8"))["version"]


def test_remote_index_entry_installs_its_folder_from_the_repository_archive(remote_marketplace):
    """B266: every install from the user's marketplace failed because the
    entry's relative path was treated as a local folder. It is the entry's
    folder in the index repository's archive, checked against its checksum."""
    market = remote_marketplace
    checksum = market.publish()
    review, receipt = _run("install")
    assert review["source"].startswith(ARCHIVE) and f"plugins/{PLUGIN}" in review["source"]
    assert review["checksum"] == checksum
    assert "checked against the displayed checksum" in " ".join(review["disclosures"])
    assert receipt["status"] == "completed", receipt
    assert market.downloads == [ARCHIVE]
    assert _installed_version(market) == "1.0.0"


def test_remote_entry_ignores_a_same_named_folder_in_the_working_folder(
    remote_marketplace, tmp_path, monkeypatch,
):
    market = remote_marketplace
    market.publish()
    decoy = tmp_path / "working-folder"
    write_plugin(decoy / "plugins", PLUGIN, manifest=manifest_payload(PLUGIN, version="9.9.9"))
    monkeypatch.chdir(decoy)
    review, receipt = _run("install")
    assert "Local directory" not in review["source"]
    assert receipt["status"] == "completed", receipt
    assert market.downloads == [ARCHIVE]
    assert _installed_version(market) == "1.0.0"


def test_remote_entry_whose_files_do_not_match_its_checksum_is_refused(remote_marketplace):
    market = remote_marketplace
    market.publish(checksum="sha256:" + "0" * 64)
    _review, receipt = _run("install")
    assert receipt["status"] == "failed"
    # The installer's own reason, and a next step that isn't "try again" blindly.
    assert "Checksum mismatch" in receipt["message"] and "Refresh the marketplace" in receipt["message"]
    assert not (market.installer.PLUGINS_DIR / PLUGIN).exists()


def test_remote_entry_without_a_checksum_is_never_downloaded(remote_marketplace, tmp_path, caplog):
    market = remote_marketplace
    market.publish(checksum="")
    item = plugin_commands.read_plugin_catalog(validate=_valid)["items"][0]
    assert item["capabilities"]["install"] == {"available": False, "code": "plugin_checksum_unavailable"}
    with caplog.at_level(logging.WARNING, logger=owner.__name__):
        with pytest.raises(ClientPlatformError, match="plugin_checksum_unavailable"):
            owner.review_plugin_lifecycle("install", PLUGIN, validate=_valid)
    # The refusal is logged by its code, without local paths.
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "plugin_checksum_unavailable" in logged and str(tmp_path) not in logged
    assert market.downloads == []


def test_update_takes_the_same_archive_and_checksum_path(remote_marketplace):
    market = remote_marketplace
    market.publish("1.0.0")
    assert _run("install")[1]["status"] == "completed"
    checksum = market.publish("2.0.0")
    item = plugin_commands.read_plugin_catalog(validate=_valid)["items"][0]
    assert item["update_version"] == "2.0.0" and item["capabilities"]["update"]["available"] is True
    review, receipt = _run("update")
    assert review["checksum"] == checksum and review["source"].startswith(ARCHIVE)
    assert receipt["status"] == "completed", receipt
    assert market.downloads == [ARCHIVE, ARCHIVE]
    assert _installed_version(market) == "2.0.0"


def test_marketplace_labels_say_where_plugin_code_comes_from(remote_marketplace, plugin_modules, tmp_path):
    market = remote_marketplace
    market.publish()
    item = plugin_commands.read_plugin_catalog(validate=_valid)["items"][0]
    assert item["source_label"] == "github.com/example-owner/row-bot-plugins"
    # Only an index that is itself a local folder has local directories.
    local_index = tmp_path / "local-index"
    write_plugin(local_index / "plugins", PLUGIN)
    plugin_modules["marketplace"]._write_disk_cache({
        "source": local_index.as_uri(),
        "plugins": [{"id": PLUGIN, "name": "Sample Plugin", "version": "1.0.0", "path": f"plugins/{PLUGIN}"}],
    })
    item = plugin_commands.read_plugin_catalog(validate=_valid)["items"][0]
    assert item["source_label"] == "local directory"
    assert item["capabilities"]["install"]["available"] is True


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
        "archive_url": entry.archive_url, "archive_path": "",
        "expected_checksum": entry.checksum,
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
    # A plain-http archive is refused for good, not "couldn't be reached".
    entry.archive_url = "http://localhost/private.zip"
    with pytest.raises(ClientPlatformError, match="plugin_source_unsupported"):
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
        checksum="sha256:" + "b" * 64, index_source="local",
    )
    monkeypatch.setattr(owner, "_entry", lambda _id: entry)
    reviewed = owner.review_plugin_lifecycle("install", entry.id, validate=lambda: None)
    assert reviewed["source"] == "Local directory: plugin-source"
    # B208: the index checksum is shown and checked, as for a download.
    assert reviewed["checksum"] == entry.checksum
    owner.execute_plugin_lifecycle(
        {"command_id": str(uuid4()), "action": "install", "plugin_id": entry.id, "revision": reviewed["revision"]},
        owner_id="owner", validate=lambda: None,
    )
    assert calls[0][1]["source_dir"] == source.resolve()
    assert calls[0][1]["expected_checksum"] == entry.checksum
    (source / "plugin.json").write_text('{"id":"synthetic-plugin","version":"2"}', encoding="utf-8")
    with pytest.raises(ClientPlatformError, match="plugin_lifecycle_changed"):
        owner.execute_plugin_lifecycle(
            {"command_id": str(uuid4()), "action": "install", "plugin_id": entry.id, "revision": reviewed["revision"]},
            owner_id="owner", validate=lambda: None,
        )
    assert len(calls) == 1


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


@pytest.mark.parametrize("code", [
    "plugin_checksum_unavailable", "plugin_source_unsupported",
    # Refusals the review raises for the plugin's own state (B273).
    "plugin_already_installed", "plugin_not_installed", "plugin_update_unavailable",
])
def test_an_entry_that_cannot_install_is_refused_with_its_own_code(workspace_api, monkeypatch, code):
    _, _, _, client, headers, _, _ = workspace_api

    def refuse(action, plugin_id, *, validate):
        raise ClientPlatformError(code)

    monkeypatch.setattr(owner, "review_plugin_lifecycle", refuse)
    response = client.post(
        "/api/v1/settings/plugins/lifecycle/review", headers=headers,
        json={"action": "install", "plugin_id": "synthetic-plugin"},
    )
    assert response.status_code == 409, response.text
    assert response.json()["code"] == code and response.json()["retryable"] is False


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


def test_install_review_says_everything_the_person_confirms(lifecycle):
    """B144: the review's disclosures were never shown and install ran right
    after review. React now shows them before Install; the review says where
    the code comes from, whether it is pinned, and that a worker plugin gets
    its own environment."""
    _item, entry, calls = lifecycle
    review = owner.review_plugin_lifecycle("install", entry.id, validate=lambda: None)
    assert review["source"] == entry.archive_url and review["permissions"] == ["filesystem_read"]
    text = " ".join(review["disclosures"])
    assert "kept disabled" in text and "checked against the displayed checksum" in text
    assert "private Python environment" in text
    assert calls == []
