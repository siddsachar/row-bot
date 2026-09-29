from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@dataclass(frozen=True)
class CommandSpec:
    name: str
    argv: tuple[str, ...]
    env: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {"name": self.name, "argv": list(self.argv), "env": dict(self.env)}

    def display(self) -> str:
        return " ".join(self.argv)


def _cmd(name: str, *argv: str, env: dict[str, str] | None = None) -> CommandSpec:
    return CommandSpec(name=name, argv=tuple(argv), env=env or {})


TEST_ENV = {
    "ROW_BOT_DATA_DIR": str(
        Path(
            os.environ.get(
                "ROW_BOT_MATRIX_DATA_DIR",
                str(REPO_ROOT / ".tmp" / "matrix_row_bot"),
            )
        )
    ),
    "ROW_BOT_TEST_MODE": "1",
}
COVERAGE_ENV = {**TEST_ENV, "COVERAGE_FILE": str(REPO_ROOT / ".tmp" / "coverage" / ".coverage")}

DETERMINISTIC = "not live_provider and not e2e"
# docs.yml owns the docs and marketing tooling tests; they read docs sources, not app code.
APP_LANES = ("tests", "--ignore=tests/docs", "--ignore=tests/marketing")

# One PR browser pass: boot, first run, shell, overlays, a turn's lifecycle, chat,
# providers, restart recovery, the desktop Buddy window and the UI primitives.
BROWSER_SMOKE_SPECS = (
    "bootstrap.spec",
    "setup-first-run.spec",
    "shell-slice1.spec",
    "overlays.spec",
    "unified-lifecycle.spec",
    "conversation-first.spec",
    "providers-parity.spec",
    "unified-restart.spec",
    "buddy-overlay.spec",
    "polish-foundation.spec",
)
BROWSER_RUNNER = ("uv", "run", "python", "tests/browser/client_workspace/run_browser.py", "--engine", "chromium")


def _pytest(name: str, *args: str, marker: str = DETERMINISTIC, env: dict[str, str] = TEST_ENV) -> CommandSpec:
    return _cmd(name, "uv", "run", "python", "-m", "pytest", *args, "-m", marker, "-q", env=env)


COMMANDS: dict[str, CommandSpec] = {
    "lock-check": _cmd("lock-check", "uv", "lock", "--check"),
    "requirements-check": _cmd("requirements-check", "python", "scripts/export_locked_requirements.py", "--check"),
    "sync-test": _cmd("sync-test", "uv", "sync", "--locked", "--all-extras", "--group", "test"),
    "ruff-safety": _cmd(
        "ruff-safety", "uv", "run", "--group", "lint", "ruff", "check", ".",
        "--select", "E9,F63,F7,F82", "--output-format=github",
    ),
    "dependency-requirements": _cmd(
        "dependency-requirements", "uv", "run", "python", "scripts/dependency_requirements.py", env=TEST_ENV,
    ),
    "client-platform-boundaries": _cmd(
        "client-platform-boundaries", "uv", "run", "python", "scripts/check_client_platform_boundaries.py",
        env=TEST_ENV,
    ),
    "client-platform-contracts": _cmd(
        "client-platform-contracts", "uv", "run", "python", "scripts/generate_client_platform_contracts.py", "--check",
        env=TEST_ENV,
    ),
    "runtime-deps": _cmd(
        "runtime-deps", "uv", "run", "python", "scripts/verify_runtime_dependencies.py", "all", env=TEST_ENV,
    ),
    "client-foundation": _cmd("client-foundation", "python", "scripts/run_client_checks.py", env=TEST_ENV),
    # The PR pass: every deterministic app test once, coverage recorded but not
    # gated. CI splits it by file with ROW_BOT_TEST_SHARD=k/N (tests/conftest.py).
    "python": _pytest(
        "python", *APP_LANES, "--cov=src/row_bot", "--cov-report=xml:.tmp/coverage/python.xml",
        marker=f"not slow and {DETERMINISTIC}", env=COVERAGE_ENV,
    ),
    # The nightly pass: the same lanes with the slow tests.
    "python-full": _pytest(
        "python-full", *APP_LANES, "--cov=src/row_bot", "--cov-report=xml:.tmp/coverage/python-full.xml",
        env=COVERAGE_ENV,
    ),
    # OS-sensitive tests; CI runs them on Windows (the shipped Python 3.13) and macOS.
    "platform": _pytest("platform", "tests", marker=f"platform and not slow and {DETERMINISTIC}"),
    "app-smoke": _cmd(
        "app-smoke", "uv", "run", "python", "scripts/smoke_app.py", "--port", "8090", "--timeout", "120",
        env={**TEST_ENV, "ROW_BOT_AUTO_START_OLLAMA": "0"},
    ),
    # The launcher owns its launch secret, so the smoke uses the public probes (B203).
    "launcher-smoke": _cmd(
        "launcher-smoke", "uv", "run", "python", "scripts/smoke_app.py", "--port", "8092", "--timeout", "180",
        "--public-probes", "--", "python", "launcher.py", "--server", "--no-open", "--no-splash", "--no-ollama",
        "--port", "8092",
        env={**TEST_ENV, "ROW_BOT_AUTO_START_OLLAMA": "0"},
    ),
    "contracts": _pytest("contracts", "tests/contracts"),
    "subsystem": _pytest("subsystem", "tests/subsystem"),
    "installer-contracts": _pytest("installer-contracts", "tests/subsystem/installer", "tests/contracts/installers"),
    "docs": _pytest("docs", "tests/docs", "tests/marketing"),
    # Needs a local Chromium: Playwright's (CI) or an installed channel via ROW_BOT_BROWSER_CHANNEL.
    "browser-smoke": _cmd(
        "browser-smoke", *BROWSER_RUNNER, "--timeout", "1800", "--",
        "--project=chromium-desktop", "--project=chromium-buddy-overlay", *BROWSER_SMOKE_SPECS,
        env=TEST_ENV,
    ),
}


QUALITY = (
    "lock-check",
    "requirements-check",
    "ruff-safety",
    "dependency-requirements",
    "client-platform-boundaries",
    "client-platform-contracts",
)

TIER_COMMANDS: dict[str, tuple[str, ...]] = {
    "quality": QUALITY,
    "client-foundation": ("client-foundation",),
    "python": ("python",),
    "platform": ("runtime-deps", "platform", "launcher-smoke"),
    "browser-smoke": ("browser-smoke",),
    "app-smoke": ("app-smoke",),
    # What the Linux PR lane runs, in one local command.
    "pr": (*QUALITY, "client-foundation", "runtime-deps", "python", "app-smoke"),
    "nightly": (*QUALITY, "client-foundation", "runtime-deps", "python-full", "app-smoke"),
    "fast": ("ruff-safety", "client-platform-boundaries", "client-platform-contracts", "contracts"),
    "dependency-integrity": ("lock-check", "requirements-check", "sync-test", "dependency-requirements", "runtime-deps"),
    "contracts": ("contracts",),
    "subsystem": ("subsystem",),
    "installer-contracts": ("installer-contracts",),
    "docs": ("docs",),
}


def _dedupe_commands(names: tuple[str, ...]) -> list[CommandSpec]:
    specs: list[CommandSpec] = []
    seen: set[str] = set()
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        specs.append(COMMANDS[name])
    return specs


def changed_files_from_git(base: str) -> list[str]:
    changed: list[str] = []

    def _append_names(argv: tuple[str, ...]) -> bool:
        result = subprocess.run(
            argv,
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            return False
        for line in result.stdout.splitlines():
            name = line.strip()
            if name and name not in changed:
                changed.append(name)
        return True

    if not _append_names(("git", "diff", "--name-only", f"{base}...HEAD")):
        _append_names(("git", "diff", "--name-only", base))
    _append_names(("git", "diff", "--name-only", "HEAD"))
    _append_names(("git", "ls-files", "--others", "--exclude-standard"))
    return changed


def changed_commands(changed_files: list[str]) -> list[CommandSpec]:
    from tests.helpers.source_test_map import select_tests_for_changes

    selection = select_tests_for_changes(changed_files)
    commands: list[CommandSpec] = []
    if any(path.replace("\\", "/").startswith(("frontend/", "contracts/client-platform/"))
           or path == "scripts/run_client_checks.py" for path in changed_files):
        commands.append(COMMANDS["client-foundation"])
    if any(path in {"pyproject.toml", "uv.lock", "requirements.txt", "scripts/dependency_requirements.py"}
           for path in changed_files):
        commands.append(COMMANDS["dependency-requirements"])
    if selection.test_paths:
        commands.append(_pytest("changed-tests", *selection.test_paths))
    return commands


def commands_for_tier(tier: str, changed_files: list[str] | None = None) -> list[CommandSpec]:
    if tier == "changed":
        return changed_commands(changed_files or [])
    if tier not in TIER_COMMANDS:
        raise KeyError(f"unknown tier: {tier}")
    return _dedupe_commands(TIER_COMMANDS[tier])


def run_commands(commands: list[CommandSpec], *, continue_on_failure: bool) -> int:
    exit_code = 0
    for spec in commands:
        print(f":: {spec.name}: {spec.display()}", flush=True)
        env = {**os.environ, **spec.env}
        result = subprocess.run(spec.argv, cwd=REPO_ROOT, env=env, check=False)
        if result.returncode != 0:
            exit_code = result.returncode
            if not continue_on_failure:
                return exit_code
    return exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Row-Bot's explicit local/CI test matrix tiers.")
    parser.add_argument(
        "tier",
        choices=sorted([*TIER_COMMANDS.keys(), "changed"]),
        help="Matrix tier to run.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print commands without executing them.")
    parser.add_argument("--json-plan", action="store_true", help="Print the command plan as JSON.")
    parser.add_argument("--continue-on-failure", action="store_true", help="Run remaining commands after a failure.")
    parser.add_argument("--base", default="origin/main", help="Git base for the changed tier.")
    parser.add_argument("--changed-file", action="append", default=[], help="Changed file path for deterministic tests.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    changed_files = args.changed_file or (changed_files_from_git(args.base) if args.tier == "changed" else [])
    commands = commands_for_tier(args.tier, changed_files=changed_files)

    if args.json_plan:
        print(json.dumps({"tier": args.tier, "commands": [spec.as_dict() for spec in commands]}, indent=2))
    elif args.dry_run:
        for spec in commands:
            print(f":: {spec.name}: {spec.display()}")

    if args.dry_run:
        return 0
    return run_commands(commands, continue_on_failure=args.continue_on_failure)


if __name__ == "__main__":
    raise SystemExit(main())
