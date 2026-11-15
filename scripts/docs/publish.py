"""Publish the docs site: regenerate, build and sync it into ``docs/``, once per release.

Pull requests change the docs' sources (``docs-site/docs``) but not the published site, so open pull
requests never conflict over hundreds of built files. Preparing a release runs this
(``scripts/cut_release.py``), and the release-prep pull request carries the site; run it by hand to
publish a docs fix between releases.

It regenerates the reference pages from the code, validates the docs, builds the site (with its
search index) and syncs it into ``docs/``, then runs the docs tests. It never changes the landing
page: the sync owns only the docs site, and a publish that touched a landing file fails.

Inventory collection imports Row-Bot, so a scratch data folder is used unless ``ROW_BOT_DATA_DIR``
is set: the person's own profile is never read or migrated.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from landing_guard import is_landing  # noqa: E402


def _run(step: str, command: list[str], *, cwd: Path = ROOT, env: dict[str, str]) -> None:
    print(f":: {step}", flush=True)
    result = subprocess.run(command, cwd=cwd, env=env, check=False)  # noqa: S603 - fixed local tooling
    if result.returncode:
        raise SystemExit(f"Publishing the docs stopped at: {step}")


def _changed_landing() -> list[str]:
    result = subprocess.run(["git", "status", "--porcelain", "--", "docs"], cwd=ROOT, capture_output=True,  # noqa: S603,S607
                            text=True, check=False)
    paths = [line[3:].strip().strip('"') for line in result.stdout.splitlines() if line.strip()]
    return [path for path in paths if is_landing(path)]


def main() -> int:
    env = dict(os.environ)
    env.setdefault("ROW_BOT_DATA_DIR", tempfile.mkdtemp(prefix="row-bot-docs-"))
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    npx = shutil.which("npx") or shutil.which("npx.cmd")
    if not npm or not npx:
        raise SystemExit("Publishing the docs needs Node.js (npm) on this computer.")
    before = set(_changed_landing())
    python = sys.executable
    _run("Collect the docs inventory", [python, "scripts/docs/collect_inventory.py"], env=env)
    _run("Generate the reference pages", [python, "scripts/docs/generate_mdx.py"], env=env)
    _run("Generate the LLM exports", [python, "scripts/docs/generate_llms_txt.py"], env=env)
    _run("Validate the docs", [python, "scripts/docs/validate_public_docs.py"], env=env)
    site = ROOT / "docs-site"
    if not (site / "node_modules").is_dir():
        _run("Install the docs site's packages", [npm, "ci", "--no-audit", "--no-fund"], cwd=site, env=env)
    _run("Clear the docs build cache", [npx, "docusaurus", "clear"], cwd=site, env=env)
    _run("Build the docs site", [npm, "run", "build:ci"], cwd=site, env=env)
    if not (site / "build" / "pagefind").is_dir():
        raise SystemExit("The build made no search index (docs-site/build/pagefind): is pagefind blocked here?")
    _run("Sync the site into docs/", [python, "scripts/docs/sync_github_pages.py"], env=env)
    _run("Check the published site", [python, "scripts/docs/sync_github_pages.py", "--check"], env=env)
    touched = sorted(set(_changed_landing()) - before)
    if touched:
        raise SystemExit("The publish changed the landing page, which only changes by hand: " + ", ".join(touched))
    _run("Run the docs tests", [python, "-m", "pytest", "-q", "tests/docs", "tests/marketing", "-p", "no:cacheprovider"],
         env={**env, "ROW_BOT_DOCS_PUBLISH": "1"})
    print("Published the docs site into docs/. Commit it with the release-prep changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
