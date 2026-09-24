"""Explicit migration scans stay local, read only and redacted."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from uuid import uuid4

import pytest

from row_bot.application import client_migration
from row_bot.migration.fixtures import create_realistic_hermes_home
from tests.subsystem.client_protocol.test_protocol_security import bootstrap, client_app

pytestmark = pytest.mark.subsystem


def test_migration_scan_is_read_only_redacted_and_scoped_to_disposable_paths(tmp_path):
    source = create_realistic_hermes_home(tmp_path / "source")
    target = tmp_path / "target"
    before = {str(path.relative_to(source)): path.read_bytes() for path in source.rglob("*") if path.is_file()}
    preview = client_migration.scan_migration(
        owner_id="local", provider="hermes", source=str(source), target=str(target),
    )
    assert preview["source_found"] is True
    assert preview["summary"]["total"] == len(preview["items"])
    assert preview["summary"]["selected"] > 0
    assert any(item["category"] == "api_keys" and not item["selected"] for item in preview["items"])
    assert not target.exists()
    assert before == {str(path.relative_to(source)): path.read_bytes() for path in source.rglob("*") if path.is_file()}
    encoded = json.dumps(preview)
    assert "sk-hermes-three-month-user" not in encoded
    assert "linear-secret-token" not in encoded


def test_migration_scan_rejects_overlap_and_missing_source(tmp_path):
    source = create_realistic_hermes_home(tmp_path / "source")
    with pytest.raises(Exception, match="invalid_migration_selection"):
        client_migration.scan_migration(
            owner_id="local", provider="hermes", source=str(source), target=str(source / "target"),
        )
    with pytest.raises(Exception, match="invalid_migration_selection"):
        client_migration.scan_migration(
            owner_id="local", provider="hermes", source=str(tmp_path / "missing"), target=str(tmp_path / "target"),
        )


def test_migration_scan_api_denies_remote_before_reading_source(tmp_path, monkeypatch):
    source = create_realistic_hermes_home(tmp_path / "source")
    target = tmp_path / "target"
    calls = []
    original = client_migration.scan_migration

    def observed(**kwargs):
        calls.append(kwargs["provider"])
        return original(**kwargs)

    monkeypatch.setattr(client_migration, "scan_migration", observed)
    local, _, _ = client_app()
    remote, _, _ = client_app(remote=True)
    body = {"provider": "hermes", "source": str(source), "target": str(target), "include_secrets": False}
    with local, remote:
        _, headers = bootstrap(local)
        _, remote_headers = bootstrap(remote)
        assert remote.post("/api/v1/system/migration/scan", headers=remote_headers, json=body).status_code == 403
        assert calls == []
        result = local.post("/api/v1/system/migration/scan", headers=headers, json=body)
    assert result.status_code == 200, result.text
    assert result.json()["summary"]["selected"] > 0
    assert calls == ["hermes"]
    assert not target.exists()


def test_migration_apply_exact_selection_receipt_and_duplicate(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "row-bot-profile"))
    source = create_realistic_hermes_home(tmp_path / "source")
    target = tmp_path / "target"
    preview = client_migration.scan_migration(owner_id="owner-a", provider="hermes", source=str(source), target=str(target))
    item = next(item for item in preview["items"] if item["category"] == "identity" and item["status"] == "planned")
    chosen = [item["id"]]
    review = client_migration.review_migration_apply(
        owner_id="owner-a", plan_id=preview["plan_id"], revision=preview["revision"],
        selected_ids=chosen, overwrite=False,
    )
    assert review["selected"] == 1
    assert review["backup_required"] is True
    command_id = str(uuid4())
    args = dict(owner_id="owner-a", command_id=command_id, plan_id=preview["plan_id"],
                revision=preview["revision"], review_digest=review["review_digest"],
                selected_ids=chosen, overwrite=False, confirmed=True)
    receipt = client_migration.apply_selected_migration(**args)
    assert receipt["status"] == "completed"
    assert receipt["summary"]["migrated"] == 1
    assert receipt["command_id"] == command_id
    assert client_migration.apply_selected_migration(**args) == receipt
    assert len(list(target.glob("migration-reports/*"))) == 1
    assert not (target / "migration-reports" / "sessions").exists()
    encoded = json.dumps(receipt)
    assert str(source) not in encoded
    assert str(target) not in encoded
    with pytest.raises(Exception, match="migration_command_conflict"):
        client_migration.apply_selected_migration(**{**args, "selected_ids": []})


def test_migration_apply_rejects_stale_denied_and_unconfirmed(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "row-bot-profile"))
    source = create_realistic_hermes_home(tmp_path / "source")
    target = tmp_path / "target"
    preview = client_migration.scan_migration(owner_id="owner-b", provider="hermes", source=str(source), target=str(target))
    chosen = [next(item["id"] for item in preview["items"] if item["category"] == "identity" and item["status"] == "planned")]
    params = dict(owner_id="owner-b", plan_id=preview["plan_id"], revision=preview["revision"],
                  selected_ids=chosen, overwrite=False)
    with pytest.raises(Exception, match="migration_plan_missing"):
        client_migration.review_migration_apply(**{**params, "owner_id": "other-owner"})
    review = client_migration.review_migration_apply(**params)
    command = dict(**params, command_id=str(uuid4()), review_digest=review["review_digest"], confirmed=False)
    with pytest.raises(Exception, match="migration_confirmation_required"):
        client_migration.apply_selected_migration(**command)
    assert not target.exists()
    (source / "SOUL.md").write_text("changed after preview", encoding="utf-8")
    with pytest.raises(Exception, match="migration_changed"):
        client_migration.apply_selected_migration(**{**command, "confirmed": True})
    assert not target.exists()


def test_migration_api_review_apply_recover_and_remote_denial(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "row-bot-profile"))
    source = create_realistic_hermes_home(tmp_path / "source")
    target = tmp_path / "target"
    local, _, _ = client_app()
    remote, _, _ = client_app(remote=True)
    with local, remote:
        _, headers = bootstrap(local)
        _, remote_headers = bootstrap(remote)
        scan = local.post("/api/v1/system/migration/scan", headers=headers, json={
            "provider": "hermes", "source": str(source), "target": str(target),
        })
        assert scan.status_code == 200, scan.text
        preview = scan.json()
        chosen = [next(item["id"] for item in preview["items"] if item["category"] == "identity" and item["status"] == "planned")]
        request = {"plan_id": preview["plan_id"], "revision": preview["revision"],
                   "selected_ids": chosen, "overwrite": False}
        assert remote.post("/api/v1/system/migration/review", headers=remote_headers, json=request).status_code == 403
        review = local.post("/api/v1/system/migration/review", headers=headers, json=request)
        assert review.status_code == 200, review.text
        command_id = str(uuid4())
        body = {**request, "command_id": command_id,
                "review_digest": review.json()["review_digest"], "confirmed": True}
        assert remote.post("/api/v1/system/migration/apply", headers={**remote_headers, "idempotency-key": command_id}, json=body).status_code == 403
        assert not target.exists()
        assert local.post("/api/v1/system/migration/apply", headers=headers, json=body).status_code == 409
        executed = local.post("/api/v1/system/migration/apply", headers={**headers, "idempotency-key": command_id}, json=body)
        assert executed.status_code == 200, executed.text
        receipt = executed.json()
        assert receipt["summary"]["migrated"] == 1
        repeat = local.post("/api/v1/system/migration/apply", headers={**headers, "idempotency-key": command_id}, json=body)
        assert repeat.json() == receipt
        recovered = local.get(f"/api/v1/system/migration/apply/{command_id}", headers=headers)
        assert recovered.json() == receipt
        assert remote.get(f"/api/v1/system/migration/apply/{command_id}", headers=remote_headers).status_code == 403


def test_migration_interruption_keeps_durable_original_command(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "row-bot-profile"))
    source = create_realistic_hermes_home(tmp_path / "source")
    target = tmp_path / "target"
    preview = client_migration.scan_migration(owner_id="local", provider="hermes", source=str(source), target=str(target))
    chosen = [next(item["id"] for item in preview["items"] if item["category"] == "identity" and item["status"] == "planned")]
    request = dict(owner_id="local", plan_id=preview["plan_id"], revision=preview["revision"],
                   selected_ids=chosen, overwrite=False)
    review = client_migration.review_migration_apply(**request)
    command_id = str(uuid4())
    command = dict(**request, command_id=command_id, review_digest=review["review_digest"], confirmed=True)
    calls = []

    def interrupted(*_args, **_kwargs):
        calls.append("attempt")
        raise RuntimeError("synthetic interruption")

    monkeypatch.setattr(client_migration, "apply_migration_plan", interrupted)
    with pytest.raises(RuntimeError, match="synthetic interruption"):
        client_migration.apply_selected_migration(**command)
    receipt = client_migration.read_migration_receipt(owner_id="local", command_id=command_id)
    assert receipt["status"] == "interrupted"
    assert client_migration.apply_selected_migration(**command) == receipt
    assert calls == ["attempt"]
    assert not target.exists()


def test_migration_conflict_requires_explicit_overwrite_and_preserves_restore_copy(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "row-bot-profile"))
    source = create_realistic_hermes_home(tmp_path / "source")
    target = tmp_path / "target"
    target.mkdir()
    original = "# Existing persona\n"
    (target / "identity").mkdir()
    (target / "identity" / "SOUL.md").write_text(original, encoding="utf-8")
    preview = client_migration.scan_migration(owner_id="local", provider="hermes", source=str(source), target=str(target))
    conflict = next(item for item in preview["items"] if item["target"] == str(Path("identity") / "SOUL.md"))
    assert conflict["status"] == "conflict"
    params = dict(owner_id="local", plan_id=preview["plan_id"], revision=preview["revision"], selected_ids=[conflict["id"]])
    with pytest.raises(Exception, match="invalid_migration_selection"):
        client_migration.review_migration_apply(**params, overwrite=False)
    review = client_migration.review_migration_apply(**params, overwrite=True)
    assert review["conflicts"] == 1
    receipt = client_migration.apply_selected_migration(
        **params, overwrite=True, command_id=str(uuid4()),
        review_digest=review["review_digest"], confirmed=True,
    )
    assert receipt["status"] == "completed"
    assert (target / "identity" / "SOUL.md").read_text(encoding="utf-8") == (source / "SOUL.md").read_text(encoding="utf-8")
    backups = list((target / "migration-backups").glob("*/identity/SOUL.md"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == original
    shutil.copy2(backups[0], target / "identity" / "SOUL.md")
    assert (target / "identity" / "SOUL.md").read_text(encoding="utf-8") == original
