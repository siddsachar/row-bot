"""Opt-in check with real Docker: a networked Developer sandbox reaches the internet, never this computer.

A service listens on this computer's loopback (like an unauthenticated Ollama). A sandbox with its
network on must reach a public site through its gate, and must not reach that service, neither
through the gate nor directly by ``host.docker.internal`` or Docker Desktop's host address.

It needs Docker already running (it never starts Docker Desktop) and the sandbox image on this
computer (it never pulls one); otherwise it is skipped. It reaches example.com::

    uv run python -m pytest tests/e2e/test_developer_sandbox_network_e2e.py -m e2e

``ROW_BOT_SANDBOX_E2E_IMAGE`` picks another local image with Python 3.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytestmark = [pytest.mark.e2e]

PROBE = r'''
import json, socket, sys, urllib.error, urllib.request
port = int(sys.argv[1])

def through_gate(url):
    try:
        with urllib.request.urlopen(url, timeout=20) as reply:
            return reply.status
    except urllib.error.HTTPError as error:
        return error.code
    except Exception as error:
        return type(error).__name__

def direct(host):
    try:
        socket.create_connection((host, port), timeout=5).close()
        return "open"
    except Exception as error:
        return type(error).__name__

print(json.dumps({
    "public": through_gate("https://example.com/"),
    "host_by_name": through_gate(f"http://host.docker.internal:{port}/"),
    "host_by_address": through_gate(f"http://192.168.65.254:{port}/"),
    "metadata": through_gate("http://169.254.169.254/latest/meta-data/"),
    "direct_by_name": direct("host.docker.internal"),
    "direct_by_address": direct("192.168.65.254"),
}))
'''


def _docker() -> str:
    binary = shutil.which("docker")
    if not binary:
        pytest.skip("Docker isn't installed")
    try:
        ready = subprocess.run([binary, "info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True,
                               timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        pytest.skip("Docker isn't answering")
    if ready.returncode != 0:
        pytest.skip("Docker isn't running; start it yourself, this test never does")
    return binary


@pytest.fixture
def host_service():
    """A loopback-only service on this computer that counts every request it gets."""
    seen: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server's name
            seen.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"this computer")

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1], seen
    server.shutdown()
    server.server_close()


def test_a_networked_sandbox_reaches_the_internet_but_not_this_computer(tmp_path, reload_for_data_dir, host_service):
    binary = _docker()
    _executables, _storage, runtime = reload_for_data_dir(
        tmp_path / "data", "row_bot.developer.executables", "row_bot.developer.storage",
        "row_bot.developer.sandbox_runtime",
    )
    from row_bot.developer.state import DEFAULT_SANDBOX_IMAGE

    image = os.environ.get("ROW_BOT_SANDBOX_E2E_IMAGE") or DEFAULT_SANDBOX_IMAGE
    if subprocess.run([binary, "image", "inspect", image], capture_output=True, check=False).returncode != 0:
        pytest.skip(f"The sandbox image {image} isn't on this computer (docker pull {image})")
    port, seen = host_service
    folder = tmp_path / "project"
    folder.mkdir()
    (folder / "probe.py").write_text(PROBE, encoding="utf-8", newline="\n")
    workspace = runtime.DeveloperWorkspace(
        id=f"e2e-gate-{uuid.uuid4().hex[:8]}", name="Gate e2e", path=str(folder), execution_mode="docker",
        sandbox_network="on", sandbox_image=image,
    )
    try:
        outcome = runtime.run_docker_sandbox_command(workspace, f"python3 probe.py {port}", thread_id="e2e",
                                                     timeout=240)
        assert outcome.returncode == 0, outcome.stderr
        found = json.loads(outcome.stdout.strip().splitlines()[-1])
        # The next command reuses the same sandbox behind the same gate.
        box = runtime.sandbox_container_name(workspace.id)
        first = subprocess.run([binary, "inspect", "-f", "{{.Id}}", box], capture_output=True, text=True).stdout
        assert runtime.run_docker_sandbox_command(workspace, "true", thread_id="e2e").returncode == 0
        assert subprocess.run([binary, "inspect", "-f", "{{.Id}}", box], capture_output=True, text=True).stdout == first
    finally:
        runtime.cleanup_workspace_sandbox(workspace.id)

    assert found["public"] == 200, found
    # The gate refuses this computer, by name and by address, and cloud metadata.
    assert found["host_by_name"] == 403 and found["host_by_address"] == 403 and found["metadata"] == 403, found
    # And there is no way round the gate.
    assert found["direct_by_name"] != "open" and found["direct_by_address"] != "open", found
    assert seen == []
    # Nothing of the sandbox is left behind.
    from row_bot.developer import sandbox_egress

    left = subprocess.run([binary, "ps", "-aq", "--filter", f"label=row_bot.developer.workspace_id={workspace.id}"],
                          capture_output=True, text=True, check=False).stdout.strip()
    assert left == ""
    assert subprocess.run([binary, "network", "inspect", sandbox_egress.network_name(workspace.id)],
                          capture_output=True, check=False).returncode != 0
