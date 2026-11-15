"""Which published files a change touches: the landing page, or the docs site.

GitHub Pages serves row-bot.ai from ``main:/docs``. Its root is the landing page, kept by hand
and changed only after a release. The docs site under it is built from ``docs-site`` and published
only when a release is prepared (``scripts/docs/publish.py``).

Reads changed paths (one per line, relative to the repository) on stdin:

- ``--landing``: fails, naming them, when any is a landing-page file, unless ``--allowed true``
  (the pull request carries the ``landing`` label).
- ``--published``: prints ``true`` when any is a file of the published docs site (a publish), else
  ``false``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sync_github_pages import OWNED_DIRECTORIES, OWNED_FILES  # noqa: E402

# Developer notes kept beside the site, which the site doesn't serve as pages.
_NOTES = ("qa", "client-platform")


def published_path(path: str) -> str | None:
    """The path inside the published folder, or None when it's elsewhere in the repository."""
    return path[len("docs/"):] if path.startswith("docs/") else None


def is_landing(path: str) -> bool:
    """True for a file of the landing page: anything published at the site's root that the docs build doesn't
    own (pages, styles, scripts, media, the domain's CNAME), but not the developer notes kept beside it."""
    inside = published_path(path)
    if not inside or inside.endswith(".md"):
        return False
    top = inside.split("/", 1)[0]
    return top not in OWNED_DIRECTORIES and inside not in OWNED_FILES and top not in _NOTES


def is_docs_site(path: str) -> bool:
    """True for a file of the published docs site (what a publish rewrites)."""
    inside = published_path(path)
    if not inside:
        return False
    return inside.split("/", 1)[0] in OWNED_DIRECTORIES or inside in OWNED_FILES


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--landing", action="store_true")
    mode.add_argument("--published", action="store_true")
    parser.add_argument("--allowed", default="false", help="true when the pull request carries the landing label")
    args = parser.parse_args(argv)
    paths = [line.strip() for line in sys.stdin if line.strip()]
    if args.published:
        print("true" if any(is_docs_site(path) for path in paths) else "false")
        return 0
    landing = [path for path in paths if is_landing(path)]
    if landing and args.allowed.strip().lower() != "true":
        print("The landing page changes by hand, only after a release. This pull request changes:")
        for path in landing:
            print(f"  {path}")
        print("If this is that update, add the `landing` label to the pull request.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
