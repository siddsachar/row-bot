"""First run: detect a local runtime, test the chosen model, check a key.

Decision 10: until a default model exists the React client opens Setup, which
asks one question ("How should Row-Bot think?"). This module answers what that
step needs without choosing anything for the person (decision 9):

- the local runtime: running (with the models it serves), installed but not
  running, or not installed, detected over loopback only;
- another assistant's data that could be imported (a cheap signature check);
- a one-message test of the model the person just chose;
- a check of an API key with its provider before anything is saved.
"""
from __future__ import annotations

import concurrent.futures
import logging
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

OLLAMA_DOWNLOAD_URL = "https://ollama.com/download"
_TEST_PROMPT = "Reply with exactly one word: ready"
_TEST_TIMEOUT_SECONDS = 180.0
_KEY_CHECK_TIMEOUT_SECONDS = 20.0


def platform_name() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _ollama_install_candidates() -> list[Path]:
    home = Path.home()
    if sys.platform == "win32":
        local = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
        return [local / "Programs" / "Ollama" / "ollama app.exe", local / "Programs" / "Ollama" / "ollama.exe"]
    if sys.platform == "darwin":
        return [Path("/Applications/Ollama.app"), home / "Applications" / "Ollama.app",
                Path("/usr/local/bin/ollama"), Path("/opt/homebrew/bin/ollama")]
    return [Path("/usr/local/bin/ollama"), Path("/usr/bin/ollama"), home / ".local" / "bin" / "ollama"]


def ollama_installed() -> bool:
    """Whether the Ollama app or command is on this computer (no process started)."""
    if shutil.which("ollama"):
        return True
    return any(candidate.exists() for candidate in _ollama_install_candidates())


def ollama_running() -> bool:
    from row_bot.models import _ollama_reachable

    return _ollama_reachable(timeout=0.5)


def local_models() -> list[str]:
    from row_bot.models import list_local_models

    return list_local_models()


def detect_local_runtime() -> dict[str, Any]:
    """Loopback-only detection; lists models only when the runtime answers."""
    from row_bot.models import is_tool_compatible

    running = ollama_running()
    names = local_models() if running else []
    models = []
    for name in names[:256]:
        # The family list only knows some models; an unknown one is not "Chat
        # only" (qwen3.8 uses tools), so only a known answer is reported.
        try:
            agent_ready = True if is_tool_compatible(name) else None
        except Exception:
            agent_ready = None
        models.append({"model_ref": f"model:ollama:{name}", "name": name, "agent_ready": agent_ready})
    return {
        "schema_version": 1,
        "state": "running" if running else "installed" if ollama_installed() else "not_installed",
        "platform": platform_name(),
        "download_url": OLLAMA_DOWNLOAD_URL,
        "models": models,
    }


def import_sources() -> list[dict[str, str]]:
    """Another assistant's data folder, by signature files only (no tree walk)."""
    try:
        from row_bot.migration import detection
    except Exception:
        return []
    found = []
    home = Path.home()
    try:
        if detection._looks_like_hermes(home / ".hermes"):
            found.append({"id": "hermes", "label": "Hermes Agent"})
        if any(detection._looks_like_openclaw(home / name) for name in (".openclaw", ".clawdbot", ".moltbot")):
            found.append({"id": "openclaw", "label": "OpenClaw"})
    except OSError:
        return []
    return found


def _model_settings_path() -> Path:
    from row_bot.data_paths import get_row_bot_data_dir

    return get_row_bot_data_dir(create=False) / "model_settings.json"


def saved_default_model() -> str | None:
    """The chat default the person saved, or None (nothing is preset)."""
    from row_bot.providers import saved_model_settings as settings
    from row_bot.providers.selection import model_ref as build_ref, parse_model_ref

    try:
        raw, _revision, _exists = settings.read_saved_model_settings(_model_settings_path())
    except Exception:
        return None
    stored = raw.get("model")
    parsed = parse_model_ref(stored) if isinstance(stored, str) and len(stored) <= 1024 else None
    return build_ref(*parsed) if parsed else None


def _chat_row(model_ref: str):
    from row_bot.providers import client_status

    _snapshot, rows = client_status._read(None, include_readiness=True)
    return next((row for row in rows if row.selection_ref == model_ref and "chat" in row.categories), None)


def choose_model(model_ref: str) -> str:
    """Save the person's pick as the default and pin it for the composer.

    Only a model that is available right now can be chosen: an installed local
    model the runtime serves, or a connected provider's chat model.
    """
    from row_bot.application.client_platform import ClientPlatformError
    from row_bot.providers import saved_model_settings as settings
    from row_bot.providers.selection import add_quick_choice_for_model, model_ref as build_ref, parse_model_ref

    parsed = parse_model_ref(str(model_ref or ""))
    if not parsed:
        raise ClientPlatformError("invalid_model_selection")
    provider_id, model_id = parsed
    reference = build_ref(provider_id, model_id)
    if provider_id == "ollama":
        if model_id not in local_models():
            raise ClientPlatformError("model_configuration_unavailable")
        # The saved catalog learns the local models so pickers list them.
        from row_bot.providers.model_catalog_cache import refresh_model_catalog_cache

        refresh_model_catalog_cache(reason="first_run", force=True, provider_id="ollama")
    else:
        row = _chat_row(reference)
        if row is None or not (row.configured and row.runtime_ready and row.installed):
            raise ClientPlatformError("model_configuration_unavailable")
    settings.update_saved_model_settings(lambda raw: {**raw, "model": reference}, path=_model_settings_path())
    runtime = sys.modules.get("row_bot.models")
    if runtime is not None:
        runtime.adopt_saved_default(reference)
    from row_bot.application.client_diagnosis import recheck_default_model

    recheck_default_model()
    try:
        add_quick_choice_for_model(model_id, provider_id=provider_id, surface="chat")
    except Exception:
        logger.warning("The chosen model could not be pinned to the picker", exc_info=True)
    return reference


def _invoke_test(model_ref: str) -> str:
    from langchain_core.messages import HumanMessage

    from row_bot.models import get_llm_for

    response = get_llm_for(model_ref).invoke([HumanMessage(content=_TEST_PROMPT)])
    content = getattr(response, "content", "")
    if isinstance(content, list):
        content = " ".join(
            str(item.get("text", "")) if isinstance(item, dict) else str(item) for item in content
        )
    return str(content or "")


# Replaced by the browser fixture and tests; never contacts a provider there.
test_invoker: Callable[[str], str] = _invoke_test


def _short_reason(exc: BaseException) -> str:
    text = " ".join(str(exc).split()) or type(exc).__name__
    return text[:240]


def run_model_test() -> dict[str, Any]:
    """Send one short message to the saved default and report in words."""
    from row_bot.models import NO_MODEL_CHOSEN, get_current_model
    from row_bot.providers.selection import provider_display_label, parse_model_ref

    reference = str(get_current_model() or "")
    parsed = parse_model_ref(reference)
    if not parsed:
        return {"schema_version": 1, "ok": False, "detail": NO_MODEL_CHOSEN, "elapsed_ms": 0}
    provider = provider_display_label(parsed[0])
    started = time.monotonic()
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="row-bot-model-test")
    try:
        answer = pool.submit(test_invoker, reference).result(timeout=_TEST_TIMEOUT_SECONDS)
    except concurrent.futures.TimeoutError:
        return {"schema_version": 1, "ok": False, "elapsed_ms": int((time.monotonic() - started) * 1000),
                "detail": f"{parsed[1]} didn't answer within {int(_TEST_TIMEOUT_SECONDS)} seconds."}
    except Exception as exc:
        logger.info("First-run model test failed for %s", reference, exc_info=True)
        return {"schema_version": 1, "ok": False, "elapsed_ms": int((time.monotonic() - started) * 1000),
                "detail": f"{parsed[1]} via {provider} didn't answer: {_short_reason(exc)}"}
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    elapsed = int((time.monotonic() - started) * 1000)
    if not answer.strip():
        return {"schema_version": 1, "ok": False, "elapsed_ms": elapsed,
                "detail": f"{parsed[1]} answered with nothing. Try again or choose another model."}
    return {"schema_version": 1, "ok": True, "elapsed_ms": elapsed, "detail": f"{parsed[1]} answered."}


def _validate_openai_key(api_key: str) -> bool:
    import httpx

    response = httpx.get("https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {api_key}"},
                         timeout=_KEY_CHECK_TIMEOUT_SECONDS)
    if response.status_code in (401, 403):
        return False
    response.raise_for_status()
    return True


def _key_validators() -> dict[str, Callable[[str], bool]]:
    from row_bot import models

    return {
        "openai": _validate_openai_key,
        "anthropic": models.validate_anthropic_key,
        "google": models.validate_google_key,
        "openrouter": models.validate_openrouter_key,
        "requesty": models.validate_requesty_key,
        "xai": models.validate_xai_key,
        "minimax": models.validate_minimax_key,
        "atlascloud": models.validate_atlascloud_key,
    }


# Replaced by the browser fixture and tests; never contacts a provider there.
key_validators: Callable[[], dict[str, Callable[[str], bool]]] = _key_validators


def check_provider_key(provider_id: str, value: str) -> dict[str, Any]:
    """Ask the provider whether the key works; nothing is saved or logged."""
    from row_bot.application.client_platform import ClientPlatformError
    from row_bot.providers.credential_controls import CredentialControlError, normalize_api_key
    from row_bot.providers.selection import provider_display_label

    try:
        key = normalize_api_key(provider_id, value)
    except CredentialControlError as exc:
        raise ClientPlatformError(str(exc)) from None
    label = provider_display_label(provider_id)
    validator = key_validators().get(provider_id)
    if validator is None:
        return {"schema_version": 1, "state": "unchecked",
                "detail": f"{label} is checked when Row-Bot loads its models."}
    try:
        valid = bool(validator(key))
    except Exception as exc:
        logger.info("Could not reach %s to check a key (%s)", provider_id, type(exc).__name__)
        return {"schema_version": 1, "state": "unreachable",
                "detail": f"Row-Bot couldn't reach {label} to check the key. Check your connection and try again."}
    if not valid:
        return {"schema_version": 1, "state": "invalid",
                "detail": f"{label} didn't accept this key. Check that you copied all of it."}
    return {"schema_version": 1, "state": "valid", "detail": f"{label} accepted the key."}
