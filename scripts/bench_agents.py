"""Row-Bot as the agent in public agent benchmarks (benchmarks plan, lane E): τ²-bench first.

    uv run python scripts/bench_agents.py tau2 plan --domain airline --split train --tau2-dir <tau2-bench checkout>
    ROW_BOT_BENCH=1 uv run python scripts/bench_agents.py tau2 run --domain airline --split train --trials 1 \\
        --model codex:gpt-5.2 --reasoning-effort high --user-model codex:gpt-5.2 --user-effort low
    uv run python scripts/bench_agents.py tau2 report --run-dir <run> --published <their results.json>

Same rules as ``scripts/bench.py``: a scratch data folder per run, test mode, Learning off, the install
count off, paid APIs refused while ``ROW_BOT_BENCH_MAX_USD`` is $0, the ChatGPT plan only from the eval
profile, booked in ``ROW_BOT_BENCH_BOOKING_FILE``. See ``tests/e2e/bench/agent_evals/``.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.e2e.bench.agent_evals.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
