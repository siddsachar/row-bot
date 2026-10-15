"""The client's views of background processes (F1, F6): every one, one, its output, Stop.

A person sees and stops every process, whichever conversation started it; the list names that
conversation. Output is read in pages from the process's bounded log.
"""

from __future__ import annotations

from typing import Any

from row_bot import background_processes as processes


def _titles(conversation_ids: set[str]) -> dict[str, str]:
    if not conversation_ids:
        return {}
    try:
        from row_bot.application.client_monitor import _conversation_titles

        return _conversation_titles(conversation_ids)
    except Exception:
        return {}


def _view(record: processes.BackgroundProcess, titles: dict[str, str]) -> dict[str, Any]:
    return processes.view(record, conversation_title=titles.get(record.conversation_id, ""))


def read_background_processes(conversation_id: str | None = None) -> dict[str, Any]:
    """Running processes first, then finished ones, newest first."""
    records = processes.list_processes(conversation_id or None)[:200]
    titles = _titles({record.conversation_id for record in records})
    return {
        "items": [_view(record, titles) for record in records],
        "running": sum(record.running for record in records),
        "limit_per_conversation": processes.MAX_RUNNING_PER_CONVERSATION,
        "limit_total": processes.MAX_RUNNING_TOTAL,
    }


def read_background_process(process_id: str) -> dict[str, Any] | None:
    record = processes.get(process_id)
    if record is None:
        return None
    return _view(record, _titles({record.conversation_id}))


def read_background_process_output(process_id: str, after: int = 0, limit: int = processes.READ_LIMIT
                                   ) -> dict[str, Any] | None:
    """One page of a process's output from position *after* (the oldest kept when that is gone)."""
    record = processes.get(process_id)
    if record is None:
        return None
    limit = max(1024, min(int(limit or processes.READ_LIMIT), processes.READ_LIMIT))
    text, start, end = record.log.read(max(0, int(after or 0)), limit)
    return {
        "process_id": record.process_id,
        "text": text,
        "start": start,
        "end": end,
        "size": record.log.written,
        "earlier_dropped": start > max(0, int(after or 0)),
        "running": record.running,
    }


def stop_background_process(process_id: str) -> dict[str, Any] | None:
    """The person's Stop: no approval, whichever conversation started it."""
    record = processes.stop(process_id, by="you")
    if record is None or record.inline:
        return None
    processes.wait(record, 5)
    return _view(record, _titles({record.conversation_id}))
