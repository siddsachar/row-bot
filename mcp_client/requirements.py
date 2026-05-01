"""MCP server runtime requirement detection and managed installs."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import shutil
import tarfile
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from mcp_client.config import DATA_DIR
from mcp_client.logging import log_event

RUNTIMES_DIR = DATA_DIR / "runtimes"


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
        install_hint="Install Node.js LTS or let Thoth install a private portable Node.js runtime.",
    ),
    "uv": RuntimeRequirement(
        id="uv",
        label="uv / uvx",
        commands=("uv", "uvx"),
        managed=True,
        setup_url="https://docs.astral.sh/uv/getting-started/installation/",
        install_hint="Install uv/uvx or let Thoth install a private portable uv runtime.",
    ),
    "docker": RuntimeRequirement(
        id="docker",
        label="Docker Desktop",
        commands=("docker",),
        managed=False,
        setup_url="https://www.docker.com/products/docker-desktop/",
        install_hint="Install Docker Desktop, restart Thoth, then test this MCP server again.",
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
            install_hint=str(raw.get("install_hint") or "Install this dependency, restart Thoth if PATH changes, then test again."),
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
        with open(_manifest_path(runtime_id), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_manifest(runtime_id: str, data: dict[str, Any]) -> None:
    path = _manifest_path(runtime_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)


def _managed_bin_dir(runtime_id: str) -> Path | None:
    manifest = _read_manifest(runtime_id)
    bin_dir = manifest.get("bin_dir")
    if bin_dir and Path(str(bin_dir)).exists():
        return Path(str(bin_dir))
    return None


def playwright_browsers_path() -> str:
    return str(RUNTIMES_DIR / "playwright-browsers")


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


def playwright_browser_executable_path() -> str:
    manifest = _read_manifest("playwright-chrome")
    executable_path = manifest.get("executable_path")
    if executable_path and Path(str(executable_path)).exists():
        return str(executable_path)
    found = _find_playwright_chromium_executable(Path(str(manifest.get("browsers_dir") or playwright_browsers_path())))
    return str(found) if found else ""


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
        manifest = _read_manifest("playwright-chrome")
        browsers_dir = Path(str(manifest.get("browsers_dir") or playwright_browsers_path()))
        executable_path = playwright_browser_executable_path()
        available = bool(manifest.get("installed")) and browsers_dir.exists() and bool(executable_path)
        if available:
            return RuntimeCheck(
                requirement=requirement,
                available=True,
                source="managed",
                paths={"PLAYWRIGHT_BROWSERS_PATH": str(browsers_dir), "PLAYWRIGHT_MCP_EXECUTABLE_PATH": executable_path},
                message="Playwright browser is available in Thoth's managed browser cache.",
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
    message = requirement.install_hint or f"Install {requirement.label}, restart Thoth if PATH changes, then test again."
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
            return f"MCP stdio command '{command}' was not found on PATH. {req.label} is required. Install it in Thoth, install it system-wide and restart Thoth, or edit this MCP server to use an absolute executable path."
        return f"MCP stdio command '{command}' was not found on PATH. {req.label} is required. {req.install_hint}"
    return f"MCP stdio command '{command}' was not found on PATH. Install the command, restart Thoth, or edit this MCP server to use an absolute executable path."


def _download(url: str, destination: Path, progress: Callable[[str], None] | None = None) -> None:
    if progress:
        progress(f"Downloading {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "Thoth-MCP-Runtime-Installer/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response, open(destination, "wb") as handle:  # noqa: S310 - explicit user-triggered runtime install
        shutil.copyfileobj(response, handle)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_sha256(path: Path, expected: str | None) -> None:
    if expected and _sha256(path).lower() != expected.lower():
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
    request = urllib.request.Request("https://nodejs.org/dist/index.json", headers={"User-Agent": "Thoth-MCP-Runtime-Installer/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - explicit user-triggered runtime install
        releases = json.loads(response.read().decode("utf-8"))
    for release in releases:
        if release.get("lts"):
            return str(release["version"])
    raise RuntimeError("Could not determine latest Node.js LTS version")


def _node_checksum(version: str, asset_name: str) -> str | None:
    url = f"https://nodejs.org/dist/{version}/SHASUMS256.txt"
    request = urllib.request.Request(url, headers={"User-Agent": "Thoth-MCP-Runtime-Installer/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - explicit user-triggered runtime install
        lines = response.read().decode("utf-8", errors="replace").splitlines()
    for line in lines:
        if line.endswith(" " + asset_name):
            return line.split()[0]
    return None


def _extract_archive(archive: Path, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    destination_root = destination.resolve()

    def _safe_target(name: str) -> Path:
        target = (destination / name).resolve()
        if target != destination_root and destination_root not in target.parents:
            raise RuntimeError(f"Archive contains unsafe path: {name}")
        return target

    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as handle:
            for member in handle.infolist():
                _safe_target(member.filename)
            handle.extractall(destination)
    else:
        with tarfile.open(archive) as handle:
            for member in handle.getmembers():
                _safe_target(member.name)
            handle.extractall(destination)
    children = [item for item in destination.iterdir() if item.is_dir()]
    return children[0] if len(children) == 1 else destination


def _install_node(progress: Callable[[str], None] | None = None) -> RuntimeInstallResult:
    version = _latest_node_lts_version()
    asset_name, _ = _node_asset_name(version)
    base_url = f"https://nodejs.org/dist/{version}"
    with tempfile.TemporaryDirectory(prefix="thoth_node_") as tmp:
        archive = Path(tmp) / asset_name
        expected = _node_checksum(version, asset_name)
        _download(f"{base_url}/{asset_name}", archive, progress)
        _verify_sha256(archive, expected)
        install_root = RUNTIMES_DIR / "node" / version
        if install_root.exists():
            shutil.rmtree(install_root)
        extracted = _extract_archive(archive, install_root)
    bin_dir = extracted if platform.system().lower() == "windows" else extracted / "bin"
    _write_manifest("node", {"version": version, "bin_dir": str(bin_dir), "root": str(extracted), "source": "nodejs.org"})
    log_event("mcp.runtime_installed", runtime="node", version=version, bin_dir=str(bin_dir))
    return RuntimeInstallResult(True, "node", f"Installed Node.js {version} for Thoth.", str(bin_dir), version)


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


def _latest_uv_asset() -> tuple[str, str, str]:
    request = urllib.request.Request("https://api.github.com/repos/astral-sh/uv/releases/latest", headers={"User-Agent": "Thoth-MCP-Runtime-Installer/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - explicit user-triggered runtime install
        release = json.loads(response.read().decode("utf-8"))
    fragment = _uv_asset_fragment()
    assets = release.get("assets") or []
    for asset in assets:
        name = str(asset.get("name") or "")
        if fragment in name and (name.endswith(".zip") or name.endswith(".tar.gz")):
            return str(release.get("tag_name") or "latest"), name, str(asset.get("browser_download_url") or "")
    raise RuntimeError("Could not find a compatible uv release asset")


def _install_uv(progress: Callable[[str], None] | None = None) -> RuntimeInstallResult:
    version, asset_name, url = _latest_uv_asset()
    if not url:
        raise RuntimeError("uv release asset did not include a download URL")
    with tempfile.TemporaryDirectory(prefix="thoth_uv_") as tmp:
        archive = Path(tmp) / asset_name
        _download(url, archive, progress)
        install_root = RUNTIMES_DIR / "uv" / version
        if install_root.exists():
            shutil.rmtree(install_root)
        extracted = _extract_archive(archive, install_root)
    bin_dir = extracted
    if not shutil.which("uv", path=str(bin_dir)) and (extracted / "bin").exists():
        bin_dir = extracted / "bin"
    _write_manifest("uv", {"version": version, "bin_dir": str(bin_dir), "root": str(extracted), "source": "github.com/astral-sh/uv"})
    log_event("mcp.runtime_installed", runtime="uv", version=version, bin_dir=str(bin_dir))
    return RuntimeInstallResult(True, "uv", f"Installed uv {version} for Thoth.", str(bin_dir), version)


def _install_playwright_chrome(progress: Callable[[str], None] | None = None) -> RuntimeInstallResult:
    env = os.environ.copy()
    npx_path, env, missing = resolve_command("npx", env)
    if not npx_path:
        return RuntimeInstallResult(False, "playwright-chrome", missing_command_message("npx", missing))
    browsers_dir = Path(playwright_browsers_path())
    browsers_dir.mkdir(parents=True, exist_ok=True)
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers_dir)
    command = [npx_path, "-y", "playwright", "install", "chromium"]
    if progress:
        progress("Installing Playwright browser dependency")
    completed = subprocess.run(
        command,
        env=env,
        cwd=str(DATA_DIR),
        text=True,
        capture_output=True,
        timeout=900,
        check=False,
    )
    if completed.returncode != 0:
        output_parts = [str(part).strip() for part in (completed.stdout, completed.stderr) if part]
        output = "\n".join(part for part in output_parts if part).strip()
        if len(output) > 2000:
            output = output[:2000] + "..."
        raise RuntimeError(output or f"Playwright install exited with code {completed.returncode}")
    executable_path = _find_playwright_chromium_executable(browsers_dir)
    if not executable_path:
        raise RuntimeError(f"Playwright installed Chromium, but no browser executable was found in {browsers_dir}")
    _write_manifest("playwright-chrome", {
        "installed": True,
        "browsers_dir": str(browsers_dir),
        "browser": "chromium",
        "executable_path": str(executable_path),
        "command": "npx -y playwright install chromium",
        "source": "playwright",
    })
    log_event("mcp.runtime_installed", runtime="playwright-chrome", browsers_dir=str(browsers_dir), executable_path=str(executable_path))
    return RuntimeInstallResult(True, "playwright-chrome", "Installed Playwright browser for Thoth.", str(browsers_dir), "chromium")


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
