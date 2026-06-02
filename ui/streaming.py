"""Thoth UI - Streaming consumer, send-message, and interrupt-resume logic.

This module extracts the three heavyweight async inner functions from the
monolith:

* ``consume_generation`` - drain event queue and update the UI
* ``send_message``        - append user message, launch producer + consumer
* ``resume_after_interrupt`` - re-start the producer after an approval

Every function receives ``state``, ``p``, and named callbacks so no globals
leak in.
"""

from __future__ import annotations

import asyncio
import base64 as _b64
import logging
import queue
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Callable

from nicegui import run, ui

from ui.state import AppState, GenerationState, P, _active_generations
from ui.constants import (

    SENTENCE_SPLIT,
    MAX_STREAM_SENTENCES,
    YT_URL_PATTERN,
    IMAGE_EXTENSIONS,
)
from ui.render import autolink_urls, _auto_fence_mermaid, render_image_with_save
from ui.performance import log_ui_perf
from ui.tool_trace import canonical_tool_name, display_tool_content, is_browser_tool_name, tool_result_failed
from voice.cues import (
    approval_needed_cue,
    error_cue,
    heard_cue,
    long_running_cue,
    results_found_cue,
    thinking_cue,
    tool_progress_cue,
    tool_start_cue,
)
from voice.output_controller import VoiceOutputController
from voice.speech_policy import make_speakable_response, user_requested_read_aloud

logger = logging.getLogger(__name__)


def _client_is_live(client: Any) -> bool:
    if client is None or getattr(client, "_deleted", False):
        return False
    client_id = getattr(client, "id", "")
    instances = getattr(getattr(client, "__class__", object), "instances", None)
    if client_id and isinstance(instances, dict) and instances and client_id not in instances:
        return False
    return True


def run_realtime_client_js(p: P, code: str, *, context: str = "realtime js") -> bool:
    """Send Realtime browser JS to the owning NiceGUI client, if still alive."""
    client = getattr(p, "realtime_client", None)
    if not _client_is_live(client):
        logger.warning(
            "voice.realtime.pipeline %s",
            {"stage": "browser_js_client_missing", "context": context},
        )
        return False
    try:
        client.run_javascript(code)
        logger.info(
            "voice.realtime.pipeline %s",
            {"stage": "browser_js_dispatched", "context": context, "code_chars": len(str(code or ""))},
        )
        return True
    except Exception as exc:
        logger.warning(
            "voice.realtime.pipeline %s",
            {"stage": "browser_js_dispatch_failed", "context": context, "error": str(exc)[:500]},
            exc_info=True,
        )
        return False


def _codex_auth_block_message(model_ref: str) -> str | None:
    """Return a user-facing reconnect message when the selected Codex model cannot run."""
    try:
        from providers.selection import provider_id_from_choice_value

        if provider_id_from_choice_value(model_ref) != "codex":
            return None
    except Exception:
        return None
    try:
        from providers.codex import codex_runtime_block_message

        return codex_runtime_block_message(refresh_if_needed=True)
    except Exception as exc:
        return (
            "ChatGPT needs to be reconnected before using this Codex model. "
            "Open Settings -> Providers -> ChatGPT / Codex, reconnect, then try again. "
            f"Could not verify ChatGPT sign-in: {exc}"
        )


async def _agent_ready_forced_surface(model_ref: str, surface: str) -> bool:
    """Return whether a forced-Agent surface can run the selected model."""
    try:
        from providers.readiness import evaluate_agent_readiness
        from providers.selection import model_id_from_choice_value

        readiness = await run.io_bound(lambda: evaluate_agent_readiness(model_ref))
        if readiness.ready:
            return True
        model_id = model_id_from_choice_value(model_ref)
        details = "; ".join(readiness.errors) or readiness.user_message()
        ui.notify(
            f"{model_id} cannot run {surface}: this area requires an Agent-ready model. {details}",
            type="negative",
            close_button=True,
            timeout=12000,
        )
        return False
    except Exception as exc:
        ui.notify(
            f"Could not verify Agent Mode readiness for {surface}: {exc}",
            type="negative",
            close_button=True,
            timeout=12000,
        )
        return False


# ── Captured-image memory cap ────────────────────────────────────────
# During long tool-heavy runs (dozens of browser snapshots) the per-gen
# ``captured_images`` list can hold hundreds of megabytes of base64.  We
# cap in-memory base64 entries to this many; older entries get spilled
# to disk and the list slot is replaced with the on-disk filename
# (which ``persist_thread_media_state`` treats as already-persisted).
_MAX_CAPTURED_B64_IN_MEMORY = 20


def _spill_excess_captured_images(gen: GenerationState) -> None:
    """Spill older base64 image entries to disk to keep memory bounded.

    No-op unless more than ``_MAX_CAPTURED_B64_IN_MEMORY`` b64 entries
    are resident.  The list LENGTH is unchanged; only its contents are
    swapped (b64 → filename).  ``captured_images_persist`` stays aligned.
    """
    imgs = gen.captured_images
    flags = gen.captured_images_persist
    from utils.media import is_image_filename
    # Count how many entries are still base64 (not filenames).  Anything
    # that isn't a persisted-media filename is treated as base64.
    def _is_b64(x: Any) -> bool:
        return isinstance(x, str) and bool(x) and not is_image_filename(x)

    b64_count = sum(1 for x in imgs if _is_b64(x))
    if b64_count <= _MAX_CAPTURED_B64_IN_MEMORY:
        return
    try:
        from threads import save_media_file, _next_media_filename
        from utils.media import image_ext_from_b64
    except Exception:
        logger.debug("spill: imports failed", exc_info=True)
        return

    to_spill = b64_count - _MAX_CAPTURED_B64_IN_MEMORY
    for i in range(len(imgs)):
        if to_spill <= 0:
            break
        if not _is_b64(imgs[i]):
            continue
        b64 = imgs[i]
        persist = bool(flags[i]) if i < len(flags) else False
        try:
            raw = b64.split(",", 1)[-1] if b64.startswith("data:") else b64
            data = _b64.b64decode(raw)
            ext = image_ext_from_b64(raw)
            prefix = "gen" if persist else "cap"
            fname = _next_media_filename(gen.thread_id, prefix, ext)
            save_media_file(gen.thread_id, fname, data)
            imgs[i] = fname
            to_spill -= 1
        except Exception:
            logger.debug("spill: failed to write b64 to disk", exc_info=True)
            # Move on - next tick may succeed
            break


def _format_assistant_markdown(text: str) -> str:
    """Normalise assistant markdown before rendering in streaming UI."""
    return autolink_urls(_auto_fence_mermaid(text or ""))


def _img_data_uri(b64: str) -> str:
    """Return a data URI with the correct MIME type for a base64-encoded image."""
    if b64.startswith("iVBOR"):
        return f"data:image/png;base64,{b64}"
    if b64.startswith("UklGR"):
        return f"data:image/webp;base64,{b64}"
    if b64.startswith("R0lGO"):
        return f"data:image/gif;base64,{b64}"
    return f"data:image/jpeg;base64,{b64}"


def _detach_generation(gen: GenerationState, state: AppState, reason: str) -> None:
    """Convert a live run into a detached run after the client disappears."""
    if gen.detached:
        return

    gen.detached = True
    gen.assistant_md = None
    gen.thinking_label = None
    gen.thinking_md = None
    gen.thinking_expansion = None
    gen.thinking_code = None
    gen.tool_col = None
    gen.wrapper = None
    gen.pending_tools.clear()

    if gen.tts_active:
        try:
            state.tts_service.stop()
        except Exception:
            logger.debug("Failed to stop TTS during detach", exc_info=True)
        gen.tts_active = False

    logger.info("Detached generation for thread %s after %s", gen.thread_id, reason)


def _handle_ui_runtime_error(
    gen: GenerationState,
    state: AppState,
    exc: Exception,
    reason: str,
) -> bool:
    """Detach the generation when NiceGUI raises after the client is gone."""
    if not isinstance(exc, RuntimeError):
        return False
    message = str(exc).strip().lower()
    dead_client = "client" in message and "deleted" in message
    missing_slot = "current slot cannot be determined" in message or "slot stack" in message
    if not dead_client and not missing_slot:
        return False
    _detach_generation(gen, state, reason)
    return True


def _ui_handle_client_deleted(handle: Any) -> bool:
    try:
        client = getattr(handle, "client", None)
    except RuntimeError as exc:
        message = str(exc).lower()
        if "client" in message and "deleted" in message:
            return True
        raise
    if client is None:
        return False
    return bool(getattr(client, "_deleted", False))


def _detach_if_ui_client_deleted(
    gen: GenerationState,
    state: AppState,
    reason: str,
) -> bool:
    if gen.detached:
        return False
    for handle in (
        gen.wrapper,
        gen.assistant_md,
        gen.thinking_label,
        gen.thinking_md,
        gen.thinking_expansion,
        gen.thinking_code,
        gen.tool_col,
    ):
        if handle is not None and _ui_handle_client_deleted(handle):
            _detach_generation(gen, state, reason)
            return True
    return False


def _generation_is_terminal(gen: GenerationState) -> bool:
    return str(getattr(gen, "status", "")).lower() in {"done", "error", "stopped"}


def _render_thinking_collapse(gen: GenerationState) -> bool:
    """Persist streamed reasoning as a collapsed disclosure.

    The live markdown is intentionally removed only after the expansion exists.
    This avoids a UX failure where reasoning tokens briefly stream, then vanish
    if the preferred tool column cannot accept a new child.
    """
    if not gen.thinking_text or gen.thinking_collapsed or gen.thinking_expansion:
        return bool(gen.thinking_expansion)

    container = gen.tool_col or gen.wrapper
    if not container:
        return False

    with container:
        gen.thinking_expansion = ui.expansion(
            "\U0001f4ad Thinking", icon="psychology"
        ).classes("w-full")
        with gen.thinking_expansion:
            gen.thinking_code = ui.code(
                gen.thinking_text.strip()[:8_000]
            ).classes("w-full text-xs")

    if gen.thinking_md:
        gen.thinking_md.delete()
        gen.thinking_md = None
    gen.thinking_collapsed = True
    return True


def _drop_terminal_active_generation(thread_id: str | None) -> bool:
    if not thread_id:
        return False
    existing = _active_generations.get(thread_id)
    if existing is None or not _generation_is_terminal(existing):
        return False
    _active_generations.pop(thread_id, None)
    logger.info("Cleared terminal active generation for thread %s", thread_id)
    return True


# ── Type alias for the callback bundle ───────────────────────────────
def _set_expansion_title(expansion: Any, title: str, icon: str) -> None:
    """Update a NiceGUI expansion title without relying on a public setter."""

    try:
        expansion._props["icon"] = icon
        expansion._props["label"] = title
        expansion._text = title
        expansion.update()
    except Exception:
        logger.debug("Failed to update expansion title", exc_info=True)


def _live_tool_group(gen: GenerationState, tool_name: str) -> dict[str, Any] | None:
    if gen.detached or not gen.tool_col:
        return None
    canonical_name = canonical_tool_name(tool_name)
    display_name = "Browser activity" if is_browser_tool_name(canonical_name) else canonical_name
    group = gen.pending_tools.get(display_name)
    if isinstance(group, dict):
        return group
    with gen.tool_col:
        activity = _tool_activity_line(display_name)
        if activity:
            ui.label(activity).classes("text-xs text-grey-6 q-ml-sm")
        exp = ui.expansion(f"Running {display_name} - 0 calls", icon="hourglass_empty").classes("w-full")
    group = {
        "name": display_name,
        "expansion": exp,
        "count": 0,
        "done": 0,
        "pending": [],
    }
    gen.pending_tools[display_name] = group
    return group


def _tool_activity_line(display_name: str) -> str:
    name = str(display_name or "").lower()
    if name == "developer":
        return "Developer is working in the code workspace."
    if name == "shell":
        return "Running a workspace command."
    if name == "browser activity":
        return "Browser automation is stepping through the page."
    if name in {"file", "document"}:
        return "Reading local context."
    return ""


def _render_inline_interrupt_notice(
    gen: GenerationState,
    state: AppState,
    p: P,
    cb: _Callbacks,
) -> bool:
    """Render a redundant approval surface inside the active chat.

    The modal dialog is still the primary approval UI. Developer Studio also
    gets this inline banner so an approval remains visible if the dialog's
    client/slot is unavailable during a long run.
    """

    if not state.active_developer_workspace_id or state.thread_id != gen.thread_id:
        return False
    target_container = getattr(p, "developer_approval_container", None) or p.chat_container
    if gen.detached or target_container is None:
        return False
    try:
        def _resume_inline(approved: bool) -> None:
            try:
                if p.interrupt_dlg is not None:
                    p.interrupt_dlg.close()
            except Exception:
                logger.debug("Inline approval could not close modal dialog", exc_info=True)
            asyncio.create_task(resume_after_interrupt(approved, state=state, p=p, cb=cb))

        try:
            target_container.clear()
        except Exception:
            logger.debug("Developer approval container clear failed", exc_info=True)
        with target_container:
            with ui.card().classes("w-full q-pa-md").style(
                "border-radius: 8px; border: 1px solid rgba(245,158,11,0.55); "
                "background: rgba(120, 53, 15, 0.20);"
            ):
                with ui.row().classes("w-full items-center justify-between gap-3"):
                    with ui.row().classes("items-center gap-2"):
                        ui.icon("warning_amber", color="amber", size="sm")
                        ui.label("Developer approval pending").classes("text-sm text-amber-3")
                    with ui.row().classes("items-center gap-2"):
                        ui.button(
                            "Deny",
                            on_click=lambda: _resume_inline(False),
                        ).props("flat dense no-caps")
                        ui.button(
                            "Approve",
                            on_click=lambda: _resume_inline(True),
                        ).props("color=amber dense no-caps")
                data = gen.interrupt_data
                items = data if isinstance(data, list) else [data]
                first = items[0] if items else {}
                if isinstance(first, dict):
                    desc = first.get("description") or first.get("label") or first.get("tool") or "Approval required"
                    ui.label(str(desc)).classes("text-xs text-grey-5")
        if p.chat_scroll:
            p.chat_scroll.scroll_to(percent=1.0)
        return True
    except Exception as exc:
        _handle_ui_runtime_error(gen, state, exc, "developer inline interrupt render")
        logger.debug("Developer inline interrupt render failed", exc_info=True)
        return False


def _add_live_tool_pending(gen: GenerationState, tool_name: str) -> None:
    group = _live_tool_group(gen, tool_name)
    if not group:
        return
    group["count"] += 1
    call_no = group["count"]
    exp = group["expansion"]
    with exp:
        row = ui.column().classes("w-full gap-1 q-ml-sm")
        with row:
            label = ui.label(f"#{call_no} running...").classes("text-xs text-grey-6")
    group["pending"].append({"row": row, "label": label, "call_no": call_no})
    unit = "step" if group["name"] == "Browser activity" else "call"
    suffix = unit if group["count"] == 1 else f"{unit}s"
    _set_expansion_title(exp, f"Running {group['name']} - {group['count']} {suffix}", "hourglass_empty")


def _finish_live_tool_result(gen: GenerationState, tool_name: str, content: str) -> bool:
    canonical_name = canonical_tool_name(tool_name)
    display_name = "Browser activity" if is_browser_tool_name(canonical_name) else canonical_name
    group = gen.pending_tools.get(display_name)
    if not isinstance(group, dict):
        return False
    pending = group.get("pending") or []
    item = pending.pop(0) if pending else None
    group["done"] = int(group.get("done") or 0) + 1
    row = item.get("row") if item else None
    label = item.get("label") if item else None
    call_no = item.get("call_no") if item else group["done"]
    failed = tool_result_failed(content)
    if row is None:
        with group["expansion"]:
            row = ui.column().classes("w-full gap-1 q-ml-sm")
    with row:
        if label is not None:
            label.set_text(f"#{call_no} {'failed' if failed else 'complete'}")
            label.classes(f"text-xs {'text-negative' if failed else 'text-positive'}")
        else:
            ui.label(f"#{call_no} {'failed' if failed else 'complete'}").classes(
                f"text-xs {'text-negative' if failed else 'text-positive'}"
            )
        display = display_tool_content(content)
        if display:
            ui.code(display).classes("w-full text-xs")
    group["failed"] = bool(group.get("failed")) or failed
    icon = "error" if group.get("failed") else ("check_circle" if group["done"] >= group["count"] else "hourglass_empty")
    prefix = "Failed" if group.get("failed") else ("Done" if icon == "check_circle" else "Running")
    _set_expansion_title(
        group["expansion"],
        f"{prefix} {group['name']} - {group['done']}/{group['count']} complete",
        icon,
    )
    return True


class _Callbacks:
    """Container for all cross-cutting callbacks.

    Every field *must* be set before ``send_message`` is called.
    """

    __slots__ = (
        "rebuild_main",
        "rebuild_thread_list",
        "show_interrupt",
        "update_token_counter",
        "add_chat_message",
        "mark_chat_message_rendered",
        "render_text_with_embeds",
        "refresh_chat_messages",
    )

    def __init__(self) -> None:
        for s in self.__slots__:
            setattr(self, s, None)


Callbacks = _Callbacks  # public alias for wiring in app


# ══════════════════════════════════════════════════════════════════════
# CONSUME GENERATION
# ══════════════════════════════════════════════════════════════════════

async def consume_generation(
    gen: GenerationState,
    state: AppState,
    p: P,
    cb: _Callbacks,
) -> None:
    """Drain *gen.q* and update the UI.  Runs as an ``asyncio.Task``.

    When *gen.detached* is True (user switched to another thread), all UI
    writes are skipped but event accumulation continues so the response is
    ready when the user switches back.
    """
    from agent import get_agent_graph, repair_orphaned_tool_calls
    from buddy.events import BuddyEventType, emit_buddy_event
    from langchain_core.messages import AIMessage
    from ui.helpers import (
        attach_thinking_to_message,
        load_thread_messages,
        persist_detached_thread_media,
        persist_thread_media_state,
    )

    _stopped_shown = False
    _drain_deadline = 0.0
    _last_buddy_token_at = 0.0
    _consume_started = time.perf_counter()
    _stream_updates = 0

    generation_scope_id = f"{gen.thread_id}:{id(gen)}"
    realtime_chunker = None
    realtime_speech_queue = None
    if gen.voice_mode and state.voice_coordinator.transport == "realtime":
        from voice.realtime_presenter import RealtimeSpeechChunker, RealtimeSpeechQueue

        realtime_chunker = RealtimeSpeechChunker()
        realtime_speech_queue = RealtimeSpeechQueue()
    realtime_last_voice_attempt_at = _consume_started
    realtime_last_queue_flush_at = -999.0
    realtime_tool_events_since_cue = 0
    realtime_tool_done_since_cue = 0

    def _mark_realtime_voice_attempt(origin: str, text: str) -> None:
        nonlocal realtime_last_voice_attempt_at
        if gen.voice_mode and state.voice_coordinator.transport == "realtime" and str(text or "").strip():
            realtime_last_voice_attempt_at = time.perf_counter()
            _voice_diag("realtime_voice_attempt", origin=origin, chars=len(str(text or "").strip()))

    def _speak_realtime_text(text: str, *, origin: str = "final") -> bool:
        from voice.realtime_client import send_realtime_function_output_js, send_realtime_run_event_js

        if (
            origin == "final"
            and realtime_speech_queue is not None
        ):
            decision_basis = gen.accumulated if str(gen.accumulated or "").strip() else text
            decision = realtime_speech_queue.final_decision(decision_basis)
            if not decision.get("speak"):
                gen.realtime_stream_finalized = True
                _voice_diag("realtime_final_suppressed_after_stream", **decision)
                if gen.realtime_tool_call_id and not gen.realtime_tool_output_sent:
                    gen.realtime_tool_output_sent = True
                    code = send_realtime_function_output_js(
                        call_id=gen.realtime_tool_call_id,
                        output={
                            "status": "completed",
                            "speakable": "",
                            "worker": "thoth",
                        },
                        thread_id=gen.thread_id,
                        generation_id=generation_scope_id,
                        silent=True,
                    )
                    sent = run_realtime_client_js(p, code, context="realtime_function_output_silent_after_stream")
                    _voice_diag("realtime_function_output_silent_after_stream", sent=sent)
                return False
        if origin == "final" and (gen.realtime_tool_call_id or gen.realtime_forced_consult):
            if gen.realtime_tool_output_sent:
                return False
            gen.realtime_tool_output_sent = True
            if gen.realtime_tool_call_id:
                code = send_realtime_function_output_js(
                    call_id=gen.realtime_tool_call_id,
                    output={
                        "status": "completed",
                        "speakable": text,
                        "worker": "thoth",
                    },
                    thread_id=gen.thread_id,
                    generation_id=generation_scope_id,
                    silent=False,
                )
                context = "realtime_function_output"
            else:
                if gen.realtime_streamed_speech_chunks > 0:
                    gen.realtime_stream_finalized = True
                    return False
                code = send_realtime_run_event_js(
                    text,
                    origin="forced_consult_result",
                    thread_id=gen.thread_id,
                    generation_id=generation_scope_id,
                )
                context = "realtime_forced_consult_result"
            sent = run_realtime_client_js(p, code, context=context)
            if sent:
                _mark_realtime_voice_attempt(origin, text)
            return sent
        sent = run_realtime_client_js(
            p,
            send_realtime_run_event_js(
                text,
                origin=origin,
                thread_id=gen.thread_id,
                generation_id=generation_scope_id,
            ),
            context="realtime_run_event",
        )
        if sent:
            _mark_realtime_voice_attempt(origin, text)
        return sent

    def _speak_realtime_stream_chunk(text: str) -> None:
        clean = str(text or "").strip()
        if not clean:
            return
        if realtime_speech_queue is not None:
            playback_active = bool(getattr(state.voice_coordinator, "playback_active", False))
            queued = realtime_speech_queue.offer_stream_chunk(clean, playback_active=playback_active)
            if not queued:
                _voice_diag(
                    "realtime_stream_chunk_queued",
                    chunks=gen.realtime_streamed_speech_chunks,
                    chars=len(clean),
                    playback_active=playback_active,
                    spoken_stream_chars=realtime_speech_queue.spoken_stream_chars,
                    coalesced_chars=len(realtime_speech_queue.coalesced_stream_text),
                )
                return
            clean = queued
        if _speak_realtime_text(clean, origin="stream_chunk"):
            gen.realtime_streamed_speech_chunks += 1
            if gen.realtime_streamed_speech_chunks == 1:
                state.voice_coordinator.mark_realtime_latency("first_speakable_chunk")
                state.voice_coordinator.mark_realtime_latency("first_substantive_audio_requested")
            _voice_diag(
                "realtime_stream_chunk_spoken",
                chunks=gen.realtime_streamed_speech_chunks,
                chars=len(clean),
            )

    def _flush_realtime_speech_queue(reason: str) -> bool:
        nonlocal realtime_last_queue_flush_at
        if realtime_speech_queue is None or not gen.tts_active:
            return False
        if bool(getattr(state.voice_coordinator, "playback_active", False)):
            return False
        now = time.perf_counter()
        if now - realtime_last_queue_flush_at < 1.2:
            return False
        chunk = realtime_speech_queue.flush_queued(playback_active=False)
        if not chunk:
            return False
        realtime_last_queue_flush_at = now
        if _speak_realtime_text(chunk, origin="stream_chunk"):
            gen.realtime_streamed_speech_chunks += 1
            _voice_diag(
                "realtime_stream_chunk_flushed",
                reason=reason,
                chunks=gen.realtime_streamed_speech_chunks,
                chars=len(chunk),
                spoken_stream_chars=realtime_speech_queue.spoken_stream_chars,
                coalesced_chars=len(realtime_speech_queue.coalesced_stream_text),
            )
            return True
        return False

    voice_output = VoiceOutputController.for_generation(
        voice_mode=gen.voice_mode,
        transport=state.voice_coordinator.transport,
        tts_service=state.tts_service,
        realtime_speaker=_speak_realtime_text,
        now=time.perf_counter,
    )

    def _generation_elapsed() -> float:
        return max(0.0, time.perf_counter() - _consume_started)

    def _voice_diag(stage: str, **extra: Any) -> None:
        if not gen.voice_mode and state.voice_coordinator.transport != "realtime":
            return
        active_gen = _active_generations.get(state.thread_id)
        snapshot = state.voice_coordinator.diagnostic_snapshot()
        snapshot.update({
            "stage": stage,
            "voice_enabled": state.voice_enabled,
            "voice_input_mode": state.voice_input_mode,
            "thread_id": state.thread_id,
            "generation_thread_id": gen.thread_id,
            "generation_id": generation_scope_id,
            "generation_status": gen.status,
            "generation_voice_mode": gen.voice_mode,
            "generation_tts_active": gen.tts_active,
            "active_generation_status": getattr(active_gen, "status", None),
            **extra,
        })
        logger.info("voice.realtime.pipeline %s", snapshot)

    def _maybe_speak_realtime_progress(reason: str) -> bool:
        nonlocal realtime_tool_events_since_cue, realtime_tool_done_since_cue
        if not (gen.voice_mode and state.voice_coordinator.transport == "realtime"):
            return False
        if _stopped_shown or gen.status != "streaming":
            return False
        if bool(getattr(state.voice_coordinator, "playback_active", False)):
            return False
        if str(getattr(state.voice_coordinator, "state", "")).lower() in {"user_speaking", "speaking"}:
            return False

        elapsed = _generation_elapsed()
        quiet_for = time.perf_counter() - realtime_last_voice_attempt_at
        if quiet_for < 9.0:
            return False

        cue = None
        cue_reason = ""
        if realtime_tool_done_since_cue >= 2 and quiet_for >= 9.0:
            cue = results_found_cue()
            cue_reason = "tool_results_ready"
        elif realtime_tool_events_since_cue > 0 and quiet_for >= 12.0:
            cue = tool_progress_cue()
            cue_reason = "tools_still_running"
        elif not gen.first_content:
            cue = heard_cue() if elapsed < 3.0 else (thinking_cue() if elapsed < 10.0 else long_running_cue())
            cue_reason = "awaiting_first_content"
        elif quiet_for >= 18.0:
            cue = long_running_cue()
            cue_reason = "quiet_after_content"

        if cue is None:
            return False
        spoke = voice_output.speak_cue(cue, generation_elapsed=elapsed)
        _voice_diag(
            "realtime_cue_spoken" if spoke else "realtime_cue_suppressed",
            reason=reason,
            cue_type=str(getattr(cue.type, "value", cue.type)),
            cue_reason=cue_reason,
            quiet_for=round(quiet_for, 3),
            elapsed=round(elapsed, 3),
        )
        _voice_diag(
            "realtime_silence_watchdog_tick",
            reason=reason,
            cue_reason=cue_reason,
            spoke=spoke,
            quiet_for=round(quiet_for, 3),
            elapsed=round(elapsed, 3),
            tool_events_since_cue=realtime_tool_events_since_cue,
            tool_done_since_cue=realtime_tool_done_since_cue,
        )
        if spoke and cue.type.value in {"tool_start", "tool_progress"}:
            realtime_tool_events_since_cue = 0
            realtime_tool_done_since_cue = 0
        return spoke

    def _set_realtime_generation_state(state_name: str, *, detail: str = "") -> None:
        if gen.voice_mode and state.voice_coordinator.transport == "realtime":
            state.voice_coordinator.set_realtime_state(state_name, detail=detail)
            _voice_diag(f"coordinator_state:{state_name}", detail=detail)

    def _buddy(event_type: BuddyEventType, label: str = "", **payload: Any) -> None:
        if label:
            payload["label"] = label
        try:
            emit_buddy_event(event_type, source="ui.streaming", payload={"thread_id": gen.thread_id, **payload})
        except Exception:
            logger.debug("Buddy event emit failed", exc_info=True)

    _buddy(BuddyEventType.GENERATION_STARTED, "Thinking")
    _set_realtime_generation_state("thinking", detail="generation_started")
    _voice_diag("generation_consumer_started")
    if gen.voice_mode and state.voice_coordinator.transport == "realtime":
        state.voice_coordinator.set_active_thoth_generation(generation_scope_id)

    try:
      while True:
        # ── Stop handling ────────────────────────────────────────────
        if gen.stop_event.is_set() and not _stopped_shown:
            _stopped_shown = True
            gen.status = "stopped"
            _set_realtime_generation_state("stopped", detail="stop_event")
            gen.accumulated += "\n\n\u23f9\ufe0f *[Stopped]*"
            _drain_deadline = asyncio.get_event_loop().time() + 30
            if not gen.detached:
                try:
                    if gen.thinking_label:
                        gen.thinking_label.delete()
                        gen.thinking_label = None
                    if gen.thinking_md:
                        gen.thinking_md.delete()
                        gen.thinking_md = None
                    if gen.assistant_md:
                        gen.assistant_md.set_visibility(True)
                        gen.assistant_md.set_content(_format_assistant_markdown(gen.accumulated))
                except Exception as exc:
                    _handle_ui_runtime_error(gen, state, exc, "stop-handling UI cleanup")
                    logger.debug("Stop-handling UI cleanup failed", exc_info=True)
            if gen.tts_active:
                state.tts_service.stop()

        if _stopped_shown and asyncio.get_event_loop().time() > _drain_deadline:
            break

        try:
            event = gen.q.get_nowait()
        except queue.Empty:
            if gen.voice_mode and state.voice_coordinator.transport == "realtime":
                if not _flush_realtime_speech_queue("queue_empty"):
                    _maybe_speak_realtime_progress("queue_empty")
            if (
                not gen.first_content
                and not _stopped_shown
                and gen.status == "streaming"
                and state.voice_coordinator.transport != "realtime"
            ):
                elapsed = _generation_elapsed()
                voice_output.speak_cue(
                    heard_cue() if elapsed < 3.0 else (thinking_cue() if elapsed < 10.0 else long_running_cue()),
                    generation_elapsed=elapsed,
                )
            await asyncio.sleep(0.05)
            continue

        if event is None:
            break

        if _stopped_shown:
            continue

        event_type, payload = event
        _break_loop = False
        if not gen.detached:
            _detach_if_ui_client_deleted(gen, state, f"before {event_type} UI update")

        # ── First content transition ─────────────────────────────────
        if (
            not gen.first_content
            and (
                (event_type == "token" and str(payload or "").strip())
                or (event_type == "done" and str(payload or "").strip())
            )
        ):
            gen.first_content = True
            if not gen.detached:
                try:
                    if gen.thinking_label:
                        gen.thinking_label.delete()
                        gen.thinking_label = None
                    if gen.thinking_text and not gen.thinking_collapsed:
                        _render_thinking_collapse(gen)
                    if gen.assistant_md:
                        gen.assistant_md.set_visibility(True)
                except Exception as exc:
                    _handle_ui_runtime_error(gen, state, exc, "first-content transition")
                    logger.error("Error rendering thinking collapse", exc_info=True)

        if event_type == "error":
            _buddy(BuddyEventType.GENERATION_ERROR, "Error", error=str(payload)[:500])
            _voice_diag("generation_event:error", payload_preview=str(payload)[:500])
            voice_output.speak_cue(error_cue(), generation_elapsed=_generation_elapsed())
            _set_realtime_generation_state("error", detail=str(payload)[:500])
            gen.status = "error"
            gen.error = payload
            gen.accumulated = f"\u26a0\ufe0f An error occurred: {payload}"
            if not gen.detached:
                try:
                    if gen.thinking_label:
                        gen.thinking_label.delete()
                    if gen.assistant_md:
                        gen.assistant_md.set_visibility(True)
                        gen.assistant_md.set_content(_format_assistant_markdown(gen.accumulated))
                except Exception as exc:
                    _handle_ui_runtime_error(gen, state, exc, "error-event cleanup")
                    logger.debug("Error-event UI cleanup failed", exc_info=True)
            try:
                configurable = gen.config.get("configurable", {}) if isinstance(gen.config, dict) else {}
                if configurable.get("runtime_mode") == "agent":
                    try:
                        repair_orphaned_tool_calls(gen.enabled_tools, gen.config)
                    except Exception:
                        logger.debug("repair_orphaned_tool_calls failed", exc_info=True)
                if configurable.get("runtime_mode") == "agent":
                    _agent = get_agent_graph(
                        gen.enabled_tools,
                        model_override=configurable.get("model_override"),
                    )
                    _agent.update_state(
                        gen.config,
                        {"messages": [AIMessage(content=gen.accumulated)]},
                    )
                else:
                    from threads import append_checkpoint_messages

                    append_checkpoint_messages(gen.thread_id, [AIMessage(content=gen.accumulated)])
            except Exception:
                logger.debug("Failed to persist error to checkpoint", exc_info=True)
            _break_loop = True

        elif event_type == "tool_call":
            _buddy(BuddyEventType.TOOL_STARTED, "Using a tool", tool=str(payload))
            _voice_diag("generation_event:tool_call", tool=str(payload))
            if gen.voice_mode and state.voice_coordinator.transport == "realtime":
                realtime_tool_events_since_cue += 1
                state.voice_coordinator.mark_realtime_latency("thoth_tool_started")
            _set_realtime_generation_state("thoth_tool_running", detail=str(payload))
            spoke_tool_cue = voice_output.speak_cue(tool_start_cue(str(payload)), generation_elapsed=_generation_elapsed())
            if spoke_tool_cue:
                realtime_tool_events_since_cue = 0
            _voice_diag(
                "realtime_cue_spoken" if spoke_tool_cue else "realtime_cue_suppressed",
                cue_type="tool_start",
                reason="tool_call",
                spoke=spoke_tool_cue,
                tool_events_since_cue=realtime_tool_events_since_cue,
            )
            _grouped_tool_call = False
            if not gen.detached and gen.tool_col:
                try:
                    _add_live_tool_pending(gen, str(payload))
                    _grouped_tool_call = True
                except Exception as exc:
                    _handle_ui_runtime_error(gen, state, exc, "tool-call group")
                    logger.debug("Tool-call group creation failed", exc_info=True)
            if not _grouped_tool_call and not gen.detached and gen.tool_col:
                try:
                    with gen.tool_col:
                        _pending_exp = ui.expansion(
                            f"\U0001f504 {payload}\u2026", icon="hourglass_empty"
                        ).classes("w-full")
                        # FIFO queue per tool name - parallel calls to the
                        # same tool must each get their own pending slot so
                        # later tool_done events can still match them.
                        gen.pending_tools.setdefault(payload, []).append(_pending_exp)
                except Exception as exc:
                    _handle_ui_runtime_error(gen, state, exc, "tool-call expansion")
                    logger.debug("Tool-call expansion creation failed", exc_info=True)

        elif event_type == "tool_done":
            _buddy(BuddyEventType.TOOL_FINISHED, "Tool finished")
            _voice_diag("generation_event:tool_done")
            if gen.voice_mode and state.voice_coordinator.transport == "realtime":
                realtime_tool_done_since_cue += 1
                state.voice_coordinator.mark_realtime_latency("thoth_tool_done")
            await _handle_tool_done(gen, state, p, payload, cb)

        elif event_type == "summarizing":
            _buddy(BuddyEventType.THINKING, "Summarizing")
            if not gen.detached and gen.wrapper:
                try:
                    if gen.thinking_label:
                        gen.thinking_label.delete()
                        gen.thinking_label = None
                    with gen.wrapper:
                        gen.thinking_label = ui.html(
                            '<span class="thoth-typing" style="font-size:0.9rem; opacity:0.6;">'
                            '\U0001f4dd Summarizing conversation history<span class="dots">'
                            '<span>.</span><span>.</span><span>.</span></span></span>',
                            sanitize=False,
                        )
                except Exception as exc:
                    _handle_ui_runtime_error(gen, state, exc, "summarizing label")
                    logger.debug("Summarizing label update failed", exc_info=True)

        elif event_type == "thinking":
            pass  # spinner already visible

        elif event_type == "thinking_token":
            _buddy(BuddyEventType.THINKING, "Reasoning")
            gen.thinking_text += payload
            _stream_updates += 1
            if not gen.detached:
                try:
                    if gen.thinking_label:
                        gen.thinking_label.delete()
                        gen.thinking_label = None
                    if gen.thinking_collapsed:
                        if gen.thinking_code:
                            gen.thinking_code.set_content(gen.thinking_text.strip()[:8_000])
                    elif gen.thinking_md is None and gen.wrapper:
                        with gen.wrapper:
                            gen.thinking_md = ui.markdown(
                                "", extras=["code-friendly", "fenced-code-blocks"]
                            ).classes("thoth-msg w-full").style(
                                "opacity: 0.55; font-size: 0.88rem; font-style: italic;"
                            )
                    if gen.thinking_md and not gen.thinking_collapsed:
                        gen.thinking_md.set_content(gen.thinking_text)
                except Exception as exc:
                    _handle_ui_runtime_error(gen, state, exc, "thinking-token rendering")
                    logger.debug("Thinking-token rendering failed", exc_info=True)

        elif event_type == "token":
            if not str(payload or "").strip() and not str(gen.accumulated or "").strip():
                continue
            if not gen.first_content:
                _voice_diag("generation_event:first_token")
                if gen.voice_mode and state.voice_coordinator.transport == "realtime":
                    state.voice_coordinator.mark_realtime_latency("first_token")
            _stream_updates += 1
            _now = asyncio.get_event_loop().time()
            if _now - _last_buddy_token_at > 0.8:
                _last_buddy_token_at = _now
                _buddy(BuddyEventType.TOKEN, "Writing")
            gen.accumulated += payload
            if not gen.detached and gen.assistant_md:
                try:
                    gen.assistant_md.set_content(_format_assistant_markdown(gen.accumulated))
                except Exception as exc:
                    _handle_ui_runtime_error(gen, state, exc, "token content update")
                    logger.debug("Token content update failed", exc_info=True)

            realtime_tts = gen.tts_active and state.voice_coordinator.transport == "realtime"
            if realtime_tts:
                if "```" in payload:
                    gen.tts_in_code = not gen.tts_in_code
                if not gen.tts_in_code:
                    if realtime_chunker is not None:
                        for chunk in realtime_chunker.push(str(payload or "")):
                            _speak_realtime_stream_chunk(chunk)
                    else:
                        gen.tts_buffer += payload

            # Streaming TTS (only when attached)
            if gen.tts_active and not realtime_tts:
                if "```" in payload:
                    gen.tts_in_code = not gen.tts_in_code
                if not gen.tts_in_code:
                    gen.tts_buffer += payload
                    sentences = SENTENCE_SPLIT.split(gen.tts_buffer)
                    if len(sentences) > 1:
                        for s in sentences[:-1]:
                            if gen.tts_spoken >= MAX_STREAM_SENTENCES:
                                break
                            speakable = make_speakable_response(
                                s,
                                allow_long=gen.tts_allow_long,
                                reason="assistant_stream_sentence",
                            )
                            state.tts_service.speak_streaming(speakable.text)
                            gen.tts_spoken += 1
                            if gen.tts_spoken >= MAX_STREAM_SENTENCES:
                                state.tts_service.flush_streaming(
                                    "The full response is shown in the app."
                                )
                                gen.tts_active = False
                        gen.tts_buffer = sentences[-1]

        elif event_type == "interrupt":
            _buddy(BuddyEventType.APPROVAL_NEEDED, "Approval pending")
            _voice_diag("generation_event:interrupt")
            voice_output.speak_cue(approval_needed_cue(), generation_elapsed=_generation_elapsed())
            _set_realtime_generation_state("waiting_for_approval", detail="interrupt")
            gen.interrupt_data = payload
            state.pending_interrupt = payload
            gen.status = "interrupted"
            rendered_inline = _render_inline_interrupt_notice(gen, state, p, cb)
            if not gen.detached and not _ui_handle_client_deleted(p.interrupt_dlg):
                try:
                    cb.show_interrupt(payload)
                    gen.interrupt_rendered = True
                except RuntimeError as exc:
                    _handle_ui_runtime_error(gen, state, exc, "interrupt dialog render")
                    logger.warning("Approval is pending but the interrupt dialog could not be rendered", exc_info=True)
            elif rendered_inline:
                gen.interrupt_rendered = True
            _break_loop = True

        elif event_type == "done":
            _buddy(BuddyEventType.GENERATION_DONE, "Done")
            _voice_diag("generation_event:done", final_chars=len(str(payload or "")))
            gen.accumulated = payload
            if not gen.detached:
                try:
                    if gen.thinking_label:
                        gen.thinking_label.delete()
                        gen.thinking_label = None
                    if gen.thinking_text and not gen.thinking_collapsed:
                        _render_thinking_collapse(gen)
                    elif gen.thinking_md and gen.thinking_expansion:
                        gen.thinking_md.delete()
                        gen.thinking_md = None
                    if gen.assistant_md:
                        gen.assistant_md.set_visibility(True)
                        gen.assistant_md.set_content(_format_assistant_markdown(gen.accumulated))
                except Exception as exc:
                    _handle_ui_runtime_error(gen, state, exc, "done-event finalization")
                    logger.debug("Done-event UI finalization failed", exc_info=True)

        if _break_loop:
            break

    except Exception:
        logger.error("Error in generation consumer", exc_info=True)

    # ── Finalise ─────────────────────────────────────────────────────
    if gen.status == "streaming":
        gen.status = "done"
    _voice_diag("generation_finalizing")

    try:
        if not gen.detached:
            _detach_if_ui_client_deleted(gen, state, "post-stream finalization")

        if not gen.detached:
            if gen.tts_active and gen.status == "done":
                if (
                    state.voice_coordinator.transport == "realtime"
                    and realtime_chunker is not None
                    and not gen.realtime_stream_finalized
                ):
                    for chunk in realtime_chunker.flush():
                        _speak_realtime_stream_chunk(chunk)
                    _flush_realtime_speech_queue("finalizing")
                speakable = make_speakable_response(
                    gen.accumulated if state.voice_coordinator.transport == "realtime" else gen.tts_buffer,
                    allow_long=gen.tts_allow_long or state.voice_coordinator.transport == "realtime",
                    reason="assistant_stream_final",
                )
                if state.voice_coordinator.transport == "realtime":
                    _set_realtime_generation_state("speaking", detail="final_speech_requested")
                    if realtime_speech_queue is not None:
                        _voice_diag(
                            "realtime_final_speech_decision",
                            **realtime_speech_queue.final_decision(gen.accumulated),
                        )
                spoke = voice_output.speak_final(speakable.text)
                _voice_diag(
                    "final_speech_requested",
                    spoke=spoke,
                    speakable_chars=len(speakable.text),
                    speakable_reason=speakable.reason,
                    speakable_truncated=speakable.truncated,
                    speakable_fallback=speakable.fallback,
                )
                if state.voice_coordinator.transport == "realtime" and not spoke:
                    _set_realtime_generation_state("listening", detail="final_speech_not_started")
            elif state.voice_coordinator.transport == "realtime" and gen.voice_mode and gen.status == "done":
                _set_realtime_generation_state("listening", detail="generation_done_no_speech")

            # Re-render normal chat content via render_text_with_embeds so code
            # blocks get proper highlight.js and mermaid diagrams render. Keep
            # Developer Studio's streamed markdown node in place: deleting and
            # recreating the final message is the path most likely to detach a
            # still-active Developer workspace and collapse the inspector.
            if gen.accumulated and not state.active_developer_workspace_id:
                if gen.assistant_md:
                    try:
                        gen.assistant_md.delete()
                    except (ValueError, RuntimeError) as exc:
                        _handle_ui_runtime_error(gen, state, exc, "assistant markdown delete")
                        logger.debug("assistant_md already removed from DOM", exc_info=True)
                    gen.assistant_md = None
                if gen.wrapper:
                    try:
                        with gen.wrapper:
                            cb.render_text_with_embeds(gen.accumulated)
                    except RuntimeError as exc:
                        _handle_ui_runtime_error(gen, state, exc, "final response render")
                        logger.debug("Client deleted during render_text_with_embeds", exc_info=True)

            try:
                ui.run_javascript(
                    "if (window.thothHighlightCodeBlocks) { window.thothHighlightCodeBlocks(); } "
                    "else { setTimeout(function() { document.querySelectorAll('pre code').forEach(function(el) { if (!el.closest('.thoth-live-stream')) hljs.highlightElement(el); }); }, 80); }"
                )
                ui.run_javascript(
                    "setTimeout(function() {"
                    "  var nodes = Array.from(document.querySelectorAll('pre.mermaid')).filter(function(node) { return !node.closest('.thoth-live-stream'); });"
                    "  mermaid.run({nodes: nodes, suppressErrors: true});"
                    "}, 150);"
                )
            except RuntimeError as exc:
                # Syntax highlighting / mermaid enhancement is optional. Treat
                # failures here as cosmetic; marking the generation detached
                # after the final row has rendered causes detached recovery to
                # append the persisted assistant message as a duplicate.
                logger.debug("JS runtime unavailable for hljs/mermaid: %s", exc)
    except Exception:
        logger.error("Error in post-stream finalization", exc_info=True)

    # Store assistant message
    _has_final_output = bool(
        str(gen.accumulated or "").strip()
        or gen.tool_results
        or gen.chart_data
        or gen.captured_images
        or gen.captured_videos
    )
    _persisted_detached = False
    if _has_final_output:
        await _capture_balanced_browser_screenshot(gen, state)
        visible_content = gen.accumulated if str(gen.accumulated or "").strip() else ""
        a_msg: dict = {"role": "assistant", "content": visible_content}
        attach_thinking_to_message(a_msg, gen.thinking_text)
        if gen.tool_results:
            a_msg["tool_results"] = gen.tool_results
        if gen.chart_data:
            a_msg["charts"] = gen.chart_data
        if gen.captured_images:
            a_msg["images"] = gen.captured_images
            if gen.captured_images_persist:
                a_msg["_media_persist_flags"] = list(gen.captured_images_persist)
                if any(gen.captured_images_persist):
                    a_msg["_media_persist"] = True
        if gen.captured_videos:
            a_msg["videos"] = gen.captured_videos
        if state.thread_id == gen.thread_id:
            state.messages.append(a_msg)
            persist_thread_media_state(state.thread_id, state.messages)
            state.cache_active_messages()
            if not gen.detached and cb.mark_chat_message_rendered:
                try:
                    cb.mark_chat_message_rendered(a_msg)
                except Exception:
                    logger.debug("Final assistant render-state mark failed", exc_info=True)
            _persisted_detached = True
        else:
            _has_detached_media = bool(gen.captured_images or gen.captured_videos)
            if _has_detached_media:
                _persisted_detached = persist_detached_thread_media(
                    gen.thread_id,
                    gen.accumulated,
                    images=gen.captured_images,
                    image_persist_flags=gen.captured_images_persist,
                    videos=gen.captured_videos,
                )
            # Detached run for a different thread wrote straight to the
            # checkpoint; mark its cached message list stale so the next
            # select re-reads.  Do not reload the active thread here: an
            # active but UI-detached run may have newer optimistic user
            # messages in memory than the checkpoint has flushed yet.
            state.mark_thread_dirty(gen.thread_id)
            if _has_detached_media and not _persisted_detached:
                logger.warning(
                    "Detached generation for thread %s completed but media sidecar persistence did not attach anything",
                    gen.thread_id,
                )

    # Cleanup
    queued_voice_controls = list(getattr(gen, "voice_control_queue", []) or [])
    if _active_generations.get(gen.thread_id) is gen:
        _active_generations.pop(gen.thread_id, None)

    # Update UI if this is still the active thread
    if state.thread_id == gen.thread_id:
        if not gen.detached:
            _detach_if_ui_client_deleted(gen, state, "final active-thread UI update")
        # If we detached mid-stream but the client came back and is
        # still looking at this thread, the in-DOM element handles are
        # stale. Refresh only the transcript container; rebuilding the
        # whole main area causes visible flashes and can race optimistic
        # user-message rendering.
        if gen.detached:
            try:
                cb.refresh_chat_messages()
                logger.info(
                    "Detached finalize refreshed transcript without full main rebuild for thread %s",
                    gen.thread_id,
                )
            except RuntimeError as exc:
                _handle_ui_runtime_error(gen, state, exc, "scoped transcript refresh")
                logger.info(
                    "Detached finalize could not refresh transcript because UI client is detached for thread %s",
                    gen.thread_id,
                )
            except Exception:
                logger.debug("Scoped transcript refresh failed", exc_info=True)
        if p.stop_btn and not _ui_handle_client_deleted(p.stop_btn):
            try:
                p.stop_btn.props('icon=stop')
                p.stop_btn.disable()
            except Exception:
                logger.debug("stop_btn reset failed", exc_info=True)
        if state.voice_enabled and not (state.tts_service and state.tts_service.enabled):
            state.voice_coordinator.unmute()
        if gen.interrupt_data:
            _set_realtime_generation_state("waiting_for_approval", detail="approval_pending")
            state.pending_interrupt = gen.interrupt_data
            if not gen.interrupt_rendered:
                rendered_inline = _render_inline_interrupt_notice(gen, state, p, cb)
            else:
                rendered_inline = True
            if gen.detached or _ui_handle_client_deleted(p.interrupt_dlg) or gen.interrupt_rendered:
                logger.info(
                    "Approval pending for thread %s; dialog render skipped because UI client is detached or approval was already rendered",
                    gen.thread_id,
                )
            else:
                try:
                    cb.show_interrupt(gen.interrupt_data)
                    gen.interrupt_rendered = True
                except RuntimeError as exc:
                    _handle_ui_runtime_error(gen, state, exc, "interrupt dialog render")
                    logger.warning("Approval is pending but the interrupt dialog could not be rendered", exc_info=True)
            if rendered_inline:
                gen.interrupt_rendered = True
        try:
            cb.update_token_counter()
        except RuntimeError as exc:
            _handle_ui_runtime_error(gen, state, exc, "token counter update")
            logger.debug("Client deleted during update_token_counter", exc_info=True)

    try:
        cb.rebuild_thread_list()
    except RuntimeError as exc:
        _handle_ui_runtime_error(gen, state, exc, "thread list rebuild")
        logger.debug("Client deleted during rebuild_thread_list", exc_info=True)

    if state.active_developer_workspace_id and state.thread_id == gen.thread_id:
        try:
            from developer.inspector_snapshot import request_snapshot_refresh

            request_snapshot_refresh(
                state.active_developer_workspace_id,
                gen.thread_id,
                reason="developer_generation_final",
            )
        except Exception:
            logger.debug("Developer inspector final refresh scheduling failed", exc_info=True)

    if queued_voice_controls and state.thread_id == gen.thread_id and not gen.detached:
        follow_ups = [
            str(item.get("text") or "").strip()
            for item in queued_voice_controls
            if isinstance(item, dict) and str(item.get("text") or "").strip()
        ]
        if follow_ups:
            follow_up_text = "\n".join(follow_ups)
            logger.info(
                "voice.realtime.pipeline %s",
                {
                    "stage": "queued_voice_controls_dispatch",
                    "thread_id": gen.thread_id,
                    "count": len(follow_ups),
                    "text_chars": len(follow_up_text),
                },
            )
            asyncio.create_task(
                send_message(
                    follow_up_text,
                    state=state,
                    p=p,
                    cb=cb,
                    voice_mode=gen.voice_mode,
                )
            )

    log_ui_perf(
        "streaming.consume_generation",
        (time.perf_counter() - _consume_started) * 1000.0,
        rows=_stream_updates,
        thread_id=gen.thread_id,
        status=gen.status,
        detached=gen.detached,
        thinking_chars=len(gen.thinking_text or ""),
        answer_chars=len(gen.accumulated or ""),
    )


# ── Tool-done sub-handler ────────────────────────────────────────────

async def _handle_tool_done(
    gen: GenerationState,
    state: AppState,
    p: P,
    payload: Any,
    cb: _Callbacks,
) -> None:
    tool_name = payload["name"] if isinstance(payload, dict) else payload
    raw_tool_name = payload.get("raw_name", tool_name) if isinstance(payload, dict) else tool_name
    tool_content = payload.get("content", "") if isinstance(payload, dict) else ""
    if not isinstance(tool_content, str):
        # content may be a list of content-blocks (e.g. Anthropic cache_control format)
        tool_content = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in tool_content) if isinstance(tool_content, list) else str(tool_content)

    if not gen.detached:
        _detach_if_ui_client_deleted(gen, state, f"tool {tool_name} result rendering")

    if state.active_developer_workspace_id and state.thread_id == gen.thread_id:
        try:
            from developer.inspector_snapshot import request_snapshot_refresh

            request_snapshot_refresh(
                state.active_developer_workspace_id,
                gen.thread_id,
                reason=f"tool_done:{tool_name}",
                debounce=0.8,
            )
        except Exception:
            logger.debug("Developer inspector tool refresh scheduling failed", exc_info=True)

    # Chart detection
    if tool_content and tool_content.startswith("__CHART__:"):
        marker_end = tool_content.find("\n\n", 10)
        if marker_end == -1:
            fig_json = tool_content[10:]
            display_text = "Chart created"
        else:
            fig_json = tool_content[10:marker_end]
            display_text = tool_content[marker_end + 2:]
        gen.chart_data.append(fig_json)
        if not gen.detached and gen.tool_col:
            try:
                import plotly.io as _pio
                fig = _pio.from_json(fig_json)
                with gen.tool_col:
                    ui.plotly(fig).classes("w-full")
            except Exception as exc:
                _handle_ui_runtime_error(gen, state, exc, "chart rendering")
                logger.debug("Chart rendering failed", exc_info=True)
        tool_content = display_text

    # Image marker detection (plugins / rich returns)
    if tool_content and tool_content.startswith("__IMAGE__:"):
        marker_end = tool_content.find("\n\n", 10)
        if marker_end == -1:
            _img_b64 = tool_content[10:]
            display_text = "Image generated"
        else:
            _img_b64 = tool_content[10:marker_end]
            display_text = tool_content[marker_end + 2:]
        gen.captured_images.append(_img_b64)
        gen.captured_images_persist.append(True)  # Tier 1: plugin-generated
        _spill_excess_captured_images(gen)
        if not gen.detached and gen.tool_col:
            try:
                with gen.tool_col:
                    render_image_with_save(_img_b64)
            except Exception as exc:
                _handle_ui_runtime_error(gen, state, exc, "image marker rendering")
                logger.debug("Image marker rendering failed", exc_info=True)
        tool_content = display_text

    # HTML marker detection (plugins / rich returns)
    if tool_content and tool_content.startswith("__HTML__:"):
        marker_end = tool_content.find("\n\n", 9)
        if marker_end == -1:
            _html_content = tool_content[9:]
            display_text = ""
        else:
            _html_content = tool_content[9:marker_end]
            display_text = tool_content[marker_end + 2:]
        if not gen.detached and gen.tool_col:
            try:
                with gen.tool_col:
                    ui.html(_html_content).classes("w-full")
            except Exception as exc:
                _handle_ui_runtime_error(gen, state, exc, "HTML widget rendering")
                logger.debug("HTML widget rendering failed", exc_info=True)
        tool_content = display_text

    # Update the pending expansion or create a new one
    _grouped_live_result = False
    failed = tool_result_failed(tool_content)
    if not gen.detached and gen.tool_col:
        try:
            _grouped_live_result = _finish_live_tool_result(gen, tool_name, tool_content)
        except Exception as exc:
            _handle_ui_runtime_error(gen, state, exc, "tool group update")
            logger.debug("Tool group update failed for %s", tool_name, exc_info=True)
    if not _grouped_live_result and not gen.detached and gen.tool_col:
        try:
            _queue = gen.pending_tools.get(tool_name)
            matched_exp = _queue.pop(0) if _queue else None
            if _queue is not None and not _queue:
                gen.pending_tools.pop(tool_name, None)
            if matched_exp:
                matched_exp._props["icon"] = "error" if failed else "check_circle"
                matched_exp._text = f"{'Failed' if failed else 'Done'} {tool_name}"
                matched_exp.update()
                if tool_content:
                    display = tool_content[:5_000]
                    if len(tool_content) > 5_000:
                        display += "\n\n\u2026 (truncated)"
                    with matched_exp:
                        ui.code(display).classes("w-full text-xs")
            else:
                with gen.tool_col:
                    with ui.expansion(
                        f"{'Failed' if failed else 'Done'} {tool_name}",
                        icon="error" if failed else "check_circle",
                    ).classes("w-full"):
                        if tool_content:
                            display = tool_content[:5_000]
                            if len(tool_content) > 5_000:
                                display += "\n\n\u2026 (truncated)"
                            ui.code(display).classes("w-full text-xs")
        except Exception as exc:
            _handle_ui_runtime_error(gen, state, exc, "tool expansion update")
            logger.debug("Tool expansion update failed for %s", tool_name, exc_info=True)

    gen.tool_results.append({"name": tool_name, "content": tool_content, "error": failed})

    # Vision capture
    if raw_tool_name in ("analyze_image",) or tool_name == "\U0001f441\ufe0f Vision":
        vsvc = state.vision_service
        if vsvc and vsvc.last_capture:
            b64_img = _b64.b64encode(vsvc.last_capture).decode("ascii")
            gen.captured_images.append(b64_img)
            gen.captured_images_persist.append(False)  # Tier 2: vision capture
            _spill_excess_captured_images(gen)
            if not gen.detached and gen.tool_col:
                try:
                    with gen.tool_col:
                        render_image_with_save(b64_img)
                except Exception as exc:
                    _handle_ui_runtime_error(gen, state, exc, "vision capture rendering")
                    logger.debug("Vision capture rendering failed", exc_info=True)
            vsvc.last_capture = None

    # Browser screenshot thumbnail - run on a thread so the Playwright
    # round-trip (200-800 ms) does not block the asyncio loop; otherwise
    # socket.io pings stall and the client is considered disconnected.
    if raw_tool_name.startswith("browser_"):
        gen.browser_step_count += 1

    # Filesystem image display (workspace_read_file on image files)
    if raw_tool_name in ("workspace_read_file",):
        try:
            from tools.filesystem_tool import get_and_clear_displayed_image
            _fs_img = get_and_clear_displayed_image()
            if _fs_img:
                gen.captured_images.append(_fs_img["b64"])
                gen.captured_images_persist.append(False)  # Tier 2: filesystem display
                _spill_excess_captured_images(gen)
                if not gen.detached and gen.tool_col:
                    with gen.tool_col:
                        render_image_with_save(_fs_img["b64"])
        except Exception as exc:
            _handle_ui_runtime_error(gen, state, exc, "filesystem image rendering")
            logger.debug("Filesystem image rendering failed", exc_info=True)

    # Image generation display (generate_image / edit_image)
    if raw_tool_name in ("generate_image", "edit_image"):
        try:
            from tools.image_gen_tool import get_and_clear_last_image
            _gen_img = get_and_clear_last_image()
            if _gen_img:
                gen.captured_images.append(_gen_img)
                gen.captured_images_persist.append(True)  # Tier 1: generated image
                _spill_excess_captured_images(gen)
                if not gen.detached and gen.tool_col:
                    with gen.tool_col:
                        render_image_with_save(_gen_img)
        except Exception as exc:
            _handle_ui_runtime_error(gen, state, exc, "image generation rendering")
            logger.debug("Image generation rendering failed", exc_info=True)

    # Video generation display (generate_video / animate_image)
    if raw_tool_name in ("generate_video", "animate_image"):
        try:
            from tools.video_gen_tool import get_and_clear_last_video
            _gen_vid = get_and_clear_last_video()
            if _gen_vid and _gen_vid.get("path"):
                gen.captured_videos.append(_gen_vid)
                gen.captured_videos_persist.append(True)
                if not gen.detached and gen.tool_col:
                    with gen.tool_col:
                        from ui.render import render_video_with_save
                        render_video_with_save(_gen_vid["path"])
        except Exception as exc:
            _handle_ui_runtime_error(gen, state, exc, "video generation rendering")
            logger.debug("Video generation rendering failed", exc_info=True)


async def _capture_balanced_browser_screenshot(gen: GenerationState, state: AppState) -> None:
    """Capture one final browser screenshot for Balanced browser traces."""

    if gen.browser_step_count <= 0:
        return
    try:
        from tools.browser_tool import get_session_manager as _get_bsm

        _bsm = _get_bsm()
        if not _bsm.has_active_session():
            return
        _bs = _bsm.get_session()
        _screenshot_bytes = await run.io_bound(_bs.take_screenshot, gen.thread_id)
        if not _screenshot_bytes:
            return
        _b64_ss = _b64.b64encode(_screenshot_bytes).decode("ascii")
        gen.captured_images.append(_b64_ss)
        gen.captured_images_persist.append(False)
        _spill_excess_captured_images(gen)
        if not gen.detached and gen.tool_col:
            with gen.tool_col:
                ui.label("Final browser screenshot").classes("text-xs text-grey-6")
                render_image_with_save(
                    _b64_ss,
                    extra_style="border: 1px solid #333; margin-top: 4px;",
                )
    except Exception as exc:
        _handle_ui_runtime_error(gen, state, exc, "balanced browser screenshot capture")
        logger.debug("Balanced browser screenshot capture failed", exc_info=True)


# ══════════════════════════════════════════════════════════════════════
# SEND MESSAGE
# ══════════════════════════════════════════════════════════════════════

async def send_message(
    text: str,
    *,
    state: AppState,
    p: P,
    cb: _Callbacks,
    voice_mode: bool = False,
) -> None:
    """Send a message and stream the agent response."""
    from agent import stream_agent, repair_orphaned_tool_calls, recursion_limit_for_mode
    from threads import _save_thread_meta
    from tools import registry as tool_registry
    from ui.helpers import (
        materialize_chat_attachments,
        process_attached_files,
        persist_thread_media_state,
        wrap_attachment_context,
    )

    if not text.strip() and not p.pending_files:
        return
    if state.thread_id and state.thread_id in _active_generations:
        if not _drop_terminal_active_generation(state.thread_id):
            from voice.agent_bridge import VoiceAgentBridge

            bridge = VoiceAgentBridge(
                send_message=lambda *args, **kwargs: None,
                active_generation=lambda: _active_generations.get(state.thread_id),
            )
            control = bridge.control_active_run(text)
            if control.get("handled"):
                speakable = str(control.get("speakable") or "")
                if speakable:
                    logger.info(
                        "voice.realtime.pipeline %s",
                        {
                            "stage": "active_run_control_notification",
                            "thread_id": state.thread_id,
                            "control": control.get("control"),
                            "speakable": speakable,
                        },
                    )
                    try:
                        ui.notify(speakable, type="info", close_button=True, timeout=4000)
                    except Exception:
                        logger.debug("Active-run control notification failed", exc_info=True)
                if state.voice_coordinator.transport == "realtime":
                    state.voice_coordinator.set_realtime_state(
                        "listening",
                        detail=f"active_run_control:{control.get('control')}",
                    )
            return

    # Ensure a thread exists
    if state.thread_id is None:
        tid = uuid.uuid4().hex[:12]
        name = text[:50]
        _save_thread_meta(tid, name)
        state.thread_id = tid
        state.thread_name = name
        state.messages = []
        state.show_onboarding = False
        # ``immediate=True`` so ``p.chat_container`` is ready for the
        # user-message render and streaming placeholder below (no
        # skeleton-then-hydrate race on first-send).
        try:
            cb.rebuild_main(immediate=True)
        except TypeError:
            # Older callback signature without kwargs - fall back.
            cb.rebuild_main()
        cb.rebuild_thread_list()

    gen_thread_id = state.thread_id

    if text.strip().startswith("/") and not p.pending_files:
        from slash_commands import dispatch_text_command

        enabled_tool_names = [t.name for t in tool_registry.get_enabled_tools()]
        command_response = await run.io_bound(
            lambda: dispatch_text_command(
                gen_thread_id,
                text,
                enabled_tool_names=enabled_tool_names,
            )
        )
        if command_response is not None:
            user_msg = {"role": "user", "content": text}
            assistant_msg = {"role": "assistant", "content": command_response}
            state.messages.extend([user_msg, assistant_msg])
            persist_thread_media_state(state.thread_id, state.messages)
            state.cache_active_messages()
            cb.add_chat_message(user_msg)
            cb.add_chat_message(assistant_msg)
            _save_thread_meta(state.thread_id, state.thread_name)
            try:
                cb.rebuild_main()
            except TypeError:
                pass
            return

    # ── Snapshot & clear attached files immediately ──────────────────
    _files_snapshot: list[dict] = list(p.pending_files)
    file_names: list[str] = [f["name"] for f in _files_snapshot]
    if _files_snapshot:
        p.pending_files.clear()
        if p.file_chips_row:
            p.file_chips_row.clear()

    # ── Extract image thumbnails so the user message renders NOW ─────
    import pathlib as _plib
    user_images: list[str] = []
    for f in _files_snapshot:
        if _plib.Path(f["name"]).suffix.lower() in IMAGE_EXTENSIONS:
            user_images.append(_b64.b64encode(f["data"]).decode("ascii"))

    # ── Build display and render user message immediately ────────────
    if file_names:
        badge_text = ", ".join(f"\U0001f4ce {n}" for n in file_names)
        display_content = f"{badge_text}\n\n{text}" if text.strip() else badge_text
    else:
        display_content = text

    user_msg: dict = {"role": "user", "content": display_content}
    if user_images:
        user_msg["images"] = user_images
    state.messages.append(user_msg)
    persist_thread_media_state(state.thread_id, state.messages)
    state.cache_active_messages()
    cb.add_chat_message(user_msg)

    auth_block_message = None
    try:
        from models import get_current_model

        selected_model = state.thread_model_override or get_current_model()
        auth_block_message = await run.io_bound(lambda: _codex_auth_block_message(selected_model))
    except Exception:
        logger.debug("Codex auth preflight skipped unexpectedly", exc_info=True)
    if auth_block_message:
        assistant_msg = {"role": "assistant", "content": auth_block_message}
        state.messages.append(assistant_msg)
        persist_thread_media_state(state.thread_id, state.messages)
        state.cache_active_messages()
        cb.add_chat_message(assistant_msg)
        _save_thread_meta(state.thread_id, state.thread_name)
        return

    if getattr(state, "active_developer_workspace_id", None) and not _files_snapshot:
        try:
            from developer.agent_context import maybe_answer_workspace_identity

            direct_answer = await run.io_bound(
                maybe_answer_workspace_identity,
                state.active_developer_workspace_id,
                text,
            )
        except Exception:
            direct_answer = None
            logger.debug("Failed to build Developer Studio direct identity answer", exc_info=True)
        if direct_answer:
            assistant_msg = {"role": "assistant", "content": direct_answer}
            state.messages.append(assistant_msg)
            persist_thread_media_state(state.thread_id, state.messages)
            state.cache_active_messages()
            cb.add_chat_message(assistant_msg)
            if state.thread_name and (
                state.thread_name.startswith("Thread ")
                or state.thread_name.startswith("\U0001f4bb Thread ")
            ):
                state.thread_name = f"\U0001f4bb {display_content[:50]}"
                _save_thread_meta(state.thread_id, state.thread_name)
                cb.rebuild_thread_list()
                if p.chat_header_label:
                    p.chat_header_label.set_text(f"\U0001f4ac {state.thread_name}")
            else:
                _save_thread_meta(state.thread_id, state.thread_name)
            return

    # Process attached files (slow - vision analysis etc.)
    file_context = ""
    file_warnings: list[str] = []
    if _files_snapshot:
        _has_images = any(
            _plib.Path(f["name"]).suffix.lower() in IMAGE_EXTENSIONS
            for f in _files_snapshot
        )
        _processing_note = None
        if _has_images and p.chat_container:
            with p.chat_container:
                _processing_note = ui.html(
                    '<div style="opacity:0.6; font-size:0.85rem; padding:4px 0 4px 48px;">'
                    '\U0001f50d Analyzing image<span class="dots">'
                    '<span>.</span><span>.</span><span>.</span></span></div>',
                    sanitize=False,
                )
            if p.chat_scroll:
                p.chat_scroll.scroll_to(percent=1.0)

        _effective_model = state.thread_model_override or None
        try:
            await run.io_bound(materialize_chat_attachments, _files_snapshot)
            file_context, _, file_warnings = await run.io_bound(
                process_attached_files, _files_snapshot, state.vision_service,
                state.attached_data_cache, _effective_model,
            )
        except Exception as exc:
            logger.error("process_attached_files failed: %s", exc, exc_info=True)
            ui.notify(f"Failed to process attached files: {exc}", type="negative",
                      position="top", close_button=True, timeout=10000)
        for fw in file_warnings:
            ui.notify(fw, type="warning", position="top", close_button=True, timeout=8000)
        if _processing_note:
            try:
                _processing_note.delete()
            except Exception:
                logger.debug("Processing note cleanup failed", exc_info=True)

    # ── Build agent input ────────────────────────────────────────────
    agent_input = text
    if file_context:
        marked_file_context = wrap_attachment_context(file_context)
        agent_input = f"{marked_file_context}\n\n{text}" if text else marked_file_context
    developer_context = ""
    if getattr(state, "active_developer_workspace_id", None):
        try:
            from developer.agent_context import build_developer_agent_context

            developer_context = await run.io_bound(
                build_developer_agent_context,
                state.active_developer_workspace_id,
            )
        except Exception:
            logger.debug("Failed to build Developer Studio context", exc_info=True)
    logger.info("send_message: file_names=%s, file_context_len=%d, agent_input_len=%d",
                file_names, len(file_context), len(agent_input))

    # Auto-name thread
    if state.thread_name and (
        state.thread_name.startswith("Thread ")
        or state.thread_name.startswith("\U0001f4bb Thread ")
    ):
        state.thread_name = f"\U0001f4bb {display_content[:50]}"
        _save_thread_meta(state.thread_id, state.thread_name)
        cb.rebuild_thread_list()
        if p.chat_header_label:
            p.chat_header_label.set_text(f"\U0001f4ac {state.thread_name}")
    else:
        _save_thread_meta(state.thread_id, state.thread_name)

    # ── Build config ─────────────────────────────────────────────────
    # Sync attachment cache to chart tool so it can read attached data files
    from tools.chart_tool import _attachment_cache as _chart_cache
    _chart_cache.clear()
    _chart_cache.update(state.attached_data_cache)

    # Sync pasted/attached images to image gen tool for edit_image
    from tools.image_gen_tool import _image_cache as _img_cache
    import tools.image_gen_tool as _igt_mod
    # Preserve all cached images within the same thread (so images from
    # earlier turns stay available); clear on thread switch.
    _same_thread = (_igt_mod._image_cache_thread_id == gen_thread_id)
    if not _same_thread:
        _img_cache.clear()
    _igt_mod._image_cache_thread_id = gen_thread_id
    # Layer new attachments on top (overwrite if same filename re-attached)
    for f in _files_snapshot:
        if _plib.Path(f["name"]).suffix.lower() in IMAGE_EXTENSIONS:
            _img_cache[f["name"]] = f["data"]

    _thread_mo = state.thread_model_override or ""
    is_developer = bool(getattr(state, "active_developer_workspace_id", None))
    is_designer = bool(getattr(state, "active_designer_project", None))
    runtime_surface = "developer" if is_developer else "designer" if is_designer else "normal_chat"
    runtime_mode = "agent" if is_developer or is_designer else "auto"
    if runtime_mode == "agent":
        from models import get_current_model

        if not await _agent_ready_forced_surface(_thread_mo or get_current_model(), runtime_surface):
            return
    recursion_limit = recursion_limit_for_mode(is_developer=is_developer)
    config = {
        "configurable": {
            "thread_id": gen_thread_id,
            "runtime_surface": runtime_surface,
            "runtime_mode": runtime_mode,
            **({"model_override": _thread_mo} if _thread_mo else {}),
            **({"developer_workspace_id": state.active_developer_workspace_id} if getattr(state, "active_developer_workspace_id", None) else {}),
            **({"developer_context": developer_context} if developer_context else {}),
        },
        "recursion_limit": recursion_limit,
    }
    logger.info(
        "send_message: thread=%s developer=%s recursion_limit=%d",
        gen_thread_id[:8] if gen_thread_id else "?",
        is_developer,
        recursion_limit,
    )
    enabled_tools = [t.name for t in tool_registry.get_enabled_tools()]
    if getattr(state, "active_developer_workspace_id", None):
        from developer.profile import effective_tool_names
        enabled_tools = effective_tool_names(enabled_tools)

    if voice_mode:
        agent_input = (
            "[Voice input \u2014 the user is speaking to you via microphone "
            "and your response will be read aloud. Keep responses concise "
            "and conversational.]\n\n" + agent_input
        )

    # ── Create generation state ──────────────────────────────────────
    stop_ev = threading.Event()
    gen = GenerationState(
        thread_id=gen_thread_id,
        q=queue.Queue(),
        stop_event=stop_ev,
        config=config,
        enabled_tools=enabled_tools,
        voice_mode=voice_mode,
        tts_active=voice_mode and (state.tts_service.enabled or state.voice_coordinator.transport == "realtime"),
        tts_allow_long=voice_mode and user_requested_read_aloud(text),
    )
    if voice_mode and state.voice_coordinator.transport == "realtime":
        realtime_call = state.voice_coordinator.consume_realtime_tool_call()
        if realtime_call:
            gen.realtime_tool_call_id = str(realtime_call.get("call_id") or "")
            gen.realtime_tool_name = str(realtime_call.get("name") or "")
            gen.realtime_consult_request = str(realtime_call.get("request") or "")
            gen.realtime_forced_consult = gen.realtime_tool_name == "forced_consult"
    _active_generations[gen_thread_id] = gen
    if voice_mode or state.voice_coordinator.transport == "realtime":
        snapshot = state.voice_coordinator.diagnostic_snapshot()
        snapshot.update({
            "stage": "generation_created",
            "voice_enabled": state.voice_enabled,
            "voice_input_mode": state.voice_input_mode,
            "thread_id": gen_thread_id,
            "generation_status": gen.status,
            "generation_voice_mode": gen.voice_mode,
            "generation_tts_active": gen.tts_active,
            "input_chars": len(str(text or "")),
        })
        logger.info("voice.realtime.pipeline %s", snapshot)

    if p.stop_btn:
        p.stop_btn.enable()

    # ── Prepare assistant message placeholder ────────────────────────
    _build_assistant_placeholder(gen, p)

    if p.chat_scroll:
        p.chat_scroll.scroll_to(percent=1.0)

    # ── Start producer thread ────────────────────────────────────────
    def _sync_stream():
        first_event_logged = False
        try:
            if voice_mode or state.voice_coordinator.transport == "realtime":
                logger.info(
                    "voice.realtime.pipeline %s",
                    {
                        "stage": "producer_thread_started",
                        "thread_id": gen_thread_id,
                        "voice_mode": voice_mode,
                        "transport": state.voice_coordinator.transport,
                    },
                )
            for ev in stream_agent(agent_input, enabled_tools, config,
                                   stop_event=stop_ev):
                if stop_ev.is_set():
                    break
                if not first_event_logged and (voice_mode or state.voice_coordinator.transport == "realtime"):
                    first_event_logged = True
                    logger.info(
                        "voice.realtime.pipeline %s",
                        {
                            "stage": "producer_first_event",
                            "thread_id": gen_thread_id,
                            "event_type": ev[0] if isinstance(ev, tuple) and ev else type(ev).__name__,
                        },
                    )
                gen.q.put(ev)
        except Exception as exc:
            if voice_mode or state.voice_coordinator.transport == "realtime":
                logger.exception(
                    "voice.realtime.pipeline %s",
                    {"stage": "producer_thread_error", "thread_id": gen_thread_id},
                )
            if not stop_ev.is_set():
                gen.q.put(("error", str(exc)))
        finally:
            if stop_ev.is_set():
                try:
                    repair_orphaned_tool_calls(enabled_tools, config)
                except Exception:
                    logger.debug("repair_orphaned_tool_calls failed in stream finally", exc_info=True)
            try:
                from skills_activation import consume_one_shot_skills

                consume_one_shot_skills(gen_thread_id)
            except Exception:
                logger.debug("consume_one_shot_skills failed in stream finally", exc_info=True)
            if voice_mode or state.voice_coordinator.transport == "realtime":
                logger.info(
                    "voice.realtime.pipeline %s",
                    {
                        "stage": "producer_thread_finished",
                        "thread_id": gen_thread_id,
                        "stop_requested": stop_ev.is_set(),
                        "first_event_logged": first_event_logged,
                    },
                )
            gen.q.put(None)

    threading.Thread(target=_sync_stream, daemon=True).start()

    asyncio.create_task(consume_generation(gen, state, p, cb))
    cb.rebuild_thread_list()


# ══════════════════════════════════════════════════════════════════════
# RESUME AFTER INTERRUPT
# ══════════════════════════════════════════════════════════════════════

async def resume_after_interrupt(
    approved: bool,
    *,
    state: AppState,
    p: P,
    cb: _Callbacks,
) -> None:
    from agent import resume_stream_agent, repair_orphaned_tool_calls, recursion_limit_for_mode
    from buddy.events import BuddyEventType, emit_buddy_event
    from tools import registry as tool_registry

    pending = state.pending_interrupt
    interrupt_ids = None
    if isinstance(pending, list) and len(pending) > 1:
        interrupt_ids = [
            item.get("__interrupt_id")
            for item in pending
            if isinstance(item, dict) and item.get("__interrupt_id")
    ]
    state.pending_interrupt = None
    try:
        approval_container = getattr(p, "developer_approval_container", None)
        if approval_container is not None:
            approval_container.clear()
    except Exception:
        logger.debug("Developer approval container clear after resume failed", exc_info=True)

    gen_thread_id = state.thread_id
    try:
        emit_buddy_event(
            BuddyEventType.APPROVAL_APPROVED if approved else BuddyEventType.APPROVAL_DENIED,
            source="ui.streaming",
            payload={"thread_id": gen_thread_id, "label": "Approved" if approved else "Denied"},
        )
    except Exception:
        logger.debug("Buddy approval resolution event failed", exc_info=True)

    _thread_mo = state.thread_model_override or ""
    developer_context = ""
    if getattr(state, "active_developer_workspace_id", None):
        try:
            from developer.agent_context import build_developer_agent_context

            developer_context = await run.io_bound(
                build_developer_agent_context,
                state.active_developer_workspace_id,
            )
        except Exception:
            logger.debug("Failed to build Developer Studio context for resume", exc_info=True)
    is_developer = bool(getattr(state, "active_developer_workspace_id", None))
    is_designer = bool(getattr(state, "active_designer_project", None))
    runtime_surface = "developer" if is_developer else "designer" if is_designer else "approval"
    from models import get_current_model

    if not await _agent_ready_forced_surface(_thread_mo or get_current_model(), runtime_surface):
        return
    recursion_limit = recursion_limit_for_mode(is_developer=is_developer)
    config = {
        "configurable": {
            "thread_id": gen_thread_id,
            "runtime_surface": runtime_surface,
            "runtime_mode": "agent",
            **({"model_override": _thread_mo} if _thread_mo else {}),
            **({"developer_workspace_id": state.active_developer_workspace_id} if getattr(state, "active_developer_workspace_id", None) else {}),
            **({"developer_context": developer_context} if developer_context else {}),
        },
        "recursion_limit": recursion_limit,
    }
    logger.info(
        "resume_after_interrupt: thread=%s developer=%s recursion_limit=%d",
        gen_thread_id[:8] if gen_thread_id else "?",
        is_developer,
        recursion_limit,
    )
    enabled_tools = [t.name for t in tool_registry.get_enabled_tools()]
    if getattr(state, "active_developer_workspace_id", None):
        from developer.profile import effective_tool_names
        enabled_tools = effective_tool_names(enabled_tools)

    stop_ev = threading.Event()
    gen = GenerationState(
        thread_id=gen_thread_id,
        q=queue.Queue(),
        stop_event=stop_ev,
        config=config,
        enabled_tools=enabled_tools,
    )
    _active_generations[gen_thread_id] = gen

    if p.stop_btn:
        p.stop_btn.enable()

    _build_assistant_placeholder(gen, p)

    if p.chat_scroll:
        p.chat_scroll.scroll_to(percent=1.0)

    # ── Start producer thread ────────────────────────────────────────
    def _sync_resume():
        try:
            for ev in resume_stream_agent(
                enabled_tools, config, approved,
                interrupt_ids=interrupt_ids,
                stop_event=stop_ev,
            ):
                if stop_ev.is_set():
                    break
                gen.q.put(ev)
        except Exception as exc:
            if not stop_ev.is_set():
                gen.q.put(("error", str(exc)))
        finally:
            if stop_ev.is_set():
                try:
                    repair_orphaned_tool_calls(enabled_tools, config)
                except Exception:
                    logger.debug("repair_orphaned_tool_calls failed in resume finally", exc_info=True)
            gen.q.put(None)

    threading.Thread(target=_sync_resume, daemon=True).start()

    asyncio.create_task(consume_generation(gen, state, p, cb))
    cb.rebuild_thread_list()


# ══════════════════════════════════════════════════════════════════════
# INTERRUPT DIALOG
# ══════════════════════════════════════════════════════════════════════

def build_interrupt_dialog(
    state: AppState,
    p: P,
    cb: _Callbacks,
) -> None:
    """Create the interrupt dialog and its show/close helpers.

    Attaches ``p.interrupt_dlg`` and returns the ``show_interrupt`` function
    for use as a callback.
    """
    p.interrupt_dlg = ui.dialog().props("persistent")

    def show_interrupt(data) -> None:
        p.interrupt_dlg.clear()
        items = data if isinstance(data, list) else [data]
        plural = len(items) > 1
        with p.interrupt_dlg, ui.card().classes("q-pa-none").style(
            "width: 520px; max-width: 90vw; border-radius: 16px; overflow: hidden;"
            "background: #1a1a2e; border: 1px solid #2a2a4a;"
        ):
            with ui.row().classes("w-full items-center q-pa-md").style(
                "background: linear-gradient(135deg, #2d1b00 0%, #1a1a2e 100%);"
                "border-bottom: 1px solid #3d2e00;"
            ):
                ui.icon("warning_amber", size="28px", color="amber")
                title = f"Confirm {len(items)} Actions" if plural else "Confirmation Required"
                ui.label(title).style(
                    "font-size: 1.15rem; font-weight: 700; color: #f0c040; margin-left: 8px;"
                )
            with ui.column().classes("w-full q-pa-lg"):
                subtitle = (
                    "The agent wants to perform the following actions:"
                    if plural else
                    "The agent wants to perform the following action:"
                )
                ui.label(subtitle).style(
                    "font-size: 0.85rem; color: #8888aa; margin-bottom: 8px;"
                )
                with ui.element("div").style(
                    "background: #12121e; border: 1px solid #2a2a4a; border-radius: 10px;"
                    "padding: 14px 16px; max-height: 260px; overflow-y: auto;"
                    "font-size: 0.9rem; color: #d0d0e0; line-height: 1.6;"
                    "word-wrap: break-word; white-space: pre-wrap;"
                ):
                    for i, item in enumerate(items):
                        desc = item.get("description", "Unknown action") if isinstance(item, dict) else str(item)
                        if plural:
                            ui.markdown(f"**{i + 1}.** {desc}", extras=['code-friendly', 'fenced-code-blocks', 'tables'])
                        else:
                            ui.markdown(desc, extras=['code-friendly', 'fenced-code-blocks', 'tables'])
            btn_label = f"Approve All ({len(items)})" if plural else "Approve"
            with ui.row().classes("w-full justify-end q-pa-md gap-3").style(
                "border-top: 1px solid #2a2a4a;"
            ):
                ui.button("Deny", on_click=lambda: _close_interrupt(False)).props(
                    "flat no-caps"
                ).style(
                    "color: #ff6b6b; font-weight: 600; font-size: 0.9rem;"
                    "padding: 8px 24px; border-radius: 8px;"
                )
                ui.button(btn_label, on_click=lambda: _close_interrupt(True)).props(
                    "unelevated no-caps"
                ).style(
                    "background: #2d8a4e; color: white; font-weight: 600;"
                    "font-size: 0.9rem; padding: 8px 28px; border-radius: 8px;"
                )
        p.interrupt_dlg.open()

    def _close_interrupt(approved: bool) -> None:
        p.interrupt_dlg.close()
        asyncio.create_task(resume_after_interrupt(approved, state=state, p=p, cb=cb))

    return show_interrupt  # type: ignore[return-value]


# ══════════════════════════════════════════════════════════════════════
# HELPER – assistant placeholder
# ══════════════════════════════════════════════════════════════════════

def _build_assistant_placeholder(gen: GenerationState, p: P) -> None:
    """Build the streaming assistant message placeholder in the chat."""
    from identity import get_assistant_name
    from ui.status_bar import get_bot_avatar_html
    _ph_avatar = get_bot_avatar_html()
    _ph_name = get_assistant_name()
    with p.chat_container:
        with ui.element("div").classes("thoth-msg-row"):
            ui.html(
                f'<div class="thoth-avatar thoth-avatar-bot">{_ph_avatar}</div>',
                sanitize=False,
            )
            with ui.column().classes("thoth-msg-body gap-1") as _wrapper:
                ui.html(
                    '<div class="thoth-msg-header">'
                    f'<span class="thoth-msg-name">{_ph_name}</span>'
                    f'<span class="thoth-msg-stamp">{datetime.now().strftime("%H:%M")}</span>'
                    '</div>',
                    sanitize=False,
                )
                gen.tool_col = ui.column().classes("w-full gap-1")
                gen.thinking_label = ui.html(
                    '<span class="thoth-typing" style="font-size:0.9rem; opacity:0.6;">'
                    f'{_ph_name} is thinking<span class="dots">'
                    '<span>.</span><span>.</span><span>.</span></span></span>',
                    sanitize=False,
                )
                gen.assistant_md = ui.markdown(
                    "",
                    extras=['code-friendly', 'fenced-code-blocks', 'tables'],
                ).classes("thoth-msg thoth-live-stream w-full")
                gen.assistant_md.set_visibility(False)
                gen.wrapper = _wrapper
