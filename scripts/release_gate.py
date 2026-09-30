"""Release gate: a release builds only a commit that CI has already passed.

For the commit being released it checks that the requested version matches
src/row_bot/version.py and installer/row_bot_setup.iss, and refuses the commit
unless a `CI / ci-ok` check on it succeeded. It then reports, as
`nightly_green=true|false` in $GITHUB_OUTPUT, whether a nightly run already
passed on that commit, so release.yml runs the nightly suite only when none has.

Reads GitHub through the `gh` CLI (GH_TOKEN and GH_REPO come from the workflow).
Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


class GateError(Exception):
    pass


def _gh(*args: str) -> object:
    result = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise GateError(f"could not read the commit's runs from GitHub ({result.stderr.strip() or result.returncode})")
    return json.loads(result.stdout or "null")


def _source_versions(root: Path) -> tuple[str | None, str | None]:
    version_py = (root / "src/row_bot/version.py").read_text(encoding="utf-8")
    setup_iss = (root / "installer/row_bot_setup.iss").read_text(encoding="utf-8")
    py_match = re.search(r'__version__\s*=\s*"([^"]+)"', version_py)
    iss_match = re.search(r'#define\s+MyAppVersion\s+"([^"]+)"', setup_iss)
    return (py_match.group(1) if py_match else None, iss_match.group(1) if iss_match else None)


def ci_passed(sha: str, repository: str) -> bool:
    runs = _gh("api", f"repos/{repository}/commits/{sha}/check-runs?check_name=ci-ok&per_page=100")
    return any(
        run.get("status") == "completed" and run.get("conclusion") == "success"
        for run in (runs or {}).get("check_runs", [])
    )


def nightly_passed(sha: str) -> bool:
    runs = _gh("run", "list", "--workflow", "nightly.yml", "--commit", sha, "--status", "success",
               "--json", "databaseId", "--limit", "1")
    return bool(runs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--repository", default=os.environ.get("GH_REPO", ""))
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    args = parser.parse_args(argv)
    if not args.repository:
        parser.error("--repository (or GH_REPO) is required")

    versions = _source_versions(Path(args.root))
    if versions != (args.version, args.version):
        print(f"::error::version mismatch: input={args.version!r}, version.py={versions[0]!r}, iss={versions[1]!r}")
        return 1
    try:
        if not ci_passed(args.sha, args.repository):
            print(f"::error::{args.sha} has no successful CI (CI / ci-ok). Wait for CI on this commit, then release it.")
            return 1
        nightly = nightly_passed(args.sha)
    except GateError as error:
        print(f"::error::{error}")
        return 1
    print(f"version {args.version}; CI passed on {args.sha}; nightly {'passed' if nightly else 'has not run'} on it")
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(f"nightly_green={'true' if nightly else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
