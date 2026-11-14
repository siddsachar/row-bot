"""Row-Bot's public-benchmark runner (benchmarks plan, P0): memory benchmarks first.

    uv run python scripts/bench.py plan --benchmark locomo --data <locomo10.json> --key <errors.json>
    uv run python scripts/bench.py protocol --benchmark beam-1m --protocol <src/prompts.py>,<compute_metrics.py>
    ROW_BOT_BENCH=1 uv run python scripts/bench.py run --benchmark locomo --data ... --key ... \\
        --protocol <prompts.yaml> --model codex:auto [--mode full-context] [--dream] \\
        [--judge codex:auto --second-judge ollama:qwen3.8:27b --second-judge-subset 100]
    ROW_BOT_BENCH=1 uv run python scripts/bench.py judge --run-dir <run> --judge codex:auto
    uv run python scripts/bench.py report --run-dir <run>

Benchmarks: ``locomo`` (the audited key next to the original), ``longmemeval-s``, ``beam-1m`` and
``longmemeval-v2``. Modes: ``memory`` (Row-Bot's memory path; ``--dream`` adds a Dream Cycle
pass) and ``full-context`` (the same model reads the whole history, no memory).

Every task runs in its own worker with a scratch data folder, test mode, Learning off and the
install count off. Paid APIs are refused while ``ROW_BOT_BENCH_MAX_USD`` is $0 (the default); the
ChatGPT plan runs only from the eval profile (``ROW_BOT_LIVE_EVAL_CODEX_PROFILE``), counted, not
capped; the only local model is the stock ``qwen3.8:27b``, its digest recorded. The eval profile
and the graphics card are booked in ``ROW_BOT_BENCH_BOOKING_FILE``. Runs land in ``.tmp/bench/``.
See ``tests/e2e/bench/``.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.e2e.bench.orchestrate import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
