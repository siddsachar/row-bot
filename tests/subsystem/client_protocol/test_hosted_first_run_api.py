"""A hosted Row-Bot's first run and Setup Center (U3, U4, U10).

On a server whose host Row-Bot knows (``ROW_BOT_HOSTED_BY``), Setup adds "Add
your phone" and "Back up", a sign-in starts on its provider's own first model,
the phone card stays dismissed, and the final check names the host. Without a
known host nothing changes. Fakes stand in for providers: nothing reaches a
network, a real runtime or the keychain.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from row_bot import models
from row_bot.application import client_first_run, client_onboarding
from tests.subsystem.client_protocol.test_protocol_security import bootstrap, client_app

pytestmark = pytest.mark.subsystem


@pytest.fixture
def profile(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("ROW_BOT_HOSTED_BY", raising=False)
    monkeypatch.setattr(models, "_current_model", "")
    return tmp_path


def _send(action: str, **fields) -> dict:
    current = client_onboarding.read_onboarding()
    return client_onboarding.execute_onboarding(
        command_id=str(uuid4()), expected_revision=current["revision"], action=action,
        profile=fields.pop("profile", []), step=fields.pop("step", ""), **fields,
    )["snapshot"]


def _finished(profile) -> None:
    (profile / "model_settings.json").write_text(json.dumps({"model": "model:codex:gpt-5.6-sol"}), encoding="utf-8")


def test_a_hosted_setup_center_adds_the_phone_and_a_backup(profile, monkeypatch):
    _finished(profile)
    desktop = client_onboarding.read_onboarding()
    assert desktop["host_name"] is None
    assert [step["id"] for step in desktop["steps"]] == [
        "models", "knowledge", "workflows", "designer", "developer", "apps", "skills", "tools", "voice", "final",
    ]

    monkeypatch.setenv("ROW_BOT_HOSTED_BY", "pikapods")
    hosted = client_onboarding.read_onboarding()
    assert hosted["host_name"] == "PikaPods"
    steps = {step["id"]: step for step in hosted["steps"]}
    assert [step["id"] for step in hosted["steps"]] == [
        "models", "phone", "knowledge", "workflows", "designer", "developer", "apps", "skills", "tools", "voice",
        "backup", "final",
    ]
    assert steps["phone"]["title"] == "Add your phone"
    assert steps["backup"]["title"] == "Back up"

    done = _send("mark_done", step="phone")
    assert "phone" in done["completed_steps"]
    skipped = _send("skip_step", step="backup")
    assert "backup" in skipped["skipped_steps"]

    # The same profile on a computer (a restored backup) has no such areas, and can't mark them.
    monkeypatch.delenv("ROW_BOT_HOSTED_BY")
    moved = client_onboarding.read_onboarding()
    assert "phone" not in moved["completed_steps"] and "backup" not in moved["skipped_steps"]
    with pytest.raises(Exception, match="invalid_onboarding_command"):
        _send("mark_done", step="phone")


def test_an_unknown_host_changes_nothing(profile, monkeypatch):
    monkeypatch.setenv("ROW_BOT_HOSTED_BY", "somebody-else")
    snapshot = client_onboarding.read_onboarding()
    assert snapshot["host_name"] is None
    assert "phone" not in {step["id"] for step in snapshot["steps"]}


def test_the_phone_card_once_dismissed_never_comes_back(profile, monkeypatch):
    monkeypatch.setenv("ROW_BOT_HOSTED_BY", "pikapods")
    _finished(profile)
    first = client_onboarding.read_onboarding()
    assert first["dismissed_phone_card"] is False
    with pytest.raises(Exception, match="invalid_onboarding_command"):
        _send("dismiss_phone_card", step="phone")

    dismissed = _send("dismiss_phone_card")
    assert dismissed["dismissed_phone_card"] is True
    assert dismissed["revision"] != first["revision"]
    # Read again later (another browser, after a restart): still dismissed, and nothing else moved.
    again = client_onboarding.read_onboarding()
    assert again["dismissed_phone_card"] is True
    assert again["dismissed_home_card"] is False


def _rows(*specs: tuple[str, str, bool]):
    """Catalog rows as client_status reads them: (provider, model, ready)."""
    return [
        SimpleNamespace(provider_id=provider, model_id=model, selection_ref=f"model:{provider}:{model}",
                        categories=("chat",), configured=True, runtime_ready=ready, installed=True)
        for provider, model, ready in specs
    ]


@pytest.fixture
def catalog(monkeypatch):
    rows: list = []
    monkeypatch.setattr("row_bot.providers.client_status._read", lambda *_a, **_kw: (None, rows))
    monkeypatch.setattr("row_bot.providers.codex.list_codex_model_infos_for_status", lambda: [])
    monkeypatch.setattr("row_bot.providers.claude_subscription.list_claude_subscription_model_infos_for_status",
                        lambda: [SimpleNamespace(model_id="default"), SimpleNamespace(model_id="opus")])
    return rows


def test_a_sign_in_starts_on_its_providers_own_first_pick(profile, catalog, monkeypatch):
    """U3: after "Sign in with ChatGPT" Row-Bot opens a chat; it doesn't ask which of nine models."""
    chosen: list[str] = []
    monkeypatch.setattr(client_first_run, "choose_model", lambda ref: chosen.append(ref) or ref)
    # The catalog lists models by name; ChatGPT's recommended one isn't first there.
    catalog.extend(_rows(("codex", "gpt-5.2", True), ("codex", "gpt-5.5", True), ("codex", "gpt-5.6-sol", True),
                         ("claude_subscription", "haiku", True), ("claude_subscription", "default", True),
                         ("claude_subscription", "opus", False), ("openai", "gpt-4.1", True)))

    assert client_first_run.suggested_model("codex") == "model:codex:gpt-5.6-sol"
    # Claude Code's own list order (its default first); a model not ready now is never picked.
    assert client_first_run.suggested_model("claude_subscription") == "model:claude_subscription:default"
    assert client_first_run.suggested_model("xai_oauth") is None

    _send("choose_suggested", provider_id="codex")
    assert chosen == ["model:codex:gpt-5.6-sol"]
    with pytest.raises(Exception, match="model_configuration_unavailable"):
        _send("choose_suggested", provider_id="xai_oauth")
    with pytest.raises(Exception, match="invalid_onboarding_command"):
        _send("choose_suggested")
    with pytest.raises(Exception, match="invalid_onboarding_command"):
        _send("choose_suggested", provider_id="codex", model_ref="model:codex:gpt-5.2")
    assert chosen == ["model:codex:gpt-5.6-sol"]


def test_the_suggested_pick_becomes_the_saved_default(profile, catalog, monkeypatch):
    monkeypatch.setattr("row_bot.providers.selection.add_quick_choice_for_model", lambda *_a, **_kw: None)
    monkeypatch.setattr("row_bot.application.client_diagnosis.recheck_default_model", lambda: None)
    catalog.extend(_rows(("codex", "gpt-5.2", True), ("codex", "gpt-5.6-sol", True)))
    monkeypatch.setattr(client_first_run, "_chat_row", lambda ref: next(row for row in catalog if row.selection_ref == ref))

    snapshot = _send("choose_suggested", provider_id="codex")

    assert snapshot["needs_model"] is False
    assert snapshot["default_model"] == "model:codex:gpt-5.6-sol"
    assert client_first_run.saved_default_model() == "model:codex:gpt-5.6-sol"


def test_the_final_check_names_the_host_without_calling_out(profile, monkeypatch):
    """U4: "Hosted by PikaPods: reachable" is the server answering itself; no row on a computer."""
    from row_bot import status_checks
    from row_bot.application import client_diagnosis

    def no_network(*_args, **_kwargs):
        raise AssertionError("the host's check must not leave the server")

    monkeypatch.setattr("socket.create_connection", no_network)
    monkeypatch.setattr(status_checks, "LOCAL_CHECKS", (status_checks.check_hosting,))
    monkeypatch.setattr(status_checks, "NETWORK_CHECKS", ())

    assert client_diagnosis.run_system_diagnosis()["checks"] == []
    monkeypatch.setenv("ROW_BOT_HOSTED_BY", "pikapods")
    checks = client_diagnosis.run_system_diagnosis()["checks"]
    assert [(check["name"], check["status"], check["detail"]) for check in checks] == [
        ("Hosted by PikaPods", "ok", "Reachable"),
    ]
    assert checks[0]["fix"] is None


def test_the_handshake_says_when_a_known_host_runs_row_bot(profile, monkeypatch):
    client, _, _ = client_app(remote=True)
    with client:
        data, headers = bootstrap(client)
        assert "hosted" not in data["application_capabilities"]
        snapshot = client.get("/api/v1/setup/onboarding", headers=headers).json()
        assert snapshot["host_name"] is None

        monkeypatch.setenv("ROW_BOT_HOSTED_BY", "pikapods")
        data, headers = bootstrap(client)
        assert "hosted" in data["application_capabilities"]
        snapshot = client.get("/api/v1/setup/onboarding", headers=headers).json()
        assert snapshot["host_name"] == "PikaPods"
        assert [step["id"] for step in snapshot["steps"]][:2] == ["models", "phone"]
        command = {"command_id": str(uuid4()), "expected_revision": snapshot["revision"],
                   "action": "dismiss_phone_card"}
        sent = client.post("/api/v1/setup/onboarding/commands", json=command,
                           headers={**headers, "Idempotency-Key": command["command_id"]})
        assert sent.status_code == 200, sent.text
        assert sent.json()["snapshot"]["dismissed_phone_card"] is True
