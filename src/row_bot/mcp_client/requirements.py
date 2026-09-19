"""MCP server runtime requirement detection and managed installs."""

from __future__ import annotations

import hashlib
import io
import contextlib
import json
import os
import platform
import re
import subprocess as subprocess  # Retained legacy installer injection seam.
import shutil
import tarfile
import tempfile
import threading
import time
import urllib.request
import urllib.parse
import uuid
import zipfile
import stat
import struct
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from row_bot.mcp_client.config import DATA_DIR
from row_bot.mcp_client.logging import log_event

RUNTIMES_DIR = DATA_DIR / "runtimes"
ARCHIVE_BYTE_LIMIT = 256 * 1024 * 1024
EXTRACTED_BYTE_LIMIT = 1024 * 1024 * 1024
ARCHIVE_ENTRY_LIMIT = 20000
METADATA_BYTE_LIMIT = 2 * 1024 * 1024
_INSTALL_LOCK = threading.RLock()


@dataclass(frozen=True)
class ArchiveRuntimePlan:
    """Exact reviewed archive inputs; constructing/reading a plan has no effects."""

    schema_version: int
    runtime_id: str
    version: str
    url: str
    sha256: str
    size_bytes: int
    system: str
    arch: str
    asset_name: str
    executable_candidates: tuple[str, ...]
    preserve_top_level_directory: bool
    expected_revision: str
    internal_links: bool = False


def _safe_component(value: str) -> str:
    if (type(value) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value)
            or value.endswith((".", " ")) or value.split(".")[0].upper() in
            {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}):
        raise RuntimeError("Invalid managed runtime path component")
    return value


def _safe_url(value: str) -> str:
    if type(value) is not str or len(value) > 4096 or any(ord(c) < 33 for c in value):
        raise RuntimeError("Invalid managed runtime URL")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise RuntimeError("Managed runtime downloads require an explicit HTTPS URL")
    return value


class _Directory:
    """An already-held runtime directory; POSIX effects use its descriptor."""

    def __init__(self, path: Path, descriptor: int | None):
        self.path, self.fd = path, descriptor

    def leaf(self, name):
        return name if self.fd is not None else self.path / name

    def stat(self, name):
        return os.stat(self.leaf(name), dir_fd=self.fd, follow_symlinks=False)

    @contextlib.contextmanager
    def child(self, name, *, create=False):
        from row_bot.file_ownership import directory_identity, guard_directory
        _archive_parts(name)
        if "/" in name:
            raise RuntimeError("Invalid managed directory component")
        if create:
            try:
                os.mkdir(self.leaf(name), mode=0o700, dir_fd=self.fd)
            except FileExistsError:
                pass
        if self.fd is not None:
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.fd)
            try:
                yield _Directory(self.path / name, child)
            finally:
                os.close(child)
        else:
            path = self.path / name
            with guard_directory(path, directory_identity(path, parent=True)):
                yield _Directory(path, None)

    def read(self, name, maximum):
        descriptor = os.open(self.leaf(name), os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0), dir_fd=self.fd)
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_size > maximum
                    or getattr(before, "st_file_attributes", 0) & 0x400):
                raise RuntimeError("Invalid managed runtime file")
            data = stream.read(maximum + 1)
            after, named = os.fstat(stream.fileno()), self.stat(name)
            def key(value):
                return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns,
                        value.st_ctime_ns if os.name != "nt" else None)
            if len(data) > maximum or key(before) != key(after) or key(after) != key(named):
                raise RuntimeError("Managed runtime file changed during validation")
            return data

    def rename(self, source, destination, target=None):
        from row_bot.developer.edits import _rename_edit_no_replace
        target = target or self
        _rename_edit_no_replace(self.leaf(source), target.leaf(destination), src_dir_fd=self.fd, dst_dir_fd=target.fd)


@contextlib.contextmanager
def _owned_directory(path: Path, *, create=False):
    from row_bot.file_ownership import directory_identity, guard_directory
    path = path.absolute()
    ancestor, missing = path, []
    while not os.path.lexists(ancestor):
        if not create:
            raise FileNotFoundError(path)
        missing.append(ancestor.name)
        ancestor = ancestor.parent
    with contextlib.ExitStack() as stack:
        descriptor = stack.enter_context(guard_directory(ancestor, directory_identity(ancestor, parent=True)))
        current = _Directory(ancestor, descriptor)
        for part in reversed(missing):
            current = stack.enter_context(current.child(part, create=True))
        yield current


def _manifest_bytes(runtime_id):
    _safe_component(runtime_id)
    try:
        with _owned_directory(RUNTIMES_DIR / runtime_id) as directory:
            return directory.read("manifest.json", METADATA_BYTE_LIMIT)
    except FileNotFoundError:
        return b""


def _manifest_json(data):
    def fields(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate runtime manifest field")
            result[key] = value
        return result
    def constant(_value):
        raise ValueError("Non-finite runtime manifest value")
    return json.loads(data, object_pairs_hook=fields, parse_constant=constant)


def runtime_install_revision(runtime_id: str) -> str:
    """Saved installation identity only; never discover, create or execute."""
    from row_bot.file_ownership import directory_identity, guard_directory
    _safe_component(runtime_id)
    target = (RUNTIMES_DIR / runtime_id).absolute()
    ancestor = target
    while not os.path.lexists(ancestor):
        ancestor = ancestor.parent
    identity = directory_identity(ancestor, parent=True)
    with guard_directory(ancestor, identity):
        data = _manifest_bytes(runtime_id)
        file_identity = None
        try:
            with _owned_directory(target) as directory:
                info = directory.stat("manifest.json")
                file_identity = [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
                                 info.st_mode, getattr(info, "st_file_attributes", 0)]
        except FileNotFoundError:
            pass
        if file_identity is not None and not data:
            raise RuntimeError("Invalid empty managed runtime manifest")
    if data:
        document = _manifest_json(data)
        if type(document) is not dict:
            raise RuntimeError("Invalid managed runtime manifest")
    return hashlib.sha256(json.dumps([str(target), str(ancestor), identity, file_identity,
        hashlib.sha256(data).hexdigest()]).encode()).hexdigest()


def make_archive_runtime_plan(runtime_id: str, *, version: str, url: str, sha256: str,
                              size_bytes: int, asset_name: str, executable_candidates: tuple[str, ...],
                              preserve_top_level_directory: bool = False) -> ArchiveRuntimePlan:
    """Validate supplied exact pins without downloading or probing a runtime."""
    _safe_component(runtime_id)
    _safe_component(version)
    if version.lower() in {"latest", "lts", "stable"}:
        raise RuntimeError("An exact runtime version is required")
    _safe_component(asset_name)
    _safe_url(url)
    if (type(sha256) is not str or not re.fullmatch(r"[a-fA-F0-9]{64}", sha256)
            or type(size_bytes) is not int or not 0 < size_bytes <= ARCHIVE_BYTE_LIMIT
            or type(preserve_top_level_directory) is not bool
            or type(executable_candidates) is not tuple or not 1 <= len(executable_candidates) <= 8):
        raise RuntimeError("Invalid pinned runtime archive plan")
    for candidate in executable_candidates:
        _archive_parts(candidate)
    return ArchiveRuntimePlan(1, runtime_id, version, url, sha256.lower(), size_bytes,
        platform.system().lower(), _system_arch(), asset_name, executable_candidates,
        preserve_top_level_directory, runtime_install_revision(runtime_id), runtime_id == "node" and platform.system().lower() != "windows")


@dataclass(frozen=True)
class RuntimeRequirement:
    id: str
    label: str
    commands: tuple[str, ...]
    managed: bool = False
    setup_url: str = ""
    install_hint: str = ""
    source: str = "inferred"


@dataclass(frozen=True)
class RuntimeCheck:
    requirement: RuntimeRequirement
    available: bool
    source: str = "missing"
    paths: dict[str, str] = field(default_factory=dict)
    missing_commands: tuple[str, ...] = ()
    message: str = ""

    @property
    def installable(self) -> bool:
        return self.requirement.managed and not self.available


@dataclass(frozen=True)
class RuntimeInstallResult:
    ok: bool
    runtime_id: str
    message: str
    bin_dir: str = ""
    version: str = ""


_RUNTIME_DEFS: dict[str, RuntimeRequirement] = {
    "node": RuntimeRequirement(
        id="node",
        label="Node.js LTS",
        commands=("node", "npm", "npx"),
        managed=True,
        setup_url="https://nodejs.org/",
        install_hint="Install Node.js LTS or let Row-Bot install a private portable Node.js runtime.",
    ),
    "uv": RuntimeRequirement(
        id="uv",
        label="uv / uvx",
        commands=("uv", "uvx"),
        managed=True,
        setup_url="https://docs.astral.sh/uv/getting-started/installation/",
        install_hint="Install uv/uvx or let Row-Bot install a private portable uv runtime.",
    ),
    "docker": RuntimeRequirement(
        id="docker",
        label="Docker Desktop",
        commands=("docker",),
        managed=False,
        setup_url="https://www.docker.com/products/docker-desktop/",
        install_hint="Install Docker Desktop, restart Row-Bot, then test this MCP server again.",
    ),
    "playwright-chrome": RuntimeRequirement(
        id="playwright-chrome",
        label="Playwright Browser",
        commands=(),
        managed=True,
        setup_url="https://playwright.dev/docs/browsers",
        install_hint="Install the Playwright-managed browser dependency before using Playwright MCP browser tools.",
    ),
}

_COMMAND_RUNTIME_MAP = {
    "node": "node",
    "npm": "node",
    "npx": "node",
    "uv": "uv",
    "uvx": "uv",
    "docker": "docker",
}


def known_runtime_ids() -> list[str]:
    return sorted(_RUNTIME_DEFS)


def _normalize_command_name(command: str) -> str:
    name = Path(str(command or "").strip().strip('"')).name.lower()
    for suffix in (".cmd", ".exe", ".bat", ".ps1"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def infer_runtime_id_for_command(command: str) -> str | None:
    return _COMMAND_RUNTIME_MAP.get(_normalize_command_name(command))


def _requirement_from_id(runtime_id: str, *, commands: tuple[str, ...] | None = None, source: str = "inferred") -> RuntimeRequirement | None:
    base = _RUNTIME_DEFS.get(runtime_id)
    if not base:
        return None
    return RuntimeRequirement(
        id=base.id,
        label=base.label,
        commands=commands or base.commands,
        managed=base.managed,
        setup_url=base.setup_url,
        install_hint=base.install_hint,
        source=source,
    )


def _requirement_from_mapping(raw: dict[str, Any]) -> RuntimeRequirement | None:
    runtime_id = str(raw.get("id") or raw.get("runtime") or "").strip().lower()
    base = _RUNTIME_DEFS.get(runtime_id)
    commands = tuple(str(item) for item in raw.get("commands") or () if str(item).strip())
    if base:
        return RuntimeRequirement(
            id=base.id,
            label=str(raw.get("label") or base.label),
            commands=commands or base.commands,
            managed=bool(raw.get("managed", base.managed)),
            setup_url=str(raw.get("setup_url") or raw.get("url") or base.setup_url),
            install_hint=str(raw.get("install_hint") or base.install_hint),
            source="metadata",
        )
    if runtime_id:
        return RuntimeRequirement(
            id=runtime_id,
            label=str(raw.get("label") or runtime_id),
            commands=commands,
            managed=False,
            setup_url=str(raw.get("setup_url") or raw.get("url") or ""),
            install_hint=str(raw.get("install_hint") or "Install this dependency, restart Row-Bot if PATH changes, then test again."),
            source="metadata",
        )
    return None


def requirements_for_install(install: dict[str, Any] | None) -> list[RuntimeRequirement]:
    install = install if isinstance(install, dict) else {}
    if str(install.get("transport") or "stdio") != "stdio":
        return []
    command = str(install.get("command") or "").strip()
    runtime_id = infer_runtime_id_for_command(command)
    if not runtime_id:
        return []
    req = _requirement_from_id(runtime_id, commands=(command,), source="inferred")
    requirements = [req] if req else []
    if _looks_like_playwright_mcp(install):
        browser_req = _requirement_from_id("playwright-chrome", source="inferred")
        if browser_req:
            requirements.append(browser_req)
    return requirements


def requirements_for_server(server_cfg: dict[str, Any] | None) -> list[RuntimeRequirement]:
    if not isinstance(server_cfg, dict):
        return []
    requirements: list[RuntimeRequirement] = []
    raw_requirements = server_cfg.get("requirements")
    source = server_cfg.get("source") if isinstance(server_cfg.get("source"), dict) else {}
    if raw_requirements is None:
        raw_requirements = source.get("requirements")
    if isinstance(raw_requirements, list):
        for raw in raw_requirements:
            req = _requirement_from_mapping(raw) if isinstance(raw, dict) else _requirement_from_id(str(raw).strip().lower(), source="metadata")
            if req:
                requirements.append(req)
    elif isinstance(raw_requirements, dict):
        req = _requirement_from_mapping(raw_requirements)
        if req:
            requirements.append(req)
    if not requirements and str(server_cfg.get("transport") or "stdio") == "stdio":
        command = str(server_cfg.get("command") or "").strip()
        runtime_id = infer_runtime_id_for_command(command)
        if runtime_id:
            req = _requirement_from_id(runtime_id, commands=(command,), source="inferred")
            if req:
                requirements.append(req)
    if _looks_like_playwright_mcp(server_cfg):
        req = _requirement_from_id("playwright-chrome", source="inferred")
        if req:
            requirements.append(req)
    deduped: dict[str, RuntimeRequirement] = {}
    for req in requirements:
        deduped.setdefault(req.id, req)
    return list(deduped.values())


def _looks_like_playwright_mcp(config: dict[str, Any] | None) -> bool:
    if not isinstance(config, dict):
        return False
    text = " ".join([
        str(config.get("command") or ""),
        " ".join(str(arg) for arg in config.get("args") or []),
        str((config.get("source") or {}).get("id") if isinstance(config.get("source"), dict) else ""),
        str((config.get("source") or {}).get("name") if isinstance(config.get("source"), dict) else ""),
    ]).lower()
    return "@playwright/mcp" in text or "playwright-mcp" in text or "microsoft-playwright" in text


def _manifest_path(runtime_id: str) -> Path:
    return RUNTIMES_DIR / runtime_id / "manifest.json"


def _read_manifest(runtime_id: str) -> dict[str, Any]:
    try:
        data = _manifest_bytes(runtime_id)
        value = _manifest_json(data) if data else {}
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _write_manifest(runtime_id: str, data: dict[str, Any], *, expected_revision: str | None = None,
                    validate: Callable[[], None] = lambda: None,
                    command_id: str | None = None, checkpoint: Callable[[str, dict], None] | None = None) -> None:
    from row_bot.developer.edits import publish_text_revision, read_edit_bytes
    _safe_component(runtime_id)
    encoded = json.dumps(data, indent=2, allow_nan=False)
    if len(encoded.encode()) > METADATA_BYTE_LIMIT:
        raise RuntimeError("Runtime manifest exceeds its byte budget")
    with _INSTALL_LOCK, _owned_directory(RUNTIMES_DIR / runtime_id, create=True) as directory:
        def authority():
            validate()
            if expected_revision is not None and runtime_install_revision(runtime_id) != expected_revision:
                raise RuntimeError("Managed runtime revision changed")
        authority()
        _before, digest, identity, _mode = read_edit_bytes(directory.path, "manifest.json", max_bytes=METADATA_BYTE_LIMIT)
        # Bind the captured bytes to the reviewed manifest before passing its
        # identity/digest into the publisher. An external edit between the
        # first authority check and this read must not become our baseline.
        authority()
        publish_text_revision(directory.path, "manifest.json", encoded, expected_digest=digest,
            command_id=command_id or str(uuid.uuid4()),
            persist_recovery=lambda proof: checkpoint("manifest_prepared", {"publication": asdict(proof)}) if checkpoint else None,
            validate=validate,
            expected_identity=identity, max_bytes=METADATA_BYTE_LIMIT)


def _managed_bin_dir(runtime_id: str) -> Path | None:
    try:
        manifest = _read_manifest(runtime_id)
        directory = Path(str(manifest.get("bin_dir") or "")).absolute()
        managed = (RUNTIMES_DIR / _safe_component(runtime_id)).absolute()
        root = Path(str(manifest.get("root") or "")).absolute()
        if not directory.is_relative_to(managed) or not root.is_relative_to(managed) or not directory.is_relative_to(root):
            return None
        with _owned_directory(directory):
            pass
        proof = manifest.get("generation_sha256")
        if proof is not None:
            if (type(proof) is not str or not re.fullmatch(r"[a-f0-9]{64}", proof)
                    or root.parent != managed or manifest.get("system") != platform.system().lower()
                    or manifest.get("arch") != _system_arch()):
                return None
            with _owned_directory(root) as generation:
                if _generation_digest(generation, internal_links=manifest.get("internal_links") is True) != proof:
                    return None
        return directory
    except (OSError, RuntimeError, ValueError, TypeError):
        return None


def playwright_browsers_path() -> str:
    from row_bot.browser.runtime import check_managed_browser_runtime, runtime_root

    readiness = check_managed_browser_runtime()
    return readiness.browsers_dir if readiness.ready else str(runtime_root() / "browsers")


def _find_playwright_chromium_executable(browsers_dir: Path) -> Path | None:
    if not browsers_dir.exists():
        return None
    system = platform.system().lower()
    patterns = (
        ("chromium-*/chrome-win/chrome.exe", "chromium-*/chrome.exe") if system == "windows"
        else ("chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium", "chromium-*/Chromium.app/Contents/MacOS/Chromium") if system == "darwin"
        else ("chromium-*/chrome-linux/chrome", "chromium-*/chrome")
    )
    for pattern in patterns:
        for candidate in sorted(browsers_dir.glob(pattern), reverse=True):
            if candidate.exists():
                return candidate
    executable_name = "chrome.exe" if system == "windows" else "Chromium" if system == "darwin" else "chrome"
    for candidate in sorted(browsers_dir.rglob(executable_name), reverse=True):
        if "chromium" in str(candidate).lower() and candidate.exists():
            return candidate
    return None


def _playwright_browser_from_env(env: dict[str, str]) -> tuple[Path | None, Path | None]:
    executable_value = str(env.get("PLAYWRIGHT_MCP_EXECUTABLE_PATH") or "").strip()
    executable = Path(executable_value) if executable_value else None
    if executable is not None and executable.exists():
        browsers_value = str(env.get("PLAYWRIGHT_BROWSERS_PATH") or "").strip()
        return executable, Path(browsers_value) if browsers_value else executable.parent
    browsers_value = str(env.get("PLAYWRIGHT_BROWSERS_PATH") or "").strip()
    browsers_dir = Path(browsers_value) if browsers_value else None
    if browsers_dir is None:
        return None, None
    return _find_playwright_chromium_executable(browsers_dir), browsers_dir


def playwright_browser_executable_path(
    env: dict[str, str] | None = None,
) -> str:
    environment = os.environ if env is None else env
    executable, _browsers_dir = _playwright_browser_from_env(environment)
    if executable is not None:
        return str(executable)
    from row_bot.browser.runtime import check_managed_browser_runtime

    readiness = check_managed_browser_runtime()
    return readiness.executable_path if readiness.ready else ""


def apply_managed_runtime_env(server_cfg: dict[str, Any] | None, env: dict[str, str]) -> dict[str, str]:
    next_env = dict(env)
    checks = check_server_requirements(server_cfg, next_env)
    for check in checks:
        if check.requirement.id == "playwright-chrome" and check.available:
            next_env.setdefault("PLAYWRIGHT_BROWSERS_PATH", playwright_browsers_path())
            executable_path = playwright_browser_executable_path()
            if executable_path:
                next_env.setdefault("PLAYWRIGHT_MCP_EXECUTABLE_PATH", executable_path)
    return next_env


def managed_command_path(runtime_id: str, command: str) -> str | None:
    bin_dir = _managed_bin_dir(runtime_id)
    if not bin_dir:
        return None
    return shutil.which(command, path=str(bin_dir))


def managed_path_for_requirement(requirement: RuntimeRequirement) -> str:
    bin_dir = _managed_bin_dir(requirement.id)
    return str(bin_dir) if bin_dir else ""


def _path_with_prefix(bin_dir: str, env: dict[str, str]) -> str:
    old_path = env.get("PATH") or env.get("Path") or ""
    return str(bin_dir) + os.pathsep + old_path if old_path else str(bin_dir)


def _env_path(env: dict[str, str]) -> str | None:
    if "PATH" in env:
        return env["PATH"]
    if "Path" in env:
        return env["Path"]
    return None


def check_requirement(requirement: RuntimeRequirement, env: dict[str, str] | None = None) -> RuntimeCheck:
    env = env or os.environ.copy()
    if requirement.id == "playwright-chrome":
        environment_executable, environment_browsers_dir = _playwright_browser_from_env(env)
        if environment_executable is not None and environment_browsers_dir is not None:
            return RuntimeCheck(
                requirement=requirement,
                available=True,
                source="environment",
                paths={
                    "PLAYWRIGHT_BROWSERS_PATH": str(environment_browsers_dir),
                    "PLAYWRIGHT_MCP_EXECUTABLE_PATH": str(environment_executable),
                },
                message="Playwright browser is available from the configured browser runtime.",
            )
        from row_bot.browser.runtime import check_managed_browser_runtime

        readiness = check_managed_browser_runtime()
        if readiness.ready:
            return RuntimeCheck(
                requirement=requirement,
                available=True,
                source="managed",
                paths={
                    "PLAYWRIGHT_BROWSERS_PATH": readiness.browsers_dir,
                    "PLAYWRIGHT_MCP_EXECUTABLE_PATH": readiness.executable_path,
                },
                message=(
                    "The Python Playwright-matched Chromium runtime is available "
                    f"(Playwright {readiness.package_version}, Chromium revision {readiness.chromium_revision})."
                ),
            )
        return RuntimeCheck(
            requirement=requirement,
            available=False,
            message=requirement.install_hint,
        )
    paths: dict[str, str] = {}
    missing: list[str] = []
    system_path = _env_path(env)
    managed_bin = _managed_bin_dir(requirement.id)
    for command in requirement.commands:
        found = shutil.which(command, path=system_path)
        source = "system"
        if not found and managed_bin:
            found = shutil.which(command, path=str(managed_bin))
            source = "managed"
        if found:
            paths[command] = found
        else:
            missing.append(command)
    available = not missing
    if available:
        source = "managed" if managed_bin and any(str(path).startswith(str(managed_bin)) for path in paths.values()) else "system"
        return RuntimeCheck(requirement=requirement, available=True, source=source, paths=paths, message=f"{requirement.label} is available.")
    message = requirement.install_hint or f"Install {requirement.label}, restart Row-Bot if PATH changes, then test again."
    return RuntimeCheck(requirement=requirement, available=False, missing_commands=tuple(missing), message=message)


def check_server_requirements(server_cfg: dict[str, Any] | None, env: dict[str, str] | None = None) -> list[RuntimeCheck]:
    return [check_requirement(req, env) for req in requirements_for_server(server_cfg)]


def missing_requirement_for_command(command: str, env: dict[str, str] | None = None) -> RuntimeCheck | None:
    runtime_id = infer_runtime_id_for_command(command)
    req = _requirement_from_id(runtime_id, commands=(command,), source="inferred") if runtime_id else None
    if not req:
        return None
    check = check_requirement(req, env)
    return check if not check.available else None


def resolve_command(command: str, env: dict[str, str]) -> tuple[str | None, dict[str, str], RuntimeCheck | None]:
    expanded = os.path.expandvars(os.path.expanduser(command.strip()))
    if not expanded:
        return None, env, None
    system_path = _env_path(env)
    found = shutil.which(expanded, path=system_path)
    if found:
        return found, env, None
    runtime_id = infer_runtime_id_for_command(expanded)
    req = _requirement_from_id(runtime_id, commands=(expanded,), source="inferred") if runtime_id else None
    if req:
        bin_dir = _managed_bin_dir(req.id)
        if bin_dir:
            next_env = dict(env)
            next_env["PATH"] = _path_with_prefix(str(bin_dir), next_env)
            found = shutil.which(expanded, path=next_env["PATH"])
            if found:
                return found, next_env, None
        return None, env, check_requirement(req, env)
    return None, env, None


def missing_command_message(command: str, check: RuntimeCheck | None = None) -> str:
    if check:
        req = check.requirement
        if req.managed:
            return f"MCP stdio command '{command}' was not found on PATH. {req.label} is required. Install it in Row-Bot, install it system-wide and restart Row-Bot, or edit this MCP server to use an absolute executable path."
        return f"MCP stdio command '{command}' was not found on PATH. {req.label} is required. {req.install_hint}"
    return f"MCP stdio command '{command}' was not found on PATH. Install the command, restart Row-Bot, or edit this MCP server to use an absolute executable path."


class _HttpsRedirects(urllib.request.HTTPRedirectHandler):
    max_redirections = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _safe_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _request(url, *, method=None):
    request = urllib.request.Request(_safe_url(url), method=method,
        headers={"User-Agent": "Row-Bot-MCP-Runtime-Installer/1.0", "Accept-Encoding": "identity"})
    return urllib.request.build_opener(_HttpsRedirects()).open(request, timeout=30)


def _remote_bytes(url, maximum=METADATA_BYTE_LIMIT):
    with _request(url) as response:
        deadline, value = time.monotonic() + 30, bytearray()
        read = getattr(response, "read1", response.read)
        while block := read(min(16384, maximum + 1 - len(value))):
            value.extend(block)
            if len(value) > maximum or time.monotonic() >= deadline:
                raise RuntimeError("Runtime metadata exceeds its download budget")
        return bytes(value)


def _download(url: str, destination: Path, progress: Callable[[str], None] | None = None,
              *, validate: Callable[[], None] = lambda: None) -> None:
    if progress:
        progress("Downloading the reviewed runtime archive")
    deadline = time.monotonic() + 120
    validate()
    with _request(url) as response, destination.open("xb") as handle:
        size = 0
        read = getattr(response, "read1", response.read)
        while block := read(1024 * 1024):
            validate()
            size += len(block)
            if size > ARCHIVE_BYTE_LIMIT or time.monotonic() >= deadline:
                raise RuntimeError("Runtime archive exceeds its download budget")
            handle.write(block)
        handle.flush()
        os.fsync(handle.fileno())


def _sha256(path: Path) -> str:
    with _owned_directory(path.absolute().parent) as directory:
        return hashlib.sha256(directory.read(path.name, ARCHIVE_BYTE_LIMIT)).hexdigest()


def _verify_sha256(path: Path, expected: str | None) -> None:
    if type(expected) is not str or not re.fullmatch(r"[a-fA-F0-9]{64}", expected):
        raise RuntimeError("A pinned archive checksum is required")
    if _sha256(path).lower() != expected.lower():
        raise RuntimeError(f"Downloaded archive checksum mismatch for {path.name}")


def _system_arch() -> str:
    machine = platform.machine().lower()
    if machine in {"amd64", "x86_64"}:
        return "x64"
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    raise RuntimeError(f"Unsupported CPU architecture for managed runtime install: {platform.machine()}")


def _node_asset_name(version: str) -> tuple[str, str]:
    system = platform.system().lower()
    arch = _system_arch()
    if system == "windows":
        return f"node-{version}-win-{arch}.zip", "zip"
    if system == "darwin":
        return f"node-{version}-darwin-{arch}.tar.gz", "tar"
    if system == "linux":
        return f"node-{version}-linux-{arch}.tar.xz", "tar"
    raise RuntimeError(f"Unsupported OS for managed Node.js install: {platform.system()}")


def _latest_node_lts_version() -> str:
    releases = json.loads(_remote_bytes("https://nodejs.org/dist/index.json"))
    if not isinstance(releases, list):
        raise RuntimeError("Invalid Node.js release metadata")
    for release in releases:
        if isinstance(release, dict) and release.get("lts"):
            version = _safe_component(release.get("version"))
            if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", version):
                raise RuntimeError("Invalid Node.js release version")
            return version
    raise RuntimeError("Could not determine latest Node.js LTS version")


def _node_checksum(version: str, asset_name: str) -> str:
    _safe_component(version)
    _safe_component(asset_name)
    lines = _remote_bytes(f"https://nodejs.org/dist/{version}/SHASUMS256.txt").decode("utf-8").splitlines()
    matches = [line.split()[0] for line in lines if len(line.split()) == 2 and line.split()[1] == asset_name]
    if len(matches) != 1 or not re.fullmatch(r"[a-fA-F0-9]{64}", matches[0]):
        raise RuntimeError("Node.js release is missing an unambiguous pinned checksum")
    return matches[0].lower()


def _remote_size(url):
    with _request(url, method="HEAD") as response:
        value = response.headers.get("Content-Length", "")
        if not re.fullmatch(r"[0-9]{1,12}", value) or not 0 < int(value) <= ARCHIVE_BYTE_LIMIT:
            raise RuntimeError("Runtime archive has no acceptable exact byte size")
        return int(value)


def _archive_parts(name):
    if type(name) is not str or not name or len(name.encode("utf-8")) > 2048 or "\\" in name or name.startswith("/"):
        raise RuntimeError("Archive contains unsafe path")
    parts = name.rstrip("/").split("/")
    for part in parts:
        if (not part or part in {".", ".."} or part.endswith((".", " "))
                or any(ord(c) < 32 or c in '<>:"|?*' for c in part)
                or part.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL",
                    *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}):
            raise RuntimeError("Archive contains unsafe path")
    return parts


@contextlib.contextmanager
def _relative_parent(root, parts, *, create=False):
    with contextlib.ExitStack() as stack:
        directory = root
        for part in parts[:-1]:
            directory = stack.enter_context(directory.child(part, create=create))
        yield directory, parts[-1]


def _archive_entries(handle, is_zip, *, internal_links=False):
    entries, folded, total = {}, set(), 0
    source = handle.infolist() if is_zip else handle
    for member in source:
        name = member.filename if is_zip else member.name
        while name.startswith("./"):
            name = name[2:]
        if not name or name == ".":
            if (member.is_dir() if is_zip else member.isdir()):
                continue
        parts = _archive_parts(name)
        name = "/".join(parts)
        mode = member.external_attr >> 16 if is_zip else member.mode
        directory = member.is_dir() if is_zip else member.isdir()
        link = stat.S_ISLNK(mode) if is_zip else member.issym()
        if not is_zip and (member.sparse is not None or any(key.startswith("GNU.sparse") for key in member.pax_headers)):
            raise RuntimeError("Archive contains unsupported special entry")
        regular = not directory and (not stat.S_IFMT(mode) or stat.S_ISREG(mode)) if is_zip else member.isfile()
        if not directory and not regular and not (link and internal_links and not is_zip):
            raise RuntimeError("Archive contains unsupported special entry or symlink")
        if name.casefold() in folded or len(entries) >= ARCHIVE_ENTRY_LIMIT:
            raise RuntimeError("Archive contains duplicate entries or exceeds its entry budget")
        folded.add(name.casefold())
        size = member.file_size if is_zip else member.size
        if type(size) is not int or size < 0 or size > ARCHIVE_BYTE_LIMIT:
            raise RuntimeError("Archive member exceeds its size budget")
        total += size
        if total > EXTRACTED_BYTE_LIMIT:
            raise RuntimeError("Archive exceeds its extracted byte budget")
        entries[name] = (member, "directory" if directory else "link" if link else "file", size, mode)
    spellings = {}
    for name in entries:
        components = name.split("/")
        for index in range(1, len(components) + 1):
            prefix = "/".join(components[:index])
            old = spellings.setdefault(prefix.casefold(), prefix)
            if old != prefix:
                raise RuntimeError("Archive contains case-colliding paths")
    links = {}
    for name, (member, kind, _size, _mode) in entries.items():
        parts = name.split("/")
        for index in range(1, len(parts)):
            ancestor = entries.get("/".join(parts[:index]))
            if ancestor and ancestor[1] != "directory":
                raise RuntimeError("Archive contains an ancestor link or file pivot")
        if kind == "link":
            value = member.linkname
            if not value or value.startswith("/") or "\\" in value or len(value) > 2048:
                raise RuntimeError("Archive contains unsafe link")
            resolved = parts[:-1]
            for part in value.split("/"):
                if part == "..":
                    if not resolved:
                        raise RuntimeError("Archive link leaves its generation")
                    resolved.pop()
                elif part not in {"", "."}:
                    _archive_parts(part)
                    resolved.append(part)
            target = "/".join(resolved)
            # Canonical npm/npx links end at regular files; no link chains or
            # ancestor links are admitted, so cycles cannot be constructed.
            if target not in entries or entries[target][1] != "file":
                raise RuntimeError("Archive link does not resolve to an admitted regular file")
            for index in range(1, len(resolved)):
                ancestor = entries.get("/".join(resolved[:index]))
                if ancestor and ancestor[1] != "directory":
                    raise RuntimeError("Archive link traverses an ancestor link")
            links[name] = value
    return entries, links


def _extract_into(archive: Path, root: _Directory, *, internal_links=False, validate=lambda: None):
    deadline = time.monotonic() + 120
    original_validate = validate
    def validate():
        original_validate()
        if time.monotonic() >= deadline:
            raise RuntimeError("Runtime extraction exceeded its time budget")
    source = io.BytesIO(archive) if isinstance(archive, bytes) else archive
    is_zip = zipfile.is_zipfile(source)
    if is_zip:
        if isinstance(archive, bytes):
            zip_data = archive
        else:
            with _owned_directory(Path(archive).absolute().parent) as owner:
                zip_data = owner.read(Path(archive).name, ARCHIVE_BYTE_LIMIT)
        end = zip_data.rfind(b"PK\x05\x06", max(0, len(zip_data) - 65557))
        if end < 0 or len(zip_data) < end + 22:
            raise RuntimeError("Invalid archive directory")
        fields = struct.unpack_from("<4s4H2LH", zip_data, end)
        if (fields[1] or fields[2] or fields[3] != fields[4] or fields[4] > ARCHIVE_ENTRY_LIMIT
                or fields[5] > 4 * 1024 * 1024 or fields[6] + fields[5] > end
                or end + 22 + fields[7] != len(zip_data)):
            raise RuntimeError("Archive directory exceeds its bounds")
        source = io.BytesIO(zip_data)
    if hasattr(source, "seek"):
        source.seek(0)
    class BoundedTarInfo(tarfile.TarInfo):
        headers = 0
        metadata = 0
        metadata_bytes = 0
        @classmethod
        def frombuf(cls, buf, encoding, errors):
            validate()
            item = super().frombuf(buf, encoding, errors)
            cls.headers += 1
            if cls.headers > ARCHIVE_ENTRY_LIMIT:
                raise RuntimeError("Archive exceeds its header budget")
            if item.type in {tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK}:
                cls.metadata += 1
                cls.metadata_bytes += item.size
                if cls.metadata > 64 or cls.metadata_bytes > 64 * 1024 * 1024:
                    raise RuntimeError("Archive exceeds its extended metadata budget")
            else:
                cls.metadata = 0
            if item.type in {tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK} and item.size > METADATA_BYTE_LIMIT:
                raise RuntimeError("Archive metadata exceeds its byte budget")
            if item.type == tarfile.GNUTYPE_SPARSE:
                raise RuntimeError("Archive contains unsupported special entry")
            return item
    archive_handle = zipfile.ZipFile(source) if is_zip else tarfile.open(
        name=None if hasattr(source, "read") else source,
        fileobj=source if hasattr(source, "read") else None, tarinfo=BoundedTarInfo)
    with archive_handle as handle:
        entries, links = _archive_entries(handle, is_zip, internal_links=internal_links)
        # Validate the complete graph before creating any archive member.
        for name, (member, kind, size, mode) in entries.items():
            validate()
            with _relative_parent(root, name.split("/"), create=True) as (parent, leaf):
                if kind == "directory":
                    with parent.child(leaf, create=True):
                        pass
                elif kind == "file":
                    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
                    descriptor = os.open(parent.leaf(leaf), flags, 0o600, dir_fd=parent.fd)
                    with os.fdopen(descriptor, "wb") as output, (handle.open(member) if is_zip else handle.extractfile(member)) as source:
                        copied = 0
                        while block := source.read(1024 * 1024):
                            validate()
                            copied += len(block)
                            if copied > size:
                                raise RuntimeError("Archive member exceeded its declared size")
                            output.write(block)
                        if copied != size:
                            raise RuntimeError("Archive member was incomplete")
                        output.flush()
                        os.fsync(output.fileno())
                        if os.name != "nt":
                            os.fchmod(output.fileno(), 0o755 if mode & 0o111 else 0o644)
        for name, target in links.items():
            validate()
            if os.name == "nt":
                raise RuntimeError("POSIX runtime links are unavailable on Windows")
            with _relative_parent(root, name.split("/")) as (parent, leaf):
                os.symlink(target, leaf, dir_fd=parent.fd)
    validate()
    top = {name.split("/")[0] for name in entries}
    return next(iter(top)) if len(top) == 1 and entries.get(next(iter(top)), (None, "directory"))[1] == "directory" else None


def _extract_archive(archive: Path, destination: Path) -> Path:
    with _owned_directory(destination, create=True) as root:
        child = _extract_into(archive, root)
    return destination / child if child else destination


def _generation_digest(directory, *, internal_links=False):
    digest, entries, used = hashlib.sha256(), {}, 0
    def walk(current, prefix=""):
        nonlocal used
        names = sorted(os.listdir(current.fd if current.fd is not None else current.path))
        for name in names:
            _archive_parts(name)
            relative = prefix + name
            if len(entries) >= ARCHIVE_ENTRY_LIMIT:
                raise RuntimeError("Runtime generation exceeds its entry budget")
            info = current.stat(name)
            if stat.S_ISDIR(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400:
                entries[relative] = ["directory"]
                with current.child(name) as child:
                    walk(child, relative + "/")
            elif stat.S_ISLNK(info.st_mode) and internal_links and current.fd is not None:
                entries[relative] = ["link", os.readlink(name, dir_fd=current.fd)]
            elif stat.S_ISREG(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400:
                data = current.read(name, ARCHIVE_BYTE_LIMIT)
                used += len(data)
                if used > EXTRACTED_BYTE_LIMIT:
                    raise RuntimeError("Runtime generation exceeds its byte budget")
                entries[relative] = ["file", info.st_mode & 0o777, hashlib.sha256(data).hexdigest()]
            else:
                raise RuntimeError("Runtime generation contains an unsupported entry")
    walk(directory)
    for name, row in entries.items():
        if row[0] == "link":
            parts = name.split("/")[:-1]
            target = row[1]
            if type(target) is not str or target.startswith("/") or "\\" in target or len(target) > 2048:
                raise RuntimeError("Runtime generation contains an unsafe link")
            for part in target.split("/"):
                if part == "..":
                    if not parts:
                        raise RuntimeError("Runtime generation link escapes its owner")
                    parts.pop()
                elif part not in {"", "."}:
                    _archive_parts(part)
                    parts.append(part)
            resolved = entries.get("/".join(parts))
            if not resolved or resolved[0] != "file":
                raise RuntimeError("Runtime generation link lacks an owned regular target")
            for index in range(1, len(parts)):
                if entries.get("/".join(parts[:index]), ["directory"])[0] != "directory":
                    raise RuntimeError("Runtime generation link traverses a link")
            row.append(resolved[2])
    digest.update(json.dumps(entries, sort_keys=True, ensure_ascii=True).encode())
    return digest.hexdigest()


def _validated_plan(plan):
    if (type(plan) is not ArchiveRuntimePlan or type(plan.schema_version) is not int or plan.schema_version != 1
            or type(plan.internal_links) is not bool):
        raise RuntimeError("Invalid pinned runtime plan")
    expected = make_archive_runtime_plan(plan.runtime_id, version=plan.version, url=plan.url,
        sha256=plan.sha256, size_bytes=plan.size_bytes, asset_name=plan.asset_name,
        executable_candidates=plan.executable_candidates,
        preserve_top_level_directory=plan.preserve_top_level_directory)
    if plan != expected:
        raise RuntimeError("Managed runtime plan is stale or has incompatible platform pins")


def _install_archive_bytes(plan, data, *, progress=None, cancelled=None, validate=lambda: None,
                           command_id=None, checkpoint=None):
    def authority():
        validate()
        if cancelled and cancelled():
            raise RuntimeError("Runtime installation cancelled")
    authority()
    if len(data) != plan.size_bytes or hashlib.sha256(data).hexdigest() != plan.sha256:
        raise RuntimeError("Downloaded archive checksum mismatch or size mismatch")
    with _INSTALL_LOCK:
        authority()
        _validated_plan(plan)
        with _owned_directory(RUNTIMES_DIR / plan.runtime_id, create=True) as runtime:
            baseline = runtime_install_revision(plan.runtime_id)
            previous = _read_manifest(plan.runtime_id)
            stage_name = ".install-" + uuid.uuid4().hex
            os.mkdir(runtime.leaf(stage_name), mode=0o700, dir_fd=runtime.fd)
            backup = None
            activated = False
            # Staged/failed generations stay retained on interruption. They are
            # never advertised or automatically adopted without a fresh plan.
            with runtime.child(stage_name) as stage:
                with stage.child("extracted", create=True) as extracted:
                    child = _extract_into(data, extracted, internal_links=plan.internal_links, validate=authority)
                    selected = extracted
                    with contextlib.ExitStack() as stack:
                        if child and not plan.preserve_top_level_directory:
                            selected = stack.enter_context(extracted.child(child))
                        executable = None
                        for candidate in plan.executable_candidates:
                            candidates = [candidate]
                            if child and candidate.startswith(child + "/") and not plan.preserve_top_level_directory:
                                candidates.append(candidate[len(child) + 1:])
                            for relative in candidates:
                                try:
                                    with _relative_parent(selected, _archive_parts(relative)) as (parent, leaf):
                                        parent.read(leaf, ARCHIVE_BYTE_LIMIT)
                                        if os.name != "nt":
                                            fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent.fd)
                                            try:
                                                os.fchmod(fd, os.fstat(fd).st_mode | stat.S_IXUSR)
                                            finally:
                                                os.close(fd)
                                    executable = relative
                                    break
                                except FileNotFoundError:
                                    continue
                            if executable is not None:
                                break
                        if executable is None:
                            raise RuntimeError("Reviewed executable layout not found")
                        tree = _generation_digest(selected, internal_links=plan.internal_links)
                        selected_info = os.fstat(selected.fd) if selected.fd is not None else selected.path.stat()
                        generation_identity = f"{selected_info.st_dev}:{selected_info.st_ino}"
                        if checkpoint:
                            checkpoint("generation_prepared", {"generation": {"version": plan.version,
                                "identity": generation_identity, "sha256": tree, "internal_links": plan.internal_links}})
                        source_name = child if selected is not extracted else "extracted"
                        source_under_extracted = selected is not extracted
                authority()
                if runtime_install_revision(plan.runtime_id) != baseline:
                    raise RuntimeError("Managed runtime revision changed")
                try:
                    runtime.stat(plan.version)
                    exists = True
                except FileNotFoundError:
                    exists = False
                if exists:
                    with runtime.child(plan.version) as current:
                        current_tree = _generation_digest(current, internal_links=plan.internal_links)
                    valid_owner = (previous.get("version") == plan.version and previous.get("archive_sha256") == plan.sha256
                        and Path(str(previous.get("root", ""))).absolute() == runtime.path / plan.version)
                    if (valid_owner and current_tree == tree
                            and previous.get("generation_sha256", tree) == tree
                            and Path(str(previous.get("executable_path", ""))).absolute() == runtime.path / plan.version / executable):
                        return RuntimeInstallResult(True, plan.runtime_id, "The exact verified runtime is already installed.",
                            str(runtime.path / plan.version / Path(executable).parent), plan.version)
                    # Preserve the existing Cua signed-bundle repair seam only.
                    old_path = Path(str(previous.get("executable_path", "")))
                    repair = (valid_owner and plan.preserve_top_level_directory
                        and old_path.is_relative_to(runtime.path / plan.version)
                        and not any(part.endswith(".app") for part in old_path.relative_to(runtime.path / plan.version).parts))
                    if not repair:
                        raise RuntimeError("Existing runtime generation failed integrity checks")
                    backup = ".retained-layout-" + uuid.uuid4().hex
                    runtime.rename(plan.version, backup)
                try:
                    authority()
                    if source_under_extracted:
                        with stage.child("extracted") as source_parent:
                            source_parent.rename(source_name, plan.version, runtime)
                    else:
                        stage.rename(source_name, plan.version, runtime)
                    activated = True
                    with runtime.child(plan.version) as active:
                        if _generation_digest(active, internal_links=plan.internal_links) != tree:
                            raise RuntimeError("Runtime generation changed before publication")
                    authority()
                    manifest = {"installed": True, "version": plan.version, "archive_name": plan.asset_name,
                        "archive_url": plan.url, "archive_sha256": plan.sha256, "archive_size": plan.size_bytes,
                        "root": str(runtime.path / plan.version),
                        "bin_dir": str(runtime.path / plan.version / Path(executable).parent),
                        "executable_path": str(runtime.path / plan.version / executable),
                        "source": "reviewed-pinned-archive", "generation_sha256": tree,
                        "system": plan.system, "arch": plan.arch, "internal_links": plan.internal_links,
                        "preserve_top_level_directory": plan.preserve_top_level_directory}
                    if previous and (previous.get("doctor_ok") or plan.runtime_id in {"node", "uv"}):
                        retained = dict(previous)
                        retained.pop("previous_manifest", None)
                        manifest["previous_manifest"] = retained
                    def publication_authority():
                        authority()
                        with runtime.child(plan.version) as active:
                            if _generation_digest(active, internal_links=plan.internal_links) != tree:
                                raise RuntimeError("Runtime generation changed during publication")
                    if command_id:
                        manifest["_client_publication"] = {"owner_id": "mcp-runtime-installation", "key": command_id,
                                                           "command_id": command_id}
                    _write_manifest(plan.runtime_id, manifest, expected_revision=baseline, validate=publication_authority,
                        **({"command_id": command_id, "checkpoint": checkpoint} if checkpoint else {}))
                    if _read_manifest(plan.runtime_id) != manifest:
                        raise RuntimeError("Runtime publication is unconfirmed")
                    publication_authority()
                except Exception:
                    # Never recursively delete an uncertain or edited tree.
                    # For the legacy same-version layout repair, restore its
                    # original name only when the old manifest is still exact.
                    if backup and activated and _read_manifest(plan.runtime_id) == previous:
                        runtime.rename(plan.version, ".failed-" + uuid.uuid4().hex)
                        runtime.rename(backup, plan.version)
                    elif backup and not activated:
                        runtime.rename(backup, plan.version)
                    raise
    if progress:
        progress("The verified runtime generation was published")
    return RuntimeInstallResult(True, plan.runtime_id, f"Installed {plan.runtime_id} {plan.version} for Row-Bot.",
        manifest["bin_dir"], plan.version)


def install_runtime_plan(plan: ArchiveRuntimePlan, *, progress: Callable[[str], None] | None = None,
                         cancelled: Callable[[], bool] | None = None,
                         validate: Callable[[], None] = lambda: None,
                         command_id: str | None = None,
                         checkpoint: Callable[[str, dict], None] | None = None) -> RuntimeInstallResult:
    """Install exact pins; optional command checkpoints precede owned publication."""
    if (command_id is None) != (checkpoint is None):
        raise RuntimeError("Incomplete runtime installation checkpoint owner")
    if command_id is not None and (type(command_id) is not str or str(uuid.UUID(command_id)) != command_id):
        raise RuntimeError("Invalid runtime installation command identity")
    def authority():
        validate()
        if cancelled and cancelled():
            raise RuntimeError("Runtime installation cancelled")
    validate()
    _validated_plan(plan)
    if cancelled and cancelled():
        return RuntimeInstallResult(False, plan.runtime_id, "Installation cancelled before download.")
    with tempfile.TemporaryDirectory(prefix="row-bot-runtime-download-") as temporary:
        # Only this newly created private temporary root may have an OS alias.
        archive = Path(temporary).resolve(strict=True) / plan.asset_name
        _download(plan.url, archive, progress, **({"validate": authority} if checkpoint else {}))
        with _owned_directory(archive.parent) as directory:
            data = directory.read(archive.name, ARCHIVE_BYTE_LIMIT)
        if cancelled and cancelled():
            return RuntimeInstallResult(False, plan.runtime_id, "Installation cancelled after download.")
        # Extraction consumes this same verified immutable byte string, so a
        # later archive pathname replacement cannot substitute unverified input.
        return _install_archive_bytes(plan, data, progress=progress, cancelled=cancelled, validate=validate,
                                      command_id=command_id, checkpoint=checkpoint)


def read_runtime_installation_proof(runtime_id: str, command_id: str, proof: dict,
                                    *, validate: Callable[[], None] = lambda: None) -> bool:
    """Verify exact retained manifest and generation ownership without publishing."""
    from row_bot.file_ownership import confirmed_edit_publication
    validate()
    try:
        if runtime_id not in {"node", "uv"} or str(uuid.UUID(command_id)) != command_id:
            return False
        publication, generation = proof["publication"], proof["generation"]
        version = _safe_component(generation["version"])
        path = _manifest_path(runtime_id)
        with _INSTALL_LOCK, _owned_directory(path.parent) as owner:
            with owner.child(version) as directory:
                info = os.fstat(directory.fd) if directory.fd is not None else directory.path.stat()
                if (f"{info.st_dev}:{info.st_ino}" != generation["identity"] or
                    _generation_digest(directory, internal_links=generation["internal_links"] is True) != generation["sha256"]):
                    return False
            verified = confirmed_edit_publication(path, publication["after_digest"], publication["candidate_identity"],
                publication, owner_id="mcp-runtime-installation", key=command_id, command_id=command_id,
                max_bytes=METADATA_BYTE_LIMIT)
            validate()
            return verified
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        validate()
        return False


def install_pinned_archive_runtime(runtime_id: str, *, version: str, url: str, sha256: str,
    asset_name: str, executable_candidates: tuple[str, ...], progress: Callable[[str], None] | None = None,
    cancelled: Callable[[], bool] | None = None, preserve_top_level_directory: bool = False) -> RuntimeInstallResult:
    """Compatible pinned helper; size is captured from its one bounded download.

    Existing signed Cua bundle layout, doctor and rollback semantics remain.
    New client review uses make_archive_runtime_plan with an exact size first.
    """
    # Validate all supplied pins before even creating a download directory.
    draft = make_archive_runtime_plan(runtime_id, version=version, url=url, sha256=sha256,
        size_bytes=1, asset_name=asset_name, executable_candidates=executable_candidates,
        preserve_top_level_directory=preserve_top_level_directory)
    if cancelled and cancelled():
        return RuntimeInstallResult(False, runtime_id, "Installation cancelled before download.")
    with tempfile.TemporaryDirectory(prefix="row-bot-runtime-download-") as temporary:
        # Do not apply this private-root canonicalization to user/runtime paths.
        archive = Path(temporary).resolve(strict=True) / asset_name
        _download(url, archive, progress)
        with _owned_directory(archive.parent) as directory:
            data = directory.read(archive.name, ARCHIVE_BYTE_LIMIT)
        if cancelled and cancelled():
            return RuntimeInstallResult(False, runtime_id, "Installation cancelled after download.")
        from dataclasses import replace
        return _install_archive_bytes(replace(draft, size_bytes=len(data)), data, progress=progress, cancelled=cancelled)


def _validated_runtime_root(runtime_id: str, value: Any) -> Path | None:
    try:
        _safe_component(runtime_id)
        candidate = Path(str(value or "")).absolute()
        if candidate.parent != (RUNTIMES_DIR / runtime_id).absolute():
            return None
        _safe_component(candidate.name)
        with _owned_directory(candidate):
            pass
        return candidate
    except (OSError, RuntimeError, ValueError):
        return None


def _retire_runtime_generation(runtime_id, root):
    # Retire by name instead of deleting a tree which may have been edited.
    with _owned_directory(RUNTIMES_DIR / runtime_id) as owner:
        before = owner.stat(root.name)
        if not stat.S_ISDIR(before.st_mode) or getattr(before, "st_file_attributes", 0) & 0x400:
            raise RuntimeError("Runtime generation is not an owned directory")
        retained = ".retired-" + uuid.uuid4().hex
        owner.rename(root.name, retained)
        after = owner.stat(retained)
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise RuntimeError("Changed runtime generation retained for manual review")


def rollback_pinned_archive_runtime(runtime_id: str) -> bool:
    """Restore the retained known-good manifest; preserve the failed generation."""
    with _INSTALL_LOCK:
        revision = runtime_install_revision(runtime_id)
        active = _read_manifest(runtime_id)
        previous = active.get("previous_manifest")
        if not isinstance(previous, dict):
            return False
        previous_root = _validated_runtime_root(runtime_id, previous.get("root"))
        active_root = _validated_runtime_root(runtime_id, active.get("root"))
        previous_executable = Path(str(previous.get("executable_path") or "")).absolute()
        if previous_root is None or not previous_executable.is_relative_to(previous_root):
            return False
        with _owned_directory(previous_root) as generation:
            with _relative_parent(generation, _archive_parts(previous_executable.relative_to(previous_root).as_posix())) as (parent, leaf):
                parent.read(leaf, ARCHIVE_BYTE_LIMIT)
            if previous.get("generation_sha256") is not None and _generation_digest(generation,
                    internal_links=previous.get("internal_links") is True) != previous["generation_sha256"]:
                return False
        restored = dict(previous)
        restored.pop("previous_manifest", None)
        _write_manifest(runtime_id, restored, expected_revision=revision)
        if active_root is not None and active_root != previous_root:
            _retire_runtime_generation(runtime_id, active_root)
        return True


def finalize_pinned_archive_runtime(runtime_id: str) -> bool:
    """Retire the previous name after doctor, retaining its bytes for recovery."""
    with _INSTALL_LOCK:
        revision = runtime_install_revision(runtime_id)
        active = _read_manifest(runtime_id)
        previous = active.get("previous_manifest")
        if not active.get("doctor_ok") or not isinstance(previous, dict):
            return False
        previous_root = _validated_runtime_root(runtime_id, previous.get("root"))
        active_root = _validated_runtime_root(runtime_id, active.get("root"))
        if active_root is None:
            return False
        with _owned_directory(active_root) as generation:
            if active.get("generation_sha256") is not None and _generation_digest(generation,
                    internal_links=active.get("internal_links") is True) != active["generation_sha256"]:
                return False
        active.pop("previous_manifest", None)
        _write_manifest(runtime_id, active, expected_revision=revision)
        if previous_root is not None and previous_root != active_root:
            _retire_runtime_generation(runtime_id, previous_root)
        return True


def _install_node(progress: Callable[[str], None] | None = None) -> RuntimeInstallResult:
    return install_runtime_plan(resolve_managed_runtime_plan("node"), progress=progress)


def _uv_asset_fragment() -> str:
    system = platform.system().lower()
    arch = _system_arch()
    if system == "windows":
        return "x86_64-pc-windows-msvc" if arch == "x64" else "aarch64-pc-windows-msvc"
    if system == "darwin":
        return "x86_64-apple-darwin" if arch == "x64" else "aarch64-apple-darwin"
    if system == "linux":
        return "x86_64-unknown-linux-gnu" if arch == "x64" else "aarch64-unknown-linux-gnu"
    raise RuntimeError(f"Unsupported OS for managed uv install: {platform.system()}")


def _uv_release_asset():
    release = json.loads(_remote_bytes("https://api.github.com/repos/astral-sh/uv/releases/latest"))
    if type(release) is not dict or type(release.get("assets")) is not list:
        raise RuntimeError("Invalid uv release metadata")
    version = _safe_component(release.get("tag_name"))
    extension = ".zip" if platform.system().lower() == "windows" else ".tar.gz"
    expected = "uv-" + _uv_asset_fragment() + extension
    matches = [asset for asset in release["assets"] if type(asset) is dict and asset.get("name") == expected]
    if len(matches) != 1:
        raise RuntimeError("Could not find an unambiguous compatible uv release asset")
    return version, matches[0]


def _latest_uv_asset() -> tuple[str, str, str]:
    version, asset = _uv_release_asset()
    return version, asset["name"], asset.get("browser_download_url", "")


def resolve_managed_runtime_plan(runtime_id: str, *, validate: Callable[[], None] = lambda: None,
                                 cancelled: Callable[[], bool] | None = None) -> ArchiveRuntimePlan:
    """Explicit network metadata resolution; never called by a passive read.

    The caller reviews this immutable result before install_runtime_plan. The
    legacy explicit Install button performs these two steps in the same action.
    """
    if runtime_id not in {"node", "uv"}:
        raise RuntimeError("This runtime has a separate canonical installer")
    def check():
        validate()
        if cancelled and cancelled():
            raise RuntimeError("Runtime plan resolution cancelled")
    check()
    revision = runtime_install_revision(runtime_id)
    if runtime_id == "node":
        version = _latest_node_lts_version()
        check()
        name, _kind = _node_asset_name(version)
        checksum = _node_checksum(version, name)
        check()
        url = f"https://nodejs.org/dist/{version}/{name}"
        size = _remote_size(url)
        candidates = ("node.exe",) if platform.system().lower() == "windows" else ("bin/node",)
    else:
        version, asset = _uv_release_asset()
        name, url, size = asset["name"], asset.get("browser_download_url"), asset.get("size")
        digest = asset.get("digest")
        if type(digest) is not str or not digest.startswith("sha256:"):
            raise RuntimeError("uv release is missing its pinned SHA-256 digest")
        checksum = digest.removeprefix("sha256:")
        candidates = ("uv.exe",) if platform.system().lower() == "windows" else ("uv", "bin/uv")
    check()
    plan = make_archive_runtime_plan(runtime_id, version=version, url=url, sha256=checksum,
        size_bytes=size, asset_name=name, executable_candidates=candidates)
    if plan.expected_revision != revision:
        raise RuntimeError("Managed runtime changed during plan resolution")
    return plan


def _install_uv(progress: Callable[[str], None] | None = None) -> RuntimeInstallResult:
    return install_runtime_plan(resolve_managed_runtime_plan("uv"), progress=progress)


def _install_playwright_chrome(progress: Callable[[str], None] | None = None) -> RuntimeInstallResult:
    from row_bot.browser.runtime import install_managed_browser_runtime

    result = install_managed_browser_runtime(progress=progress)
    if not result.ok:
        return RuntimeInstallResult(False, "playwright-chrome", result.message)
    log_event(
        "mcp.runtime_installed",
        runtime="playwright-chrome",
        browsers_dir=result.browsers_dir,
        executable_path=result.executable_path,
        playwright_version=result.package_version,
        chromium_revision=result.chromium_revision,
    )
    return RuntimeInstallResult(
        True,
        "playwright-chrome",
        result.message,
        result.browsers_dir,
        f"playwright-{result.package_version}/chromium-{result.chromium_revision}",
    )


def install_managed_runtime(runtime_id: str, progress: Callable[[str], None] | None = None) -> RuntimeInstallResult:
    runtime_id = runtime_id.strip().lower()
    requirement = _RUNTIME_DEFS.get(runtime_id)
    if not requirement:
        return RuntimeInstallResult(False, runtime_id, f"Unknown runtime: {runtime_id}")
    if not requirement.managed:
        return RuntimeInstallResult(False, runtime_id, f"{requirement.label} must be installed manually.")
    RUNTIMES_DIR.mkdir(parents=True, exist_ok=True)
    try:
        if runtime_id == "node":
            return _install_node(progress)
        if runtime_id == "uv":
            return _install_uv(progress)
        if runtime_id == "playwright-chrome":
            return _install_playwright_chrome(progress)
        return RuntimeInstallResult(False, runtime_id, f"No managed installer exists for {requirement.label}.")
    except Exception as exc:
        log_event("mcp.runtime_install_failed", level=30, runtime=runtime_id, error=str(exc))
        return RuntimeInstallResult(False, runtime_id, f"Failed to install {requirement.label}: {exc}")
