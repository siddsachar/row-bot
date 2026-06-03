from __future__ import annotations

from skills_hub.input_detection import detect_source_input
from skills_hub.pasted_markdown_source import PastedMarkdownSource


def test_detects_supported_source_input_forms():
    cases = {
        "---\nname: pasted_skill\n---\n\nInstructions.": ("pasted_markdown", "pasted_markdown"),
        "https://github.com/openai/skills/tree/main/skills/researcher": ("github_url", "github"),
        "openai/skills/skills/researcher": ("github_shorthand", "github"),
        "https://example.test/skills/researcher/SKILL.md": ("direct_skill_url", "direct_url"),
        "https://example.test/.well-known/skills/index.json": ("well_known_index_url", "well_known"),
        "https://example.test/docs": ("website_url", "well_known"),
        "https://skills.sh/skills/researcher": ("marketplace_url", "skills_sh"),
        "https://browse.sh/skills/browser": ("marketplace_url", "browse_sh"),
        "https://clawhub.ai/skills/demo": ("marketplace_url", "clawhub"),
        "https://chat-agents.lobehub.com/agents/helper": ("marketplace_url", "lobehub"),
    }

    for value, expected in cases.items():
        detected = detect_source_input(value)
        assert (detected.kind, detected.source_id) == expected


def test_plain_keywords_are_not_forced_into_source_modes():
    detected = detect_source_input("browser research")

    assert detected.kind == "keyword"
    assert not detected.source_id


def test_website_url_normalizes_to_well_known_index():
    detected = detect_source_input("https://example.test/docs")

    assert detected.kind == "website_url"
    assert detected.normalized == "https://example.test/.well-known/skills/index.json"
    assert detected.metadata["index_url"] == detected.normalized


def test_pasted_markdown_source_generates_preview_bundle_from_heading():
    source = PastedMarkdownSource()
    markdown = "# Demo Research Skill\n\n## When to use\nUse for research.\n\n## Instructions\nSummarize sources."

    result = source.resolve(markdown)
    bundle = source.inspect(result.entries[0])

    assert result.entries[0].source == "pasted_markdown"
    assert bundle.frontmatter["name"] == "demo_research_skill"
    assert "Summarize sources." in bundle.instructions
