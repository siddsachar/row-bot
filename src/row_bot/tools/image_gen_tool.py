"""Image generation tool — generate and edit images with any provider in providers/media.py.

The agent calls ``generate_image`` to create images from text prompts, or
``edit_image`` to modify an existing image (pasted, last generated, or from
a file path).  Generated images are rendered inline in the chat via the
``captured_images`` pipeline (same as vision / browser screenshots).

The user picks a provider+model combination in Settings → Models (e.g.
``google/gemini-3.1-flash-image-preview``). The choices are the image models
each connected provider's own model list offers, so new models appear without
a Row-Bot change.

Google providers:
  - **Nano Banana** models use the ``generate_content`` API (same endpoint as
    chat) with ``response_modalities=['IMAGE']``.  They support both
    generation and editing.
  - **Imagen** models (any ``imagen-*``) use the dedicated ``generate_images``
    API.  They support generation only (no editing).
"""

from __future__ import annotations

import base64
import logging
import time
from collections.abc import Mapping, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from row_bot.tools.base import BaseTool
from row_bot.tools import registry

logger = logging.getLogger(__name__)

# No preset: image generation stays off and unset until the person chooses a
# model in Settings › Models (decision 9).
NO_IMAGE_MODEL = (
    "No image model is chosen yet. Choose an image model in Settings › Models, "
    "then ask again."
)

IMAGE_SIZES = ["auto", "1024x1024", "1536x1024", "1024x1536"]
IMAGE_QUALITIES = ["auto", "low", "medium", "high"]

# ── Size / aspect-ratio mapping ──────────────────────────────────────────
_OPENAI_SIZE_TO_ASPECT: dict[str, str] = {
    "1024x1024": "1:1",
    "1536x1024": "3:2",
    "1024x1536": "2:3",
}

_OPENAI_SIZE_TO_GOOGLE = {
    "1024x1024": ("1:1", "1K"),
    "1536x1024": ("3:2", "1K"),
    "1024x1536": ("2:3", "1K"),
}

_QUALITY_TO_RESOLUTION: dict[str, str | None] = {
    "low": "512",
    "medium": "1K",
    "high": "2K",
    "auto": None,   # let model default
}

# A provider with its own image API answers (or, Atlas Cloud, finishes its job) within
# this many seconds, or the request fails.
_DIRECT_TIMEOUT = 180
_JOB_POLL_INTERVAL = 2

# The routes whose image models make new images only here: Together's and Z.AI's APIs
# take no source image, MiniMax's takes a character reference rather than an image to
# edit, and Atlas Cloud's image field differs per model.
_NEW_IMAGES_ONLY = {"together", "minimax", "zai", "atlascloud"}

# The sizes Z.AI's GLM-Image takes that are nearest each size the tool offers.
_ZAI_SIZES = {"1024x1024": "1280x1280", "1536x1024": "1568x1056", "1024x1536": "1056x1568"}

# Imagen aspect ratios (subset supported by the API)
_IMAGEN_ASPECT_RATIOS = {"1:1", "3:4", "4:3", "9:16", "16:9"}


def _map_google_params(size: str, quality: str) -> tuple[str, str | None]:
    """Convert OpenAI-style size/quality to Google (aspect_ratio, resolution).

    Returns ``("1:1", "1K")`` by default.  Quality can override the
    resolution tier (e.g. ``quality="high"`` → ``"2K"``).
    """
    if size != "auto" and size in _OPENAI_SIZE_TO_GOOGLE:
        aspect_ratio, base_res = _OPENAI_SIZE_TO_GOOGLE[size]
    else:
        aspect_ratio, base_res = "1:1", "1K"

    # Quality can override the resolution tier
    if quality != "auto" and quality in _QUALITY_TO_RESOLUTION:
        override = _QUALITY_TO_RESOLUTION[quality]
        if override is not None:
            base_res = override

    return aspect_ratio, base_res

# ── Side-channel for generated images ────────────────────────────────────
# The streaming layer reads and clears this after generate/edit calls.
_last_generated_image: str | None = None  # base64-encoded image data
_strict_output: ContextVar[tuple[str, Callable[[], None], Callable[[bytes], str]] | None] = ContextVar(
    "strict_image_generation_output", default=None)


@contextmanager
def strict_generation_output(*, selection: str, validate: Callable[[], None],
                             sink: Callable[[bytes], str], auth=None) -> Iterator[None]:
    """Bind one explicit generation selection and owned bounded output sink."""
    if not isinstance(selection, str) or not 1 <= len(selection) <= 256 or "/" not in selection:
        raise ValueError("invalid_media_selection")
    token = _strict_output.set((selection, validate, sink))
    try:
        from row_bot.providers.media_auth import media_auth_scope
        with media_auth_scope(auth, selection=selection, validate=validate):
            yield
    finally:
        _strict_output.reset(token)


def _validate_generation() -> None:
    scope = _strict_output.get()
    if scope is not None:
        scope[1]()

# ── Attachment cache for pasted/attached images ──────────────────────────
# Populated by ui/streaming.py before agent invocation.
_image_cache: dict[str, bytes] = {}  # filename → raw bytes
_image_cache_thread_id: str | None = None  # thread that owns __last_generated__


def _execution_image_cache() -> dict[str, bytes]:
    from row_bot.application.attachment_context import tool_cache
    return tool_cache("images", _image_cache)


def _set_pending_image(value: str, name: str = "") -> None:
    from row_bot.application.attachment_context import current_caches
    global _last_generated_image
    if _strict_output.get() is not None:
        _validate_generation()
        if not isinstance(value, str) or len(value) > 4 * ((16 * 1024 * 1024 + 2) // 3):
            raise ValueError("generated_media_too_large")
    caches = current_caches()
    if caches is None:
        _last_generated_image = value
    else:
        caches.pending_image, caches.pending_image_name = value, name


def get_and_clear_last_image() -> str | None:
    """Return and clear the pending generated image, if any."""
    global _last_generated_image
    from row_bot.application.attachment_context import current_caches
    caches = current_caches()
    if caches is not None:
        img = caches.pending_image
        caches.pending_image, caches.pending_image_name = None, ""
        return img
    img = _last_generated_image
    _last_generated_image = None
    return img


def _save_image_to_disk(b64_str: str, prefix: str = "gen") -> str | None:
    """Persist generated/edited image to the per-thread media directory.

    Returns the absolute path as a string, or None if saving fails.
    Uses the media pipeline from threads.py (``save_media_file`` +
    ``_next_media_filename``) so images survive reload and can be
    referenced by tools like ``send_telegram_photo``.
    """
    scope = _strict_output.get()
    if scope is not None:
        _validate_generation()
        if len(b64_str) > 4 * ((16 * 1024 * 1024 + 2) // 3):
            raise ValueError("generated_media_too_large")
        data = base64.b64decode(b64_str, validate=True)
        if not data or len(data) > 16 * 1024 * 1024:
            raise ValueError("generated_media_too_large")
        return scope[2](data)
    try:
        from row_bot.application.attachment_context import current_caches
        caches = current_caches()
        if caches is not None:
            from row_bot.application.generated_media import save_generated_output
            return save_generated_output(caches.conversation_id, base64.b64decode(b64_str),
                                         prefix=prefix, extension="png")
        from row_bot.agent import _current_thread_id_var
        from row_bot.threads import save_media_file, _next_media_filename

        thread_id = _current_thread_id_var.get() or ""
        if not thread_id:
            logger.debug("_save_image_to_disk: no thread_id in context")
            return None

        filename = _next_media_filename(thread_id, prefix, "png")
        img_bytes = base64.b64decode(b64_str)
        saved_path = save_media_file(thread_id, filename, img_bytes)
        logger.info("Saved generated image to %s", saved_path)
        return str(saved_path)
    except Exception:
        logger.warning("Failed to save generated image to disk", exc_info=True)
        return None


# ── Provider resolution ──────────────────────────────────────────────────

def _parse_model_config(value: str) -> tuple[str, str]:
    """Parse a 'provider/model' config string → (provider, model_id).

    Falls back gracefully for bare model names (legacy configs).
    """
    if "/" in value:
        provider, model_id = value.split("/", 1)
        return provider, model_id
    # Legacy bare model name — default to openai
    return "openai", value


def get_available_image_models() -> dict[str, str]:
    """Return {config_value: display_label} for models whose provider key is set.

    Used by the Settings UI to populate the model dropdown.
    """
    from row_bot.models import _cloud_model_cache, _sync_custom_model_cache
    from row_bot.providers.media import media_model_options
    _sync_custom_model_cache()
    return media_model_options("image", _cloud_model_cache)


def _get_client() -> tuple:
    """Return (client, provider_label, provider_id) based on the user's model selection.

    Google gets a ``google.genai.Client``; a provider on the OpenAI images route
    gets an ``openai.OpenAI`` client at its base URL. xAI and the providers with
    their own image APIs are called directly, so their client is ``None``.
    """
    from row_bot.providers.media import IMAGE_PROVIDER_META, media_api_key

    provider, _ = _parse_model_config(_get_configured_selection())
    from row_bot.providers.media_auth import current_media_auth, google_client, openai_client
    captured = current_media_auth(provider)
    if captured is not None and provider in {"openai", "google"}:
        return (google_client() if provider == "google" else openai_client()), IMAGE_PROVIDER_META[provider]["label"], provider
    if provider in {"xai", "xai_oauth"}:
        from row_bot.providers.xai_media import xai_media_auth_context

        ctx = xai_media_auth_context(provider)
        return None, ctx.label, provider
    if provider == "codex":
        # The ChatGPT sign-in; the request itself says if it needs reconnecting.
        return None, IMAGE_PROVIDER_META["codex"]["label"], provider

    meta = IMAGE_PROVIDER_META.get(provider)
    if not meta:
        raise RuntimeError(
            f"Unknown image generation provider '{provider}'. "
            "Please select a valid model in Settings → Models."
        )

    api_key = media_api_key(provider)
    if not api_key:
        raise RuntimeError(
            f"No API key for {meta['label']}. "
            f"Please add your {meta['label']} API key in Settings → Models."
        )

    if provider == "google":
        from google import genai
        return genai.Client(api_key=api_key), meta["label"], provider
    if meta["route"] != "openai":
        return None, meta["label"], provider

    import openai
    if provider == "openai":
        return openai.OpenAI(api_key=api_key), meta["label"], provider
    return openai.OpenAI(api_key=api_key, base_url=meta["base_url"]), meta["label"], provider


def _route(provider_id: str) -> str:
    from row_bot.providers.media import IMAGE_PROVIDER_META

    return IMAGE_PROVIDER_META[provider_id]["route"]


def _request_json(provider_id: str, method: str, path: str, body: dict | None = None) -> dict:
    """Call an image provider's own API with its key, raising its error message."""
    import httpx

    from row_bot.providers.media import IMAGE_PROVIDER_META, media_api_key, media_error_text

    meta = IMAGE_PROVIDER_META[provider_id]
    _validate_generation()
    response = httpx.request(
        method, f"{meta['base_url']}{path}", json=body, timeout=_DIRECT_TIMEOUT,
        headers={"Authorization": f"Bearer {media_api_key(provider_id)}"},
    )
    if response.status_code >= 400:
        raise RuntimeError(f"HTTP {response.status_code}: {media_error_text(response)}")
    return response.json()


def _image_b64(item: dict) -> str:
    """The image in one response item: its base64, or the bytes behind its URL."""
    if item.get("b64_json"):
        return str(item["b64_json"])
    if item.get("url"):
        import httpx

        _validate_generation()
        response = httpx.get(str(item["url"]), timeout=_DIRECT_TIMEOUT, follow_redirects=True)
        response.raise_for_status()
        return base64.b64encode(response.content).decode("ascii")
    return ""


def _finish_image(b64_str: str, *, prefix: str, done: str) -> str:
    """Show and keep a generated or edited image, and say where it was saved."""
    _set_pending_image(b64_str)
    _execution_image_cache()["__last_generated__"] = base64.b64decode(b64_str)
    saved = _save_image_to_disk(b64_str, prefix)
    return done + (f"\nSaved to: {saved}" if saved else "")


def _first_item_b64(data: dict) -> str:
    items = data.get("data") if isinstance(data.get("data"), list) else []
    return _image_b64(items[0]) if items and isinstance(items[0], dict) else ""


def _openrouter_image(provider_id: str, model: str, prompt: str, size: str, quality: str, source: str) -> str:
    from row_bot.providers.media import generation_options, nearest_aspect_ratio, nearest_resolution

    aspect = _OPENAI_SIZE_TO_ASPECT.get(size, "1:1") if size != "auto" else None
    wanted = quality if quality != "auto" else None
    settings: dict = {"aspect_ratio": aspect, "quality": wanted}
    options = generation_options(provider_id, model)
    if options is not None:
        # Each setting is the nearest the model lists; one it lists nothing for is left to the model.
        settings = {"aspect_ratio": nearest_aspect_ratio(aspect, options.get("aspect_ratio", [])) if aspect else None,
                    "quality": wanted if wanted in options.get("quality", []) else None}
        if wanted and not settings["quality"] and _QUALITY_TO_RESOLUTION.get(wanted):
            # Models listed by resolution tier take quality as their resolution.
            settings["resolution"] = nearest_resolution(_QUALITY_TO_RESOLUTION[wanted], options.get("resolution", []))
    body: dict = {"model": model, "prompt": prompt, "n": 1,
                  **{key: value for key, value in settings.items() if value is not None}}
    if source:
        body["input_references"] = [{"type": "image_url", "image_url": {"url": source}}]
    return _first_item_b64(_request_json(provider_id, "POST", "/images", body))


def _together_image(provider_id: str, model: str, prompt: str, size: str, quality: str, source: str) -> str:
    body: dict = {"model": model, "prompt": prompt, "n": 1, "response_format": "base64"}
    if size != "auto":
        width, _, height = size.partition("x")
        body["width"], body["height"] = int(width), int(height)
    return _first_item_b64(_request_json(provider_id, "POST", "/images/generations", body))


def _minimax_image(provider_id: str, model: str, prompt: str, size: str, quality: str, source: str) -> str:
    body: dict = {"model": model, "prompt": prompt, "n": 1, "response_format": "base64"}
    if size != "auto":
        body["aspect_ratio"] = _OPENAI_SIZE_TO_ASPECT.get(size, "1:1")
    data = _request_json(provider_id, "POST", "/image_generation", body)
    # MiniMax answers 200 with its error in base_resp.
    status = data.get("base_resp") if isinstance(data.get("base_resp"), dict) else {}
    if status.get("status_code"):
        raise RuntimeError(f"MiniMax error {status['status_code']}: {status.get('status_msg') or 'no details'}")
    images = (data.get("data") or {}).get("image_base64") if isinstance(data.get("data"), dict) else None
    return str(images[0]) if isinstance(images, list) and images else ""


def _zai_image(provider_id: str, model: str, prompt: str, size: str, quality: str, source: str) -> str:
    body: dict = {"model": model, "prompt": prompt}
    if size != "auto":
        body["size"] = _ZAI_SIZES.get(size, "1280x1280")
    if quality == "high":
        body["quality"] = "hd"
    return _first_item_b64(_request_json(provider_id, "POST", "/images/generations", body))


def _dashscope_image(provider_id: str, model: str, prompt: str, size: str, quality: str, source: str) -> str:
    content: list[dict] = [{"image": source}] if source else []
    content.append({"text": prompt})
    body: dict = {"model": model, "input": {"messages": [{"role": "user", "content": content}]},
                  "parameters": {"n": 1}}
    if size != "auto":
        body["parameters"]["size"] = size.replace("x", "*")
    data = _request_json(provider_id, "POST", "/services/aigc/multimodal-generation/generation", body)
    choices = (data.get("output") or {}).get("choices") or []
    parts = ((choices[0].get("message") or {}).get("content") or []) if choices else []
    url = next((str(part["image"]) for part in parts if isinstance(part, dict) and part.get("image")), "")
    return _image_b64({"url": url}) if url else ""


def _atlascloud_image(provider_id: str, model: str, prompt: str, size: str, quality: str, source: str) -> str:
    # Each Atlas Cloud model names its own size fields, so the model's defaults are used.
    job = _request_json(provider_id, "POST", "/model/generateImage", {"model": model, "prompt": prompt})
    job_id = str((job.get("data") or {}).get("id") or "")
    if not job_id:
        raise RuntimeError("Atlas Cloud started no image job")
    elapsed = 0
    while elapsed < _DIRECT_TIMEOUT:
        time.sleep(_JOB_POLL_INTERVAL)
        elapsed += _JOB_POLL_INTERVAL
        result = _request_json(provider_id, "GET", f"/model/prediction/{job_id}").get("data") or {}
        status = str(result.get("status") or "")
        if status == "completed":
            outputs = result.get("outputs") or []
            return _image_b64({"url": outputs[0]}) if outputs and isinstance(outputs[0], str) else ""
        if status in {"failed", "canceled", "cancelled", "error", "timeout"}:
            raise RuntimeError(f"Atlas Cloud's job ended ({status}): {result.get('error') or 'no details'}")
    raise RuntimeError(f"Atlas Cloud's image job {job_id} did not finish within {_DIRECT_TIMEOUT}s")


def _codex_image(provider_id: str, model: str, prompt: str, size: str, quality: str, source: str) -> str:
    from row_bot.providers.codex_media import codex_image

    _validate_generation()
    return codex_image(model, prompt, size=size, quality=quality, source=source)


# The image routes with their own APIs (OpenAI, Google and xAI have theirs above).
_DIRECT_IMAGE: dict[str, Callable[[str, str, str, str, str, str], str]] = {
    "codex": _codex_image,
    "openrouter": _openrouter_image,
    "together": _together_image,
    "minimax": _minimax_image,
    "zai": _zai_image,
    "dashscope": _dashscope_image,
    "atlascloud": _atlascloud_image,
}


def _direct_image(provider_id: str, label: str, model: str, prompt: str, size: str,
                  quality: str, image_bytes: bytes | None = None) -> str:
    """Generate (or, with image_bytes, edit) on a provider with its own image API."""
    verb, prefix = ("edit", "edit") if image_bytes is not None else ("generation", "gen")
    route = _route(provider_id)
    if image_bytes is not None and route in _NEW_IMAGES_ONLY:
        return (f"{label} image models can only make new images here. "
                "Choose another image model in Settings › Models to edit one.")
    source = ""
    if image_bytes is not None:
        source = f"data:{_detect_mime(image_bytes)};base64,{base64.b64encode(image_bytes).decode('ascii')}"
    try:
        b64_str = _DIRECT_IMAGE[route](provider_id, model, prompt, size, quality, source)
    except Exception as e:
        logger.error("Image %s failed: %s", verb, e, exc_info=True)
        return f"Image {verb} failed: {e}{_unlisted_note()}"
    if not b64_str:
        return f"Image {verb} returned no image data."
    past = "edited" if image_bytes is not None else "generated"
    return _finish_image(b64_str, prefix=prefix,
                         done=f"Image {past} successfully. Model: {model} | Provider: {label}")


def _get_configured_selection() -> str:
    """Return the raw 'provider/model' string from tool config."""
    scope = _strict_output.get()
    if scope is not None:
        return scope[0]
    val = registry.get_tool_config("image_gen", "model", None)
    if val:
        return val
    tool = registry.get_tool("image_gen")
    if tool:
        val = tool.get_config("model", "")
        if val:
            return val
    return ""


def _is_imagen(model: str) -> bool:
    """Imagen models take the generate_images API; Gemini image models take generate_content."""
    return model.startswith("imagen-")


def _unlisted_note() -> str:
    """Why a request may have failed: the chosen model's provider no longer lists it."""
    from row_bot.models import _cloud_model_cache
    from row_bot.providers.media import unlisted_media_note

    note = unlisted_media_note("image", _get_configured_selection(), _cloud_model_cache)
    return f" {note}" if note else ""


def _get_configured_model() -> str:
    """Return just the model ID (e.g. 'gpt-image-2') from the stored config."""
    _, model_id = _parse_model_config(_get_configured_selection())
    return model_id


# ── Image resolution helpers ─────────────────────────────────────────────

def _detect_mime(data: bytes) -> str:
    """Detect image MIME type from magic bytes.  Defaults to image/png."""
    if data[:4] == b"\xff\xd8\xff\xe0" or data[:4] == b"\xff\xd8\xff\xe1":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


@dataclass(frozen=True)
class _XAIImageRequestPlan:
    quality: str | None = None
    resolution: str | None = None
    note: str = ""


_XAI_QUALITY_RANK = {"low": 0, "medium": 1, "high": 2}
_XAI_RESOLUTION_RANK = {"1k": 0, "2k": 1}


def _plan_xai_image_request(
    provider_id: str,
    model: str,
    requested_quality: str,
) -> _XAIImageRequestPlan:
    from row_bot.providers.capability_resolution import resolve_capability_snapshot

    requested = str(requested_quality or "auto").strip().lower()
    snapshot = resolve_capability_snapshot(
        provider_id,
        model,
        include_static_fallback=False,
    )
    generation_parameters = snapshot.get("generation_parameters")
    selected: tuple[str, str] | None = None
    if requested != "auto" and isinstance(generation_parameters, Mapping):
        options = generation_parameters.get("options")
        defaults = generation_parameters.get("defaults")
        raw_combinations = generation_parameters.get("valid_combinations")
        if isinstance(options, Mapping) and isinstance(defaults, Mapping) and isinstance(raw_combinations, list):
            qualities = _xai_parameter_options(options.get("quality"))
            resolutions = _xai_parameter_options(options.get("resolution"))
            combinations: list[tuple[str, str]] = []
            for combination in raw_combinations:
                if not isinstance(combination, Mapping):
                    continue
                quality = str(combination.get("quality") or "").strip().lower()
                resolution = str(combination.get("resolution") or "").strip().lower()
                pair = (quality, resolution)
                if quality in qualities and resolution in resolutions and pair not in combinations:
                    combinations.append(pair)
            default_pair = (
                str(defaults.get("quality") or "").strip().lower(),
                str(defaults.get("resolution") or "").strip().lower(),
            )
            if default_pair in combinations:
                if requested == "high":
                    ranked = [
                        pair for pair in combinations
                        if pair[0] in _XAI_QUALITY_RANK and pair[1] in _XAI_RESOLUTION_RANK
                    ]
                    if ranked:
                        selected = max(
                            ranked,
                            key=lambda pair: (_XAI_QUALITY_RANK[pair[0]], _XAI_RESOLUTION_RANK[pair[1]]),
                        )
                else:
                    matching = [
                        pair for pair in combinations
                        if pair[0] == requested and pair[1] in _XAI_RESOLUTION_RANK
                    ]
                    if matching:
                        selected = next(
                            (pair for pair in matching if pair[1] == default_pair[1]),
                            min(matching, key=lambda pair: _XAI_RESOLUTION_RANK[pair[1]]),
                        )

    if selected:
        note = ""
        if selected[0] != requested:
            note = (
                f"Requested quality {requested}; used xAI's highest supported tier "
                f"{selected[0]}/{selected[1]}."
            )
        plan = _XAIImageRequestPlan(quality=selected[0], resolution=selected[1], note=note)
        effective = f"{selected[0]}/{selected[1]}"
    else:
        note = ""
        if requested != "auto":
            note = (
                f"Requested quality {requested}; used xAI provider defaults because the selected model "
                "did not publish a complete matching tier."
            )
        plan = _XAIImageRequestPlan(note=note)
        effective = "provider defaults"
    logger.info(
        "xAI image parameters: provider=%s model=%s requested_quality=%s effective=%s",
        provider_id,
        model,
        requested,
        effective,
    )
    return plan


def _xai_parameter_options(value: Any) -> set[str]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return set()
    return {str(item).strip().lower() for item in value if str(item).strip()}


def _newest_chat_picture() -> bytes | None:
    """The newest picture in this chat's files (generated, edited or attached)."""
    from row_bot.application.attachments import AttachmentError, list_conversation_files
    from row_bot.tool_outputs import current_thread_id

    conversation = current_thread_id()
    if not conversation:
        return None
    try:
        files = list_conversation_files(conversation)
    except AttachmentError:
        return None
    for item in files:  # Newest first.
        if str(item.get("mime_type") or "").startswith("image/"):
            return _chat_picture(str(item["name"]))
    return None


def _chat_picture(name: str) -> bytes | None:
    from row_bot import media_sources
    from row_bot.tool_outputs import current_thread_id

    conversation = current_thread_id()
    if not conversation:
        return None
    try:
        found = media_sources.chat_file(conversation, name)
        return found.read_bytes() if found is not None and found.kind == "image" else None
    except media_sources.SourceError:
        return None


def _resolve_image_source(image_source: str) -> bytes:
    """Resolve an image_source string to raw bytes.

    Priority:
      1. "last" → last generated image (from _last_generated_image backup)
      2. Key match in _image_cache → pasted/attached image
      3. File path on disk
    """
    # "last" — the image this turn made, else the chat's newest picture (a later turn's "make it night").
    if image_source.strip().lower() == "last":
        last_b64 = _execution_image_cache().get("__last_generated__")
        if last_b64:
            return last_b64
        newest = _newest_chat_picture()
        if newest is not None:
            return newest
        raise ValueError(
            "No previously generated image available. "
            "Generate an image first, then use edit_image with image_source='last'."
        )

    # Check attachment cache (pasted images)
    if image_source in _execution_image_cache():
        return _execution_image_cache()[image_source]

    # Partial filename match in cache
    for cached_name, cached_data in _execution_image_cache().items():
        if cached_name != "__last_generated__" and image_source.lower() in cached_name.lower():
            return cached_data

    # File path on disk
    path = Path(image_source).expanduser()
    if path.exists() and path.is_file():
        return path.read_bytes()

    # Try workspace-relative
    tool = registry.get_tool("filesystem")
    if tool:
        ws_root = tool.get_config("workspace_root", "")
        if ws_root:
            ws_path = Path(ws_root) / image_source
            if ws_path.exists() and ws_path.is_file():
                return ws_path.read_bytes()

    # A picture saved in this chat's files, by the name its result gave ("Saved in this chat's files as …").
    named = _chat_picture(image_source)
    if named is not None:
        return named

    raise ValueError(
        f"Could not find image '{image_source}'. "
        "Use 'last' for the last generated image, paste/attach an image, "
        "or provide a valid file path."
    )


# ── Core generation functions ────────────────────────────────────────────

def _generate_image(
    prompt: str,
    size: str = "auto",
    quality: str = "auto",
) -> str:
    """Generate an image from a text prompt."""
    global _last_generated_image
    _validate_generation()
    if not _get_configured_selection():
        return NO_IMAGE_MODEL

    try:
        client, provider_label, provider_id = _get_client()
    except Exception as e:
        if _strict_output.get() is not None:
            raise
        logger.error("Image generation failed: %s", e, exc_info=True)
        return f"Image generation failed: {e}{_unlisted_note()}"
    model = _get_configured_model()
    _validate_generation()

    logger.info("generate_image: model=%s, size=%s, quality=%s, provider=%s",
                model, size, quality, provider_label)

    # ── Google provider ──────────────────────────────────────────────────
    if provider_id == "google":
        from google.genai import types

        aspect_ratio, resolution = _map_google_params(size, quality)

        try:
            if _is_imagen(model):
                # Imagen — dedicated generate_images endpoint
                cfg = types.GenerateImagesConfig(
                    number_of_images=1,
                    aspect_ratio=aspect_ratio if aspect_ratio in _IMAGEN_ASPECT_RATIOS else "1:1",
                )
                _validate_generation()
                response = client.models.generate_images(
                    model=model, prompt=prompt, config=cfg,
                )
                if not response.generated_images:
                    return "Image generation returned no images."
                img_bytes = response.generated_images[0].image.image_bytes
            else:
                # Nano Banana — generate_content with IMAGE modality
                img_cfg_kwargs: dict = {"aspect_ratio": aspect_ratio}
                if resolution:
                    img_cfg_kwargs["image_size"] = resolution
                cfg = types.GenerateContentConfig(
                    response_modalities=["IMAGE"],
                    image_config=types.ImageConfig(**img_cfg_kwargs),
                )
                _validate_generation()
                response = client.models.generate_content(
                    model=model, contents=[prompt], config=cfg,
                )
                # Extract image bytes from response parts
                img_bytes = None
                for part in (response.parts or []):
                    if part.inline_data and part.inline_data.data:
                        img_bytes = part.inline_data.data
                        break
                if not img_bytes:
                    return "Image generation returned no image data."
        except Exception as e:
            if _strict_output.get() is not None:
                raise
            logger.error("Image generation failed: %s", e, exc_info=True)
            return f"Image generation failed: {e}{_unlisted_note()}"

        b64_str = base64.b64encode(img_bytes).decode("ascii")
        _set_pending_image(b64_str)
        _execution_image_cache()["__last_generated__"] = img_bytes
        saved = _save_image_to_disk(b64_str, "gen")
        result = (
            f"Image generated successfully. Model: {model} | "
            f"Aspect ratio: {aspect_ratio} | Provider: {provider_label}"
        )
        if saved:
            result += f"\nSaved to: {saved}"
        return result

    # ── xAI provider ──────────────────────────────────────────────────────
    # xAI does NOT support 'size' or 'style'. Uses 'aspect_ratio' & 'resolution'.
    # These are xAI-specific params so must go via extra_body.
    if provider_id in {"xai", "xai_oauth"}:
        from row_bot.providers.xai_media import (
            XAI_IMAGE_GENERATION_READ_TIMEOUT,
            XAI_MEDIA_DOWNLOAD_READ_TIMEOUT,
            xai_media_get,
            xai_media_json_request,
        )

        aspect = _OPENAI_SIZE_TO_ASPECT.get(size, "1:1") if size != "auto" else "auto"
        body: dict = {
            "model": model,
            "prompt": prompt,
            "n": 1,
            "response_format": "b64_json",
        }
        if aspect != "auto":
            body["aspect_ratio"] = aspect
        plan = _plan_xai_image_request(provider_id, model, quality)
        if plan.quality:
            body["quality"] = plan.quality
        if plan.resolution:
            body["resolution"] = plan.resolution

        try:
            _validate_generation()
            data = xai_media_json_request(
                provider_id,
                "POST",
                "/images/generations",
                json=body,
                timeout=XAI_IMAGE_GENERATION_READ_TIMEOUT,
            )
        except Exception as e:
            if _strict_output.get() is not None:
                raise
            logger.error("Image generation failed: %s", e, exc_info=True)
            return f"Image generation failed: {e}{_unlisted_note()}"

        items = data.get("data") if isinstance(data.get("data"), list) else []
        if not items:
            return "Image generation returned no image data."
        image_data = items[0] if isinstance(items[0], dict) else {}
        if image_data.get("b64_json"):
            b64_str = str(image_data["b64_json"])
        elif image_data.get("url"):
            try:
                _validate_generation()
                resp = xai_media_get(
                    provider_id,
                    str(image_data["url"]),
                    timeout=XAI_MEDIA_DOWNLOAD_READ_TIMEOUT,
                    follow_redirects=True,
                )
                b64_str = base64.b64encode(resp.content).decode("ascii")
            except Exception as e:
                if _strict_output.get() is not None:
                    raise
                logger.error("Image download failed: %s", e, exc_info=True)
                return f"Image generated but download failed: {e}"
        else:
            return "Image generation returned no image data."

        _set_pending_image(b64_str)
        _execution_image_cache()["__last_generated__"] = base64.b64decode(b64_str)
        saved = _save_image_to_disk(b64_str, "gen")
        disp_aspect = aspect if aspect != "auto" else "auto"
        result = (
            f"Image generated successfully. Model: {model} | "
            f"Aspect ratio: {disp_aspect} | Provider: {provider_label}"
        )
        if saved:
            result += f"\nSaved to: {saved}"
        if plan.note:
            result += f"\n{plan.note}"
        return result

    if _route(provider_id) in _DIRECT_IMAGE:
        return _direct_image(provider_id, provider_label, model, prompt, size, quality)

    # ── OpenAI images route (OpenAI, Vercel AI Gateway, Requesty) ──────────
    kwargs: dict = {"model": model, "prompt": prompt, "n": 1}
    if size != "auto" or provider_id == "openai":
        kwargs["size"] = size if size != "auto" else "1024x1024"
    if quality != "auto":
        kwargs["quality"] = quality

    try:
        _validate_generation()
        response = client.images.generate(**kwargs)
    except Exception as e:
        if _strict_output.get() is not None:
            raise
        logger.error("Image generation failed: %s", e, exc_info=True)
        return f"Image generation failed: {e}{_unlisted_note()}"

    # Extract image data
    image_data = response.data[0]
    if hasattr(image_data, "b64_json") and image_data.b64_json:
        b64_str = image_data.b64_json
    elif hasattr(image_data, "url") and image_data.url:
        # Download the URL and convert to base64
        import urllib.request
        _validate_generation()
        with urllib.request.urlopen(image_data.url) as resp:
            data = resp.read(16 * 1024 * 1024 + 1) if _strict_output.get() is not None else resp.read()
            if _strict_output.get() is not None and len(data) > 16 * 1024 * 1024:
                raise ValueError("generated_media_too_large")
            b64_str = base64.b64encode(data).decode("ascii")
    else:
        return "Image generation returned no image data."

    # Store in side-channel for UI rendering
    _set_pending_image(b64_str)
    # Also store in cache for edit_image "last" reference
    _execution_image_cache()["__last_generated__"] = base64.b64decode(b64_str)

    saved = _save_image_to_disk(b64_str, "gen")
    revised_prompt = getattr(image_data, "revised_prompt", None)
    result = f"Image generated successfully. Model: {model} | Size: {kwargs.get('size', 'auto')} | Provider: {provider_label}"
    if saved:
        result += f"\nSaved to: {saved}"
    if revised_prompt:
        result += f"\nRevised prompt: {revised_prompt}"
    return result


def _edit_image(
    prompt: str,
    image_source: str = "last",
    size: str = "auto",
    quality: str = "auto",
) -> str:
    """Edit an existing image using a text prompt."""
    global _last_generated_image
    if not _get_configured_selection():
        return NO_IMAGE_MODEL

    try:
        client, provider_label, provider_id = _get_client()
    except Exception as e:
        logger.error("Image edit failed: %s", e, exc_info=True)
        return f"Image edit failed: {e}{_unlisted_note()}"
    model = _get_configured_model()

    # Imagen 4 does not support editing
    if _is_imagen(model):
        return (
            f"Image editing is not supported by {model}. "
            "Imagen 4 models can only generate new images. "
            "Please switch to a Nano Banana or OpenAI model for editing."
        )

    # Resolve the source image to raw bytes
    try:
        image_bytes = _resolve_image_source(image_source)
    except ValueError as e:
        return str(e)

    logger.info("edit_image: model=%s, size=%s, source=%s, provider=%s",
                model, size, image_source, provider_label)

    # ── Google Nano Banana — send image+text via generate_content ─────
    if provider_id == "google":
        from google.genai import types

        mime = _detect_mime(image_bytes)
        img_part = types.Part.from_bytes(data=image_bytes, mime_type=mime)

        try:
            cfg = types.GenerateContentConfig(
                response_modalities=["IMAGE"],
            )
            response = client.models.generate_content(
                model=model, contents=[prompt, img_part], config=cfg,
            )
            img_out = None
            for part in (response.parts or []):
                if part.inline_data and part.inline_data.data:
                    img_out = part.inline_data.data
                    break
            if not img_out:
                return "Image edit returned no image data."
        except Exception as e:
            logger.error("Image edit failed: %s", e, exc_info=True)
            return f"Image edit failed: {e}{_unlisted_note()}"

        b64_str = base64.b64encode(img_out).decode("ascii")
        _set_pending_image(b64_str)
        _execution_image_cache()["__last_generated__"] = img_out
        saved = _save_image_to_disk(b64_str, "edit")
        result = (
            f"Image edited successfully. Model: {model} | "
            f"Provider: {provider_label}"
        )
        if saved:
            result += f"\nSaved to: {saved}"
        return result

    # ── xAI provider — uses JSON body with image URL, not multipart ─────
    if provider_id in {"xai", "xai_oauth"}:
        from row_bot.providers.xai_media import (
            XAI_IMAGE_GENERATION_READ_TIMEOUT,
            XAI_MEDIA_DOWNLOAD_READ_TIMEOUT,
            xai_media_get,
            xai_media_json_request,
        )

        b64_src = base64.b64encode(image_bytes).decode("ascii")
        mime = _detect_mime(image_bytes)
        data_uri = f"data:{mime};base64,{b64_src}"

        body: dict = {
            "model": model,
            "prompt": prompt,
            "n": 1,
            "response_format": "b64_json",
            "image": {"url": data_uri, "type": "image_url"},
        }
        aspect = _OPENAI_SIZE_TO_ASPECT.get(size, None) if size != "auto" else None
        if aspect:
            body["aspect_ratio"] = aspect
        plan = _plan_xai_image_request(provider_id, model, quality)
        if plan.quality:
            body["quality"] = plan.quality
        if plan.resolution:
            body["resolution"] = plan.resolution

        try:
            data = xai_media_json_request(
                provider_id,
                "POST",
                "/images/edits",
                json=body,
                timeout=XAI_IMAGE_GENERATION_READ_TIMEOUT,
            )
        except Exception as e:
            logger.error("Image edit failed: %s", e, exc_info=True)
            return f"Image edit failed: {e}{_unlisted_note()}"

        items = data.get("data", [])
        if not items:
            return "Image edit returned no image data."
        item = items[0]
        b64_str = item.get("b64_json") or ""
        if not b64_str and item.get("url"):
            try:
                resp = xai_media_get(
                    provider_id,
                    str(item["url"]),
                    timeout=XAI_MEDIA_DOWNLOAD_READ_TIMEOUT,
                    follow_redirects=True,
                )
                b64_str = base64.b64encode(resp.content).decode("ascii")
            except Exception as e:
                logger.error("Image edit download failed: %s", e, exc_info=True)
                return f"Image edited but download failed: {e}"
        if not b64_str:
            return "Image edit returned no image data."

        _set_pending_image(b64_str)
        _execution_image_cache()["__last_generated__"] = base64.b64decode(b64_str)
        saved = _save_image_to_disk(b64_str, "edit")
        result = (
            f"Image edited successfully. Model: {model} | "
            f"Provider: {provider_label}"
        )
        if saved:
            result += f"\nSaved to: {saved}"
        if plan.note:
            result += f"\n{plan.note}"
        return result

    if _route(provider_id) in _DIRECT_IMAGE:
        return _direct_image(provider_id, provider_label, model, prompt, size, quality, image_bytes)

    # ── OpenAI images route (OpenAI, Vercel AI Gateway, Requesty) ──────────
    mime = _detect_mime(image_bytes)
    image_file = ("image.png", image_bytes, mime)

    kwargs: dict = {"model": model, "prompt": prompt, "image": [image_file], "n": 1}
    if size != "auto" or provider_id == "openai":
        kwargs["size"] = size if size != "auto" else "1024x1024"

    try:
        response = client.images.edit(**kwargs)
    except Exception as e:
        logger.error("Image edit failed: %s", e, exc_info=True)
        return f"Image edit failed: {e}{_unlisted_note()}"

    # Extract image data
    image_data = response.data[0]
    if hasattr(image_data, "b64_json") and image_data.b64_json:
        b64_str = image_data.b64_json
    elif hasattr(image_data, "url") and image_data.url:
        import urllib.request
        with urllib.request.urlopen(image_data.url) as resp:
            b64_str = base64.b64encode(resp.read()).decode("ascii")
    else:
        return "Image edit returned no image data."

    # Store in side-channel for UI rendering
    _set_pending_image(b64_str)
    _execution_image_cache()["__last_generated__"] = base64.b64decode(b64_str)

    saved = _save_image_to_disk(b64_str, "edit")
    revised_prompt = getattr(image_data, "revised_prompt", None)
    result = f"Image edited successfully. Model: {model} | Size: {kwargs.get('size', 'auto')} | Provider: {provider_label}"
    if saved:
        result += f"\nSaved to: {saved}"
    if revised_prompt:
        result += f"\nRevised prompt: {revised_prompt}"
    return result


# ── Pydantic input schemas ───────────────────────────────────────────────

class _GenerateImageInput(BaseModel):
    prompt: str = Field(
        description=(
            "A detailed description of the image to generate. Be specific "
            "about style, composition, colors, and subject matter. "
            "Example: 'A watercolor painting of a sunset over mountains "
            "with warm orange and purple tones'"
        )
    )
    size: str = Field(
        default="auto",
        description=(
            "Image dimensions. Options: 'auto' (default 1024x1024), "
            "'1024x1024' (square), '1536x1024' (landscape), "
            "'1024x1536' (portrait)."
        ),
    )
    quality: str = Field(
        default="auto",
        description=(
            "Image quality. Options: 'auto' (default), 'low' (fastest), "
            "'medium', 'high' (best quality, slower)."
        ),
    )


class _EditImageInput(BaseModel):
    prompt: str = Field(
        description=(
            "What to change in the image. Be specific about the edit. "
            "Example: 'Add a red hat to the cat', 'Remove the background', "
            "'Make it look more realistic'"
        )
    )
    image_source: str = Field(
        default="last",
        description=(
            "Where to get the image to edit. Use 'last' for the last "
            "generated image (default). Use the filename for an attached/"
            "pasted image (e.g. 'photo.jpg'). Or use a file path on disk."
        ),
    )
    size: str = Field(
        default="auto",
        description=(
            "Output image dimensions. Options: 'auto' (same as original), "
            "'1024x1024' (square), '1536x1024' (landscape), "
            "'1024x1536' (portrait)."
        ),
    )
    quality: str = Field(
        default="auto",
        description=(
            "Image quality. Options: 'auto' (default), 'low' (fastest), "
            "'medium', 'high' (best quality, slower)."
        ),
    )


# ── Tool registration ───────────────────────────────────────────────────

class ImageGenTool(BaseTool):

    @property
    def name(self) -> str:
        return "image_gen"

    @property
    def display_name(self) -> str:
        return "🎨 Image Generation"

    @property
    def description(self) -> str:
        return (
            "Generate images from text descriptions and edit existing images. "
            "Creates images using AI models (OpenAI GPT Image or Google "
            "Nano Banana / Imagen 4). Requires an OpenAI or Google API key."
        )

    @property
    def enabled_by_default(self) -> bool:
        # Off until the person turns it on and chooses a model (decision 11).
        return False

    @property
    def config_schema(self) -> dict[str, dict]:
        # The model selector is rendered directly in the Models tab
        # (settings.py) using get_available_image_models(), not via
        # the generic config_schema renderer.
        return {}

    def as_langchain_tools(self) -> list:
        return [
            StructuredTool.from_function(
                func=_generate_image,
                name="generate_image",
                description=(
                    "Generate an image from a text description. Use this "
                    "when the user asks you to create, draw, design, or "
                    "generate any kind of image, illustration, artwork, "
                    "diagram, logo, or visual content. Provide a detailed "
                    "prompt describing the desired image."
                ),
                args_schema=_GenerateImageInput,
            ),
            StructuredTool.from_function(
                func=_edit_image,
                name="edit_image",
                description=(
                    "Edit or modify an existing image using a text prompt. "
                    "Use 'last' to edit the most recently generated image, "
                    "or specify a filename for a pasted/attached image, or "
                    "a file path. Use this when the user wants to change, "
                    "modify, adjust, or transform an existing image."
                ),
                args_schema=_EditImageInput,
            ),
        ]

    def execute(self, query: str) -> str:
        return _generate_image(prompt=query)


registry.register(ImageGenTool())
