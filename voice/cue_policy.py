from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from voice.cues import VoiceCue, VoiceCuePriority, VoiceCueType


REALTIME_CUE_INTERVAL_SECONDS = 12.0
NORMAL_CUE_INTERVAL_SECONDS = 12.0
NORMAL_LOW_PRIORITY_DELAY_SECONDS = 2.0
REALTIME_LOW_PRIORITY_DELAY_SECONDS = 0.6
REALTIME_LONG_RUNNING_DELAY_SECONDS = 18.0
NORMAL_LONG_RUNNING_DELAY_SECONDS = 12.0
REALTIME_TOOL_CUE_INTERVAL_SECONDS = 14.0
REALTIME_TOOL_CUE_MIN_ELAPSED_SECONDS = 4.0
REALTIME_TOOL_CUE_AFTER_ANY_CUE_SECONDS = 6.0
_OPENING_LOW_PRIORITY_TYPES = {VoiceCueType.HEARD, VoiceCueType.THINKING}
_REPEATABLE_PROGRESS_TYPES = {
    VoiceCueType.LONG_RUNNING,
    VoiceCueType.TOOL_START,
    VoiceCueType.TOOL_PROGRESS,
}


@dataclass
class VoiceCuePolicy:
    mode: str
    now: Callable[[], float]
    spoken_types: set[VoiceCueType] = field(default_factory=set)
    last_spoken_at: float = -999.0
    last_tool_spoken_at: float = -999.0
    last_spoken_text: str = ""

    def should_speak(self, cue: VoiceCue, *, generation_elapsed: float = 0.0, user_speaking: bool = False) -> bool:
        if self.mode not in {"normal", "realtime"}:
            return False
        if not cue.text.strip():
            return False
        if user_speaking and cue.priority != VoiceCuePriority.CRITICAL:
            return False
        if cue.type in _OPENING_LOW_PRIORITY_TYPES and self.spoken_types.intersection(_OPENING_LOW_PRIORITY_TYPES):
            return False
        if cue.type in self.spoken_types and cue.type not in {
            VoiceCueType.ERROR,
            VoiceCueType.APPROVAL_NEEDED,
            VoiceCueType.FINAL_SUMMARY,
            VoiceCueType.LONG_RUNNING,
            VoiceCueType.TOOL_START,
            VoiceCueType.TOOL_PROGRESS,
        }:
            return False
        if cue.text.strip() == self.last_spoken_text and cue.type not in _REPEATABLE_PROGRESS_TYPES:
            return False

        if cue.priority == VoiceCuePriority.CRITICAL:
            return True

        if cue.priority == VoiceCuePriority.LOW:
            if cue.type == VoiceCueType.LONG_RUNNING:
                threshold = (
                    REALTIME_LONG_RUNNING_DELAY_SECONDS
                    if self.mode == "realtime"
                    else NORMAL_LONG_RUNNING_DELAY_SECONDS
                )
            else:
                threshold = (
                    REALTIME_LOW_PRIORITY_DELAY_SECONDS
                    if self.mode == "realtime"
                    else NORMAL_LOW_PRIORITY_DELAY_SECONDS
                )
            if generation_elapsed < threshold:
                return False

        min_interval = (
            REALTIME_CUE_INTERVAL_SECONDS
            if self.mode == "realtime"
            else NORMAL_CUE_INTERVAL_SECONDS
        )
        if self.mode == "realtime" and cue.type in {VoiceCueType.TOOL_START, VoiceCueType.TOOL_PROGRESS}:
            if generation_elapsed < REALTIME_TOOL_CUE_MIN_ELAPSED_SECONDS:
                return False
            if (self.now() - self.last_spoken_at) < REALTIME_TOOL_CUE_AFTER_ANY_CUE_SECONDS:
                return False
            return (self.now() - self.last_tool_spoken_at) >= REALTIME_TOOL_CUE_INTERVAL_SECONDS

        return (self.now() - self.last_spoken_at) >= min_interval

    def record_spoken(self, cue: VoiceCue) -> None:
        self.spoken_types.add(cue.type)
        if self.mode == "realtime" and cue.type in {VoiceCueType.TOOL_START, VoiceCueType.TOOL_PROGRESS}:
            self.last_tool_spoken_at = self.now()
        self.last_spoken_at = self.now()
        self.last_spoken_text = cue.text.strip()
