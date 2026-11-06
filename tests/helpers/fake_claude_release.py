"""A fake of Anthropic's Claude Code release location on loopback, for "Use my Claude plan" installs.

    release = FakeClaudeRelease(tmp_path, monkeypatch)       # serves /latest, /<v>/manifest.json, the binary
    with release:
        ...  # code under test downloads from release.url
    assert release.installs()[0]["argv"] == ["install", "latest"]

It serves what the native installer reads: ``<url>/latest`` (the newest version), ``<url>/<version>/manifest.json``
(``platforms.<platform>.checksum`` and ``size``) and ``<url>/<version>/<platform>/claude[.exe]``. The "binary" is a
small stand-in for Claude Code's own: run as ``<binary> install latest`` it records its arguments and environment
and puts a fake Claude Code (``tests/helpers/fake_claude.py``) where the native installer puts the real one,
``~/.local/bin/claude`` (``claude.exe`` on Windows). On Windows it is setuptools' console launcher with the script
beside the download, so Windows runs a real executable.

``checksum`` lists a wrong checksum, ``platforms`` lists other platforms only, ``install_exit`` makes the install
step fail, ``latest_status``/``binary_status`` answer an error. HOME/USERPROFILE point at a scratch home and PATH at
an empty folder, so no real Claude Code is ever found or run, and nothing reaches Anthropic.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from tests.helpers.fake_claude import write_fake_claude_program

_INSTALLER = r'''
import json, os, shutil, sys
log = sys.argv[1]
args = sys.argv[2:]
watched = ("ANTHROPIC", "CLAUDE", "DISABLE", "ROW_BOT", "OPENAI")
env = {k: v for k, v in os.environ.items()
       if k.upper().startswith(watched) or any(part in k.upper() for part in ("KEY", "TOKEN", "SECRET"))}
with open(log, "a", encoding="utf-8") as out:
    out.write(json.dumps({"argv": args, "env": env, "cwd": os.getcwd()}) + "\n")
plan = json.load(open(os.environ.get("FAKE_RELEASE_PLAN") or os.path.join(os.path.dirname(log), "plan.json"),
                      encoding="utf-8"))
if args[:1] != ["install"] or plan["install_exit"]:
    sys.exit(plan["install_exit"] or 2)
bin_dir = os.path.join(os.path.expanduser("~"), ".local", "bin")
os.makedirs(bin_dir, exist_ok=True)
if os.name == "nt":
    shutil.copyfile(plan["launcher"], os.path.join(bin_dir, "claude.exe"))
    with open(os.path.join(bin_dir, "claude-script.py"), "w", encoding="utf-8") as script:
        script.write('#!"%s"\nimport runpy, sys\nsys.argv = [%r, %r, *sys.argv[1:]]\nrunpy.run_path(%r, run_name="__main__")\n'
                     % (plan["python"], plan["script"], plan["config"], plan["script"]))
else:
    launcher = os.path.join(bin_dir, "claude")
    with open(launcher, "w", encoding="utf-8") as shim:
        shim.write('#!/bin/sh\nexec "%s" "%s" "%s" "$@"\n' % (plan["python"], plan["script"], plan["config"]))
    os.chmod(launcher, 0o755)
'''


def _launcher() -> Path | None:
    """setuptools' console launcher: a real .exe that runs ``<its name>-script.py`` beside it."""
    spec = importlib.util.find_spec("setuptools")
    folders = spec.submodule_search_locations if spec else None
    if not folders:
        return None
    name = "cli-arm64.exe" if os.environ.get("PROCESSOR_ARCHITECTURE", "").upper() == "ARM64" else "cli-64.exe"
    candidate = Path(list(folders)[0]) / name
    return candidate if candidate.is_file() else None


class FakeClaudeRelease:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, version: str = "2.1.300",
                 installed_version: str = "2.1.300 (Claude Code)", checksum: str | None = None,
                 platforms: list[str] | None = None, install_exit: int = 0, latest_status: int = 200,
                 binary_status: int = 200, login_code: str = "", login_marker: str = "") -> None:
        from row_bot.providers import claude_install, claude_subscription

        self.root = tmp_path / "fake-claude-release"
        self.root.mkdir(parents=True, exist_ok=True)
        self.home = tmp_path / "fake-claude-home"
        self.home.mkdir(exist_ok=True)
        empty = tmp_path / "fake-claude-empty-path"
        empty.mkdir(exist_ok=True)
        monkeypatch.setenv("PATH", str(empty))
        for name in ("HOME", "USERPROFILE", "APPDATA"):
            monkeypatch.setenv(name, str(self.home))
        monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
        claude_subscription.forget_claude_plan_status()
        self.version = version
        self.platform = claude_install.claude_code_platform() or "linux-x64"
        self.windows = self.platform.startswith("win32-")
        self.install_log = self.root / "installs.jsonl"
        program = self.root / "claude-code"
        script, config = write_fake_claude_program(program, version=installed_version, login_code=login_code,
                                                   login_marker=login_marker,
                                                   auth={"loggedIn": False}, auth_exit=1)
        self.claude_log = program / "runs.jsonl"
        installer = self.root / "installer.py"
        installer.write_text(_INSTALLER, encoding="utf-8")
        launcher = _launcher() if self.windows else None
        if self.windows and launcher is None:
            pytest.skip("setuptools' console launcher stands in for claude.exe on Windows")
        (self.root / "plan.json").write_text(json.dumps({
            "install_exit": install_exit, "python": sys.executable, "script": str(script), "config": str(config),
            "launcher": str(launcher or ""),
        }), encoding="utf-8")
        if self.windows:
            self.binary = launcher.read_bytes()  # type: ignore[union-attr]
            # The launcher runs "<its own name>-script.py": the name the installer gives the download.
            downloads = self.home / ".claude" / "downloads"
            downloads.mkdir(parents=True, exist_ok=True)
            (downloads / f"claude-{version}-{self.platform}-script.py").write_text(
                f'#!"{sys.executable}"\nimport runpy, sys\nsys.argv = [{str(installer)!r}, '
                f'{str(self.install_log)!r}, *sys.argv[1:]]\nrunpy.run_path({str(installer)!r}, '
                'run_name="__main__")\n', encoding="utf-8")
        else:
            self.binary = (f'#!/bin/sh\nexec "{sys.executable}" "{installer}" "{self.install_log}" "$@"\n'
                           ).encode("utf-8")
        real = hashlib.sha256(self.binary).hexdigest()
        listed = platforms if platforms is not None else [self.platform]
        self.manifest = {"version": version, "platforms": {
            name: {"checksum": checksum or real, "size": len(self.binary)} for name in listed}}
        self.latest_status = latest_status
        self.binary_status = binary_status
        self.requests: list[str] = []
        self._server: ThreadingHTTPServer | None = None
        self.url = ""
        self._monkeypatch = monkeypatch

    # ── The server ─────────────────────────────────────────────────────

    def __enter__(self) -> FakeClaudeRelease:
        release = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: Any) -> None:
                pass

            def do_GET(self) -> None:  # noqa: N802
                release.requests.append(self.path)
                name = "claude.exe" if release.windows else "claude"
                if self.path == "/latest":
                    self._answer(release.latest_status, (release.version + "\n").encode())
                elif self.path == f"/{release.version}/manifest.json":
                    self._answer(200, json.dumps(release.manifest).encode())
                elif self.path == f"/{release.version}/{release.platform}/{name}":
                    self._answer(release.binary_status, release.binary)
                else:
                    self._answer(404, b"not found")

            def _answer(self, status: int, body: bytes) -> None:
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        from row_bot.providers import claude_install

        self._monkeypatch.setattr(claude_install, "CLAUDE_CODE_RELEASES", self.url)
        return self

    def __exit__(self, *_exc: Any) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()

    # ── What happened ──────────────────────────────────────────────────

    def installs(self) -> list[dict[str, Any]]:
        """Each run of the downloaded binary: its arguments, the environment it saw and its folder."""
        if not self.install_log.exists():
            return []
        return [json.loads(line) for line in self.install_log.read_text(encoding="utf-8").splitlines()]

    def downloads(self) -> list[str]:
        """What is left in Claude Code's download folder, besides the Windows launcher's script."""
        folder = self.home / ".claude" / "downloads"
        return sorted(path.name for path in folder.iterdir() if not path.name.endswith("-script.py")) \
            if folder.is_dir() else []

    def installed_program(self) -> Path:
        return self.home / ".local" / "bin" / ("claude.exe" if self.windows else "claude")

    def remove_install(self) -> None:
        shutil.rmtree(self.home / ".local", ignore_errors=True)
