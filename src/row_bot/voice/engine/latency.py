"""Per-turn latency marks and a rolling summary for Row-Bot Voice.

Each spoken turn records when the person stopped speaking, when the turn was
judged finished, the final transcript, the agent's admission and first token,
the first speakable sentence, the first audio frame sent and (reported by the
client) the first audio played. Only durations are kept, never words or audio.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import threading

MARKS = (
    "speech_end",
    "turn_end",
    "transcript_final",
    "agent_admitted",
    "first_token",
    "first_sentence",
    "first_audio_sent",
    "first_audio_played",
)
STAGES = (
    ("endpointing", "speech_end", "turn_end"),
    ("transcription", "turn_end", "transcript_final"),
    ("admission", "transcript_final", "agent_admitted"),
    ("first_token", "agent_admitted", "first_token"),
    ("first_sentence", "first_token", "first_sentence"),
    ("synthesis", "first_sentence", "first_audio_sent"),
    ("playback_start", "first_audio_sent", "first_audio_played"),
)
WINDOW = 50


@dataclass
class TurnMarks:
    marks: dict[str, float] = field(default_factory=dict)

    def mark(self, name: str, at: float) -> None:
        self.marks.setdefault(name, at)

    def has(self, name: str) -> bool:
        return name in self.marks

    def ms(self, start: str, end: str) -> int | None:
        if start not in self.marks or end not in self.marks:
            return None
        return max(0, round((self.marks[end] - self.marks[start]) * 1000))

    def breakdown(self) -> dict[str, int]:
        result = {name: value for name, start, end in STAGES if (value := self.ms(start, end)) is not None}
        if (total := self.ms("speech_end", "first_audio_sent")) is not None:
            result["voice_to_voice"] = total
        if (played := self.ms("speech_end", "first_audio_played")) is not None:
            result["voice_to_ear"] = played
        return result


def _percentile(values: list[int], share: float) -> int:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(share * (len(ordered) - 1))))
    return ordered[index]


class LatencyLog:
    """The last ``WINDOW`` turns' breakdowns, summarised as p50 and p90."""

    def __init__(self) -> None:
        self._turns: deque[dict[str, int]] = deque(maxlen=WINDOW)
        self._lock = threading.Lock()

    def record(self, breakdown: dict[str, int]) -> None:
        if breakdown:
            with self._lock:
                self._turns.append(dict(breakdown))

    def summary(self) -> dict[str, object]:
        with self._lock:
            turns = list(self._turns)
        keys = [name for name, _start, _end in STAGES] + ["voice_to_voice", "voice_to_ear"]
        stages: dict[str, dict[str, int]] = {}
        for key in keys:
            values = [turn[key] for turn in turns if key in turn]
            if values:
                stages[key] = {"p50": _percentile(values, 0.5), "p90": _percentile(values, 0.9)}
        return {"turns": len(turns), "last": turns[-1] if turns else {}, "stages": stages}


#: This server's voice turns, for the Monitor (lives only in memory).
recent_turns = LatencyLog()
