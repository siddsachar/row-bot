"""Where Row-Bot's console logging goes (F2).

A host shows a container's output as its log, and Railway colours every line on stderr as an error.
So in Row-Bot's own image (``ROW_BOT_CONTAINERIZED=1``, which the image sets) the long-running server,
``row-bot serve``, the container's main process, sends INFO and below to stdout and WARNING and above
to stderr. Every other command keeps stdout for its own output (``row-bot access doctor --json``), with
its logging on stderr, and so does everything outside the image, as it always has.
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


def route_console_by_level(target: logging.Logger | None = None, environ: Mapping[str, str] | None = None) -> None:
    """``row-bot serve`` only: the logger's (the root's by default) console output becomes ``console_handlers()``.

    Outside Row-Bot's image this changes nothing. Log files and any other handler are left as they are.
    """
    if not is_containerized_runtime(environ):
        return
    logger = logging.getLogger() if target is None else target
    formatter: logging.Formatter | None = None
    for handler in logger.handlers[:]:
        if isinstance(handler, logging.StreamHandler) and handler.stream in (sys.stdout, sys.stderr):
            formatter = formatter or handler.formatter
            logger.removeHandler(handler)
    for handler in console_handlers(environ):
        handler.setFormatter(formatter or logging.Formatter("%(levelname)s: %(message)s"))
        logger.addHandler(handler)


__all__ = ["console_handlers", "route_console_by_level"]
