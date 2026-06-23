from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.helpers.legacy_inventory import build_legacy_inventory


MIGRATED_COMMAND = "uv run python -m pytest tests/subsystem/knowledge_graph tests/subsystem/regression -q"


def _pending_sections() -> list[str]:
    return [
        f"{entry.start_line} {entry.heading}"
        for entry in build_legacy_inventory()
        if entry.legacy_file == "tests/test_memory_e2e.py" and entry.status != "covered"
    ]


def test_legacy_memory_e2e_suite_has_no_remaining_coverage() -> None:
    pending = _pending_sections()
    assert not pending, "Legacy memory coverage still pending:\n" + "\n".join(pending)


if __name__ == "__main__":
    pending = _pending_sections()
    print("tests/test_memory_e2e.py is retired; memory coverage lives in subsystem and regression suites.")
    print(f"Run focused memory checks instead: {MIGRATED_COMMAND}")
    if pending:
        print("Pending memory sections:")
        print("\n".join(pending))
        raise SystemExit(1)
