"""Build and check the release files that background updates use.

A release publishes, next to its installers, the parts a running Row-Bot
updates itself from (``src/row_bot/updates``):

- ``row-bot-code-<v>.tar.xz``: the app payload without ``static/`` and ``sounds/``
- ``row-bot-assets-<v>.tar.xz``: ``static/`` and ``sounds/``
- ``row-bot-pylock-<v>.toml``: the ``uv.lock`` export the client plans libraries from
- ``row-bot-update-<v>.json``: what the release offers, read by
  ``row_bot.updates.manifest.parse_manifest``

The parts are platform independent, so they are built once, from the same
staged payload the installers ship (``scripts/app_payload_manifest.py`` plus the
built client). Subcommands:

``parts``             archives and the lock export from a staged ``app/`` folder
``describe-platform`` one platform build: its runtime's Python and its installer
``assemble``          ``row-bot-update-<v>.json`` from the parts and the platform builds
``wheel-check``       a changed library without a wheel forces the full installer (D16)
``previous-tags``     the published releases the lock is compared with
``verify-release``    every file a release needs is there and agrees with the update JSON
``release-notes``     the draft release's title and notes from RELEASE_NOTES.md
``container-digests`` ``row-bot-container-<v>.json``: the image digests Docker and cloud
                      installs pull by (D12), written by container.yml after publishing

Run with the repository's Python; only ``wheel-check`` needs ``packaging``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from row_bot import install_layout  # noqa: E402
from row_bot.updates import manifest as update_manifest  # noqa: E402

SCHEMA = 1
#: The platforms every release ships an installer for.
REQUIRED_PLATFORMS = ("windows-x86_64", "macos-arm64", "linux-x86_64")
#: Shipped in the code archive so Linux updates refresh the wrapper and scripts.
LINUX_SHELL_FILES = ("row-bot", "install.sh", "uninstall.sh")
#: Text files that must ship with LF line endings (D17).
TEXT_SUFFIXES = frozenset(
    {".py", ".pyi", ".toml", ".lock", ".txt", ".md", ".json", ".js", ".mjs", ".css", ".html",
     ".yml", ".yaml", ".sh", ".cfg", ".ini", ".csv"}
)
# xz -9 saves 0.5 % on the code archive for seven times the memory.
XZ_PRESET = 6
#: Glibc of the oldest supported Linux (Ubuntu 22.04) for the wheel check.
LINUX_GLIBC_MINOR = 35
#: Oldest supported macOS (D18).
MACOS_MINIMUM = (14, 0)
FULL_INSTALLER_SENTENCE = "This update needs the full installer"
FULL_INSTALLER_NOTE = (
    f"**{FULL_INSTALLER_SENTENCE}.** A library that ships without ready-made builds for every "
    "platform changed, so Row-Bot cannot prepare this update in the background. Row-Bot offers "
    "the download instead: run the installer for your computer and your data stays as it is."
)


class PartsError(Exception):
    """A release file is missing, malformed or inconsistent."""


# ── names ───────────────────────────────────────────────────────────────────


def code_name(version: str) -> str:
    return f"row-bot-code-{version}.tar.xz"


def assets_name(version: str) -> str:
    return f"row-bot-assets-{version}.tar.xz"


def pylock_name(version: str) -> str:
    return f"row-bot-pylock-{version}.toml"


def container_name(version: str) -> str:
    return f"row-bot-container-{version}.json"


def installer_name(key: str, version: str) -> str:
    """The installer asset of one platform, as the 5.1 updater's patterns expect."""
    os_name, _, arch = key.partition("-")
    if os_name == "windows":
        return f"Row-Bot-{version}-Windows-{'x64' if arch == 'x86_64' else 'arm64'}.exe"
    if os_name == "macos":
        return f"Row-Bot-{version}-macOS-{arch}.dmg"
    if os_name == "linux":
        return f"Row-Bot-{version}-Linux-{arch}.tar.gz"
    raise PartsError(f"unknown platform {key!r}")


def release_file_names(version: str, platforms: Iterable[str] = REQUIRED_PLATFORMS) -> list[str]:
    """Every file a release must carry before it is published, and nothing else may."""
    return [
        *(installer_name(key, version) for key in platforms),
        code_name(version),
        assets_name(version),
        pylock_name(version),
        update_manifest.manifest_file_name(version),
    ]


def _check_version(version: str) -> str:
    if not install_layout.is_valid_version_name(version) or not re.match(r"^\d+\.\d+\.\d+", version):
        raise PartsError(f"{version!r} is not a release version")
    return version


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _part(path: Path) -> dict[str, Any]:
    return {"file": path.name, "size": path.stat().st_size, "sha256": sha256_file(path)}


# ── parts ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Member:
    name: str
    path: Path
    mode: int


def _mode(path: Path) -> int:
    if os.name == "nt":
        return 0o755 if path.suffix == ".sh" or path.name == "row-bot" else 0o644
    return 0o755 if path.stat().st_mode & 0o100 else 0o644


def split_app(app: Path) -> tuple[list[_Member], list[_Member]]:
    """The staged ``app/`` as (code, assets) members, refusing what never ships."""
    code: list[_Member] = []
    assets: list[_Member] = []
    problems: list[str] = []
    for folder, dirs, names in os.walk(app):
        dirs.sort()
        for name in sorted(names):
            path = Path(folder) / name
            relative = path.relative_to(app).as_posix()
            if path.is_symlink():
                problems.append(f"{relative} is a symbolic link")
                continue
            if name.endswith((".pyc", ".pyo")) or "__pycache__" in relative.split("/"):
                problems.append(f"{relative} is bytecode (the client compiles what it unpacks)")
                continue
            if path.suffix.lower() in TEXT_SUFFIXES and b"\r\n" in path.read_bytes():
                problems.append(f"{relative} has CRLF line endings (release payloads ship LF)")
                continue
            member = _Member(relative, path, _mode(path))
            is_asset = relative.split("/", 1)[0] in update_manifest.ASSET_DIRS
            if is_asset and path.suffix.lower() == ".py":
                # The installers compile every Python file under app/
                # (src/row_bot/payload_trim.py bytecode), so installed assets
                # would carry __pycache__ and never match the content hash.
                problems.append(f"{relative} is Python code among the assets (static/ and sounds/ hold none)")
                continue
            (assets if is_asset else code).append(member)
    if problems:
        raise PartsError("the staged app cannot be released:\n  " + "\n  ".join(problems[:20]))
    return code, assets


def write_archive(members: Iterable[_Member], target: Path) -> None:
    """A ``tar.xz`` whose bytes depend only on the files: sorted, no times or owners."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    with tarfile.open(temporary, "w:xz", format=tarfile.PAX_FORMAT, preset=XZ_PRESET) as tar:
        for member in sorted(members, key=lambda item: item.name):
            info = tarfile.TarInfo(member.name)
            info.size = member.path.stat().st_size
            info.mtime = 0
            info.mode = member.mode
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            with member.path.open("rb") as handle:
                tar.addfile(info, handle)
    os.replace(temporary, target)


def _uv_export(cwd: Path, target: Path, *, frozen: bool) -> None:
    # uv names a pylock export pylock.toml (or pylock.<name>.toml), so it is
    # written there first and then renamed.
    with tempfile.TemporaryDirectory(prefix="row-bot-pylock-") as folder:
        exported = Path(folder) / "pylock.toml"
        command = [
            "uv", "export", "--frozen" if frozen else "--locked", "--all-extras", "--no-dev",
            "--no-emit-project", "--no-header", "--format", "pylock.toml", "--output-file", str(exported),
        ]
        completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            raise PartsError(f"uv export in {cwd} failed: {completed.stderr.strip() or completed.returncode}")
        target.write_bytes(exported.read_bytes())


def export_pylock(project_root: Path, target: Path) -> None:
    """``uv export`` of the lock in the pylock format, as the client reads it."""
    _uv_export(Path(project_root), Path(target), frozen=False)


def _check_pylock(text: str) -> int:
    try:
        packages = tomllib.loads(text).get("packages")
    except tomllib.TOMLDecodeError as exc:
        raise PartsError(f"the lock export is not TOML: {exc}") from exc
    if not isinstance(packages, list) or not packages:
        raise PartsError("the lock export lists no packages")
    for package in packages:
        files = [*(package.get("wheels") or []), *([package["sdist"]] if package.get("sdist") else [])]
        if not files or not all(re.fullmatch(r"[0-9a-f]{64}", str((item.get("hashes") or {}).get("sha256", "")))
                                for item in files):
            raise PartsError(f"{package.get('name')}: the lock export has a file without a SHA-256")
    return len(packages)


def build_parts(
    app: Path, out: Path, *, version: str, project_root: Path, pylock: Path | None = None
) -> dict[str, Any]:
    """Write the code and assets archives, the lock export and ``parts.json``."""
    _check_version(version)
    app = Path(app)
    for required in ("launcher.py", "src/row_bot/__init__.py", "uv.lock", "static", "sounds"):
        if not (app / required).exists():
            raise PartsError(f"the staged app has no {required}")
    stated = re.search(r'__version__\s*=\s*"([^"]+)"', (app / "src/row_bot/version.py").read_text(encoding="utf-8"))
    if not stated or stated.group(1) != version:
        raise PartsError(f"the staged app is {stated.group(1) if stated else 'unversioned'}, not {version}")
    code, assets = split_app(app)
    for name in LINUX_SHELL_FILES:
        source = Path(project_root) / "installer" / "linux" / name
        if not source.is_file():
            raise PartsError(f"installer/linux/{name} is missing")
        if b"\r\n" in source.read_bytes():
            raise PartsError(f"installer/linux/{name} has CRLF line endings")
        code.append(_Member(f"installer/linux/{name}", source, 0o755))
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    write_archive(code, out / code_name(version))
    write_archive(assets, out / assets_name(version))
    lock_target = out / pylock_name(version)
    if pylock is None:
        export_pylock(Path(project_root), lock_target)
    else:
        lock_target.write_bytes(Path(pylock).read_bytes())
    packages = _check_pylock(lock_target.read_text(encoding="utf-8"))
    description = {
        "schema": SCHEMA,
        "version": version,
        "code": _part(out / code_name(version)),
        "assets": {
            **_part(out / assets_name(version)),
            "content_sha256": update_manifest.assets_content_hash(app),
        },
        "lock": {
            "hash": "sha256:" + sha256_file(app / "uv.lock"),
            "pylock": _part(lock_target),
        },
        "files": {"code": len(code), "assets": len(assets), "packages": packages},
    }
    (out / "parts.json").write_text(json.dumps(description, indent=2) + "\n", encoding="utf-8", newline="\n")
    return description


# ── platforms and the update description ────────────────────────────────────


_DESCRIBE = "import json, platform; print(json.dumps([platform.python_version(), platform.system(), platform.machine()]))"


def describe_platform(python: Path, version: str, *, installer_dir: Path | None = None) -> dict[str, str]:
    """What one platform build ships, read from its own runtime."""
    _check_version(version)
    completed = subprocess.run(
        # -B: the runtime may be a signed bundle; nothing is written into it.
        [str(python), "-I", "-B", "-c", _DESCRIBE], capture_output=True, text=True, check=False,
        env={key: value for key, value in os.environ.items() if not key.startswith("PYTHON")},
    )
    if completed.returncode != 0:
        raise PartsError(f"{python} did not run: {completed.stderr.strip()}")
    python_version, system, machine = json.loads(completed.stdout.strip().splitlines()[-1])
    key = update_manifest.platform_key(system, machine)
    if key is None:
        raise PartsError(f"no platform key for {system} {machine}")
    entry = {"key": key, "python": python_version, "installer": installer_name(key, version), "version": version}
    if installer_dir is not None and not (Path(installer_dir) / entry["installer"]).is_file():
        raise PartsError(f"{entry['installer']} is not in {installer_dir}")
    return entry


def _verify_part(folder: Path, part: Mapping[str, Any]) -> None:
    path = folder / str(part["file"])
    if not path.is_file():
        raise PartsError(f"{part['file']} is missing")
    if path.stat().st_size != part["size"] or sha256_file(path) != part["sha256"]:
        raise PartsError(f"{part['file']} does not match parts.json")


def assemble(
    parts_dir: Path, platform_files: Iterable[Path], out: Path, *,
    min_source_version: str | None = None, require: Iterable[str] = (),
    wheel_report: Mapping[str, Any] | None = None,
) -> Path:
    """``row-bot-update-<v>.json``, checked with the client's own parser.

    With the wheel check's report, each build's Python must be the one the
    check planned for (the same major and minor version).
    """
    parts_dir = Path(parts_dir)
    parts = json.loads((parts_dir / "parts.json").read_text(encoding="utf-8"))
    version = _check_version(parts["version"])
    for part in (parts["code"], parts["assets"], parts["lock"]["pylock"]):
        _verify_part(parts_dir, part)
    platforms: dict[str, dict[str, str]] = {}
    for path in platform_files:
        entry = json.loads(Path(path).read_text(encoding="utf-8"))
        if entry.get("version") != version:
            raise PartsError(f"{path} describes {entry.get('version')}, not {version}")
        if entry["installer"] != installer_name(entry["key"], version):
            raise PartsError(f"{path}: unexpected installer name {entry['installer']}")
        if entry["key"] in platforms:
            raise PartsError(f"two builds describe {entry['key']}")
        platforms[entry["key"]] = {"python": entry["python"], "installer": entry["installer"]}
    missing = [key for key in require if key not in platforms]
    if missing:
        raise PartsError(f"no build describes {', '.join(missing)}")
    # The client compares the exact Python version (a different one needs the
    # full installer), so the builds must run the Pythons the check planned for.
    for key, planned in ((wheel_report or {}).get("targets") or {}).items():
        built = platforms.get(key, {}).get("python")
        if built and built != str(planned):
            raise PartsError(f"the {key} build runs Python {built}, but the wheel check planned for {planned}")
    description: dict[str, Any] = {"schema": SCHEMA, "version": version}
    if min_source_version:
        description["min_source_version"] = min_source_version
    description.update(
        code=parts["code"],
        assets=parts["assets"],
        lock=parts["lock"],
        platforms={key: platforms[key] for key in sorted(platforms)},
    )
    raw = json.dumps(description, indent=2) + "\n"
    try:
        update_manifest.parse_manifest(raw, expected_version=version)
    except update_manifest.ManifestError as exc:
        raise PartsError(f"the client would refuse this update description: {exc}") from exc
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    target = out / update_manifest.manifest_file_name(version)
    target.write_text(raw, encoding="utf-8", newline="\n")
    return target


# ── D16: libraries without wheels ───────────────────────────────────────────


def target_environment(key: str, python: str) -> tuple[dict[str, str], list[Any]]:
    """Marker environment and wheel tags of a supported platform, as its runtime sees them."""
    from packaging import tags

    numbers = [int(part) for part in python.split(".")[:3]]
    major, minor = numbers[0], numbers[1]
    full = ".".join(str(part) for part in (numbers + [0, 0])[:3])
    os_name, _, arch = key.partition("-")
    if os_name == "windows":
        platforms = ["win_amd64" if arch == "x86_64" else "win_arm64"]
        env = {"os_name": "nt", "sys_platform": "win32", "platform_system": "Windows",
               "platform_machine": "AMD64" if arch == "x86_64" else "ARM64"}
    elif os_name == "macos":
        machine = "arm64" if arch == "arm64" else "x86_64"
        platforms = list(tags.mac_platforms(MACOS_MINIMUM, machine))
        env = {"os_name": "posix", "sys_platform": "darwin", "platform_system": "Darwin", "platform_machine": machine}
    elif os_name == "linux":
        machine = "x86_64" if arch == "x86_64" else "aarch64"
        legacy = {17: "manylinux2014", 12: "manylinux2010", 5: "manylinux1"}
        platforms = []
        for glibc in range(LINUX_GLIBC_MINOR, 4, -1):
            platforms.append(f"manylinux_2_{glibc}_{machine}")
            if glibc in legacy and (machine == "x86_64" or glibc == 17):
                platforms.append(f"{legacy[glibc]}_{machine}")
        platforms.append(f"linux_{machine}")
        env = {"os_name": "posix", "sys_platform": "linux", "platform_system": "Linux", "platform_machine": machine}
    else:
        raise PartsError(f"unknown platform {key!r}")
    interpreter = f"cp{major}{minor}"
    env.update(
        implementation_name="cpython", implementation_version=full, platform_python_implementation="CPython",
        platform_release="", platform_version="", python_full_version=full, python_version=f"{major}.{minor}",
    )
    found = [
        *tags.cpython_tags((major, minor), abis=[interpreter], platforms=platforms),
        *tags.compatible_tags((major, minor), interpreter=interpreter, platforms=platforms),
    ]
    return env, found


def _installed_from_pylock(text: str, env: Mapping[str, str]) -> dict[str, Any]:
    from packaging.markers import Marker

    from row_bot.updates.planner import InstalledDist, canonical_name

    out: dict[str, Any] = {}
    for package in tomllib.loads(text).get("packages") or []:
        marker = package.get("marker")
        if marker and not Marker(str(marker)).evaluate({**env, "extra": ""}):
            continue
        out[canonical_name(package["name"])] = InstalledDist(package["name"], str(package["version"]), "")
    return out


def wheel_check(
    new_pylock: str, previous: Mapping[str, str], targets: Mapping[str, str],
    previous_pythons: Mapping[str, Mapping[str, str]] | None = None,
) -> dict[str, Any]:
    """What the client's planner would do on each platform, from each previous lock.

    *previous* maps a label (a release tag) to that release's lock export, and
    *targets* a platform key to its Python version. A changed or added library
    without a wheel for a platform sends everyone there to the full installer,
    and so does a different Python: *previous_pythons* maps a label to the
    ``platforms`` of that release's update description, where it has one.
    """
    from row_bot.updates.planner import plan_runtime

    report: dict[str, Any] = {"previous": sorted(previous), "targets": dict(targets), "platforms": {}, "no_wheel": {},
                              "python_changes": {}}
    for label, platforms in sorted((previous_pythons or {}).items()):
        for key, python in sorted(targets.items()):
            before = (platforms.get(key) or {}).get("python")
            if before and before != python:
                report["python_changes"][f"{key} from {label}"] = f"{before} -> {python}"
    for key, python in sorted(targets.items()):
        env, found = target_environment(key, python)
        for label, text in sorted(previous.items()):
            plan = plan_runtime(new_pylock, environment=env, tags=found,
                                installed=_installed_from_pylock(text, env))
            report["platforms"][f"{key} from {label}"] = {
                "python": python,
                "mode": plan.mode,
                "change": [f"{item.name} {item.version_from} -> {item.version_to}" for item in plan.change],
                "add": [f"{item.name} {item.version_to}" for item in plan.add],
                "remove": [f"{item.name} {item.version}" for item in plan.remove],
                "no_wheel": list(plan.no_wheel),
            }
            if plan.no_wheel:
                report["no_wheel"][f"{key} from {label}"] = list(plan.no_wheel)
    report["needs_full_installer"] = bool(report["no_wheel"] or report["python_changes"])
    return report


def export_pylock_at(ref: str, project_root: Path) -> str:
    """The lock export of an earlier commit or tag (its ``pyproject.toml`` and ``uv.lock``)."""
    with tempfile.TemporaryDirectory(prefix="row-bot-previous-lock-") as folder:
        work = Path(folder)
        for name in ("pyproject.toml", "uv.lock"):
            shown = subprocess.run(["git", "show", f"{ref}:{name}"], cwd=project_root, capture_output=True, check=False)
            if shown.returncode != 0:
                raise PartsError(f"git show {ref}:{name} failed: {shown.stderr.decode(errors='replace').strip()}")
            (work / name).write_bytes(shown.stdout)
        target = work / "previous.toml"
        _uv_export(work, target, frozen=True)
        return target.read_text(encoding="utf-8")


def previous_tags(version: str, releases: Iterable[Mapping[str, Any]]) -> list[str]:
    """The newest published release before *version*, and the newest stable one if different."""
    candidates = []
    for release in releases:
        tag = str(release.get("tagName") or "")
        if release.get("isDraft") or not re.fullmatch(r"v\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?", tag):
            continue
        if install_layout.compare_versions(tag[1:], version) < 0:
            candidates.append((tag, bool(release.get("isPrerelease"))))
    ordered = sorted(candidates, key=lambda item: _VersionKey(item[0][1:]), reverse=True)
    picked: list[str] = []
    if ordered:
        picked.append(ordered[0][0])
    stable = next((tag for tag, prerelease in ordered if not prerelease), None)
    if stable and stable not in picked:
        picked.append(stable)
    return picked


class _VersionKey:
    def __init__(self, version: str) -> None:
        self.version = version

    def __lt__(self, other: "_VersionKey") -> bool:
        return install_layout.compare_versions(self.version, other.version) < 0


# ── publish gate ────────────────────────────────────────────────────────────


def verify_release(
    version: str, listing: Iterable[Mapping[str, Any]], update_json: bytes, *,
    platforms: Iterable[str] = REQUIRED_PLATFORMS,
) -> list[str]:
    """Problems that stop a release from being published (empty when it can be).

    *listing* is every release file as ``{"name", "size", "sha256"}``; the
    update description must name exactly those parts and installers, and the
    release carries nothing else (the SHA-256 block vouches for every file, and
    a stray installer could match the 5.1 updater's patterns).
    """
    _check_version(version)
    files = {str(item["name"]): item for item in listing}
    expected = release_file_names(version, platforms)
    problems = [f"{name} is missing" for name in expected if name not in files]
    problems += [f"{name} is not a file of release {version}" for name in sorted(files) if name not in expected]
    try:
        described = update_manifest.parse_manifest(update_json, expected_version=version)
    except update_manifest.ManifestError as exc:
        return problems + [f"the update description is not valid: {exc}"]
    named = [described.code, described.assets] + ([described.pylock] if described.pylock else [])
    if described.pylock is None:
        problems.append("the update description has no lock export")
    for part in named:
        found = files.get(part.file)
        if found is None:
            problems.append(f"the update description names {part.file}, which the release does not carry")
        elif int(found["size"]) != part.size or str(found["sha256"]).lower() != part.sha256:
            problems.append(f"{part.file} differs from the update description")
    for key in platforms:
        entry = described.platform(key)
        if entry is None:
            problems.append(f"the update description has no {key} entry")
        elif entry.installer != installer_name(key, version):
            problems.append(f"the {key} entry names {entry.installer}")
    for name, item in files.items():
        if not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256", "")).lower()):
            problems.append(f"{name} has no SHA-256")
    return problems


# ── release notes ───────────────────────────────────────────────────────────


def release_notes(version: str, notes: str, report: Mapping[str, Any] | None = None) -> tuple[str, str]:
    """The draft's title and body: the version's section of RELEASE_NOTES.md, refused while it still says TODO:."""
    lines = notes.replace("\r\n", "\n").split("\n")
    start = next(
        (index for index, line in enumerate(lines) if re.match(rf"^##\s+v?{re.escape(version)}(\s|$)", line)), None
    )
    if start is None:
        title, body = f"v{version}", f"## v{version}\n\nRelease notes for {version} are on their way.\n"
    else:
        end = next((index for index in range(start + 1, len(lines)) if re.match(r"^##\s+v?\d", lines[index])),
                   len(lines))
        section = lines[start:end]
        while section and section[-1].strip() in {"", "---"}:
            section.pop()
        title = section[0].lstrip("#").strip()
        body = "\n".join(section) + "\n"
        if "TODO:" in body:
            # cut_release.py's For Hosts placeholder, or any other note left to write: hosts act on these notes.
            raise PartsError(f"v{version}'s section of RELEASE_NOTES.md still says TODO: write it before releasing")
    if report and report.get("needs_full_installer") and FULL_INSTALLER_SENTENCE.lower() not in body.lower():
        body = body.rstrip("\n") + "\n\n" + FULL_INSTALLER_NOTE + "\n"
    return title, body


def notes_say_full_installer(body: str) -> bool:
    return FULL_INSTALLER_SENTENCE.lower() in (body or "").lower()


# ── container digests (D12) ─────────────────────────────────────────────────

CONTAINER_PLATFORMS = ("linux/amd64", "linux/arm64")


def container_digests(version: str, image: str, manifest_digest: str, platforms: Mapping[str, str]) -> dict[str, Any]:
    """What a Docker or cloud install pulls: the multi-arch index and each platform's image, by digest."""
    _check_version(version)
    if not re.fullmatch(r"[a-z0-9.-]+(?::\d+)?/[a-z0-9._/-]+", image):
        raise PartsError(f"{image!r} is not an image name")
    digests = {"manifest": manifest_digest, **platforms}
    for label, digest in digests.items():
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise PartsError(f"{label}: {digest!r} is not a sha256 digest")
    if sorted(platforms) != sorted(CONTAINER_PLATFORMS):
        raise PartsError(f"expected digests for {', '.join(CONTAINER_PLATFORMS)}")
    return {"schema": SCHEMA, "version": version, "image": image, "tag": version, "manifest_digest": manifest_digest,
            "platforms": {key: platforms[key] for key in CONTAINER_PLATFORMS}}


# ── command line ────────────────────────────────────────────────────────────


def _targets(values: Iterable[str]) -> dict[str, str]:
    out = {}
    for value in values:
        key, _, python = value.partition(":")
        if not python:
            raise PartsError(f"--target {value!r} is not KEY:PYTHON")
        out[key] = python
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    commands = parser.add_subparsers(dest="command", required=True)

    parts = commands.add_parser("parts", help="archives and lock export from a staged app folder")
    parts.add_argument("--app", type=Path, required=True)
    parts.add_argument("--version", required=True)
    parts.add_argument("--out", type=Path, required=True)
    parts.add_argument("--project-root", type=Path, default=_REPO_ROOT)
    parts.add_argument("--pylock", type=Path, help="use this lock export instead of running uv")

    describe = commands.add_parser("describe-platform", help="one platform build's Python and installer")
    describe.add_argument("--python", type=Path, required=True)
    describe.add_argument("--version", required=True)
    describe.add_argument("--installer-dir", type=Path)
    describe.add_argument("--out", type=Path, required=True)

    build = commands.add_parser("assemble", help="row-bot-update-<v>.json")
    build.add_argument("--parts", type=Path, required=True)
    build.add_argument("--platform", type=Path, action="append", default=[])
    build.add_argument("--require", action="append", default=[])
    build.add_argument("--min-source-version")
    build.add_argument("--wheel-check", type=Path, help="the wheel check's report: builds must run its Pythons")
    build.add_argument("--out", type=Path, required=True)

    check = commands.add_parser("wheel-check", help="D16: changed libraries without wheels")
    check.add_argument("--pylock", type=Path, required=True)
    check.add_argument("--previous-ref", action="append", default=[], help="a git tag or commit to compare with")
    check.add_argument("--previous-pylock", action="append", default=[], help="LABEL=FILE")
    check.add_argument("--previous-update-json", action="append", default=[],
                       help="LABEL=FILE: an earlier release's row-bot-update-<v>.json, for its Pythons")
    check.add_argument("--target", action="append", default=[], help="KEY:PYTHON, e.g. windows-x86_64:3.13")
    check.add_argument("--acknowledge", action="store_true", help="the release notes say it needs the full installer")
    check.add_argument("--project-root", type=Path, default=_REPO_ROOT)
    check.add_argument("--out", type=Path)

    tags = commands.add_parser("previous-tags", help="releases to compare the lock with (reads gh)")
    tags.add_argument("--version", required=True)

    verify = commands.add_parser("verify-release", help="the publish gate")
    verify.add_argument("--version", required=True)
    verify.add_argument("--listing", type=Path, required=True, help='JSON list of {"name", "size", "sha256"}')
    verify.add_argument("--update-json", type=Path, required=True)

    notes = commands.add_parser("release-notes", help="the draft release's title and notes")
    notes.add_argument("--version", required=True)
    notes.add_argument("--notes", type=Path, required=True)
    notes.add_argument("--wheel-check", type=Path)
    notes.add_argument("--title-out", type=Path, required=True)
    notes.add_argument("--body-out", type=Path, required=True)

    container = commands.add_parser("container-digests", help="row-bot-container-<v>.json (D12)")
    container.add_argument("--version", required=True)
    container.add_argument("--image", required=True)
    container.add_argument("--manifest-digest", required=True)
    container.add_argument("--platform", action="append", default=[], help="PLATFORM=DIGEST, e.g. linux/amd64=sha256:...")
    container.add_argument("--out-dir", type=Path, required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "parts":
            result = build_parts(args.app, args.out, version=args.version, project_root=args.project_root,
                                 pylock=args.pylock)
            print(json.dumps(result, indent=2))
        elif args.command == "describe-platform":
            entry = describe_platform(args.python, args.version, installer_dir=args.installer_dir)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(entry, indent=2) + "\n", encoding="utf-8", newline="\n")
            print(json.dumps(entry))
        elif args.command == "assemble":
            report = json.loads(args.wheel_check.read_text(encoding="utf-8")) if args.wheel_check else None
            target = assemble(args.parts, args.platform, args.out, min_source_version=args.min_source_version,
                              require=args.require, wheel_report=report)
            print(target.read_text(encoding="utf-8"))
        elif args.command == "wheel-check":
            previous = {ref: export_pylock_at(ref, args.project_root) for ref in args.previous_ref}
            for item in args.previous_pylock:
                label, _, file = item.partition("=")
                previous[label] = Path(file).read_text(encoding="utf-8")
            pythons = {}
            for item in args.previous_update_json:
                label, _, file = item.partition("=")
                pythons[label] = json.loads(Path(file).read_text(encoding="utf-8")).get("platforms") or {}
            report = wheel_check(args.pylock.read_text(encoding="utf-8"), previous, _targets(args.target), pythons)
            report["acknowledged"] = bool(args.acknowledge)
            if args.out:
                args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
            if not previous:
                print("No earlier release to compare with: nothing to check.")
            for label, item in report["platforms"].items():
                print(f"{label}: {item['mode']}; {len(item['change'])} changed, {len(item['add'])} added, "
                      f"{len(item['remove'])} removed")
            for label, names in report["no_wheel"].items():
                print(f"::warning::{label}: no wheel for {', '.join(names)}")
            for label, change in report["python_changes"].items():
                print(f"::warning::{label}: Python {change}")
            if report["needs_full_installer"] and not args.acknowledge:
                print("::error::A library without a wheel changed, or a Python: this update needs the full installer. "
                      "Run the release again with acknowledge_full_installer=true; the release notes then say so.")
                return 1
        elif args.command == "previous-tags":
            listed = subprocess.run(
                ["gh", "release", "list", "--exclude-drafts", "--limit", "100", "--json", "tagName,isPrerelease,isDraft"],
                capture_output=True, text=True, check=False,
            )
            if listed.returncode != 0:
                raise PartsError(f"gh release list failed: {listed.stderr.strip()}")
            print(" ".join(previous_tags(args.version, json.loads(listed.stdout or "[]"))))
        elif args.command == "verify-release":
            listing = json.loads(args.listing.read_text(encoding="utf-8"))
            problems = verify_release(args.version, listing, args.update_json.read_bytes())
            for problem in problems:
                print(f"::error::{problem}")
            if problems:
                return 1
            print(f"{args.version}: all {len(listing)} release files are present and agree with the update description")
        elif args.command == "container-digests":
            platforms = dict(item.split("=", 1) for item in args.platform)
            described = container_digests(args.version, args.image, args.manifest_digest, platforms)
            args.out_dir.mkdir(parents=True, exist_ok=True)
            target = args.out_dir / container_name(args.version)
            target.write_text(json.dumps(described, indent=2) + "\n", encoding="utf-8", newline="\n")
            print(target.read_text(encoding="utf-8"))
        elif args.command == "release-notes":
            report = json.loads(args.wheel_check.read_text(encoding="utf-8")) if args.wheel_check else None
            title, body = release_notes(args.version, args.notes.read_text(encoding="utf-8"), report)
            args.title_out.write_text(title + "\n", encoding="utf-8", newline="\n")
            args.body_out.write_text(body, encoding="utf-8", newline="\n")
            print(title)
    except PartsError as exc:
        print(f"::error::{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
