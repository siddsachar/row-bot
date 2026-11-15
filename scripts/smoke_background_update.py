"""Rehearse a real background update on an installed Row-Bot (CI only).

The install under test is version N-1. This script makes a release N from it
and serves it from a loopback feed, the way GitHub serves a release:

- the code archive (the installed ``app/`` without ``static/`` and
  ``sounds/``, with ``row_bot.version`` set to N and a marker file),
- the assets archive (with one changed file, so the assets part downloads),
- a ``pylock.toml`` that changes one small pure-Python library to another
  version published on PyPI (so the engine fetches a real wheel, checks its
  hash and installs it with pip from the local file),
- ``row-bot-update-N.json`` and a ``releases.json`` whose body carries the
  SHA-256 manifest block.

With ``--parts DIR`` (release.yml) the release's own parts are served instead
(``scripts/build_update_parts.py parts``): its code archive with only
``row_bot.version`` changed to N, its assets archive and its lock export as
they are, and a lock hash the install does not have. The engine must then
download the code and the lock export but not the assets (the installed
assets are the release's), and plan no library change (the lock export
describes the installed runtime exactly).

Then, through the installed native launcher with ``ROW_BOT_UPDATE_FEED`` (and
its test-only opt-in ``ROW_BOT_UPDATE_FEED_TESTING=1``) set,
it starts N-1 in desktop mode, checks for updates through the client API,
waits until the engine has downloaded, staged and self-tested N, checks that
nothing of N-1 changed, restarts into N with "Restart now", and checks that N
answers, is verified and reports the update. Finally it puts ``state.json``
back and removes N.

    python scripts/smoke_background_update.py --root R --kind windows --port 8110 \\
        --version-root R/versions/5.2.0 [--parts DIR] -- R/Row-Bot.exe --no-tray --no-open --no-splash --no-ollama --port 8110
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from smoke_app import _port_open  # noqa: E402
from smoke_install_layout import (  # noqa: E402
    Launcher,
    _get,
    _read_state,
    _set_current,
    _versions_dir,
    _wait,
    _write_state,
)

ASSET_DIRS = ("static", "sounds")
# Small, pure-Python libraries whose other releases are wheels on PyPI; the
# first one installed is moved to its previous release.
CANDIDATES = ("six", "idna", "zipp", "tzdata")
KEEP = {"pip", "setuptools", "wheel"}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _tar_xz(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:xz", format=tarfile.PAX_FORMAT, preset=6) as tar:
        for name in sorted(files):
            info = tarfile.TarInfo(name)
            info.size = len(files[name])
            info.mtime = 0
            info.mode = 0o755 if name.endswith(("row-bot", ".sh")) else 0o644
            tar.addfile(info, io.BytesIO(files[name]))
    return buffer.getvalue()


def _content_hash(files: dict[str, bytes]) -> str:
    outer = hashlib.sha256()
    for relative in sorted(files):
        outer.update(relative.encode("utf-8") + b"\0")
        outer.update(hashlib.sha256(files[relative]).digest())
    return outer.hexdigest()


def _platform_key() -> str:
    system = {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}[platform.system()]
    machine = platform.machine().lower()
    if machine in {"amd64", "x86_64", "x64"}:
        return f"{system}-x86_64"
    return f"{system}-arm64" if system in {"macos", "windows"} else f"{system}-aarch64"


def _installer_name(version: str) -> str:
    system = platform.system()
    if system == "Windows":
        return f"Row-Bot-{version}-Windows-x64.exe"
    if system == "Darwin":
        return f"Row-Bot-{version}-macOS-{'arm64' if platform.machine() == 'arm64' else 'x86_64'}.dmg"
    return f"Row-Bot-{version}-Linux-{'aarch64' if platform.machine() == 'aarch64' else 'x86_64'}.tar.gz"


def _python(version_root: Path) -> Path:
    if os.name == "nt":
        return version_root / "python" / "python.exe"
    return version_root / "python" / "bin" / "python3"


def _run_python(version_root: Path, code: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "ROW_BOT_"))}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        # -B: -I ignores PYTHONDONTWRITEBYTECODE, and a version folder is never written.
        [str(_python(version_root)), "-I", "-B", "-c", code], check=True, capture_output=True, text=True, env=env,
    ).stdout.strip()


def _invitation(version_root: Path, data_dir: Path, origin: str) -> str:
    """A one-time owner invitation, made the way a person at this computer makes one
    (``row-bot access invite``) with the installed runtime and its data folder."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "ROW_BOT_"))}
    env.update(PYTHONDONTWRITEBYTECODE="1", ROW_BOT_DATA_DIR=str(data_dir))
    code = (
        f"import sys; sys.path.insert(0, {str(version_root / 'app' / 'src')!r}); "
        "from row_bot.access.cli import build_remote_access_parser, dispatch_access_command; "
        "sys.exit(dispatch_access_command(build_remote_access_parser().parse_args("
        f"['access', 'invite', '--data-dir', {str(data_dir)!r}, '--origin', {origin!r}, '--json'])))"
    )
    made = subprocess.run(  # noqa: S603 - the installed runtime
        [str(_python(version_root)), "-I", "-B", "-c", code], capture_output=True, text=True, env=env,
    )
    assert made.returncode == 0, f"row-bot access invite failed: {made.stderr[-2000:]}"
    url = json.loads(made.stdout)["invitation"]["invitation_url"]
    return urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["invitation"][0]


def _installed_dists(version_root: Path) -> dict[str, str]:
    raw = _run_python(
        version_root,
        "import json, importlib.metadata as m; "
        "print(json.dumps({d.metadata['Name']: d.version for d in m.distributions() if d.metadata['Name']}))",
    )
    return json.loads(raw)


def _canonical(name: str) -> str:
    import re

    return re.sub(r"[-_.]+", "-", name).lower()


def _pick_change(dists: dict[str, str]) -> tuple[str, str, str, dict]:
    """(name, installed version, other version, its py3 wheel) from PyPI's JSON API."""
    by_name = {_canonical(name): (name, version) for name, version in dists.items()}
    for candidate in CANDIDATES:
        if candidate not in by_name:
            continue
        name, installed = by_name[candidate]
        with urllib.request.urlopen(f"https://pypi.org/pypi/{candidate}/json", timeout=30) as response:  # noqa: S310
            data = json.load(response)
        versions = sorted(
            (_release_key(v), v) for v, files in data["releases"].items()
            if v != installed and _release_key(v) and any(
                f["packagetype"] == "bdist_wheel" and f["filename"].endswith("-none-any.whl") and not f.get("yanked")
                for f in files
            )
        )
        older = [item for item in versions if item[0] < _release_key(installed)]
        chosen = (older or versions)[-1][1]
        wheel = next(
            f for f in data["releases"][chosen]
            if f["packagetype"] == "bdist_wheel" and f["filename"].endswith("-none-any.whl") and not f.get("yanked")
        )
        return name, installed, chosen, wheel
    raise AssertionError(f"none of {CANDIDATES} is installed")


def _release_key(version: str) -> tuple[int, ...]:
    """Numeric releases only (no pre-releases): '2024.2' -> (2024, 2)."""
    parts = version.split(".")
    return tuple(int(part) for part in parts) if all(part.isdigit() for part in parts) else ()


def _toml_string(value: str) -> str:
    return json.dumps(value)


_PLAN_WITH_THE_APP = """
import json, sys
sys.path.insert(0, sys.argv[1])
from packaging.markers import default_environment
from packaging.tags import sys_tags
from row_bot.updates.planner import installed_distributions, plan_runtime
from row_bot.updates.stager import site_packages_relative
python = sys.argv[2]
site = python + "/" + site_packages_relative(__import__("pathlib").Path(python))
plan = plan_runtime(open(sys.argv[3], encoding="utf-8").read(), environment=default_environment(),
                    tags=list(sys_tags()), installed=installed_distributions(__import__("pathlib").Path(site)))
print(json.dumps({"mode": plan.mode, "reason": plan.reason,
                  "change": [f"{p.name} {p.version_from} -> {p.version_to}" for p in plan.change],
                  "add": [f"{p.name} {p.version_to}" for p in plan.add],
                  "remove": [f"{d.name} {d.version}" for d in plan.remove], "no_wheel": plan.no_wheel}))
"""


def _plan_with_the_app(version_root: Path, pylock: Path) -> dict:
    """The installed app's own planner run against *pylock*, as the engine runs it."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "ROW_BOT_"))}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    out = subprocess.run(
        [str(_python(version_root)), "-I", "-B", "-c", _PLAN_WITH_THE_APP, str(version_root / "app" / "src"),
         str(version_root / "python"), str(pylock)],
        check=True, capture_output=True, text=True, env=env,
    ).stdout
    return json.loads(out.strip().splitlines()[-1])


def _code_with_version(archive: bytes, version: str) -> bytes:
    """The release's code archive with only ``row_bot.version`` (and a marker) changed."""
    files: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:*") as tar:
        for member in tar.getmembers():
            if member.isfile():
                files[member.name] = tar.extractfile(member).read()
    assert "src/row_bot/version.py" in files and "launcher.py" in files, "not a code archive"
    files["src/row_bot/version.py"] = (
        '"""Single source of truth for the Row-Bot version number."""\n\n' f'__version__ = "{version}"\n'
    ).encode()
    files["UPDATE_REHEARSAL.txt"] = f"Staged by the background update rehearsal as {version}.\n".encode()
    return _tar_xz(files)


def build_feed_from_parts(version_root: Path, feed: Path, version: str, parts: Path) -> dict:
    """Release *version* from a release's own parts (``build_update_parts.py parts``)."""
    described = json.loads((parts / "parts.json").read_text(encoding="utf-8"))
    pylock = (parts / described["lock"]["pylock"]["file"]).read_bytes()
    planned = _plan_with_the_app(version_root, parts / described["lock"]["pylock"]["file"])
    assert planned["mode"] == "reuse", (
        "the release's lock export does not describe the installed runtime: " + json.dumps(planned, indent=2))
    blobs = {
        f"row-bot-code-{version}.tar.xz": _code_with_version((parts / described["code"]["file"]).read_bytes(), version),
        f"row-bot-assets-{version}.tar.xz": (parts / described["assets"]["file"]).read_bytes(),
        f"row-bot-pylock-{version}.toml": pylock,
    }
    previous = json.loads((version_root / "version.json").read_text(encoding="utf-8"))["version"]
    manifest = {
        "schema": 1,
        "version": version,
        "min_source_version": previous,
        "code": _part(blobs, f"row-bot-code-{version}.tar.xz"),
        "assets": {**_part(blobs, f"row-bot-assets-{version}.tar.xz"),
                   "content_sha256": described["assets"]["content_sha256"]},
        # A lock hash this install does not have, so the engine plans from the real export.
        "lock": {"hash": "sha256:" + sha256(b"rehearsal " + pylock),
                 "pylock": _part(blobs, f"row-bot-pylock-{version}.toml")},
        "platforms": {_platform_key(): {"python": _run_python(version_root, "import platform; print(platform.python_version())"),
                                        "installer": _installer_name(version)}},
    }
    _write_feed(feed, version, blobs, manifest)
    return {"mode": "parts", "code_bytes": len(blobs[f"row-bot-code-{version}.tar.xz"]),
            "assets_bytes": len(blobs[f"row-bot-assets-{version}.tar.xz"]),
            "assets_file": f"row-bot-assets-{version}.tar.xz", "pylock_file": f"row-bot-pylock-{version}.toml",
            "packages": planned["reason"]}


def _part(blobs: dict[str, bytes], file: str) -> dict:
    return {"file": file, "size": len(blobs[file]), "sha256": sha256(blobs[file])}


def _write_feed(feed: Path, version: str, blobs: dict[str, bytes], manifest: dict) -> None:
    blobs[f"row-bot-update-{version}.json"] = json.dumps(manifest, indent=2).encode()
    blobs[_installer_name(version)] = b"not a real installer"
    feed.mkdir(parents=True, exist_ok=True)
    for file, data in blobs.items():
        (feed / file).write_bytes(data)
    body = (
        f"Background update rehearsal {version}.\n\n<!-- row-bot-update-manifest -->\n```manifest\nschema: 1\nfiles:\n"
        + "".join(f"  {file}: sha256={sha256(data)}\n" for file, data in sorted(blobs.items()))
        + "```\n"
    )
    release = {
        "tag_name": f"v{version}",
        "prerelease": False,
        "draft": False,
        "published_at": "2026-10-11T12:00:00Z",
        "html_url": f"https://github.com/siddsachar/row-bot/releases/tag/v{version}",
        "body": body,
        "assets": [{"name": file, "size": len(data), "browser_download_url": file} for file, data in blobs.items()],
    }
    (feed / "releases.json").write_text(json.dumps([release], indent=2), encoding="utf-8")


def build_feed(version_root: Path, feed: Path, version: str, checkout: Path) -> dict:
    """The release files for *version*, made from the installed version."""
    app = version_root / "app"
    code: dict[str, bytes] = {}
    assets: dict[str, bytes] = {}
    for folder, dirs, names in os.walk(app):
        dirs[:] = [name for name in dirs if name != "__pycache__"]
        for name in names:
            if name.endswith(".pyc"):
                continue
            path = Path(folder) / name
            relative = path.relative_to(app).as_posix()
            target = assets if relative.split("/", 1)[0] in ASSET_DIRS else code
            target[relative] = path.read_bytes()
    code["src/row_bot/version.py"] = (
        '"""Single source of truth for the Row-Bot version number."""\n\n'
        f'__version__ = "{version}"\n'
    ).encode()
    code["UPDATE_REHEARSAL.txt"] = f"Staged by the background update rehearsal as {version}.\n".encode()
    for name in ("row-bot", "install.sh", "uninstall.sh"):
        source = checkout / "installer" / "linux" / name
        if source.is_file():
            code[f"installer/linux/{name}"] = source.read_bytes()
    assets["static/update-rehearsal.txt"] = b"changed asset\n"

    dists = _installed_dists(version_root)
    name, installed, other, wheel = _pick_change(dists)
    lines = ['lock-version = "1.0"', 'created-by = "row-bot update rehearsal"', ""]
    for dist_name, dist_version in sorted(dists.items(), key=lambda item: _canonical(item[0])):
        if _canonical(dist_name) in KEEP:
            continue
        lines += ["[[packages]]", f"name = {_toml_string(dist_name)}"]
        if _canonical(dist_name) == _canonical(name):
            lines += [
                f"version = {_toml_string(other)}",
                f"wheels = [{{ url = {_toml_string(wheel['url'])}, size = {int(wheel['size'])}, "
                f"hashes = {{ sha256 = {_toml_string(wheel['digests']['sha256'])} }} }}]",
            ]
        else:
            lines.append(f"version = {_toml_string(dist_version)}")
        lines.append("")
    pylock = "\n".join(lines).encode()

    python_version = _run_python(version_root, "import platform; print(platform.python_version())")
    blobs = {
        f"row-bot-code-{version}.tar.xz": _tar_xz(code),
        f"row-bot-assets-{version}.tar.xz": _tar_xz(assets),
        f"row-bot-pylock-{version}.toml": pylock,
    }
    previous = json.loads((version_root / "version.json").read_text(encoding="utf-8"))["version"]
    manifest = {
        "schema": 1,
        "version": version,
        "min_source_version": previous,
        "code": _part(blobs, f"row-bot-code-{version}.tar.xz"),
        "assets": {**_part(blobs, f"row-bot-assets-{version}.tar.xz"), "content_sha256": _content_hash(assets)},
        "lock": {"hash": "sha256:" + sha256(pylock), "pylock": _part(blobs, f"row-bot-pylock-{version}.toml")},
        "platforms": {_platform_key(): {"python": python_version, "installer": _installer_name(version)}},
    }
    _write_feed(feed, version, blobs, manifest)
    return {"mode": "made", "library": name, "from": installed, "to": other,
            "code_bytes": len(blobs[f"row-bot-code-{version}.tar.xz"]),
            "assets_bytes": len(blobs[f"row-bot-assets-{version}.tar.xz"]), "wheel_bytes": int(wheel["size"])}


class Client:
    """A desktop client on this computer: one handshake, then the update API.

    With an invitation it signs in first, as the person's window does: on this computer
    only a signed-in client may change anything (A0). Without one it may only look.
    """

    def __init__(self, port: int, invitation: str = "") -> None:
        self.base = f"http://127.0.0.1:{port}"
        self.origin = self.base
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
        if invitation:
            self._call("POST", "/api/access/invitations/claim",
                       {"invitation": invitation, "display_name": "Background update smoke"}, session=False)
        data = self._call("POST", "/api/v1/handshake", {}, session=False)
        self.headers = {"X-Client-Session": data["client_session_id"], "X-CSRF-Token": data["csrf_token"]}

    def _call(self, method: str, path: str, body: dict | None = None, *, session: bool = True,
              idempotency: str | None = None) -> dict:
        headers = {"Origin": self.origin, "Content-Type": "application/json", "Accept": "application/json"}
        if session:
            headers.update(self.headers)
        if idempotency:
            headers["Idempotency-Key"] = idempotency
        # /readyz answers before startup has finished admitting commands: a
        # retryable 503 (or a refused connection) is tried again for a while.
        deadline = time.monotonic() + 90
        while True:
            request = urllib.request.Request(
                self.base + path, data=None if body is None else json.dumps(body).encode(), method=method,
                headers=headers,
            )
            try:
                with self._opener.open(request, timeout=60) as response:  # noqa: S310 - loopback
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as error:
                detail = error.read()[:400]
                if error.code != 503 or time.monotonic() > deadline:
                    raise AssertionError(f"{method} {path}: HTTP {error.code} {detail!r}") from None
            except (urllib.error.URLError, OSError):
                if time.monotonic() > deadline:
                    raise
            time.sleep(1)

    def updates(self) -> dict:
        return self._call("GET", "/api/v1/system/updates")

    def command(self, snapshot: dict, action: str, **extra) -> dict:
        command_id = str(uuid.uuid4())
        body = {"command_id": command_id, "expected_revision": snapshot["revision"], "action": action,
                "version": extra.pop("version", ""), **extra}
        return self._call("POST", "/api/v1/system/updates/commands", body, idempotency=command_id)


def _fingerprint(root: Path) -> dict[str, tuple[int, str]]:
    out = {}
    for folder, _dirs, names in os.walk(root):
        for name in names:
            path = Path(folder) / name
            if path.is_symlink():
                continue
            out[path.relative_to(root).as_posix()] = (path.stat().st_size, sha256(path.read_bytes()))
    return out


def _listening_program(port: int) -> str:
    """Windows: the program listening on *port*, the one Windows Firewall names and remembers by path."""
    script = (f"$c = Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction Stop | Select-Object -First 1; "
              "(Get-Process -Id $c.OwningProcess).Path")
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],  # noqa: S603, S607
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and out.stdout.strip(), f"no program listens on {port}: {out.stderr}"
    return out.stdout.strip()


def _check_server_program(root: Path, version_root: Path, before: str, after: str) -> str:
    """A version that has the signed Row-Bot Server.exe runs the server from R\\versions, a path every
    update keeps, so Windows Firewall asks once (B365). One older than that runs it beside its python."""
    stable = _versions_dir(root, "windows") / "Row-Bot Server.exe"
    if not (version_root / "python" / "Row-Bot Server.exe").is_file():
        return f"the server listened as {before}, then {after} (this install has no Row-Bot Server.exe)"
    assert Path(after) == stable, f"after the update the server listened as {after}, not {stable}"
    if Path(before) != stable:
        assert Path(before) == version_root / "python" / "Row-Bot Server.exe", before
        return f"the server listened as {before}, and after the update as {after}, where it stays"
    return f"the server listened as {after} before and after the update"


def run(root: Path, kind: str, version_root: Path, port: int, command: list[str], timeout: float,
        parts: Path | None = None) -> list[str]:
    results: list[str] = []
    checkout = Path(__file__).resolve().parents[1]
    original = _read_state(root) if (root / "state.json").exists() else None
    previous = (original or {}).get("active") or json.loads(
        (version_root / "version.json").read_text(encoding="utf-8"))["version"]
    major, minor, patch = (previous.split("+")[0].split("-")[0].split(".") + ["0", "0"])[:3]
    version = f"{major}.{minor}.{int(patch) + 1}"
    staged = _versions_dir(root, kind) / version
    # Resolved: macOS temp folders sit under the /var symlink, and Row-Bot
    # refuses a data folder reached through a symlink (tool settings read as
    # unavailable, so the client handshake answers 503).
    work = Path(tempfile.mkdtemp(prefix="row_bot_update_rehearsal_")).resolve()
    feed_dir = work / "feed"
    feed_port = port + 100
    server = None
    launcher = None
    try:
        if _port_open(port) or _port_open(feed_port):
            raise AssertionError(f"port {port} or {feed_port} is already in use")
        if parts is not None:
            built = build_feed_from_parts(version_root, feed_dir, version, parts)
            results.append(f"feed from the release's parts: {previous} -> {version}; code {built['code_bytes']} B, "
                           f"assets {built['assets_bytes']} B; the lock export matches the runtime ({built['packages']})")
        else:
            built = build_feed(version_root, feed_dir, version, checkout)
            results.append(f"feed: {previous} -> {version}; {built['library']} {built['from']} -> {built['to']}; "
                           f"code {built['code_bytes']} B, assets {built['assets_bytes']} B, wheel {built['wheel_bytes']} B")
        feed_log = (work / "feed-requests.log").open("w", encoding="utf-8")
        server = subprocess.Popen(  # noqa: S603 - this Python's http.server on loopback
            [sys.executable, "-m", "http.server", str(feed_port), "--bind", "127.0.0.1", "--directory", str(feed_dir)],
            stdout=subprocess.DEVNULL, stderr=feed_log,
        )
        feed_log.close()  # the server writes one line per request into it
        feed_ready = time.monotonic() + 60
        while _get(f"http://127.0.0.1:{feed_port}/releases.json") != 200:
            if time.monotonic() > feed_ready or server.poll() is not None:
                raise AssertionError("the loopback feed did not start")
            time.sleep(0.5)
        before_app = _fingerprint(version_root / "app")
        os.environ["ROW_BOT_UPDATE_FEED"] = f"http://127.0.0.1:{feed_port}/"
        os.environ["ROW_BOT_UPDATE_FEED_TESTING"] = "1"
        launcher = Launcher(command, work / "data", work / "launcher.log")
        _wait("/readyz", lambda: _get(f"http://127.0.0.1:{port}/readyz") == 200, timeout, launcher)
        program_before = _listening_program(port) if kind == "windows" else ""
        client = Client(port, _invitation(version_root, work / "data", f"http://127.0.0.1:{port}"))
        snapshot = client.updates()
        assert snapshot["mode"] == "self", snapshot
        assert snapshot["current_version"] == previous, snapshot
        started = time.monotonic()
        receipt = client.command(snapshot, "check")
        for _ in range(5):
            if receipt["status"] == "completed":
                break
            time.sleep(2)
            receipt = client.command(client.updates(), "check")
        assert receipt["status"] == "completed", receipt
        assert receipt["snapshot"]["available"]["version"] == version, receipt["snapshot"]

        def prepared() -> bool:
            engine = client.updates()["engine"]
            if engine["phase"] == "failed":
                raise AssertionError(f"preparing {version} failed: {engine}")
            return engine["phase"] == "ready"

        _wait(f"{version} to be downloaded, staged and self-tested", prepared, 1800, launcher)
        staging_seconds = time.monotonic() - started
        metadata = json.loads((staged / "version.json").read_text(encoding="utf-8"))
        assert (staged / "app" / "UPDATE_REHEARSAL.txt").is_file()
        if built["mode"] == "parts":
            requests = (work / "feed-requests.log").read_text(encoding="utf-8", errors="replace")
            # The installed assets are the release's: their content hash matched, so they were not downloaded.
            assert metadata["source"] == "update" and metadata["parts"]["assets"] is None, metadata
            assert f"GET /{built['assets_file']}" not in requests, requests
            assert (staged / "app" / "static").is_dir()
            # The lock export was downloaded and planned, and every library already matched it.
            assert f"GET /{built['pylock_file']}" in requests, requests
            assert metadata["runtime"] == "reuse", metadata
        else:
            assert metadata["source"] == "update" and metadata["runtime"] == "sync", metadata
            assert (staged / "app" / "static" / "update-rehearsal.txt").is_file()
            assert _run_python(staged, f"import importlib.metadata as m; print(m.version({built['library']!r}))") == built["to"]
            assert _run_python(version_root, f"import importlib.metadata as m; print(m.version({built['library']!r}))") == built["from"]
        assert _fingerprint(version_root / "app") == before_app, "staging changed the running version's app/"
        state = _read_state(root)
        assert state["ready"]["version"] == version and state["active"] == previous, state
        results.append(f"staged {version} in {staging_seconds:.0f}s (check, download, clone, pip, unpack, compile, "
                       f"self-test); {previous} unchanged")

        ready = client.updates()
        switched_at = time.monotonic()
        receipt = client.command(ready, "apply", version=version, when="now", force=True)
        assert receipt["status"] == "completed", receipt

        def new_version_answers() -> bool:
            if _get(f"http://127.0.0.1:{port}/readyz") != 200:
                return False
            try:
                return Client(port).updates()["current_version"] == version
            except (AssertionError, OSError, KeyError):
                return False

        _wait(f"{version} to answer after Restart now", new_version_answers, timeout, launcher)
        restart_seconds = time.monotonic() - switched_at
        if kind == "windows":
            results.append(_check_server_program(root, version_root, program_before, _listening_program(port)))

        def verified() -> bool:
            current = _read_state(root)
            return current.get("active") == version and current.get("pending_verification") is False

        _wait("the new version to be verified", verified, 120, launcher)
        state = _read_state(root)
        assert state["previous"] == previous, state
        after = Client(port).updates()
        assert after["engine"]["phase"] == "verified", after["engine"]
        assert after["previous_version"] == previous, after
        if kind == "linux":
            assert Path(os.readlink(root / "current")).name == version
        # Restart now is a restart Row-Bot made: the launcher kept the marker
        # through its own state.json writes, and the new version read it once
        # (goals left working carry on, decision D4).
        assert "restart_reason" not in state, state
        app_log = work / "data" / "row_bot_app.log"

        def counted_as_row_bots_own() -> bool:
            try:
                return "Row-Bot restarted itself (update)" in app_log.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return False

        _wait("the restart to count as Row-Bot's own", counted_as_row_bots_own, 60, launcher)
        results.append(f"Restart now: {version} answered {restart_seconds:.0f}s later, verified, "
                       f"previous={previous}, counted as Row-Bot's own restart")
    except BaseException:
        for log in sorted(work.glob("*.log")):
            print(f"--- {log.name} ---\n{log.read_text(encoding='utf-8', errors='replace')[-8000:]}")
        for name in ("launcher-host.log", "launcher.log", "row_bot_app.log", "row_bot_app.log.prev"):
            path = work / "data" / name
            if path.exists():
                text = path.read_text(encoding="utf-8", errors="replace")
                errors = [line for line in text.splitlines() if "Error" in line or "Traceback" in line]
                print(f"--- data/{name}: error lines ---\n" + "\n".join(errors[-60:]))
                print(f"--- data/{name} ---\n{text[-8000:]}")
        for log in sorted((root / ".updates").glob("*/stage/staging.log")):
            print(f"--- {log} ---\n{log.read_text(encoding='utf-8', errors='replace')[-8000:]}")
        engine_state = root / ".updates" / "engine.json"
        if engine_state.exists():
            print(f"--- engine.json ---\n{engine_state.read_text(encoding='utf-8')}")
        raise
    finally:
        if launcher is not None:
            launcher.stop(port)
        if server is not None:
            server.terminate()
            server.wait(timeout=10)
        os.environ.pop("ROW_BOT_UPDATE_FEED", None)
        os.environ.pop("ROW_BOT_UPDATE_FEED_TESTING", None)
        if original is not None:
            _write_state(root, original)
        else:
            (root / "state.json").unlink(missing_ok=True)
        if kind == "linux":
            _set_current(root, previous)
        shutil.rmtree(staged, ignore_errors=True)
        shutil.rmtree(root / ".updates", ignore_errors=True)
        shutil.rmtree(work, ignore_errors=True)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--root", type=Path, required=True, help="The install root holding state.json")
    parser.add_argument("--kind", choices=("windows", "macos", "linux"), required=True)
    parser.add_argument("--version-root", type=Path, required=True, help="The installed version's folder (app/, python/)")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--parts", type=Path, help="serve this release's own parts (build_update_parts.py parts)")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("give the native launcher command after --")
    for line in run(args.root, args.kind, args.version_root, args.port, command, args.timeout, args.parts):
        print(f"[PASS] {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
