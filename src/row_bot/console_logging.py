"""Where Row-Bot's console logging goes (F2).

A host shows a container's output as its log, and Railway colours every line on stderr as an error.
In Row-Bot's own image (``ROW_BOT_CONTAINERIZED=1``, which the image sets), INFO and below go to
stdout and WARNING and above to stderr. Everywhere else, the desktop and ``row-bot serve`` outside
the image, all of it stays on stderr, as it always has.
"""

from __future__ import annotations

import logging
import sys
from typing import Mapping

from row_bot.runtime_paths import is_containerized_runtime


def _below_warning(record: logging.LogRecord) -> bool:
    return record.levelno < logging.WARNING


def console_handlers(environ: Mapping[str, str] | None = None) -> list[logging.Handler]:
    """Handlers for this process's console: stderr alone, or in Row-Bot's image stdout and stderr by level.

    Each handler writes to the stream as it is now, so make them where the console is wanted.
    """
    if not is_containerized_runtime(environ):
        return [logging.StreamHandler(sys.stderr)]
    quiet = logging.StreamHandler(sys.stdout)
    quiet.addFilter(_below_warning)
    loud = logging.StreamHandler(sys.stderr)
    loud.setLevel(logging.WARNING)
    return [quiet, loud]


__all__ = ["console_handlers"]
