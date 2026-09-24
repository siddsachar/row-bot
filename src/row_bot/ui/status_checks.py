"""Compatibility import for the shared headless status-check owner."""

from __future__ import annotations

import sys

from row_bot import status_checks as _status_checks

# Keep NiceGUI callers and their existing monkeypatch hooks on the same module.
sys.modules[__name__] = _status_checks
