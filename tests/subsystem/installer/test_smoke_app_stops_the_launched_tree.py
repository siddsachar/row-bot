"""Ending a launch smoke stops everything the launched command started (B203).

The launcher runs the server as a child process. On Windows the smoke ended the
launcher with TerminateProcess, which no handler can catch, so the launcher never
stopped its server: it kept running after the smoke. (On macOS and Linux the
smoke sends SIGTERM and the launcher stops its server itself.)
"""
from __future__ import annotations

import socket
import sys
import textwrap
import time

import psutil
import pytest

import scripts.smoke_app as smoke_app


pytestmark = [
    pytest.mark.subsystem,
    pytest.mark.installer,
    pytest.mark.platform,
    pytest.mark.slow,
    pytest.mark.skipif(sys.platform != "win32", reason="POSIX launchers stop their server on SIGTERM"),
]

FAKE_LAUNCHER = textwrap.dedent("""
    import http.server
    import json
    import subprocess
    import sys

    server_child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    with open(sys.argv[2], "w", encoding="utf-8") as handle:
        handle.write(str(server_child.pid))

    class Health(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"ok": True, "status": "alive"}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    http.server.HTTPServer(("127.0.0.1", int(sys.argv[1])), Health).serve_forever()
""")


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _running(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def test_the_launchers_server_child_stops_with_the_smoke(tmp_path) -> None:
    launcher = tmp_path / "fake_launcher.py"
    launcher.write_text(FAKE_LAUNCHER, encoding="utf-8")
    child_pid_file = tmp_path / "server-child.pid"
    port = _free_port()
    child_pid = None
    try:
        result = smoke_app.run_app_smoke(
            command=[sys.executable, str(launcher), str(port), str(child_pid_file)],
            cwd=tmp_path, port=port, timeout=30, check_root=False, public_probes=True,
            data_dir=tmp_path / "data",
        )
        assert result.ok, result.messages
        child_pid = int(child_pid_file.read_text(encoding="utf-8"))
        deadline = time.monotonic() + 5
        while _running(child_pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _running(child_pid), "the launched command's child outlived the smoke"
    finally:
        if child_pid is not None and _running(child_pid):
            psutil.Process(child_pid).kill()
