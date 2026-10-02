from __future__ import annotations

import io
import json
import importlib
import zipfile

import pytest

from row_bot.skills_hub import browse_sh_source, clawhub_source, skills_sh_source
from row_bot.skills_hub.browse_sh_source import github_blob_to_raw, parse_browse_sh_catalog
from row_bot.skills_hub.claude_marketplace_source import parse_claude_marketplace_manifest
from row_bot.skills_hub.clawhub_source import ClawHubSource, bundle_from_clawhub_zip, parse_clawhub_payload
from row_bot.skills_hub.github_source import GitHubSource, fair_merge_root_entries, list_matching_skill_entries, list_public_root_entries
from row_bot.skills_hub.lobehub_source import _bundle_from_lobehub_entry, load_lobehub_agent_detail, parse_lobehub_index
from row_bot.skills_hub.models import SkillBundle, SkillFile, SkillHubEntry
from row_bot.skills_hub.skills_sh_source import parse_skills_sh_payload, parse_skills_sh_sitemap, parse_skills_sh_sitemap_index
from row_bot.skills_hub.sources import bundle_from_marketplace_markdown, compute_bundle_hash


def _skill_md(name: str) -> str:
    return f"---\nname: {name}\ndescription: Demo\n---\n\nInstructions for {name}."


def _bundle(name: str, *, source: str = "github", install_ref: str = "github:example/repo/skills/demo") -> SkillBundle:
    files = [SkillFile.from_text("SKILL.md", _skill_md(name), kind="markdown")]
    return SkillBundle(
        source=source,
        install_ref=install_ref,
        root_name=name,
        primary_skill_path="SKILL.md",
        files=files,
        frontmatter={"name": name, "description": "Demo"},
        instructions=f"Instructions for {name}.",
        content_hash=compute_bundle_hash(files),
        metadata={},
    )


def test_skills_sh_payload_and_sitemap_parsing():
    entries = parse_skills_sh_payload({
        "results": [
            {
                "id": "research",
                "name": "Research Brief",
                "description": "Research sources",
                "skillMdUrl": "https://example.test/research/SKILL.md",
                "tags": ["research"],
                "installCount": 42,
            }
        ]
    })
    sitemap = parse_skills_sh_sitemap(
        "<urlset><url><loc>https://www.skills.sh/github/awesome-copilot/browser-helper</loc></url></urlset>"
    )

    assert entries[0].source == "skills_sh"
    assert entries[0].install_ref.endswith("SKILL.md")
    assert entries[0].metadata["install_count"] == 42
    assert sitemap[0].name == "Browser Helper"
    assert sitemap[0].install_ref == "skills_sh:github/awesome-copilot/browser-helper"


def test_skills_sh_sitemap_index_walks_child_skill_maps(monkeypatch):
    current_skills_sh = importlib.import_module("row_bot.skills_hub.skills_sh_source")
    calls: list[str] = []

    def fake_fetch_text(url, **_kwargs):
        calls.append(url)
        if url.endswith("sitemap.xml"):
            return """
            <sitemapindex>
              <sitemap><loc>https://www.skills.sh/sitemap-skills-1.xml</loc></sitemap>
            </sitemapindex>
            """
        return """
        <urlset>
          <url><loc>https://www.skills.sh/github/awesome-copilot/python-helper</loc></url>
          <url><loc>https://www.skills.sh/wshobson/agents/python-performance-optimization</loc></url>
        </urlset>
        """

    monkeypatch.setattr(current_skills_sh, "fetch_text", fake_fetch_text)

    entries = current_skills_sh.fetch_skills_sh_sitemap_catalog(limit=2)

    assert parse_skills_sh_sitemap_index("<sitemap><loc>https://www.skills.sh/sitemap-skills-1.xml</loc></sitemap>")
    assert calls == ["https://www.skills.sh/sitemap.xml", "https://www.skills.sh/sitemap-skills-1.xml"]
    assert [entry.install_ref for entry in entries] == [
        "skills_sh:github/awesome-copilot/python-helper",
        "skills_sh:wshobson/agents/python-performance-optimization",
    ]


def test_skills_sh_live_shape_uses_source_and_skill_id_ref():
    entries = parse_skills_sh_payload({
        "skills": [
            {
                "id": "github/awesome-copilot/python-mcp-server-generator",
                "skillId": "python-mcp-server-generator",
                "name": "python-mcp-server-generator",
                "installs": 9573,
                "source": "github/awesome-copilot",
            }
        ]
    })

    assert entries[0].install_ref == "skills_sh:github/awesome-copilot/python-mcp-server-generator"
    assert entries[0].url == "https://www.skills.sh/github/awesome-copilot/python-mcp-server-generator"
    assert entries[0].metadata["source"] == "github/awesome-copilot"
    assert entries[0].metadata["skill_id"] == "python-mcp-server-generator"


def test_skills_sh_extracts_escaped_next_preview_html():
    html = r'''
    <script>self.__next_f.push([1,"{\"previewHtml\":\"\u003ch1\u003eFind Skills\u003c/h1\u003e\\n\u003cp\u003eUse \\\"quoted\\\" search terms.\u003c/p\u003e\\n\u003cul\u003e\\n\u003cli\u003eFind things\u003c/li\u003e\\n\u003c/ul\u003e\",\"restHtml\":\"$2b\"}"])</script>
    '''

    markdown = skills_sh_source.extract_skills_sh_preview_markdown(html, name="find-skills")

    assert markdown.startswith("# Find Skills")
    assert 'Use "quoted" search terms.' in markdown
    assert "- Find things" in markdown


def test_browse_sh_catalog_parsing():
    entries = parse_browse_sh_catalog({
        "skills": [
            {
                "slug": "browser-helper",
                "name": "Browser Helper",
                "description": "Browse sites",
                "skillMdUrl": "https://example.test/browser/SKILL.md",
                "tags": ["browser"],
            }
        ]
    })

    assert entries[0].source == "browse_sh"
    assert entries[0].url == "https://browse.sh/skills/browser-helper"
    assert entries[0].tags == ["browser"]


def test_browse_sh_live_shape_preserves_source_url_and_raw_conversion():
    entries = parse_browse_sh_catalog({
        "skills": [
            {
                "slug": "alltrails.com/search-trails-dsqvnx",
                "title": "AllTrails Search Trails",
                "description": "Search trails",
                "sourceUrl": "https://github.com/browserbase/browse.sh/blob/main/skills/alltrails.com/search-trails-dsqvnx/SKILL.md",
            }
        ]
    })

    assert entries[0].install_ref == "browse_sh:alltrails.com/search-trails-dsqvnx"
    assert entries[0].metadata["source_url"].endswith("/SKILL.md")
    assert github_blob_to_raw(entries[0].metadata["source_url"]) == (
        "https://raw.githubusercontent.com/browserbase/browse.sh/main/skills/alltrails.com/search-trails-dsqvnx/SKILL.md"
    )


def test_lobehub_agents_convert_to_passive_skill_bundle():
    payload = {
        "agents": [
                {
                    "id": "reviewer",
                    "title": "Review Coach",
                    "description": "Review code",
                    "prompt": "Give careful code review feedback with concrete risks, examples, and suggested tests.",
                    "tags": ["code"],
                }
        ]
    }
    entries = parse_lobehub_index(payload)

    bundle = _bundle_from_lobehub_entry(entries[0])

    assert entries[0].metadata["converted_from"] == "lobehub_agent"
    assert bundle.source == "lobehub"
    assert bundle.frontmatter["name"] == "review_coach"
    assert "Converted from a public LobeHub agent profile" in bundle.instructions
    assert "Give careful code review feedback with concrete risks" in bundle.instructions


def test_lobehub_fetches_nested_config_prompt_when_index_is_shallow(monkeypatch):
    current_lobehub = importlib.import_module("row_bot.skills_hub.lobehub_source")
    payload = {
        "agents": [
            {
                "identifier": "academic-writing-assistant",
                "author": "swarfte",
                "homepage": "https://github.com/swarfte",
                "meta": {
                    "title": "Academic Writing Assistant",
                    "description": "Expert in academic writing",
                    "tags": ["academic"],
                },
            }
        ]
    }
    entries = current_lobehub.parse_lobehub_index(payload)

    calls: list[str] = []

    def fake_fetch_json(url):
        calls.append(url)
        return {
        "author": "swarfte",
        "config": {"systemRole": "You are a careful academic writing assistant with detailed citation guidance."},
        "meta": {"title": "Academic Writing Assistant", "description": "Academic writing", "tags": ["academic"]},
        }

    monkeypatch.setattr(current_lobehub, "fetch_json", fake_fetch_json)

    detailed = current_lobehub.load_lobehub_agent_detail(entries[0])
    bundle = current_lobehub._bundle_from_lobehub_entry(detailed)

    assert calls == ["https://chat-agents.lobehub.com/academic-writing-assistant.json"]
    assert detailed.url.endswith("/academic-writing-assistant.json")
    assert "careful academic writing assistant" in bundle.instructions
    assert bundle.metadata["raw_url"] == "https://chat-agents.lobehub.com/academic-writing-assistant.json"
    assert bundle.metadata["source_url"].endswith("/src/academic-writing-assistant.json")


def test_lobehub_rejects_entries_without_substantial_prompt():
    entry = parse_lobehub_index({
        "agents": [
            {"identifier": "empty", "meta": {"title": "Empty", "description": "Only a description"}}
        ]
    })[0]

    with pytest.raises(ValueError, match="substantial agent instructions"):
        _bundle_from_lobehub_entry(entry)


def test_claude_marketplace_manifest_parsing():
    entries = parse_claude_marketplace_manifest(
        {
            "publisher": "Example Publisher",
            "skills": [
                {
                    "id": "research",
                    "name": "Researcher",
                    "description": "Research workflow",
                    "path": ".claude/skills/researcher",
                    "tags": ["research"],
                }
            ],
        },
        owner="example",
        repo="skills",
        ref="main",
    )

    assert entries[0].source == "github"
    assert entries[0].install_ref == "github:example/skills/.claude/skills/researcher?ref=main"
    assert entries[0].metadata["manifest"] == ".claude-plugin/marketplace.json"
    assert entries[0].metadata["source_adapter"] == "claude_marketplace"


def test_claude_marketplace_manifest_parses_plugins_and_relative_skill_paths():
    entries = parse_claude_marketplace_manifest(
        {
            "publisher": "Example Publisher",
            "plugins": [
                {
                    "id": "cloud",
                    "name": "Cloud Skills",
                    "skills": [
                        {
                            "name": "Cloud Runner",
                            "description": "Run cloud workflows",
                            "path": "./skills/cloud-runner",
                            "tags": ["cloud"],
                        },
                        "./skills/reviewer",
                    ],
                }
            ],
        },
        owner="example",
        repo="skills",
        ref="main",
    )

    assert [entry.name for entry in entries] == ["Cloud Runner", "Reviewer"]
    assert entries[0].install_ref == "github:example/skills/skills/cloud-runner?ref=main"
    assert entries[1].install_ref == "github:example/skills/skills/reviewer?ref=main"
    assert entries[0].metadata["plugin"] == "Cloud Skills"
    assert entries[0].source == "github"
    assert entries[0].metadata["manifest_badge"] == "Claude manifest"


def test_marketplace_markdown_without_frontmatter_is_wrapped_before_bundle_parse():
    bundle = bundle_from_marketplace_markdown(
        source="browse_sh",
        install_ref="browse_sh:demo",
        root_name="demo",
        text="# Demo Skill\n\n## When to use\nUse it.\n\n## Instructions\nDo the thing.",
        name="Demo Skill",
        description="Imported from a marketplace",
        metadata={"source_name": "browse.sh"},
    )

    assert bundle.frontmatter["name"] == "demo_skill"
    assert bundle.frontmatter["description"] == "Imported from a marketplace"
    assert "Do the thing" in bundle.instructions
    assert bundle.metadata["source_name"] == "browse.sh"


def test_skills_sh_fetch_resolves_detail_page_install_command(monkeypatch):
    current_skills_sh = importlib.import_module("row_bot.skills_hub.skills_sh_source")
    current_github = importlib.import_module("row_bot.skills_hub.github_source")
    html = """
    <html><body>
      <code>npx skills add example/skills --skill research-brief</code>
    </body></html>
    """
    seen = {}

    monkeypatch.setattr(current_skills_sh, "fetch_text", lambda url: html)

    def fake_fetch(self, install_ref):
        seen["install_ref"] = install_ref
        return _bundle("research_brief", install_ref=install_ref)

    monkeypatch.setattr(current_github.GitHubSource, "fetch", fake_fetch)

    bundle = current_skills_sh.SkillsShSource().fetch("https://www.skills.sh/skills/research-brief")

    assert seen["install_ref"] == "github:example/skills/research-brief"
    assert bundle.metadata["source_name"] == "skills.sh"


def test_skills_sh_source_ref_uses_recursive_github_lookup(monkeypatch):
    current_skills_sh = importlib.import_module("row_bot.skills_hub.skills_sh_source")
    current_github = importlib.import_module("row_bot.skills_hub.github_source")
    seen = {}

    def fake_fetch(self, install_ref):
        seen.setdefault("fetches", []).append(install_ref)
        if "plugins/python-development" not in install_ref:
            raise RuntimeError("wrong layout")
        return _bundle("python_performance_optimization", install_ref=install_ref)

    def fake_find(self, parsed, skill):
        seen["find"] = (parsed.repo_full_name, skill)
        return SkillHubEntry(
            id="github:wshobson/agents:plugins/python-development/skills/python-performance-optimization:main",
            name="Python Performance Optimization",
            description="Deep skill",
            source="github",
            source_id="wshobson/agents",
            install_ref="github:wshobson/agents/plugins/python-development/skills/python-performance-optimization?ref=main",
        )

    def no_raw_copy(url, **_kwargs):
        raise RuntimeError("raw missing")

    monkeypatch.setattr(current_skills_sh, "fetch_text", no_raw_copy)
    monkeypatch.setattr(current_github.GitHubSource, "fetch", fake_fetch)
    monkeypatch.setattr(current_github.GitHubSource, "find_skill_by_name", fake_find)

    bundle = current_skills_sh.SkillsShSource().fetch("skills_sh:wshobson/agents/python-performance-optimization")

    assert seen["find"] == ("wshobson/agents", "python-performance-optimization")
    assert bundle.install_ref == "github:wshobson/agents/plugins/python-development/skills/python-performance-optimization?ref=main"
    assert bundle.metadata["skills_sh_source"] == "wshobson/agents"


def test_skills_sh_self_resolving_detail_page_uses_preview_fallback(monkeypatch):
    current_skills_sh = importlib.import_module("row_bot.skills_hub.skills_sh_source")
    current_github = importlib.import_module("row_bot.skills_hub.github_source")
    html = r'''
    <code>npx skills add vercel-labs/skills --skill find-skills</code>
    <script>self.__next_f.push([1,"{\"previewHtml\":\"\u003ch1\u003eFind Skills\u003c/h1\u003e\\n\u003cp\u003eDiscover installable public skills.\u003c/p\u003e\",\"restHtml\":\"$2b\"}"])</script>
    '''

    def fake_fetch_text(url, **_kwargs):
        if str(url).startswith("https://www.skills.sh/"):
            return html
        raise RuntimeError("raw missing")

    monkeypatch.setattr(current_skills_sh, "fetch_text", fake_fetch_text)
    monkeypatch.setattr(current_github.GitHubSource, "fetch", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("github blocked")))
    monkeypatch.setattr(current_github.GitHubSource, "find_skill_by_name", lambda *_args, **_kwargs: None)

    bundle = current_skills_sh.SkillsShSource().fetch("skills_sh:vercel-labs/skills/find-skills")

    assert bundle.install_ref == "skills_sh:vercel-labs/skills/find-skills"
    assert bundle.frontmatter["name"] == "find_skills"
    assert "Discover installable public skills" in bundle.instructions


def test_skills_sh_uses_raw_github_skill_md_when_api_listing_is_unavailable(monkeypatch):
    current_skills_sh = importlib.import_module("row_bot.skills_hub.skills_sh_source")
    current_github = importlib.import_module("row_bot.skills_hub.github_source")
    calls: list[str] = []

    def fake_fetch_text(url, **_kwargs):
        calls.append(url)
        if url == "https://raw.githubusercontent.com/github/awesome-copilot/main/skills/python-helper/SKILL.md":
            return "---\nname: python_helper\n---\n\nUse Python carefully."
        raise RuntimeError("missing")

    monkeypatch.setattr(current_skills_sh, "fetch_text", fake_fetch_text)
    monkeypatch.setattr(current_github.GitHubSource, "fetch", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("api blocked")))
    monkeypatch.setattr(current_github.GitHubSource, "find_skill_by_name", lambda *_args, **_kwargs: None)

    bundle = current_skills_sh.SkillsShSource().fetch("skills_sh:github/awesome-copilot/python-helper")

    assert bundle.frontmatter["name"] == "python_helper"
    assert bundle.metadata["source_warning"].startswith("Fetched SKILL.md directly")
    assert "https://raw.githubusercontent.com/github/awesome-copilot/main/skills/python-helper/SKILL.md" in calls


def test_browse_sh_preserves_full_detail_route_and_extracts_inline_markdown(monkeypatch):
    entries = parse_browse_sh_catalog({
        "skills": [
            {
                "name": "Pinball Explorer",
                "description": "Explore pinball content",
                "url": "/skills/kineticist.com/explore-pinball-content-w5vgkf",
            }
        ]
    })

    assert entries[0].install_ref == "browse_sh:kineticist.com/explore-pinball-content-w5vgkf"

    html = """
    <html><body>
      <script type="application/json">
        {"skillMd":"# Pinball Explorer\\n\\n## When to use\\nUse for pinball.\\n\\n## Instructions\\nBrowse carefully."}
      </script>
    </body></html>
    """
    monkeypatch.setattr(browse_sh_source, "fetch_text", lambda url: html)
    monkeypatch.setattr(browse_sh_source, "fetch_json", lambda url: (_ for _ in ()).throw(RuntimeError("api 404")))

    bundle = browse_sh_source.BrowseShSource().fetch(entries[0].install_ref)

    assert bundle.frontmatter["name"] == "pinball_explorer"
    assert "Browse carefully." in bundle.instructions


def test_browse_sh_fetch_uses_unencoded_detail_route_first(monkeypatch):
    calls: list[str] = []
    detail = {
        "name": "search-trails",
        "title": "AllTrails Search Trails",
        "description": "Search trails",
        "skillMd": "---\nname: search-trails\n---\n\nTrail instructions.",
    }

    def fake_fetch_json(url):
        calls.append(url)
        assert "%2F" not in url
        return detail

    monkeypatch.setattr(browse_sh_source, "fetch_json", fake_fetch_json)

    bundle = browse_sh_source.BrowseShSource().fetch("browse_sh:alltrails.com/search-trails-dsqvnx")

    assert calls == ["https://browse.sh/api/skills/alltrails.com/search-trails-dsqvnx"]
    assert bundle.frontmatter["name"] == "search-trails"
    assert "Trail instructions" in bundle.instructions


def test_browse_sh_search_uses_full_catalog_not_first_page(monkeypatch):
    catalog = {
        "skills": [
            {"slug": f"example.com/early-{idx}", "name": f"Early {idx}", "description": "Unrelated"}
            for idx in range(120)
        ] + [
            {"slug": "deep.example.com/rare-result", "name": "Rare Result", "description": "Needle target"}
        ]
    }
    monkeypatch.setattr(browse_sh_source, "fetch_json", lambda _url: catalog)

    entries = browse_sh_source.BrowseShSource().search("needle", limit=5)

    assert [entry.install_ref for entry in entries] == ["browse_sh:deep.example.com/rare-result"]


def test_clawhub_fetch_pins_complete_bundle(monkeypatch):
    calls = []
    data = _zip_bytes({"SKILL.md": _skill_md("shellish").encode(),
                       "references/guide.md": b"Guide", "assets/pixel.png": b"\x89PNG\x00"})
    monkeypatch.setattr(clawhub_source, "fetch_json", lambda url: {"skill": {"slug": "shellish"}, "latestVersion": {"version": "1.2.3"}})
    monkeypatch.setattr(clawhub_source, "fetch_bytes", lambda url: calls.append(url) or data)
    monkeypatch.setattr(clawhub_source, "fetch_text", lambda url: pytest.fail("Never fetch single-file fallback"))
    bundle = ClawHubSource().fetch("clawhub:shellish")
    assert calls == ["https://clawhub.ai/api/v1/download?slug=shellish&version=1.2.3"]
    assert bundle.install_ref == "clawhub:shellish@1.2.3"
    assert bundle.metadata["version"] == "1.2.3"
    assert bundle.file_tree() == ["SKILL.md", "assets/pixel.png", "references/guide.md"]
    assert next(f.content for f in bundle.files if f.path.endswith(".png")) == b"\x89PNG\x00"


def test_clawhub_failed_bundle_does_not_fall_back(monkeypatch):
    monkeypatch.setattr(clawhub_source, "fetch_json", lambda url: {"latestVersion": {"version": "1"}})
    monkeypatch.setattr(clawhub_source, "fetch_bytes", lambda url: b"deleted version")
    monkeypatch.setattr(clawhub_source, "fetch_text", lambda url: pytest.fail("Never fetch fallback"))
    with pytest.raises(zipfile.BadZipFile):
        ClawHubSource().fetch("clawhub:shellish")


def test_clawhub_search_uses_public_search_endpoint(monkeypatch):
    calls = []
    monkeypatch.setattr(clawhub_source, "fetch_json", lambda url: calls.append(url) or {"results": [{"slug": "shellish"}]})
    assert ClawHubSource().search("shellish")
    assert calls == ["https://clawhub.ai/api/v1/search?q=shellish&limit=24"]


def test_clawhub_search_keeps_publishers_and_display_names_distinct():
    # Public v1 cards use displayName + ownerHandle; slugs are not globally unique.
    entries = parse_clawhub_payload({"results": [
        {"id": "clawhub:opaque-a", "slug": "shared", "displayName": "Helpful skill", "ownerHandle": "alice"},
        {"id": "clawhub:opaque-b", "slug": "shared", "displayName": "Different skill", "ownerHandle": "bob"},
    ]})
    assert [entry.name for entry in entries] == ["Helpful skill", "Different skill"]
    assert [entry.install_ref for entry in entries] == ["clawhub:alice/shared", "clawhub:bob/shared"]
    assert len({entry.id for entry in entries}) == 2
    assert entries[0].url == "https://clawhub.ai/alice/skills/shared"


@pytest.mark.parametrize("qualified", [True, False])
def test_clawhub_pins_the_resolved_publisher_for_download_and_future_updates(monkeypatch, qualified):
    calls = []
    detail = {"skill": {"slug": "shared", "displayName": "Helpful skill"},
        "owner": {"handle": "alice"}, "latestVersion": {"version": "1.0.0"}}
    monkeypatch.setattr(clawhub_source, "fetch_json", lambda url: calls.append(url) or detail)
    archive = _zip_bytes({"SKILL.md": _skill_md("shared").encode(), "references/guide.md": b"Guide"})
    monkeypatch.setattr(clawhub_source, "fetch_bytes", lambda url: calls.append(url) or archive)
    source = ClawHubSource()
    reference = "clawhub:alice/shared" if qualified else "clawhub:shared"
    if qualified:
        assert source.resolve("https://clawhub.ai/alice/skills/shared").entries[0].install_ref == reference
    bundle = source.fetch(reference)
    assert calls[-2] == "https://clawhub.ai/api/v1/skills/shared" + ("?owner=alice" if qualified else "")
    assert calls[-1] == "https://clawhub.ai/api/v1/download?slug=shared&version=1.0.0&owner=alice"
    assert bundle.install_ref == "clawhub:alice/shared@1.0.0"
    assert bundle.metadata["author"] == "alice"


def test_clawhub_cannot_substitute_another_publisher(monkeypatch):
    monkeypatch.setattr(clawhub_source, "fetch_json", lambda url: {"owner": {"handle": "bob"}})
    monkeypatch.setattr(clawhub_source, "fetch_bytes", lambda url: pytest.fail("Must not download another publisher"))
    with pytest.raises(ValueError, match="different publisher"):
        ClawHubSource().fetch("clawhub:alice/shared@1.0.0")


def test_clawhub_live_moderation_shape_blocks_acquisition(monkeypatch):
    monkeypatch.setattr(clawhub_source, "fetch_json", lambda url: {"moderation": {"isMalwareBlocked": True}})
    monkeypatch.setattr(clawhub_source, "fetch_bytes", lambda url: pytest.fail("Must not download blocked content"))
    with pytest.raises(clawhub_source.ClawHubSourceBlocked):
        ClawHubSource().fetch("clawhub:shared")


@pytest.mark.parametrize("path", ["/outside", "C:/outside", "a:stream", "a/../b", "a/CON.txt", "a/trailing.", "a\\outside"])
def test_clawhub_rejects_ambiguous_paths(path):
    with pytest.raises(ValueError):
        bundle_from_clawhub_zip(_zip_bytes({"SKILL.md": _skill_md("test").encode(), path: b"x"}), install_ref="clawhub:test@1")


def test_clawhub_github_handoff_is_bound_to_commit_and_subdirectory(monkeypatch):
    sha = "a" * 40
    descriptor = {"sourceRef": "public-github", "repo": "example/skills", "commit": sha,
                  "path": "skills/test", "contentHash": "upstream", "archiveUrl": f"https://codeload.github.com/example/skills/zip/{sha}"}
    archive = _zip_bytes({"repo/skills/test/SKILL.md": _skill_md("test").encode(),
                          "repo/skills/test/assets/pixel.png": b"PNG", "repo/other.txt": b"unrelated"})
    calls = []
    monkeypatch.setattr(clawhub_source, "fetch_json", lambda url: {"latestVersion": {"version": "1"}})
    monkeypatch.setattr(clawhub_source, "fetch_bytes", lambda url: calls.append(url) or (archive if "codeload" in url else json.dumps(descriptor).encode()))
    bundle = ClawHubSource().fetch("clawhub:test")
    assert bundle.file_tree() == ["SKILL.md", "assets/pixel.png"]
    assert bundle.metadata["ref"] == sha
    descriptor["commit"] = "main"
    with pytest.raises(ValueError, match="not pinned"):
        ClawHubSource().fetch("clawhub:test")


def test_clawhub_payload_marks_high_risk():
    entries = parse_clawhub_payload({
        "skills": [
            {
                "slug": "shellish",
                "name": "Shellish",
                "description": "Risky",
                "zipUrl": "https://example.test/shellish.zip",
            }
        ]
    })

    assert entries[0].source == "clawhub"
    assert entries[0].trust_level == "high-risk community"
    assert entries[0].metadata["risk"] == "high"


def test_clawhub_zip_bundle_strips_common_root_and_preserves_files():
    data = _zip_bytes({
        "skill/SKILL.md": _skill_md("zip_skill").encode("utf-8"),
        "skill/references/guide.md": b"Guide",
    })

    bundle = bundle_from_clawhub_zip(data, install_ref="https://example.test/zip_skill.zip")

    assert bundle.primary_skill_path == "SKILL.md"
    assert bundle.file_tree() == ["SKILL.md", "references/guide.md"]
    assert bundle.metadata["risk"] == "high"


def test_clawhub_zip_blocks_path_traversal():
    data = _zip_bytes({
        "skill/SKILL.md": _skill_md("bad_zip").encode("utf-8"),
        "skill/../escape.txt": b"nope",
    })

    with pytest.raises(ValueError):
        bundle_from_clawhub_zip(data, install_ref="https://example.test/bad.zip")


def test_recursive_github_public_root_discovers_nested_skills_and_skips_symlinks():
    tree = {
        "tree": [
            {"path": "skills/cloud/cloud-run/SKILL.md", "type": "blob"},
            {"path": "skills/cloud/cloud-run/references/guide.md", "type": "blob"},
            {"path": "skills/cloud/gke/SKILL.md", "type": "blob"},
            {"path": "skills/linked/SKILL.md", "type": "symlink"},
        ]
    }

    entries = list_public_root_entries(
        tree,
        owner="google",
        repo="skills",
        root="skills",
        ref="main",
        publisher="Google",
        trust_level="trusted_publisher",
        max_depth=4,
        tags=["google"],
    )

    assert [entry.name for entry in entries] == ["Cloud Run", "Gke"]
    assert entries[0].install_ref == "github:google/skills/skills/cloud/cloud-run?ref=main"
    assert entries[0].author == "Google"
    assert entries[0].trust_level == "trusted_publisher"
    assert entries[0].metadata["publisher"] == "Google"


def test_recursive_github_matching_finds_deep_skill_folder():
    tree = {
        "tree": [
            {"path": "plugins/python-development/skills/python-performance-optimization/SKILL.md", "type": "blob"},
            {"path": "plugins/python-development/skills/other/SKILL.md", "type": "blob"},
        ]
    }

    entries = list_matching_skill_entries(
        tree,
        owner="wshobson",
        repo="agents",
        ref="main",
        target="python-performance-optimization",
    )

    assert len(entries) == 1
    assert entries[0].install_ref == "github:wshobson/agents/plugins/python-development/skills/python-performance-optimization?ref=main"


def test_github_public_root_results_are_fair_merged_across_publishers():
    nvidia = [
        SkillHubEntry(
            id=f"nvidia:{index}",
            name=f"NVIDIA {index}",
            description="CUDA",
            source="github",
            source_id="NVIDIA/skills",
            install_ref=f"github:NVIDIA/skills/skills/nvidia-{index}",
            metadata={"canonical_url": f"https://github.com/NVIDIA/skills/{index}", "publisher": "NVIDIA"},
        )
        for index in range(3)
    ]
    google = [
        SkillHubEntry(
            id=f"google:{index}",
            name=f"Google {index}",
            description="Cloud",
            source="github",
            source_id="google/skills",
            install_ref=f"github:google/skills/skills/google-{index}",
            metadata={"canonical_url": f"https://github.com/google/skills/{index}", "publisher": "Google"},
        )
        for index in range(3)
    ]

    merged = fair_merge_root_entries([nvidia, google], limit=4)

    assert [entry.name for entry in merged] == ["NVIDIA 0", "Google 0", "NVIDIA 1", "Google 1"]


def test_github_source_uses_public_safe_headers(monkeypatch):
    calls: list[str] = []

    def fake_public_headers(*, user_agent: str, include_cli: bool = True):
        calls.append(user_agent)
        return {"User-Agent": user_agent, "X-Test": "public-safe"}

    monkeypatch.setattr("row_bot.skills_hub.github_source.github_account.github_public_api_headers", fake_public_headers)

    headers = GitHubSource()._headers()

    assert headers["X-Test"] == "public-safe"
    assert calls == ["Row-Bot-Skills-Hub/1.0"]


def test_github_source_reports_anonymous_fallback_status(monkeypatch):
    import row_bot.github_account as github_account

    status = github_account.GitHubAccountStatus(
        connected=False,
        source="github_cli",
        state=github_account.GITHUB_STATE_INVALID_TOKEN,
        message="GitHub token is invalid.",
        settings_message="GitHub token is invalid.",
        anonymous_ok=True,
    )
    monkeypatch.setattr(
        "row_bot.skills_hub.github_source.github_account.get_verified_github_account_status",
        lambda use_cache=True: status,
    )

    assert "using anonymous public GitHub access" in GitHubSource()._auth_status_message()


def test_github_browse_reports_rate_limit_as_source_status(monkeypatch):
    import urllib.error
    from email.message import Message

    headers = Message()
    headers["x-ratelimit-limit"] = "60"
    headers["x-ratelimit-remaining"] = "0"
    headers["x-ratelimit-reset"] = "1893456000"
    err = urllib.error.HTTPError(
        "https://api.github.com/rate",
        403,
        "API rate limit exceeded",
        headers,
        None,
    )

    monkeypatch.setattr("row_bot.skills_hub.github_source._GITHUB_BACKOFF_UNTIL", 0)
    monkeypatch.setattr("row_bot.skills_hub.github_source._GITHUB_BACKOFF_MESSAGE", "")
    monkeypatch.setattr(GitHubSource, "_list_public_root", lambda self, root, limit: (_ for _ in ()).throw(err))
    monkeypatch.setattr(GitHubSource, "_auth_status_message", lambda self: "")

    result = GitHubSource().browse(limit=10)

    assert result.status == "error"
    assert "rate limit" in result.message.lower()
    assert "Settings -> Accounts" in result.message


def _quiet_github(monkeypatch, fetch_json) -> list:
    from row_bot.skills_hub import github_source

    monkeypatch.setattr(github_source, "fetch_json", fetch_json)
    monkeypatch.setattr(github_source.github_account, "github_public_api_headers", lambda **_kwargs: {})
    monkeypatch.setattr(GitHubSource, "_auth_status_message", lambda self: "")
    monkeypatch.setattr(github_source, "_GITHUB_BACKOFF_UNTIL", 0)
    return [root for root in github_source.PUBLIC_GITHUB_ROOTS if root.enabled_by_default]


def _one_skill_tree(url: str) -> dict:
    from row_bot.skills_hub.github_source import PUBLIC_GITHUB_ROOTS

    repo = url.split("/repos/", 1)[1].split("/git/", 1)[0]
    root = next(item for item in PUBLIC_GITHUB_ROOTS if item.repo_full_name == repo)
    return {"tree": [{"path": f"{root.root}/{repo.replace('/', '-')}/SKILL.md", "type": "blob"}]}


def test_github_browse_reads_its_repositories_in_parallel(monkeypatch):
    import threading

    together: list[threading.Barrier] = []

    def fetch_json(url, *, headers=None, timeout=15):
        # Every repository's request must be in flight at once to pass.
        together[0].wait()
        return _one_skill_tree(url)

    roots = _quiet_github(monkeypatch, fetch_json)
    together.append(threading.Barrier(len(roots), timeout=5))

    result = GitHubSource().browse(limit=100)

    assert result.status == "live"
    assert len(result.entries) == len(roots)


def test_github_browse_describes_unreadable_repositories_in_plain_words(monkeypatch):
    import urllib.error

    def fetch_json(url, *, headers=None, timeout=15):
        if "/repos/NVIDIA/skills/" in url:
            raise urllib.error.URLError(OSError(11001, "getaddrinfo failed"))
        return _one_skill_tree(url)

    roots = _quiet_github(monkeypatch, fetch_json)

    result = GitHubSource().browse(limit=100)

    assert result.status == "partial"
    assert len(result.entries) == len(roots) - 1
    assert result.message == f"1 of {len(roots)} GitHub repositories couldn't be read."


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, content in files.items():
            member = zipfile.ZipInfo()
            member.filename = path
            archive.writestr(member, content)
    return buffer.getvalue()
