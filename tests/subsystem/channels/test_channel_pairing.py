"""DM pairing: the authentication for Slack, Discord, WhatsApp and SMS senders."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from row_bot.channels import auth, config as ch_config


pytestmark = pytest.mark.subsystem

_WRONG = "NOT-A-CODE"  # never a generated code: codes are A-Z and 0-9 only


@pytest.fixture
def clock(tmp_path, monkeypatch):
    monkeypatch.setattr(ch_config, "_CONFIG_PATH", tmp_path / "channels_config.json")
    monkeypatch.setattr(auth, "_active_codes", {})
    monkeypatch.setattr(auth, "_fail_trackers", {})
    now = [1_000.0]
    monkeypatch.setattr(auth, "time", SimpleNamespace(time=lambda: now[0]))
    return now


def test_a_pairing_code_is_single_use_and_bound_to_its_channel(clock) -> None:
    code = auth.generate_pairing_code("slack")

    assert auth.verify_pairing_code("discord", "u1", code) is False
    assert auth.verify_pairing_code("slack", "u1", code.lower()) is True
    assert auth.is_user_approved("slack", "u1") is True
    assert auth.is_user_approved("discord", "u1") is False
    assert auth.verify_pairing_code("slack", "u2", code) is False
    assert auth.is_user_approved("slack", "u2") is False


def test_a_revoked_sender_is_no_longer_approved(clock) -> None:
    assert auth.verify_pairing_code("slack", "u1", auth.generate_pairing_code("slack"), display_name="Sam") is True

    assert auth.revoke_user("slack", "u1") is True

    assert auth.is_user_approved("slack", "u1") is False
    assert auth.get_user_names("slack") == {}
    assert auth.revoke_user("slack", "u1") is False


def test_a_code_expires_after_an_hour_and_a_new_code_replaces_the_old(clock) -> None:
    first = auth.generate_pairing_code("slack")
    second = auth.generate_pairing_code("slack")
    assert auth.verify_pairing_code("slack", "u1", first) is False

    clock[0] += 3601
    assert auth.verify_pairing_code("slack", "u1", second) is False

    fresh = auth.generate_pairing_code("slack")
    clock[0] += 3599
    assert auth.verify_pairing_code("slack", "u2", fresh) is True


def test_five_wrong_codes_lock_the_sender_out_for_fifteen_minutes(clock) -> None:
    code = auth.generate_pairing_code("slack")
    for _ in range(5):
        assert auth.verify_pairing_code("slack", "u1", _WRONG) is False

    assert auth.verify_pairing_code("slack", "u1", code) is False  # locked out even with the right code
    clock[0] += 899
    assert auth.verify_pairing_code("slack", "u1", code) is False
    clock[0] += 2
    assert auth.verify_pairing_code("slack", "u1", code) is True
