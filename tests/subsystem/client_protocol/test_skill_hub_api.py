from __future__ import annotations

import hashlib
import urllib.error

import pytest

from row_bot.application import client_skill_hub as hub
from row_bot.skills_hub import source_registry
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


@pytest.fixture(autouse=True)
def isolated_receipts(tmp_path, reload_for_data_dir):
    reload_for_data_dir(tmp_path, "row_bot.tasks")


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
    assert first == second
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
    assert hub.admissions.read_command_metadata("revoked", "command-revoked") is None




def _unreachable_source(entry: SkillHubEntry) -> SkillBundle:
    raise urllib.error.HTTPError("https://example.test/SKILL.md", 404, "Not Found", None, None)


def _slow_source(entry: SkillHubEntry) -> SkillBundle:
    raise source_registry.SkillSourceTimeout("Fixture")


@pytest.mark.parametrize(
    ("inspect", "code"),
    [(_unreachable_source, "skill_preview_unavailable"), (_slow_source, "skill_source_timeout")],
)
def test_preview_failures_map_to_their_own_codes(monkeypatch, inspect, code) -> None:
    _fake_catalog(monkeypatch)
    monkeypatch.setattr(hub.catalog, "inspect_entry", inspect)
    search = hub.search_public_skills(owner_id="preview-failure", query="sample")

    with pytest.raises(hub.SkillHubCommandError, match=code):
        hub.preview_public_skill(
            owner_id="preview-failure",
            revision=search["revision"],
            entry_id="fixture:sample",
        )




def test_load_more_extends_results_and_earlier_pages_stay_previewable(
    monkeypatch,
) -> None:
    _fake_catalog(monkeypatch)
    entries = [
        SkillHubEntry(
            id=f"fixture:{index}",
            name=f"Sample {index}",
            description="Synthetic skill",
            source="fixture",
            source_id="fixture",
            install_ref=f"fixture:{index}",
        )
        for index in range(3)
    ]
    monkeypatch.setattr(
        hub.catalog,
        "search_skills",
        lambda query, *, limit, **kwargs: CatalogSearchResult(
            entries=entries[:limit], mode="cache", query=query
        ),
    )

    first = hub.search_public_skills(owner_id="pages", query="sample", limit=2)
    more = hub.search_public_skills(owner_id="pages", query="sample", limit=4)

    assert [entry["id"] for entry in first["entries"]] == ["fixture:0", "fixture:1"]
    assert first["has_more"] is True
    assert [entry["id"] for entry in more["entries"]] == [
        "fixture:0",
        "fixture:1",
        "fixture:2",
    ]
    assert more["has_more"] is False
    # A skill opened from the list shown before "Load more" still opens.
    preview = hub.preview_public_skill(
        owner_id="pages", revision=first["revision"], entry_id="fixture:1"
    )
    assert preview["entry"]["name"] == "Sample 1"


def test_preview_says_when_a_skill_with_its_name_is_installed(
    tmp_path, monkeypatch
) -> None:
    import row_bot.skills as skills

    _fake_catalog(monkeypatch)
    monkeypatch.setattr(skills, "USER_SKILLS_DIR", tmp_path / "skills")
    search = hub.search_public_skills(owner_id="installed", query="sample")
    fresh = hub.preview_public_skill(
        owner_id="installed", revision=search["revision"], entry_id="fixture:sample"
    )
    (tmp_path / "skills" / "sample").mkdir(parents=True)
    again = hub.preview_public_skill(
        owner_id="installed", revision=search["revision"], entry_id="fixture:sample"
    )

    assert fresh["entry"]["installed"] is False
    assert again["entry"]["installed"] is True
    assert again["skill_name"] == "sample"


def test_install_records_the_listing_it_came_from(monkeypatch) -> None:
    _fake_catalog(monkeypatch)
    installed: list[SkillBundle] = []
    monkeypatch.setattr(
        hub.installer,
        "install_bundle",
        lambda bundle, *, enabled: (
            installed.append(bundle)
            or InstallResult(True, "Installed.", skill_name="sample")
        ),
    )
    search = hub.search_public_skills(owner_id="listing", query="sample")
    preview = hub.preview_public_skill(
        owner_id="listing", revision=search["revision"], entry_id="fixture:sample"
    )
    hub.install_previewed_skill(
        owner_id="listing",
        command_id="command-listing",
        preview_id=preview["preview_id"],
        content_hash=preview["content_hash"],
        make_available=True,
    )

    assert installed[0].metadata["hub_entry_ref"] == "fixture:sample"




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
    revision = hub._record_revision(record)
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
    assert first["record"]["name"] == "sample"
    assert "secret-ref" not in str(first)
    assert "private-source" not in str(first)
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

    def uninstall(name, *, expected_record, operation_id):
        assert name == "sample" and expected_record == record
        assert operation_id == "delete-1"
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
    assert hub.execute_public_skill_maintenance(
        owner_id="maint-two",
        command_id="delete-1",
        name="sample",
        expected_revision=hub._record_revision(record),
        action="uninstall",
        confirmed=True,
    ) == result
