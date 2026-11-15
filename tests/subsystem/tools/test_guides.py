"""Row-Bot's own guides, read offline from the copy each install ships (self system, WP1)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from row_bot import guides

pytestmark = pytest.mark.subsystem

SYSTEM_PAGE = """---
title: "Settings: System"
description: "What Row-Bot may do on this computer."
---

import Screenshot from '@site/src/components/Screenshot';

# Settings: System

System settings describe where Row-Bot works.

<Screenshot id="settings-system" alt="Settings: System in Row-Bot." caption="The System page." />

## Where To Find It

Open **Settings**, then choose **System** in the **System** group.

## Privacy

{/* drafted for 5.3 */}
- **Install stats** is one row: **Share install stats** turns it on or off.
- Open **Settings › System › Privacy** to change it.

:::note On a hosted Row-Bot
Your host decides.
:::

## Logs

1. Open **Settings › System**.
2. Raise **Log detail** while troubleshooting.
3. Lower it again.

```bash
# not a heading
row-bot --logs
```

## Logs
A second section with the same heading.
"""

MODELS_PAGE = """---
title: "Settings: Models"
---

# Settings: Models

## What's New In 5.3?

Pin models with the star.
"""


@pytest.fixture
def docs(tmp_path: Path) -> Path:
    root = tmp_path / "docs-site" / "docs"
    (root / "settings").mkdir(parents=True)
    (root / "settings" / "system.mdx").write_text(SYSTEM_PAGE, encoding="utf-8")
    (root / "settings" / "models.mdx").write_text(MODELS_PAGE, encoding="utf-8")
    (root / "index.mdx").write_text("---\ntitle: Row-Bot Documentation\n---\n\nStart here.\n", encoding="utf-8")
    return root


def test_a_how_to_question_finds_the_section_that_answers_it_with_its_steps(docs):
    best = guides.search("how do I turn off install stats", root=docs)[0]

    assert best.link == "/docs/settings/system#privacy"
    assert best.title == "Settings: System › Privacy"
    # Lines, lists and the › separators stay as the guide wrote them.
    assert best.text.splitlines()[:2] == [
        "- **Install stats** is one row: **Share install stats** turns it on or off.",
        "- Open **Settings › System › Privacy** to change it.",
    ]
    assert "**Note: On a hosted Row-Bot**" in best.text and ":::" not in best.text
    assert "drafted" not in best.text


def test_steps_and_code_survive_and_a_hash_in_code_is_not_a_heading(docs):
    logs = guides.read("/docs/settings/system#logs", root=docs)

    assert logs.text.splitlines()[:3] == [
        "1. Open **Settings › System**.",
        "2. Raise **Log detail** while troubleshooting.",
        "3. Lower it again.",
    ]
    assert "# not a heading" in logs.text


def test_anchors_are_the_ones_the_docs_site_gives(docs):
    system = guides.read("/docs/settings/system", root=docs)
    models = guides.read("/docs/settings/models", root=docs)

    assert [section.anchor for section in system.sections] == ["", "where-to-find-it", "privacy", "logs", "logs-1"]
    assert [section.anchor for section in models.sections] == ["whats-new-in-53"]


def test_a_page_reads_whole_without_the_site_code(docs):
    page = guides.read("/docs/settings/system/", root=docs)

    assert page.title == "Settings: System" and page.description == "What Row-Bot may do on this computer."
    assert page.group == "settings"
    assert page.text.startswith("System settings describe where Row-Bot works.\n\n## Where To Find It")
    for site_code in ("import Screenshot", "<Screenshot", "# Settings: System"):
        assert site_code not in page.text


def test_unknown_routes_and_unrelated_questions_find_nothing(docs):
    assert guides.read("/docs/settings/nowhere", root=docs) is None
    assert guides.read("/docs/settings/system#nowhere", root=docs) is None
    assert guides.read("https://example.com/docs/settings/system", root=docs) is None
    assert guides.search("zebra quantum", root=docs) == []
    assert guides.search("how do I", root=docs) == []


def test_an_install_without_guides_says_so_instead_of_guessing(tmp_path):
    with pytest.raises(guides.GuidesUnavailable, match="no guides"):
        guides.search("install stats", root=tmp_path / "missing")


def test_an_edited_guide_is_read_again(docs):
    assert guides.search("telegram", root=docs) == []
    page = docs / "settings" / "system.mdx"
    page.write_text(SYSTEM_PAGE + "\n## Telegram\n\nScan the code.\n", encoding="utf-8")
    later = page.stat().st_mtime + 5
    os.utime(page, (later, later))

    assert guides.search("telegram", root=docs)[0].link == "/docs/settings/system#telegram"


def test_every_shipped_guide_page_reads_into_sections():
    """The guides this checkout ships (the installers copy the same folder): none lost to an unclosed
    component or a broken front matter."""
    root = guides.guides_dir()
    files = sorted(path for path in root.rglob("*") if path.suffix in {".md", ".mdx"})
    pages = guides.pages(root)

    assert files and len(pages) == len(files)
    for page in pages:
        assert page.route.startswith("/docs/") and page.title
        assert any(section.text for section in page.sections), page.route
