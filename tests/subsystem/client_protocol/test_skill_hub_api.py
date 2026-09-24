from __future__ import annotations

import hashlib

import pytest
from uuid import uuid4

from row_bot.application import client_skill_hub as hub
from row_bot.skills_hub.models import (
    CatalogSearchResult,
    InstallResult,
    SkillBundle,
    SkillFile,
    SkillHubEntry,
    SkillInstallRecord,
    SkillScanResult,
    SourceResult,
)
from tests.subsystem.client_protocol.test_protocol_security import bootstrap, client_app


def _entry() -> SkillHubEntry:
    return SkillHubEntry(
        id="fixture:sample",
        name="Sample",
        description="Synthetic skill",
        source="fixture",
        source_id="fixture",
        install_ref="fixture:sample",
        metadata={"private": "must not appear"},
    )


def _bundle() -> SkillBundle:
    content = (
        "---\nname: sample\ndescription: Synthetic skill\n---\nUse only fixtures.\n"
    )
    return SkillBundle(
        source="fixture",
        install_ref="fixture:sample",
        root_name="sample",
        primary_skill_path="SKILL.md",
        files=[SkillFile.from_text("SKILL.md", content)],
        frontmatter={"name": "sample"},
        instructions="Use only fixtures.",
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
    )


def _fake_catalog(monkeypatch) -> None:
    entry = _entry()
    monkeypatch.setattr(
        hub.catalog,
        "search_skills",
        lambda *args, **kwargs: CatalogSearchResult(
            entries=[entry],
            mode="cache",
            query="sample",
            source_statuses=[
                SourceResult(entries=[entry], source_id="fixture", status="cached"),
            ],
        ),
    )
    monkeypatch.setattr(hub.catalog, "inspect_entry", lambda selected: _bundle())
    monkeypatch.setattr(
        hub,
        "scan_bundle",
        lambda bundle: SkillScanResult(
            ok=True,
            blocked=False,
            findings=[],
            summary={},
            token_estimate=24,
        ),
    )


def test_search_preview_install_uses_exact_scanned_bundle_once(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    _fake_catalog(monkeypatch)
    installed = []
    monkeypatch.setattr(
        hub.installer,
        "install_bundle",
        lambda bundle, *, enabled: (
            installed.append((bundle.content_hash, enabled))
            or InstallResult(True, "Skill installed disabled.", skill_name="sample")
        ),
    )
    search = hub.search_public_skills(owner_id="one", query="sample")
    assert len(search["entries"]) == 1
    assert "private" not in str(search)
    preview = hub.preview_public_skill(
        owner_id="one", revision=search["revision"], entry_id="fixture:sample"
    )
    assert "Use only fixtures" in preview["primary_text"]
    kwargs = dict(
        owner_id="one",
        command_id="command-1",
        preview_id=preview["preview_id"],
        content_hash=preview["content_hash"],
        make_available=False,
    )
    first = hub.install_previewed_skill(**kwargs)
    second = hub.install_previewed_skill(**kwargs)
    assert (
        first
        == second
        == hub.read_skill_install_receipt(owner_id="one", command_id="command-1")
    )
    assert first["success"] is True
    assert installed == [(preview["content_hash"], False)]


def test_stale_catalog_and_modified_install_command_are_denied(monkeypatch) -> None:
    _fake_catalog(monkeypatch)
    search = hub.search_public_skills(owner_id="two", query="sample")
    with pytest.raises(hub.SkillHubCommandError, match="skill_catalog_changed"):
        hub.preview_public_skill(
            owner_id="two", revision="0" * 64, entry_id="fixture:sample"
        )
    preview = hub.preview_public_skill(
        owner_id="two", revision=search["revision"], entry_id="fixture:sample"
    )
    with pytest.raises(hub.SkillHubCommandError, match="skill_preview_changed"):
        hub.install_previewed_skill(
            owner_id="two",
            command_id="command-2",
            preview_id=preview["preview_id"],
            content_hash="0" * 64,
            make_available=False,
        )
    with pytest.raises(hub.SkillHubCommandError, match="skill_catalog_expired"):
        hub.preview_public_skill(
            owner_id="other", revision=search["revision"], entry_id="fixture:sample"
        )


def test_revoked_authority_prevents_install(monkeypatch) -> None:
    _fake_catalog(monkeypatch)
    search = hub.search_public_skills(owner_id="revoked", query="sample")
    preview = hub.preview_public_skill(
        owner_id="revoked", revision=search["revision"], entry_id="fixture:sample"
    )
    monkeypatch.setattr(
        hub.installer,
        "install_bundle",
        lambda *args, **kwargs: pytest.fail("must not install"),
    )

    def deny() -> None:
        raise PermissionError("revoked")

    with pytest.raises(PermissionError, match="revoked"):
        hub.install_previewed_skill(
            owner_id="revoked",
            command_id="command-revoked",
            preview_id=preview["preview_id"],
            content_hash=preview["content_hash"],
            make_available=False,
            validate=deny,
        )
    with pytest.raises(hub.SkillHubCommandError, match="skill_receipt_missing"):
        hub.read_skill_install_receipt(owner_id="revoked", command_id="command-revoked")


def test_v1_hub_search_preview_install_and_receipt_use_session_proof(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "profile"))
    _fake_catalog(monkeypatch)
    monkeypatch.setattr(
        hub.installer,
        "install_bundle",
        lambda bundle, *, enabled: InstallResult(
            True,
            "Skill installed disabled.",
            skill_name="sample",
        ),
    )
    client, _service, _active = client_app()
    with client:
        _view, headers = bootstrap(client)
        searched = client.post(
            "/api/v1/settings/skills/hub/search",
            headers=headers,
            json={"query": "sample", "source": "all", "refresh": False},
        )
        assert searched.status_code == 200, searched.text
        found = searched.json()
        previewed = client.post(
            "/api/v1/settings/skills/hub/preview",
            headers=headers,
            json={"revision": found["revision"], "entry_id": "fixture:sample"},
        )
        assert previewed.status_code == 200, previewed.text
        preview = previewed.json()
        command_id = str(uuid4())
        payload = {
            "command_id": command_id,
            "preview_id": preview["preview_id"],
            "content_hash": preview["content_hash"],
            "make_available": False,
        }
        no_key = client.post(
            "/api/v1/settings/skills/hub/install", headers=headers, json=payload
        )
        assert no_key.status_code == 409
        installed = client.post(
            "/api/v1/settings/skills/hub/install",
            headers={**headers, "Idempotency-Key": command_id},
            json=payload,
        )
        assert installed.status_code == 200, installed.text
        assert installed.json()["success"] is True
        original = client.get(
            f"/api/v1/settings/skills/hub/install/{command_id}", headers=headers
        )
        assert original.json() == installed.json()


def test_v1_hub_remote_session_revocation_blocks_delivery(monkeypatch) -> None:
    _fake_catalog(monkeypatch)
    client, _service, active = client_app(remote=True)
    with client:
        _view, headers = bootstrap(client)
        active["value"] = False
        response = client.post(
            "/api/v1/settings/skills/hub/search",
            headers=headers,
            json={"query": "sample", "source": "all", "refresh": False},
        )
        assert response.status_code == 401


def test_installed_provenance_is_bounded_and_maintenance_is_fenced(monkeypatch) -> None:
    record = SkillInstallRecord(
        local_name="sample",
        source="fixture",
        source_id="private-source",
        install_ref="secret-ref",
        installed_at="2026-09-23T00:00:00Z",
        updated_at="2026-09-23T00:00:00Z",
        content_hash="a" * 64,
        enabled=False,
        file_count=1,
        scan_summary={},
        metadata={"private": "must not appear"},
    )
    saved = {"sample": record}
    monkeypatch.setattr(hub.provenance, "load_records", lambda: saved.copy())
    monkeypatch.setattr(hub.provenance, "get_record", lambda name: saved.get(name))
    checks = []
    monkeypatch.setattr(
        hub.installer,
        "check_update",
        lambda name: (
            checks.append(name)
            or InstallResult(True, "Skill 'sample' is up to date.", skill_name=name)
        ),
    )
    page = hub.read_installed_public_skills()
    assert page["items"][0]["name"] == "sample"
    assert "secret-ref" not in str(page)
    assert "private-source" not in str(page)
    revision = page["items"][0]["revision"]
    kwargs = dict(
        owner_id="maint",
        command_id="command-check",
        name="sample",
        expected_revision=revision,
        action="check",
    )
    first = hub.execute_public_skill_maintenance(**kwargs)
    assert first == hub.execute_public_skill_maintenance(**kwargs)
    assert checks == ["sample"]
    assert first["success"] is True
    with pytest.raises(hub.SkillHubCommandError, match="skill_command_conflict"):
        hub.execute_public_skill_maintenance(**{**kwargs, "action": "update"})
    with pytest.raises(hub.SkillHubCommandError, match="skill_record_changed"):
        hub.execute_public_skill_maintenance(
            **{**kwargs, "command_id": "new", "expected_revision": "0" * 64}
        )
    with pytest.raises(hub.SkillHubCommandError, match="skill_confirmation_required"):
        hub.execute_public_skill_maintenance(
            **{**kwargs, "command_id": "uninstall", "action": "uninstall"}
        )


def test_uninstall_uses_expected_record_and_cannot_delete_another_skill(
    monkeypatch,
) -> None:
    record = SkillInstallRecord(
        local_name="sample",
        source="fixture",
        source_id="fixture",
        install_ref="fixture:sample",
        installed_at="2026-09-23T00:00:00Z",
        updated_at="2026-09-23T00:00:00Z",
        content_hash="a" * 64,
        enabled=False,
        file_count=1,
        scan_summary={},
    )
    saved = {"sample": record}
    monkeypatch.setattr(hub.provenance, "get_record", lambda name: saved.get(name))
    removed = []

    def uninstall(name, *, expected_record):
        assert name == "sample" and expected_record == record
        removed.append(name)
        saved.pop(name)
        return InstallResult(True, "Skill uninstalled.", skill_name=name)

    monkeypatch.setattr(hub.installer, "uninstall_skill", uninstall)
    result = hub.execute_public_skill_maintenance(
        owner_id="maint-two",
        command_id="delete-1",
        name="sample",
        expected_revision=hub._record_revision(record),
        action="uninstall",
        confirmed=True,
    )
    assert result["success"] is True
    assert result["record"] is None
    assert removed == ["sample"]
    assert (
        hub.read_skill_maintenance_receipt(owner_id="maint-two", command_id="delete-1")
        == result
    )
