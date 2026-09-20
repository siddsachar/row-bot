"""Run the paired core-surface audit through the canonical isolated owner."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import json

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
EVIDENCE = ROOT / ".local/evidence/unified-client-platform/core-surface-parity/qa"
sys.path.insert(0, str(ROOT))

from tests.browser.client_workspace import run_browser as workspace_runner  # noqa: E402


class _FixtureProcessAdapter:
    """Retain the canonical runner while selecting this fixture entrypoint."""

    def __getattr__(self, name: str):
        return getattr(subprocess, name)

    def Popen(self, argv, *args, **kwargs):
        argv = list(argv)
        original = ROOT / "tests/browser/client_platform/fixture_app.py"
        if len(argv) > 1 and Path(argv[1]) == original:
            argv[1] = str(HERE / "fixture_app.py")
        return subprocess.Popen(argv, *args, **kwargs)


def _ensure_core_config() -> None:
    marker = "--config"
    if marker in sys.argv:
        return
    separator = sys.argv.index("--") if "--" in sys.argv else len(sys.argv)
    if separator == len(sys.argv):
        sys.argv.append("--")
    sys.argv.extend([marker, str(HERE / "playwright.config.mjs")])


def _latest_run() -> Path | None:
    evidence = Path(os.environ.get("ROW_BOT_BROWSER_EVIDENCE", EVIDENCE)).resolve()
    runs = sorted(evidence.glob("browser-*"), key=lambda path: path.stat().st_mtime)
    return runs[-1] if runs else None


def _compose(run: Path) -> None:
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/compose_core_surface_parity.py"),
            str(run / "artifacts"),
            "--output",
            str(run / "pairs"),
        ],
        cwd=ROOT,
        check=True,
    )


def main() -> int:
    os.environ.setdefault("ROW_BOT_BROWSER_EVIDENCE", str(EVIDENCE))
    _ensure_core_config()
    workspace_runner._FixtureProcessAdapter = _FixtureProcessAdapter
    code = workspace_runner.main()
    run = _latest_run()
    if run is None:
        return code or 1
    _compose(run)
    result = json.loads((run / "results.json").read_text(encoding="utf-8"))
    if result.get("source_stable") is not True:
        print(
            "Core-surface parity rejected evidence because source changed during capture"
        )
        return 1
    return code


if __name__ == "__main__":
    raise SystemExit(main())
