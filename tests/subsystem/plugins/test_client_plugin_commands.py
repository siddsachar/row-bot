"""Client plugin settings remain passive, reviewed, and path/secret free."""

from __future__ import annotations

from copy import deepcopy
import json
import os
from uuid import UUID

import pytest

from row_bot.application import plugin_commands as commands
from tests.subsystem.plugins.conftest import manifest_payload, write_plugin

pytestmark = pytest.mark.subsystem


def _valid() -> None:
    return None


def _installed(plugin_modules, *, plugin_id: str = "sample-plugin", manifest=None):
    installer = plugin_modules["installer"]
    plugin_dir = write_plugin(installer.PLUGINS_DIR, plugin_id, manifest=manifest)
    plugin_modules["state"].mark_plugin_installed(plugin_id, version="1.0.0")
    return plugin_dir


def test_missing_store_is_passive_and_does_not_create_it(tmp_path, monkeypatch):
    target = tmp_path / "absent"
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(target))
    page = commands.read_plugin_catalog(validate=_valid)
    assert page == {
        "schema_version": 1,
        "revision": commands._revision([]),
        "availability": "available",
        "items": [],
        "total": 0,
        "next_cursor": None,
    }
    assert not target.exists()


def test_reads_installed_and_cached_marketplace_without_paths_or_secrets(
    plugin_modules,
):
    manifest = manifest_payload(
        settings={
            "region": {"type": "select", "options": ["eu", "us"], "required": True},
            "workspace": {"type": "local_path", "required": True},
        },
        secrets={"token": {"type": "secret", "required": True}},
        permissions=["network", "files"],
    )
    _installed(plugin_modules, manifest=manifest)
    state = plugin_modules["state"]
    state.set_plugin_config("sample-plugin", "region", "eu")
    state.set_plugin_config("sample-plugin", "workspace", "D:/private/work")
    state.set_plugin_secret("sample-plugin", "token", "super-secret-value")
    state.set_plugin_health_result(
        "sample-plugin", ok=True, checks=[{"label": "Required setup", "status": "ok"}]
    )
    (state.DATA_DIR / "marketplace_cache.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "plugins": [
                    {
                        "id": "sample-plugin",
                        "name": "Sample Plugin",
                        "version": "1.2.0",
                    },
                    {
                        "id": "cached-plugin",
                        "name": "Cached Plugin",
                        "version": "2.0.0",
                        "description": "Offline cached entry",
                        "verified": True,
                        "permissions": ["network"],
                        "provides": {"skills": 2},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    page = commands.read_plugin_catalog(validate=_valid)
    assert [item["plugin_id"] for item in page["items"]] == [
        "cached-plugin",
        "sample-plugin",
    ]
    installed = page["items"][1]
    assert installed["update_version"] == "1.2.0"
    assert installed["setup_complete"] is True
    assert installed["health"] == "passed"
    assert installed["capabilities"]["enable"]["available"] is True
    assert installed["capabilities"]["install"] == {
        "available": False,
        "code": "plugin_source_unavailable",
    }
    assert installed["capabilities"]["update"]["available"] is True
    assert installed["capabilities"]["remove"]["available"] is True
    assert page["items"][0]["capabilities"]["install"]["available"] is True
    detail = commands.read_plugin_detail("sample-plugin", validate=_valid)
    region = next(item for item in detail["settings"] if item["name"] == "region")
    assert region["configured"] is True and region["value"] is None
    workspace = next(item for item in detail["settings"] if item["name"] == "workspace")
    token = detail["secrets"][0]
    assert workspace == {**workspace, "value": None, "configured": True}
    assert token["value"] is None and token["configured"] is True
    public = json.dumps({"page": page, "detail": detail})
    assert "super-secret-value" not in public
    assert "D:/private/work" not in public
    assert "source_ref" not in public and "installed_plugins" not in public


def test_catalog_is_bounded_and_cursor_is_revision_bound(plugin_modules):
    for index in range(3):
        plugin_id = f"sample-{index}"
        _installed(
            plugin_modules, plugin_id=plugin_id, manifest=manifest_payload(plugin_id)
        )
    first = commands.read_plugin_catalog(limit=2, validate=_valid)
    assert len(first["items"]) == 2 and first["next_cursor"]
    second = commands.read_plugin_catalog(
        cursor=first["next_cursor"], limit=2, validate=_valid
    )
    assert len(second["items"]) == 1
    plugin_modules["state"].set_plugin_enabled("sample-0", True)
    with pytest.raises(commands.PluginCommandError, match="cursor_expired"):
        commands.read_plugin_catalog(
            cursor=first["next_cursor"], limit=2, validate=_valid
        )


def test_linked_or_hardlinked_manifest_fails_closed(plugin_modules, tmp_path):
    plugin_dir = _installed(plugin_modules)
    manifest = plugin_dir / "plugin.json"
    second = tmp_path / "manifest-copy.json"
    try:
        os.link(manifest, second)
    except OSError:
        pytest.skip("Hard links are unavailable on this filesystem")
    with pytest.raises(commands.PluginCommandError, match="plugin_catalog_unavailable"):
        commands.read_plugin_catalog(validate=_valid)


def test_review_rejects_unsupported_lifecycle_and_requires_saved_health(
    plugin_modules, monkeypatch
):
    _installed(plugin_modules)
    monkeypatch.setattr(
        commands.admissions, "keyed_digest", lambda value: commands._revision(value)
    )
    detail = commands.read_plugin_detail("sample-plugin", validate=_valid)
    with pytest.raises(
        commands.PluginCommandError, match="plugin_lifecycle_worker_unavailable"
    ):
        commands.review_plugin_command(
            "plugin.install",
            {"plugin_id": "sample-plugin", "revision": detail["revision"]},
            validate=_valid,
        )
    with pytest.raises(
        commands.PluginCommandError, match="plugin_setup_or_test_required"
    ):
        commands.review_plugin_command(
            "plugin.enable",
            {"plugin_id": "sample-plugin", "revision": detail["revision"]},
            validate=_valid,
        )


def test_configuration_review_never_returns_secret_values(plugin_modules, monkeypatch):
    manifest = manifest_payload(
        settings={"region": {"type": "select", "options": ["eu", "us"]}},
        secrets={"token": {"type": "secret"}},
    )
    _installed(plugin_modules, manifest=manifest)
    monkeypatch.setattr(
        commands.admissions, "keyed_digest", lambda value: commands._revision(value)
    )
    detail = commands.read_plugin_detail("sample-plugin", validate=_valid)
    review = commands.review_plugin_command(
        "plugin.configure",
        {
            "plugin_id": "sample-plugin",
            "revision": detail["revision"],
            "settings": {"region": "eu"},
            "secrets": {"token": "never-return-this"},
        },
        validate=_valid,
    )
    assert review["changes"] == {
        "settings": ["region"],
        "secrets": {"token": "replace"},
    }
    assert "never-return-this" not in json.dumps(review)


class _AdmissionFake:
    def __init__(self):
        self.rows: dict[tuple[str, str], dict] = {}
        self.effects = 0

    def metadata(self, owner, command_id):
        row = self.rows.get((owner, command_id))
        return deepcopy(row["metadata"]) if row else None

    def claim(self, owner, key, command, target, **kwargs):
        row = self.rows.get((owner, key))
        verifier = commands._revision(command)
        if row:
            if row["verifier"] != verifier or row["metadata"]["target"] != target:
                raise commands.admissions.AdmissionError("idempotency_mismatch")
            if row["metadata"]["status"] == "completed":
                return deepcopy(row["result"])
            raise commands.admissions.AdmissionError("operation_uncertain")
        self.rows[(owner, key)] = {
            "verifier": verifier,
            "metadata": {
                "target": target,
                "type": command["type"],
                "status": "admitting",
            },
            "result": deepcopy(kwargs.get("initial_result", {})),
        }
        return None

    def complete(self, owner, key, result):
        row = self.rows[(owner, key)]
        row["metadata"]["status"] = "completed"
        row["result"] = deepcopy(result)
        return result

    def receipt(self, owner, command_id):
        row = self.rows.get((owner, command_id))
        if not row:
            return None
        return {
            "command_id": command_id,
            "status": row["metadata"]["status"],
            **deepcopy(row["result"]),
        }


def _fake_admissions(monkeypatch) -> _AdmissionFake:
    fake = _AdmissionFake()
    monkeypatch.setattr(
        commands.admissions, "keyed_digest", lambda value: commands._revision(value)
    )
    monkeypatch.setattr(commands.admissions, "read_command_metadata", fake.metadata)
    monkeypatch.setattr(commands.admissions, "claim_command", fake.claim)
    monkeypatch.setattr(commands.admissions, "complete_command", fake.complete)
    monkeypatch.setattr(commands.admissions, "read_command_receipt", fake.receipt)
    return fake


def test_reviewed_configuration_executes_once_and_receipt_redacts(
    plugin_modules, monkeypatch
):
    manifest = manifest_payload(
        settings={"region": {"type": "select", "options": ["eu", "us"]}},
        secrets={"token": {"type": "secret"}},
    )
    _installed(plugin_modules, manifest=manifest)
    fake = _fake_admissions(monkeypatch)
    detail = commands.read_plugin_detail("sample-plugin", validate=_valid)
    request = {
        "plugin_id": "sample-plugin",
        "revision": detail["revision"],
        "settings": {"region": "eu"},
        "secrets": {"token": "private-token"},
    }
    review = commands.review_plugin_command(
        "plugin.configure", request, validate=_valid
    )
    command = {
        "command_id": str(UUID(int=1)),
        "type": "plugin.configure",
        "payload": {**request, "action_digest": review["action_digest"]},
    }
    calls = []
    result = commands.execute_plugin_command(
        owner_id="owner",
        key=command["command_id"],
        command=command,
        validate=_valid,
        validate_review=lambda value: calls.append(value["action_digest"]),
    )
    assert result["status"] == "completed"
    assert plugin_modules["state"].get_plugin_config("sample-plugin", "region") == "eu"
    assert (
        plugin_modules["state"].get_plugin_secret("sample-plugin", "token")
        == "private-token"
    )
    assert "private-token" not in json.dumps(list(fake.rows.values()))
    replay = commands.execute_plugin_command(
        owner_id="owner",
        key=command["command_id"],
        command=command,
        validate=_valid,
        validate_review=lambda _value: pytest.fail("replay re-reviewed"),
    )
    assert replay == result and len(calls) == 1


def test_effect_time_revocation_is_partial_and_never_blindly_replayed(
    plugin_modules, monkeypatch
):
    manifest = manifest_payload(
        settings={"first": {"type": "text"}, "second": {"type": "text"}}
    )
    _installed(plugin_modules, manifest=manifest)
    _fake_admissions(monkeypatch)
    detail = commands.read_plugin_detail("sample-plugin", validate=_valid)
    request = {
        "plugin_id": "sample-plugin",
        "revision": detail["revision"],
        "settings": {"first": "saved", "second": "blocked"},
        "secrets": {},
    }
    review = commands.review_plugin_command(
        "plugin.configure", request, validate=_valid
    )
    command = {
        "command_id": str(UUID(int=2)),
        "type": "plugin.configure",
        "payload": {**request, "action_digest": review["action_digest"]},
    }
    checks = 0
    armed = False

    def revoked():
        nonlocal checks
        if not armed:
            return
        checks += 1
        if checks >= 3:
            raise RuntimeError("auth revoked")

    def approve(_value):
        nonlocal armed, checks
        armed = True
        checks = 0

    result = commands.execute_plugin_command(
        owner_id="owner",
        key=command["command_id"],
        command=command,
        validate=revoked,
        validate_review=approve,
    )
    assert result == {
        "command_id": command["command_id"],
        "status": "partial",
        "code": "plugin_operation_unconfirmed",
        "plugin": {"plugin_id": "sample-plugin", "action": "plugin.configure"},
    }
    state = plugin_modules["state"]
    assert state.get_plugin_config("sample-plugin", "second") is None
    calls = checks
    assert (
        commands.execute_plugin_command(
            owner_id="owner",
            key=command["command_id"],
            command=command,
            validate=_valid,
            validate_review=lambda _value: pytest.fail("must not replay"),
        )
        == result
    )
    assert checks == calls


def test_reviewed_enable_and_disable_use_existing_runtime_revocation_owner(
    plugin_modules, monkeypatch
):
    _installed(plugin_modules)
    state, loader = plugin_modules["state"], plugin_modules["loader"]
    state.set_plugin_health_result(
        "sample-plugin", ok=True, checks=[{"label": "Setup", "status": "ok"}]
    )
    _fake_admissions(monkeypatch)
    refreshes = []

    class Loaded:
        plugin_id = "sample-plugin"
        success = True

    monkeypatch.setattr(
        loader,
        "refresh_plugin_runtime",
        lambda reason: refreshes.append(reason) or [Loaded()],
    )
    detail = commands.read_plugin_detail("sample-plugin", validate=_valid)
    request = {"plugin_id": "sample-plugin", "revision": detail["revision"]}
    review = commands.review_plugin_command("plugin.enable", request, validate=_valid)
    enable = {
        "command_id": str(UUID(int=3)),
        "type": "plugin.enable",
        "payload": {**request, "action_digest": review["action_digest"]},
    }
    result = commands.execute_plugin_command(
        owner_id="owner",
        key=enable["command_id"],
        command=enable,
        validate=_valid,
        validate_review=lambda current: (
            current == review or pytest.fail("changed review")
        ),
    )
    assert result["status"] == "completed" and result["plugin"]["enabled"] is True
    assert state.is_plugin_enabled("sample-plugin") is True

    detail = commands.read_plugin_detail("sample-plugin", validate=_valid)
    request = {"plugin_id": "sample-plugin", "revision": detail["revision"]}
    review = commands.review_plugin_command("plugin.disable", request, validate=_valid)
    disable = {
        "command_id": str(UUID(int=4)),
        "type": "plugin.disable",
        "payload": {**request, "action_digest": review["action_digest"]},
    }
    result = commands.execute_plugin_command(
        owner_id="owner",
        key=disable["command_id"],
        command=disable,
        validate=_valid,
        validate_review=lambda current: (
            current == review or pytest.fail("changed review")
        ),
    )
    assert result["status"] == "completed" and result["plugin"]["enabled"] is False
    assert state.is_plugin_enabled("sample-plugin") is False
    assert refreshes == ["reviewed plugin enablement", "reviewed plugin enablement"]


def test_reviewed_local_self_test_unlocks_enablement_without_loading_plugin(
    plugin_modules, monkeypatch
):
    _installed(plugin_modules)
    _fake_admissions(monkeypatch)
    monkeypatch.setattr(
        plugin_modules["loader"],
        "refresh_plugin_runtime",
        lambda *_: pytest.fail("Self-test must not load a plugin"),
    )
    detail = commands.read_plugin_detail("sample-plugin", validate=_valid)
    assert detail["capabilities"]["enable"]["available"] is False
    payload = {"plugin_id": "sample-plugin", "revision": detail["revision"]}
    review = commands.review_plugin_command("plugin.test", payload, validate=_valid)
    original = {
        "command_id": str(UUID(int=9)),
        "type": "plugin.test",
        "payload": {**payload, "action_digest": review["action_digest"]},
    }
    receipt = commands.execute_plugin_command(
        owner_id="owner",
        key=original["command_id"],
        command=original,
        validate=_valid,
        validate_review=lambda _: None,
    )
    assert receipt["status"] == "completed" and receipt["plugin"]["enabled"] is False
    assert (
        commands.execute_plugin_command(
            owner_id="owner",
            key=original["command_id"],
            command=original,
            validate=_valid,
            validate_review=lambda _: pytest.fail("no replay"),
        )
        == receipt
    )
    current = commands.read_plugin_detail("sample-plugin", validate=_valid)
    assert current["health"]["status"] == "passed"
    assert current["capabilities"]["enable"]["available"] is True
