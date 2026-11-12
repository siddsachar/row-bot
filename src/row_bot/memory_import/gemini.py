"""Gemini from Google Takeout: ``My Activity/Gemini Apps/MyActivity.json``.

One record per prompt, newest first, answers as HTML. The chat link in
``details`` is the only way to put prompts back into their chats.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from row_bot.memory_import.archive import Member
from row_bot.memory_import.models import ImportedConversation, ImportedMessage, build_conversation
from row_bot.memory_import.sink import Sink
from row_bot.memory_import.streaming import JsonStream
from row_bot.memory_import.values import as_dict, as_list, as_text, html_to_text, to_utc


PROVIDER = "gemini"
_CHAT_LINK = re.compile(r"gemini\.google\.com/(?:u/\d+/)?app/([0-9A-Za-z_-]+)")
# Title prefixes of prompt records; other languages keep their full title.
_PROMPT_PREFIXES = (
    "Prompted ", "Eingegebener Prompt: ", "Prompt eingegeben: ", "Prompt saisi : ", "Requête : ",
    "Instrucción: ", "Prompt: ", "Prompt inserito: ", "Prompt enviado: ",
)
_OTHER_PREFIXES = ("Created ", "Cleared ", "Gave feedback", "Used ", "Deleted ", "Selected ", "Shared ")


def is_gemini_record(record: dict[str, Any]) -> bool:
    labels = [as_text(record.get("header"))]
    labels += [as_text(value) for value in as_list(record.get("products"))]
    labels += [as_text(value) for value in as_list(record.get("activityControls"))]
    return any("gemini" in label.lower() or "bard" in label.lower() for label in labels)


def _prompt(record: dict[str, Any], answer: str) -> str | None:
    title = as_text(record.get("title")).strip()
    for prefix in _PROMPT_PREFIXES:
        if title.startswith(prefix):
            return title[len(prefix):].strip()
    if title.startswith(_OTHER_PREFIXES) or not answer:
        return None
    return title


def _chat_id(record: dict[str, Any]) -> str | None:
    for detail in as_list(record.get("details")) + as_list(record.get("subtitles")):
        match = _CHAT_LINK.search(as_text(as_dict(detail).get("url")) or as_text(as_dict(detail).get("value")))
        if match:
            return match.group(1)
    match = _CHAT_LINK.search(as_text(record.get("titleUrl")))
    return match.group(1) if match else None


def iter_conversations(member: Member, sink: Sink) -> Iterator[ImportedConversation]:
    chats: dict[str, list[tuple[datetime | None, int, str, str, list[str]]]] = {}
    other = 0
    unlinked = 0
    with member.open() as raw:
        for index, record in enumerate(JsonStream.from_bytes(raw).iter_array()):
            record = as_dict(record)
            if not record:
                continue
            if not is_gemini_record(record):
                other += 1
                continue
            answer = "\n\n".join(html_to_text(as_text(as_dict(item).get("html")))
                                 for item in as_list(record.get("safeHtmlItem")))
            prompt = _prompt(record, answer)
            if prompt is None:
                continue
            stamp = to_utc(record.get("time"))
            files = [as_text(name) for name in as_list(record.get("attachedFiles")) + [record.get("imageFile")]
                     if as_text(name)]
            chat_id = _chat_id(record)
            if chat_id is None:
                unlinked += 1
                chat_id = "single-" + hashlib.sha256(f"{record.get('time')}|{prompt}".encode()).hexdigest()[:16]
            chats.setdefault(chat_id, []).append((stamp, -index, prompt, answer, files))
    if other:
        sink.skip(member.display, f"{other} Google activity records that are not Gemini were left out.")
    if unlinked:
        sink.warn("Some Gemini prompts have no chat link, so each became its own chat.")
    for chat_id, turns in chats.items():
        turns.sort(key=lambda turn: (turn[0].timestamp() if turn[0] else float("-inf"), turn[1]))
        messages: list[ImportedMessage] = []
        for stamp, _, prompt, answer, files in turns:
            notes = [f"[File not included in the import: {name}]" for name in files]
            messages.append(ImportedMessage("user", "\n".join([prompt, *notes]), stamp))
            if answer:
                messages.append(ImportedMessage("assistant", answer, stamp))
        conversation = build_conversation(PROVIDER, chat_id, None, None, None, messages)
        if conversation is not None:
            yield conversation
