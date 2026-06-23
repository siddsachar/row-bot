from __future__ import annotations

import asyncio

import pytest

from tests.fixtures.channels import FakeChannel, approval_payload


pytestmark = pytest.mark.contract


def test_fake_channel_lifecycle_and_message_contract() -> None:
    channel = FakeChannel()

    assert channel.is_configured() is True
    assert channel.is_running() is False
    assert asyncio.run(channel.start()) is True
    assert channel.is_running() is True

    channel.send_message("target", "hello")
    channel.send_photo("target", "image.png", "caption")
    channel.send_document("target", "file.txt", None)

    assert channel.messages[0].text == "hello"
    assert channel.photos == [("target", "image.png", "caption")]
    assert channel.documents == [("target", "file.txt", None)]

    asyncio.run(channel.stop())
    assert channel.is_running() is False


def test_fake_channel_approval_contract_records_resolution() -> None:
    channel = FakeChannel(name="approval-fake")
    asyncio.run(channel.start())

    ref = channel.send_approval_request(
        "target",
        {"tool": "dangerous_tool"},
        {"task_name": "Contract", "resume_token": "resume-1", "message": "Approve?"},
    )
    channel.update_approval_message(ref or "", "approved", source="web")

    assert ref == "approval-fake-approval-1"
    assert channel.approvals[0]["config"]["resume_token"] == "resume-1"
    assert channel.approval_updates == [("approval-fake-approval-1", "approved", "web")]
    assert approval_payload("resume-1", True) == {"resume_token": "resume-1", "approved": True, "source": "fake"}


def test_channel_base_thread_id_contract() -> None:
    channel = FakeChannel(name="slack")

    assert channel.make_thread_id("abc123") == "slack_abc123"
    assert channel.get_default_target() == "fake-user"
    assert channel.config_fields[0].key == "fake_target"
