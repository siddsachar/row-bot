"""Smoke an existing image the way a hosting service runs it (H9).

The host's shape (PikaPods'): one container from the unmodified image, as the
image's own user, with one volume at /data, one HTTP port and the host's values
in the environment: a generated ROW_BOT_SETUP_CODE and ROW_BOT_SECRET_STORE_KEY
(64 hex), ROW_BOT_HOSTED_BY and the pod's own address as ROW_BOT_PUBLIC_URL,
with an exact ROW_BOT_ALLOWED_HOSTS that leaves out 127.0.0.1 (a VPS's shape).
Through the HTTP API a browser at the pod's address uses, it checks that:

- the container becomes healthy, its launcher reports Row-Bot running, and its
  log shows the setup code and its sign-in link at the pod's address, all on
  stdout: only warnings go to stderr, which hosts show as errors;
- the code the logged link carries signs this browser in (A1);
- the host list is exact: from outside, a request naming 127.0.0.1 is refused;
- an API key saved for a custom endpoint, and a chat turn through it, work;
- after ``docker restart`` the container is healthy again, the session cookie
  still works, the key is still saved and a second chat turn uses it;
- the key and the code never reach the container log after the first start's
  code and link lines, and the key never reaches /data.

``--shape railway`` runs the same checks the way Railway runs a template's image
(R1): as root (Railway's ``RAILWAY_RUN_UID=0``), on a volume whose top root owns
with a root-owned ``lost+found`` in it, with the template's values (a generated
setup code, ``ROW_BOT_HOSTED_BY=railway``, the public address) and no
secret-store key, so Row-Bot makes its own on the volume. It also checks, after
the first start and after the restart, that:

- Row-Bot and every program it started, the server listening on 8080 among
  them, run as uid and gid 10001 with no other groups;
- everything in /data is 10001's, /data is 0700, and the volume was handed over
  at the first start only; Row-Bot's own key is there;
- a program of uid 10001 can't read the starting environment of Row-Bot's
  launcher or server (H2 still holds after the drop).

The model is a fake: ``dummy_openai_endpoint.py``, run inside the container on
its loopback and answering only the saved key. No real provider, no network.
The container's memory high-water mark (cgroup ``memory.peak``) goes to the job
summary, for the release checklist's 3.2 GB.

Dependency-free like ``smoke_docker_server.py``, whose owned-resource rules,
HTTP adapter and diagnostics it reuses. It never builds or pulls an image.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import secrets
import socket
import subprocess
import sys
import tempfile
import uuid
from typing import Mapping, Sequence
from urllib.parse import parse_qs, urlsplit

try:  # run as a script, from its own folder
    from smoke_docker_server import KIND_LABEL, SMOKE_LABEL, STOP_GRACE_SECONDS, DockerServerSmoke, SmokeError
except ModuleNotFoundError:  # imported as scripts.smoke_hosting_partner
    from scripts.smoke_docker_server import KIND_LABEL, SMOKE_LABEL, STOP_GRACE_SECONDS, DockerServerSmoke, SmokeError

FAKE_MODEL_SERVER = Path(__file__).resolve().parent / "dummy_openai_endpoint.py"
# Inside the container, on its own loopback.
FAKE_MODEL_PORT = 8157
FAKE_MODEL_REPLY = "Dummy endpoint stream OK"
PROVIDER_ID = "custom_openai_smoke"
MODEL_REF = f"model:{PROVIDER_ID}:row-bot-dummy-chat"
# What the launcher logs once it has reached its own server (row-bot serve).
RUNNING_LINE = "Row-Bot is running at"
# The one-click sign-in link Row-Bot prints beside the code while nobody owns it (A1).
LINK_LINE = "Open this link to sign in: "
# The pod's own name, as a host gives each pod one (PikaPods: <pod>.pikapod.net). The runner reaches the
# published port by 127.0.0.1 and names the pod in Host, as a browser at the pod's address does.
POD_HOST = "pod.row-bot.test"
CONFIGURATION = "/api/v1/settings/providers/configuration"
PROVIDER_COMMANDS = "/api/v1/settings/providers/commands"
# Row-Bot's own setup-code alphabet (Crockford's base32) and length.
CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_LENGTH = 12
STARTUP_TIMEOUT = 180.0
REQUEST_TIMEOUT = 30.0
TURN_TIMEOUT = 120.0
# The recommended pod has 4 GB; the release checklist keeps this smoke's high-water mark under 3.2 GB.
MEMORY_BUDGET_BYTES = 3_200_000_000
SHAPES = ("partner", "railway")
# The image's user (deploy/docker/Dockerfile), which Row-Bot becomes when a host starts it as root.
ROW_BOT_UID = 10001
HANDED_OVER_LINE = f"Row-Bot gave /data to uid {ROW_BOT_UID}"
RUNS_AS_LINE = f"runs as uid {ROW_BOT_UID}"

# Run as root in the container: who owns what in the data folder, and who Row-Bot's processes are (its
# launcher, ``root_pid``, and every process under it, the one listening on ``port`` among them). Programs
# started from outside, such as Docker's health check or this probe, are not Row-Bot's and aren't listed.
ROW_BOT_USER_PROBE = r"""
import json, os, sys
from pathlib import Path

data, port, root_pid = sys.argv[1], int(sys.argv[2]), sys.argv[3]
listening = set()
for table in ("/proc/net/tcp", "/proc/net/tcp6"):
    try:
        rows = Path(table).read_text().splitlines()[1:]
    except OSError:
        continue
    for row in rows:
        fields = row.split()
        if int(fields[1].rsplit(":", 1)[1], 16) == port and fields[3] == "0A":
            listening.add(f"socket:[{fields[9]}]")
processes, parents, server = {}, {}, None
for entry in Path("/proc").iterdir():
    if not entry.name.isdigit() or int(entry.name) == os.getpid():
        continue
    try:
        status = dict(line.split(":", 1) for line in (entry / "status").read_text().splitlines() if ":" in line)
    except OSError:
        continue
    parents[entry.name] = status["PPid"].strip()
    processes[entry.name] = {key.lower(): [int(v) for v in status[key].split()] for key in ("Uid", "Gid", "Groups")}
    # Another user's non-dumpable process shows its open files only to CAP_SYS_PTRACE.
    if server is None and listening:
        try:
            if any(os.readlink(fd) in listening for fd in (entry / "fd").iterdir()):
                server = entry.name
        except OSError:
            pass
row_bot, grew = {root_pid}, True
while grew:
    found = {pid for pid, parent in parents.items() if parent in row_bot} - row_bot
    row_bot |= found
    grew = bool(found)
owners = {}
for folder, folders, files in os.walk(data):
    for name in (*folders, *files):
        path = os.path.join(folder, name)
        info = os.lstat(path)
        owners[path] = [info.st_uid, info.st_gid]
top = os.lstat(data)
print(json.dumps({
    "processes": {pid: processes[pid] for pid in sorted(row_bot) if pid in processes},
    "server": server,
    "top": [top.st_uid, top.st_gid, top.st_mode & 0o777],
    "owners": owners,
}))
"""

# Run as uid 10001: the starting environment of each process named, if it can be read.
ENVIRONMENT_PROBE = r"""
import json, sys
readable = []
for pid in sys.argv[1:]:
    try:
        open(f"/proc/{pid}/environ", "rb").read()
    except PermissionError:
        continue
    readable.append(pid)
print(json.dumps(readable))
"""


def format_setup_code(code: str) -> str:
    """The form Row-Bot prints a code in: groups of four."""
    return "-".join(code[index : index + 4] for index in range(0, len(code), 4))


def partner_run_args(
    *,
    image: str,
    container_name: str,
    volume_name: str,
    ownership: str,
    port: int,
    env_file: str,
) -> tuple[str, ...]:
    """The host's run: one volume, one port, the image's own user, entrypoint and health check."""
    return (
        "docker",
        "run",
        "--detach",
        "--name",
        container_name,
        "--label",
        f"{SMOKE_LABEL}={ownership}",
        "--label",
        f"{KIND_LABEL}=container",
        "--publish",
        f"127.0.0.1:{port}:8080",
        "--mount",
        f"type=volume,src={volume_name},dst=/data",
        # The generated setup code and key come from a file, never the command line.
        "--env-file",
        env_file,
        "--env",
        "ROW_BOT_HOSTED_BY=pikapods",
        # The pod's own address, as a host sets it (PikaPods: https://<pod>.pikapod.net), and an exact host
        # list without 127.0.0.1, as a VPS sets it: the image's health check and the launcher still pass.
        "--env",
        f"ROW_BOT_PUBLIC_URL=http://{POD_HOST}:{port}",
        "--env",
        f"ROW_BOT_ALLOWED_HOSTS={POD_HOST}",
        # A smoke never sends install stats to the real service.
        "--env",
        "ROW_BOT_INSTALL_COUNT=0",
        image,
    )


def railway_run_args(
    *,
    image: str,
    container_name: str,
    volume_name: str,
    ownership: str,
    port: int,
    env_file: str,
) -> tuple[str, ...]:
    """Railway's run of the template (deploy/railway/README.md): as root, as ``RAILWAY_RUN_UID=0`` makes it,
    on the service's volume, with the template's variables and no secret-store key."""
    return (
        "docker",
        "run",
        "--detach",
        "--name",
        container_name,
        "--label",
        f"{SMOKE_LABEL}={ownership}",
        "--label",
        f"{KIND_LABEL}=container",
        "--user",
        "0",
        "--publish",
        f"127.0.0.1:{port}:8080",
        "--mount",
        f"type=volume,src={volume_name},dst=/data",
        # The generated setup code comes from a file, never the command line.
        "--env-file",
        env_file,
        "--env",
        "ROW_BOT_HOSTED_BY=railway",
        "--env",
        "RAILWAY_RUN_UID=0",
        # The service's own address, as the template sets it from RAILWAY_PUBLIC_DOMAIN (over https there).
        "--env",
        f"ROW_BOT_PUBLIC_URL=http://{POD_HOST}:{port}",
        "--env",
        "ROW_BOT_INSTALL_COUNT=0",
        image,
    )


def root_volume_args(*, image: str, volume_name: str, ownership: str) -> tuple[str, ...]:
    """A Railway volume as the container first sees it: its top root's, with a root-owned ``lost+found``.

    The volume is mounted without the image's /data copied in, the way a host's own volume is.
    """
    return (
        "docker",
        "run",
        "--rm",
        "--label",
        f"{SMOKE_LABEL}={ownership}",
        "--label",
        f"{KIND_LABEL}=volume-setup",
        "--user",
        "0",
        "--entrypoint",
        "/bin/sh",
        "--mount",
        f"type=volume,src={volume_name},dst=/data,volume-nocopy=true",
        image,
        "-c",
        "chown 0:0 /data && chmod 0755 /data && mkdir -m 0700 /data/lost+found && touch /data/lost+found/12",
    )


def check_row_bot_user(report: Mapping[str, object], *, uid: int = ROW_BOT_UID, gid: int = ROW_BOT_UID) -> None:
    """Started as root, Row-Bot runs as its own user, and everything in /data is that user's (R1)."""
    processes = report.get("processes")
    if not isinstance(processes, dict) or not processes:
        raise SmokeError("No Row-Bot process was found in the container")
    server = report.get("server")
    if server not in processes:
        raise SmokeError("The process listening on 8080 is not Row-Bot's")
    for pid, ids in processes.items():
        if ids.get("uid") != [uid] * 4 or ids.get("gid") != [gid] * 4 or ids.get("groups"):
            who = "Row-Bot's server" if pid == server else f"Row-Bot's process {pid}"
            raise SmokeError(f"{who} runs as uid {ids.get('uid')} gid {ids.get('gid')} groups {ids.get('groups')}")
    if report.get("top") != [uid, gid, 0o700]:
        raise SmokeError(f"/data is {report.get('top')} (uid, gid, mode), not Row-Bot's own 0700 folder")
    owners = report.get("owners")
    if not isinstance(owners, dict):
        raise SmokeError("The probe did not list /data")
    others = sorted(path for path, owner in owners.items() if owner != [uid, gid])
    if others:
        raise SmokeError(f"{len(others)} entries in /data are not Row-Bot's, such as {others[0]}")


def check_container_log(log: str, *, code: str, public_origin: str, secret_values: Sequence[str]) -> None:
    """The first start prints the code once, readably, with its sign-in link (A1); nothing prints either
    again, or prints a secret."""
    readable = format_setup_code(code)
    if f"Row-Bot setup code: {readable}" not in log:
        raise SmokeError("The container log does not show the setup code")
    if f"{LINK_LINE}{public_origin}/setup#code={readable}" not in log.splitlines():
        raise SmokeError("The container log does not show the sign-in link at the pod's address")
    if log.count(readable) != 2 or log.count(LINK_LINE) != 1 or code in log:
        raise SmokeError("The setup code appears in the container log after the first start's code line")
    if any(value and value in log for value in secret_values):
        raise SmokeError("A secret appears in the container log")


def link_code(log: str) -> str:
    """The code in the logged sign-in link's fragment, as the setup page's script reads it (A1)."""
    for line in log.splitlines():
        if line.startswith(LINK_LINE):
            fragment = urlsplit(line[len(LINK_LINE):].strip()).fragment
            codes = parse_qs(fragment).get("code")
            if codes:
                return codes[0]
    raise SmokeError("The container log does not show the sign-in link")


def check_log_streams(out: str, err: str) -> None:
    """In Row-Bot's image INFO lines are on stdout and only warnings on stderr, which hosts colour as errors (F2)."""
    for line, what in ((RUNNING_LINE, "that Row-Bot is running"), ("Row-Bot setup code:", "the setup code")):
        if line not in out:
            raise SmokeError(f"The container's stdout does not say {what}")
        if line in err:
            raise SmokeError(f"The container's stderr says {what}, which a host shows as an error")


def memory_report(architecture: str, peaks: Sequence[int | None], shape: str = "partner") -> tuple[str, bool]:
    """The job summary's lines, and whether the high-water mark is within the release checklist's budget."""
    measured = [peak for peak in peaks if peak is not None]
    if shape == "railway":
        lines = [f"## Hosting smoke, Railway's shape ({architecture})", ""]
        lines.append(
            "Passed: started as root on a root-owned volume, ran as uid 10001 with /data handed over once, "
            "healthy, setup code shown and claimed, a saved API key and a chat turn before and after "
            "`docker restart`, no code in the log after the first start's code line."
        )
    else:
        lines = [f"## Hosting partner smoke ({architecture})", ""]
        lines.append(
            "Passed: healthy, setup code shown and claimed, a saved API key and a chat turn "
            "before and after `docker restart`, no key or code in the log after the first start's code line."
        )
    if not measured:
        lines.append("")
        lines.append("Memory high-water mark: not measured (no cgroup `memory.peak` in the container).")
        return "\n".join(lines) + "\n", True
    peak = max(measured)
    within = peak < MEMORY_BUDGET_BYTES
    each = "; ".join(
        f"{start} {'not measured' if value is None else f'{value / 1e9:.2f} GB'}"
        for start, value in zip(("first start", "after the restart"), peaks)
    )
    lines.append("")
    lines.append(
        f"Memory high-water mark: **{peak / 1e9:.2f} GB** (cgroup `memory.peak`; {each}). "
        f"The release checklist wants it under {MEMORY_BUDGET_BYTES / 1e9:.1f} GB"
        + ("." if within else ": **over**. Update the recommended pod size in the docs and the listing.")
    )
    return "\n".join(lines) + "\n", within


class _PodAddress:
    """The runner's HTTP, naming the pod in Host as a browser at the pod's address does."""

    def __init__(self, http, authority: str) -> None:  # noqa: ANN001
        self._http, self._authority = http, authority

    def send(self, method: str, url: str, *, headers: Mapping[str, str] | None = None,
             json_body: Mapping[str, object] | None = None, timeout: float | None = None):  # noqa: ANN201
        return self._http.send(method, url, headers={"Host": self._authority, **(headers or {})},
                               json_body=json_body, timeout=timeout)

    def cookie_values(self) -> tuple[str, ...]:
        return self._http.cookie_values()


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class HostingPartnerSmoke(DockerServerSmoke):
    def __init__(self, *, image: str, port: int | None = None, shape: str = "partner", **kwargs: object) -> None:
        super().__init__(image=image, **kwargs)  # type: ignore[arg-type]
        if shape not in SHAPES:
            raise ValueError(f"--shape must be one of {', '.join(SHAPES)}")
        self.shape = shape
        self.port = port or _free_loopback_port()
        self.origin = f"http://127.0.0.1:{self.port}"
        self.public_origin = f"http://{POD_HOST}:{self.port}"
        self._direct = self.http
        self.http = _PodAddress(self.http, f"{POD_HOST}:{self.port}")
        self.code ="".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        # Railway's template sets no secret-store key: Row-Bot makes its own on the volume.
        self.key = secrets.token_hex(32) if shape == "partner" else ""
        self.model_key = "fake-smoke-model-" + secrets.token_urlsafe(24)
        # Redacted from every diagnostic; the code's line is the only place the code may appear.
        self._secrets.extend((self.code, format_setup_code(self.code), self.key, self.model_key))
        self._env_file = ""
        self._client: dict[str, str] = {}

    # ── the container ───────────────────────────────────────────────────────

    def _create_container(self) -> None:
        run_args = partner_run_args if self.shape == "partner" else railway_run_args
        self.runner.run(
            run_args(
                image=self.image,
                container_name=self.container_name,
                volume_name=self.volume_name,
                ownership=self.suffix,
                port=self.port,
                env_file=self._env_file,
            )
        )

    def _wait_started(self, starts: int) -> None:
        """Healthy by the image's own check, and the launcher says Row-Bot is running (for the ``starts``th time).

        The launcher says so once it has reached its own server; one that can't stops the container.
        """
        deadline = self._monotonic() + self.startup_timeout
        while True:
            state = self.runner.run(
                ("docker", "inspect", "--format", "{{.State.Status}} {{.State.Health.Status}}", self.container_name),
                check=False,
            ).stdout.split()
            if state and state[0] in {"exited", "dead"}:
                raise SmokeError(f"The container stopped ({state[0]})")
            running = self._log().count(RUNNING_LINE)
            if state[1:] == ["healthy"] and running >= starts:
                break
            if self._monotonic() >= deadline:
                raise SmokeError(
                    f"The container did not start ({' '.join(state) or 'no state'}; "
                    f"the launcher said Row-Bot is running {running} of {starts} times)"
                )
            self._sleep(1.0)
        self._wait_ready(self.origin)

    def _log_streams(self) -> tuple[str, str]:
        """The container's stdout and stderr, apart (``docker logs`` replays each to its own)."""
        result = self.runner.run(("docker", "logs", self.container_name), check=False)
        return result.stdout, result.stderr

    def _log(self) -> str:
        return "\n".join(self._log_streams())

    def _check_runs_as_row_bot(self) -> None:
        """Railway's shape: Row-Bot runs as 10001, /data is all 10001's, and H2's private environment holds."""
        # The probe alone runs privileged: which process listens on 8080 shows in its open files, and a
        # container's root may read those of Row-Bot's non-dumpable uid-10001 processes only with
        # CAP_SYS_PTRACE, which Docker doesn't give it. The container itself stays as Railway runs it.
        probe = self.runner.run(
            ("docker", "exec", "--privileged", self.container_name, "python", "-c", ROW_BOT_USER_PROBE,
             "/data", "8080", "1")
        )
        try:
            report = json.loads(probe.stdout)
        except json.JSONDecodeError as exc:
            raise SmokeError("The user probe returned invalid JSON") from exc
        check_row_bot_user(report)
        if not report["owners"].get("/data/secret-store-key/ROW_BOT_SECRET_STORE_KEY"):
            raise SmokeError("Row-Bot did not make its own secret-store key on the volume")
        private = self.runner.run(
            ("docker", "exec", "--user", f"{ROW_BOT_UID}:{ROW_BOT_UID}", self.container_name, "python", "-c",
             ENVIRONMENT_PROBE, "1", str(report["server"]))
        )
        if json.loads(private.stdout) != []:
            raise SmokeError("A program of uid 10001 can read the starting environment of Row-Bot's launcher or server")

    def _memory_peak(self) -> int | None:
        result = self.runner.run(
            ("docker", "exec", self.container_name, "cat", "/sys/fs/cgroup/memory.peak"), check=False
        )
        text = result.stdout.strip()
        return int(text) if result.returncode == 0 and text.isdigit() else None

    def _start_fake_model(self) -> None:
        digest = hashlib.sha256(self.model_key.encode("utf-8")).hexdigest()
        source = FAKE_MODEL_SERVER.read_text(encoding="utf-8")
        # In Railway's shape a plain exec is root's; the fake model is the image user's program, as in the other.
        user = ("--user", f"{ROW_BOT_UID}:{ROW_BOT_UID}") if self.shape == "railway" else ()
        self.runner.run(
            ("docker", "exec", "--detach", *user, self.container_name, "python", "-c", source,
             "--port", str(FAKE_MODEL_PORT), "--api-key-sha256", digest)
        )
        listening = f"import socket; socket.create_connection(('127.0.0.1', {FAKE_MODEL_PORT}), 2).close()"
        deadline = self._monotonic() + 30.0
        while self.runner.run(
            ("docker", "exec", self.container_name, "python", "-c", listening), check=False
        ).returncode != 0:
            if self._monotonic() >= deadline:
                raise SmokeError("The fake model server did not start in the container")
            self._sleep(0.5)

    # ── the HTTP API, as the browser uses it ────────────────────────────────

    def _api(
        self,
        method: str,
        path: str,
        *,
        body: Mapping[str, object] | None = None,
        expect: int = 200,
        command_id: str = "",
    ) -> dict:
        headers = {"Origin": self.public_origin, "Accept": "application/json", **self._client}
        if command_id:
            headers["Idempotency-Key"] = command_id
        stage = f"{method} {path.split('?')[0]}"
        response = self._send_http(
            method, self.origin, path, stage=stage, headers=headers, json_body=body,
            retry_transient=method == "GET",
        )
        payload = response.json() if response.body else {}
        if response.status != expect or not isinstance(payload, dict):
            code = payload.get("code") or payload.get("error") if isinstance(payload, dict) else None
            raise SmokeError(f"{stage} returned HTTP {response.status} ({code or 'no code'})")
        return payload

    def _command(self, path: str, kind: str, payload: Mapping[str, object], *, expect: int = 200) -> dict:
        command_id = str(uuid.uuid4())
        command = {
            "command_id": command_id,
            "client_session_id": self._client["X-Client-Session"],
            "type": kind,
            "expected_revision": "0",
            "payload": dict(payload),
        }
        return self._api("POST", path, body=command, expect=expect, command_id=command_id)

    def _claim(self, code: str) -> str:
        """Sign in as the setup page does, with the code a browser took from the logged link (A1)."""
        response = self._send_http(
            "POST", self.origin, "/api/access/setup", stage="setup-code sign-in",
            headers={"Origin": self.public_origin, "Accept": "application/json"}, json_body={"code": code},
        )
        payload = response.json()
        if response.status != 200 or not isinstance(payload, dict) or payload.get("authenticated") is not True:
            raise SmokeError(f"The setup code did not sign in (HTTP {response.status})")
        cookies = self.http.cookie_values()
        if len(cookies) != 1:
            raise SmokeError("The setup-code sign-in did not issue exactly one session cookie")
        self._secrets.append(cookies[0])
        return self._session_id()

    def _check_exact_hosts(self) -> None:
        """Only the pod's name is allowed: from outside, a request naming 127.0.0.1 is refused."""
        stage = "a request naming 127.0.0.1"
        try:
            response = self._direct.send("GET", self.origin + "/healthz", timeout=self.request_timeout)
        except SmokeError as exc:
            raise SmokeError(f"{stage} failed: {exc}") from exc
        payload = response.json() if response.body else {}
        if response.status != 400 or not isinstance(payload, dict) or payload.get("error") != "unexpected_host":
            raise SmokeError(f"{stage} from outside returned HTTP {response.status}, not unexpected_host")

    def _session_id(self) -> str:
        payload = self._api("GET", "/api/access/session")
        if payload.get("authenticated") is not True or not payload.get("session_id"):
            raise SmokeError("This browser is not signed in")
        return str(payload["session_id"])

    def _handshake(self) -> None:
        self._client = {}
        payload = self._api("POST", "/api/v1/handshake", body={})
        self._client = {"X-Client-Session": str(payload["client_session_id"]), "X-CSRF-Token": str(payload["csrf_token"])}

    def _endpoint(self, operation: str, fields: Mapping[str, object]) -> None:
        revision = self._api("GET", CONFIGURATION)["revision"]
        review = self._api(
            "POST", CONFIGURATION + "/review",
            body={"operation": operation, "configuration_revision": revision, "fields": dict(fields)},
        )
        self._command(PROVIDER_COMMANDS, operation,
                      {"configuration_revision": revision, "fields": dict(fields), "nonce": review["nonce"]})

    def _key_saved(self) -> bool:
        return self._api("GET", f"/api/v1/settings/providers/{PROVIDER_ID}/credential").get("configured") is True

    def _connect_fake_model(self) -> None:
        self._endpoint("provider.endpoint.create", {
            "endpoint_id": "smoke",
            "display_name": "Smoke model server",
            "base_url": f"http://127.0.0.1:{FAKE_MODEL_PORT}/v1",
            "auth_required": True,
        })
        credential = f"/api/v1/settings/providers/{PROVIDER_ID}"
        revision = self._api("GET", credential + "/credential")["revision"]
        review = self._api("POST", credential + "/credential-review",
                           body={"provider_revision": revision, "operation": "save", "value": self.model_key})
        self._command(PROVIDER_COMMANDS, "provider.custom_credential.save", {
            "provider_id": PROVIDER_ID, "provider_revision": revision, "nonce": review["nonce"], "value": self.model_key,
        })
        if not self._key_saved():
            raise SmokeError("The API key was not saved")
        self._endpoint("provider.endpoint.probe", {"endpoint_id": "smoke"})
        items = self._api("GET", CONFIGURATION)["items"]
        state = next((item.get("probe_state") for item in items if item.get("provider_id") == PROVIDER_ID), None)
        if state != "agent_ready":
            raise SmokeError(f"The fake model server probed as {state!r}, not Agent ready")

    def _chat(self, conversation_id: str, replies: int) -> None:
        submitted = self._command(
            f"/api/v1/conversations/{conversation_id}/commands", "conversation.submit",
            {"submission_id": str(uuid.uuid4()), "text": "Hello from the hosting smoke",
             "model_selection": {"provider_id": PROVIDER_ID, "model_ref": MODEL_REF}},
            expect=202,
        )
        deadline = self._monotonic() + TURN_TIMEOUT
        while True:
            transcript = self._api("GET", f"/api/v1/conversations/{conversation_id}/transcript")
            generation = transcript.get("generation") or {}
            if generation.get("generation_id") == submitted["generation_id"] and generation.get("status") not in {
                "running", "stopping"
            }:
                break
            if self._monotonic() >= deadline:
                raise SmokeError("The chat turn did not finish in time")
            self._sleep(1.0)
        if generation.get("status") != "completed":
            raise SmokeError(f"The chat turn ended {generation.get('status')!r}, not completed")
        answers = [
            row for row in transcript.get("rows", [])
            if row.get("role") == "assistant"
            and any(FAKE_MODEL_REPLY in str(block.get("text") or "") for block in row.get("blocks", []))
        ]
        if len(answers) != replies:
            raise SmokeError(f"The conversation holds {len(answers)} answers from the fake model, not {replies}")

    # ── the run ─────────────────────────────────────────────────────────────

    def run(self) -> list[int | None]:  # type: ignore[override]
        """The partner-shape checks; returns the memory high-water mark of each start."""
        self._reject_collisions()
        with tempfile.TemporaryDirectory(prefix=f"row-bot-hosting-smoke-{self.suffix}-") as directory:
            env_file = Path(directory) / "host.env"
            key_line = f"ROW_BOT_SECRET_STORE_KEY={self.key}\n" if self.key else ""
            env_file.write_text(f"ROW_BOT_SETUP_CODE={self.code}\n{key_line}", encoding="ascii", newline="\n")
            os.chmod(env_file, 0o600)
            self._env_file = str(env_file)
            try:
                self._create_volume()
                if self.shape == "railway":
                    self.runner.run(
                        root_volume_args(image=self.image, volume_name=self.volume_name, ownership=self.suffix)
                    )
                self._create_container()
                self._wait_started(starts=1)
                if self.shape == "railway":
                    self._check_runs_as_row_bot()
                else:
                    self._check_exact_hosts()
                if f"Row-Bot setup code: {format_setup_code(self.code)}" not in self._log():
                    raise SmokeError("The container log does not show the setup code")
                session_id = self._claim(link_code(self._log()))
                self._start_fake_model()
                self._handshake()
                self._connect_fake_model()
                conversation_id = self._command(
                    "/api/v1/conversations/commands", "conversation.create", {"title": "Hosting smoke"}
                )["conversation_id"]
                self._chat(conversation_id, replies=1)
                peaks = [self._memory_peak()]

                self.runner.run(
                    ("docker", "restart", "--time", str(STOP_GRACE_SECONDS), self.container_name),
                    timeout=STOP_GRACE_SECONDS + 30,
                )
                self._client = {}
                self._wait_started(starts=2)
                if self.shape == "railway":
                    self._check_runs_as_row_bot()
                if self._session_id() != session_id:
                    raise SmokeError("The session changed across the restart")
                self._handshake()
                if not self._key_saved():
                    raise SmokeError("The saved API key did not survive the restart")
                self._start_fake_model()
                self._chat(conversation_id, replies=2)
                peaks.append(self._memory_peak())

                log = self._log()
                check_container_log(
                    log, code=self.code, public_origin=self.public_origin, secret_values=(self.key, self.model_key)
                )
                check_log_streams(*self._log_streams())
                if self.shape == "railway":
                    # The volume is handed over at the first start; the restart finds it Row-Bot's already.
                    if log.count(HANDED_OVER_LINE) != 1 or log.count(RUNS_AS_LINE) != 2:
                        raise SmokeError("Row-Bot did not hand /data over at the first start only")
                elif self.runner.run(
                    ("docker", "exec", self.container_name, "test", "-e", "/data/secret-store-key"), check=False
                ).returncode == 0:
                    raise SmokeError("Row-Bot made a key on /data although the host set one")
                return peaks
            except Exception:
                try:
                    self._emit_failure_diagnostics()
                except Exception as diagnostic_exc:
                    print(f"hosting smoke diagnostics unavailable ({type(diagnostic_exc).__name__})", file=sys.stderr)
                raise
            finally:
                self.cleanup()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smoke an already-built Row-Bot image in a hosting service's shape.")
    parser.add_argument("--image", required=True, help="Existing local image reference")
    parser.add_argument("--startup-timeout", type=float, default=STARTUP_TIMEOUT,
                        help=f"Seconds to wait for a healthy container (default: {STARTUP_TIMEOUT:g})")
    parser.add_argument("--shape", choices=SHAPES, default="partner",
                        help="partner: as the image's user, with the host's key (PikaPods); "
                             "railway: as root on a root-owned volume, with no key (Railway's template)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        peaks = HostingPartnerSmoke(
            image=args.image, shape=args.shape, startup_timeout=args.startup_timeout,
            request_timeout=REQUEST_TIMEOUT,
        ).run()
    except (SmokeError, subprocess.SubprocessError, OSError, ValueError, KeyError) as exc:
        print(f"hosting partner smoke failed: {exc}", file=sys.stderr)
        return 1
    report, within = memory_report(platform.machine(), peaks, args.shape)
    print(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(report)
    if not within:
        print("::warning::The hosting smoke's memory high-water mark is over the release checklist's 3.2 GB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
