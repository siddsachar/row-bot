"""Runtime path helpers for source and packaged Row-Bot layouts."""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Mapping
from pathlib import Path

logger = logging.getLogger(__name__)

PACKAGE_DIR = Path(__file__).resolve().parent
SRC_DIR = PACKAGE_DIR.parent
APP_ROOT = SRC_DIR.parent
APP_ROOT_ENV = "ROW_BOT_APP_ROOT"
CONTAINERIZED_ENV = "ROW_BOT_CONTAINERIZED"
#: Runs the server beside python.exe in a Windows install (installer/windows/row_bot_server.c).
WINDOWS_SERVER_EXE = "Row-Bot Server.exe"


def server_python() -> str:
    """The program the server runs as: Row-Bot Server.exe where a Windows install has it, so Windows
    Firewall names Row-Bot, not Python, when the server listens for other devices (B355).

    In a per-user install it runs from ``R\\versions``, a folder no update renames, so Windows asks once
    rather than after every update (B365); beside python.exe when that copy can't be made."""
    if sys.platform == "win32":
        python = Path(sys.executable)
        branded = python.with_name(WINDOWS_SERVER_EXE)
        if branded.is_file():
            stable = _stable_windows_server(python.parent)
            return str(stable or branded)
    return sys.executable


def _stable_windows_server(python_dir: Path) -> Path | None:
    """Row-Bot Server.exe in ``R\\versions``, kept in step with the running version (B365).

    Windows Firewall ties its answer to a program's path, and each version's ``python`` folder has its
    own. So the server runs from copies in ``R\\versions`` itself: the version's signed Row-Bot Server.exe,
    the Python DLLs it loads from its own folder, and a ``._pth`` that points Python at the version's
    folders (CPython reads the ``._pth`` beside its DLL). They are files, not a folder: pruning old
    versions removes folders only, and every uninstaller (5.2's too) removes ``{app}\\versions``.
    Background updates clone ``python`` from the running version, so 5.2 installs have the signed
    program to copy. None (the caller falls back) when this isn't a per-user install or a copy fails,
    for example while another Row-Bot runs an older copy from this install."""
    from row_bot import install_layout

    layout = install_layout.detect(system="windows")
    versions = layout.versions_dir
    if layout.kind is not install_layout.InstallKind.WINDOWS or versions is None or layout.version_root is None \
            or os.path.normcase(layout.version_root / "python") != os.path.normcase(python_dir.resolve()):
        return None
    runtime = f"python{sys.version_info[0]}{sys.version_info[1]}"
    names = [WINDOWS_SERVER_EXE, "python3.dll", f"{runtime}.dll", "vcruntime140.dll", "vcruntime140_1.dll"]
    try:
        pth = _stable_pth(python_dir, (python_dir / f"{runtime}._pth").read_text(encoding="utf-8"))
        for name in names:
            source = python_dir / name
            if source.is_file():
                _replace_if_changed(versions / name, source.read_bytes())
        _replace_if_changed(versions / f"{runtime}._pth", pth.encode("utf-8"))
    except OSError as exc:
        logger.info("The server runs from its version folder: %s", exc)
        return None
    for stale in [*versions.glob("python3*.dll"), *versions.glob("python3*._pth")]:
        if stale.name not in {*names, f"{runtime}._pth"}:
            try:
                stale.unlink()
            except OSError:
                pass  # in use by a Row-Bot still running the older Python; harmless, and gone next time
    return versions / WINDOWS_SERVER_EXE


def _stable_pth(python_dir: Path, text: str) -> str:
    """The version's ``._pth`` with each folder made absolute, in the same order."""
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith(("#", "import ")):
            line = os.path.normpath(python_dir / line)
        lines.append(line)
    return "\n".join(lines) + "\n"


def _replace_if_changed(target: Path, data: bytes) -> None:
    try:
        if target.read_bytes() == data:
            return
    except FileNotFoundError:
        pass
    temporary = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    try:
        temporary.write_bytes(data)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def python_for_children() -> None:
    """Inside Row-Bot Server.exe, what the server starts (helpers, environments, workers) runs as the
    version's python.exe, as it always has.

    The copy in ``R\\versions`` (B365) also takes the version's folder as its prefix, leaves that
    folder off ``sys.path`` and reads site-packages' ``.pth`` files, as python.exe there would."""
    current = Path(sys.executable)
    if current.name != WINDOWS_SERVER_EXE:
        return
    python_dir = current.parent
    if not (python_dir / "python.exe").is_file():
        python_dir = APP_ROOT.parent / "python"
        prefix = str(python_dir)
        sys.prefix = sys.base_prefix = sys.exec_prefix = sys.base_exec_prefix = prefix
        host = os.path.normcase(str(current.parent))
        sys.path[:] = [entry for entry in sys.path if os.path.normcase(entry) != host]
        import site

        site.addsitedir(str(python_dir / "Lib" / "site-packages"))
    python = str(python_dir / "python.exe")
    sys.executable = python
    sys._base_executable = python  # type: ignore[attr-defined]  # venv's choice of interpreter


def is_containerized_runtime(
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Return whether this is the explicitly marked official container runtime."""

    selected = os.environ if environ is None else environ
    return selected.get(CONTAINERIZED_ENV) == "1"


def app_root() -> Path:
    """Return the repository root or installed app payload root."""

    configured = os.environ.get(APP_ROOT_ENV, "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return APP_ROOT


def app_path(*parts: str) -> Path:
    return app_root().joinpath(*parts)


def static_dir() -> Path:
    return app_path("static")


def sounds_dir() -> Path:
    return app_path("sounds")


def bundled_skills_dir() -> Path:
    return app_path("bundled_skills")


def tool_guides_dir() -> Path:
    return app_path("tool_guides")


def guides_dir() -> Path:
    """Row-Bot's own guides (the docs site's Markdown), shipped with every install."""
    return app_path("docs-site", "docs")


def app_icon_path() -> Path:
    return app_path("row-bot.ico")
