"""Typed local settings for full agent loops and child delegation capacity."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from row_bot.data_paths import get_row_bot_data_dir


logger = logging.getLogger(__name__)
_SETTINGS_LOCK = threading.RLock()
_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class AgentRuntimeSettings:
    """Application-wide capacity for new logical agent turns and child runs."""

    schema_version: int = _SCHEMA_VERSION
    max_iterations: int = 90
    max_spawn_depth: int = 1
    max_concurrent_children: int = 3
    max_active_children_global: int = 8
    child_timeout_seconds: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def agent_runtime_settings_path() -> Path:
    """Resolve the active local settings path at call time."""

    return get_row_bot_data_dir() / "agent_settings.json"


def _strict_int(value: Any, field: str, *, allow_zero: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be a whole number.")
    if value < 0 or (not allow_zero and value == 0):
        qualifier = "zero or greater" if allow_zero else "greater than zero"
        raise ValueError(f"{field} must be {qualifier}.")
    return value


def validate_agent_runtime_settings(
    settings: AgentRuntimeSettings | Mapping[str, Any],
) -> AgentRuntimeSettings:
    """Return a validated immutable settings value or raise ``ValueError``."""

    raw = asdict(settings) if isinstance(settings, AgentRuntimeSettings) else dict(settings)
    if raw.get("schema_version", _SCHEMA_VERSION) != _SCHEMA_VERSION:
        raise ValueError("Unsupported agent settings schema version.")
    return AgentRuntimeSettings(
        schema_version=_SCHEMA_VERSION,
        max_iterations=_strict_int(raw.get("max_iterations"), "Maximum work rounds"),
        max_spawn_depth=_strict_int(raw.get("max_spawn_depth"), "Delegation depth"),
        max_concurrent_children=_strict_int(
            raw.get("max_concurrent_children"),
            "Children per parent",
        ),
        max_active_children_global=_strict_int(
            raw.get("max_active_children_global"),
            "Active children",
        ),
        child_timeout_seconds=_strict_int(
            raw.get("child_timeout_seconds"),
            "Child timeout",
            allow_zero=True,
        ),
    )


def load_agent_runtime_settings() -> AgentRuntimeSettings:
    """Load validated local settings, falling back safely without rewriting."""

    path = agent_runtime_settings_path()
    with _SETTINGS_LOCK:
        if not path.exists():
            return AgentRuntimeSettings()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("Agent settings must be a JSON object.")
            return validate_agent_runtime_settings(raw)
        except Exception as exc:
            logger.warning("Ignoring invalid local agent settings at %s: %s", path, exc)
            return AgentRuntimeSettings()


def _notify_dispatcher() -> None:
    try:
        from row_bot.agent_runner import notify_agent_runtime_settings_changed

        notify_agent_runtime_settings_changed()
    except (ImportError, AttributeError):
        return
    except Exception:
        logger.debug("Child dispatcher settings notification failed", exc_info=True)


def save_agent_runtime_settings(
    settings: AgentRuntimeSettings | Mapping[str, Any],
) -> AgentRuntimeSettings:
    """Validate and atomically persist one complete application-wide snapshot."""

    validated = validate_agent_runtime_settings(settings)
    path = agent_runtime_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _SETTINGS_LOCK:
        fd, temporary = tempfile.mkstemp(
            prefix=f"{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        temporary_path = Path(temporary)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(asdict(validated), handle, indent=2, sort_keys=True)
                handle.write("\n")
            temporary_path.replace(path)
        finally:
            temporary_path.unlink(missing_ok=True)
    _notify_dispatcher()
    return validated


def reset_agent_runtime_settings() -> AgentRuntimeSettings:
    """Restore and persist the reviewed application defaults."""

    return save_agent_runtime_settings(AgentRuntimeSettings())
