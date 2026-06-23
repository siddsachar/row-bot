from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.helpers.legacy_inventory import build_legacy_inventory


MIGRATED_COMMAND = "uv run python scripts/run_test_matrix.py fast"


def _pending_sections() -> list[str]:
    return [
        f"{entry.legacy_file}:{entry.start_line} {entry.heading}"
        for entry in build_legacy_inventory()
        if entry.status != "covered"
    ]


def test_legacy_test_suite_has_no_remaining_coverage() -> None:
    pending = _pending_sections()
    assert not pending, "Legacy coverage still pending:\n" + "\n".join(pending)


if __name__ == "__main__":
    pending = _pending_sections()
    print("tests/test_suite.py is retired; coverage lives in focused pytest suites.")
    print(f"Run the deterministic matrix instead: {MIGRATED_COMMAND}")
    if pending:
        print("Pending legacy sections:")
        print("\n".join(pending))
        raise SystemExit(1)
