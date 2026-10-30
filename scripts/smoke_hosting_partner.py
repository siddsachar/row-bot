"""Smoke an existing image the way a hosting service runs it (H9).

The host's shape (PikaPods'): one container from the unmodified image, as the
image's own user, with one volume at /data, one HTTP port and the host's values
in the environment: a generated ROW_BOT_SETUP_CODE and ROW_BOT_SECRET_STORE_KEY
(64 hex), ROW_BOT_HOSTED_BY, and the pod's own address as ROW_BOT_PUBLIC_URL and
the only ROW_BOT_ALLOWED_HOSTS. Through the HTTP API a browser uses, it checks
that:

- the container becomes healthy (the image's own check asks 127.0.0.1, which
  the allowed hosts leave out) and its log shows the setup code;
- the code signs this browser in;
- an API key saved for a custom endpoint, and a chat turn through it, work;
- after ``docker restart`` the container is healthy again, the session cookie
  still works, the key is still saved and a second chat turn uses it;
- the key and the code never reach the container log after the first start's
  code line, and the key never reaches /data.

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
        # The pod's own address only, as a host lists it: here `localhost`, which the runner reaches through
        # the published port. The image's health check asks 127.0.0.1 from inside, which only its loopback
        # rule lets through.
        "--env",
        f"ROW_BOT_PUBLIC_URL=http://localhost:{port}",
        "--env",
        "ROW_BOT_ALLOWED_HOSTS=localhost",
        # A smoke never sends install stats to the real service.
        "--env",
        "ROW_BOT_INSTALL_COUNT=0",
        image,
    )


def check_container_log(log: str, *, code: str, secret_values: Sequence[str]) -> None:
    """The first start prints the code once, readably; nothing prints it again, or prints a secret."""
    readable = format_setup_code(code)
    if f"Row-Bot setup code: {readable}" not in log:
        raise SmokeError("The container log does not show the setup code")
    if log.count(readable) != 1 or code in log:
        raise SmokeError("The setup code appears in the container log after the first start's code line")
    if any(value and value in log for value in secret_values):
        raise SmokeError("A secret appears in the container log")


def memory_report(architecture: str, peaks: Sequence[int | None]) -> tuple[str, bool]:
    """The job summary's lines, and whether the high-water mark is within the release checklist's budget."""
    measured = [peak for peak in peaks if peak is not None]
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


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class HostingPartnerSmoke(DockerServerSmoke):
    def __init__(self, *, image: str, port: int | None = None, **kwargs: object) -> None:
        super().__init__(image=image, **kwargs)  # type: ignore[arg-type]
        self.port = port or _free_loopback_port()
        self.origin = f"http://localhost:{self.port}"
        self.code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        self.key = secrets.token_hex(32)
        self.model_key = "fake-smoke-model-" + secrets.token_urlsafe(24)
        # Redacted from every diagnostic; the code's line is the only place the code may appear.
        self._secrets.extend((self.code, format_setup_code(self.code), self.key, self.model_key))
        self._env_file = ""
        self._client: dict[str, str] = {}

    # ── the container ───────────────────────────────────────────────────────

    def _create_container(self) -> None:
        self.runner.run(
            partner_run_args(
                image=self.image,
                container_name=self.container_name,
                volume_name=self.volume_name,
                ownership=self.suffix,
                port=self.port,
                env_file=self._env_file,
            )
        )

    def _wait_healthy(self) -> None:
        deadline = self._monotonic() + self.startup_timeout
        while True:
            state = self.runner.run(
                ("docker", "inspect", "--format", "{{.State.Status}} {{.State.Health.Status}}", self.container_name),
                check=False,
            ).stdout.split()
            if state and state[0] in {"exited", "dead"}:
                raise SmokeError(f"The container stopped ({state[0]})")
            if state[1:] == ["healthy"]:
                break
            if self._monotonic() >= deadline:
                raise SmokeError(f"The container did not become healthy ({' '.join(state) or 'no state'})")
            self._sleep(1.0)
        self._wait_ready(self.origin)

    def _log(self) -> str:
        result = self.runner.run(("docker", "logs", self.container_name), check=False)
        return "\n".join((result.stdout, result.stderr))

    def _memory_peak(self) -> int | None:
        result = self.runner.run(
            ("docker", "exec", self.container_name, "cat", "/sys/fs/cgroup/memory.peak"), check=False
        )
        text = result.stdout.strip()
        return int(text) if result.returncode == 0 and text.isdigit() else None

    def _start_fake_model(self) -> None:
        digest = hashlib.sha256(self.model_key.encode("utf-8")).hexdigest()
        source = FAKE_MODEL_SERVER.read_text(encoding="utf-8")
        self.runner.run(
            ("docker", "exec", "--detach", self.container_name, "python", "-c", source,
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
        headers = {"Origin": self.origin, "Accept": "application/json", **self._client}
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

    def _claim(self) -> str:
        response = self._send_http(
            "POST", self.origin, "/api/access/setup", stage="setup-code sign-in",
            headers={"Origin": self.origin, "Accept": "application/json"}, json_body={"code": self.code},
        )
        payload = response.json()
        if response.status != 200 or not isinstance(payload, dict) or payload.get("authenticated") is not True:
            raise SmokeError(f"The setup code did not sign in (HTTP {response.status})")
        cookies = self.http.cookie_values()
        if len(cookies) != 1:
            raise SmokeError("The setup-code sign-in did not issue exactly one session cookie")
        self._secrets.append(cookies[0])
        return self._session_id()

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
            env_file.write_text(
                f"ROW_BOT_SETUP_CODE={self.code}\nROW_BOT_SECRET_STORE_KEY={self.key}\n",
                encoding="ascii", newline="\n",
            )
            os.chmod(env_file, 0o600)
            self._env_file = str(env_file)
            try:
                self._create_volume()
                self._create_container()
                self._wait_healthy()
                if f"Row-Bot setup code: {format_setup_code(self.code)}" not in self._log():
                    raise SmokeError("The container log does not show the setup code")
                session_id = self._claim()
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
                self._wait_healthy()
                if self._session_id() != session_id:
                    raise SmokeError("The session changed across the restart")
                self._handshake()
                if not self._key_saved():
                    raise SmokeError("The saved API key did not survive the restart")
                self._start_fake_model()
                self._chat(conversation_id, replies=2)
                peaks.append(self._memory_peak())

                check_container_log(self._log(), code=self.code, secret_values=(self.key, self.model_key))
                if self.runner.run(
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        peaks = HostingPartnerSmoke(
            image=args.image, startup_timeout=args.startup_timeout, request_timeout=REQUEST_TIMEOUT
        ).run()
    except (SmokeError, subprocess.SubprocessError, OSError, ValueError, KeyError) as exc:
        print(f"hosting partner smoke failed: {exc}", file=sys.stderr)
        return 1
    report, within = memory_report(platform.machine(), peaks)
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
