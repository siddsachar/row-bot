"""The docs site is published once per release; the landing page changes only by hand, after a release.

Pull requests change the docs' sources, not the built site under docs/, so they never conflict over
it. The landing page at row-bot.ai's root is never touched by a publish, and a pull request that
changes it needs the `landing` label.
"""

import io
import sys
from pathlib import Path

import pytest

from scripts.docs import landing_guard

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("path", [
    "docs/index.html", "docs/features.html", "docs/CNAME", "docs/site.css", "docs/landing-story.js",
    "docs/media/landing-story/buddy/approval.webm", "docs/row_bot_hero.webp", "docs/404.html",
])
def test_the_landing_pages_files_are_the_landing_page(path):
    assert landing_guard.is_landing(path) and not landing_guard.is_docs_site(path)


@pytest.mark.parametrize("path", [
    "docs/docs/chat/index.html", "docs/assets/js/runtime~main.1b41e21c.js", "docs/pagefind/pagefind-entry.json",
    "docs/search/index.html", "docs/llms.txt", "docs/sitemap.xml", "docs/oauth/client-metadata.json",
])
def test_the_built_docs_site_is_published_only_by_a_publish(path):
    assert landing_guard.is_docs_site(path) and not landing_guard.is_landing(path)


@pytest.mark.parametrize("path", [
    "docs/RELEASING.md", "docs/qa/react-app-ux-polish/README.md", "docs/client-platform/unified-workspace.md",
    "docs-site/docs/chat/index.mdx", "src/row_bot/app.py",
])
def test_notes_sources_and_code_are_neither(path):
    assert not landing_guard.is_landing(path) and not landing_guard.is_docs_site(path)


def test_every_landing_file_in_the_repository_is_guarded():
    """Each committed file the site serves at its root is the landing page's, or the docs build's."""
    import subprocess

    files = subprocess.run(["git", "ls-files", "docs"], cwd=ROOT, capture_output=True, text=True, check=True)
    served = [path for path in files.stdout.splitlines()
              if not path.endswith(".md") and path.split("/")[1] not in {"qa", "client-platform"}]
    assert {"docs/index.html", "docs/CNAME"} <= set(served)
    assert all(landing_guard.is_landing(path) or landing_guard.is_docs_site(path) for path in served)


def _guard(monkeypatch, capsys, changed: list[str], *args: str) -> tuple[int, str]:
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n".join(changed)))
    code = landing_guard.main(list(args))
    return code, capsys.readouterr().out


def test_a_pull_request_changing_the_landing_page_needs_the_landing_label(monkeypatch, capsys):
    code, said = _guard(monkeypatch, capsys, ["src/row_bot/app.py", "docs/index.html"], "--landing")
    assert code == 1 and "docs/index.html" in said and "`landing` label" in said
    assert _guard(monkeypatch, capsys, ["docs/index.html"], "--landing", "--allowed", "true")[0] == 0
    assert _guard(monkeypatch, capsys, ["docs-site/docs/chat/index.mdx", "docs/docs/chat/index.html"],
                  "--landing")[0] == 0


def test_a_publish_is_a_change_to_the_built_site(monkeypatch, capsys):
    assert _guard(monkeypatch, capsys, ["docs-site/docs/chat/index.mdx"], "--published")[1].strip() == "false"
    assert _guard(monkeypatch, capsys, ["docs/docs/chat/index.html"], "--published")[1].strip() == "true"


def test_a_docs_change_is_checked_against_generated_pages_only_when_published(monkeypatch):
    """Generated reference pages are refreshed at a release, so a pull request editing a page isn't failed by them."""
    from scripts.docs import validate_public_docs as validate

    monkeypatch.setattr(validate, "build_inventory", lambda: {})
    monkeypatch.setattr(validate, "render_pages", lambda inventory: {})
    monkeypatch.setattr(validate, "check_pages", lambda pages: ["Stale generated page: cli.mdx"])
    monkeypatch.delenv("ROW_BOT_DOCS_PUBLISH", raising=False)
    errors: list[str] = []
    validate._validate_generated_pages(errors)
    assert errors == []
    monkeypatch.setenv("ROW_BOT_DOCS_PUBLISH", "1")
    validate._validate_generated_pages(errors)
    assert errors == ["Stale generated page: cli.mdx"]
