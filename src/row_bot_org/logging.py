"""JSON lines on stdout, one per record: what a container's log driver collects. No log files."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import TextIO


class JsonLineFormatter(logging.Formatter):
    """One JSON object per record: time (UTC), level, logger, message and any exception."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str, ensure_ascii=False)


def configure_logging(level: str, stream: TextIO | None = None) -> None:
    """Send every record at ``level`` and above to ``stream`` (stdout) as JSON lines.

    uvicorn's records reach it too, as long as uvicorn is started with ``log_config=None``.
    """
    handler = logging.StreamHandler(sys.stdout if stream is None else stream)
    handler.setFormatter(JsonLineFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)


__all__ = ["JsonLineFormatter", "configure_logging"]
