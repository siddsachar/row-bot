"""Row-Bot Learning's release check on seeded people (S1, PLAN.md O92).

    uv run python scripts/learning_release_check.py --people all --days 30 --tiers 8gb \\
        --out <folder> --scratch <folder> --eval-profile <folder> --runtimes-from <folder>
    uv run python scripts/learning_release_check.py --plan --out <folder> --scratch <folder>

Made-up people with scripted 30-day histories and a live stream of requests, each run through the
real runtime in its own scratch profile (Learning's preview flag on, the main model GPT-6.1 Sol on the
ChatGPT lane's eval profile), with a simulated person who asks, corrects, teaches, approves and stops;
then a report of the whole Learning loop with denominators and intervals. Resumes where it stopped.
See tests/e2e/learning/seeded/ for the harness and the people.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.e2e.learning.seeded.release_check import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
