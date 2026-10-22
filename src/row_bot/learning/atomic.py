"""Small files Learning saves whole: written beside, then swapped in (L2c2).

A reader never sees half a file. On Windows a file written a moment ago can be held briefly by an
antivirus scan or the indexer (``os.replace`` fails with WinError 5 or 32), so the swap waits that
out for a few seconds before giving up.
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path


def replace(source: Path, target: Path, *, seconds: float = 10.0) -> None:
    """``os.replace`` that waits out a brief Windows hold on either file."""

    deadline = time.monotonic() + seconds
    wait = 0.01
    while True:
        try:
            os.replace(source, target)
            return
        except OSError as error:
            if getattr(error, "winerror", None) not in (5, 32) or time.monotonic() > deadline:
                raise
        time.sleep(wait)
        wait = min(wait * 2, 0.5)


def write_text(path: Path, text: str) -> None:
    """Save ``text`` as ``path`` (UTF-8), whole or not at all."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(text, encoding="utf-8")
    try:
        replace(temporary, target)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


__all__ = ["replace", "write_text"]
