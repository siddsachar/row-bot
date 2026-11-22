"""Streamed answer text into speakable sentences, as soon as each one is whole.

The first piece of an answer may be a clause ("Sure, ..."), so speech starts
before the first sentence ends. Code blocks are never spoken. Every piece is
cleaned with ``speech_policy.clean_for_speech``; nothing is ever capped.
"""
from __future__ import annotations

import re

from row_bot.voice.speech_policy import clean_for_speech

_FENCE = "```"
# A sentence ends at . ! ? (with closing quotes or brackets) before whitespace
# and a character that starts a new sentence, or at a line break.
_SENTENCE_END = re.compile(r"[.!?…]+[\"'”’)\]]*(?=\s+[^\s])|\n")
_CLAUSE_END = re.compile(r"[,;:—–](?=\s)")
_LOWER_AFTER = re.compile(r"\s+[a-z]")
FIRST_CLAUSE_CHARS = 24
MAX_PIECE_CHARS = 240


class SentenceAggregator:
    def __init__(self) -> None:
        self._buffer = ""
        self._in_code = False
        self._emitted = 0

    def reset(self) -> None:
        self._buffer = ""
        self._in_code = False
        self._emitted = 0

    def push(self, text: str) -> list[str]:
        self._buffer += str(text or "")
        return self._drain()

    def flush(self) -> list[str]:
        pieces = self._drain()
        rest, self._buffer = self._buffer, ""
        if not self._in_code:
            pieces.extend(self._clean(rest))
        self._in_code = False
        return pieces

    def _clean(self, raw: str) -> list[str]:
        text = clean_for_speech(raw)
        if not text:
            return []
        self._emitted += 1
        return [" ".join(text.split())]

    def _drain(self) -> list[str]:
        pieces: list[str] = []
        while True:
            if self._in_code:
                end = self._buffer.find(_FENCE)
                if end < 0:
                    # Keep only what could start the closing fence.
                    self._buffer = self._buffer[-2:]
                    return pieces
                self._buffer = self._buffer[end + 3:]
                self._in_code = False
                continue
            fence = self._buffer.find(_FENCE)
            head = self._buffer if fence < 0 else self._buffer[:fence]
            cut = self._cut(head)
            if cut is not None:
                pieces.extend(self._clean(head[:cut]))
                self._buffer = self._buffer[cut:]
                continue
            if fence >= 0:
                pieces.extend(self._clean(head))
                self._buffer = self._buffer[fence + 3:]
                self._in_code = True
                continue
            return pieces

    def _cut(self, text: str) -> int | None:
        for match in _SENTENCE_END.finditer(text):
            end = match.end()
            # "e.g. the" and "3.5 km" are not sentence ends.
            if match.group() != "\n" and _LOWER_AFTER.match(text, end):
                continue
            if text[:end].strip():
                return end
        if self._emitted == 0:
            for match in _CLAUSE_END.finditer(text):
                if match.end() >= FIRST_CLAUSE_CHARS and len(text[:match.end()].split()) >= 4:
                    return match.end()
        if len(text) > MAX_PIECE_CHARS:
            clause = [m.end() for m in _CLAUSE_END.finditer(text, 0, MAX_PIECE_CHARS)]
            if clause:
                return clause[-1]
            space = text.rfind(" ", 0, MAX_PIECE_CHARS)
            return space if space > 0 else MAX_PIECE_CHARS
        return None
