from __future__ import annotations

from pathlib import Path


def test_ui_module_imports_and_dialog_callable():
    import skills_hub.ui as hub_ui

    assert callable(hub_ui.open_skills_hub_dialog)
    assert callable(hub_ui.install_preview_bundle)
    assert '"Install"' in Path("skills_hub/ui.py").read_text(encoding="utf-8")
    assert "Install & Make Available" in Path("skills_hub/ui.py").read_text(encoding="utf-8")
    assert "Import from Source" in Path("skills_hub/ui.py").read_text(encoding="utf-8")
    assert "ui.select" not in Path("skills_hub/ui.py").read_text(encoding="utf-8")


def test_filter_chips_are_sources_not_trust_modes(monkeypatch):
    import skills_hub.ui as hub_ui

    monkeypatch.setattr(hub_ui, "source_metadata", lambda: [
        {"id": "github", "source_group": "github", "display_name": "GitHub", "supports_browse": True},
        {"id": "claude_marketplace", "source_group": "github", "display_name": "GitHub Manifest", "supports_browse": False},
        {"id": "skills_sh", "source_group": "skills_sh", "display_name": "skills.sh", "supports_browse": True},
    ])

    chips = hub_ui._available_filter_chips()

    assert chips == [("all", "All"), ("github", "GitHub"), ("skills_sh", "skills.sh")]
    assert "Trusted" not in {label for _id, label in chips}
    assert "Community" not in {label for _id, label in chips}


def test_skill_preview_uses_escaped_preformatted_text():
    src = Path("skills_hub/ui.py").read_text(encoding="utf-8")

    assert "html.escape" in src
    assert "skill-hub-preview-code" not in src
    assert "```markdown\\n" not in src


def test_browse_dialog_has_no_outer_scroll_layout_regression():
    src = Path("skills_hub/ui.py").read_text(encoding="utf-8")

    assert "height: calc(100vh - 190px)" not in src
    assert "height:auto" not in src
    assert "height:100%" in src
    assert "overflow:hidden" in src
    assert "flex flex-col" in src
    assert "min-height:0" in src
    assert "overflow-auto" in src


def test_settings_skills_tab_contains_browse_wiring():
    src = Path("ui/settings.py").read_text(encoding="utf-8")

    assert "Browse Skills" in src
    assert "open_skills_hub_dialog" in src
    assert "load_records" in src


def test_install_actions_pass_correct_enabled_default(tmp_path, monkeypatch):
    import os
    import sys

    os.environ["THOTH_DATA_DIR"] = str(tmp_path)
    for name in list(sys.modules):
        if name == "skills" or name.startswith("skills_hub"):
            sys.modules.pop(name, None)

    from skills_hub.models import InstallResult
    from skills_hub.sources import SkillFile, bundle_from_files
    import skills_hub.ui as hub_ui

    bundle = bundle_from_files(
        source="direct_url",
        install_ref="https://example.test/ui/SKILL.md",
        root_name="ui_skill",
        files=[
            SkillFile.from_text(
                "SKILL.md",
                "---\nname: ui_skill\ndescription: UI skill\n---\n\nInstructions.",
                kind="markdown",
            )
        ],
    )
    calls: list[bool] = []

    def fake_install_bundle(_bundle, *, enabled=False, conflict_policy="keep_existing"):
        calls.append(enabled)
        return InstallResult(True, "ok", "ui_skill")

    monkeypatch.setattr(hub_ui, "install_bundle", fake_install_bundle)

    hub_ui.install_preview_bundle(bundle)
    hub_ui.install_preview_bundle(bundle, make_available=True)

    assert calls == [False, True]
