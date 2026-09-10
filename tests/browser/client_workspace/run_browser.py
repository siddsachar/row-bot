"""Run Phase 3 browser checks through the existing isolated host/process owner."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent


class _FixtureProcessAdapter:
    """Replace only the exact synthetic entry script, retaining owned cleanup."""

    def __getattr__(self, name: str):
        return getattr(subprocess, name)

    def Popen(self, argv, *args, **kwargs):
        argv = list(argv)
        original = ROOT / "tests/browser/client_platform/fixture_app.py"
        if len(argv) > 1 and Path(argv[1]) == original:
            argv[1] = str(HERE / "fixture_app.py")
        return subprocess.Popen(argv, *args, **kwargs)


def main() -> int:
    """Delegate isolation, private launch, observations and cleanup unchanged."""
    source = ROOT / "tests/browser/client_foundation/run_browser.py"
    spec = importlib.util.spec_from_file_location("phase3_browser_runner", source)
    if spec is None or spec.loader is None:
        raise RuntimeError("The canonical browser runner is unavailable")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    evidence = Path(os.environ.get(
        "ROW_BOT_BROWSER_EVIDENCE",
        str(ROOT / ".local/evidence/unified-client-platform/phase-3-visual-alignment/qa"),
    )).resolve()
    sealed = (ROOT / ".local/evidence/unified-client-platform/phase-3").resolve()
    if evidence == sealed or sealed in evidence.parents:
        raise RuntimeError("Browser output cannot overwrite sealed Phase 3 evidence")
    runner.EVIDENCE = evidence
    private_environment = runner.private_environment

    def isolated_git_environment(short, port, token):
        env = private_environment(short, port, token)
        config = short / "synthetic-gitconfig"
        config.write_text("", encoding="utf-8")
        env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": str(config)})
        return env

    runner.private_environment = isolated_git_environment
    runner.subprocess = _FixtureProcessAdapter()
    return runner.main()


if __name__ == "__main__":
    raise SystemExit(main())
