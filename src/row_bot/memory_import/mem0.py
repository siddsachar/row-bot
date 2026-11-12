"""mem0 memories: a ``get_all`` list or ``{"results": [...]}``, and OpenMemory backups."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from row_bot.memory_import.archive import Member
from row_bot.memory_import.sink import Sink
from row_bot.memory_import.streaming import JsonStream
from row_bot.memory_import.values import as_dict, as_list, as_text, iso_date


PROVIDER = "mem0"
_CATEGORIES = {
    "family": "person", "relationships": "person", "friends": "person",
    "user_preferences": "preference", "preferences": "preference", "food": "preference", "music": "preference",
    "hobbies": "preference", "fashion": "preference", "entertainment": "preference", "sports": "preference",
    "travel": "event", "milestones": "event", "events": "event",
    "technology": "skill", "skills": "skill", "projects": "project", "work": "fact",
    "organization": "organisation", "organisation": "organisation", "places": "place", "location": "place",
}
_OPTIONAL_STATES = {"paused", "archived"}


def _category(names: Iterable[Any]) -> str:
    for name in names:
        mapped = _CATEGORIES.get(as_text(name).strip().lower().replace(" ", "_"))
        if mapped:
            return mapped
    return "fact"


class _Counter:
    def __init__(self) -> None:
        self.deleted = 0


def _add_item(item: Any, sink: Sink, label: str, counter: _Counter,
              category_names: dict[str, str] | None = None) -> None:
    item = as_dict(item)
    text = as_text(item.get("memory")) or as_text(item.get("content"))
    if not text.strip():
        return
    state = as_text(item.get("state")).lower()
    if state == "deleted" or item.get("deleted_at") or item.get("is_deleted"):
        counter.deleted += 1
        return
    names = [as_text(value) if not isinstance(value, dict) else as_text(value.get("name"))
             for value in as_list(item.get("categories"))]
    names += [(category_names or {}).get(as_text(value), "") for value in as_list(item.get("category_ids"))]
    tags = (state,) if state in _OPTIONAL_STATES else ()
    item_id = as_text(item.get("id")) or as_text(item.get("hash"))
    sink.add_facts(text, category=_category(names), source=PROVIDER, source_label=label,
                   ref=f"mem0:{item_id}" if item_id else "mem0", date=iso_date(item.get("created_at")),
                   tags=tags, sentences=False)


def _finish(counter: _Counter, member: Member, sink: Sink) -> None:
    if counter.deleted:
        sink.skip(member.display, f"{counter.deleted} deleted memories were left out.")


def read_items(member: Member, sink: Sink) -> None:
    """A bare list of memory items, or an object that wraps them in ``results`` or ``memories``."""
    counter = _Counter()
    with member.open() as raw:
        stream = JsonStream.from_bytes(raw)
        if stream.peek() == "[":
            for item in stream.iter_array():
                _add_item(item, sink, "mem0 memory", counter)
        else:
            for key in stream.iter_object():
                if key in {"results", "memories"} and stream.peek() == "[":
                    for item in stream.iter_array():
                        _add_item(item, sink, "mem0 memory", counter)
    _finish(counter, member, sink)


def read_openmemory(member: Member, sink: Sink) -> None:
    """OpenMemory ``memories.json``: active memories as facts; paused and archived ones tagged."""
    counter = _Counter()
    category_names: dict[str, str] = {}
    with member.open() as raw:
        stream = JsonStream.from_bytes(raw)
        for key in stream.iter_object():
            if key == "categories" and stream.peek() == "[":
                for category in stream.iter_array():
                    category = as_dict(category)
                    category_names[as_text(category.get("id"))] = as_text(category.get("name"))
            elif key == "memories" and stream.peek() == "[":
                for item in stream.iter_array():
                    _add_item(item, sink, "OpenMemory backup", counter, category_names)
            elif key in {"user", "access_controls"}:
                sink.skip(f"{member.display} ({key})", "Account details are never imported.")
    _finish(counter, member, sink)


def read_openmemory_lines(member: Member, sink: Sink) -> None:
    """OpenMemory ``memories.jsonl.gz``: one memory per line."""
    counter = _Counter()
    with member.open() as raw:
        for item in JsonStream.from_bytes(raw).iter_values():
            for record in item if isinstance(item, list) else [item]:
                _add_item(record, sink, "OpenMemory backup", counter)
    _finish(counter, member, sink)
