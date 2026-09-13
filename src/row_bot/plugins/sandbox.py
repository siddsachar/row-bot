"""Plugin dependency safety — core dependency protection.

Explicit preparation resolves and installs hash-pinned wheels into a private
plugin environment. Host constraints are preserved; there is no host fallback.
"""

from __future__ import annotations

import logging
import hashlib
import json
import pathlib
import os
import stat
import uuid
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

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
    # Installation is an authority boundary: an updater or repair may have
    # changed the environment since a previous Plugin Center read.

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


@dataclass(frozen=True)
class DependencyPlan:
    """One target-bound, wheel-only resolver result. Never a host install plan."""

    environment: str
    target_identity: tuple[int, ...]
    requirements: tuple[str, ...]
    constraints: tuple[tuple[str, str], ...]
    distributions: tuple[tuple[str, str, str], ...]


class EnvironmentError(ValueError):
    """An environment preparation failure with a safe, stable code."""


_MAX_REPORT_BYTES = 8 * 1024 * 1024
_MAX_REQUIREMENTS = 128
_MAX_DISTRIBUTIONS = 512


def _requirement_values(values: list[str]) -> tuple[str, ...]:
    if type(values) is not list or len(values) > _MAX_REQUIREMENTS:
        raise EnvironmentError("invalid_requirements")
    result = []
    for value in values:
        if type(value) is not str or not value or len(value) > 2048:
            raise EnvironmentError("invalid_requirements")
        req = Requirement(value)
        if req.url:
            raise EnvironmentError("direct_reference_unavailable")
        result.append(str(req))
    return tuple(sorted(set(result)))


def _no_link(path: pathlib.Path) -> os.stat_result:
    value = path.lstat()
    if stat.S_ISLNK(value.st_mode) or getattr(value, "st_file_attributes", 0) & 0x400:
        raise EnvironmentError("environment_path_invalid")
    return value


def _checked_path(root: pathlib.Path, path: pathlib.Path) -> os.stat_result:
    relative = path.relative_to(root)
    current = root
    value = _no_link(current)
    for part in relative.parts:
        current /= part
        value = _no_link(current)
    return value


def _interpreter_path(environment: pathlib.Path) -> pathlib.Path:
    return environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _candidate_location(environment: pathlib.Path) -> pathlib.Path:
    from row_bot.data_paths import get_row_bot_data_dir

    environment = environment.absolute()
    relative = environment.relative_to(get_row_bot_data_dir(create=False).absolute() / "plugin_environments")
    if len(relative.parts) != 3 or relative.parts[-1] != "environment" or not re.fullmatch(r"[a-z][a-z0-9-]{1,63}", relative.parts[0]) or str(uuid.UUID(relative.parts[1])) != relative.parts[1]:
        raise EnvironmentError("environment_path_invalid")
    host = pathlib.Path(sys.prefix).resolve()
    if environment == host or environment.is_relative_to(host) or host.is_relative_to(environment):
        raise EnvironmentError("host_environment_blocked")
    return environment


def _target(environment: pathlib.Path) -> tuple[pathlib.Path, tuple[int, ...]]:
    environment = _candidate_location(environment)
    # Every component is checked before resolution, including junctions on Windows.
    _checked_path(pathlib.Path(environment.anchor), environment)
    root = environment.resolve(strict=True)
    host = pathlib.Path(sys.prefix).resolve()
    if root == host or root.is_relative_to(host) or host.is_relative_to(root):
        raise EnvironmentError("host_environment_blocked")
    python = _interpreter_path(root)
    identity = _checked_path(root, python)
    config_path = root / "pyvenv.cfg"
    _checked_path(root, config_path)
    python_digest = _content_identity(root, python, 256 * 1024 * 1024)
    config_bytes, config_digest = _content_identity(root, config_path, 16384, retain=True)
    config = {}
    for line in config_bytes.decode("utf-8").splitlines():
        if "=" in line:
            name, value = line.split("=", 1)
            name = name.strip().lower()
            if name in config:
                raise EnvironmentError("environment_path_invalid")
            config[name] = value.strip()
    if config.get("include-system-site-packages", "").lower() != "false":
        raise EnvironmentError("host_environment_blocked")
    if not stat.S_ISREG(identity.st_mode) or python.samefile(sys.executable):
        raise EnvironmentError("host_environment_blocked")
    root_stat = root.stat()
    config_stat = config_path.stat()
    return python, (root_stat.st_dev, root_stat.st_ino, identity.st_dev, identity.st_ino,
                    identity.st_size, identity.st_mtime_ns, config_stat.st_size, config_stat.st_mtime_ns,
                    python_digest, config_digest)


def _content_identity(root: pathlib.Path, path: pathlib.Path, limit: int,
                      *, retain: bool = False) -> int | tuple[bytes, int]:
    """Bind bounded regular-file bytes to the checked path and opened handle."""
    before = _checked_path(root, path)
    def signature(value: os.stat_result) -> tuple[int, ...]:
        # Windows path stat and fstat expose different ctime semantics.
        return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
    if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= limit:
        raise EnvironmentError("environment_path_invalid")
    digest = hashlib.sha256()
    chunks = []
    count = 0
    with path.open("rb") as source:
        opened = os.fstat(source.fileno())
        if signature(opened) != signature(before):
            raise EnvironmentError("environment_path_invalid")
        while chunk := source.read(min(1024 * 1024, limit + 1 - count)):
            count += len(chunk)
            if count > limit:
                raise EnvironmentError("environment_path_invalid")
            digest.update(chunk)
            if retain:
                chunks.append(chunk)
        if (count != before.st_size
                or signature(os.fstat(source.fileno())) != signature(before)
                or os.fstat(source.fileno()).st_ctime_ns != opened.st_ctime_ns
                or signature(_checked_path(root, path)) != signature(before)):
            raise EnvironmentError("environment_path_invalid")
    identity = int.from_bytes(digest.digest(), "big")
    return (b"".join(chunks), identity) if retain else identity


def _run(argv: list[str], *, cwd: pathlib.Path, timeout: int) -> None:
    """Explicit preparation only: no inherited secrets or pip/Python options."""
    from row_bot.cancellation import current_cancellation_scope
    from row_bot.process_cancellation import request_process_stop

    scope = current_cancellation_scope()
    if scope is not None and scope.is_cancelled():
        raise EnvironmentError("cancelled")
    allowed = ("SystemRoot", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "TMPDIR")
    env = {name: os.environ[name] for name in allowed if name in os.environ}
    env.update({
        "PIP_CONFIG_FILE": os.devnull, "PYTHONNOUSERSITE": "1",
        "HOME": str(cwd), "USERPROFILE": str(cwd),
        "APPDATA": str(cwd), "LOCALAPPDATA": str(cwd),
        "NETRC": str(cwd / ".disabled-netrc"),
    })
    options: dict[str, Any] = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if os.name == "nt" else {"start_new_session": True}
    process = subprocess.Popen(
        argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False, **options,
    )
    try:
        unregister = scope.register(lambda: request_process_stop(process), "plugin.environment.stop") if scope else None
        try:
            try:
                code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                request_process_stop(process)
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
                raise EnvironmentError("environment_process_timeout") from None
            if scope is not None and scope.is_cancelled():
                raise EnvironmentError("cancelled")
            if code:
                raise EnvironmentError("environment_process_failed")
        finally:
            if unregister is not None:
                unregister()
    finally:
        # Popen's context manager waits without a bound on exceptional exit.
        # Keep final cleanup bounded even when a native terminate call fails.
        if process.poll() is None:
            request_process_stop(process)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)


def create_environment(environment: pathlib.Path) -> None:
    """Create a fresh fixed-location candidate, never a host or shared target."""
    environment = _candidate_location(environment)
    _checked_path(pathlib.Path(environment.anchor), environment.parent)
    if environment.exists() or environment.is_symlink():
        raise EnvironmentError("environment_already_exists")
    _run([sys.executable, "-I", "-m", "venv", "--copies", str(environment)], cwd=environment.parent, timeout=120)
    _target(environment)


def _constraint_text(pins: dict[str, str]) -> str:
    return "".join(f"{name}=={version}\n" for name, version in sorted(pins.items()))


def _read_report(path: pathlib.Path) -> dict[str, Any]:
    if not stat.S_ISREG(_no_link(path).st_mode):
        raise EnvironmentError("dependency_plan_unverified")
    with path.open("rb") as stream:
        raw = stream.read(_MAX_REPORT_BYTES + 1)
    if len(raw) > _MAX_REPORT_BYTES:
        raise EnvironmentError("dependency_plan_unverified")
    value = json.loads(raw)
    if type(value) is not dict:
        raise EnvironmentError("dependency_plan_unverified")
    return value


def _validated_distributions(report: dict[str, Any], requirements: tuple[str, ...], core: dict[str, str]) -> tuple[tuple[str, str, str], ...]:
    if report.get("version") != "1" or type(report.get("install")) is not list or len(report["install"]) > _MAX_DISTRIBUTIONS:
        raise EnvironmentError("dependency_plan_unverified")
    versions: dict[str, str] = {}
    hashes: dict[str, str] = {}
    edges: dict[str, list[Requirement]] = {}
    for item in report["install"]:
        metadata = item["metadata"]
        name = canonicalize_name(metadata["name"])
        if type(metadata["name"]) is not str or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) or name in versions:
            raise EnvironmentError("dependency_plan_unverified")
        version = str(Version(metadata["version"]))
        if name in core and Version(core[name]) != Version(version):
            raise EnvironmentError("dependency_core_conflict")
        download = item["download_info"]
        url = urlparse(download["url"])
        digest = download["archive_info"]["hashes"]["sha256"]
        if url.scheme != "https" or not url.hostname or url.username or url.password or not url.path.lower().endswith(".whl"):
            raise EnvironmentError("dependency_source_unavailable")
        if type(digest) is not str or not re.fullmatch(r"[a-fA-F0-9]{64}", digest):
            raise EnvironmentError("dependency_plan_unverified")
        values = metadata.get("requires_dist", [])
        if type(values) is not list or len(values) > 1024:
            raise EnvironmentError("dependency_plan_unverified")
        edges[name] = [Requirement(value) for value in values]
        if any(req.url for req in edges[name]):
            raise EnvironmentError("direct_reference_unavailable")
        versions[name], hashes[name] = version, digest.lower()
    # Validate closure ourselves: an exit-zero resolver report can omit a request
    # or transitive edge. Propagate requested extras until the closure stabilizes.
    active: dict[str, set[str]] = {}
    pending = [(Requirement(value), {""}) for value in requirements]
    while pending:
        req, extras = pending.pop()
        if req.marker and not any(req.marker.evaluate({"extra": extra}) for extra in extras):
            continue
        name = canonicalize_name(req.name)
        if name not in versions or not req.specifier.contains(versions[name], prereleases=True):
            raise EnvironmentError("dependency_plan_incomplete")
        requested = {"", *req.extras}
        previous = active.get(name, set())
        if name in active and requested <= previous:
            continue
        active[name] = previous | requested
        pending.extend((edge, active[name]) for edge in edges[name])
    if set(active) != set(versions):
        raise EnvironmentError("dependency_plan_unverified")
    return tuple((name, versions[name], hashes[name]) for name in sorted(versions))


def _resolve_dependencies(plugin_deps: list[str], environment: pathlib.Path) -> tuple[DepCheckResult, DependencyPlan | None]:
    """Resolve exactly once in the empty isolated target and validate its plan."""
    try:
        values = _requirement_values(plugin_deps)
        python, identity = _target(environment)
        core = {canonicalize_name(k): str(Version(v)) for k, v in _get_core_requirements().items()}
        if not core:
            raise EnvironmentError("host_constraints_unavailable")
        for value in values:
            req = Requirement(value)
            name = canonicalize_name(req.name)
            if name in core and (not req.marker or req.marker.evaluate()) and not req.specifier.contains(core[name], prereleases=True):
                raise EnvironmentError("dependency_core_conflict")
        with tempfile.TemporaryDirectory(prefix="rb-plan-", dir=environment.parent) as tmp:
            root = pathlib.Path(tmp)
            constraint, report = root / "constraints.txt", root / "report.json"
            constraint.write_text(_constraint_text(core), encoding="utf-8")
            if values:
                _run([str(python), "-I", "-m", "pip", "--isolated", "install", "--dry-run", "--ignore-installed", "--no-input", "--keyring-provider", "disabled", "--disable-pip-version-check", "--no-cache-dir", "--only-binary=:all:", "--constraint", str(constraint), "--report", str(report), *values], cwd=root, timeout=60)
                distributions = _validated_distributions(_read_report(report), values, core)
            else:
                distributions = ()
        if _target(environment)[1] != identity:
            raise EnvironmentError("environment_changed")
        return DepCheckResult(True, [], []), DependencyPlan(str(environment.absolute()), identity, values, tuple(sorted(core.items())), distributions)
    except Exception as exc:
        code = str(exc) if isinstance(exc, EnvironmentError) else "dependency_plan_unverified"
        return DepCheckResult(False, [code], []), None


def check_dependencies(plugin_deps: list[str], *, environment: pathlib.Path | None = None) -> DepCheckResult:
    """Explicit isolated compatibility check; never provision or probe the host."""
    if environment is None:
        return DepCheckResult(False, ["isolated_environment_required"], [])
    return _resolve_dependencies(plugin_deps, environment)[0]


def _installed_versions(environment: pathlib.Path) -> dict[str, str]:
    """Read distribution metadata without importing packages or running .pth code."""
    from importlib.metadata import distributions

    site = environment / ("Lib/site-packages" if os.name == "nt" else f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages")
    _checked_path(environment, site)
    paths = []
    for path in site.iterdir():
        if len(paths) >= 8192:
            raise EnvironmentError("environment_verification_failed")
        paths.append(path)
    found = {}
    for path in paths:
        _no_link(path)
        if path.name.endswith((".dist-info", ".egg-info")):
            for metadata in path.iterdir() if path.is_dir() else [path]:
                _no_link(metadata)
                if metadata.is_file() and metadata.stat().st_size > _MAX_REPORT_BYTES:
                    raise EnvironmentError("environment_verification_failed")
    for dist in distributions(path=[str(site)]):
        name = canonicalize_name(dist.metadata["Name"])
        if name in found or len(found) > _MAX_DISTRIBUTIONS + 8:
            raise EnvironmentError("environment_verification_failed")
        found[name] = str(Version(dist.version))
    return found


def install_dependencies(plugin_deps: list[str], *, environment: pathlib.Path | None = None) -> tuple[bool, str]:
    """Explicit hash-pinned install into an isolated candidate; no host fallback."""
    if environment is None:
        return False, "isolated_environment_required"
    try:
        from row_bot.plugins import state as plugin_state

        location = _candidate_location(environment)
        saved = plugin_state.get_plugin_environment_state(location.parent.parent.name)
        receipt = saved.get("operations", {}).get(location.parent.name)
        if receipt is not None and (type(receipt) is not dict or receipt.get("stage") != "preparing"):
            return False, "environment_already_published"
        if saved.get("active_operation_id") == location.parent.name:
            return False, "environment_already_published"
    except Exception:
        return False, "environment_path_invalid"
    check, plan = _resolve_dependencies(plugin_deps, environment)
    if not check.ok or plan is None:
        return False, check.conflicts[0]
    try:
        python, identity = _target(environment)
        if identity != plan.target_identity:
            raise EnvironmentError("environment_changed")
        current_core = {canonicalize_name(k): str(Version(v)) for k, v in _get_core_requirements().items()}
        if tuple(sorted(current_core.items())) != plan.constraints:
            raise EnvironmentError("dependency_core_conflict")
        baseline = _installed_versions(environment)
        with tempfile.TemporaryDirectory(prefix="rb-install-", dir=environment.parent) as tmp:
            root = pathlib.Path(tmp)
            constraints = root / "constraints.txt"
            constraints.write_text(_constraint_text(dict(plan.constraints) | {name: version for name, version, _ in plan.distributions}), encoding="utf-8")
            requirements = root / "requirements.txt"
            requirements.write_text("".join(f"{name}=={version} --hash=sha256:{digest}\n" for name, version, digest in plan.distributions), encoding="utf-8")
            if plan.distributions:
                _run([str(python), "-I", "-m", "pip", "--isolated", "install", "--no-input", "--keyring-provider", "disabled", "--disable-pip-version-check", "--no-cache-dir", "--only-binary=:all:", "--no-deps", "--require-hashes", "--constraint", str(constraints), "--requirement", str(requirements)], cwd=root, timeout=300)
        if _target(environment)[1] != identity:
            raise EnvironmentError("environment_changed")
        expected = baseline | {name: version for name, version, _ in plan.distributions}
        if _installed_versions(environment) != expected:
            raise EnvironmentError("environment_verification_failed")
        return True, "environment_dependencies_verified"
    except Exception as exc:
        return False, str(exc) if isinstance(exc, EnvironmentError) else "environment_verification_failed"


def _reset():
    global _core_requirements
    _core_requirements = None
