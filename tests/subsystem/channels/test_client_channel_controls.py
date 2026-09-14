"""Client channel controls remain passive, redacted and one-shot."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest

from row_bot.application import channel_controls as controls
from row_bot.channels.base import ChannelCapabilities, ConfigField
from row_bot.runtime import admissions
from tests.fixtures.channels import FakeChannel


pytestmark = pytest.mark.subsystem


class ConfigOwner:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], object] = {}
        self.writes: list[tuple[str, str, object]] = []

    def get(self, channel: str, key: str, default=None):
        return self.values.get((channel, key), default)

    def set(self, channel: str, key: str, value) -> None:
        self.writes.append((channel, key, value))
        self.values[(channel, key)] = value


class SecretOwner:
    def __init__(self) -> None:
        self.values = {("slack", "SLACK_TOKEN"): "synthetic-private-token"}
        self.writes: list[tuple[str, str, str | None]] = []

    def channel_secret_status(self, channel: str, env_key: str) -> dict:
        value = self.values.get((channel, env_key), "")
        return {
            "configured": bool(value),
            "source": "channel keyring" if value else "",
            "fingerprint": "fp:synthetic" if value else "",
        }

    def set_channel_secret(self, channel: str, env_key: str, value: str) -> None:
        self.writes.append((channel, env_key, value))
        self.values[(channel, env_key)] = value

    def delete_channel_secret(self, channel: str, env_key: str) -> None:
        self.writes.append((channel, env_key, None))
        self.values.pop((channel, env_key), None)


class AuthOwner:
    def __init__(self) -> None:
        self.users = {"slack": ["private-user-123456"]}
        self.names = {"slack": {"private-user-123456": "Synthetic person"}}
        self.pair_calls = 0
        self.revoke_calls: list[tuple[str, str]] = []

    def get_approved_users(self, channel: str) -> list[str]:
        return list(self.users.get(channel, []))

    def get_user_names(self, channel: str) -> dict[str, str]:
        return dict(self.names.get(channel, {}))

    def generate_pairing_code(self, channel: str) -> str:
        self.pair_calls += 1
        return "PAIR1234"

    def revoke_user(self, channel: str, user_id: str) -> bool:
        self.revoke_calls.append((channel, user_id))
        if user_id not in self.users.get(channel, []):
            return False
        self.users[channel].remove(user_id)
        return True


class SlackChannel(FakeChannel):
    def __init__(self) -> None:
        super().__init__(
            name="slack",
            display_name="Synthetic Slack",
            capabilities=ChannelCapabilities(streaming=True, buttons=True),
        )
        self.start_calls = 0
        self.stop_calls = 0
        self.fail_after_start = False

    @property
    def config_fields(self) -> list[ConfigField]:
        return [
            ConfigField(
                key="bot_token",
                label="Bot token",
                field_type="password",
                storage="env",
                env_key="SLACK_TOKEN",
            )
        ]

    async def start(self) -> bool:
        self.start_calls += 1
        self._running = True
        if self.fail_after_start:
            raise ConnectionError("synthetic response loss")
        return True

    async def stop(self) -> None:
        self.stop_calls += 1
        self._running = False


@dataclass(frozen=True)
class Source:
    kind: str = "core"
    plugin_id: str = ""
    label: str = ""


class RegistryOwner:
    def __init__(self, channel: SlackChannel, *, source: Source = Source()) -> None:
        self.channel = channel
        self.source = source
        self.cache_clears = 0
        self.deliver_calls = 0

    def get(self, name: str):
        return self.channel if name == self.channel.name else None

    def all_channels(self):
        return [self.channel]

    def get_source(self, _name: str) -> Source:
        return self.source

    def clear_agent_cache_if_loaded(self) -> None:
        self.cache_clears += 1

    def deliver(self, *_args, **_kwargs):
        self.deliver_calls += 1
        raise AssertionError("channel controls must never deliver messages")


class EmptyRegistryOwner:
    def get(self, _name: str):
        return None

    def all_channels(self):
        return []

    def get_source(self, _name: str) -> Source:
        return Source()


@pytest.fixture
def environment(tmp_path, monkeypatch):
    from row_bot import tasks

    monkeypatch.setattr(tasks, "_DB_PATH", tmp_path / "tasks.db")
    monkeypatch.setattr(tasks, "_SCHEMA_READY_PATH", None)
    admissions.instance_identity()
    channel = SlackChannel()
    owners = {
        "registry_owner": RegistryOwner(channel),
        "config_owner": ConfigOwner(),
        "auth_owner": AuthOwner(),
        "secret_owner": SecretOwner(),
    }
    return channel, owners


def snapshot(environment):
    channel, owners = environment
    return controls.read_channel_status(channel.name, validate=lambda: None, **owners)


def test_cold_import_does_not_create_channel_configuration(tmp_path) -> None:
    data_dir = tmp_path / "absent"
    script = """
import os, sys
from pathlib import Path
os.environ['ROW_BOT_DATA_DIR'] = sys.argv[1]
import row_bot.application.channel_controls
assert not Path(sys.argv[1]).exists()
assert 'row_bot.channels.config' not in sys.modules
assert 'row_bot.channels.auth' not in sys.modules
"""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(__file__).parents[3] / "src")
    result = subprocess.run(
        [sys.executable, "-c", script, str(data_dir)],
        capture_output=True,
        text=True,
        timeout=15,
        env=environment,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def command(environment, operation: str, **updates):
    state = snapshot(environment)
    payload = {
        "channel_id": "slack",
        "revision": state["revision"],
        "operation": operation,
        "field_key": None,
        "value": None,
        "identity_id": None,
        **updates,
    }
    review = controls.review_channel_command(
        payload["channel_id"],
        payload["revision"],
        payload["operation"],
        field_key=payload["field_key"],
        value=payload["value"],
        identity_id=payload["identity_id"],
        validate=lambda: None,
        **environment[1],
    )
    return {
        "command_id": str(uuid4()),
        "type": "channel.control",
        "payload": payload,
    }, review


async def execute(environment, value, review):
    return await controls.execute_channel_command(
        owner_id="synthetic-owner",
        key=value["command_id"],
        command=value,
        validate=lambda: None,
        validate_review=lambda actual: (
            actual == review or pytest.fail("review changed")
        ),
        **environment[1],
    )


def test_passive_status_is_bounded_redacted_and_never_delivers(environment) -> None:
    channel, owners = environment

    page = controls.read_channels(validate=lambda: None, **owners)

    assert page["total"] == 1 and page["truncated"] is False
    item = page["items"][0]
    assert item["running"] is False
    assert item["activity"] in {"none", "unknown"}
    assert item["activity_history"] == []
    assert item["availability"]["monitor"] in {"available", "unavailable"}
    assert item["fields"][0] == {
        "key": "bot_token",
        "label": "Bot token",
        "field_type": "password",
        "storage": "env",
        "help_text": "",
        "configured": True,
        "source": "channel keyring",
        "fingerprint": "fp:synthetic",
        "externally_managed": False,
        "writable": True,
    }
    wire = json.dumps(page)
    assert "synthetic-private-token" not in wire
    assert "private-user-123456" not in wire
    assert item["paired_identities"][0]["hint"] == "…3456"
    assert "Synthetic person" in wire
    assert channel.start_calls == 0 and channel.stop_calls == 0
    assert owners["registry_owner"].deliver_calls == 0
    assert owners["config_owner"].writes == []
    assert owners["secret_owner"].writes == []


def test_empty_default_registry_projects_five_passive_core_channels(
    monkeypatch,
) -> None:
    registry = EmptyRegistryOwner()

    class PassiveSecrets:
        def channel_secret_status(self, channel: str, env_key: str) -> dict:
            configured = channel == "telegram" and env_key in {
                "TELEGRAM_BOT_TOKEN",
                "TELEGRAM_USER_ID",
            }
            return {
                "configured": configured,
                "source": "channel keyring" if configured else "",
                "fingerprint": "masked" if configured else "",
            }

    monkeypatch.setattr(controls, "_registry", lambda _owner: registry)
    monkeypatch.setattr(controls, "_passive_secret_owner", PassiveSecrets)

    page = controls.read_channels(validate=lambda: None)

    assert page["total"] == 5
    assert {item["channel_id"] for item in page["items"]} == {
        "telegram",
        "slack",
        "sms",
        "discord",
        "whatsapp",
    }
    telegram = next(item for item in page["items"] if item["channel_id"] == "telegram")
    assert telegram["configured"] is True
    assert telegram["running"] is False
    assert telegram["activity"] == "unknown"
    assert telegram["availability"] == {
        "configuration": "limited",
        "lifecycle": "configuration_required",
        "pairing": "unsupported",
        "monitor": "unavailable",
    }
    assert all(field["writable"] is False for field in telegram["fields"])
    assert "TELEGRAM_BOT_TOKEN" not in json.dumps(page)

    injected = controls.read_channels(
        registry_owner=registry,
        secret_owner=PassiveSecrets(),
        validate=lambda: None,
    )
    assert injected["total"] == 0


def test_review_is_passive_and_one_field_configuration_is_write_only(
    environment,
) -> None:
    channel, owners = environment
    value, review = command(
        environment,
        "configure",
        field_key="bot_token",
        value="replacement-private-token",
    )
    assert owners["secret_owner"].writes == []
    assert "replacement-private-token" not in json.dumps(review)

    result = asyncio.run(execute(environment, value, review))

    assert result["status"] == "completed"
    assert result["operation"] == "configure"
    assert "replacement-private-token" not in json.dumps(result)
    assert owners["secret_owner"].writes == [
        ("slack", "SLACK_TOKEN", "replacement-private-token")
    ]
    assert channel.messages == []


def test_start_transport_loss_is_uncertain_and_original_never_replays(
    environment,
) -> None:
    channel, owners = environment
    channel.fail_after_start = True
    value, review = command(environment, "start")

    first = asyncio.run(execute(environment, value, review))
    second = asyncio.run(execute(environment, value, review))

    assert first["status"] == second["status"] == "partial"
    assert first["code"] == "channel_operation_unconfirmed"
    assert channel.start_calls == 1
    assert channel.is_running() is True
    assert owners["registry_owner"].deliver_calls == 0


def test_successful_start_and_stop_delegate_once_and_update_autostart(
    environment,
) -> None:
    channel, owners = environment
    start, start_review = command(environment, "start")
    started = asyncio.run(execute(environment, start, start_review))
    assert started["status"] == "completed" and started["channel"]["running"] is True
    assert channel.start_calls == 1
    assert owners["config_owner"].writes == [("slack", "auto_start", True)]

    stop, stop_review = command(environment, "stop")
    stopped = asyncio.run(execute(environment, stop, stop_review))
    assert stopped["status"] == "completed" and stopped["channel"]["running"] is False
    assert channel.stop_calls == 1
    assert owners["config_owner"].writes[-1] == ("slack", "auto_start", False)
    assert owners["registry_owner"].deliver_calls == 0


def test_pair_and_revoke_use_canonical_auth_without_exposing_raw_identity(
    environment,
) -> None:
    _channel, owners = environment
    pairing, pairing_review = command(environment, "pair")
    paired = asyncio.run(execute(environment, pairing, pairing_review))
    assert paired["pairing_code"] == "PAIR1234"
    assert owners["auth_owner"].pair_calls == 1

    identity_id = snapshot(environment)["paired_identities"][0]["identity_id"]
    revoke, revoke_review = command(environment, "revoke", identity_id=identity_id)
    revoked = asyncio.run(execute(environment, revoke, revoke_review))
    assert revoked["status"] == "completed"
    assert owners["auth_owner"].revoke_calls == [("slack", "private-user-123456")]
    assert revoked["channel"]["paired_identities"] == []


def test_plugin_pairing_is_explicitly_unavailable(environment) -> None:
    channel, owners = environment
    owners["registry_owner"] = RegistryOwner(
        channel, source=Source(kind="plugin", plugin_id="synthetic-plugin")
    )
    state = snapshot((channel, owners))
    assert state["availability"]["pairing"] == "unavailable"
    with pytest.raises(controls.ChannelControlError, match="action_unavailable"):
        command((channel, owners), "pair")


def test_receipts_are_scoped_and_passive(environment) -> None:
    value, review = command(environment, "configure", field_key="bot_token", value=None)
    result = asyncio.run(execute(environment, value, review))
    assert (
        controls.read_channel_receipt(
            "slack",
            owner_id="synthetic-owner",
            command_id=value["command_id"],
            validate=lambda: None,
        )
        == result
    )
    with pytest.raises(
        controls.ChannelControlError, match="channel_operation_unavailable"
    ):
        controls.read_channel_receipt(
            "discord",
            owner_id="synthetic-owner",
            command_id=value["command_id"],
            validate=lambda: None,
        )


def test_configuration_rejects_delivery_fields_and_preserves_delivery_semantics(
    environment,
) -> None:
    value, review = command(environment, "start")
    value["payload"]["delivery_target"] = ""
    with pytest.raises(controls.ChannelControlError, match="invalid_command"):
        asyncio.run(execute(environment, value, review))
    assert environment[1]["registry_owner"].deliver_calls == 0


def test_pages_and_owner_enumeration_fail_closed_when_oversized(environment) -> None:
    channel, owners = environment
    owners["registry_owner"].all_channels = lambda: [channel] * 129
    with pytest.raises(
        controls.ChannelControlError, match="channel_status_unavailable"
    ):
        controls.read_channels(validate=lambda: None, **owners)
    owners["registry_owner"].all_channels = lambda: [channel]
    with pytest.raises(controls.ChannelControlError, match="invalid_limit"):
        controls.read_channels(limit=51, validate=lambda: None, **owners)
