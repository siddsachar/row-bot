"""A turn on the local model that fails makes Monitor check that model's
server again at once, instead of within the hour (B252)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.contracts.client_platform.test_headless_lifecycle import command, platform  # noqa: F401
from tests.helpers.client_platform_fakes import ScriptedAgentStream, fixture_id

pytestmark = pytest.mark.subsystem


def _turn(service, fake: ScriptedAgentStream, label: str, provider_id: str, model_ref: str) -> None:
    service.stream_factory = fake.stream
    service.resume_factory = fake.resume
    accepted = service.execute(
        owner_id="fixture-owner", idempotency_key=fixture_id(label + ":key"), target="conversation-a",
        command=command("conversation.submit", label, {
            "submission_id": fixture_id(label), "text": "Synthetic input", "attachment_refs": [],
            "model_selection": {"provider_id": provider_id, "model_ref": model_ref}}))
    assert service.registry.get(accepted["execution_id"]).producer_done.wait(10)


def test_only_a_failed_local_model_turn_schedules_a_recheck(platform, monkeypatch):  # noqa: F811
    from row_bot.application import client_diagnosis

    scheduled: list[str] = []
    monkeypatch.setattr(client_diagnosis, "_scheduler",
                        SimpleNamespace(add_job=lambda func, **options: scheduled.append(options["id"])))

    _turn(platform, ScriptedAgentStream((("done", "Fine"),)), "local-ok", "ollama", "model:ollama:fixture")
    _turn(platform, ScriptedAgentStream((("error", "Synthetic failure"),)), "cloud-failed", "fixture",
          "fixture/model")
    assert scheduled == []

    _turn(platform, ScriptedAgentStream((("error", "Synthetic failure"),)), "local-failed", "ollama",
          "model:ollama:fixture")
    assert scheduled == [client_diagnosis.MODEL_JOB]
