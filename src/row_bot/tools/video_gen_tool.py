"""Video generation tool: clips from every video provider in ``providers/media.py``.

The agent calls ``generate_video`` to make a clip from a prompt (or to continue an earlier clip), or
``animate_image`` to turn a still into a clip. Either can hold a shot to the shots around it (C3):

- ``references``: up to three pictures of a character, a product or a place, plus a saved ``character``
  or ``style`` from Library › Media (``media_library.look_pictures``);
- ``last_frame``: a picture the shot ends on, so the next shot can start there;
- ``extend`` (``generate_video``): an earlier clip to continue.

Each provider takes what its own API documents, mapped below. What a model doesn't offer is said in the
result in plain words, never dropped silently; a clip that can't be extended is refused before anything is
made. Plain shot words in the prompt ("close-up", "wide", "slow push-in", "handheld") reach the model unchanged:
none of these APIs has a camera parameter of its own, so the words reach the model in the prompt.

When no resolution is asked for, a clip is made at the highest resolution the model allows for its length
and inputs, up to 1080p (4k only when asked), and the result says when it's lower and why.

Generated videos are saved to the chat's files and shown inline; channel adapters attach the saved MP4.
The person picks a provider and model in Settings › Models (e.g. ``google/gemini-omni-1.2-flash`` or
``xai/grok-imagine-video-1.5``), from the video models each connected provider's own list offers.

Google, on a Gemini API key:
  - Gemini Omni Flash (``gemini-omni-*``), the Interactions API: 3-15 s, 16:9 or 9:16, 360p-4k (1080p and 4k
    upscaled), up to 6 reference pictures, a first and last frame (3-10 s), and extending a clip by 3-10 s
    (https://aistudio.google.com/docs/omni).
  - Veo (``veo-*``), the ``generate_videos`` operation: 4, 6 or 8 s, 16:9 or 9:16, 720p, or 1080p and 4k at
    8 s; Veo 3.1 takes up to 3 reference pictures (at 8 s), Veo 3 and 3.1 a last frame with a first frame
    (https://aistudio.google.com/docs/veo). Google shut the Veo 3.1 Preview models down on the Gemini API on
    22 Oct 2026; its GA Veo models are on the Gemini Enterprise Agent Platform, which a Gemini API key
    doesn't reach.

xAI (``grok-imagine-video``, ``grok-imagine-video-1.5``), REST POST /v1/videos/generations or
/v1/videos/extensions, then GET /v1/videos/{request_id}; the clip's URL is short-lived, so it's
downloaded at once (https://docs.x.ai/developers/model-capabilities/video/generation):
  - 1-15 s, 480p-1080p (720p for the classic model and for shots from references);
  - reference pictures (14 on 1.5, 7 on the classic model), a last frame on 1.5, and extending a clip by
    2-10 s (https://docs.x.ai/developers/model-capabilities/video/reference-to-video,
    https://docs.x.ai/developers/model-capabilities/video/extension).
"""

from __future__ import annotations

import base64
import contextvars
import hashlib
import io
import logging
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator
from collections.abc import Callable

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from row_bot.tools.base import BaseTool
from row_bot.tools import registry

logger = logging.getLogger(__name__)

# ── Timeouts & polling ───────────────────────────────────────────────────
_GOOGLE_POLL_INTERVAL = 10   # seconds
_XAI_POLL_INTERVAL = 5       # seconds
_JOB_POLL_INTERVAL = 5       # seconds, providers with their own video job APIs
_POLL_TIMEOUT = 360           # 6 minutes hard ceiling

# No preset: video generation stays off and unset until the person chooses a
# model in Settings › Models (decision 9).
NO_VIDEO_MODEL = (
    "No video model is chosen yet, so no generated footage can be made. Unless the person asked for "
    "generated footage, make the video with the video tool instead: illustrated scenes (generate_image) "
    "with camera motion, voices and music. Otherwise tell them to choose a video model in Settings › Models."
)

# ── Google constraints ───────────────────────────────────────────────────
_GOOGLE_ASPECT_RATIOS = {"16:9", "9:16"}
_GOOGLE_DURATIONS = {4, 6, 8}
_GOOGLE_RESOLUTIONS = {"720p", "1080p", "4k"}
_GOOGLE_HIGH_RES_DURATION = 8  # 1080p / 4k require 8s
_OMNI_RESOLUTIONS = {"360p", "720p", "1080p", "4k"}

# ── xAI constraints ─────────────────────────────────────────────────────
_XAI_ASPECT_RATIOS = {"1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "21:9", "5:2"}
_XAI_MIN_DURATION = 1
_XAI_MAX_DURATION = 15
_XAI_RESOLUTIONS = {"480p", "720p", "1080p"}
_XAI_CLASSIC = "grok-imagine-video"  # 720p at most; references, no last frame (xAI's generation guide)

# An earlier clip read to continue it: Omni and xAI take clips of up to 15 s.
_MAX_CLIP_BYTES = 64 * 1024 * 1024


# ── What a shot holds to (C3) ────────────────────────────────────────────

@dataclass
class _Shot:
    """What a clip holds to beyond its prompt, and what the result must say about it."""

    references: list[bytes] = field(default_factory=list)
    last_frame: bytes | None = None
    extend: bytes | None = None  # the earlier clip, as MP4
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _Offers:
    """What one model's API documents taking, beyond a prompt and a first frame."""

    references: int = 0        # how many reference pictures
    last_frame: bool = False
    last_alone: bool = False   # a last frame without a first one
    mixes: bool = False        # reference pictures together with a first or last frame
    extend: bool = False
    extend_why: str = ""


_ANY = 6  # references for an API that documents them but no count


def _offers(provider_id: str, model: str) -> _Offers:
    """What the chosen model documents taking; the docstring and each comment cite the provider's page."""
    lower = model.lower()
    if provider_id == "google":
        if lower.startswith("gemini-omni"):
            return _Offers(references=6, last_frame=True, extend=True)
        # Veo extends only a clip it made in the last two days, by Google's own link to it.
        why = "Veo continues only a clip it made itself in the last two days"
        if lower.startswith("veo-3.1") and "lite" not in lower:
            return _Offers(references=3, last_frame=True, extend_why=why)
        return _Offers(last_frame=lower.startswith("veo-3"), extend_why=why)
    if provider_id in {"xai", "xai_oauth"}:
        if lower == _XAI_CLASSIC:
            return _Offers(references=7, extend=True)
        return _Offers(references=14, last_frame=True, last_alone=True, mixes=True, extend=True)
    if provider_id == "openrouter":
        # https://openrouter.ai/docs/guides/overview/multimodal/video-generation: frame_images (first_frame,
        # last_frame) win over input_references; /videos/models lists a model's supported_frame_images.
        from row_bot.providers.media import generation_options

        frames = (generation_options(provider_id, model) or {}).get("frame_images") or []
        return _Offers(references=_ANY, last_frame=not frames or "last_frame" in frames, last_alone=True)
    if provider_id == "together":
        # https://docs.together.ai/reference/create-videos: media.frame_images ("first"/"last") and
        # media.reference_images; no way to continue a clip.
        return _Offers(references=_ANY, last_frame=True, last_alone=True, mixes=True)
    if provider_id == "minimax":
        # https://platform.minimax.io/docs/api-reference/video-generation-v2-create: content roles first_frame,
        # last_frame and reference_image (up to 9); frames and references are never mixed.
        return _Offers(references=9, last_frame=True)
    if provider_id == "zai":
        # https://docs.z.ai/guides/video/cogvideox-3: image_url takes the first frame, or the first and last.
        return _Offers(last_frame=True)
    if provider_id == "dashscope" and _wan_takes_media(model):
        # Wan 2.7 i2v takes first_frame and last_frame; r2v up to 5 reference_image with a first_frame
        # (https://www.alibabacloud.com/help/en/model-studio/image-to-video-general-api-reference,
        # https://www.alibabacloud.com/help/en/model-studio/wan-video-to-video-api-reference). Its first_clip
        # continues a clip only from a public web link.
        why = "Wan continues a clip only from a public web link"
        if "r2v" in lower:
            return _Offers(references=5, mixes=True, extend_why=why)
        return _Offers(last_frame="i2v" in lower, extend_why=why)
    return _Offers()


def _label(provider_id: str) -> str:
    from row_bot.providers.media import VIDEO_PROVIDER_META

    return VIDEO_PROVIDER_META.get(provider_id, {}).get("label", provider_id)


def _fit(shot: _Shot, provider_id: str, model: str, first_frame: bool) -> str:
    """Fit a shot to what its model takes, noting each thing it leaves out; a refusal when it can't extend."""
    offers, label = _offers(provider_id, model), _label(provider_id)
    if shot.extend is not None:
        if not offers.extend:
            why = f": {offers.extend_why}" if offers.extend_why else ""
            return (f"{label} can't continue a clip with {model}{why}. Nothing was made or charged. Offer the "
                    "person a model that extends clips (Settings › Models), or a new shot that starts on the "
                    "clip's last frame (animate_image).")
        left = [what for what, given in (("first frame", first_frame), ("last frame", shot.last_frame),
                                         ("reference pictures", shot.references)) if given]
        if left:
            shot.notes.append(f"Not used: the {' and '.join(left)}. Extending continues the clip as it is.")
        shot.last_frame, shot.references = None, []
        return ""
    if shot.last_frame is not None and not offers.last_frame:
        shot.last_frame = None
        shot.notes.append(f"Last frame: {label} doesn't take one for {model}, so the shot ends where the model "
                          "takes it.")
    elif shot.last_frame is not None and not first_frame and not offers.last_alone:
        shot.last_frame = None
        shot.notes.append(f"Last frame: {label} takes one only with a first frame, so it wasn't used. Offer the "
                          "person a shot from its first picture to this last frame with animate_image.")
    if shot.references:
        if not offers.references:
            shot.notes.append(f"References: {label} doesn't take reference pictures for {model}, so the shot "
                              "was made without them.")
            shot.references = []
        elif (first_frame or shot.last_frame is not None) and not offers.mixes:
            shot.notes.append(f"References: {label} takes reference pictures or a first and last frame, not "
                              "both, so the shot was made from the frames without the references.")
            shot.references = []
        elif len(shot.references) > offers.references:
            shot.notes.append(f"References: {label} takes up to {offers.references} for {model}, so the rest "
                              "weren't used.")
            del shot.references[offers.references:]
    return ""


def _best_resolution(provider_id: str, model: str, duration: int, shot: _Shot) -> tuple[str, str]:
    """The highest resolution, up to 1080p, the model makes for this length and these inputs, and why it's
    lower when it is. Empty leaves it to the model, for providers that list no model's resolutions."""
    lower = model.lower()
    if provider_id == "google":
        if lower.startswith("gemini-omni") or shot.references or duration > 6:
            return "1080p", ""
        return "720p", "Resolution: Veo makes 1080p only at 8 seconds, so this is 720p."
    if provider_id in {"xai", "xai_oauth"}:
        if lower == _XAI_CLASSIC:
            return "720p", ""
        if shot.references:
            return "720p", "Resolution: xAI makes shots from reference pictures at up to 720p."
        return "1080p", ""
    if provider_id in {"together", "atlascloud"}:
        return "", ""
    if provider_id == "openrouter":
        from row_bot.providers.media import generation_options

        return ("1080p", "") if generation_options(provider_id, model) is not None else ("", "")
    return "1080p", ""


def _mode(image_bytes: bytes | None, shot: _Shot) -> str:
    if shot.extend is not None:
        return "extension"
    if image_bytes is not None or shot.last_frame is not None:
        return "image-to-video"
    return "reference-to-video" if shot.references else "text-to-video"


def _data_uri(data: bytes, mime: str = "") -> str:
    return f"data:{mime or _detect_mime(data)};base64,{base64.b64encode(data).decode('ascii')}"


def _resolve_clip(value: str) -> bytes:
    """An earlier clip to continue: 'last' (this chat's newest video), a name in this chat's files, an edit
    (edit-…), a recording (rec-…) or a workspace path, read through ``media_sources``."""
    from row_bot import media_sources
    from row_bot.application.attachments import AttachmentError, list_conversation_files
    from row_bot.tool_outputs import current_thread_id

    conversation = current_thread_id()
    if not conversation:
        raise ValueError("Extending a clip needs a conversation.")
    text = value.strip()
    if text.lower() == "last":
        try:
            files = list_conversation_files(conversation)
        except AttachmentError:
            files = []
        newest = next((item for item in files if str(item.get("mime_type") or "").startswith("video/")), None)
        if newest is None:
            raise ValueError("There's no video in this chat's files to extend yet.")
        text = str(newest["name"])
    try:
        found = media_sources.resolve(text, conversation)[0]
        if found.kind != "video":
            raise ValueError(f"{text} isn't a video, so it can't be extended.")
        return found.read_bytes(limit=_MAX_CLIP_BYTES)
    except media_sources.SourceError as exc:
        raise ValueError(str(exc)) from None


def _shot_inputs(references: list[str] | None, character: str, style: str, last_frame: str,
                 extend: str = "") -> _Shot:
    """The pictures and clip a shot names, read once; ValueError says what can't be found."""
    from row_bot.tools.image_gen_tool import reference_picture, resolve_references

    return _Shot(references=resolve_references(references, character, style),
                 last_frame=reference_picture(last_frame) if last_frame.strip() else None,
                 extend=_resolve_clip(extend) if extend.strip() else None)


def _plan(duration: int, resolution: str, image_bytes: bytes | None, shot: _Shot) -> tuple[str, str]:
    """(resolution, refusal) for a shot on the chosen model: fitted to the model, at its best resolution."""
    provider, model = _parse_model_config(_get_configured_selection())
    refusal = _fit(shot, provider, model, image_bytes is not None)
    if not refusal and not resolution:
        resolution, why = _best_resolution(provider, model, duration, shot)
        if why:
            shot.notes.append(why)
    return resolution, refusal


# ── Parameter normalization ──────────────────────────────────────────────

def _normalize_google_params(
    duration: int, aspect_ratio: str, resolution: str, *, references: bool = False,
) -> tuple[int, str, str]:
    """Snap values to Google-valid ranges and return (duration, aspect, res)."""
    # Duration: snap to nearest valid
    if duration <= 4:
        dur = 4
    elif duration <= 6:
        dur = 6
    else:
        dur = 8

    # Aspect ratio
    ar = aspect_ratio if aspect_ratio in _GOOGLE_ASPECT_RATIOS else "16:9"

    # Resolution
    res = resolution if resolution in _GOOGLE_RESOLUTIONS else "720p"
    # 1080p, 4k and reference pictures need 8 s.
    if res in ("1080p", "4k") or references:
        dur = _GOOGLE_HIGH_RES_DURATION

    return dur, ar, res


def _normalize_xai_params(
    duration: int, aspect_ratio: str, resolution: str, *, model: str = "", references: bool = False,
) -> tuple[int, str, str]:
    """Clamp values to xAI-valid ranges and return (duration, aspect, res)."""
    longest = 10 if references and model.lower() == _XAI_CLASSIC else _XAI_MAX_DURATION
    dur = max(_XAI_MIN_DURATION, min(longest, duration))
    ar = aspect_ratio if aspect_ratio in _XAI_ASPECT_RATIOS else "16:9"
    res = resolution if resolution in _XAI_RESOLUTIONS else "720p"
    if res == "1080p" and (references or model.lower() == _XAI_CLASSIC):
        res = "720p"
    return dur, ar, res


# ── Side-channel for generated videos ────────────────────────────────────
# The streaming layer reads and clears this after generate/animate calls.
_last_generated_video: dict | None = None  # {path, filename, provider, model, duration, mode}
_strict_output: contextvars.ContextVar[tuple[str, Callable[[], None], Callable[[bytes], str]] | None] = contextvars.ContextVar(
    "strict_video_generation_output", default=None)


@contextmanager
def strict_generation_output(*, selection: str, validate: Callable[[], None],
                             sink: Callable[[bytes], str], auth=None) -> Iterator[None]:
    """Bind one reviewed selection and owned output; legacy saving is unchanged."""
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
_video_output_dir_var: contextvars.ContextVar[Path | None] = contextvars.ContextVar(
    "video_output_dir",
    default=None,
)
_video_output_filename_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "video_output_filename",
    default=None,
)


@contextmanager
def video_output_override(directory: str | Path, filename: str | None = None) -> Iterator[None]:
    """Temporarily save generated video output to a specific directory.

    Normal chat/tool calls keep using per-thread media. In-app flows such as
    Buddy Hatch do not always run inside an agent thread, so they need a narrow
    way to persist generated motion directly into their own asset directory.
    """

    dir_token = _video_output_dir_var.set(Path(directory).expanduser())
    name_token = _video_output_filename_var.set(filename)
    try:
        yield
    finally:
        _video_output_filename_var.reset(name_token)
        _video_output_dir_var.reset(dir_token)


def _set_pending_video(value: dict) -> None:
    from row_bot.application.attachment_context import current_caches
    global _last_generated_video
    caches = current_caches()
    if caches is None:
        _last_generated_video = value
    else:
        caches.pending_video = value


def get_and_clear_last_video() -> dict | None:
    """Return and clear the pending generated video metadata, if any."""
    global _last_generated_video
    from row_bot.application.attachment_context import current_caches
    caches = current_caches()
    if caches is not None:
        vid = caches.pending_video
        caches.pending_video = None
        return vid
    vid = _last_generated_video
    _last_generated_video = None
    return vid


# ── Provider resolution ──────────────────────────────────────────────────

def _parse_model_config(value: str) -> tuple[str, str]:
    """Parse a 'provider/model' config string → (provider, model_id)."""
    if "/" in value:
        provider, model_id = value.split("/", 1)
        return provider, model_id
    return "google", value


def get_available_video_models() -> dict[str, str]:
    """Return {config_value: display_label} for models whose provider key is set.

    Used by the Settings UI to populate the model dropdown.
    """
    from row_bot.models import _cloud_model_cache, _sync_custom_model_cache
    from row_bot.providers.media import media_model_options
    _sync_custom_model_cache()
    return media_model_options("video", _cloud_model_cache)


def _get_configured_selection() -> str:
    """Return the raw 'provider/model' string from tool config."""
    scope = _strict_output.get()
    if scope is not None:
        return scope[0]
    val = registry.get_tool_config("video_gen", "model", None)
    if val:
        return val
    tool = registry.get_tool("video_gen")
    if tool:
        val = tool.get_config("model", "")
        if val:
            return val
    return ""


def _unlisted_note() -> str:
    """Why a request may have failed: the chosen model's provider no longer lists it."""
    from row_bot.models import _cloud_model_cache
    from row_bot.providers.media import unlisted_media_note

    note = unlisted_media_note("video", _get_configured_selection(), _cloud_model_cache)
    return f" {note}" if note else ""


def _get_configured_model() -> str:
    """Return just the model ID from the stored config."""
    _, model_id = _parse_model_config(_get_configured_selection())
    return model_id


def _get_google_client():
    """Return a google.genai.Client configured with the user's API key."""
    from row_bot.providers.media_auth import current_media_auth, google_client
    if current_media_auth("google") is not None:
        return google_client()
    from row_bot.providers.media import media_api_key
    api_key = media_api_key("google")
    if not api_key:
        raise RuntimeError(
            "No Google API key configured. "
            "Please add your Google API key in Settings › Models."
        )
    from google import genai
    return genai.Client(api_key=api_key)


def _get_xai_key() -> str:
    """Return the xAI API key or raise."""
    from row_bot.providers.media import media_api_key
    api_key = media_api_key("xai")
    if not api_key:
        raise RuntimeError(
            "No xAI API key configured. "
            "Please add your xAI API key in Settings › Models."
        )
    return api_key


# ── Image source resolution (reuses image_gen_tool patterns) ─────────────

def _resolve_image_source(image_source: str) -> bytes:
    """Resolve an image_source string to raw bytes.

    Priority:
      1. "last" → last generated image (from image_gen_tool cache)
      2. Key match in image_gen_tool._image_cache → pasted/attached image
      3. File path on disk / workspace-relative path
    """
    from row_bot.tools.image_gen_tool import _execution_image_cache
    _image_cache = _execution_image_cache()

    # "last" — use the last generated image
    if image_source.strip().lower() == "last":
        last_bytes = _image_cache.get("__last_generated__")
        if last_bytes:
            return last_bytes
        raise ValueError(
            "No previously generated image available. "
            "Generate an image first, then use animate_image with image_source='last'."
        )

    # Check attachment cache (pasted images)
    if image_source in _image_cache:
        return _image_cache[image_source]

    # Partial filename match in cache
    for cached_name, cached_data in _image_cache.items():
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

    raise ValueError(
        f"Could not find image '{image_source}'. "
        "Use 'last' for the last generated image, paste/attach an image, "
        "or provide a valid file path."
    )


def _detect_mime(data: bytes) -> str:
    """Detect image MIME type from magic bytes.  Defaults to image/png."""
    if data[:4] == b"\xff\xd8\xff\xe0" or data[:4] == b"\xff\xd8\xff\xe1":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


# ── Save video to thread media ───────────────────────────────────────────

def _save_video_to_disk(video_bytes: bytes, prefix: str = "vid") -> str | None:
    """Persist generated video to the per-thread media directory.

    Returns the absolute path as a string, or None if saving fails.
    """
    scope = _strict_output.get()
    if scope is not None:
        _validate_generation()
        if not isinstance(video_bytes, bytes) or not video_bytes or len(video_bytes) > 64 * 1024 * 1024:
            raise ValueError("generated_media_too_large")
        return scope[2](video_bytes)
    override_dir = _video_output_dir_var.get()
    if override_dir is not None:
        try:
            override_dir.mkdir(parents=True, exist_ok=True)
            filename = _video_output_filename_var.get() or f"{prefix}.mp4"
            if not filename.lower().endswith(".mp4"):
                filename = f"{filename}.mp4"
            saved_path = override_dir / filename
            saved_path.write_bytes(video_bytes)
            logger.info("Saved generated video to %s", saved_path)
            return str(saved_path)
        except Exception:
            logger.warning("Failed to save generated video to override directory", exc_info=True)
            return None

    try:
        from row_bot.application.attachment_context import current_caches
        caches = current_caches()
        if caches is not None:
            from row_bot.application.generated_media import save_generated_output
            return save_generated_output(caches.conversation_id, video_bytes, prefix=prefix, extension="mp4")
        from row_bot.agent import _current_thread_id_var
        from row_bot.threads import save_media_file, _next_media_filename

        thread_id = _current_thread_id_var.get() or ""
        if not thread_id:
            logger.debug("_save_video_to_disk: no thread_id in context")
            return None

        filename = _next_media_filename(thread_id, prefix, "mp4")
        saved_path = save_media_file(thread_id, filename, video_bytes)
        logger.info("Saved generated video to %s", saved_path)
        return str(saved_path)
    except Exception:
        logger.warning("Failed to save generated video to disk", exc_info=True)
        return None


def _finish_video(video_bytes: bytes, *, label: str, model: str, duration: int, settings: str, mode: str,
                  shot: _Shot) -> str:
    """Keep and show a generated clip, and say what was made and what the model left out."""
    saved = _save_video_to_disk(video_bytes)
    _set_pending_video({
        "path": saved,
        "filename": Path(saved).name if saved else None,
        "provider": label,
        "model": model,
        "duration": duration,
        "mode": mode,
    })
    result = f"Video generated successfully. Model: {model} | {settings} | Mode: {mode} | Provider: {label}"
    result += "".join(f"\n{note}" for note in shot.notes)
    if saved:
        result += f"\nSaved to: {saved}"
    return result


# ── Google: Gemini Omni Flash and Veo ────────────────────────────────────

# Files API uploads a provider can reuse (the person's references across a film's shots), by content and key.
# Google keeps an upload 48 hours (https://aistudio.google.com/docs/omni, "Files API").
_GOOGLE_FILES: dict[tuple[str, str], tuple[str, float]] = {}
_GOOGLE_FILE_LIFE = 47 * 3600
_GOOGLE_FILE_POLL = 2


def _google_file(client, data: bytes, mime: str) -> str:
    """A Files API URI for these bytes: an upload from earlier in this run when it's still kept, else a new one."""
    from row_bot.providers.media import media_api_key

    key = (hashlib.sha256(data).hexdigest(), hashlib.sha256(media_api_key("google").encode()).hexdigest())
    kept = _GOOGLE_FILES.get(key)
    if kept and kept[1] > time.monotonic():
        return kept[0]
    _validate_generation()
    uploaded = client.files.upload(file=io.BytesIO(data), config={"mime_type": mime})
    waited = 0
    while "PROCESSING" in str(getattr(uploaded, "state", "")) and waited < _POLL_TIMEOUT:
        time.sleep(_GOOGLE_FILE_POLL)
        waited += _GOOGLE_FILE_POLL
        uploaded = client.files.get(name=uploaded.name)
    _GOOGLE_FILES[key] = (str(uploaded.uri), time.monotonic() + _GOOGLE_FILE_LIFE)
    return str(uploaded.uri)


def _omni_video(client, interaction) -> bytes:
    """The clip an Omni interaction made: inline, or a Files API file once it's ready."""
    video = getattr(interaction, "output_video", None)
    if getattr(video, "data", None):
        return base64.b64decode(video.data)
    found = re.search(r"files/[\w-]+", str(getattr(video, "uri", "") or ""))
    if not found:
        return b""
    name, waited = found.group(0), 0
    while "PROCESSING" in str(getattr(client.files.get(name=name), "state", "")) and waited < _POLL_TIMEOUT:
        time.sleep(_GOOGLE_FILE_POLL)
        waited += _GOOGLE_FILE_POLL
    _validate_generation()
    return client.files.download(file=name)


def _generate_video_omni(client, model: str, prompt: str, duration: int, aspect_ratio: str, resolution: str,
                         image_bytes: bytes | None, shot: _Shot) -> str:
    """Gemini Omni Flash through the Interactions API (https://aistudio.google.com/docs/omni)."""
    frames = [data for data in (image_bytes, shot.last_frame) if data is not None]
    parts: list[dict] = []
    for data in frames or shot.references:
        mime = _detect_mime(data)
        parts.append({"type": "image", "uri": _google_file(client, data, mime), "mime_type": mime})
    if shot.extend is not None:
        parts.append({"type": "video", "uri": _google_file(client, shot.extend, "video/mp4"),
                      "mime_type": "video/mp4"})
    parts.append({"type": "text", "text": prompt})
    # Two frames and each extension make 3-10 s; anything else 3-15 s.
    dur = max(3, min(10 if len(frames) == 2 or shot.extend is not None else 15, duration))
    response_format: dict = {"type": "video", "duration": f"{dur}s", "delivery": "uri"}
    if shot.extend is None:
        ar = aspect_ratio if aspect_ratio in _GOOGLE_ASPECT_RATIOS else "16:9"
        res = resolution if resolution in _OMNI_RESOLUTIONS else "1080p"
        response_format |= {"aspect_ratio": ar, "resolution": res}
        settings = _settings(dur, ar, res)
    else:
        settings = f"Duration: {dur}s more | Aspect ratio: the clip's own | Resolution: the clip's own"
    request: dict = {"model": model, "input": parts, "response_format": response_format}
    # One picture alone is a first frame unless the task says it's a reference; an extension says so too.
    task = "extend" if shot.extend is not None else "reference_to_video" if shot.references and not frames else ""
    if task:
        request["generation_config"] = {"video_config": {"task": task}}
    logger.info("generate_video (Google Omni): model=%s, %s, task=%s", model, settings, task or "default")
    try:
        _validate_generation()
        video_bytes = _omni_video(client, client.interactions.create(**request))
    except Exception as e:
        if _strict_output.get() is not None:
            raise
        logger.error("Video generation request failed: %s", e, exc_info=True)
        return f"Video generation failed: {e}{_unlisted_note()}"
    if not video_bytes:
        return "Video generation returned no video data."
    return _finish_video(video_bytes, label="Google", model=model, duration=dur, settings=settings,
                         mode=_mode(image_bytes, shot), shot=shot)


def _generate_video_google(
    prompt: str,
    duration: int,
    aspect_ratio: str,
    resolution: str,
    image_bytes: bytes | None = None,
    *,
    shot: _Shot | None = None,
) -> str:
    """Generate a video via Google (Omni Flash or Veo).  Blocks until completion or timeout."""
    shot = shot or _Shot()
    client = _get_google_client()
    model = _get_configured_model()
    if model.lower().startswith("gemini-omni"):
        return _generate_video_omni(client, model, prompt, duration, aspect_ratio, resolution, image_bytes, shot)
    dur, ar, res = _normalize_google_params(duration, aspect_ratio, resolution, references=bool(shot.references))

    from google.genai import types

    def picture(data: bytes):
        return types.Image(image_bytes=data, mime_type=_detect_mime(data))

    # Do not force person_generation here. Google's accepted values vary by
    # mode and region, and the default request shape is the most compatible.
    config_kwargs: dict = {"aspect_ratio": ar, "resolution": res, "duration_seconds": dur}
    if shot.references:
        config_kwargs["reference_images"] = [
            types.VideoGenerationReferenceImage(image=picture(data), reference_type="asset")
            for data in shot.references]
    if shot.last_frame is not None:
        config_kwargs["last_frame"] = picture(shot.last_frame)
    config = types.GenerateVideosConfig(**config_kwargs)

    logger.info(
        "generate_video (Google): model=%s, duration=%ds, aspect=%s, res=%s",
        model, dur, ar, res,
    )

    try:
        kwargs: dict = {"model": model, "prompt": prompt, "config": config}
        if image_bytes is not None:
            kwargs["image"] = picture(image_bytes)

        _validate_generation()
        operation = client.models.generate_videos(**kwargs)
    except Exception as e:
        if _strict_output.get() is not None:
            raise
        logger.error("Video generation request failed: %s", e, exc_info=True)
        return f"Video generation failed: {e}{_unlisted_note()}"

    # Poll until done
    elapsed = 0
    while not operation.done:
        if elapsed >= _POLL_TIMEOUT:
            op_name = getattr(operation, "name", "unknown")
            return (
                f"Video generation timed out after {_POLL_TIMEOUT}s. "
                f"Operation: {op_name}. The video may still complete on the "
                "server — try again shortly."
            )
        time.sleep(_GOOGLE_POLL_INTERVAL)
        elapsed += _GOOGLE_POLL_INTERVAL
        try:
            _validate_generation()
            operation = client.operations.get(operation)
        except Exception as e:
            if _strict_output.get() is not None:
                raise
            logger.error("Polling failed: %s", e, exc_info=True)
            return f"Video generation polling failed: {e}"

    # Download the result
    try:
        generated_video = operation.response.generated_videos[0]
        # Download to a temporary buffer
        _validate_generation()
        client.files.download(file=generated_video.video)

        # The SDK saves video data into the file object — read it
        import tempfile
        import os
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp_path = tmp.name
        generated_video.video.save(tmp_path)
        if _strict_output.get() is not None:
            with open(tmp_path, "rb") as downloaded:
                video_bytes = downloaded.read(64 * 1024 * 1024 + 1)
            if len(video_bytes) > 64 * 1024 * 1024:
                raise ValueError("generated_media_too_large")
        else:
            video_bytes = Path(tmp_path).read_bytes()
        os.unlink(tmp_path)
    except Exception as e:
        if _strict_output.get() is not None:
            raise
        logger.error("Video download failed: %s", e, exc_info=True)
        return f"Video generated but download failed: {e}"

    if not video_bytes:
        return "Video generation returned no video data."
    return _finish_video(video_bytes, label="Google", model=model, duration=dur, settings=_settings(dur, ar, res),
                         mode=_mode(image_bytes, shot), shot=shot)


# ── xAI video generation ────────────────────────────────────────────────

def _generate_video_xai(
    prompt: str,
    duration: int,
    aspect_ratio: str,
    resolution: str,
    image_bytes: bytes | None = None,
    *,
    provider_id: str = "xai",
    shot: _Shot | None = None,
) -> str:
    """Generate (or extend) a video via xAI.  Blocks until completion or timeout."""
    from row_bot.providers.xai_media import xai_media_get, xai_media_json_request, xai_media_label

    shot = shot or _Shot()
    model = _get_configured_model()
    provider_label = xai_media_label(provider_id)
    if shot.extend is not None:
        # The added part is 2-10 s; the clip keeps its own shape and resolution, up to 720p.
        dur = max(2, min(10, duration))
        path = "/videos/extensions"
        settings = f"Duration: {dur}s more | Aspect ratio: the clip's own | Resolution: up to 720p, the clip's own"
        body: dict = {"model": model, "prompt": prompt, "duration": dur,
                      "video": {"url": _data_uri(shot.extend, "video/mp4")}}
    else:
        dur, ar, res = _normalize_xai_params(duration, aspect_ratio, resolution, model=model,
                                             references=bool(shot.references))
        path, settings = "/videos/generations", _settings(dur, ar, res)
        body = {"model": model, "prompt": prompt, "duration": dur, "aspect_ratio": ar, "resolution": res}
        if image_bytes is not None:
            body["image"] = {"url": _data_uri(image_bytes)}
        if shot.last_frame is not None:
            body["last_frame"] = {"url": _data_uri(shot.last_frame)}
        if shot.references:
            body["reference_images"] = [{"url": _data_uri(data)} for data in shot.references]

    logger.info("generate_video (%s): model=%s, %s", provider_label, model, settings)

    # Step 1: Start generation
    try:
        _validate_generation()
        start_data = xai_media_json_request(provider_id, "POST", path, json=body, timeout=30)
        request_id = start_data.get("request_id")
        if not request_id:
            return "xAI video generation returned no request_id."
    except Exception as e:
        if _strict_output.get() is not None:
            raise
        logger.error("%s video generation request failed: %s", provider_label, e, exc_info=True)
        return f"Video generation failed: {e}{_unlisted_note()}"

    logger.info("%s video generation started: request_id=%s", provider_label, request_id)

    # Step 2: Poll for result
    elapsed = 0
    while True:
        if elapsed >= _POLL_TIMEOUT:
            return (
                f"Video generation timed out after {_POLL_TIMEOUT}s. "
                f"Request ID: {request_id}. The video may still be processing."
            )
        time.sleep(_XAI_POLL_INTERVAL)
        elapsed += _XAI_POLL_INTERVAL

        try:
            _validate_generation()
            data = xai_media_json_request(
                provider_id,
                "GET",
                f"/videos/{request_id}",
                timeout=30,
            )
        except Exception as e:
            if _strict_output.get() is not None:
                raise
            logger.error("%s polling failed: %s", provider_label, e, exc_info=True)
            return f"Video generation polling failed: {e}"

        status = data.get("status", "")
        if status == "done":
            break
        if status == "failed":
            return f"Video generation failed on xAI side. Request ID: {request_id}"
        if status == "expired":
            return f"Video generation request expired. Request ID: {request_id}"
        # status == "pending" → keep polling

    # Step 3: Download video from ephemeral URL
    video_url = data.get("video", {}).get("url")
    if not video_url:
        return f"Video generation completed but no URL returned. Request ID: {request_id}"

    try:
        _validate_generation()
        dl_resp = xai_media_get(provider_id, video_url, timeout=120, follow_redirects=True)
        video_bytes = dl_resp.content
    except Exception as e:
        if _strict_output.get() is not None:
            raise
        logger.error("%s video download failed: %s", provider_label, e, exc_info=True)
        return f"Video generated but download failed: {e}"

    if not video_bytes:
        return "Video download returned no data."
    return _finish_video(video_bytes, label=provider_label, model=model, duration=dur, settings=settings,
                         mode=_mode(image_bytes, shot), shot=shot)


# ── Providers with their own video job APIs: submit a job, poll it, download the clip ─────

# The short side of each resolution, for providers that take width and height.
_SHORT_SIDE = {"480p": 480, "720p": 720, "1080p": 1080, "4k": 2160}
_HIGH_RESOLUTIONS = {"1080p", "4k"}

_MINIMAX_RATIOS = {"21:9", "16:9", "4:3", "1:1", "3:4", "9:16"}
_DASHSCOPE_RATIOS = {"16:9", "9:16", "1:1", "4:3", "3:4"}
# The sizes CogVideoX-3 takes, by aspect ratio and resolution.
_ZAI_SIZES = {
    "16:9": {"720p": "1280x720", "1080p": "1920x1080", "4k": "3840x2160"},
    "9:16": {"720p": "720x1280", "1080p": "1080x1920"},
    "1:1": {"720p": "1024x1024"},
}


def _frame_size(resolution: str, aspect_ratio: str) -> tuple[int, int]:
    """Width and height for a resolution and aspect ratio, rounded to even pixels."""
    short = _SHORT_SIDE.get(resolution.lower(), 720)
    w, _, h = aspect_ratio.partition(":")
    ratio_w, ratio_h = (int(w), int(h)) if w.isdigit() and h.isdigit() and int(h) else (16, 9)
    long = round(short * max(ratio_w, ratio_h) / min(ratio_w, ratio_h) / 2) * 2
    return (long, short) if ratio_w >= ratio_h else (short, long)


def _settings(duration: int, aspect_ratio: str, resolution: str) -> str:
    return f"Duration: {duration}s | Aspect ratio: {aspect_ratio} | Resolution: {resolution}"


def _job_request(provider_id: str, method: str, path: str, *, json: dict | None = None, timeout: float = 30,
                 headers: dict | None = None):
    """Call a video provider's own API with its key, raising its error message."""
    import httpx

    from row_bot.providers.media import VIDEO_PROVIDER_META, media_api_key, media_error_text

    meta = VIDEO_PROVIDER_META[provider_id]
    _validate_generation()
    response = httpx.request(
        method, f"{meta['base_url']}{path}", json=json, timeout=timeout,
        headers={"Authorization": f"Bearer {media_api_key(provider_id)}", **(headers or {})},
    )
    if response.status_code >= 400:
        raise RuntimeError(f"HTTP {response.status_code}: {media_error_text(response)}")
    return response


# Each route submits a job, returning its ID and the settings it asked for, and reads a
# job's state: ("running", ""), ("done", the clip's link) or (how it ended, why). A shot's
# pictures arrive fitted to what the model takes (``_fit``), and each goes as a data URI.

def _submit_openrouter(provider_id: str, model: str, prompt: str, duration: int, aspect_ratio: str,
                       resolution: str, image: str, shot: _Shot) -> tuple[str, str]:
    from row_bot.providers.media import (
        generation_options, nearest_aspect_ratio, nearest_duration, nearest_resolution, nearest_size,
    )

    sent: dict = {"duration": duration, "aspect_ratio": aspect_ratio, "resolution": resolution or None}
    options = generation_options(provider_id, model)
    if options is not None:
        # Each setting is the nearest the model lists; one it lists nothing for is left to the model.
        sent = {"duration": nearest_duration(duration, options.get("duration", [])),
                "aspect_ratio": nearest_aspect_ratio(aspect_ratio, options.get("aspect_ratio", [])),
                "resolution": nearest_resolution(resolution, options.get("resolution", []))}
        if sent["resolution"] is None:
            sent["size"] = nearest_size(aspect_ratio, resolution, options.get("size", []))
    body: dict = {"model": model, "prompt": prompt, **{key: value for key, value in sent.items() if value is not None}}
    frames = [(image, "first_frame")] if image else []
    if shot.last_frame is not None:
        frames.append((_data_uri(shot.last_frame), "last_frame"))
    if frames:
        body["frame_images"] = [{"type": "image_url", "image_url": {"url": url}, "frame_type": kind}
                                for url, kind in frames]
    if shot.references:
        body["input_references"] = [{"type": "image_url", "image_url": {"url": _data_uri(data)}}
                                    for data in shot.references]
    job = _job_request(provider_id, "POST", "/videos", json=body).json()

    def shown(value, unit: str = "") -> str:
        return f"{value}{unit}" if value is not None else "the model's own"

    return str(job.get("id") or ""), (
        f"Duration: {shown(sent['duration'], 's')} | Aspect ratio: {shown(sent['aspect_ratio'])} | "
        f"Resolution: {shown(sent.get('size') or sent['resolution'])}")


def _submit_together(provider_id: str, model: str, prompt: str, duration: int, aspect_ratio: str,
                     resolution: str, image: str, shot: _Shot) -> tuple[str, str]:
    # Resolution and ratio, not pixels: each model makes its own frame of them (Seedance 1.0 Lite's
    # 720p is 1248×704 and it refuses 1280×720), and Together's list gives no model's sizes (I25).
    body: dict = {"model": model, "prompt": prompt, "seconds": str(duration), "ratio": aspect_ratio}
    if resolution:
        body["resolution"] = resolution
    frames = [{"input_image": image, "frame": "first"}] if image else []
    if shot.last_frame is not None:
        frames.append({"input_image": _data_uri(shot.last_frame), "frame": "last"})
    media: dict = {"frame_images": frames} if frames else {}
    if shot.references:
        media["reference_images"] = [_data_uri(data) for data in shot.references]
    if media:
        body["media"] = media
    job = _job_request(provider_id, "POST", "/videos", json=body).json()
    return str(job.get("id") or ""), _settings(duration, aspect_ratio, resolution or "the model's own")


def _openai_style_state(job: dict, link: str) -> tuple[str, str]:
    status = str(job.get("status") or "")
    if status == "completed":
        return "done", link
    if status in {"failed", "cancelled", "expired"}:
        error = job.get("error")
        return status, str((error.get("message") if isinstance(error, dict) else error) or "")
    return "running", ""


def _poll_openrouter(provider_id: str, job_id: str) -> tuple[str, str]:
    # OpenRouter's clip is downloaded from the job itself, with the key.
    return _openai_style_state(_job_request(provider_id, "GET", f"/videos/{job_id}").json(), "")


def _poll_together(provider_id: str, job_id: str) -> tuple[str, str]:
    job = _job_request(provider_id, "GET", f"/videos/{job_id}").json()
    return _openai_style_state(job, str((job.get("outputs") or {}).get("video_url") or ""))


def _submit_minimax(provider_id: str, model: str, prompt: str, duration: int, aspect_ratio: str,
                    resolution: str, image: str, shot: _Shot) -> tuple[str, str]:
    # MiniMax H3 makes 768P or 2K; its Highspeed, Lite and Max models 480P or 768P.
    if model == "MiniMax-H3":
        res = "2K" if resolution in _HIGH_RESOLUTIONS else "768P"
    else:
        res = "480P" if resolution == "480p" else "768P"
    seconds = max(5 if model == "MiniMax-H3-Max" else 4, min(15, duration))
    content: list[dict] = [{"type": "text", "text": prompt}]
    pictures = [(image, "first_frame")] if image else []
    if shot.last_frame is not None:
        pictures.append((_data_uri(shot.last_frame), "last_frame"))
    pictures += [(_data_uri(data), "reference_image") for data in shot.references]
    content += [{"type": "image_url", "image_url": {"url": url}, "role": role} for url, role in pictures]
    # A clip from frames takes their shape, except on Lite, which needs a ratio; one from references any ratio.
    ratio = aspect_ratio if aspect_ratio in _MINIMAX_RATIOS else "16:9"
    if (image or shot.last_frame is not None) and model != "MiniMax-H3-Lite":
        ratio = "adaptive"
    body = {"model": model, "content": content, "resolution": res, "duration": seconds, "ratio": ratio}
    job = _job_request(provider_id, "POST", "/video_generation", json=body).json()
    return str(job.get("task_id") or ""), _settings(seconds, ratio, res)


def _poll_minimax(provider_id: str, job_id: str) -> tuple[str, str]:
    task = _job_request(provider_id, "GET", f"/query/video_generation/{job_id}").json().get("task") or {}
    status = str(task.get("status") or "")
    if status == "succeeded":
        return "done", str((task.get("content") or {}).get("url") or "")
    if status in {"failed", "cancelled"}:
        return status, str((task.get("error") or {}).get("message") or "")
    return "running", ""


def _submit_zai(provider_id: str, model: str, prompt: str, duration: int, aspect_ratio: str,
                resolution: str, image: str, shot: _Shot) -> tuple[str, str]:
    sizes = _ZAI_SIZES.get(aspect_ratio, _ZAI_SIZES["16:9"])
    wanted = resolution if resolution in sizes else "1080p" if resolution == "4k" and "1080p" in sizes else "720p"
    seconds = 5 if duration <= 7 else 10
    body: dict = {"model": model, "prompt": prompt, "size": sizes[wanted], "duration": seconds}
    if image:
        body["image_url"] = [image] + ([_data_uri(shot.last_frame)] if shot.last_frame is not None else [])
    job = _job_request(provider_id, "POST", "/videos/generations", json=body).json()
    ratio = aspect_ratio if aspect_ratio in _ZAI_SIZES else "16:9"
    return str(job.get("id") or ""), _settings(seconds, ratio, sizes[wanted])


def _poll_zai(provider_id: str, job_id: str) -> tuple[str, str]:
    job = _job_request(provider_id, "GET", f"/async-result/{job_id}").json()
    status = str(job.get("task_status") or "")
    if status == "SUCCESS":
        videos = job.get("video_result") or []
        return "done", str(videos[0].get("url") or "") if videos and isinstance(videos[0], dict) else ""
    if status == "FAIL":
        return "failed", ""
    return "running", ""


def _wan_takes_media(model: str) -> bool:
    """Wan 2.7 and later take a ratio and a media list; earlier Wan models a size and img_url."""
    match = re.match(r"wan(\d+)\.(\d+)", model.lower())
    return not match or (int(match.group(1)), int(match.group(2))) >= (2, 7)


def _submit_dashscope(provider_id: str, model: str, prompt: str, duration: int, aspect_ratio: str,
                      resolution: str, image: str, shot: _Shot) -> tuple[str, str]:
    res = "1080P" if resolution in _HIGH_RESOLUTIONS else "720P"
    ratio = aspect_ratio if aspect_ratio in _DASHSCOPE_RATIOS else "16:9"
    seconds = max(2, min(15, duration))
    task_input: dict = {"prompt": prompt}
    parameters: dict = {"resolution": res, "duration": seconds}
    if _wan_takes_media(model):
        media = [{"type": "first_frame", "url": image}] if image else []
        if shot.last_frame is not None:
            media.append({"type": "last_frame", "url": _data_uri(shot.last_frame)})
        media += [{"type": "reference_image", "url": _data_uri(data)} for data in shot.references]
        if media:
            task_input["media"] = media
        if not image:
            parameters["ratio"] = ratio
    elif image:
        task_input["img_url"] = image
    else:
        width, height = _frame_size(res.lower(), ratio)
        parameters = {"size": f"{width}*{height}", "duration": seconds}
    job = _job_request(provider_id, "POST", "/services/aigc/video-generation/video-synthesis",
                       json={"model": model, "input": task_input, "parameters": parameters},
                       headers={"X-DashScope-Async": "enable"}).json()
    return str((job.get("output") or {}).get("task_id") or ""), _settings(seconds, ratio, res)


def _poll_dashscope(provider_id: str, job_id: str) -> tuple[str, str]:
    output = _job_request(provider_id, "GET", f"/tasks/{job_id}").json().get("output") or {}
    status = str(output.get("task_status") or "")
    if status == "SUCCEEDED":
        return "done", str(output.get("video_url") or "")
    if status in {"FAILED", "CANCELED", "UNKNOWN"}:
        return status.lower(), str(output.get("message") or "")
    return "running", ""


_ATLASCLOUD_ENDED = {"failed", "canceled", "cancelled", "error", "timeout"}


def _submit_atlascloud(provider_id: str, model: str, prompt: str, duration: int, aspect_ratio: str,
                       resolution: str, image: str, shot: _Shot) -> tuple[str, str]:
    # Each Atlas Cloud model names its own duration and size fields, so the model's defaults are used.
    job = _job_request(provider_id, "POST", "/model/generateVideo", json={"model": model, "prompt": prompt}).json()
    return str((job.get("data") or {}).get("id") or ""), (
        "Duration: the model's own | Aspect ratio: the model's own | Resolution: the model's own")


def _poll_atlascloud(provider_id: str, job_id: str) -> tuple[str, str]:
    result = _job_request(provider_id, "GET", f"/model/prediction/{job_id}").json().get("data") or {}
    status = str(result.get("status") or "")
    if status == "completed":
        outputs = result.get("outputs") or []
        return "done", outputs[0] if outputs and isinstance(outputs[0], str) else ""
    if status in _ATLASCLOUD_ENDED:
        return status, str(result.get("error") or "")
    return "running", ""


_VIDEO_JOBS: dict[str, tuple[Callable[..., tuple[str, str]], Callable[[str, str], tuple[str, str]]]] = {
    "openrouter": (_submit_openrouter, _poll_openrouter),
    "together": (_submit_together, _poll_together),
    "minimax": (_submit_minimax, _poll_minimax),
    "zai": (_submit_zai, _poll_zai),
    "dashscope": (_submit_dashscope, _poll_dashscope),
    "atlascloud": (_submit_atlascloud, _poll_atlascloud),
}
# Atlas Cloud's models name their source-image field differently, so they make clips from text only here.
_TEXT_TO_VIDEO_ONLY = {"atlascloud"}


def _job_route(provider_id: str) -> str:
    from row_bot.providers.media import VIDEO_PROVIDER_META

    meta = VIDEO_PROVIDER_META.get(provider_id)
    return meta["route"] if meta and meta["route"] in _VIDEO_JOBS else ""


def _generate_video_job(
    prompt: str,
    duration: int,
    aspect_ratio: str,
    resolution: str,
    image_bytes: bytes | None = None,
    *,
    provider_id: str,
    shot: _Shot | None = None,
) -> str:
    """Generate a video on a provider with its own job API.  Blocks until completion or timeout."""
    import httpx

    from row_bot.providers.media import VIDEO_PROVIDER_META

    shot = shot or _Shot()
    label, route = VIDEO_PROVIDER_META[provider_id]["label"], _job_route(provider_id)
    if image_bytes is not None and route in _TEXT_TO_VIDEO_ONLY:
        return (f"{label} video models can only make videos from text here. "
                "Choose another video model in Settings › Models to animate an image.")
    submit, poll = _VIDEO_JOBS[route]
    model = _get_configured_model()
    image = _data_uri(image_bytes) if image_bytes is not None else ""

    logger.info("generate_video (%s): model=%s, duration=%ds, aspect=%s, res=%s",
                label, model, duration, aspect_ratio, resolution or "model default")
    try:
        job_id, settings = submit(provider_id, model, prompt, duration, aspect_ratio, resolution, image, shot)
    except Exception as e:
        logger.error("%s video generation request failed: %s", label, e, exc_info=True)
        return f"Video generation failed: {e}{_unlisted_note()}"
    if not job_id:
        return f"{label} started no video job."

    elapsed = 0
    while True:
        if elapsed >= _POLL_TIMEOUT:
            return (f"Video generation timed out after {_POLL_TIMEOUT}s. "
                    f"Job: {job_id}. The video may still be processing.")
        time.sleep(_JOB_POLL_INTERVAL)
        elapsed += _JOB_POLL_INTERVAL
        try:
            state, value = poll(provider_id, job_id)
        except Exception as e:
            logger.error("%s polling failed: %s", label, e, exc_info=True)
            return f"Video generation polling failed: {e}"
        if state == "done":
            break
        if state != "running":
            return f"Video generation {state} on {label}'s side{f': {value}' if value else ''}. Job: {job_id}"

    try:
        if route == "openrouter":
            video_bytes = _job_request(provider_id, "GET", f"/videos/{job_id}/content?index=0", timeout=120).content
        else:
            # The clip is at a short-lived link the provider gives; the key isn't sent there.
            if not value:
                return f"Video generation completed but no URL returned. Job: {job_id}"
            _validate_generation()
            download = httpx.get(value, timeout=120, follow_redirects=True)
            download.raise_for_status()
            video_bytes = download.content
    except Exception as e:
        logger.error("%s video download failed: %s", label, e, exc_info=True)
        return f"Video generated but download failed: {e}"
    if not video_bytes:
        return "Video download returned no data."
    return _finish_video(video_bytes, label=label, model=model, duration=duration, settings=settings,
                         mode=_mode(image_bytes, shot), shot=shot)


# ── Core generation functions (dispatchers) ──────────────────────────────

def _dispatch(prompt: str, duration: int, aspect_ratio: str, resolution: str, image_bytes: bytes | None,
              shot: _Shot | None) -> str:
    shot = shot or _Shot()
    resolution, refusal = _plan(duration, resolution, image_bytes, shot)
    if refusal:
        return refusal
    provider, _ = _parse_model_config(_get_configured_selection())
    if provider == "google":
        return _generate_video_google(prompt, duration, aspect_ratio, resolution, image_bytes, shot=shot)
    if provider in {"xai", "xai_oauth"}:
        return _generate_video_xai(prompt, duration, aspect_ratio, resolution, image_bytes, provider_id=provider,
                                   shot=shot)
    if _job_route(provider):
        return _generate_video_job(prompt, duration, aspect_ratio, resolution, image_bytes, provider_id=provider,
                                   shot=shot)
    return f"Unknown video generation provider '{provider}'. Please select a valid model in Settings."


def _generate_video(
    prompt: str,
    duration_seconds: int = 8,
    aspect_ratio: str = "16:9",
    resolution: str = "",
    *,
    shot: _Shot | None = None,
) -> str:
    """Generate a video from a text prompt (or, with ``shot.extend``, continue a clip)."""
    _validate_generation()
    if not _get_configured_selection():
        return NO_VIDEO_MODEL
    return _dispatch(prompt, duration_seconds, aspect_ratio, resolution, None, shot)


def _animate_image(
    prompt: str,
    image_source: str = "last",
    duration_seconds: int = 8,
    aspect_ratio: str = "16:9",
    resolution: str = "",
    *,
    shot: _Shot | None = None,
    image_bytes: bytes | None = None,
) -> str:
    """Turn a still image into a short video clip. The agent's tool gives the still as *image_bytes*, found by
    ``reference_picture``; in-app callers name an *image_source*."""
    _validate_generation()
    if not _get_configured_selection():
        return NO_VIDEO_MODEL
    try:
        image_bytes = image_bytes if image_bytes is not None else _resolve_image_source(image_source)
    except ValueError as e:
        return str(e)
    return _dispatch(prompt, duration_seconds, aspect_ratio, resolution, image_bytes, shot)


# ── Paid calls go through the approval gate (as pictures do) ─────────────


def _selection() -> str:
    """The chosen video model as "provider/model" (a bare legacy name is Google's)."""
    selection = _get_configured_selection()
    return "/".join(_parse_model_config(selection)) if selection else ""


def _what(duration: int, resolution: str, shot: _Shot, made: str) -> str:
    if shot.extend is not None:
        return f"continue a video ({duration} more seconds)"
    return f"{made} ({duration} seconds, {resolution or 'its own resolution'})"


def _generate_video_tool(prompt: str, duration_seconds: int = 8, aspect_ratio: str = "16:9",
                         resolution: str = "", references: list[str] | None = None, character: str = "",
                         style: str = "", last_frame: str = "", extend: str = "") -> str:
    from row_bot.tools.image_gen_tool import _led, paid_generation_check

    if not _get_configured_selection():
        return NO_VIDEO_MODEL
    try:
        shot = _shot_inputs(references, character, style, last_frame, extend)
    except ValueError as e:
        return str(e)
    # Fitted and at its resolution before the card, so it asks for (and prices) the clip that will be made.
    resolution, refusal = _plan(duration_seconds, resolution, None, shot)
    if refusal:
        return refusal
    refusal, line = paid_generation_check("generate_video", "video", _selection(),
                                          _what(duration_seconds, resolution, shot, "make a video"), prompt,
                                          seconds=duration_seconds, resolution=resolution)
    return refusal or _led(line, _generate_video(prompt, duration_seconds, aspect_ratio, resolution, shot=shot))


def _animate_image_tool(prompt: str, image_source: str = "last", duration_seconds: int = 8,
                        aspect_ratio: str = "16:9", resolution: str = "", references: list[str] | None = None,
                        character: str = "", style: str = "", last_frame: str = "") -> str:
    from row_bot.providers.media import bills_per_call
    from row_bot.tools.image_gen_tool import _led, paid_generation_check, reference_picture

    if not _get_configured_selection():
        return NO_VIDEO_MODEL
    try:  # Nothing to approve when there is no picture to animate.
        still = reference_picture(image_source)
        shot = _shot_inputs(references, character, style, last_frame)
    except ValueError as e:
        return str(e)
    selection, line = _selection(), ""
    resolution, _ = _plan(duration_seconds, resolution, still, shot)
    if bills_per_call("video", selection) and _job_route(selection.partition("/")[0]) not in _TEXT_TO_VIDEO_ONLY:
        refusal, line = paid_generation_check(
            "animate_image", "video", selection,
            _what(duration_seconds, resolution, shot, "animate a picture into a video"), prompt,
            seconds=duration_seconds, resolution=resolution)
        if refusal:
            return refusal
    return _led(line, _animate_image(prompt, image_source, duration_seconds, aspect_ratio, resolution, shot=shot,
                                     image_bytes=still))


# ── Pydantic input schemas ───────────────────────────────────────────────

_PROMPT_WORDS = "Use plain shot words (close-up, wide, slow push-in, handheld); they reach the model unchanged."
_REFERENCES = ("Up to 3 pictures the shot holds to (a character, a product, a place): names of pictures in this "
               "chat's files, 'last' or files in the workspace.")
_CHARACTER = "A character saved in Library › Media (pictures tagged 'character: <name>'), by name."
_STYLE = "A style saved in Library › Media (a still tagged 'style: <name>'), by name."
_LAST_FRAME = "A picture the shot ends on, so the next shot can start there; named as references are."
_RESOLUTION = ("Leave empty for the highest the model makes at this length, up to 1080p; or '480p', '720p', "
               "'1080p', '4k'.")


class _GenerateVideoInput(BaseModel):
    prompt: str = Field(
        description=(
            "A detailed description of the video to generate: subject, action, style, camera and ambiance. "
            f"{_PROMPT_WORDS} With extend, what happens next."
        )
    )
    duration_seconds: int = Field(
        default=8,
        description="Length in seconds (with extend, the seconds to add); each model's nearest is used. Default: 8.",
    )
    aspect_ratio: str = Field(
        default="16:9",
        description="Aspect ratio, e.g. '16:9' (default) or '9:16'; each model's nearest is used.",
    )
    resolution: str = Field(default="", description=_RESOLUTION)
    references: list[str] = Field(default_factory=list, description=_REFERENCES)
    character: str = Field(default="", description=_CHARACTER)
    style: str = Field(default="", description=_STYLE)
    last_frame: str = Field(default="", description=_LAST_FRAME)
    extend: str = Field(
        default="",
        description=("An earlier clip to continue instead of starting a new one: 'last' for this chat's newest "
                     "video, or its name in this chat's files."),
    )


class _AnimateImageInput(BaseModel):
    prompt: str = Field(
        description=(
            "How the image should move. Example: 'The cat slowly turns its head and blinks, soft light, "
            f"gentle push-in'. {_PROMPT_WORDS}"
        )
    )
    image_source: str = Field(
        default="last",
        description=(
            "The picture the shot starts on: 'last' for the last generated image (default), the filename of "
            "an attached or pasted image (e.g. 'photo.jpg'), or a file in the workspace."
        ),
    )
    duration_seconds: int = Field(
        default=8,
        description="Video length in seconds. Default: 8.",
    )
    aspect_ratio: str = Field(
        default="16:9",
        description="Video aspect ratio. Default: '16:9'.",
    )
    resolution: str = Field(default="", description=_RESOLUTION)
    references: list[str] = Field(default_factory=list, description=_REFERENCES)
    character: str = Field(default="", description=_CHARACTER)
    style: str = Field(default="", description=_STYLE)
    last_frame: str = Field(default="", description=_LAST_FRAME)


# ── Tool registration ───────────────────────────────────────────────────

class VideoGenTool(BaseTool):

    @property
    def name(self) -> str:
        return "video_gen"

    @property
    def display_name(self) -> str:
        return "🎬 Video Generation"

    @property
    def description(self) -> str:
        return (
            "Generate videos from text descriptions and animate still images, holding shots together with "
            "reference pictures, saved characters and styles, a last frame and extended clips. Uses the "
            "video model chosen in Settings › Models (Google, xAI, OpenRouter, Together AI, MiniMax, Z.AI, "
            "Qwen or Atlas Cloud)."
        )

    @property
    def enabled_by_default(self) -> bool:
        # Off until the person turns it on and chooses a model (decision 11).
        return False

    @property
    def config_schema(self) -> dict[str, dict]:
        # Model selector is rendered in the Models tab (settings.py)
        return {}

    def as_langchain_tools(self) -> list:
        return [
            StructuredTool.from_function(
                func=_generate_video_tool,
                name="generate_video",
                description=(
                    "Generate a video from a text description, or continue an earlier clip (extend). Use this "
                    "when the user asks you to create, produce, or generate any kind of video, animation, "
                    "clip, or motion content. For shots that belong together, give the same references, "
                    "character or style, and end a shot on the picture the next one starts from (last_frame). "
                    "The result says what the chosen model doesn't take."
                ),
                args_schema=_GenerateVideoInput,
            ),
            StructuredTool.from_function(
                func=_animate_image_tool,
                name="animate_image",
                description=(
                    "Turn a still image into a short video clip that starts on it. Use this when the user "
                    "wants to animate an existing image — the last generated image, an attached/pasted "
                    "image, or a file in the workspace. Describe how the image should move; references, "
                    "character, style and last_frame work as in generate_video."
                ),
                args_schema=_AnimateImageInput,
            ),
        ]

    def execute(self, query: str) -> str:
        return _generate_video(prompt=query)


registry.register(VideoGenTool())
