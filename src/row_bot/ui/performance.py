"""Small UI performance and safety helpers."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Generator
from contextlib import contextmanager
import logging
from time import perf_counter
from typing import Any, TypeVar

from row_bot.ui.timer_utils import _is_benign_dead_ui_error, safe_ui_task as _timer_safe_ui_task

logger = logging.getLogger("row_bot.ui.performance")

T = TypeVar("T")

UI_SHELL_WARN_MS = 500.0
UI_DATA_WARN_MS = 1000.0
UI_COMPONENT_WARN_COUNT = 250
UI_PAYLOAD_WARN_CHARS = 250_000


class LoadGeneration:
    """Monotonic generation token for stale-safe UI loads."""

    def __init__(self) -> None:
        self._value = 0

    @property
    def current(self) -> int:
        return self._value

    def next(self) -> int:
        self._value += 1
        return self._value

    def invalidate(self) -> int:
        return self.next()

    def is_current(self, token: int) -> bool:
        return token == self._value


def _metadata_string(metadata: dict[str, Any]) -> str:
    if not metadata:
        return ""
    parts = []
    for key, value in sorted(metadata.items()):
        if value is None:
            continue
        parts.append(f"{key}={value}")
    return " ".join(parts)


def warn_if_slow(
    name: str,
    elapsed_ms: float,
    *,
    threshold_ms: float = UI_DATA_WARN_MS,
    **metadata: Any,
) -> bool:
    """Log a warning and performance snapshot when a UI section is slow."""

    if elapsed_ms < threshold_ms:
        return False
    suffix = _metadata_string(metadata)
    logger.warning(
        "ui perf slow: %s took %.1fms threshold=%.1fms%s%s",
        name,
        elapsed_ms,
        threshold_ms,
        " " if suffix else "",
        suffix,
    )
    try:
        from row_bot.stability import log_performance_snapshot

        log_performance_snapshot(f"ui-slow:{name}")
    except Exception:
        logger.debug("Could not write UI performance snapshot", exc_info=True)
    return True


def warn_if_many_components(
    name: str,
    components: int,
    *,
    limit: int = UI_COMPONENT_WARN_COUNT,
    elapsed_ms: float | None = None,
    **metadata: Any,
) -> bool:
    if components <= limit:
        return False
    fields = dict(metadata)
    fields.update({
        "components": components,
        "component_warn_count": limit,
    })
    if elapsed_ms is not None:
        fields["elapsed_ms"] = round(elapsed_ms, 1)
    suffix = _metadata_string(fields)
    logger.warning("ui perf component count high: %s%s%s", name, " " if suffix else "", suffix)
    return True


def warn_if_large_payload(
    name: str,
    payload_chars: int,
    *,
    limit: int = UI_PAYLOAD_WARN_CHARS,
    elapsed_ms: float | None = None,
    **metadata: Any,
) -> bool:
    if payload_chars <= limit:
        return False
    fields = dict(metadata)
    fields.update({
        "payload_chars": payload_chars,
        "payload_warn_chars": limit,
    })
    if elapsed_ms is not None:
        fields["elapsed_ms"] = round(elapsed_ms, 1)
    suffix = _metadata_string(fields)
    logger.warning("ui perf payload large: %s%s%s", name, " " if suffix else "", suffix)
    return True


def log_ui_perf(
    name: str,
    elapsed_ms: float,
    *,
    rows: int | None = None,
    components: int | None = None,
    payload_chars: int | None = None,
    threshold_ms: float | None = None,
    **metadata: Any,
) -> None:
    """Log structured-ish UI timing metadata."""

    fields = dict(metadata)
    if rows is not None:
        fields["rows"] = rows
    if components is not None:
        fields["components"] = components
    if payload_chars is not None:
        fields["payload_chars"] = payload_chars
    suffix = _metadata_string(fields)
    logger.info("ui perf: %s took %.1fms%s%s", name, elapsed_ms, " " if suffix else "", suffix)

    slow_threshold = threshold_ms if threshold_ms is not None else UI_DATA_WARN_MS
    warn_if_slow(name, elapsed_ms, threshold_ms=slow_threshold, **fields)
    if components is not None and components > UI_COMPONENT_WARN_COUNT:
        warn_if_many_components(
            name,
            components=components,
            limit=UI_COMPONENT_WARN_COUNT,
            elapsed_ms=elapsed_ms,
            **{key: value for key, value in fields.items() if key != "components"},
        )
    if payload_chars is not None and payload_chars > UI_PAYLOAD_WARN_CHARS:
        warn_if_large_payload(
            name,
            payload_chars=payload_chars,
            limit=UI_PAYLOAD_WARN_CHARS,
            elapsed_ms=elapsed_ms,
            **{key: value for key, value in fields.items() if key != "payload_chars"},
        )


@contextmanager
def timed_ui_section(
    name: str,
    *,
    threshold_ms: float | None = None,
    **metadata: Any,
) -> Generator[None, None, None]:
    """Context manager that logs elapsed UI render/load time."""

    start = perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (perf_counter() - start) * 1000.0
        log_ui_perf(name, elapsed_ms, threshold_ms=threshold_ms, **metadata)


def safe_ui_callback(
    context: str,
    fn: Callable[..., T],
    *,
    notify: bool = True,
) -> Callable[..., T | None]:
    """Wrap a sync UI callback so failures are recorded and localized."""

    def _wrapped(*args: Any, **kwargs: Any) -> T | None:
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            if _is_benign_dead_ui_error(exc):
                logger.debug("safe_ui_callback skipped dead UI: %s", exc)
                return None
            try:
                from row_bot.stability import record_ui_callback_error

                record_ui_callback_error(context, exc)
            except Exception:
                logger.debug("Could not persist UI callback error", exc_info=True)
            logger.exception("UI callback failed: %s", context)
            if notify:
                try:
                    from nicegui import ui

                    ui.notify("That section hit an error. The rest of Settings is still available.", type="negative")
                except Exception:
                    pass
            return None

    return _wrapped


def safe_ui_task(
    context: str,
    callback: Callable[..., Any] | Awaitable[Any],
) -> asyncio.Task | None:
    """Create a supervised UI task using the existing timer utility wrapper."""

    if asyncio.iscoroutine(callback):
        return _timer_safe_ui_task(lambda: callback, context=context)
    return _timer_safe_ui_task(callback, context=context)
