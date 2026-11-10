"""What models.dev's open catalogue says models can do, for model lists that don't say.

Some providers' model lists name their models and nothing else (OpenAI, Cerebras,
NVIDIA and others). Row-Bot fills those gaps from models.dev (MIT licensed,
https://models.dev): whether a model takes tools and images, how much it reads,
what it outputs and whether it reasons. A provider's own list always wins; this
only fills gaps.

The catalogue is fetched when a provider's models are refreshed, at most once an
hour, and kept in the data folder. Nothing is sent but the request itself. When
the fetch fails, the copy saved earlier or the copy bundled with this release
(refreshed when a release is prepared, ``scripts/refresh_model_capabilities.py``)
stands in, whichever is newer.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CATALOG_URL = "https://models.dev/api.json"
BUNDLED_PATH = Path(__file__).with_name("model_capabilities.json")
_FRESH_SECONDS = 3600
_MAX_BYTES = 32 * 1024 * 1024

# Row-Bot's provider IDs and the models.dev provider each one reads.
PROVIDERS: dict[str, str] = {
    "openai": "openai", "codex": "openai",
    "anthropic": "anthropic", "claude_subscription": "anthropic",
    "google": "google",
    "xai": "xai", "xai_oauth": "xai",
    "openrouter": "openrouter",
    "requesty": "requesty",
    "minimax": "minimax",
    "ollama_cloud": "ollama-cloud",
    "opencode_zen": "opencode", "opencode_go": "opencode-go",
    "deepseek": "deepseek",
    "mistral": "mistral",
    "groq": "groq",
    "moonshot": "moonshotai",
    "zai": "zai",
    "dashscope": "alibaba",
    "together": "togetherai",
    "fireworks": "fireworks-ai",
    "cerebras": "cerebras",
    "huggingface": "huggingface",
    "nvidia": "nvidia",
    "vercel": "vercel",
}


@dataclass(frozen=True)
class ModelCapabilities:
    tool_calling: bool
    vision: bool
    context_window: int
    outputs: frozenset[str]
    reasoning: bool = False


_lock = threading.Lock()
_loaded: dict[str, dict[str, Any]] | None = None


def trim(api: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    """The fields Row-Bot reads, for the providers it uses, from models.dev's ``api.json``."""
    providers: dict[str, dict[str, dict[str, Any]]] = {}
    for provider in sorted(set(PROVIDERS.values())):
        models = api.get(provider, {}).get("models") if isinstance(api.get(provider), dict) else None
        rows = {}
        for model_id, model in sorted(models.items()) if isinstance(models, dict) else ():
            if not isinstance(model, dict):
                continue
            modalities = model.get("modalities") if isinstance(model.get("modalities"), dict) else {}
            limit = model.get("limit") if isinstance(model.get("limit"), dict) else {}
            context = limit.get("context")
            rows[str(model_id)] = {
                "tool_call": model.get("tool_call") is True,
                "vision": "image" in (modalities.get("input") or []),
                "context": context if isinstance(context, int) and context > 0 else 0,
                "output": sorted(str(item) for item in modalities.get("output") or []),
                "reasoning": model.get("reasoning") is True,
            }
        if rows:
            providers[provider] = rows
    return providers


def document(providers: dict[str, dict[str, dict[str, Any]]], fetched_at: str) -> str:
    """The saved and bundled form: the trimmed catalogue, when it was fetched and from where."""
    return json.dumps({"source": CATALOG_URL, "fetched_at": fetched_at, "providers": providers},
                      ensure_ascii=False, indent=1, sort_keys=True)


def _saved_path() -> Path:
    from row_bot.data_paths import get_row_bot_data_dir

    return get_row_bot_data_dir(create=False) / "model_capabilities.json"


def _download() -> bytes:
    import httpx

    response = httpx.get(CATALOG_URL, timeout=30, follow_redirects=True)
    response.raise_for_status()
    if len(response.content) > _MAX_BYTES:
        raise ValueError("the catalogue is larger than expected")
    return response.content


def refresh() -> bool:
    """Fetch the live catalogue unless the saved copy is under an hour old; True when fetched."""
    global _loaded
    path = _saved_path()
    try:
        if time.time() - path.stat().st_mtime < _FRESH_SECONDS:
            return False
    except OSError:
        pass
    try:
        providers = trim(json.loads(_download()))
        if not providers:
            raise ValueError("it lists none of Row-Bot's providers")
    except Exception as exc:
        logger.warning("Couldn't fetch models.dev's catalogue (%s); using the saved or bundled copy", exc)
        return False
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    temporary = path.with_suffix(".tmp")
    temporary.write_text(document(providers, fetched_at), encoding="utf-8")
    temporary.replace(path)
    with _lock:
        _loaded = providers
    return True


def _read(path: Path) -> tuple[str, dict[str, Any]]:
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "", {}
    providers = saved.get("providers") if isinstance(saved, dict) else None
    return (str(saved.get("fetched_at") or ""), providers) if isinstance(providers, dict) else ("", {})


def _catalogue() -> dict[str, Any]:
    global _loaded
    with _lock:
        if _loaded is None:
            # The newer of the copy fetched earlier and the copy bundled with this release.
            _loaded = max(_read(_saved_path()), _read(BUNDLED_PATH), key=lambda item: item[0])[1]
        return _loaded


def lookup(provider_id: str, model_id: str) -> ModelCapabilities | None:
    """What the catalogue says about one of a provider's models, or None when it doesn't know it."""
    row = _catalogue().get(PROVIDERS.get(provider_id, ""), {}).get(model_id)
    if not isinstance(row, dict):
        return None
    return ModelCapabilities(
        tool_calling=row.get("tool_call") is True,
        vision=row.get("vision") is True,
        context_window=row.get("context") if isinstance(row.get("context"), int) else 0,
        outputs=frozenset(str(item) for item in row.get("output") or ()),
        reasoning=row.get("reasoning") is True,
    )
