"""UI-facing public skill catalog/search facade."""

from __future__ import annotations

from typing import Iterable

from .models import CatalogSearchResult, SkillHubEntry, SourceResult
from .provenance import load_records
from .search_index import entry_search_text, search_entries
from .source_registry import SkillSourceRegistry, default_registry


def default_sources() -> list[object]:
    return default_registry().sources


def source_for_id(source_id: str):
    return default_registry().source(source_id)


def source_metadata() -> list[dict[str, object]]:
    return default_registry().source_metadata()


def search_skills(
    query: str = "",
    *,
    source: str = "all",
    limit: int = 24,
    force_refresh: bool = False,
    registry: SkillSourceRegistry | None = None,
) -> CatalogSearchResult:
    from row_bot.docs_capture import is_docs_capture

    if is_docs_capture():
        all_entries = [
            SkillHubEntry(
                id="docs:research-brief",
                name="Research Brief",
                description="Turn local sources into a concise, cited briefing.",
                source="bundled-demo",
                source_id="bundled-demo",
                install_ref="docs/research-brief",
                author="Row-Bot Docs",
                tags=["research", "documents"],
                trust_level="verified",
            ),
            SkillHubEntry(
                id="docs:workflow-review",
                name="Workflow Review",
                description="Review an automation plan for approvals, delivery, and recovery.",
                source="bundled-demo",
                source_id="bundled-demo",
                install_ref="docs/workflow-review",
                author="Row-Bot Docs",
                tags=["workflows", "safety"],
                trust_level="verified",
            ),
        ]
        needle = (query or "").strip().casefold()
        entries = [
            entry
            for entry in all_entries
            if not needle
            or needle in entry.name.casefold()
            or needle in entry.description.casefold()
        ][:limit]
        return CatalogSearchResult(
            entries=entries,
            mode="cache",
            query=(query or "").strip(),
            source_counts={"bundled-demo": len(entries)},
            source_statuses=[
                SourceResult(
                    entries=entries,
                    source_id="bundled-demo",
                    status="cached",
                    message="Offline documentation fixture",
                    from_cache=True,
                )
            ],
        )
    active_registry = registry or default_registry()
    entries, statuses, detected = active_registry.browse(
        query=query,
        source_filter=source,
        limit=limit,
        force_refresh=force_refresh,
    )
    decorated = _decorate_installed_state(entries)
    mode = _mode_from_statuses(statuses, bool(decorated))
    errors = "; ".join(result.message for result in statuses if result.status == "error" and result.message)
    return CatalogSearchResult(
        entries=decorated[:limit],
        mode=mode,
        query=(query or "").strip(),
        source_counts=_count_sources(decorated),
        error=errors,
        source_statuses=statuses,
        detected_input=detected,
    )


def inspect_entry(entry: SkillHubEntry):
    return default_registry().inspect_entry(entry)


def installed_hub_entries() -> list[SkillHubEntry]:
    entries: list[SkillHubEntry] = []
    for record in load_records().values():
        entries.append(SkillHubEntry(
            id=f"installed:{record.local_name}",
            name=record.local_name.replace("_", " ").title(),
            description="Installed public skill",
            source=record.source,
            source_id=record.source_id,
            install_ref=record.install_ref,
            url=str(record.metadata.get("url") or ""),
            author=str(record.metadata.get("author") or ""),
            tags=[],
            trust_level=str(record.metadata.get("trust_level") or "community"),
            metadata={"installed": True, "installed_state": "available" if record.enabled else "off", "record": record.as_dict()},
        ))
    return entries


def filter_entries(entries: Iterable[SkillHubEntry], query: str) -> list[SkillHubEntry]:
    return search_entries(entries, query, limit=10_000)


def _decorate_installed_state(entries: Iterable[SkillHubEntry]) -> list[SkillHubEntry]:
    records = load_records()
    by_install = {record.install_ref: record for record in records.values()}
    by_source = {(record.source, record.install_ref): record for record in records.values()}
    decorated: list[SkillHubEntry] = []
    for entry in entries:
        record = by_source.get((entry.source, entry.install_ref)) or by_install.get(entry.install_ref)
        if record is None:
            decorated.append(entry)
            continue
        metadata = dict(entry.metadata or {})
        metadata["installed"] = True
        metadata["installed_state"] = "available" if record.enabled else "off"
        metadata["record"] = record.as_dict()
        decorated.append(SkillHubEntry(
            id=entry.id,
            name=entry.name,
            description=entry.description,
            source=entry.source,
            source_id=entry.source_id,
            install_ref=entry.install_ref,
            url=entry.url,
            author=entry.author,
            tags=list(entry.tags or []),
            trust_level=entry.trust_level,
            metadata=metadata,
        ))
    return decorated


def _mode_from_statuses(statuses: list, has_entries: bool) -> str:
    if not statuses:
        return "empty"
    modes = {getattr(status, "status", "") for status in statuses}
    if "live" in modes and ("error" in modes or "stale" in modes):
        return "partial"
    if "live" in modes:
        return "live"
    if "cached" in modes:
        return "cache"
    if "stale" in modes:
        return "cache"
    if "error" in modes:
        return "error" if not has_entries else "partial"
    return "empty" if not has_entries else "live"


def _count_sources(entries: Iterable[SkillHubEntry]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry.source] = counts.get(entry.source, 0) + 1
    return counts
