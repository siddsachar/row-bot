"""Start Learning's evaluation worker (``eval_worker.worker_main``) in its own owned process.

Run by path with the app's base interpreter and ``-I`` (no user site, no PYTHONPATH), so a
virtual environment's launcher never starts a second, unowned process (O43). It adds the folders
Row-Bot itself was imported from (``--site``: site-packages, processed for their ``.pth`` files;
``--path``: plain folders), then hands over. Standard library only until then.
"""

from __future__ import annotations

import os
import site
import sys


def main(argv: list[str]) -> int:
    sites: list[str] = []
    paths: list[str] = []
    index = 0
    while index < len(argv):
        key = argv[index]
        if key in ("--site", "--path") and index + 1 < len(argv):
            (sites if key == "--site" else paths).append(argv[index + 1])
            index += 2
            continue
        raise SystemExit(f"row-bot evaluation worker: unexpected {key!r}")
    for folder in sites:
        if os.path.isdir(folder):
            site.addsitedir(folder)
    for folder in reversed(paths):
        if os.path.isdir(folder) and folder not in sys.path:
            sys.path.insert(0, folder)
    # Before any Row-Bot import: a failed test turn here never reaches the person's desktop
    # (``row_bot.notifications`` shows no desktop notification and plays no sound).
    os.environ["ROW_BOT_DESKTOP_NOTIFICATIONS"] = "off"
    from row_bot.learning.eval_worker import worker_main

    return worker_main()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
