"""Smoke the Org Company Server's Compose deployment (deploy/org/compose.yaml) with an image already built.

    python scripts/smoke_org_compose.py --image row-bot-org:ci

Standard library only, like ``smoke_docker_server.py``. It never builds the server image (the workflow
does; Compose pulls Postgres). It writes its own secrets with ``deploy/org/make_secrets.py`` and its own
settings into a temporary folder, runs ``docker compose`` under a random project name from there, and always
ends with ``down --volumes``. On a failure it prints the services' logs with every secret value it wrote
blanked out.

The identity provider it configures is never contacted: its issuer is the server container's own loopback,
and nobody starts a sign-in (the server reads the provider only then). That is the smallest setting with
which the server serves sign-in and the admin console.

What it checks, following the first run in deploy/org/README.md:
1. every service comes up: Postgres and the server healthy, ``migrate`` exited 0;
2. ``/readyz`` answers 200;
3. ``/console/`` answers with the console's strict Content-Security-Policy: a sign-in redirect for someone
   signed out (or the console);
4. the server refuses to start as the owner role;
5. ``principals grant-admin`` for someone who never signed in fails cleanly;
6. a stopped and started deployment runs ``migrate`` again with nothing to apply, and keeps its data: a
   person stored before is made an admin after.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from email.message import Message
from pathlib import Path
from urllib import error, request

REPO = Path(__file__).resolve().parents[1]
COMPOSE_FILE = REPO / "deploy" / "org" / "compose.yaml"
SECRETS_HELPER = REPO / "deploy" / "org" / "make_secrets.py"
# row_bot_org.console.CONSOLE_CSP
CONSOLE_CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; base-uri 'none'; "
               "form-action 'self'; frame-ancestors 'none'")
SIGN_IN_REDIRECT = "/auth/login?next=%2Fconsole%2F"
SMOKE_IDP = {"kind": "okta", "issuer": "http://127.0.0.1:9", "client_id": "row-bot-org-smoke"}
STORED_PERSON = "smoke-person@example.com"
UP_TIMEOUT = 600.0
HEALTHY_TIMEOUT = 180.0
READY_TIMEOUT = 60.0
COMMAND_TIMEOUT = 180.0
LOG_LINES = 300


class SmokeError(RuntimeError):
    """A check of the Compose deployment failed."""


class _NoRedirects(request.HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class Deployment:
    """One Compose project of the deployment, with its secrets and settings in ``folder``."""

    def __init__(self, image: str, folder: Path) -> None:
        self.project = "rbo-smoke-" + secrets.token_hex(4)
        self.port = _free_port()
        self.secrets_dir = folder / "secrets"
        env_file = folder / "org.env"
        subprocess.run([sys.executable, str(SECRETS_HELPER), "--dir", str(self.secrets_dir)], check=True,
                       capture_output=True, timeout=60)
        with open(self.secrets_dir / "ROW_BOT_ORG_IDP_CLIENT_SECRET", "w", encoding="utf-8", newline="\n") as file:
            file.write("row-bot-org-smoke-" + secrets.token_urlsafe(16))
        with open(env_file, "w", encoding="utf-8", newline="\n") as file:
            file.write(f"ROW_BOT_ORG_PUBLIC_URL=http://127.0.0.1:{self.port}\n"
                       f"ROW_BOT_ORG_IDP='{json.dumps(SMOKE_IDP)}'\n")
        self.hidden = sorted({path.read_text(encoding="utf-8") for path in self.secrets_dir.iterdir()} - {""},
                             key=len, reverse=True)
        self.env = {name: value for name, value in os.environ.items() if not name.startswith("ROW_BOT_ORG_")}
        self.env.update(ROW_BOT_ORG_IMAGE=image, ROW_BOT_ORG_SECRETS_DIR=str(self.secrets_dir),
                        ROW_BOT_ORG_ENV_FILE=str(env_file), ROW_BOT_ORG_BIND_ADDRESS="127.0.0.1",
                        ROW_BOT_ORG_HOST_PORT=str(self.port))

    def redact(self, text: str) -> str:
        for value in self.hidden:
            text = text.replace(value, "[secret]")
        return text

    def run(self, args: list[str], *, timeout: float = COMMAND_TIMEOUT) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(args, env=self.env, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            raise SmokeError(f"{' '.join(args[:6])} ... took longer than {timeout:g} s") from None

    def compose(self, *args: str, check: bool = True,
                timeout: float = COMMAND_TIMEOUT) -> subprocess.CompletedProcess[str]:
        result = self.run(["docker", "compose", "--project-name", self.project, "--file", str(COMPOSE_FILE),
                           *args], timeout=timeout)
        if check and result.returncode != 0:
            detail = self.redact((result.stderr or result.stdout).strip())[-2000:]
            raise SmokeError(f"docker compose {' '.join(args)} exited {result.returncode}: {detail}")
        return result

    def state(self, service: str) -> tuple[str, int, str]:
        """The service's container: its status, exit code and health ("" without a health check)."""
        ids = self.compose("ps", "--all", "--quiet", service).stdout.split()
        if not ids:
            return "missing", -1, ""
        result = self.run(["docker", "inspect", "--format",
                           "{{.State.Status}} {{.State.ExitCode}} {{if .State.Health}}{{.State.Health.Status}}{{end}}",
                           ids[0]])
        if result.returncode != 0:
            return "missing", -1, ""
        status, code, *health = result.stdout.split()
        return status, int(code), health[0] if health else ""

    def wait_until_up(self) -> None:
        deadline = time.monotonic() + HEALTHY_TIMEOUT
        while True:
            migrate, postgres, server = self.state("migrate"), self.state("postgres"), self.state("row-bot-org")
            if migrate[0] == "exited" and migrate[1] != 0:
                raise SmokeError(f"migrate exited {migrate[1]}")
            if server[0] in {"exited", "dead"}:
                raise SmokeError(f"the server stopped (exit {server[1]})")
            if migrate[:2] == ("exited", 0) and postgres[2] == "healthy" and server[2] == "healthy":
                return
            if time.monotonic() > deadline:
                raise SmokeError(f"not up after {HEALTHY_TIMEOUT:g} s: migrate {migrate}, postgres {postgres}, "
                                 f"server {server}")
            time.sleep(3)

    def get(self, path: str) -> tuple[int, Message]:
        opener = request.build_opener(_NoRedirects)
        try:
            with opener.open(f"http://127.0.0.1:{self.port}{path}", timeout=10) as response:
                return response.status, response.headers
        except error.HTTPError as answer:
            return answer.code, answer.headers

    def wait_until_ready(self) -> None:
        deadline = time.monotonic() + READY_TIMEOUT
        status = 0
        while time.monotonic() < deadline:
            try:
                status, _ = self.get("/readyz")
            except OSError:
                status = 0
            if status == 200:
                return
            time.sleep(2)
        raise SmokeError(f"/readyz answered {status or 'nothing'}, not 200, for {READY_TIMEOUT:g} s")

    def operator(self, *args: str) -> subprocess.CompletedProcess[str]:
        """``row-bot-org`` in a one-off ``migrate`` container, as the owner (the guide's operator commands)."""
        return self.compose("run", "--rm", "-T", "migrate", *args, check=False)


def _up(deployment: Deployment) -> None:
    deployment.compose("up", "--detach", "--no-build", timeout=UP_TIMEOUT)
    deployment.wait_until_up()
    deployment.wait_until_ready()


def _check_console(deployment: Deployment) -> None:
    status, headers = deployment.get("/console/")
    if headers.get("Content-Security-Policy") != CONSOLE_CSP:
        raise SmokeError(f"/console/ ({status}) doesn't carry the console's Content-Security-Policy")
    if not (status == 303 and headers.get("Location") == SIGN_IN_REDIRECT
            or status == 200 and (headers.get("Content-Type") or "").startswith("text/html")):
        raise SmokeError(f"/console/ answered {status}, neither the sign-in redirect nor the console")


def _check_owner_refused(deployment: Deployment) -> None:
    # The owner's address from the migrate service's own secret file, so it never crosses the host's command line.
    owner_serve = ('ROW_BOT_ORG_DATABASE_URL="$(cat /run/secrets/ROW_BOT_ORG_MIGRATION_DATABASE_URL)" '
                   "exec python -m row_bot_org.cli serve")
    result = deployment.compose("run", "--rm", "--no-deps", "-T", "--entrypoint", "sh", "migrate", "-c",
                                owner_serve, check=False)
    output = result.stdout + result.stderr
    if result.returncode != 1 or "owns (or is a member of the owner of)" not in output:
        raise SmokeError(f"serve as the owner role exited {result.returncode} without refusing the role: "
                         + deployment.redact(output.strip())[-1000:])


def _check_unknown_person(deployment: Deployment) -> None:
    result = deployment.operator("principals", "grant-admin", "nobody@example.com")
    output = result.stdout + result.stderr
    refused = "Nobody with the id or email nobody@example.com" in output and "Traceback" not in output
    if result.returncode != 1 or not refused:
        raise SmokeError(f"grant-admin for someone who never signed in exited {result.returncode}: "
                         + deployment.redact(output.strip())[-1000:])


def _store_person(deployment: Deployment) -> None:
    # As if they had signed in once: the server makes the row at sign-in.
    deployment.compose(
        "exec", "-T", "postgres", "psql", "--username", "postgres", "--dbname", "row_bot_org", "--quiet",
        "--set", "ON_ERROR_STOP=1", "--command",
        "INSERT INTO row_bot_org.principals (issuer, subject, display_name, email) "
        f"VALUES ('{SMOKE_IDP['issuer']}', 'smoke-person', 'Smoke Person', '{STORED_PERSON}')")


def _check_restart_keeps_data(deployment: Deployment) -> None:
    deployment.compose("down")
    _up(deployment)
    logs = deployment.compose("logs", "--no-color", "migrate").stdout
    if "no migration was pending" not in logs:
        raise SmokeError("migrate didn't find the database up to date after a restart")
    result = deployment.operator("principals", "grant-admin", STORED_PERSON)
    output = result.stdout + result.stderr
    if result.returncode != 0 or "is now an admin" not in output:
        raise SmokeError(f"grant-admin for the person stored before the restart exited {result.returncode}: "
                         + deployment.redact(output.strip())[-1000:])


CHECKS = (
    ("every service came up: Postgres and the server healthy, migrate exited 0, /readyz 200", _up),
    ("/console/ has the strict Content-Security-Policy and sends a signed-out browser to sign in", _check_console),
    ("the server refuses to start as the owner role", _check_owner_refused),
    ("grant-admin for someone who never signed in fails cleanly", _check_unknown_person),
    ("a person is stored", _store_person),
    ("after a restart migrate has nothing to apply and the stored person is made an admin", _check_restart_keeps_data),
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke deploy/org/compose.yaml with a built server image.")
    parser.add_argument("--image", required=True, help="the server image, already built (it is never built here)")
    image = parser.parse_args(argv).image
    with tempfile.TemporaryDirectory(prefix="row-bot-org-smoke-") as folder:
        deployment = Deployment(image, Path(folder))
        failed = True
        try:
            for name, check in CHECKS:
                check(deployment)
                print(f"ok: {name}", flush=True)
            failed = False
        except SmokeError as problem:
            print(f"FAILED: {problem}", file=sys.stderr, flush=True)
        finally:
            if failed:
                logs = deployment.compose("logs", "--no-color", "--timestamps", "--tail", str(LOG_LINES), check=False)
                print(deployment.redact(logs.stdout + logs.stderr), file=sys.stderr, flush=True)
            deployment.compose("down", "--volumes", "--remove-orphans", "--timeout", "10", check=False)
    if failed:
        return 1
    print("The Org Compose deployment passed its smoke.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
