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


def test_docs_mode_is_opt_in_and_seed_data_is_safe(tmp_path: Path, monkeypatch) -> None:
    from row_bot.docs_mode import is_docs_mode, load_docs_demo_state, write_docs_demo_state

    monkeypatch.delenv("ROW_BOT_DOCS_MODE", raising=False)
    assert not is_docs_mode()

    monkeypatch.setenv("ROW_BOT_DOCS_MODE", "1")
    assert is_docs_mode()
    write_docs_demo_state(tmp_path, scenario="full")
    data = load_docs_demo_state(tmp_path)
    payload = json.dumps(data, sort_keys=True)

    assert "example.com" in payload
    assert "sk-" not in payload
    assert "ghp_" not in payload
    assert "C:\\Users\\" not in payload
    assert "/Users/" not in payload


def test_screenshot_manifest_is_executable_and_safe() -> None:
    manifest_path = ROOT / "docs-content" / "metadata" / "screenshots.yml"
    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    screenshots = data["screenshots"]

    assert len(screenshots) >= 20
    assert all(shot["status"] in {"required", "deferred"} for shot in screenshots.values())
    assert all(shot.get("alt") for shot in screenshots.values())
    assert all(shot.get("route", "").startswith("/docs-mode/surface/") for shot in screenshots.values() if shot["status"] == "required")
    assert all(shot.get("capture_selector") for shot in screenshots.values() if shot["status"] == "required")


def test_public_docs_metadata_validates() -> None:
    assert validate() == []
