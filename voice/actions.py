from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal


VoiceInputMode = Literal["talk", "dictate"]
ActiveRunControlKind = Literal["none", "status", "cancel", "follow_up", "steer"]

logger = logging.getLogger(__name__)


@dataclass
class ActiveVoiceSurfaceBinding:
    """Per-client binding between voice input and the visible composer surface."""

    surface: str
    thread_id: str
    get_composer_text: Callable[[], str]
    set_composer_text: Callable[[str], None]
    send_talk_text: Callable[..., Any]
    active: bool = True

    def is_current(self, thread_id: str | None) -> bool:
        return self.active and bool(self.thread_id) and self.thread_id == str(thread_id or "")

    def clear(self) -> None:
        self.active = False

    async def send_talk(self, text: str) -> None:
        await submit_voice_text(
            self.send_talk_text,
            text,
            surface=self.surface,
            thread_id=self.thread_id,
        )

    def append_dictation(self, text: str) -> str:
        updated = append_dictation_text(self.get_composer_text(), text)
        self.set_composer_text(updated)
        return updated


_STATUS_PHRASES = (
    "what are you doing",
    "what's happening",
    "whats happening",
    "status",
    "where are we",
    "are you still working",
)
_CANCEL_PHRASES = (
    "cancel",
    "stop",
    "stop that",
    "never mind",
    "nevermind",
    "abort",
)
_STEER_PREFIXES = (
    "also ",
    "actually ",
    "instead ",
    "change that",
    "add ",
    "include ",
    "while you're there",
    "while youre there",
)


def append_dictation_text(existing: str, transcript: str) -> str:
    current = str(existing or "")
    incoming = str(transcript or "").strip()
    if not incoming:
        return current
    if not current.strip():
        return incoming
    separator = "" if current.endswith(("\n", "\r")) else " "
    return f"{current}{separator}{incoming}"


def route_voice_transcript(
    mode: str,
    transcript: str,
    *,
    get_composer_text: Callable[[], str],
    set_composer_text: Callable[[str], None],
    send_talk_text: Callable[[str], None],
) -> Literal["dictated", "sent", "ignored"]:
    text = str(transcript or "").strip()
    if not text:
        return "ignored"
    if mode == "dictate":
        set_composer_text(append_dictation_text(get_composer_text(), text))
        return "dictated"
    send_talk_text(text)
    return "sent"


async def submit_voice_text(
    send_fn: Callable[..., Any],
    text: str,
    *,
    surface: str = "",
    thread_id: str = "",
) -> None:
    """Submit voice text through a composer sender, preserving voice mode when supported."""
    try:
        signature = inspect.signature(send_fn)
        supports_voice_mode = any(
            param.kind == inspect.Parameter.VAR_KEYWORD or name == "voice_mode"
            for name, param in signature.parameters.items()
        )
    except (TypeError, ValueError):
        supports_voice_mode = False

    logger.info(
        "voice.realtime.pipeline %s",
        {
            "stage": "voice_surface_submit_start",
            "surface": surface,
            "thread_id": thread_id,
            "text_chars": len(str(text or "")),
            "supports_voice_mode": supports_voice_mode,
        },
    )
    try:
        if supports_voice_mode:
            result = send_fn(text, voice_mode=True)
        else:
            result = send_fn(text)
        if inspect.isawaitable(result):
            await result
    except Exception:
        logger.exception(
            "voice.realtime.pipeline %s",
            {
                "stage": "voice_surface_submit_failed",
                "surface": surface,
                "thread_id": thread_id,
                "text_chars": len(str(text or "")),
            },
        )
        raise
    logger.info(
        "voice.realtime.pipeline %s",
        {
            "stage": "voice_surface_submit_done",
            "surface": surface,
            "thread_id": thread_id,
            "text_chars": len(str(text or "")),
        },
    )


def classify_active_run_control(text: str) -> ActiveRunControlKind:
    """Classify user input received while a Thoth run is active."""
    normalized = " ".join(str(text or "").strip().lower().split())
    if not normalized:
        return "none"
    if any(normalized == phrase or normalized.startswith(f"{phrase} ") for phrase in _CANCEL_PHRASES):
        return "cancel"
    if any(phrase in normalized for phrase in _STATUS_PHRASES):
        return "status"
    if any(normalized.startswith(prefix) for prefix in _STEER_PREFIXES):
        return "steer"
    return "follow_up"
