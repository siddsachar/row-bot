from __future__ import annotations

import json
import logging
import pathlib
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from row_bot.application.live_updates import publish as _publish_live
from row_bot.data_paths import get_row_bot_data_dir

logger = logging.getLogger(__name__)

CACHE_VERSION = 1
CATALOG_CACHE_TTL_SECONDS = 6 * 60 * 60
_DATA_DIR = get_row_bot_data_dir(create=False)
CATALOG_CACHE_PATH = _DATA_DIR / "model_catalog_cache.json"

_refresh_lock = threading.Lock()
_refresh_state_lock = threading.Lock()
_refresh_state: dict[str, Any] = {
    "running": False,
    "started_at": 0.0,
    "finished_at": 0.0,
    "reason": "",
    "last_result": None,
}
# Providers that changed while a refresh was running: refreshed again once it ends (id -> reason).
_follow_ups: dict[str, str] = {}


@dataclass(frozen=True)
class CatalogCacheSnapshot:
    version: int
    generated_at: float
    cloud_cache: dict[str, dict[str, Any]]
    ollama_rows: list[dict[str, Any]]
    provider_status: dict[str, dict[str, Any]]
    warnings: tuple[str, ...]
    reason: str = ""

    @property
    def age_seconds(self) -> float:
        if self.generated_at <= 0:
            return float("inf")
        return max(0.0, time.time() - self.generated_at)

    @property
    def is_empty(self) -> bool:
        return not self.cloud_cache and not self.ollama_rows

    @property
    def is_stale(self) -> bool:
        return self.is_empty or self.age_seconds >= CATALOG_CACHE_TTL_SECONDS

    @property
    def total_rows(self) -> int:
        return len(self.cloud_cache) + len(self.ollama_rows)


def empty_catalog_cache() -> CatalogCacheSnapshot:
    return CatalogCacheSnapshot(
        version=CACHE_VERSION,
        generated_at=0.0,
        cloud_cache={},
        ollama_rows=[],
        provider_status={},
        warnings=(),
        reason="empty",
    )


def read_model_catalog_cache(
    path: pathlib.Path | None = None, *, allow_runtime_bootstrap: bool = True,
    max_bytes: int | None = None,
) -> CatalogCacheSnapshot:
    """Read saved catalog metadata; passive clients must disable runtime bootstrap."""
    if max_bytes is not None and (type(max_bytes) is not int or max_bytes < 1):
        raise ValueError("invalid_catalog_read_limit")
    cache_path = path or CATALOG_CACHE_PATH
    try:
        if cache_path.exists():
            if max_bytes is None:
                payload = json.loads(cache_path.read_text(encoding="utf-8"))
            else:
                with cache_path.open("rb") as source:
                    captured = source.read(max_bytes + 1)
                if len(captured) > max_bytes:
                    return empty_catalog_cache()
                payload = json.loads(captured.decode("utf-8"))
            snapshot = _snapshot_from_payload(payload)
            if snapshot.version == CACHE_VERSION:
                return snapshot
            logger.warning("Ignoring model catalog cache with unsupported version: %s", snapshot.version)
    except Exception:
        if max_bytes is not None:
            # The bounded passive path never returns truncated metadata, logs
            # private paths/content, or falls back to another runtime snapshot.
            return empty_catalog_cache()
        logger.warning("Failed to load model catalog cache from %s", cache_path, exc_info=True)
    return _bootstrap_snapshot_from_runtime() if allow_runtime_bootstrap else empty_catalog_cache()


def write_model_catalog_cache(snapshot: CatalogCacheSnapshot, path: pathlib.Path | None = None) -> None:
    cache_path = path or CATALOG_CACHE_PATH
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(_snapshot_to_payload(snapshot), indent=2), encoding="utf-8")
    tmp_path.replace(cache_path)


def refresh_model_catalog_cache(
    *,
    reason: str = "manual",
    force: bool = False,
    provider_id: str | None = None,
) -> CatalogCacheSnapshot:
    """Refresh model catalog metadata synchronously.

    This is intended to run in a worker thread. It always preserves the
    previous cache if refresh work fails before a usable snapshot is produced.
    """
    previous = read_model_catalog_cache()
    if not force and not previous.is_stale:
        logger.info(
            "model catalog cache refresh skipped; cache fresh age=%.0fs reason=%s",
            previous.age_seconds,
            reason,
        )
        return previous

    started = time.perf_counter()
    warnings: list[str] = []
    provider_status: dict[str, dict[str, Any]] = {}
    cloud_cache = dict(previous.cloud_cache)
    ollama_rows = list(previous.ollama_rows)

    try:
        from row_bot.models import get_current_model

        get_current_model()
    except Exception:
        logger.debug("Could not validate current model during catalog refresh", exc_info=True)

    try:
        cloud_cache, cloud_status = _refresh_cloud_cache(provider_id=provider_id)
        provider_status.update(cloud_status)
        if provider_id:
            targeted = cloud_status.get(provider_id, {})
            if targeted.get("status") in {"cached", "fallback", "empty"} and targeted.get("message"):
                warnings.append(f"{provider_id}: {targeted['message']}")
    except Exception as exc:
        warnings.append(f"Cloud providers refresh failed: {exc}")
        logger.warning("Cloud model catalog refresh failed; preserving previous cloud cache", exc_info=True)

    # A refresh for another provider (Claude once its plan is ready, say) still lists Ollama when the cache
    # has no Ollama rows yet: it writes a fresh cache, so the startup refresh after it would skip them.
    should_refresh_ollama = provider_id in {None, "", "ollama", "ollama_cloud"} or not previous.ollama_rows
    if should_refresh_ollama:
        try:
            ollama_rows = _refresh_ollama_rows()
            provider_status["ollama"] = {"status": "ok", "count": len(ollama_rows)}
        except Exception as exc:
            warnings.append(f"Ollama catalog refresh failed: {exc}")
            logger.warning("Ollama model catalog refresh failed; preserving previous Ollama rows", exc_info=True)

    snapshot = CatalogCacheSnapshot(
        version=CACHE_VERSION,
        generated_at=time.time(),
        cloud_cache=cloud_cache,
        ollama_rows=ollama_rows,
        provider_status=provider_status,
        warnings=tuple(warnings),
        reason=reason,
    )
    if snapshot.is_empty and not previous.is_empty:
        logger.warning("Model catalog refresh produced an empty cache; preserving previous last-known-good cache")
        return previous
    write_model_catalog_cache(snapshot)
    logger.info(
        "perf: model catalog cache refreshed in %.3fs rows=%d warnings=%d reason=%s provider=%s",
        time.perf_counter() - started,
        snapshot.total_rows,
        len(snapshot.warnings),
        reason,
        provider_id or "all",
    )
    return snapshot


def start_model_catalog_refresh_background(
    *,
    reason: str = "manual",
    force: bool = False,
    provider_id: str | None = None,
    follow_up: bool = False,
) -> bool:
    """Start a coalesced daemon-thread refresh. Returns False if one is already running.

    ``follow_up``: *provider_id* has just changed, so a refresh already running may
    have read it before the change. It is then refreshed again as soon as that
    one ends, instead of not at all. Every finished refresh publishes the
    ``models`` live topic, so an open Models page reads the catalog again.
    """
    with _refresh_state_lock:
        if _refresh_state.get("running"):
            if follow_up and provider_id:
                _follow_ups[provider_id] = reason
            return False
        _refresh_state.update({
            "running": True,
            "started_at": time.time(),
            "finished_at": 0.0,
            "reason": reason,
            "last_result": None,
        })

    def _runner() -> None:
        request: tuple[str, bool, str | None] | None = (reason, force, provider_id)
        while request is not None:
            result = _refresh_once(*request)
            with _refresh_state_lock:
                if _follow_ups:
                    target, why = _follow_ups.popitem()
                    request = (why, True, target)
                    _refresh_state.update({"reason": why, "last_result": result})
                else:
                    request = None
                    _refresh_state.update({
                        "running": False,
                        "finished_at": time.time(),
                        "last_result": result,
                    })
            _publish_live("models")

    thread = threading.Thread(target=_runner, name="model-catalog-refresh", daemon=True)
    thread.start()
    return True


def _refresh_once(reason: str, force: bool, provider_id: str | None) -> dict[str, Any]:
    if not _refresh_lock.acquire(blocking=False):
        return {"ok": False, "coalesced": True, "message": "Refresh already running"}
    try:
        snapshot = refresh_model_catalog_cache(reason=reason, force=force, provider_id=provider_id)
        return {
            "ok": True,
            "rows": snapshot.total_rows,
            "warnings": list(snapshot.warnings),
            "provider_status": snapshot.provider_status,
            "provider_id": provider_id or "",
            "generated_at": snapshot.generated_at,
            "reason": reason,
        }
    except Exception as exc:
        logger.warning("Background model catalog refresh failed", exc_info=True)
        return {"ok": False, "message": str(exc), "reason": reason}
    finally:
        _refresh_lock.release()


def provider_connection_changed(provider_id: str, *, connected: bool) -> None:
    """A provider was signed in to or given a key, or disconnected: Settings › Models shows it at once (B411).

    A connection refreshes that provider's catalog in the background, so its models
    are listed without the person pressing Refresh. A disconnect asks nobody: every
    catalog read assesses readiness from local state, so open pages only read again.
    The connection changed either way, so a refresh that can't start is logged, never raised.
    """
    if not connected:
        _publish_live("models")
        return
    try:
        start_model_catalog_refresh_background(reason="provider_connection", force=True,
                                               provider_id=provider_id, follow_up=True)
    except Exception:
        logger.warning("Could not start refreshing the %s model catalog", provider_id, exc_info=True)


def model_catalog_refresh_state() -> dict[str, Any]:
    with _refresh_state_lock:
        return dict(_refresh_state)


def is_model_catalog_refresh_running() -> bool:
    return bool(model_catalog_refresh_state().get("running"))


def schedule_model_catalog_refresh_jobs() -> None:
    """Schedule delayed startup and periodic model catalog refresh jobs."""
    try:
        from row_bot.tasks import _get_scheduler

        scheduler = _get_scheduler()
        scheduler.add_job(
            lambda: start_model_catalog_refresh_background(reason="startup", force=False),
            trigger="date",
            run_date=datetime.now() + timedelta(seconds=45),
            id="model_catalog_startup_refresh",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        scheduler.add_job(
            lambda: start_model_catalog_refresh_background(reason="scheduled", force=False),
            trigger="interval",
            hours=6,
            id="model_catalog_periodic_refresh",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        logger.info("Model catalog cache refresh scheduled (startup delay + every 6 h)")
    except Exception:
        logger.warning("Could not schedule model catalog cache refresh", exc_info=True)


def _refresh_cloud_cache(*, provider_id: str | None = None) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    import row_bot.models as models

    provider_status: dict[str, dict[str, Any]] = {}
    if provider_id:
        models.fetch_context_catalog()
        result = models.refresh_cloud_provider_models(provider_id)
        models._save_cloud_cache()
        provider_status[provider_id] = result.status_payload()
    else:
        results = models.refresh_cloud_models_detailed()
        provider_status.update({
            result_provider_id: result.status_payload()
            for result_provider_id, result in results.items()
        })
    with models._cloud_cache_lock:
        cloud_cache = {
            str(model_id): dict(info)
            for model_id, info in models._cloud_model_cache.items()
            if isinstance(info, dict)
        }
    return cloud_cache, provider_status


def _refresh_ollama_rows() -> list[dict[str, Any]]:
    from row_bot.providers.model_catalog import load_ollama_catalog_rows

    return [dict(row) for row in load_ollama_catalog_rows()]


def _bootstrap_snapshot_from_runtime() -> CatalogCacheSnapshot:
    cloud_cache: dict[str, dict[str, Any]] = {}
    try:
        import row_bot.models as models

        with models._cloud_cache_lock:
            cloud_cache = {
                str(model_id): dict(info)
                for model_id, info in models._cloud_model_cache.items()
                if isinstance(info, dict)
            }
    except Exception:
        cloud_cache = {}
    generated_at = time.time() if cloud_cache else 0.0
    return CatalogCacheSnapshot(
        version=CACHE_VERSION,
        generated_at=generated_at,
        cloud_cache=cloud_cache,
        ollama_rows=[],
        provider_status={"bootstrap": {"status": "ok", "count": len(cloud_cache)}} if cloud_cache else {},
        warnings=(),
        reason="bootstrap",
    )


def _snapshot_from_payload(payload: Any) -> CatalogCacheSnapshot:
    if not isinstance(payload, dict):
        return empty_catalog_cache()
    cloud_cache = {
        str(model_id): dict(info)
        for model_id, info in (payload.get("cloud_cache") or {}).items()
        if isinstance(info, dict)
    } if isinstance(payload.get("cloud_cache"), dict) else {}
    ollama_rows = [
        dict(row)
        for row in (payload.get("ollama_rows") or [])
        if isinstance(row, dict)
    ] if isinstance(payload.get("ollama_rows"), list) else []
    provider_status = {
        str(provider): dict(status)
        for provider, status in (payload.get("provider_status") or {}).items()
        if isinstance(status, dict)
    } if isinstance(payload.get("provider_status"), dict) else {}
    warnings = tuple(str(item) for item in payload.get("warnings") or [] if str(item))
    return CatalogCacheSnapshot(
        version=int(payload.get("version") or 0),
        generated_at=float(payload.get("generated_at") or 0.0),
        cloud_cache=cloud_cache,
        ollama_rows=ollama_rows,
        provider_status=provider_status,
        warnings=warnings,
        reason=str(payload.get("reason") or ""),
    )


def _snapshot_to_payload(snapshot: CatalogCacheSnapshot) -> dict[str, Any]:
    return {
        "version": snapshot.version,
        "generated_at": snapshot.generated_at,
        "generated_at_iso": datetime.fromtimestamp(snapshot.generated_at).isoformat() if snapshot.generated_at else "",
        "reason": snapshot.reason,
        "cloud_cache": snapshot.cloud_cache,
        "ollama_rows": snapshot.ollama_rows,
        "provider_status": snapshot.provider_status,
        "warnings": list(snapshot.warnings),
    }
