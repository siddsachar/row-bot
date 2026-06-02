from __future__ import annotations

from dataclasses import dataclass

from voice.speech_policy import clean_for_speech, split_sentences


@dataclass
class RealtimeSpeechChunker:
    """Turn streamed Thoth text into bounded chunks suitable for Realtime speech."""

    buffer: str = ""
    max_chunks: int = 4
    max_chunk_chars: int = 280
    min_chunk_chars: int = 32
    spoken_chunks: int = 0

    def push(self, text: str) -> list[str]:
        if self.spoken_chunks >= self.max_chunks:
            return []
        self.buffer += str(text or "")
        return self._drain(allow_short=False)

    def flush(self) -> list[str]:
        return self._drain(allow_short=True)

    def _drain(self, *, allow_short: bool) -> list[str]:
        chunks: list[str] = []
        while self.spoken_chunks + len(chunks) < self.max_chunks:
            candidate, remainder = self._next_candidate(allow_short=allow_short)
            if not candidate:
                break
            clean = clean_for_speech(candidate)
            if not clean:
                self.buffer = remainder
                continue
            chunks.append(clean[: self.max_chunk_chars].strip())
            self.buffer = remainder
        self.spoken_chunks += len(chunks)
        return chunks

    def _next_candidate(self, *, allow_short: bool) -> tuple[str, str]:
        parts = split_sentences(self.buffer)
        if len(parts) > 1:
            return self._bounded(parts[0], self.buffer[len(parts[0]) :])
        if parts and allow_short:
            return self._bounded(parts[0], "")
        if parts and len(parts[0]) >= self.max_chunk_chars:
            return self._bounded(parts[0], "")
        if parts and len(parts[0]) >= self.min_chunk_chars and parts[0].endswith((".", "!", "?")):
            return self._bounded(parts[0], self.buffer[len(parts[0]) :])
        return "", self.buffer

    def _bounded(self, text: str, remainder: str) -> tuple[str, str]:
        clean = str(text or "").strip()
        if len(clean) <= self.max_chunk_chars:
            return clean, remainder.lstrip()
        boundary = clean.rfind(" ", 0, self.max_chunk_chars)
        if boundary < self.min_chunk_chars:
            boundary = self.max_chunk_chars
        return clean[:boundary].strip(), (clean[boundary:] + " " + remainder).strip()


@dataclass
class RealtimeSpeechQueue:
    """Coverage-aware Realtime speech queue for streamed Thoth answers."""

    max_spoken_chunks: int = 4
    max_flush_chars: int = 320
    min_final_coverage_ratio: float = 0.55
    min_final_unspoken_chars: int = 180
    spoken_chunk_count: int = 0
    spoken_stream_chars: int = 0
    coalesced_stream_text: str = ""
    final_suppressed_reason: str = ""
    final_suppressed_after_stream: bool = False

    def offer_stream_chunk(self, text: str, *, playback_active: bool = False) -> str:
        clean = clean_for_speech(text)
        if not clean:
            return ""
        if playback_active or self.spoken_chunk_count >= self.max_spoken_chunks:
            self._queue(clean)
            return ""
        self.record_spoken(clean)
        return clean

    def flush_queued(self, *, playback_active: bool = False) -> str:
        if playback_active or self.spoken_chunk_count >= self.max_spoken_chunks:
            return ""
        clean = clean_for_speech(self.coalesced_stream_text)
        if not clean:
            self.coalesced_stream_text = ""
            return ""
        chunk, remainder = self._bounded(clean)
        self.coalesced_stream_text = remainder
        self.record_spoken(chunk)
        return chunk

    def record_spoken(self, text: str) -> None:
        clean = clean_for_speech(text)
        if not clean:
            return
        self.spoken_chunk_count += 1
        self.spoken_stream_chars += len(clean)

    def final_decision(self, text: str) -> dict[str, object]:
        clean = clean_for_speech(text)
        final_chars = len(clean)
        if not clean:
            self.final_suppressed_reason = "empty"
            return {
                "speak": False,
                "reason": "empty",
                "final_chars": 0,
                "spoken_stream_chars": self.spoken_stream_chars,
                "coalesced_chars": len(self.coalesced_stream_text),
                "coverage_ratio": 0.0,
            }
        if self.spoken_stream_chars <= 0:
            self.final_suppressed_reason = ""
            return {
                "speak": True,
                "reason": "no_stream_speech",
                "final_chars": final_chars,
                "spoken_stream_chars": 0,
                "coalesced_chars": len(self.coalesced_stream_text),
                "coverage_ratio": 0.0,
            }
        coverage = min(1.0, self.spoken_stream_chars / max(1, final_chars))
        unspoken = max(0, final_chars - self.spoken_stream_chars)
        should_speak = coverage < self.min_final_coverage_ratio or unspoken >= self.min_final_unspoken_chars
        reason = "insufficient_stream_coverage" if should_speak else "stream_coverage_sufficient"
        self.final_suppressed_after_stream = not should_speak
        self.final_suppressed_reason = "" if should_speak else reason
        return {
            "speak": should_speak,
            "reason": reason,
            "final_chars": final_chars,
            "spoken_stream_chars": self.spoken_stream_chars,
            "coalesced_chars": len(self.coalesced_stream_text),
            "coverage_ratio": coverage,
            "unspoken_chars": unspoken,
        }

    def should_speak_final(self, text: str) -> bool:
        return bool(self.final_decision(text).get("speak"))

    def _queue(self, text: str) -> None:
        clean = clean_for_speech(text)
        if not clean:
            return
        self.coalesced_stream_text = " ".join(
            part for part in [self.coalesced_stream_text.strip(), clean] if part
        ).strip()

    def _bounded(self, text: str) -> tuple[str, str]:
        clean = str(text or "").strip()
        if len(clean) <= self.max_flush_chars:
            return clean, ""
        boundary = clean.rfind(" ", 0, self.max_flush_chars)
        if boundary < 80:
            boundary = self.max_flush_chars
        return clean[:boundary].strip(), clean[boundary:].strip()
