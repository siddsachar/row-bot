"""Source registry, cache, status, and routing for the Skills Hub."""

from __future__ import annotations

import concurrent.futures
import json
import logging
import threading
import time
import urllib.error
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Callable, Iterable

from .input_detection import detect_source_input
from .models import DetectedSourceInput, SourceHealth, SourceResult, SkillHubEntry
from .provenance import hub_dir
from .search_index import dedupe_entries, search_entries

logger = logging.getLogger(__name__)

BROWSE_CACHE_TTL_SECONDS = 6 * 60 * 60
HEALTH_CACHE_TTL_SECONDS = 30 * 60
# How long a browse or search waits before answering with the sources that have
# replied. Slower sources keep going and are reported "pending"; their results
# land in the cache, and the client asks again to pick them up.
DEFAULT_BROWSE_TIMEOUT = 5
DEFAULT_SEARCH_TIMEOUT = 5
DEFAULT_RESOLVE_TIMEOUT = 20
DEFAULT_PREVIEW_TIMEOUT = 20
# A source whose keyword search only filters its own list (`search_from_browse`)
# keeps the whole list in its browse cache, so keyword searches need no network.
SOURCE_INDEX_LIMIT = 1000
# The most entries one source adds to an empty browse (and fetches per search).
BROWSE_PER_SOURCE = 50
# A finished live answer that wasn't cached (a failure, or nothing found) is
# reused this long, so follow-up requests don't ask that source again.
RECENT_ANSWER_SECONDS = 60
SOURCE_CACHE_SCHEMA_VERSION = 4
RESOLVE_FAILED_MESSAGE = (
    "Row-Bot couldn't find a skill there. Try a GitHub folder, a SKILL.md link, "
    "or a skills.sh, browse.sh, ClawHub or LobeHub page."
)


class SkillSourceTimeout(TimeoutError):
    """A public source didn't answer within its time limit."""

    def __init__(self, source_name: str) -> None:
        super().__init__(f"{source_name} took too long to answer.")
        self.source_name = source_name


class SkillSourceRegistry:
    def __init__(self, sources: Iterable[object] | None = None) -> None:
        self._sources = list(sources) if sources is not None else build_default_sources()
        # Live fetches by (source, operation, query) with their start time; a
        # running one is shared by every request that needs it.
        self._live: dict[tuple[str, str, str], tuple[float, concurrent.futures.Future]] = {}
        self._live_lock = threading.Lock()

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
            entries = dedupe_entries(
                entry for result in results for entry in result.entries[:BROWSE_PER_SOURCE]
            )
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
        # One time limit covers every source tried; a source still working when
        # it runs out is left behind instead of holding the answer back.
        deadline = time.monotonic() + DEFAULT_RESOLVE_TIMEOUT
        timed_out = ""
        for source in preferred:
            can_resolve = getattr(source, "can_resolve", None)
            if callable(can_resolve) and not can_resolve(value):
                continue
            resolver = getattr(source, "resolve", None)
            if not callable(resolver):
                continue
            source_id = str(getattr(source, "id", "source"))
            try:
                result = _call_with_limit(
                    source,
                    resolver,
                    value,
                    timeout=max(0.0, deadline - time.monotonic()),
                )
            except SkillSourceTimeout:
                timed_out = timed_out or _display_name(source)
                _log_outcome(source_id, "resolve", "timed_out", started)
                if time.monotonic() >= deadline:
                    break
                continue
            except Exception as exc:
                _log_outcome(source_id, "resolve", f"error {type(exc).__name__}", started)
                continue
            if isinstance(result, SourceResult) and result.entries:
                result.duration_ms = int((time.perf_counter() - started) * 1000)
                _log_outcome(source_id, "resolve", result.status, started, len(result.entries))
                return result
        if timed_out:
            raise SkillSourceTimeout(timed_out)
        return SourceResult(
            [],
            detected.source_id or "unknown",
            "error",
            RESOLVE_FAILED_MESSAGE,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    def inspect_entry(self, entry: SkillHubEntry):
        source = self.source(entry.source)
        if source is None:
            raise ValueError(f"No source adapter registered for {entry.source}")
        inspect = getattr(source, "inspect", None)
        if not callable(inspect):
            raise ValueError(f"Source adapter cannot inspect entries: {entry.source}")
        started = time.perf_counter()
        try:
            bundle = _call_with_limit(source, inspect, entry, timeout=DEFAULT_PREVIEW_TIMEOUT)
        except Exception as exc:
            _log_outcome(entry.source, "preview", f"error {type(exc).__name__}", started)
            raise
        _log_outcome(entry.source, "preview", "live", started)
        return bundle

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
        timeout: float,
    ) -> list[SourceResult]:
        results: list[SourceResult] = []
        waiting: dict[concurrent.futures.Future, tuple[object, str, str, bool]] = {}

        for source in sources:
            source_id = str(getattr(source, "id", "unknown"))
            from_index = bool(getattr(source, "search_from_browse", False))
            # A keyword search of an index source filters its cached browse list.
            filtered = from_index and operation == "search"
            live_operation, live_query = ("browse", "") if filtered else (operation, query)
            cached = None
            if not force_refresh:
                cached = _read_source_cache(source_id, live_operation, live_query)
            if cached is not None:
                results.append(_filtered(cached, query, limit) if filtered else cached)
                continue
            fetch_limit = SOURCE_INDEX_LIMIT if from_index else max(limit, BROWSE_PER_SOURCE)
            future = self._live_fetch(
                source, live_operation, live_query, fetch_limit, retry=force_refresh
            )
            waiting[future] = (source, live_operation, live_query, filtered)

        if not waiting:
            return results

        done, _still_running = concurrent.futures.wait(waiting, timeout=timeout)
        for future, (source, live_operation, live_query, filtered) in waiting.items():
            source_id = str(getattr(source, "id", "unknown"))
            stale = None
            if future in done:
                result = future.result()
                if result.status == "error":
                    stale = _read_source_cache(source_id, live_operation, live_query, allow_stale=True)
                if stale is not None:
                    stale.status = "stale"
                    stale.message = result.message
                    result = stale
            else:
                # Still running: show older results meanwhile, if any.
                stale = _read_source_cache(source_id, live_operation, live_query, allow_stale=True)
                result = SourceResult(stale.entries if stale else [], source_id, "pending")
            results.append(_filtered(result, query, limit) if filtered else result)
        return results

    def _live_fetch(
        self, source: object, operation: str, query: str, limit: int, *, retry: bool
    ) -> concurrent.futures.Future:
        """Start one live fetch per source, operation and query, or join the known one."""
        key = (_normalize_source_id(getattr(source, "id", "")), operation, _normalize_source_id(query))
        now = time.monotonic()
        with self._live_lock:
            for known, (started, future) in list(self._live.items()):
                if future.done() and now - started >= RECENT_ANSWER_SECONDS:
                    del self._live[known]
            if key in self._live:
                _started, future = self._live[key]
                if not (retry and future.done()):
                    return future
            future = _in_background(source, _fetch_and_cache, source, operation, query, limit)
            self._live[key] = (now, future)
        return future


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


def _in_background(source: object, fn: Callable[..., Any], *args: Any) -> concurrent.futures.Future:
    """Run `fn` on a daemon thread, so no caller ever waits past its own limit."""
    future: concurrent.futures.Future = concurrent.futures.Future()

    def run() -> None:
        if not future.set_running_or_notify_cancel():
            return
        try:
            future.set_result(fn(*args))
        except BaseException as exc:  # handed to whoever waits on the future
            future.set_exception(exc)

    name = f"skills-hub-{_normalize_source_id(getattr(source, 'id', 'source'))}"
    threading.Thread(target=run, name=name, daemon=True).start()
    return future


def _call_with_limit(source: object, fn: Callable[..., Any], *args: Any, timeout: float) -> Any:
    try:
        return _in_background(source, fn, *args).result(timeout=timeout)
    except TimeoutError as exc:
        raise SkillSourceTimeout(_display_name(source)) from exc


def _fetch_and_cache(source: object, operation: str, query: str, limit: int) -> SourceResult:
    started = time.perf_counter()
    result = _call_source(source, operation, query, limit)
    _log_outcome(result.source_id, operation, result.status, started, len(result.entries))
    if result.entries:
        try:
            _write_source_cache(result, operation, query)
        except OSError as exc:
            logger.warning(
                "Skills hub %s %s results were not cached: %s",
                result.source_id, operation, type(exc).__name__,
            )
    return result


def _filtered(result: SourceResult, query: str, limit: int) -> SourceResult:
    return replace(result, entries=search_entries(result.entries, query, limit=limit))


def _display_name(source: object) -> str:
    return str(getattr(source, "display_name", "") or getattr(source, "id", "") or "The source")


def _failure_message(exc: BaseException, name: str) -> str:
    """Why a source failed, in plain words; the raw error only reaches the log."""
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in {403, 429}:
            return f"{name} is limiting requests right now. Try again later."
        if exc.code == 404:
            return f"{name} couldn't find it."
        return f"{name} had a problem answering. Try again later."
    if isinstance(exc, TimeoutError) or isinstance(getattr(exc, "reason", None), TimeoutError):
        return f"{name} took too long to answer."
    if isinstance(exc, (urllib.error.URLError, ConnectionError)):
        return f"{name} couldn't be reached."
    if isinstance(exc, ValueError):
        return f"{name} sent something Row-Bot couldn't read."
    return f"{name} couldn't be searched."


def _log_outcome(source_id: str, operation: str, outcome: str, started: float, entries: int = 0) -> None:
    logger.info(
        "Skills hub %s %s: %s in %d ms, %d entries",
        source_id, operation, outcome, int((time.perf_counter() - started) * 1000), entries,
    )


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
        logger.warning("Skills hub %s %s failed: %s", source_id, operation, type(exc).__name__)
        return SourceResult(
            [],
            source_id,
            "error",
            _failure_message(exc, _display_name(source)),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
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
