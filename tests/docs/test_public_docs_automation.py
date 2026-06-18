from pathlib import Path

from scripts.docs.collect_inventory import build_inventory
from scripts.docs.generate_llms_txt import generate
from scripts.docs.validate_public_docs import validate


def test_public_docs_inventory_has_core_sections() -> None:
    inventory = build_inventory()

    assert inventory["version"]["version"] != "unknown"
    assert any(tool["id"] == "browser" for tool in inventory["tools"])
    assert any(provider["id"] == "catalog" for provider in inventory["providers"])
    assert any(skill["id"] == "task_automation" for skill in inventory["skills"])
    assert any(page["path"] == "index.mdx" for page in inventory["docs_pages"])


def test_llms_txt_generation(tmp_path: Path) -> None:
    docs_root = Path(__file__).resolve().parents[2] / "docs-site" / "docs"
    generate(docs_root, tmp_path)

    llms = (tmp_path / "llms.txt").read_text(encoding="utf-8")
    llms_full = (tmp_path / "llms-full.txt").read_text(encoding="utf-8")

    assert "# Row-Bot Docs" in llms
    assert "[Row-Bot Documentation](/docs/)" in llms
    assert "Route: /docs/" in llms_full
    assert (tmp_path / "docs" / "llms.txt").is_file()
    assert (tmp_path / "docs" / "llms-full.txt").is_file()


def test_public_docs_metadata_validates() -> None:
    assert validate() == []
