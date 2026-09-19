"""Allowlisted, passive views of saved provider and model catalog metadata.

Reading these views never discovers models, changes defaults or refreshes a
provider. Scoped Models availability uses local credential state and saved model
metadata; the legacy passive Providers view leaves runtime readiness unknown.
"""
from __future__ import annotations

import base64
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import re
import time
from typing import Literal

from row_bot.providers.catalog import PROVIDER_DEFINITIONS
from row_bot.providers.config import load_provider_config
from row_bot.providers.custom import normalize_custom_endpoint
from row_bot.providers.model_catalog import CatalogModelRow, build_saved_model_catalog_rows, project_saved_catalog_readiness
from row_bot.providers.model_catalog_cache import (
    CATALOG_CACHE_TTL_SECONDS,
    is_model_catalog_refresh_running,
    read_model_catalog_cache,
)

Freshness = Literal["fresh", "stale", "unavailable"]
CatalogState = Literal["cached", "verified_empty", "unavailable", "error"]
ProviderGroup = Literal["local", "subscription", "api", "custom"]
_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
_MODALITIES = ("text", "image", "audio", "video")


class ProviderStatusError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class CachedReasoning:
    supported_efforts: tuple[str, ...]
    default_effort: str | None
    default_enabled: bool | None
    can_disable: bool
    mandatory: bool
    supports_budget: bool
    budget_min: int | None
    budget_max: int | None
    thinking_mode: Literal["none", "toggle", "manual"]


@dataclass(frozen=True)
class CachedModelRow:
    provider_id: str
    model_id: str
    selection_ref: str
    display_name: str
    provider_display_name: str
    categories: tuple[str, ...]
    input_modalities: tuple[str, ...]
    output_modalities: tuple[str, ...]
    tool_calling: bool | None
    reasoning: CachedReasoning | None
    context_window: int | None
    installed: bool | None
    pinned_surfaces: tuple[str, ...]
    configured: bool
    runtime_ready: bool
    status_reason: str
    runtime_mode: str
    source: str
    runtime_state: Literal["unknown", "ready", "unavailable"] = "unknown"


@dataclass(frozen=True)
class ProviderStatusRow:
    provider_id: str
    display_name: str
    group: ProviderGroup
    auth_methods: tuple[str, ...]
    catalog_state: CatalogState
    model_count: int | None
    enabled: bool | None
    runtime_state: Literal["unknown"] = "unknown"


@dataclass(frozen=True)
class ProviderStatusSnapshot:
    schema_version: int
    revision: str
    generated_at: float | None
    freshness: Freshness
    refresh_running: bool
    providers: tuple[ProviderStatusRow, ...]
    total_models: int


@dataclass(frozen=True)
class CachedModelPage:
    schema_version: int
    revision: str
    generated_at: float | None
    freshness: Freshness
    items: tuple[CachedModelRow, ...]
    total: int
    next_cursor: str | None


def _label(value: object, fallback: str) -> str:
    # Text, never HTML. The frontend renders this through its escaping primitive.
    return ("".join(c for c in value if c.isprintable())[:256] or fallback) if isinstance(value, str) else fallback


def _identity(value: object, maximum: int) -> bool:
    return isinstance(value, str) and 0 < len(value) <= maximum and all(c.isprintable() for c in value)


def _boolean(value: object) -> bool | None:
    return value if type(value) is bool else None


def _integer(value: object) -> int | None:
    return value if type(value) is int and 0 < value <= 2**53 - 1 else None


def _provider_identity(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", value) is not None


def _reasoning(value: object) -> CachedReasoning | None:
    if not isinstance(value, dict):
        return None
    efforts = value.get("supported_efforts")
    efforts = efforts if isinstance(efforts, (list, tuple)) else []
    default = value.get("default_effort")
    mode = value.get("thinking_mode")
    return CachedReasoning(
        tuple(effort for effort in _EFFORTS if effort in efforts),
        default if default in _EFFORTS else None,
        _boolean(value.get("default_enabled")), value.get("can_disable") is True,
        value.get("mandatory") is True, value.get("supports_budget") is True,
        _integer(value.get("budget_min")), _integer(value.get("budget_max")),
        mode if mode in ("none", "toggle", "manual") else "none",
    )


def _public_model(row: CatalogModelRow, *, provider_label: str, installed: bool | None,
                  include_readiness: bool) -> CachedModelRow:
    snapshot = row.capabilities_snapshot
    return CachedModelRow(
        row.provider_id, row.model_id, row.selection_ref, _label(row.display_name, row.model_id),
        provider_label, row.categories,
        tuple(item for item in _MODALITIES if item in snapshot.get("input_modalities", ())),
        tuple(item for item in _MODALITIES if item in snapshot.get("output_modalities", ())),
        _boolean(snapshot.get("tool_calling")), _reasoning(snapshot.get("reasoning")),
        _integer(row.context_window), installed, row.pinned_surfaces,
        row.configured, row.runtime_ready, _label(row.status_reason, ""),
        row.runtime_mode, row.source,
        "ready" if include_readiness and row.configured and row.runtime_ready and installed is True
        else "unavailable" if include_readiness else "unknown",
    )


def _read(now: float | None, *, include_readiness: bool = False) -> tuple[ProviderStatusSnapshot, tuple[CachedModelRow, ...]]:
    saved = read_model_catalog_cache(allow_runtime_bootstrap=False)
    config = load_provider_config()
    rows = build_saved_model_catalog_rows(
        cloud_cache=saved.cloud_cache, ollama_rows=saved.ollama_rows, provider_config=config,
    )
    if include_readiness:
        rows = project_saved_catalog_readiness(rows)
    rows = [row for row in rows if _provider_identity(row.provider_id) and _identity(row.model_id, 512)]
    endpoints = {}
    for raw in config.get("custom_endpoints", []):
        if not isinstance(raw, dict):
            continue
        try:
            endpoint = normalize_custom_endpoint(raw)
        except (ValueError, TypeError, OverflowError):
            continue
        if _provider_identity(endpoint["provider_id"]):
            endpoint["display_name"] = _label(raw.get("name") or raw.get("display_name"), endpoint["provider_id"])
            endpoints[endpoint["provider_id"]] = endpoint
    labels = {key: definition.display_name for key, definition in PROVIDER_DEFINITIONS.items()}
    labels.update({key: _label(item["display_name"], key) for key, item in endpoints.items()})
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.provider_id] = counts.get(row.provider_id, 0) + 1
        labels.setdefault(row.provider_id, row.provider_id)
    local_install = {item["model_id"]: _boolean(item.get("installed")) for item in saved.ollama_rows if isinstance(item.get("model_id"), str)}
    models = tuple(sorted((
        _public_model(row, provider_label=labels[row.provider_id], include_readiness=include_readiness,
                      installed=local_install.get(row.model_id) if row.provider_id == "ollama" else True if include_readiness else None)
        for row in rows
    ), key=lambda row: (row.provider_display_name.casefold(), row.display_name.casefold(), row.provider_id, row.model_id)))
    providers = []
    for key, label in sorted(labels.items(), key=lambda item: (item[1].casefold(), item[0])):
        definition = PROVIDER_DEFINITIONS.get(key)
        endpoint = endpoints.get(key)
        state: CatalogState = "cached" if key in counts else "unavailable"
        count = counts.get(key)
        status = saved.provider_status.get(key, {})
        if count is None:
            if status.get("status") in ("error", "failed"):
                state = "error"
            elif status.get("status") in ("ok", "live", "empty_verified") and type(status.get("count")) is int and status["count"] == 0:
                state, count = "verified_empty", 0
        group: ProviderGroup = "custom" if key.startswith("custom_openai_") else "local" if key == "ollama" else "subscription" if definition and definition.risk_label == "subscription" else "api"
        entry = config.get("providers", {}).get(key, {})
        enabled = endpoint["enabled"] if endpoint else _boolean(entry.get("enabled")) if isinstance(entry, dict) else None
        providers.append(ProviderStatusRow(
            key, label, group,
            tuple(method.value for method in definition.auth_methods) if definition else ("api_key", "none") if endpoint else (),
            state, count, enabled,
        ))
    generated = saved.generated_at if math.isfinite(saved.generated_at) and saved.generated_at > 0 else None
    clock = time.time() if now is None else now
    freshness: Freshness = "unavailable" if generated is None else "stale" if clock - generated >= CATALOG_CACHE_TTL_SECONDS else "fresh"
    public = {"generated_at": generated, "providers": [asdict(row) for row in providers], "models": [asdict(row) for row in models]}
    revision = hashlib.sha256(json.dumps(public, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()
    return ProviderStatusSnapshot(1, revision, generated, freshness, is_model_catalog_refresh_running(), tuple(providers), len(models)), models


def read_provider_snapshot(*, now: float | None = None) -> ProviderStatusSnapshot:
    """Read public saved status; no refresh or authentication side effects."""
    return _read(now)[0]


def list_cached_models(
    *, provider_id: str | None = None, query: str = "", cursor: str | None = None,
    limit: int = 50, surface: str | None = None, readiness: bool = False,
    now: float | None = None,
) -> CachedModelPage:
    """Search the entire saved catalog, then page a stable public revision."""
    if type(limit) is not int or not 1 <= limit <= 100 or not isinstance(query, str) or len(query) > 256 or (provider_id is not None and not _identity(provider_id, 128)) or (surface is not None and surface not in {"chat", "vision", "image", "video", "voice"}):
        raise ProviderStatusError("invalid_catalog_query")
    query = query.strip().casefold()
    snapshot, models = _read(now, include_readiness=readiness)
    matches = tuple(row for row in models if (surface is None or surface in row.categories) and (provider_id is None or row.provider_id == provider_id) and (
        not query or any(query in value.casefold() for value in (row.model_id, row.display_name, row.provider_display_name, row.provider_id))
    ))
    offset = 0
    if cursor is not None:
        try:
            if not isinstance(cursor, str) or len(cursor) > 2048:
                raise ValueError
            fields = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
            if not isinstance(fields, list) or len(fields) != (5 if surface else 4):
                raise ValueError
            revision, previous_provider, previous_query, offset = fields[:4]
            if revision != snapshot.revision or previous_provider != provider_id or previous_query != query or (surface and fields[4] != surface) or type(offset) is not int or not 0 <= offset <= len(matches):
                raise ValueError
        except (ValueError, TypeError, UnicodeError) as exc:
            raise ProviderStatusError("cursor_expired") from exc
    end = min(len(matches), offset + limit)
    next_cursor = base64.urlsafe_b64encode(json.dumps([snapshot.revision, provider_id, query, end] + ([surface] if surface else [])).encode()).decode() if end < len(matches) else None
    return CachedModelPage(1, snapshot.revision, snapshot.generated_at, snapshot.freshness, matches[offset:end], len(matches), next_cursor)
