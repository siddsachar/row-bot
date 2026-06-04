"""Compatibility wrapper for running Row-Bot from the repository root."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _export_implementation_symbols() -> None:
    import row_bot.app as _app_impl

    for name in dir(_app_impl):
        if name.startswith("__") and name.endswith("__"):
            continue
        globals()[name] = getattr(_app_impl, name)


if __name__ != "__main__":
    _export_implementation_symbols()


if __name__ == "__main__":
    runpy.run_module("row_bot.app", run_name="__main__")
