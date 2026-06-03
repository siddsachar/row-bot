"""Source registry, cache, status, and routing for the Skills Hub."""

from __future__ import annotations

import concurrent.futures
import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

from .input_detection import detect_source_input
from .models import DetectedSourceInput, SourceHealth, SourceResult, SkillHubEntry
from .provenance import hub_dir
from .search_index import dedupe_entries, search_entries

BROWSE_CACHE_TTL_SECONDS = 6 * 60 * 60
HEALTH_CACHE_TTL_SECONDS = 30 * 60
DEFAULT_BROWSE_TIMEOUT = 10
DEFAULT_SEARCH_TIMEOUT = 8
DEFAULT_RESOLVE_TIMEOUT = 20
SOURCE_CACHE_SCHEMA_VERSION = 3


class SkillSourceRegistry:
    def __init__(self, sources: Iterable[object] | None = None) -> None:
        self._sources = list(sources) if sources is not None else build_default_sources()

    @property
    def sources(self) -> list[object]:
        return list(self._sources)

    def source(self, source_id: str) -> object | None:
        normalized = _normalize_source_id(source_id)
        for source in self._sources:
            if _normalize_source_id(getattr(source, "id", "")) == normalized:
                return source
        return None

    def source_metadata(self) -> list[dict[str, object]]:
        metadata: list[dict[str, object]] = []
        for source in self._sources:
            metadata.append({
                "id": getattr(source, "id", ""),
                "source_group": getattr(source, "source_group", getattr(source, "id", "")),
                "display_name": getattr(source, "display_name", getattr(source, "id", "Source")),
                "trust_default": getattr(source, "trust_default", "community"),
                "supports_browse": bool(getattr(source, "supports_browse", False)),
                "supports_search": bool(getattr(source, "supports_search", False)),
                "supports_import": bool(getattr(source, "supports_import", True)),
                "risk": getattr(source, "risk", ""),
            })
        return metadata

    def browse(
        self,
        *,
        query: str = "",
        source_filter: str = "all",
        limit: int = 50,
        force_refresh: bool = False,
    ) -> tuple[list[SkillHubEntry], list[SourceResult], DetectedSourceInput]:
        detected = detect_source_input(query)
        if detected.is_import_like:
            result = self.resolve(query, detected=detected)
            return result.entries[:limit], [result], detected

        selected = self._selected_sources(source_filter, browse_or_search=True)
        if not selected:
            return [], [], detected

        if detected.kind == "empty":
            results = self._run_sources(
                selected,
                operation="browse",
                query="",
                limit=limit,
                force_refresh=force_refresh,
                timeout=DEFAULT_BROWSE_TIMEOUT,
            )
            entries = dedupe_entries(entry for result in results for entry in result.entries)
            return search_entries(entries, "", limit=limit), results, detected

        results = self._run_sources(
            selected,
            operation="search",
            query=query,
            limit=max(limit, 50),
            force_refresh=force_refresh,
            timeout=DEFAULT_SEARCH_TIMEOUT,
        )
        entries = dedupe_entries(entry for result in results for entry in result.entries)
        return search_entries(entries, query, limit=limit), results, detected

    def resolve(
        self,
        value: str,
        *,
        detected: DetectedSourceInput | None = None,
    ) -> SourceResult:
        detected = detected or detect_source_input(value)
        preferred = []
        if detected.source_id:
            source = self.source(detected.source_id)
            if source is not None:
                preferred.append(source)
        for source in self._sources:
            if source not in preferred:
                preferred.append(source)

        started = time.perf_counter()
        messages: list[str] = []
        for source in preferred:
            can_resolve = getattr(source, "can_resolve", None)
            if callable(can_resolve) and not can_resolve(value):
                continue
            resolver = getattr(source, "resolve", None)
            if not callable(resolver):
                continue
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(resolver, value)
                    result = future.result(timeout=DEFAULT_RESOLVE_TIMEOUT)
                if isinstance(result, SourceResult) and result.entries:
                    result.duration_ms = int((time.perf_counter() - started) * 1000)
                    return result
                if isinstance(result, SourceResult) and result.message:
                    messages.append(f"{getattr(source, 'id', 'source')}: {result.message}")
            except Exception as exc:
                messages.append(f"{getattr(source, 'id', 'source')}: {exc}")
        return SourceResult(
            [],
            detected.source_id or "unknown",
            "error",
            "; ".join(messages) or "Could not resolve this skill source. Try a GitHub path, SKILL.md URL, well-known index URL, website URL, marketplace URL, or pasted markdown.",
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    def inspect_entry(self, entry: SkillHubEntry):
        source = self.source(entry.source)
        if source is None:
            raise ValueError(f"No source adapter registered for {entry.source}")
        inspect = getattr(source, "inspect", None)
        if not callable(inspect):
            raise ValueError(f"Source adapter cannot inspect entries: {entry.source}")
        return inspect(entry)

    def fetch(self, source_id: str, install_ref: str):
        source = self.source(source_id)
        if source is None:
            raise ValueError(f"No source adapter registered for {source_id}")
        fetch = getattr(source, "fetch", None)
        if not callable(fetch):
            raise ValueError(f"Source adapter cannot fetch bundles: {source_id}")
        return fetch(install_ref)

    def health(self) -> list[SourceHealth]:
        statuses: list[SourceHealth] = []
        for source in self._sources:
            health = getattr(source, "health", None)
            if callable(health):
                try:
                    statuses.append(health())
                    continue
                except Exception as exc:
                    statuses.append(SourceHealth(getattr(source, "id", "unknown"), False, last_error=str(exc)))
                    continue
            statuses.append(SourceHealth(getattr(source, "id", "unknown"), False))
        return statuses

    def _selected_sources(self, source_filter: str, *, browse_or_search: bool) -> list[object]:
        normalized = _normalize_source_id(source_filter or "all")
        selected: list[object] = []
        for source in self._sources:
            source_id = _normalize_source_id(getattr(source, "id", ""))
            source_group = _normalize_source_id(getattr(source, "source_group", source_id))
            trust = str(getattr(source, "trust_default", "community")).lower()
            if normalized in {"", "all"}:
                pass
            elif source_id != normalized and source_group != normalized:
                continue
            if browse_or_search and not (
                bool(getattr(source, "supports_browse", False))
                or bool(getattr(source, "supports_search", False))
            ):
                continue
            selected.append(source)
        return selected

    def _run_sources(
        self,
        sources: list[object],
        *,
        operation: str,
        query: str,
        limit: int,
        force_refresh: bool,
        timeout: int,
    ) -> list[SourceResult]:
        results: list[SourceResult] = []
        live_sources: list[object] = []

        for source in sources:
            cached = None
            if not force_refresh:
                cached = _read_source_cache(getattr(source, "id", ""), operation, query)
            if cached is not None:
                results.append(cached)
                continue
            live_sources.append(source)

        if not live_sources:
            return results

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=min(6, len(live_sources)))
        try:
            future_map = {
                executor.submit(_call_source, source, operation, query, limit): source
                for source in live_sources
            }
            done, pending = concurrent.futures.wait(
                future_map,
                timeout=timeout,
                return_when=concurrent.futures.ALL_COMPLETED,
            )
            for future in pending:
                future.cancel()
            for future in done:
                source = future_map[future]
                source_id = getattr(source, "id", "unknown")
                try:
                    result = future.result(timeout=0)
                except Exception as exc:
                    stale = _read_source_cache(source_id, operation, query, allow_stale=True)
                    if stale is not None:
                        stale.status = "stale"
                        stale.message = f"Live refresh failed: {exc}"
                        results.append(stale)
                    else:
                        results.append(SourceResult([], source_id, "error", str(exc)))
                    continue
                if result.entries:
                    _write_source_cache(result, operation, query)
                elif result.status == "error":
                    stale = _read_source_cache(source_id, operation, query, allow_stale=True)
                    if stale is not None:
                        stale.status = "stale"
                        stale.message = result.message
                        results.append(stale)
                        continue
                results.append(result)
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        seen_ids = {result.source_id for result in results}
        for source in live_sources:
            source_id = getattr(source, "id", "unknown")
            if source_id in seen_ids:
                continue
            stale = _read_source_cache(source_id, operation, query, allow_stale=True)
            if stale is not None:
                stale.status = "stale"
                stale.message = "Live source timed out."
                results.append(stale)
            else:
                results.append(SourceResult([], source_id, "error", "Live source timed out."))
        return results


def build_default_sources() -> list[object]:
    from .browse_sh_source import BrowseShSource
    from .claude_marketplace_source import ClaudeMarketplaceSource
    from .clawhub_source import ClawHubSource
    from .github_source import GitHubSource
    from .lobehub_source import LobeHubSource
    from .pasted_markdown_source import PastedMarkdownSource
    from .skills_sh_source import SkillsShSource
    from .url_source import DirectURLSource
    from .well_known_source import WellKnownSource

    return [
        SkillsShSource(),
        BrowseShSource(),
        GitHubSource(),
        ClaudeMarketplaceSource(),
        LobeHubSource(),
        ClawHubSource(),
        DirectURLSource(),
        WellKnownSource(),
        PastedMarkdownSource(),
    ]


_DEFAULT_REGISTRY: SkillSourceRegistry | None = None


def default_registry() -> SkillSourceRegistry:
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = SkillSourceRegistry()
    return _DEFAULT_REGISTRY


def reset_default_registry() -> None:
    global _DEFAULT_REGISTRY
    _DEFAULT_REGISTRY = None


def _call_source(source: object, operation: str, query: str, limit: int) -> SourceResult:
    source_id = getattr(source, "id", "unknown")
    started = time.perf_counter()
    try:
        if operation == "browse":
            browse = getattr(source, "browse", None)
            if not callable(browse):
                return SourceResult([], source_id, "empty", "Browse is not supported.")
            result = browse(limit=limit)
        else:
            if bool(getattr(source, "supports_search", False)):
                search_result = getattr(source, "search_result", None)
                if callable(search_result):
                    result = search_result(query, limit=limit)
                else:
                    search = getattr(source, "search", None)
                    entries = search(query, limit=limit) if callable(search) else []
                    result = SourceResult(entries, source_id, "live" if entries else "empty")
            else:
                browse = getattr(source, "browse", None)
                entries = browse(limit=max(limit, 50)).entries if callable(browse) else []
                result = SourceResult(search_entries(entries, query, limit=limit), source_id, "live")
    except Exception as exc:
        return SourceResult([], source_id, "error", str(exc), duration_ms=int((time.perf_counter() - started) * 1000))
    if not isinstance(result, SourceResult):
        result = SourceResult(list(result or []), source_id, "live")
    result.source_id = result.source_id or source_id
    result.duration_ms = int((time.perf_counter() - started) * 1000)
    if not result.fetched_at:
        result.fetched_at = time.time()
    return result


def _cache_root() -> Path:
    root = hub_dir() / "index-cache"
    root.mkdir(parents=True, exist_ok=True)
    ignore = root / ".ignore"
    if not ignore.exists():
        ignore.write_text("Skills Hub public index cache. Do not scan as skills.\n", encoding="utf-8")
    return root


def _cache_path(source_id: str, operation: str, query: str) -> Path:
    safe_query = _normalize_source_id(query or "browse")[:80] or "browse"
    name = f"{_normalize_source_id(source_id)}_{operation}_{safe_query}.json"
    return _cache_root() / name


def _read_source_cache(
    source_id: str,
    operation: str,
    query: str,
    *,
    allow_stale: bool = False,
) -> SourceResult | None:
    path = _cache_path(source_id, operation, query)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if int(data.get("cache_schema_version") or 0) != SOURCE_CACHE_SCHEMA_VERSION:
            return None
        result = SourceResult.from_dict(data)
    except Exception:
        return None
    age = time.time() - float(result.fetched_at or 0)
    if not allow_stale and age > BROWSE_CACHE_TTL_SECONDS:
        return None
    result.status = "cached" if age <= BROWSE_CACHE_TTL_SECONDS else "stale"
    result.from_cache = True
    if not result.message:
        result.message = f"Cached {int(age)} seconds ago."
    return result


def _write_source_cache(result: SourceResult, operation: str, query: str) -> None:
    if not result.entries:
        return
    result.fetched_at = result.fetched_at or time.time()
    path = _cache_path(result.source_id, operation, query)
    payload = result.as_dict()
    payload["cache_schema_version"] = SOURCE_CACHE_SCHEMA_VERSION
    payload["entries"] = [asdict(entry) for entry in result.entries]
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _normalize_source_id(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in str(value or "").strip().lower()).strip("_")
