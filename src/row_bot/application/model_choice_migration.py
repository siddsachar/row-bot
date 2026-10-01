"""Keep what finished profiles were effectively using when presets went away.

Before onboarding version 4 a profile that never saved a model silently ran on
built-in presets (chat ``qwen3:14b`` on Ollama, Vision ``gemma3:4b``, and the
image and video tools on ``gpt-image-1.5`` / ``veo-3.1`` whenever a cloud
provider was connected). The presets are gone (decision 9). A profile that
finished setup under the old rules gets exactly the value it was running with,
written once as its own choice, so upgrading never changes what it uses.

Values a profile saved are never read differently, rewritten or cleared, and a
profile that never finished setup (or finished it after the change) gets
nothing: it chooses its first model in Setup.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from row_bot.data_paths import get_row_bot_data_dir

logger = logging.getLogger(__name__)

FIRST_VERSION_WITHOUT_PRESETS = 4
LEGACY_CHAT_MODEL = "model:ollama:qwen3:14b"
LEGACY_VISION_MODEL = "gemma3:4b"
LEGACY_MEDIA_MODELS = {
    "image_gen": "openai/gpt-image-1.5",
    "video_gen": "google/veo-3.1-generate-preview",
}

_LOCK = threading.Lock()


def _read_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def is_legacy_finished_profile(data_dir: Path | None = None) -> bool:
    """A profile that finished setup while presets still applied."""
    config = _read_json((data_dir or get_row_bot_data_dir(create=False)) / "app_config.json")
    if not config or not config.get("setup_complete"):
        return False
    try:
        version = int(config.get("onboarding_version") or 0)
    except (TypeError, ValueError):
        version = 0
    return version < FIRST_VERSION_WITHOUT_PRESETS


def _migrate_chat(data_dir: Path) -> bool:
    import sys

    from row_bot.providers import saved_model_settings as settings

    path = data_dir / "model_settings.json"
    raw, _revision, _exists = settings.read_saved_model_settings(path)
    if "model" in raw:
        return False
    written = settings.update_saved_model_settings(
        lambda current: current if "model" in current else {**current, "model": LEGACY_CHAT_MODEL},
        path=path,
    )
    runtime = sys.modules.get("row_bot.models")
    if (runtime is not None and not runtime.get_current_model()
            and Path(runtime._SETTINGS_PATH).resolve() == path.resolve()):
        runtime.adopt_saved_default(str(written.get("model") or ""))
    return True


def _migrate_vision(data_dir: Path) -> bool:
    path = data_dir / "vision_settings.json"
    saved = _read_json(path)
    if saved is None or "model" in saved:
        return False
    payload = {
        "model": LEGACY_VISION_MODEL,
        "camera_index": saved.get("camera_index", 0),
        "enabled": saved.get("enabled", True),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    try:
        from row_bot.vision_runtime import get_vision_service

        service = get_vision_service()
        if not service.model:
            service._model = LEGACY_VISION_MODEL
    except Exception:
        logger.debug("Vision service not ready for the legacy model", exc_info=True)
    return True


def _migrate_media(tool_name: str) -> bool:
    from row_bot.tools import registry

    tool = registry.get_tool(tool_name)
    if tool is None:
        return False
    saved = _read_json(registry._config_path()) or {}
    tools_map = saved.get("tools", saved) if isinstance(saved.get("tools"), dict) else saved
    configs = saved.get("tool_configs") if isinstance(saved.get("tool_configs"), dict) else {}
    tool_config = configs.get(tool_name) if isinstance(configs.get(tool_name), dict) else {}
    if tool_config.get("model"):
        return False
    if tool_name in tools_map:
        was_on = bool(tools_map[tool_name])
    else:
        # The old default: on whenever any cloud provider was connected.
        from row_bot.models import is_cloud_available

        was_on = is_cloud_available()
    if not was_on:
        return False
    registry.set_tool_config(tool_name, "model", LEGACY_MEDIA_MODELS[tool_name])
    if tool_name not in tools_map:
        registry.set_enabled(tool_name, True)
    return True


def migrate_legacy_presets(data_dir: Path | None = None) -> list[str]:
    """Write the old presets once for finished pre-change profiles; idempotent."""
    from row_bot.docs_capture import is_docs_real_data_capture

    if is_docs_real_data_capture():
        return []
    root = data_dir or get_row_bot_data_dir(create=False)
    with _LOCK:
        if not is_legacy_finished_profile(root):
            return []
        written: list[str] = []
        for name, step in (
            ("chat", lambda: _migrate_chat(root)),
            ("vision", lambda: _migrate_vision(root)),
            ("image", lambda: _migrate_media("image_gen")),
            ("video", lambda: _migrate_media("video_gen")),
        ):
            try:
                if step():
                    written.append(name)
            except Exception:
                logger.warning("Could not keep the %s model this profile was using", name, exc_info=True)
        if written:
            logger.info("Kept the models this profile was using before presets were removed: %s", ", ".join(written))
        return written
