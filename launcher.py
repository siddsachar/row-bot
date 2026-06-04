"""Compatibility wrapper for launching Row-Bot from the repository root."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from row_bot.launcher import main


def _export_implementation_symbols() -> None:
    import row_bot.launcher as _launcher_impl

    for name in dir(_launcher_impl):
        if name.startswith("__") and name.endswith("__"):
            continue
        globals()[name] = getattr(_launcher_impl, name)


if __name__ != "__main__":
    _export_implementation_symbols()


if __name__ == "__main__":
    main()
