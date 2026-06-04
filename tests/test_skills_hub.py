from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path


def _reload_hub(tmp_path: Path):
    os.environ["THOTH_DATA_DIR"] = str(tmp_path)
    os.environ["ROW_BOT_DATA_DIR"] = str(tmp_path)
    for name in list(sys.modules):
        if name == "skills" or name.startswith("skills_hub"):
            sys.modules.pop(name, None)
    import row_bot.skills as skills
    import row_bot.skills_hub.catalog as catalog
    import row_bot.skills_hub.installer as installer
    import row_bot.skills_hub.provenance as provenance
    import row_bot.skills_hub.scanner as scanner
    import row_bot.skills_hub.sources as sources

    importlib.reload(skills)
    return skills, catalog, installer, provenance, scanner, sources


def _skill_md(
    name: str,
    *,
    body: str | None = None,
    tools: bool = False,
    extra_meta: str = "",
) -> str:
    lines = [
        "---",
        f"name: {name}",
        f"display_name: {name.replace('_', ' ').title()}",
        "description: Test public skill",
        "version: 1.0",
        "author: Public Tester",
    ]
    if tools:
        lines.extend(["tools:", "  - browser"])
    if extra_meta:
        lines.extend(extra_meta.strip().splitlines())
    lines.extend(["---", "", body or f"Instructions for {name}."])
    return "\n".join(lines)


def _bundle(tmp_path: Path, name: str, *, files=None):
    from row_bot.skills_hub import sources

    skill_files = files or [sources.SkillFile.from_text("SKILL.md", _skill_md(name), kind="markdown")]
    return sources.bundle_from_files(
        source="direct_url",
        install_ref=f"https://example.test/{name}/SKILL.md",
        root_name=name,
        files=skill_files,
        metadata={"url": f"https://example.test/{name}/SKILL.md", "trust_level": "community"},
    )


def test_direct_url_skill_md_bundle_parse(tmp_path, monkeypatch):
    _skills, _catalog, _installer, _prov, _scanner, _sources = _reload_hub(tmp_path)
    from row_bot.skills_hub.url_source import DirectURLSource
    import row_bot.skills_hub.url_source as url_source

    monkeypatch.setattr(url_source, "fetch_text", lambda _url: _skill_md("direct_skill"))
    bundle = DirectURLSource().fetch("https://example.test/direct/SKILL.md")

    assert bundle.frontmatter["name"] == "direct_skill"
    assert bundle.primary_skill_path == "SKILL.md"
    assert bundle.instructions == "Instructions for direct_skill."


def test_github_style_install_ref_parsing(tmp_path):
    _reload_hub(tmp_path)
    from row_bot.skills_hub.github_source import parse_github_install_ref

    parsed = parse_github_install_ref("openai/skills/skills/researcher")
    assert parsed is not None
    assert parsed.owner == "openai"
    assert parsed.repo == "skills"
    assert parsed.path == "skills/researcher"

    parsed = parse_github_install_ref("https://github.com/openai/skills/tree/main/skills/researcher")
    assert parsed is not None
    assert parsed.ref == "main"
    assert parsed.path == "skills/researcher"


def test_well_known_index_parsing(tmp_path):
    _reload_hub(tmp_path)
    from row_bot.skills_hub.well_known_source import parse_well_known_index

    entries = parse_well_known_index(
        {
            "verified": True,
            "skills": [
                {
                    "id": "brief",
                    "name": "Brief Writer",
                    "description": "Write concise briefs",
                    "url": "https://example.test/brief/SKILL.md",
                    "tags": ["writing"],
                }
            ],
        },
        "https://example.test/.well-known/skills/index.json",
    )
    assert entries[0].name == "Brief Writer"
    assert entries[0].trust_level == "verified"
    assert entries[0].install_ref.endswith("SKILL.md")


def test_multi_file_bundle_copy_scripts_warned_and_default_off(tmp_path):
    skills, _catalog, installer, provenance, _scanner, sources = _reload_hub(tmp_path)
    files = [
        sources.SkillFile.from_text("SKILL.md", _skill_md("multi_file"), kind="markdown"),
        sources.SkillFile.from_text("references/guide.md", "Reference notes.", kind="markdown"),
        sources.SkillFile.from_text("templates/template.md", "Template.", kind="markdown"),
        sources.SkillFile.from_bytes("assets/pixel.png", b"\x89PNG\r\n", kind="asset"),
        sources.SkillFile.from_text("scripts/helper.py", "print('passive only')", kind="script"),
    ]
    bundle = sources.bundle_from_files(
        source="github",
        install_ref="github:owner/repo/skills/multi_file",
        root_name="multi_file",
        files=files,
        metadata={"repository": "owner/repo", "trust_level": "community"},
    )

    result = installer.install_bundle(bundle)

    assert result.success
    assert result.skill_name == "multi_file"
    assert not skills.is_enabled("multi_file")
    assert (skills.USER_SKILLS_DIR / "multi_file" / "references" / "guide.md").exists()
    assert (skills.USER_SKILLS_DIR / "multi_file" / "templates" / "template.md").exists()
    assert (skills.USER_SKILLS_DIR / "multi_file" / "assets" / "pixel.png").exists()
    assert (skills.USER_SKILLS_DIR / "multi_file" / "scripts" / "helper.py").exists()
    assert any(w.code == "scripts_present" for w in result.warnings)
    assert provenance.get_record("multi_file") is not None


def test_scanner_blocks_tools_metadata_and_path_traversal(tmp_path):
    _skills, _catalog, _installer, _prov, scanner, sources = _reload_hub(tmp_path)
    tool_bundle = sources.bundle_from_files(
        source="direct_url",
        install_ref="https://example.test/toolish/SKILL.md",
        root_name="toolish",
        files=[sources.SkillFile.from_text("SKILL.md", _skill_md("toolish", tools=True), kind="markdown")],
    )
    tool_scan = scanner.scan_bundle(tool_bundle)
    assert tool_scan.blocked
    assert any(f.code == "tools_metadata" for f in tool_scan.findings)

    traversal_bundle = sources.bundle_from_files(
        source="direct_url",
        install_ref="https://example.test/bad/SKILL.md",
        root_name="bad",
        files=[
            sources.SkillFile.from_text("SKILL.md", _skill_md("bad"), kind="markdown"),
            sources.SkillFile.from_text("../escape.txt", "nope", kind="text"),
        ],
    )
    traversal_scan = scanner.scan_bundle(traversal_bundle)
    assert traversal_scan.blocked
    assert any(f.code == "path_traversal" for f in traversal_scan.findings)


def test_scanner_warns_on_shell_commands(tmp_path):
    _skills, _catalog, _installer, _prov, scanner, sources = _reload_hub(tmp_path)
    bundle = sources.bundle_from_files(
        source="direct_url",
        install_ref="https://example.test/shell/SKILL.md",
        root_name="shell",
        files=[sources.SkillFile.from_text("SKILL.md", _skill_md("shell", body="Run `curl https://example.test` then summarize."), kind="markdown")],
    )
    scan = scanner.scan_bundle(bundle)
    assert not scan.blocked
    assert any(f.code == "shell_commands" for f in scan.findings)


def test_scanner_blocks_too_many_files(tmp_path):
    _skills, _catalog, _installer, _prov, scanner, sources = _reload_hub(tmp_path)
    files = [sources.SkillFile.from_text("SKILL.md", _skill_md("many_files"), kind="markdown")]
    files.extend(
        sources.SkillFile.from_text(f"references/{index}.md", "x", kind="markdown")
        for index in range(scanner.MAX_FILE_COUNT + 1)
    )
    bundle = sources.bundle_from_files(
        source="github",
        install_ref="github:owner/repo/skills/many_files",
        root_name="many_files",
        files=files,
    )

    scan = scanner.scan_bundle(bundle)

    assert scan.blocked
    assert any(f.code == "too_many_files" for f in scan.findings)


def test_explicit_install_make_available_sets_enabled(tmp_path):
    skills, _catalog, installer, _prov, _scanner, _sources = _reload_hub(tmp_path)
    bundle = _bundle(tmp_path, "available_skill")

    result = installer.install_bundle(bundle, enabled=True)

    assert result.success
    assert skills.is_enabled("available_skill")
    assert not skills.is_tool_guide(skills.get_skill("available_skill"))


def test_conflict_rename_behavior(tmp_path):
    skills, _catalog, installer, provenance, _scanner, _sources = _reload_hub(tmp_path)
    assert installer.install_bundle(_bundle(tmp_path, "conflict_skill")).success

    result = installer.install_bundle(_bundle(tmp_path, "conflict_skill"), conflict_policy="rename")

    assert result.success
    assert result.skill_name == "conflict_skill_2"
    assert skills.get_skill("conflict_skill_2") is not None
    assert provenance.get_record("conflict_skill_2") is not None


def test_replace_with_backup_behavior(tmp_path):
    skills, _catalog, installer, _prov, _scanner, _sources = _reload_hub(tmp_path)
    assert installer.install_bundle(_bundle(tmp_path, "replace_skill")).success
    changed = _bundle(
        tmp_path,
        "replace_skill",
        files=None,
    )
    changed.files[0] = type(changed.files[0]).from_text(
        "SKILL.md",
        _skill_md("replace_skill", body="New instructions."),
        kind="markdown",
    )
    from row_bot.skills_hub.sources import compute_bundle_hash
    from dataclasses import replace

    changed = replace(changed, files=changed.files, content_hash=compute_bundle_hash(changed.files), instructions="New instructions.")

    result = installer.install_bundle(changed, conflict_policy="replace_with_backup")

    assert result.success
    assert "New instructions." in skills.get_skill("replace_skill").instructions
    backups = list((skills.DATA_DIR / "skill_versions" / "replace_skill").glob("hub-replace-*"))
    assert backups


def test_update_hash_detection_and_update(tmp_path, monkeypatch):
    skills, _catalog, installer, provenance, _scanner, _sources = _reload_hub(tmp_path)
    initial = _bundle(tmp_path, "update_skill")
    assert installer.install_bundle(initial).success

    monkeypatch.setattr(installer, "fetch_bundle_for_record", lambda _record: initial)
    current = installer.check_update("update_skill")
    assert current.success
    assert "up to date" in current.message

    changed = _bundle(
        tmp_path,
        "update_skill",
        files=[
            _sources.SkillFile.from_text(
                "SKILL.md",
                _skill_md("update_skill", body="Updated public instructions."),
                kind="markdown",
            )
        ],
    )
    monkeypatch.setattr(installer, "fetch_bundle_for_record", lambda _record: changed)
    available = installer.check_update("update_skill")
    assert available.success
    assert "Update available" in available.message

    updated = installer.update_skill("update_skill")
    assert updated.success
    assert "Updated public instructions." in skills.get_skill("update_skill").instructions
    assert provenance.get_record("update_skill").content_hash == updated.record.content_hash


def test_update_check_reports_unavailable_for_non_refetchable_source(tmp_path):
    _skills, _catalog, installer, _provenance, _scanner, sources = _reload_hub(tmp_path)
    bundle = sources.bundle_from_files(
        source="pasted_markdown",
        install_ref="pasted:abc123",
        root_name="pasted_update",
        files=[sources.SkillFile.from_text("SKILL.md", _skill_md("pasted_update"), kind="markdown")],
        metadata={"trust_level": "community", "pasted": True},
    )
    assert installer.install_bundle(bundle).success

    result = installer.check_update("pasted_update")

    assert not result.success
    assert "Update check unavailable" in result.message


def test_uninstall_removes_only_hub_installed_skill(tmp_path):
    skills, _catalog, installer, provenance, _scanner, _sources = _reload_hub(tmp_path)
    assert installer.install_bundle(_bundle(tmp_path, "hub_delete")).success
    assert installer.uninstall_skill("hub_delete").success
    assert skills.get_skill("hub_delete") is None
    assert provenance.get_record("hub_delete") is None

    skills.create_skill(
        name="custom_only",
        display_name="Custom Only",
        icon="star",
        description="Normal custom skill",
        instructions="Custom instructions.",
    )
    result = installer.uninstall_skill("custom_only")
    assert not result.success
    assert skills.get_skill("custom_only") is not None
