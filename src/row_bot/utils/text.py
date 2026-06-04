"""Text-safety helpers.

Currently exposes:
    * ``strip_surrogates(s)`` — remove lone UTF-16 surrogate code points that
      ``orjson``/``utf-8`` reject.  Surrogates most commonly arrive from
      web scraping, PDF text extraction, or clipboard data on Windows.
"""

from __future__ import annotations

import re as _re

_SURROGATE_RE = _re.compile("[\ud800-\udfff]")


def strip_surrogates(s: str) -> str:
    """Return *s* with any lone UTF-16 surrogate code points removed.

    Fast path — returns the original string object (no copy) when no
    surrogates are present.
    """
    if not isinstance(s, str):
        return s
    if _SURROGATE_RE.search(s) is None:
        return s
    return _SURROGATE_RE.sub("", s)
