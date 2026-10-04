from __future__ import annotations

import json
import logging
import threading
import urllib.error
from pathlib import Path

import pytest

from row_bot.skills_hub import github_source, provenance, source_registry
from row_bot.skills_hub.catalog import search_skills
from row_bot.skills_hub.models import (
    SkillBundle,
    SkillHubEntry,
    SkillInstallRecord,
    SourceResult,
)
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


@pytest.fixture
def hub_cache(tmp_path: Path, monkeypatch):
    """Keep the public index cache and install records in this test's folder."""
    import row_bot.skills as skills

    monkeypatch.setattr(skills, "USER_SKILLS_DIR", tmp_path / "skills")
    return tmp_path


class _EventSource:
    """A fake public source; a test holds its answer back with an event."""

    def __init__(
        self,
        source_id: str,
        entries: list[SkillHubEntry],
        *,
        release: threading.Event | None = None,
        failure: Exception | None = None,
    ):
        self.id = source_id
        self.display_name = {"clawhub": "ClawHub", "skills_sh": "skills.sh"}.get(source_id, source_id)
        self.trust_default = "community"
        self.supports_browse = True
        self.supports_search = True
        self.supports_import = False
        self.calls: list[str] = []
        self._entries = entries
        self._release = release
        self._failure = failure

    def _answer(self, operation: str) -> None:
        self.calls.append(operation)
        if self._release is not None:
            assert self._release.wait(10)
        if self._failure is not None:
            raise self._failure

    def browse(self, limit=50, cursor=None):
        self._answer("browse")
        return SourceResult(self._entries[:limit], self.id, "live")

    def search(self, query, limit=24):
        self._answer("search")
        return search_entries(self._entries, query, limit=limit)


def test_a_slow_source_does_not_hold_back_the_others(hub_cache, monkeypatch, caplog):
    release = threading.Event()
    fast = _EventSource("skills_sh", [_entry("skills_sh", "Blender Helper", "Model scenes in Blender")])
    slow = _EventSource(
        "clawhub",
        [_entry("clawhub", "Blender Renderer", "Render Blender scenes")],
        release=release,
    )
    registry = SkillSourceRegistry([fast, slow])
    caplog.set_level(logging.INFO, logger="row_bot.skills_hub.source_registry")
    monkeypatch.setattr(source_registry, "DEFAULT_SEARCH_TIMEOUT", 0.3)
    try:
        entries, statuses, _detected = registry.browse(query="blender")
        by_source = {status.source_id: status for status in statuses}
        assert [entry.name for entry in entries] == ["Blender Helper"]
        assert by_source["skills_sh"].status == "live"
        assert by_source["clawhub"].status == "pending"
    finally:
        release.set()

    # The slow answer lands in the background: the next search shows it without
    # asking either source again.
    monkeypatch.setattr(source_registry, "DEFAULT_SEARCH_TIMEOUT", 5)
    entries, _statuses, _detected = registry.browse(query="blender")
    assert {entry.name for entry in entries} == {"Blender Helper", "Blender Renderer"}
    assert fast.calls == ["search"]
    assert slow.calls == ["search"]
    # Each source's outcome and time are logged, never what was searched for.
    assert "skills_sh" in caplog.text and "clawhub" in caplog.text
    assert "blender" not in caplog.text.lower()


def _github_tree(url: str) -> dict:
    repo = url.split("/repos/", 1)[1].split("/git/", 1)[0]
    root = next(item for item in github_source.PUBLIC_GITHUB_ROOTS if item.repo_full_name == repo)
    slug = repo.replace("/", "-").lower()
    return {"tree": [
        {"path": f"{root.root}/blender-{slug}/SKILL.md", "type": "blob"},
        {"path": f"{root.root}/notes-{slug}/SKILL.md", "type": "blob"},
    ]}


@pytest.fixture
def fake_github(monkeypatch):
    calls: list[str] = []

    def fetch_json(url, *, headers=None, timeout=15):
        calls.append(url)
        return _github_tree(url)

    monkeypatch.setattr(github_source, "fetch_json", fetch_json)
    monkeypatch.setattr(github_source.github_account, "github_public_api_headers", lambda **_kwargs: {})
    monkeypatch.setattr(github_source.GitHubSource, "_auth_status_message", lambda self: "")
    monkeypatch.setattr(github_source, "_GITHUB_BACKOFF_UNTIL", 0)
    return calls


def test_github_keyword_search_is_served_from_the_browse_index(hub_cache, fake_github):
    registry = SkillSourceRegistry([github_source.GitHubSource()])
    registry.browse(query="")
    browsed = len(fake_github)
    assert browsed > 0

    entries, statuses, _detected = registry.browse(query="blender")

    assert len(fake_github) == browsed
    assert statuses[0].status == "cached"
    enabled = [root for root in github_source.PUBLIC_GITHUB_ROOTS if root.enabled_by_default]
    assert len(entries) == len(enabled)
    assert all(entry.name.startswith("Blender") for entry in entries)


def test_resolve_returns_within_its_time_limit(hub_cache, monkeypatch):
    release = threading.Event()

    class _HangingResolver:
        id = "github"
        display_name = "GitHub"
        supports_browse = False
        supports_search = False

        def can_resolve(self, value):
            return True

        def resolve(self, value):
            release.wait(10)
            return SourceResult([], self.id, "empty")

    monkeypatch.setattr(source_registry, "DEFAULT_RESOLVE_TIMEOUT", 0.05)
    registry = SkillSourceRegistry([_HangingResolver()])
    outcome: dict[str, object] = {}

    def resolve() -> None:
        try:
            outcome["result"] = registry.resolve("example/skills")
        except Exception as exc:  # the outcome under test
            outcome["error"] = exc

    worker = threading.Thread(target=resolve, daemon=True)
    worker.start()
    try:
        worker.join(2)
        assert not worker.is_alive()
    finally:
        release.set()
    assert isinstance(outcome.get("error"), source_registry.SkillSourceTimeout)


def test_preview_returns_within_its_time_limit(hub_cache, monkeypatch):
    release = threading.Event()
    entry = _entry("clawhub", "Blender Renderer", "Render Blender scenes")

    class _HangingInspector(_EventSource):
        def inspect(self, selected):
            release.wait(10)
            return SkillBundle("clawhub", selected.install_ref, "demo", "SKILL.md", [], {}, "", "")

    monkeypatch.setattr(source_registry, "DEFAULT_PREVIEW_TIMEOUT", 0.05)
    registry = SkillSourceRegistry([_HangingInspector("clawhub", [entry])])
    outcome: dict[str, object] = {}

    def inspect() -> None:
        try:
            outcome["bundle"] = registry.inspect_entry(entry)
        except Exception as exc:  # the outcome under test
            outcome["error"] = exc

    worker = threading.Thread(target=inspect, daemon=True)
    worker.start()
    try:
        worker.join(2)
        assert not worker.is_alive()
    finally:
        release.set()
    assert isinstance(outcome.get("error"), source_registry.SkillSourceTimeout)


def test_source_failures_are_described_in_plain_words(hub_cache):
    failure = urllib.error.URLError(OSError(11001, "getaddrinfo failed"))
    unreachable = _EventSource("clawhub", [], failure=failure)
    registry = SkillSourceRegistry([unreachable])

    _entries, statuses, _detected = registry.browse(query="blender", force_refresh=True)
    assert statuses[0].status == "error"
    assert statuses[0].message == "ClawHub couldn't be reached."

    # A follow-up request reuses the recent failure; Refresh asks again.
    _entries, statuses, _detected = registry.browse(query="blender")
    assert statuses[0].message == "ClawHub couldn't be reached."
    assert unreachable.calls == ["search"]
    registry.browse(query="blender", force_refresh=True)
    assert unreachable.calls == ["search", "search"]


def test_search_marks_a_marketplace_skill_installed_after_install(hub_cache, monkeypatch):
    # A marketplace page often installs from its GitHub folder, so the saved
    # record names the page the person installed it from as well.
    entry = _entry("skills_sh", "Research Brief", "Research public sources")
    provenance.upsert_record(SkillInstallRecord(
        local_name="research_brief",
        source="skills_sh",
        source_id="owner/repo",
        install_ref="github:owner/repo/skills/research-brief",
        installed_at="2026-09-30T00:00:00Z",
        updated_at="2026-09-30T00:00:00Z",
        content_hash="a" * 64,
        enabled=True,
        file_count=1,
        scan_summary={},
        metadata={"hub_entry_ref": entry.install_ref},
    ))
    monkeypatch.setattr(source_registry, "_write_source_cache", lambda *args, **kwargs: None)
    registry = SkillSourceRegistry([_MockSource("skills_sh", [entry])])

    result = search_skills("research", registry=registry, force_refresh=True)

    assert result.entries[0].metadata["installed"] is True


def test_skill_identity_preserves_subpath_version_case_and_fragment():
    entries = [
        _entry("github", "Same", "", install_ref=ref)
        for ref in ("https://example.test/repo#skills/a@1", "https://example.test/repo#skills/b@1",
                    "https://example.test/repo#skills/a@2", "https://example.test/repo#skills/A@1")
    ]
    assert len(search_entries(entries, "")) == 4


def test_query_cache_identity_preserves_punctuation_and_long_suffix(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    source = _MockSource("clawhub", [_entry("clawhub", "shared", "")])
    registry = SkillSourceRegistry([source])
    first, _, _ = registry.browse(query="shared-a", source_filter="clawhub")
    # Seed different results via the public registry, then verify cache isolation.
    source._entries = [_entry("clawhub", "shared a", "")]
    registry.browse(query="shared a", source_filter="clawhub")
    cached, _, _ = registry.browse(query="shared-a", source_filter="clawhub", cached_only=True)
    assert [e.id for e in cached] == [e.id for e in first]
