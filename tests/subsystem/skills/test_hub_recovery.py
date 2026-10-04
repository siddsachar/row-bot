from dataclasses import replace
from uuid import uuid4

import pytest

from row_bot.application import client_skill_hub as hub
from row_bot.skills_hub import installer, provenance
from row_bot.skills_hub.models import SkillFile
from row_bot.skills_hub.sources import bundle_from_files

pytestmark = pytest.mark.platform


def bundle(body):
    return bundle_from_files(source="fixture", install_ref="fixture:writing", root_name="writing",
        files=[SkillFile.from_text("SKILL.md", "---\nname: writing\ndescription: Fixture writing skill\n---\n" + body),
            SkillFile.from_bytes("references/chart.bin", b"\x89PNG\r\n\x00fixture")])


def test_failed_record_publication_can_restore_proven_previous_files(tmp_path, monkeypatch, reload_for_data_dir):
    skills, = reload_for_data_dir(tmp_path, "row_bot.skills")
    reload_for_data_dir(tmp_path, "row_bot.tasks")
    monkeypatch.setattr(installer, "_clear_agent_cache", lambda: None)
    original = installer.install_bundle(bundle("Original."), enabled=False)
    assert original.success
    old = provenance.get_record("writing")
    with monkeypatch.context() as fault:
        fault.setattr(installer, "upsert_record", lambda *a: (_ for _ in ()).throw(OSError("interrupted")))
        with pytest.raises(OSError):
            installer.update_skill("writing", expected_record=old, reviewed_bundle=bundle("New."), operation_id=str(uuid4()))
    assert provenance.get_record("writing") == old
    assert "New." in (skills.USER_SKILLS_DIR / "writing" / "SKILL.md").read_text()
    restored = installer.restore_skill("writing", expected_record=old, operation_id=str(uuid4()))
    assert restored.success, restored.message
    assert "Original." in (skills.USER_SKILLS_DIR / "writing" / "SKILL.md").read_text()
    assert (skills.USER_SKILLS_DIR / "writing/references/chart.bin").read_bytes() == b"\x89PNG\r\n\x00fixture"


def test_local_edits_survive_update_and_restore(tmp_path, monkeypatch, reload_for_data_dir):
    skills, = reload_for_data_dir(tmp_path, "row_bot.skills")
    monkeypatch.setattr(installer, "_clear_agent_cache", lambda: None)
    assert installer.install_bundle(bundle("Original.")).success
    assert installer.update_skill("writing", reviewed_bundle=bundle("Second.")).success
    record = provenance.get_record("writing")
    path = skills.USER_SKILLS_DIR / "writing/SKILL.md"
    path.write_text("My local edit")
    assert not installer.update_skill("writing", reviewed_bundle=bundle("Third.")).success
    assert not installer.restore_skill("writing", expected_record=record, operation_id=str(uuid4())).success
    assert path.read_text() == "My local edit"


def _previewed(tmp_path, monkeypatch, reload_for_data_dir):
    from row_bot.skills_hub.models import SkillHubEntry, CatalogSearchResult
    reload_for_data_dir(tmp_path, "row_bot.skills", "row_bot.tasks")
    monkeypatch.setattr(installer, "_clear_agent_cache", lambda: None)
    entry = SkillHubEntry(id="fixture:writing", name="Writing", description="Fixture", source="fixture", source_id="fixture", install_ref="fixture:writing")
    monkeypatch.setattr(hub.catalog, "inspect_entry", lambda *a: bundle("Original."))
    monkeypatch.setattr(hub.catalog, "search_skills", lambda *a, **k: CatalogSearchResult(entries=[entry], mode="cache", query="writing"))
    found = hub.search_public_skills(owner_id="owner", query="writing")
    return hub.preview_public_skill(owner_id="owner", revision=found["revision"], entry_id=entry.id)


def test_a_read_leaves_an_unproven_outcome_unfinished_until_it_is_checked(tmp_path, monkeypatch, reload_for_data_dir):
    from row_bot.runtime import admissions
    preview = _previewed(tmp_path, monkeypatch, reload_for_data_dir)
    identity = str(uuid4())
    monkeypatch.setattr(installer, "install_bundle", lambda *a, **k: (_ for _ in ()).throw(OSError("interrupted")))
    assert not hub.install_previewed_skill(owner_id="owner", command_id=identity, preview_id=preview["preview_id"],
                                           content_hash=preview["content_hash"], make_available=False)["success"]
    passive = hub.reconcile_skill_hub_operation(owner_id="owner", command_id=identity, validate=lambda: None, proven_only=True)
    assert not passive["settled"] and admissions.read_command_metadata("owner", identity)["status"] == "admitting"
    assert hub.reconcile_skill_hub_operation(owner_id="owner", command_id=identity, validate=lambda: None)["settled"]
    assert admissions.read_command_metadata("owner", identity)["status"] == "completed"


def test_explicit_reconciliation_closes_lost_receipt_without_repeating_publication(tmp_path, monkeypatch, reload_for_data_dir):
    from row_bot.runtime import admissions
    preview = _previewed(tmp_path, monkeypatch, reload_for_data_dir)
    identity = str(uuid4())
    with monkeypatch.context() as fault:
        fault.setattr(admissions, "complete_command", lambda *a, **k: (_ for _ in ()).throw(OSError("receipt interrupted")))
        result = hub.install_previewed_skill(owner_id="owner", command_id=identity, preview_id=preview["preview_id"], content_hash=preview["content_hash"], make_available=False)
    assert result["success"]
    assert admissions.read_command_metadata("owner", identity)["status"] == "admitting"
    monkeypatch.setattr(installer, "install_bundle", lambda *a, **k: pytest.fail("publication replayed"))
    assert hub.read_skill_install_receipt(owner_id="owner", command_id=identity)["success"]
    assert admissions.read_command_metadata("owner", identity)["status"] == "admitting"
    assert hub.reconcile_skill_hub_operation(owner_id="owner", command_id=identity, validate=lambda: None)["settled"]
    assert admissions.read_command_metadata("owner", identity)["status"] == "completed"


def test_clawhub_moderation_preserves_files_but_blocks_new_activation(tmp_path, monkeypatch, reload_for_data_dir):
    from row_bot.skills_hub import catalog
    from row_bot.skills_hub.clawhub_source import ClawHubSource
    from row_bot.skills_hub import clawhub_source
    skills, = reload_for_data_dir(tmp_path, "row_bot.skills")
    monkeypatch.setattr(installer, "_clear_agent_cache", lambda: None)
    upstream = replace(bundle("Original."), source="clawhub", install_ref="clawhub:writing@1.0.0")
    assert installer.install_bundle(upstream).success
    path = skills.USER_SKILLS_DIR / "writing/SKILL.md"
    original = path.read_bytes()
    monkeypatch.setattr(catalog, "source_for_id", lambda _: ClawHubSource())
    monkeypatch.setattr(clawhub_source, "fetch_json", lambda _: {"skill": {"moderationStatus": "malicious"}})
    assert not installer.check_update("writing").success
    assert path.read_bytes() == original
    with pytest.raises(ValueError, match="skill_unavailable"):
        skills.set_enabled("writing", True)
    with pytest.raises(ValueError, match="skill_unavailable"):
        skills.set_pinned("writing", True)
    with pytest.raises(ValueError, match="skill_unavailable"):
        skills.update_client_skill_preference("writing", "availability", True,
            expected_revision=skills.read_client_skills()["revision"], command_id=str(uuid4()),
            validate=lambda: None, checkpoint=lambda _: None)
    skills.set_enabled("writing", False)
    assert not skills.is_enabled("writing")


def test_legacy_clawhub_update_keeps_its_recorded_publisher(tmp_path, monkeypatch, reload_for_data_dir):
    from row_bot.skills_hub import catalog
    from row_bot.skills_hub.clawhub_source import ClawHubSource
    reload_for_data_dir(tmp_path, "row_bot.skills")
    monkeypatch.setattr(installer, "_clear_agent_cache", lambda: None)
    upstream = replace(bundle("Original."), source="clawhub", install_ref="clawhub:writing@1.0.0", metadata={"author": "alice"})
    assert installer.install_bundle(upstream).success
    calls = []
    source = ClawHubSource()
    monkeypatch.setattr(source, "fetch", lambda reference: calls.append(reference) or upstream)
    monkeypatch.setattr(catalog, "source_for_id", lambda _: source)
    installer.fetch_bundle_for_record(provenance.get_record("writing"))
    assert calls == ["clawhub:alice/writing"]
