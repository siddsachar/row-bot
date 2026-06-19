import json
from pathlib import Path

import yaml

from scripts.docs.collect_inventory import ROOT, build_inventory
from scripts.docs.generate_llms_txt import generate
from scripts.docs.generate_mdx import check_pages, render_pages
from scripts.docs.validate_public_docs import validate


def test_public_docs_inventory_has_core_sections() -> None:
    inventory = build_inventory()

    assert inventory["version"]["version"] != "unknown"
    assert any(tool["id"] == "browser" for tool in inventory["tools"])
    assert any(provider["id"] == "ollama" for provider in inventory["providers"])
    assert any(tab["title"] == "Providers" for tab in inventory["settings"])
    assert any(tab["title"] == "Workflows" for tab in inventory["home_tabs"])
    assert any(channel["id"] == "telegram" for channel in inventory["channels"])
    assert any(skill["id"] == "task_automation" for skill in inventory["skills"])
    assert any(server["id"] == "microsoft-playwright" for server in inventory["mcp"])
    assert any(plugin["id"] == "plugin-manifest" for plugin in inventory["plugins"])
    assert any(path["id"] == "threads_db" for path in inventory["data_paths"])
    assert any(rule["id"] == "approve" for rule in inventory["safety"])
    assert any(page["path"] == "index.mdx" for page in inventory["docs_pages"])


def test_generated_mdx_pages_are_current() -> None:
    errors = check_pages(render_pages(build_inventory()))
    assert errors == []


def test_llms_txt_generation_covers_docs_routes(tmp_path: Path) -> None:
    docs_root = ROOT / "docs-site" / "docs"
    generate(docs_root, tmp_path)

    llms = (tmp_path / "llms.txt").read_text(encoding="utf-8")
    llms_full = (tmp_path / "llms-full.txt").read_text(encoding="utf-8")

    assert "# Row-Bot Docs" in llms
    assert "[Row-Bot Documentation](/docs/)" in llms
    assert "Route: /docs/reference/generated/tools" in llms
    assert "Route: /docs/" in llms_full
    assert (tmp_path / "docs" / "llms.txt").is_file()
    assert (tmp_path / "docs" / "llms-full.txt").is_file()
    for path in sorted(docs_root.rglob("*.mdx")) + sorted(docs_root.rglob("*.md")):
        from scripts.docs.schemas import public_route_for_doc

        assert public_route_for_doc(path, docs_root) in llms


def test_docs_capture_is_opt_in_and_seed_data_is_safe(tmp_path: Path, monkeypatch) -> None:
    from row_bot.docs_capture import (
        is_docs_capture,
        load_docs_capture_demo_state,
        scan_demo_data_safety,
        write_docs_capture_demo_state,
    )

    monkeypatch.delenv("ROW_BOT_DOCS_CAPTURE", raising=False)
    assert not is_docs_capture()

    monkeypatch.setenv("ROW_BOT_DOCS_CAPTURE", "1")
    assert is_docs_capture()
    write_docs_capture_demo_state(tmp_path, scenario="full")
    data = load_docs_capture_demo_state(tmp_path)
    payload = json.dumps(data, sort_keys=True)

    assert "example.com" in payload
    assert "sk-" not in payload
    assert "ghp_" not in payload
    assert "C:\\Users\\" not in payload
    assert "/Users/" not in payload
    assert scan_demo_data_safety(tmp_path) == []


def test_screenshot_manifest_is_real_ui_and_safe() -> None:
    manifest_path = ROOT / "docs-content" / "metadata" / "screenshots.yml"
    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    screenshots = data["screenshots"]

    required = [shot for shot in screenshots.values() if shot["status"] == "required"]
    assert len(screenshots) >= 20
    assert len(required) >= 20
    assert all(shot["status"] in {"required", "deferred"} for shot in screenshots.values())
    assert all(shot.get("alt") for shot in screenshots.values())
    assert all(not shot.get("route", "").startswith("/docs-mode/surface/") for shot in screenshots.values())
    assert all("/docs-mode/" not in shot.get("route", "") for shot in screenshots.values())
    assert all(shot.get("route", "/").startswith("/") for shot in required)
    assert all(shot.get("capture_selector") for shot in required)
    assert all(shot.get("expected_text") for shot in required)


def test_real_home_and_settings_tabs_have_routes() -> None:
    settings = yaml.safe_load((ROOT / "docs-content" / "metadata" / "settings.yml").read_text(encoding="utf-8"))["tabs"]
    home = yaml.safe_load((ROOT / "docs-content" / "metadata" / "home_tabs.yml").read_text(encoding="utf-8"))["tabs"]
    expected_settings = {
        "Providers",
        "Models",
        "Documents",
        "Search",
        "Skills",
        "System",
        "Accounts",
        "Utilities",
        "Tracker",
        "Knowledge",
        "Buddy",
        "Voice",
        "Channels",
        "MCP",
        "Plugins",
        "Preferences",
    }
    expected_home = {"Workflows", "Designer", "Developer", "Knowledge", "Monitor"}
    assert set(settings) == expected_settings
    assert set(home) == expected_home
    assert all(str(meta.get("docs_route", "")).startswith("/docs/") for meta in settings.values())
    assert all(str(meta.get("docs_route", "")).startswith("/docs/") for meta in home.values())


def test_validator_rejects_fake_docs_screenshot_route(monkeypatch) -> None:
    import scripts.docs.validate_public_docs as validator

    original = validator._load_yaml

    def fake_load(path: Path):
        data = original(path)
        if path.name == "screenshots.yml":
            data = json.loads(json.dumps(data))
            first = next(iter(data["screenshots"].values()))
            first["route"] = "/docs-mode/surface/fake"
        return data

    monkeypatch.setattr(validator, "_load_yaml", fake_load)
    assert any("forbidden fake docs route" in error for error in validator.validate())


def test_public_docs_metadata_validates() -> None:
    assert validate() == []
