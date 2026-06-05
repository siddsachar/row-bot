from __future__ import annotations

import json
from pathlib import Path

from row_bot.skills_hub.catalog import search_skills
from row_bot.skills_hub.models import SourceResult, SkillHubEntry
from row_bot.skills_hub.search_index import search_entries, tokenize
from row_bot.skills_hub.source_registry import SkillSourceRegistry


def _entry(
    source: str,
    name: str,
    description: str,
    *,
    tags=None,
    trust="community",
    popularity=0,
    install_ref: str | None = None,
) -> SkillHubEntry:
    slug = name.lower().replace(" ", "-")
    return SkillHubEntry(
        id=f"{source}:{slug}",
        name=name,
        description=description,
        source=source,
        source_id=source,
        install_ref=install_ref or f"https://example.test/{source}/{slug}/SKILL.md",
        url=f"https://example.test/{source}/{slug}",
        author="Tester",
        tags=list(tags or []),
        trust_level=trust,
        metadata={"install_count": popularity},
    )


def test_tokenizer_splits_common_skill_identifier_shapes():
    assert tokenize("codeReview/pdf_tools.browserSkill") == [
        "code",
        "review",
        "pdf",
        "tools",
        "browser",
        "skill",
    ]


def test_weighted_search_ranking_and_misspellings():
    entries = [
        _entry("skills_sh", "Research Brief", "Research sources and write a brief", tags=["research"], popularity=10),
        _entry("browse_sh", "Browser Navigator", "Inspect websites and extract facts", tags=["browser"], popularity=100),
        _entry("github", "Python PDF Tools", "Analyze PDFs with Python", tags=["python", "pdf"], trust="verified"),
        _entry("lobehub", "Code Review Coach", "Review code changes", tags=["code-review"]),
    ]

    assert search_entries(entries, "research")[0].name == "Research Brief"
    assert search_entries(entries, "browser")[0].name == "Browser Navigator"
    assert search_entries(entries, "python")[0].name == "Python PDF Tools"
    assert search_entries(entries, "pdf")[0].name == "Python PDF Tools"
    assert search_entries(entries, "code review")[0].name == "Code Review Coach"
    assert search_entries(entries, "reserch")[0].name == "Research Brief"


def test_dedupe_prefers_trusted_or_more_popular_entries():
    same_ref = "https://example.test/shared/SKILL.md"
    entries = [
        _entry("clawhub", "Shared Skill", "Risky copy", trust="high-risk community", popularity=200, install_ref=same_ref),
        _entry("github", "Shared Skill", "Verified copy", trust="verified", popularity=1, install_ref=same_ref),
    ]

    results = search_entries(entries, "shared")

    assert len(results) == 1
    assert results[0].source == "github"


class _MockSource:
    def __init__(self, source_id: str, entries: list[SkillHubEntry], *, source_group: str = ""):
        self.id = source_id
        if source_group:
            self.source_group = source_group
        self.display_name = source_id.replace("_", " ").title()
        self.trust_default = "community"
        self.supports_browse = True
        self.supports_search = True
        self.supports_import = False
        self._entries = entries

    def browse(self, limit=50, cursor=None):
        return SourceResult(self._entries[:limit], self.id, "live")

    def search(self, query, limit=24):
        return search_entries(self._entries, query, limit=limit)


def test_catalog_browse_empty_query_returns_public_source_results(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    import row_bot.skills_hub.source_registry as source_registry

    monkeypatch.setattr(source_registry, "_write_source_cache", lambda *args, **kwargs: None)
    registry = SkillSourceRegistry([
        _MockSource("skills_sh", [_entry("skills_sh", "Research Brief", "Research public sources")]),
        _MockSource("browse_sh", [_entry("browse_sh", "Browser Navigator", "Browse websites")]),
    ])

    result = search_skills("", registry=registry, limit=10, force_refresh=True)

    assert result.mode == "live"
    assert [entry.name for entry in result.entries] == ["Browser Navigator", "Research Brief"]
    assert result.source_counts == {"browse_sh": 1, "skills_sh": 1}
    assert all(status.status == "live" for status in result.source_statuses)


def test_catalog_search_runs_across_all_mock_public_sources(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    import row_bot.skills_hub.source_registry as source_registry

    monkeypatch.setattr(source_registry, "_write_source_cache", lambda *args, **kwargs: None)
    registry = SkillSourceRegistry([
        _MockSource("skills_sh", [_entry("skills_sh", "Research Brief", "Research public sources")]),
        _MockSource("browse_sh", [_entry("browse_sh", "Browser Navigator", "Browse websites")]),
    ])

    result = search_skills("browser", registry=registry, limit=10, force_refresh=True)

    assert [entry.name for entry in result.entries] == ["Browser Navigator"]
    assert result.detected_input is not None
    assert result.detected_input.kind == "keyword"


def test_source_filter_selects_grouped_github_manifest_sources(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    import row_bot.skills_hub.source_registry as source_registry

    monkeypatch.setattr(source_registry, "_write_source_cache", lambda *args, **kwargs: None)
    registry = SkillSourceRegistry([
        _MockSource("github", [_entry("github", "CUDA Helper", "NVIDIA CUDA workflows")]),
        _MockSource(
            "claude_marketplace",
            [_entry("github", "Manifest Skill", "Claude manifest skill")],
            source_group="github",
        ),
        _MockSource("browse_sh", [_entry("browse_sh", "Browser Navigator", "Browse websites")]),
    ])

    result = search_skills("", source="github", registry=registry, limit=10, force_refresh=True)

    assert [entry.name for entry in result.entries] == ["CUDA Helper", "Manifest Skill"]
    assert result.source_counts == {"github": 2}


def test_source_cache_ignores_payloads_without_current_schema(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    import row_bot.skills_hub.source_registry as source_registry

    path = source_registry._cache_path("skills_sh", "search", "python")
    path.write_text(json.dumps({
        "entries": [_entry("skills_sh", "Broken Cached", "Old ref").as_dict()],
        "source_id": "skills_sh",
        "status": "live",
        "fetched_at": 9999999999,
    }), encoding="utf-8")

    assert source_registry._read_source_cache("skills_sh", "search", "python") is None
