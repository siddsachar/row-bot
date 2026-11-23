"""The Org server's example container keeps the app container's hardening (deploy/docker)."""

from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.contract, pytest.mark.installer]

ORG = Path(__file__).resolve().parents[3] / "deploy" / "org"


def _service() -> dict:
    compose = yaml.safe_load((ORG / "compose.yaml").read_text(encoding="utf-8"))
    return compose["services"]["row-bot-org"]


def _runtime_instructions() -> list[tuple[str, str]]:
    """The final stage's instructions, continuation lines joined."""
    text = (ORG / "Dockerfile").read_text(encoding="utf-8").replace("\\\n", " ")
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    final = max(index for index, line in enumerate(lines) if line.upper().startswith("FROM "))
    return [(line.split(None, 1)[0].upper(), line.split(None, 1)[1]) for line in lines[final + 1:]]


def test_the_compose_service_runs_hardened_and_publishes_on_loopback() -> None:
    service = _service()

    assert service["user"] == "10001:10001"
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert "cap_add" not in service
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert "privileged" not in service and "network_mode" not in service
    assert service["ports"] == ["${ROW_BOT_ORG_BIND_ADDRESS:-127.0.0.1}:${ROW_BOT_ORG_HOST_PORT:-8100}:8100"]
    assert any("/healthz" in part for part in service["healthcheck"]["test"])


def test_the_image_runs_as_10001_and_checks_healthz() -> None:
    instructions = _runtime_instructions()

    users = [value for name, value in instructions if name == "USER"]
    assert users == ["10001:10001"]
    healthcheck = next(value for name, value in instructions if name == "HEALTHCHECK")
    command = json.loads(healthcheck.split("CMD", 1)[1])
    assert command[:2] == ["env", "-i"] and "http://127.0.0.1:8100/healthz" in command[-1]
    entrypoint = next(value for name, value in instructions if name == "ENTRYPOINT")
    assert json.loads(entrypoint) == ["python", "-m", "row_bot_org.cli"]
    # Only the Org server's package and the built admin console are copied in, never the desktop app's.
    copied = [shlex.split(value) for name, value in instructions if name == "COPY"]
    assert copied == [["--from=builder", "/opt/row-bot-org", "/opt/row-bot-org"],
                      ["--from=console-build", "/build/frontend/dist-console", "/opt/row-bot-org/console"]]
