from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import random


class VoiceCueType(str, Enum):
    HEARD = "heard"
    THINKING = "thinking"
    TOOL_START = "tool_start"
    TOOL_PROGRESS = "tool_progress"
    APPROVAL_NEEDED = "approval_needed"
    BLOCKED = "blocked"
    ERROR = "error"
    FINAL_SUMMARY = "final_summary"
    HANDOFF_TO_CHAT = "handoff_to_chat"
    LONG_RUNNING = "long_running"


class VoiceCuePriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True)
class VoiceCue:
    type: VoiceCueType
    text: str
    priority: VoiceCuePriority = VoiceCuePriority.NORMAL
    reason: str = ""


def heard_cue() -> VoiceCue:
    return VoiceCue(VoiceCueType.HEARD, "Got it. I'm on it.", VoiceCuePriority.LOW, "transcript_received")


def thinking_cue() -> VoiceCue:
    return VoiceCue(
        VoiceCueType.THINKING,
        _choose(
            (
                "I'm working on that.",
                "Still working through that.",
                "I'm pulling that together.",
            )
        ),
        VoiceCuePriority.LOW,
        "slow_response",
    )


def long_running_cue() -> VoiceCue:
    return VoiceCue(
        VoiceCueType.LONG_RUNNING,
        _choose(
            (
                "This is taking a bit longer, but I'm still on it.",
                "I'm still working through that.",
                "A little more time here, but I'm on it.",
            )
        ),
        VoiceCuePriority.LOW,
        "long_running",
    )


def tool_start_cue(tool_name: str) -> VoiceCue:
    return VoiceCue(
        VoiceCueType.TOOL_START,
        _choose(
            (
                "I'm working through the next step.",
                "One moment while I work through that.",
                "I'm taking the next step.",
            )
        ),
        VoiceCuePriority.NORMAL,
        f"tool_start:{tool_name}",
    )


def tool_progress_cue() -> VoiceCue:
    return VoiceCue(
        VoiceCueType.TOOL_PROGRESS,
        _choose(
            (
                "Still working through it.",
                "I'm still working on it.",
                "A little more to do, I'm on it.",
            )
        ),
        VoiceCuePriority.NORMAL,
        "tool_progress",
    )


def results_found_cue() -> VoiceCue:
    return VoiceCue(
        VoiceCueType.TOOL_PROGRESS,
        _choose(
            (
                "I've got the key details now.",
                "I found the main details.",
                "I've got enough to summarize.",
            )
        ),
        VoiceCuePriority.NORMAL,
        "results_found",
    )


def approval_needed_cue() -> VoiceCue:
    return VoiceCue(
        VoiceCueType.APPROVAL_NEEDED,
        "I need your approval in the app before I continue.",
        VoiceCuePriority.CRITICAL,
        "approval_needed",
    )


def error_cue() -> VoiceCue:
    return VoiceCue(
        VoiceCueType.ERROR,
        "Something went wrong. I put the details in the app.",
        VoiceCuePriority.CRITICAL,
        "generation_error",
    )


def handoff_to_chat_cue() -> VoiceCue:
    return VoiceCue(
        VoiceCueType.HANDOFF_TO_CHAT,
        "The full details are in the app.",
        VoiceCuePriority.NORMAL,
        "handoff_to_chat",
    )


def _choose(options: tuple[str, ...]) -> str:
    return random.choice(options)
