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
    "bootstrap",
    "setup-first-run",
    "shell",
    "overlays",
    "unified-lifecycle",
    "conversation-first",
    "settings-providers",
    "unified-restart",
    "buddy-overlay",
    "polish-foundation",
)
# Nightly at desktop width, in three fixture runs (each run starts a fresh backend).
BROWSER_NIGHTLY_SPECS = (
    "capability-surfaces", "conversation-layout", "media", "message-content",
    "navigation", "panels", "persistence", "polish-visual", "resources",
    "settings-models", "settings-routes", "settings", "sidebar", "suggestions",
    "theme", "visual-alignment", "voice", "workflows",
)
BROWSER_UNIFIED_SPECS = (
    "unified-history", "unified-panels", "unified-quality", "unified-recovery",
    "unified-resources", "unified-waiting",
)
# Specs that run only in their own windows (auth states, compact sizes, offline, PWA, remote).
BROWSER_DEDICATED_PROJECTS = tuple(f"--project=chromium-{name}" for name in (
    "auth-expired", "auth-revoked", "auth-unauthorized", "compact-phone", "compact-tablet", "compact-narrow",
    "offline-reconnect", "pwa-update", "remote-resource",
))
# Nightly at phone width: the shell, settings and conversation surfaces.
BROWSER_PHONE_SPECS = (
    "capability-surfaces", "conversation-layout", "message-content", "overlays",
    "polish-foundation", "polish-visual", "settings-models", "settings-providers",
    "settings-routes", "settings", "shell", "sidebar", "theme",
    "unified-panels", "unified-quality", "visual-alignment",
)
# Performance budgets and pixel baselines (recorded on Windows): a quiet local machine only.
BROWSER_BUDGET_SPECS = ("unified-startup", "unified-memory", "unified-performance", "polish-snapshots")


def _specs(*names: str) -> tuple[str, ...]:
    # Playwright matches file filters as regular expressions against the path;
    # "/name\.spec\.ts" selects exactly that file (not "unified-<name>.spec.ts").
    return tuple(rf"/{name}\.spec\.ts" for name in names)


def _browser(name: str, *playwright_args: str, engine: str = "chromium") -> CommandSpec:
    # Needs a local browser: Playwright's own (CI) or, for Chromium, an installed
    # channel named by ROW_BOT_BROWSER_CHANNEL (msedge on the maintainer's machine).
    return _cmd(name, "uv", "run", "python", "tests/browser/client_workspace/run_browser.py", "--engine", engine,
                "--timeout", "7200", "--", *playwright_args, env=TEST_ENV)


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
    # The nightly pass without coverage (Windows and macOS nightly jobs).
    "deterministic": _pytest("deterministic", *APP_LANES),
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
    "browser-smoke": _browser(
        "browser-smoke", "--project=chromium-desktop", "--project=chromium-buddy-overlay", *_specs(*BROWSER_SMOKE_SPECS),
    ),
    "browser-nightly-desktop": _browser("browser-nightly-desktop", "--project=chromium-desktop", *_specs(*BROWSER_NIGHTLY_SPECS)),
    "browser-nightly-unified": _browser("browser-nightly-unified", "--project=chromium-desktop", *_specs(*BROWSER_UNIFIED_SPECS)),
    "browser-nightly-dedicated": _browser("browser-nightly-dedicated", *BROWSER_DEDICATED_PROJECTS),
    "browser-nightly-phone": _browser("browser-nightly-phone", "--project=chromium-phone", *_specs(*BROWSER_PHONE_SPECS)),
    "browser-firefox": _browser(
        "browser-firefox", "--project=firefox-desktop", *_specs(*(s for s in BROWSER_SMOKE_SPECS if s != "buddy-overlay")),
        engine="firefox",
    ),
    "browser-webkit": _browser(
        "browser-webkit", "--project=webkit-desktop", *_specs(*(s for s in BROWSER_SMOKE_SPECS if s != "buddy-overlay")),
        engine="webkit",
    ),
    "browser-budgets": _browser("browser-budgets", "--project=chromium-desktop", *_specs(*BROWSER_BUDGET_SPECS)),
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
    "browser-nightly": (
        "browser-nightly-desktop", "browser-nightly-unified", "browser-nightly-dedicated", "browser-nightly-phone",
    ),
    "browser-firefox": ("browser-firefox",),
    "browser-webkit": ("browser-webkit",),
    "browser-budgets": ("browser-budgets",),
    "app-smoke": ("app-smoke",),
    # What the Linux PR lane runs, in one local command.
    "pr": (*QUALITY, "client-foundation", "runtime-deps", "python", "app-smoke"),
    "nightly": (*QUALITY, "client-foundation", "runtime-deps", "python-full", "app-smoke"),
    "deterministic": ("deterministic",),
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


def changed_test_paths(changed_files: list[str], root: Path = REPO_ROOT) -> list[str]:
    """The tests for changed files, by convention: changed test files themselves;
    for `src/row_bot/<package>/...` the `tests/<lane>/<package>` folders; for
    `src/row_bot/<module>.py` the `test_<module>*.py` files. A local shortcut:
    the PR lane runs everything."""
    selected: list[str] = []
    for name in (path.replace("\\", "/") for path in changed_files):
        parts = name.split("/")
        if parts[0] == "tests" and parts[-1].startswith("test_") and name.endswith(".py"):
            candidates = [root / name]
        elif name.startswith("src/row_bot/") and len(parts) > 3:
            candidates = [root / "tests" / lane / parts[2] for lane in ("contracts", "subsystem", "integration")]
        elif name.startswith("src/row_bot/") and name.endswith(".py"):
            candidates = sorted((root / "tests").rglob(f"test_{Path(name).stem}*.py"))
        else:
            candidates = []
        for candidate in candidates:
            relative = candidate.relative_to(root).as_posix()
            if candidate.exists() and relative not in selected:
                selected.append(relative)
    return selected


def changed_commands(changed_files: list[str]) -> list[CommandSpec]:
    commands: list[CommandSpec] = []
    if any(path.replace("\\", "/").startswith(("frontend/", "contracts/client-platform/"))
           or path == "scripts/run_client_checks.py" for path in changed_files):
        commands.append(COMMANDS["client-foundation"])
    if any(path in {"pyproject.toml", "uv.lock", "requirements.txt", "scripts/dependency_requirements.py"}
           for path in changed_files):
        commands.append(COMMANDS["dependency-requirements"])
    if paths := changed_test_paths(changed_files):
        commands.append(_pytest("changed-tests", *paths))
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
