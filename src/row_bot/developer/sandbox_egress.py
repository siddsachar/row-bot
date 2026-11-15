"""A networked Developer sandbox reaches the public internet through Row-Bot's network gate, nothing else.

On Docker's default network a container can reach this computer's own services: on Docker Desktop
``host.docker.internal`` (and its address, which an ``--add-host`` override can't hide) leads to the
host's loopback, an unauthenticated Ollama on 11434 or Row-Bot's own server; on Linux the bridge's
gateway is the host itself; the local network and cloud metadata are a NAT hop away. So a sandbox
with its network on ("ask" or "on") never joins it. Instead, per code folder:

- an internal network (``row-bot-net-<digest>``): no route out, no host-side address on its bridge
  (Docker's ``isolated`` gateway mode, and ``inhibit_ipv4`` for engines before 28), and the sandbox
  gets ``--dns 127.0.0.1`` so names outside it don't resolve;
- the gate (``row-bot-egress-<digest>``), a small container on Docker's default network and the
  internal one, running ``egress_proxy`` with Python's standard library: it opens public internet
  addresses only, after resolving them itself, and pins the connection to the checked address;
- the sandbox points ``HTTP_PROXY`` / ``HTTPS_PROXY`` (and their lowercase forms, which curl and
  git read, plus ``NODE_USE_ENV_PROXY=1`` for Node 24+'s fetch) at the gate; only its own loopback
  goes direct. A program that ignores those has no way out at all, SSH git remotes included. The sandbox also loses ``NET_RAW`` and the gate never forwards packets, so raw
  frames sent to the gate don't get routed onwards.

The gate always runs from the default sandbox image (it has Python 3), never from a code folder's
own image, since the gate is the one container with a real network; it drops every capability, has
a read-only file system and an unprivileged user. Prepare sandbox's install container uses the same
gate. When the gate can't start (that image isn't here, a Docker error), ``ensure_gate`` raises
``GateUnavailable`` and the caller runs without a network: never with Docker's default one.
"""
from __future__ import annotations

import hashlib
import logging
import pathlib
import subprocess
from typing import Callable

from row_bot.developer.state import DEFAULT_SANDBOX_IMAGE

logger = logging.getLogger(__name__)

Docker = Callable[..., subprocess.CompletedProcess]
"""``docker(*args, timeout=...)``: one call of the container runtime's CLI."""

GATE_PORT = 3128
PROXY_VARIABLES = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")
NO_PROXY_VARIABLES = ("NO_PROXY", "no_proxy")
# Only the sandbox's own loopback goes direct; everything else goes to the gate.
NO_PROXY = "localhost,127.0.0.1,::1"
# Node 24+'s built-in fetch and undici read the proxy variables only with this set.
NODE_PROXY = ("NODE_USE_ENV_PROXY", "1")
_PROXY_NAMES = {name.upper() for name in (*PROXY_VARIABLES, *NO_PROXY_VARIABLES, NODE_PROXY[0], "ALL_PROXY")}
# Docker's bridge options for an internal network with no host-side address. Podman has neither.
_DOCKER_NETWORK_OPTIONS = (
    "com.docker.network.bridge.gateway_mode_ipv4=isolated",
    "com.docker.network.bridge.inhibit_ipv4=true",
)


class GateUnavailable(RuntimeError):
    """The gate couldn't start; its message says why, in words for the person."""


def _digest(workspace_id: str) -> str:
    return hashlib.sha1(workspace_id.encode("utf-8")).hexdigest()[:16]


def network_name(workspace_id: str) -> str:
    return f"row-bot-net-{_digest(workspace_id)}"


def gate_name(workspace_id: str) -> str:
    return f"row-bot-egress-{_digest(workspace_id)}"


def is_proxy_variable(name: str) -> bool:
    """A variable the gate sets; a passed-through one of the same name would steer around it."""
    return name.upper() in _PROXY_NAMES


def sandbox_network_args(workspace_id: str) -> list[str]:
    """``docker run`` arguments that put a networked sandbox behind its gate."""
    proxy = f"http://{gate_name(workspace_id)}:{GATE_PORT}"
    args = ["--network", network_name(workspace_id), "--dns", "127.0.0.1"]
    for name in PROXY_VARIABLES:
        args.extend(["-e", f"{name}={proxy}"])
    for name in NO_PROXY_VARIABLES:
        args.extend(["-e", f"{name}={NO_PROXY}"])
    args.extend(["-e", "=".join(NODE_PROXY)])
    return args


def proxy_source() -> str:
    return pathlib.Path(__file__).with_name("egress_proxy.py").read_text(encoding="utf-8")


def gate_run_args(workspace_id: str, image: str) -> list[str]:
    return [
        "run", "-d",
        "--name", gate_name(workspace_id),
        "--label", f"row_bot.developer.workspace_id={workspace_id}",
        "--label", "row_bot.developer.role=network-gate",
        "--sysctl", "net.ipv4.ip_forward=0",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--read-only",
        "--user", "65534:65534",
        "--pids-limit", "128",
        "--entrypoint", "python3",
        image,
        "-I", "-c", proxy_source(), "--port", str(GATE_PORT),
    ]


def _is_podman(binary: str) -> bool:
    return pathlib.Path(binary).stem.lower() == "podman"


def _problem(proc: subprocess.CompletedProcess) -> str:
    return (proc.stderr or proc.stdout or "no details").strip()[:300]


def _ensure_network(docker: Docker, workspace_id: str, *, podman: bool) -> None:
    name = network_name(workspace_id)
    found = docker("network", "inspect", "-f", "{{.Internal}}", name, timeout=15)
    if found.returncode == 0:
        if (found.stdout or "").strip().lower() == "true":
            return
        # Not one Row-Bot made: never put a sandbox on a network with a way out.
        docker("network", "rm", name, timeout=30)
    args = ["network", "create", "--internal", "--label", f"row_bot.developer.workspace_id={workspace_id}"]
    if not podman:
        for option in _DOCKER_NETWORK_OPTIONS:
            args.extend(["-o", option])
    made = docker(*args, name, timeout=30)
    if made.returncode != 0:
        raise GateUnavailable(f"Row-Bot couldn't create the sandbox's private network: {_problem(made)}")


def _call(docker: Docker, *args: str, timeout: float) -> subprocess.CompletedProcess:
    try:
        return docker(*args, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("docker %s failed for the sandbox's network gate", args[0], exc_info=True)
        raise GateUnavailable("Docker didn't answer while Row-Bot started the sandbox's network gate.") from exc


def ensure_gate(docker: Docker, workspace_id: str, *, binary: str = "docker") -> None:
    """The code folder's private network and its running gate; raises GateUnavailable when they can't be made."""
    name = gate_name(workspace_id)
    state = _call(docker, "inspect", "-f", "{{.State.Running}}", name, timeout=10)
    if state.returncode == 0:
        if (state.stdout or "").strip().lower() == "true":
            return
        if _call(docker, "start", name, timeout=30).returncode == 0:
            return
        _call(docker, "rm", "-f", name, timeout=30)
    if _call(docker, "image", "inspect", DEFAULT_SANDBOX_IMAGE, timeout=15).returncode != 0:
        raise GateUnavailable(
            "The sandbox's network gate runs from the default sandbox image, which isn't on this computer: "
            f"download it with `docker pull {DEFAULT_SANDBOX_IMAGE}`."
        )
    try:
        _ensure_network(docker, workspace_id, podman=_is_podman(binary))
    except (OSError, subprocess.SubprocessError) as exc:
        raise GateUnavailable("Docker didn't answer while Row-Bot made the sandbox's private network.") from exc
    started = _call(docker, *gate_run_args(workspace_id, DEFAULT_SANDBOX_IMAGE), timeout=60)
    if started.returncode != 0:
        raise GateUnavailable(f"Row-Bot couldn't start the sandbox's network gate: {_problem(started)}")
    joined = _call(docker, "network", "connect", network_name(workspace_id), name, timeout=30)
    if joined.returncode != 0:
        _call(docker, "rm", "-f", name, timeout=30)
        raise GateUnavailable(f"Row-Bot couldn't connect the sandbox's network gate: {_problem(joined)}")
    logger.info("Developer sandbox network gate started for workspace %s", workspace_id)


def remove_gate(docker: Docker, workspace_id: str) -> None:
    """The gate and the private network go; a network a sandbox still uses stays until that one goes."""
    for args in (("rm", "-f", gate_name(workspace_id)), ("network", "rm", network_name(workspace_id))):
        try:
            proc = docker(*args, timeout=30)
        except (OSError, subprocess.SubprocessError):
            logger.debug("Couldn't run docker %s", args[0], exc_info=True)
            continue
        if proc.returncode != 0:
            logger.debug("docker %s %s: %s", args[0], args[-1], _problem(proc))
