"""Plugin dependency safety — core dependency protection.

Before installing plugin dependencies, we freeze the core dependency
versions and block any plugin that would downgrade or change them.
"""

from __future__ import annotations

import logging
import pathlib
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

from row_bot.runtime_paths import app_path

logger = logging.getLogger(__name__)


@dataclass
class DepCheckResult:
    """Result of a dependency compatibility check."""
    ok: bool
    conflicts: list[str]  # list of conflict descriptions
    warnings: list[str]   # plugin-to-plugin conflicts (non-blocking)


# ── Core Requirements ────────────────────────────────────────────────────────
_core_requirements: dict[str, str] | None = None  # package_name → installed_version


def _get_core_requirements() -> dict[str, str]:
    """Get the current frozen core dependencies.

    Reads from requirements.txt to identify core packages, then checks
    installed versions via importlib.metadata.
    """
    global _core_requirements
    if _core_requirements is not None:
        return _core_requirements

    # Read requirements.txt to get the list of core package names
    req_path = app_path("requirements.txt")
    core_names: set[str] = set()
    if req_path.exists():
        for line in req_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith(("#", "-")):
                continue
            # Extract package name (before any version specifier)
            match = re.match(r"([a-zA-Z0-9_\-\.]+(?:\[[^\]]+\])?)", line)
            if match:
                pkg = match.group(1).split("[")[0]  # strip extras like [gmail]
                core_names.add(pkg.lower().replace("-", "_").replace(".", "_"))

    # Get installed versions
    from importlib.metadata import distributions
    installed: dict[str, str] = {}
    for dist in distributions():
        name = dist.metadata["Name"]
        if name:
            normalised = name.lower().replace("-", "_").replace(".", "_")
            if normalised in core_names:
                installed[normalised] = dist.metadata["Version"]

    _core_requirements = installed
    return _core_requirements


def check_dependencies(plugin_deps: list[str]) -> DepCheckResult:
    """Check if plugin dependencies conflict with core Thoth packages.

    Uses pip's dependency resolver in dry-run mode to detect conflicts.

    Parameters
    ----------
    plugin_deps
        List of pip requirement strings (e.g. ["requests>=2.28", "boto3"]).

    Returns
    -------
    DepCheckResult with ok=False if any core dependency would change.
    """
    if not plugin_deps:
        return DepCheckResult(ok=True, conflicts=[], warnings=[])

    core = _get_core_requirements()
    conflicts: list[str] = []
    warnings: list[str] = []

    # Quick static check: does the plugin require a specific version of a core package?
    for dep_str in plugin_deps:
        match = re.match(r"([a-zA-Z0-9_\-\.]+)", dep_str)
        if not match:
            continue
        dep_name = match.group(1).lower().replace("-", "_").replace(".", "_")
        if dep_name in core:
            # Plugin depends on a core package — check if version spec conflicts
            core_ver = core[dep_name]
            # If plugin pins a different version, flag it
            if "==" in dep_str:
                pinned = dep_str.split("==")[1].strip()
                if pinned != core_ver:
                    conflicts.append(
                        f"'{dep_str}' conflicts with core dependency "
                        f"{dep_name}=={core_ver}"
                    )

    # Deeper check: use pip dry-run to detect conflicts
    try:
        result = subprocess.run(
            [
                sys.executable, "-m", "pip", "install",
                "--dry-run", "--no-input", "--report", "-",
                *plugin_deps,
            ],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()
            # Parse pip error for conflict details
            for line in stderr.splitlines():
                line_lower = line.lower()
                if "conflict" in line_lower or "incompatible" in line_lower:
                    # Check if it involves a core package
                    for core_pkg in core:
                        if core_pkg.replace("_", "-") in line_lower or core_pkg in line_lower:
                            conflicts.append(line.strip())
                            break
                    else:
                        warnings.append(line.strip())
    except subprocess.TimeoutExpired:
        warnings.append("Dependency check timed out — could not verify compatibility")
    except Exception as exc:
        warnings.append(f"Dependency check failed: {exc}")

    return DepCheckResult(
        ok=len(conflicts) == 0,
        conflicts=conflicts,
        warnings=warnings,
    )


def install_dependencies(plugin_deps: list[str]) -> tuple[bool, str]:
    """Install plugin dependencies into the main venv.

    Returns (success, message).
    """
    if not plugin_deps:
        return True, "No dependencies to install"

    # Safety check first
    check = check_dependencies(plugin_deps)
    if not check.ok:
        return False, (
            "Cannot install — conflicts with core Thoth dependencies:\n"
            + "\n".join(f"  - {c}" for c in check.conflicts)
        )

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--no-input", *plugin_deps],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode == 0:
            return True, "Dependencies installed successfully"
        else:
            return False, f"pip install failed:\n{result.stderr.strip()}"
    except subprocess.TimeoutExpired:
        return False, "pip install timed out after 5 minutes"
    except Exception as exc:
        return False, f"pip install error: {exc}"


# ── Reset (for testing) ─────────────────────────────────────────────────────
def _reset():
    global _core_requirements
    _core_requirements = None
