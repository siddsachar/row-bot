"""Opt-in check with real Docker: a networked Developer sandbox reaches the internet, never this computer.

A service listens on this computer (like an unauthenticated Ollama). A sandbox with its network on
must reach public sites through its gate, with the tools an agent uses (curl, pip, git over https,
npm, and Node's fetch on Node 24+), and must not reach that service: neither through the gate nor
directly, by ``host.docker.internal``, Docker Desktop's host address, Docker's bridge gateway or its
own network's gateway address.

It needs Docker already running (it never starts Docker Desktop) and the sandbox image on this
computer (it never pulls one); otherwise it is skipped. It reaches example.com, PyPI, GitHub and the
npm registry::

    uv run python -m pytest tests/e2e/test_developer_sandbox_network_e2e.py -m e2e

``ROW_BOT_SANDBOX_E2E_IMAGE`` picks another local image with Python 3. ``ROW_BOT_SANDBOX_E2E_BIND``
sets the service's address (default ``127.0.0.1``); on Linux Docker Engine use ``0.0.0.0``, which a
container on Docker's default network can reach through the bridge gateway.
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
import json, shutil, socket, struct, subprocess, sys, urllib.error, urllib.request
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

def tool(command):
    if not shutil.which(command[0]):
        return "missing"
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=180)
    except Exception as error:
        return type(error).__name__
    return "ok" if done.returncode == 0 else "failed: " + (done.stderr or done.stdout)[-300:]

def own_gateway():
    """The first address of the sandbox's own network: the host's, were it on the bridge."""
    with open("/proc/net/route") as table:
        for line in table.readlines()[1:]:
            fields = line.split()
            if fields[2] == "00000000" and fields[1] != "00000000":
                # The kernel prints addresses little-endian.
                network = int.from_bytes(bytes.fromhex(fields[1])[::-1], "big")
                mask = int.from_bytes(bytes.fromhex(fields[7])[::-1], "big")
                return socket.inet_ntoa(struct.pack(">I", (network & mask) + 1))
    return ""

def node_major():
    try:
        return int(subprocess.run(["node", "--version"], capture_output=True, text=True).stdout.strip()[1:].split(".")[0])
    except Exception:
        return 0

hosts = ["host.docker.internal", "192.168.65.254", own_gateway(), *sys.argv[2:]]
fetch = "fetch('https://example.com/').then(r => process.exit(r.ok ? 0 : 1), () => process.exit(2))"
print(json.dumps({
    "public": through_gate("https://example.com/"),
    "metadata": through_gate("http://169.254.169.254/latest/meta-data/"),
    "through_gate": {host: through_gate(f"http://{host}:{port}/") for host in hosts if host},
    "direct": {host: direct(host) for host in hosts if host},
    "tools": {
        "curl": tool(["curl", "-fsS", "-o", "/dev/null", "https://example.com/"]),
        "pip": tool(["python3", "-m", "pip", "download", "--no-deps", "--no-cache-dir", "-q", "-d", "/tmp/rb-pip", "six"]),
        "git": tool(["git", "ls-remote", "https://github.com/octocat/Hello-World.git", "HEAD"]),
        "npm": tool(["npm", "view", "--cache", "/tmp/rb-npm", "left-pad", "version"]),
        "node_fetch": tool(["node", "-e", fetch]) if node_major() >= 24 else "node before 24",
    },
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
    """A service on this computer that counts every request it gets."""
    seen: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server's name
            seen.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"this computer")

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer((os.environ.get("ROW_BOT_SANDBOX_E2E_BIND") or "127.0.0.1", 0), Handler)
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
    # The host's address on Docker's default network (172.17.0.1 unless the engine chose another).
    bridge = subprocess.run([binary, "network", "inspect", "bridge", "-f", "{{range .IPAM.Config}}{{.Gateway}} {{end}}"],
                            capture_output=True, text=True, check=False).stdout.split() or ["172.17.0.1"]
    folder = tmp_path / "project"
    folder.mkdir()
    (folder / "probe.py").write_text(PROBE, encoding="utf-8", newline="\n")
    workspace = runtime.DeveloperWorkspace(
        id=f"e2e-gate-{uuid.uuid4().hex[:8]}", name="Gate e2e", path=str(folder), execution_mode="docker",
        sandbox_network="on", sandbox_image=image,
    )
    try:
        outcome = runtime.run_docker_sandbox_command(workspace, f"python3 probe.py {port} {' '.join(bridge)}", thread_id="e2e",
                                                     timeout=600)
        assert outcome.returncode == 0, outcome.stderr
        found = json.loads(outcome.stdout.strip().splitlines()[-1])
        # The next command reuses the same sandbox behind the same gate.
        box = runtime.sandbox_container_name(workspace.id)
        first = subprocess.run([binary, "inspect", "-f", "{{.Id}}", box], capture_output=True, text=True).stdout
        assert runtime.run_docker_sandbox_command(workspace, "true", thread_id="e2e").returncode == 0
        assert subprocess.run([binary, "inspect", "-f", "{{.Id}}", box], capture_output=True, text=True).stdout == first
    finally:
        runtime.cleanup_workspace_sandbox(workspace.id)

    print(json.dumps(found, indent=2))
    assert found["public"] == 200, found
    # The agent's own tools work through the gate (each one the image has).
    for name, result in found["tools"].items():
        assert result in {"ok", "missing", "node before 24"}, (name, result)
    assert found["tools"]["curl"] == "ok", found
    if image == DEFAULT_SANDBOX_IMAGE:
        assert found["tools"]["pip"] == found["tools"]["git"] == found["tools"]["npm"] == "ok", found
    # The gate refuses this computer, by every name and address, and cloud metadata.
    assert found["metadata"] == 403, found
    assert set(found["through_gate"].values()) == {403}, found
    # And there is no way round the gate.
    assert "open" not in found["direct"].values(), found
    assert seen == []
    # Nothing of the sandbox is left behind.
    from row_bot.developer import sandbox_egress

    left = subprocess.run([binary, "ps", "-aq", "--filter", f"label=row_bot.developer.workspace_id={workspace.id}"],
                          capture_output=True, text=True, check=False).stdout.strip()
    assert left == ""
    assert subprocess.run([binary, "network", "inspect", sandbox_egress.network_name(workspace.id)],
                          capture_output=True, check=False).returncode != 0


def test_a_networked_sandbox_whose_gate_cant_start_runs_with_no_network(tmp_path, reload_for_data_dir, monkeypatch):
    """The gate's image is missing: the sandbox runs with Docker's network ``none``, never its default one."""
    binary = _docker()
    _executables, _storage, runtime = reload_for_data_dir(
        tmp_path / "data", "row_bot.developer.executables", "row_bot.developer.storage",
        "row_bot.developer.sandbox_runtime",
    )
    from row_bot.developer import sandbox_egress
    from row_bot.developer.state import DEFAULT_SANDBOX_IMAGE

    image = os.environ.get("ROW_BOT_SANDBOX_E2E_IMAGE") or DEFAULT_SANDBOX_IMAGE
    if subprocess.run([binary, "image", "inspect", image], capture_output=True, check=False).returncode != 0:
        pytest.skip(f"The sandbox image {image} isn't on this computer (docker pull {image})")
    # A gate image that isn't here (Row-Bot never pulls one), so the gate can't start.
    monkeypatch.setattr(sandbox_egress, "DEFAULT_SANDBOX_IMAGE", f"row-bot-e2e-missing-{uuid.uuid4().hex[:8]}:none")
    folder = tmp_path / "project"
    folder.mkdir()
    workspace = runtime.DeveloperWorkspace(
        id=f"e2e-offline-{uuid.uuid4().hex[:8]}", name="Gate offline e2e", path=str(folder), execution_mode="docker",
        sandbox_network="on", sandbox_image=image,
    )
    probe = ("python3 -c \"import os, socket; print(sorted(k for k in os.environ if 'proxy' in k.lower())); "
             "socket.create_connection(('1.1.1.1', 443), timeout=5)\"")
    try:
        outcome = runtime.run_docker_sandbox_command(workspace, probe, thread_id="e2e", timeout=120)
        box = runtime.sandbox_container_name(workspace.id)
        mode = subprocess.run([binary, "inspect", "-f", "{{.HostConfig.NetworkMode}}", box],
                              capture_output=True, text=True, check=False).stdout.strip()
        gate = subprocess.run([binary, "inspect", sandbox_egress.gate_name(workspace.id)],
                              capture_output=True, check=False).returncode
    finally:
        runtime.cleanup_workspace_sandbox(workspace.id)
    print(outcome.stdout, outcome.stderr)
    assert mode == "none"
    assert gate != 0  # No gate container was left running from another image.
    assert outcome.returncode not in (0, None), outcome.stdout  # It ran, and couldn't connect.
    assert outcome.stdout.strip().splitlines()[0] == "[]"  # No proxy settings either.
    assert outcome.stderr.startswith("The sandbox's network is off for now.")
    assert "docker pull" in outcome.stderr
