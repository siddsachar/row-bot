"""A networked Developer sandbox reaches the public internet through its gate, and nothing on this computer.

On Docker's default network a container reaches the host's own services (``host.docker.internal`` on
Docker Desktop, the bridge gateway on Linux), the local network and cloud metadata. A sandbox with its
network on ("ask" or "on") instead sits alone with its gate on an internal network, and the gate's
proxy opens public addresses only. These tests drive the runtime against a fake Docker that keeps
containers and networks, and the gate's proxy in-process on loopback; real Docker is in
``tests/e2e/test_developer_sandbox_network_e2e.py``.
"""
from __future__ import annotations

import asyncio
import dataclasses
import subprocess
from types import SimpleNamespace

import pytest

from row_bot.developer import egress_proxy, sandbox_egress
from row_bot.developer.state import DEFAULT_SANDBOX_IMAGE

pytestmark = pytest.mark.subsystem

IMAGE = "local/dev:latest"


class FakeDocker:
    """Docker's CLI as the sandbox uses it: containers, networks and images it keeps, and every call."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.containers: dict[str, dict] = {}
        self.networks: dict[str, dict] = {}
        self.images = {IMAGE, DEFAULT_SANDBOX_IMAGE}
        self.gate_fails = False
        self.gate_hangs = False

    @staticmethod
    def _ok(stdout: str = "") -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    @staticmethod
    def _no(stderr: str = "No such object") -> SimpleNamespace:
        return SimpleNamespace(returncode=1, stdout="", stderr=stderr)

    def __call__(self, argv, **_kwargs):
        args = list(argv[1:])
        self.calls.append(args)
        if self.gate_hangs and any(str(item).startswith("row-bot-egress-") for item in args):
            raise subprocess.TimeoutExpired(argv, 10)
        if args[:2] == ["image", "inspect"]:
            return self._ok() if args[2] in self.images else self._no()
        if args[:2] == ["network", "inspect"]:
            network = self.networks.get(args[-1])
            return self._ok("true\n" if network["internal"] else "false\n") if network else self._no()
        if args[:2] == ["network", "create"]:
            options = [args[i + 1] for i, item in enumerate(args) if item == "-o"]
            self.networks[args[-1]] = {"internal": "--internal" in args, "options": options}
            return self._ok()
        if args[:2] == ["network", "rm"]:
            if any(args[-1] in box["networks"] and box["running"] for box in self.containers.values()):
                return self._no("network has active endpoints")
            return self._ok() if self.networks.pop(args[-1], None) else self._no()
        if args[:2] == ["network", "connect"]:
            self.containers[args[-1]]["networks"].add(args[-2])
            return self._ok()
        if args[0] == "inspect":
            box = self.containers.get(args[-1])
            if box is None:
                return self._no()
            if "{{.State.Running}}" in args:
                return self._ok("true\n" if box["running"] else "false\n")
            if "{{.HostConfig.NetworkMode}}" in args:
                return self._ok(box["mode"] + "\n")
            return self._ok("".join(f"{name} " for name in sorted(box["networks"])) + "\n")
        if args[0] == "run":
            name = args[args.index("--name") + 1]
            if self.gate_fails and name.startswith("row-bot-egress-"):
                return self._no('exec: "python3": executable file not found in $PATH')
            mode = args[args.index("--network") + 1] if "--network" in args else "bridge"
            self.containers[name] = {"running": True, "mode": mode, "networks": {mode}, "args": args}
            return self._ok("container-id\n")
        if args[0] == "rm":
            self.containers.pop(args[-1], None)
            return self._ok()
        if args[0] == "start":
            self.containers[args[-1]]["running"] = True
            return self._ok()
        return self._ok()

    def runs(self, name: str) -> list[list[str]]:
        return [call for call in self.calls if call[:1] == ["run"] and call[call.index("--name") + 1] == name]


@pytest.fixture
def sandbox(tmp_path, monkeypatch, reload_for_data_dir):
    _executables, _storage, sandbox_runtime = reload_for_data_dir(
        tmp_path / "data",
        "row_bot.developer.executables",
        "row_bot.developer.storage",
        "row_bot.developer.sandbox_runtime",
    )
    folder = tmp_path / "project"
    folder.mkdir()
    (folder / "app.py").write_text("print('hi')\n", encoding="utf-8")
    docker = FakeDocker()
    monkeypatch.setattr(sandbox_runtime.subprocess, "run", docker)
    monkeypatch.setattr(sandbox_runtime, "detect_container_runtime",
                        lambda: sandbox_runtime.SandboxProbe(True, binary="docker", version="Docker test"))
    workspace = sandbox_runtime.DeveloperWorkspace(
        id="ws-gate", name="Gate", path=str(folder), execution_mode="docker", sandbox_network="ask", sandbox_image=IMAGE,
    )
    return SimpleNamespace(
        runtime=sandbox_runtime, docker=docker, workspace=workspace,
        box=sandbox_runtime.sandbox_container_name(workspace.id),
        gate=sandbox_egress.gate_name(workspace.id), network=sandbox_egress.network_name(workspace.id),
    )


def _flag(args: list[str], name: str) -> str:
    return args[args.index(name) + 1]


def _env(args: list[str]) -> dict[str, str]:
    pairs = [args[i + 1].split("=", 1) for i, item in enumerate(args) if item == "-e"]
    values: dict[str, list[str]] = {}
    for key, value in pairs:
        values.setdefault(key, []).append(value)
    return {key: found[0] if len(found) == 1 else found for key, found in values.items()}


@pytest.mark.parametrize("network", ["ask", "on"])
def test_a_networked_sandbox_sits_alone_with_its_gate_on_a_network_with_no_way_out(sandbox, network):
    env = sandbox
    workspace = dataclasses.replace(env.workspace, sandbox_network=network)
    env.runtime.ensure_docker_sandbox(workspace)

    # The private network: internal, with no address for the host on its bridge.
    made = env.docker.networks[env.network]
    assert made["internal"]
    assert "com.docker.network.bridge.gateway_mode_ipv4=isolated" in made["options"]
    assert "com.docker.network.bridge.inhibit_ipv4=true" in made["options"]

    # The gate: on Docker's network and the private one, hardened, running the proxy whole.
    (gate,) = env.docker.runs(env.gate)
    assert env.docker.containers[env.gate]["networks"] == {"bridge", env.network}
    assert _flag(gate, "--cap-drop") == "ALL" and "--read-only" in gate
    assert _flag(gate, "--sysctl") == "net.ipv4.ip_forward=0"
    assert _flag(gate, "--security-opt") == "no-new-privileges"
    assert _flag(gate, "--user") == "65534:65534"
    assert _flag(gate, "--entrypoint") == "python3"
    code = _flag(gate, "-c")
    compile(code, "<gate>", "exec")
    assert len(code) < 30_000  # Windows' command line holds about 32,000 characters.

    # The sandbox: only on the private network, its DNS cut off, its way out the gate.
    (box,) = env.docker.runs(env.box)
    assert env.docker.containers[env.box]["networks"] == {env.network}
    assert _flag(box, "--dns") == "127.0.0.1"
    assert _flag(box, "--cap-drop") == "NET_RAW"
    proxy = f"http://{env.gate}:3128"
    values = _env(box)
    assert [values[name] for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")] == [proxy] * 4
    # Only the sandbox's own loopback goes direct; Node 24+'s fetch uses the gate too.
    assert values["NO_PROXY"] == values["no_proxy"] == "localhost,127.0.0.1,::1"
    assert values["NODE_USE_ENV_PROXY"] == "1"
    # The gate is up before the sandbox starts.
    assert env.docker.calls.index(gate) < env.docker.calls.index(box)


def test_a_networked_sandbox_never_takes_the_computers_proxy_settings(sandbox, monkeypatch):
    env = sandbox
    monkeypatch.setenv("HTTPS_PROXY", "http://10.0.0.5:3128")
    monkeypatch.setenv("no_proxy", "*")
    monkeypatch.setenv("NODE_USE_ENV_PROXY", "0")
    monkeypatch.setenv("PROJECT_TOKEN_NAME", "kept")
    workspace = dataclasses.replace(env.workspace, sandbox_env_allowlist=[
        "HTTPS_PROXY", "no_proxy", "NODE_USE_ENV_PROXY", "PROJECT_TOKEN_NAME"])
    env.runtime.ensure_docker_sandbox(workspace)
    values = _env(env.docker.runs(env.box)[0])
    assert values["HTTPS_PROXY"] == f"http://{env.gate}:3128"
    assert values["no_proxy"] == "localhost,127.0.0.1,::1"
    assert values["NODE_USE_ENV_PROXY"] == "1"
    assert values["PROJECT_TOKEN_NAME"] == "kept"


def test_a_sandbox_with_its_network_off_has_none_and_no_gate(sandbox):
    env = sandbox
    env.runtime.ensure_docker_sandbox(dataclasses.replace(env.workspace, sandbox_network="off"))
    (box,) = env.docker.runs(env.box)
    assert _flag(box, "--network") == "none"
    assert "HTTPS_PROXY" not in _env(box)
    assert env.gate not in env.docker.containers and env.network not in env.docker.networks


@pytest.mark.parametrize("old_networks", [{"bridge"}, {"default"}, {"bridge", "private"}])
def test_a_networked_sandbox_with_another_way_out_is_recreated_behind_its_gate(sandbox, old_networks):
    env = sandbox
    networks = {env.network if name == "private" else name for name in old_networks}
    # Made before the gate, on Docker's default network; or joined to it since.
    env.docker.containers[env.box] = {"running": True, "mode": "bridge", "networks": networks, "args": []}
    env.runtime.ensure_docker_sandbox(env.workspace)
    assert ["rm", "-f", env.box] in env.docker.calls
    assert env.docker.containers[env.box]["networks"] == {env.network}


def test_a_running_gate_and_sandbox_are_reused(sandbox):
    env = sandbox
    env.runtime.ensure_docker_sandbox(env.workspace)
    env.runtime.ensure_docker_sandbox(env.workspace)
    assert len(env.docker.runs(env.gate)) == 1 and len(env.docker.runs(env.box)) == 1
    assert len([call for call in env.docker.calls if call[:2] == ["network", "create"]]) == 1


def test_a_stopped_gate_is_started_again_before_the_sandbox_runs(sandbox):
    env = sandbox
    env.runtime.ensure_docker_sandbox(env.workspace)
    env.docker.containers[env.gate]["running"] = False  # Docker restarted.
    env.docker.containers[env.box]["running"] = False
    env.runtime.ensure_docker_sandbox(env.workspace)
    starts = [call for call in env.docker.calls if call[:1] == ["start"]]
    assert starts == [["start", env.gate], ["start", env.box]]


def test_a_network_of_that_name_with_a_way_out_is_replaced(sandbox):
    env = sandbox
    env.docker.networks[env.network] = {"internal": False, "options": []}
    env.runtime.ensure_docker_sandbox(env.workspace)
    assert ["network", "rm", env.network] in env.docker.calls
    assert env.docker.networks[env.network]["internal"]


def test_turning_the_network_off_takes_the_gate_and_its_network_away(sandbox):
    env = sandbox
    env.runtime.ensure_docker_sandbox(env.workspace)
    env.runtime.ensure_docker_sandbox(dataclasses.replace(env.workspace, sandbox_network="off"))
    assert env.docker.containers[env.box]["mode"] == "none"
    assert env.gate not in env.docker.containers and env.network not in env.docker.networks


def test_removing_the_sandbox_removes_its_gate_and_network(sandbox):
    env = sandbox
    env.runtime.ensure_docker_sandbox(env.workspace)
    env.runtime.cleanup_workspace_sandbox(env.workspace.id)
    assert env.docker.containers == {} and env.docker.networks == {}


def _offline(env) -> None:
    """The sandbox runs, with no network at all: never Docker's default one, never the gate's settings."""
    box = env.docker.runs(env.box)[-1]
    assert _flag(box, "--network") == "none"
    assert env.docker.containers[env.box]["networks"] == {"none"}
    assert not {"HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"} & set(_env(box))


def test_a_gate_that_cant_start_runs_the_sandbox_with_its_network_off_and_says_why(sandbox):
    env = sandbox
    env.docker.gate_fails = True
    outcome = env.runtime.run_docker_sandbox_command(env.workspace, "python app.py", thread_id="thread-1")
    # The command still runs, offline, and the agent is told why.
    assert outcome.returncode == 0
    assert outcome.stderr.startswith("The sandbox's network is off for now. Row-Bot couldn't start the sandbox's "
                                     "network gate:")
    assert "python3" in outcome.stderr and "tries again with the next command" in outcome.stderr
    assert [call for call in env.docker.calls if call[:1] == ["exec"]]
    _offline(env)
    assert env.runtime.network_problem(env.workspace.id) in outcome.stderr

    # Once the gate starts, the sandbox is made again behind it and the note goes.
    env.docker.gate_fails = False
    outcome = env.runtime.run_docker_sandbox_command(env.workspace, "python app.py", thread_id="thread-1")
    assert "network is off" not in outcome.stderr
    assert env.runtime.network_problem(env.workspace.id) == ""
    assert env.docker.containers[env.box]["networks"] == {env.network}
    assert _env(env.docker.runs(env.box)[-1])["HTTPS_PROXY"] == f"http://{env.gate}:3128"


def test_a_started_process_is_told_when_the_sandbox_network_is_off(sandbox):
    env = sandbox
    env.docker.gate_fails = True
    outcome = env.runtime.start_docker_sandbox_process(env.workspace, "python -m http.server")
    assert outcome.stderr == env.runtime.network_problem(env.workspace.id) != ""
    _offline(env)


def test_docker_not_answering_for_the_gate_leaves_the_sandbox_offline(sandbox):
    env = sandbox
    env.docker.gate_hangs = True
    env.runtime.ensure_docker_sandbox(env.workspace)
    _offline(env)
    assert "Docker didn't answer" in env.runtime.network_problem(env.workspace.id)


def test_without_the_default_image_the_gate_never_runs_from_the_folders_own(sandbox):
    env = sandbox
    env.docker.images.discard(DEFAULT_SANDBOX_IMAGE)
    env.runtime.ensure_docker_sandbox(env.workspace)
    # The folder's own image may hold anything; the gate is the one container with a real network.
    assert env.docker.runs(env.gate) == []
    _offline(env)
    assert f"docker pull {DEFAULT_SANDBOX_IMAGE}" in env.runtime.network_problem(env.workspace.id)


def test_a_networked_sandbox_already_running_goes_offline_when_its_gate_cant_start(sandbox):
    env = sandbox
    env.runtime.ensure_docker_sandbox(env.workspace)
    env.docker.containers.pop(env.gate)  # The gate was removed by hand, and can't be made again.
    env.docker.gate_fails = True
    env.runtime.ensure_docker_sandbox(env.workspace)
    _offline(env)
    assert env.runtime.network_problem(env.workspace.id)


def test_turning_the_network_off_clears_the_note(sandbox):
    env = sandbox
    env.docker.gate_fails = True
    env.runtime.ensure_docker_sandbox(env.workspace)
    env.runtime.ensure_docker_sandbox(dataclasses.replace(env.workspace, sandbox_network="off"))
    assert env.runtime.network_problem(env.workspace.id) == ""


def test_the_gate_always_runs_from_the_default_image(sandbox):
    env = sandbox
    env.runtime.ensure_docker_sandbox(env.workspace)
    (gate,) = env.docker.runs(env.gate)
    assert DEFAULT_SANDBOX_IMAGE in gate and IMAGE not in gate


# ── The gate's proxy ────────────────────────────────────────────────────────


@pytest.mark.parametrize(("host", "addresses"), [
    ("host.docker.internal", ["192.168.65.254"]),
    ("host.docker.internal.", ["93.184.215.14"]),  # A trailing dot, or a public answer, changes nothing.
    ("gateway.docker.internal", ["192.168.65.1"]),
    ("HOST.DOCKER.INTERNAL", ["93.184.215.14"]),
    ("localhost", ["127.0.0.1"]),
    ("ollama.localhost", ["127.0.0.1"]),
    ("metadata.google.internal", ["169.254.169.254"]),
    ("printer.local", ["192.168.1.20"]),
    ("127.0.0.1", ["127.0.0.1"]),
    ("192.168.65.254", ["192.168.65.254"]),
    ("172.17.0.1", ["172.17.0.1"]),
    ("10.0.0.1", ["10.0.0.1"]),
    ("192.168.1.1", ["192.168.1.1"]),
    ("100.64.0.1", ["100.64.0.1"]),
    ("169.254.169.254", ["169.254.169.254"]),
    ("0.0.0.0", ["0.0.0.0"]),
    ("224.0.0.251", ["224.0.0.251"]),
    ("::1", ["::1"]),
    ("fe80::1", ["fe80::1%eth0"]),
    ("fd00::1", ["fd00::1"]),
    ("::ffff:127.0.0.1", ["::ffff:127.0.0.1"]),
    ("::ffff:169.254.169.254", ["::ffff:169.254.169.254"]),
    ("64:ff9b::a9fe:a9fe", ["64:ff9b::a9fe:a9fe"]),  # NAT64 of the metadata address.
    ("2002:7f00:1::", ["2002:7f00:1::"]),  # 6to4 of loopback.
    ("::127.0.0.1", ["::127.0.0.1"]),  # IPv4-compatible IPv6 of loopback.
    ("0:0:0:0:0:ffff:a9fe:a9fe", ["::ffff:169.254.169.254"]),  # Metadata, mapped, written out.
    ("fd00:ec2::254", ["fd00:ec2::254"]),  # AWS's IPv6 metadata address.
    ("100.100.100.200", ["100.100.100.200"]),  # Alibaba Cloud's metadata address.
    ("2130706433", ["127.0.0.1"]),  # Loopback as one decimal number.
    ("metadata.google.internal.", ["93.184.215.14"]),
    ("rebound.example", ["169.254.169.254"]),  # A public name answering with a metadata address.
    ("split.example", ["93.184.215.14", "10.0.0.7"]),
    ("missing.example", []),
])
def test_the_gate_refuses_this_computer_the_local_network_and_metadata(host, addresses):
    assert egress_proxy.refusal(host, 80, addresses)


@pytest.mark.parametrize(("host", "addresses"), [
    ("example.com", ["93.184.215.14"]),
    ("pypi.org", ["151.101.0.223", "2a04:4e42::223"]),
    ("1.1.1.1", ["1.1.1.1"]),
    ("::ffff:1.1.1.1", ["::ffff:1.1.1.1"]),
])
def test_the_gate_opens_public_internet_addresses(host, addresses):
    assert egress_proxy.refusal(host, 443, addresses) == ""


def _scenario(test, answers=None):
    """Run ``test(proxy_port, upstream)`` against the gate on loopback and a fake upstream server.

    The fake resolver maps ``allowed.test`` to the upstream on 127.0.0.1, which only these tests let the
    gate open, as if it were a public address; every other address is judged as in the container.
    ``answers`` maps a name to the resolver's successive answers, one per lookup.
    """

    async def main():
        upstream = SimpleNamespace(heads=[], connections=0)

        async def serve(reader, writer):
            upstream.connections += 1
            head = await reader.readuntil(b"\r\n\r\n")
            upstream.heads.append(head.decode("latin-1"))
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
            await writer.drain()
            writer.close()

        target = await asyncio.start_server(serve, "127.0.0.1", 0)
        upstream.port = target.sockets[0].getsockname()[1]
        names = {"allowed.test": ["127.0.0.1"], "rebound.test": ["169.254.169.254"],
                 "host.docker.internal": ["127.0.0.1"]}
        queued = {name: list(found) for name, found in (answers or {}).items()}
        upstream.lookups = []

        async def resolve(host, _port):
            upstream.lookups.append(host)
            if host in queued:
                return queued[host].pop(0)
            return names.get(host, [])

        def allow(address):
            return address == "127.0.0.1" or egress_proxy.public_address(address)

        gate = await egress_proxy.start("127.0.0.1", 0, resolve=resolve, allow=allow)
        try:
            await asyncio.wait_for(test(gate.sockets[0].getsockname()[1], upstream), 10)
        finally:
            gate.close()
            target.close()

    asyncio.run(main())


async def _ask(port: int, request: bytes) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(request)
    await writer.drain()
    data = await reader.read()
    writer.close()
    return data


def test_the_gate_tunnels_connect_to_an_allowed_host():
    async def test(port, upstream):
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(f"CONNECT allowed.test:{upstream.port} HTTP/1.1\r\nHost: allowed.test\r\n\r\n".encode())
        assert await reader.readuntil(b"\r\n\r\n") == b"HTTP/1.1 200 Connection established\r\n\r\n"
        writer.write(b"GET / HTTP/1.1\r\nHost: allowed.test\r\n\r\n")
        assert (await reader.read()).endswith(b"\r\n\r\nok")
        writer.close()
        assert upstream.heads == ["GET / HTTP/1.1\r\nHost: allowed.test\r\n\r\n"]

    _scenario(test)


@pytest.mark.parametrize("host", ["host.docker.internal", "rebound.test", "nowhere.test"])
def test_the_gate_refuses_connect_without_opening_anything(host):
    async def test(port, upstream):
        reply = await _ask(port, f"CONNECT {host}:{upstream.port} HTTP/1.1\r\n\r\n".encode())
        assert reply.startswith(b"HTTP/1.1 403 Forbidden")
        assert b"public internet sites only" in reply
        assert upstream.connections == 0

    _scenario(test)


def test_the_gate_forwards_plain_http_to_the_checked_host_only():
    async def test(port, upstream):
        reply = await _ask(port, (
            f"GET http://allowed.test:{upstream.port}/simple/?q=1 HTTP/1.1\r\nHost: elsewhere.test\r\n"
            "Proxy-Authorization: Basic c2VjcmV0\r\nProxy-Connection: keep-alive\r\nAccept: */*\r\n\r\n"
        ).encode())
        assert reply.startswith(b"HTTP/1.1 200 OK") and reply.endswith(b"ok")
        (head,) = upstream.heads
        assert head == (f"GET /simple/?q=1 HTTP/1.1\r\nHost: allowed.test:{upstream.port}\r\nAccept: */*\r\n"
                        "Connection: close\r\n\r\n")

    _scenario(test)


@pytest.mark.parametrize("request_line", [
    "GET http://allowed.test@host.docker.internal/ HTTP/1.1",  # Credentials hiding the real host.
    "GET https://allowed.test/ HTTP/1.1",
    "GET /relative HTTP/1.1",
    "CONNECT allowed.test HTTP/1.1",
    "CONNECT allowed.test:notaport HTTP/1.1",
    "BREW",
])
def test_the_gate_refuses_requests_it_cant_read_plainly(request_line):
    async def test(port, upstream):
        reply = await _ask(port, f"{request_line}\r\n\r\n".encode())
        assert reply.startswith(b"HTTP/1.1 400 Bad Request")
        assert upstream.connections == 0

    _scenario(test)


@pytest.mark.parametrize("address", [
    "127.0.0.2", "10.1.2.3", "172.17.0.1", "192.168.65.254", "169.254.169.254", "100.100.100.200",
    "::ffff:169.254.169.254", "64:ff9b::a9fe:a9fe", "fd00:ec2::254", "fe80::1", "::1",
])
def test_the_gate_refuses_connect_to_a_name_that_resolves_to_a_refused_address(address):
    async def test(port, upstream):
        reply = await _ask(port, f"CONNECT innocent.test:{upstream.port} HTTP/1.1\r\n\r\n".encode())
        assert reply.startswith(b"HTTP/1.1 403 Forbidden")
        assert b"this computer, the local network or a cloud metadata address" in reply
        assert upstream.connections == 0

    _scenario(test, {"innocent.test": [[address]]})


def test_a_name_that_rebinds_to_a_private_address_is_refused_on_the_next_connection():
    """DNS rebinding: public at the first lookup, this computer at the next. Each connection is checked."""

    async def test(port, upstream):
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(f"CONNECT flip.test:{upstream.port} HTTP/1.1\r\n\r\n".encode())
        assert await reader.readuntil(b"\r\n\r\n") == b"HTTP/1.1 200 Connection established\r\n\r\n"
        writer.write(b"GET / HTTP/1.1\r\nHost: flip.test\r\n\r\n")
        assert (await reader.read()).endswith(b"ok")
        writer.close()
        reply = await _ask(port, f"GET http://flip.test:{upstream.port}/ HTTP/1.1\r\n\r\n".encode())
        assert reply.startswith(b"HTTP/1.1 403 Forbidden")
        assert upstream.connections == 1
        assert upstream.lookups == ["flip.test", "flip.test"]

    _scenario(test, {"flip.test": [["127.0.0.1"], ["169.254.169.254"]]})


def test_the_gate_connects_to_the_address_it_checked_never_the_name(monkeypatch):
    """One lookup per connection, and the connection goes to that answer: a second lookup can't re-point it."""
    dialled: list[tuple[str, int]] = []
    open_connection = asyncio.open_connection

    async def spy(host, port, *args, **kwargs):
        dialled.append((host, port))
        return await open_connection(host, port, *args, **kwargs)

    monkeypatch.setattr(egress_proxy.asyncio, "open_connection", spy)

    async def test(port, upstream):
        reply = await _ask(port, f"GET http://pinned.test:{upstream.port}/ HTTP/1.1\r\n\r\n".encode())
        assert reply.startswith(b"HTTP/1.1 200 OK")
        # Asked of the resolver once; the gate dialled the checked address, not the name.
        assert upstream.lookups == ["pinned.test"]
        assert ("127.0.0.1", upstream.port) in dialled
        assert not [host for host, _port in dialled if host == "pinned.test"]

    _scenario(test, {"pinned.test": [["127.0.0.1"], ["169.254.169.254"]]})
