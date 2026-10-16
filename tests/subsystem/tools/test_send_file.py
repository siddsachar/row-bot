"""send_file (F7): files from a chat to the person's own devices or to a chat app.

Only named files leave: this chat's files and the workspace, through the media guards. Sending to
the devices never asks; sending to a chat app goes through the approval gate like a message, with a
reviewer that is a fake and a channel that is the in-memory fake adapter (nothing real is sent).
"""

from __future__ import annotations

import os
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.fixtures.channels import FakeChannel

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

PDF = b"%PDF-1.7\n" + b"x" * 2048
MB = 1024 * 1024


@pytest.fixture
def chat(tmp_path, monkeypatch, reload_for_data_dir):
    """A chat with report.pdf in its files, a workspace with docs/notes.txt, and a fake Telegram."""
    threads, _tasks = reload_for_data_dir(tmp_path / "data", "row_bot.threads", "row_bot.tasks")
    from row_bot import approval_review, file_deliveries
    from row_bot.application import app_notices
    from row_bot.application.attachments import register_attachment
    from row_bot.channels import registry as channels
    from row_bot.channels.base import ChannelCapabilities
    from row_bot.tools import approval_gate, registry
    from row_bot.tools import file_delivery_tool

    conversation = threads.create_thread("Quarterly report", seed_default_skills=False)
    register_attachment(conversation, "report.pdf", PDF)
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "notes.txt").write_bytes(b"Notes for the meeting.\n")
    registry.set_tool_config("filesystem", "workspace_root", str(workspace))
    monkeypatch.setattr(file_deliveries, "_RECORDS", {})
    monkeypatch.setattr(file_deliveries, "_LOADED_ROOT", None)
    monkeypatch.setattr(file_delivery_tool, "_REPLY_COUNTS", OrderedDict())
    journal = app_notices.NoticeJournal()
    monkeypatch.setattr(app_notices, "app_notices", journal)

    telegram = FakeChannel(name="telegram", display_name="Telegram", default_target=4242)
    telegram._running = True
    sms = FakeChannel(name="sms", display_name="SMS", default_target="+15550100",
                      capabilities=ChannelCapabilities(document_out=False))
    sms._running = True
    monkeypatch.setattr(channels, "_channels", {"telegram": telegram, "sms": sms})
    monkeypatch.setattr(channels, "_channel_sources", {})

    state = SimpleNamespace(conversation=conversation, workspace=workspace, telegram=telegram, sms=sms,
                            notices=journal, asked=[], answer=True, mode="approve", reviews=[],
                            verdict=approval_review.ReviewVerdict("approve", "You asked to send it to Telegram."),
                            data=tmp_path / "data", threads=threads)
    monkeypatch.setattr("row_bot.tool_outputs.current_thread_id", lambda: state.conversation)
    monkeypatch.setattr(approval_gate, "current_approval_mode", lambda: state.mode)
    monkeypatch.setattr(approval_gate, "_interrupt", lambda payload: state.asked.append(payload) or state.answer)

    def review(payload, *, risks=(), **_kwargs):
        state.reviews.append({"payload": payload, "risks": tuple(risks)})
        return state.verdict

    monkeypatch.setattr(approval_review, "review", review)
    state.send = lambda **kwargs: file_delivery_tool._send_file(**kwargs)
    state.deliveries = file_deliveries
    yield state


def _delivery(result: str):
    from row_bot import file_deliveries

    line = result.splitlines()[1] if result.startswith("Approval:") else result.splitlines()[0]
    assert line.startswith("Delivery send-"), result
    return file_deliveries.get(line.split(":", 1)[0].removeprefix("Delivery "))


# ── Which files may leave ────────────────────────────────────────────────────


def test_only_files_in_the_chat_or_the_workspace_can_be_sent(chat, tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("not for sending", encoding="utf-8")
    private = chat.data / "api_keys.json"
    private.write_text("{}", encoding="utf-8")

    for name in (str(outside), "../secret.txt", "docs/../../secret.txt", str(private)):
        result = chat.send(files=[name], to="telegram")
        assert not result.startswith("Delivery"), (name, result)
        assert "outside the workspace" in result or "isn't a file in the workspace" in result, (name, result)
    assert "Couldn't find missing.pdf" in chat.send(files=["missing.pdf"])
    assert chat.telegram.documents == [] and chat.asked == [] and chat.reviews == []
    assert chat.deliveries.list_deliveries() == []


def test_a_link_inside_the_workspace_is_never_followed(chat, tmp_path):
    target = tmp_path / "elsewhere.txt"
    target.write_text("outside", encoding="utf-8")
    try:
        os.symlink(target, chat.workspace / "docs" / "linked.txt")
    except (OSError, NotImplementedError):
        pytest.skip("Symbolic links can't be made here")
    result = chat.send(files=["docs/linked.txt"])
    assert "links" in result and not result.startswith("Approval:")
    assert chat.deliveries.list_deliveries() == []


# ── To the person's devices ─────────────────────────────────────────────────


@pytest.mark.parametrize("level", ["block", "approve", "auto", "full"])
def test_sending_to_your_devices_never_asks_and_shows_in_every_open_window(chat, level):
    chat.mode = level
    result = chat.send(files=["report.pdf", "docs/notes.txt"], to="devices")

    assert result.startswith("Approval: not needed (your own devices)\nDelivery send-")
    assert "sent 2 files (report.pdf, notes.txt) to the person's devices" in result
    assert chat.asked == [] and chat.reviews == []
    delivery = _delivery(result)
    view = chat.deliveries.view(delivery)
    assert (view["target"], view["status"], view["approval"]) == ("devices", "sent", "not_needed")
    assert [(item["name"], item["downloadable"]) for item in view["files"]] == [
        ("report.pdf", True), ("notes.txt", True)]
    assert view["expires_at"] is not None
    name, size, handle = chat.deliveries.open_file(delivery.delivery_id, 0)
    with handle:
        assert (name, size, handle.read()) == ("report.pdf", len(PDF), PDF)
    [notice] = chat.notices.since(0)
    assert (notice.title, notice.source, notice.requested) == ("Row-Bot sent you 2 files", "deliveries", True)
    assert notice.view()["conversation_id"] == chat.conversation


def test_a_downloaded_copy_stays_the_file_as_sent(chat):
    result = chat.send(files=["docs/notes.txt"])
    (chat.workspace / "docs" / "notes.txt").write_bytes(b"Changed afterwards.\n")
    _name, _size, handle = chat.deliveries.open_file(_delivery(result).delivery_id, 0)
    with handle:
        assert handle.read() == b"Notes for the meeting.\n"


def test_the_download_ends_when_its_copy_expires(chat, monkeypatch):
    from row_bot.file_deliveries import DEVICE_KEEP_SECONDS, DeliveryError

    delivery = _delivery(chat.send(files=["report.pdf"]))
    folder = chat.deliveries._files_folder(delivery)
    assert folder.is_dir()
    later = delivery.created_at + DEVICE_KEEP_SECONDS + 1
    monkeypatch.setattr(chat.deliveries, "_now", lambda: later)

    with pytest.raises(DeliveryError) as refused:
        chat.deliveries.open_file(delivery.delivery_id, 0)
    assert refused.value.code == "sent_file_expired"
    assert not folder.exists()  # The copy is gone, not just hidden.
    view = chat.deliveries.view(delivery)
    assert view["status"] == "expired" and not view["files"][0]["downloadable"]


def test_start_up_removes_expired_copies(chat, monkeypatch):
    from row_bot.file_deliveries import DEVICE_KEEP_SECONDS

    old = _delivery(chat.send(files=["report.pdf"]))
    monkeypatch.setattr(chat.deliveries, "_now", lambda: old.created_at + DEVICE_KEEP_SECONDS + 1)
    assert chat.deliveries.sweep() == 1
    assert not chat.deliveries._files_folder(old).exists()
    assert chat.deliveries.get(old.delivery_id) is not None  # Its card still says it expired.


def test_a_file_too_big_for_your_devices_is_not_kept(chat, monkeypatch):
    monkeypatch.setattr(chat.deliveries, "DEVICE_FILE_LIMIT", 1024)
    result = chat.send(files=["report.pdf"])
    delivery = _delivery(result)
    assert (delivery.status, delivery.problem) == ("failed", "too_large")
    assert "too big to send to your devices (1 KB limit)" in result
    assert not chat.deliveries._files_folder(delivery).exists()
    assert chat.notices.since(0) == []


def test_deleting_the_conversation_removes_its_sent_files(chat):
    from row_bot.thread_cleanup import _purge_thread_files

    delivery = _delivery(chat.send(files=["report.pdf"]))
    folder = chat.deliveries._files_folder(delivery)
    assert folder.exists()
    _purge_thread_files(chat.conversation)
    assert not folder.exists() and chat.deliveries.get(delivery.delivery_id) is None


# ── To a chat app ───────────────────────────────────────────────────────────


def test_under_ask_the_person_approves_a_chat_app_send_and_the_adapter_sends_it(chat):
    result = chat.send(files=["report.pdf"], to="telegram", caption="Here's the report")

    [asked] = chat.asked
    assert asked["question"] == "Send report.pdf to your Telegram chat?"
    assert "report.pdf (2 KB), from this chat's files, to your Telegram chat" in asked["description"]
    assert asked["tool"] == "send_file" and chat.reviews == []
    [(target, path, caption)] = chat.telegram.documents
    assert (target, Path(path).name, caption) == (4242, "report.pdf", "Here's the report")
    assert not Path(path).exists()  # The copy it sent from is gone.
    assert result.startswith("Approval: asked; approved by you\nDelivery send-")
    view = chat.deliveries.view(_delivery(result))
    assert (view["status"], view["place"], view["approval"], view["expires_at"]) == (
        "sent", "your Telegram chat", "you", None)
    assert not any(item["downloadable"] for item in view["files"])


def test_a_denied_send_sends_nothing(chat):
    chat.answer = False
    result = chat.send(files=["report.pdf"], to="telegram")
    assert "didn't allow sending report.pdf to your Telegram chat; nothing was sent" in result
    assert chat.telegram.documents == [] and chat.deliveries.list_deliveries() == []


def test_block_refuses_a_chat_app_send_without_asking(chat):
    chat.mode = "block"
    result = chat.send(files=["report.pdf"], to="telegram")
    assert result.startswith("BLOCKED: Sending files to Telegram needs approval")
    assert chat.asked == [] and chat.telegram.documents == []


def test_auto_s_reviewer_approves_a_clear_request_and_asks_otherwise(chat):
    chat.mode = "auto"
    approved = chat.send(files=["docs/notes.txt"], to="telegram")
    assert approved.startswith("Approval: approved by the reviewer: You asked to send it to Telegram\n")
    assert chat.asked == [] and len(chat.telegram.documents) == 1
    [review] = chat.reviews
    assert review["risks"] == ("sends files out of Row-Bot to Telegram", "includes files from the workspace")
    assert chat.deliveries.view(_delivery(approved))["approval"] == "reviewer"

    chat.verdict = type(chat.verdict)("ask", "You didn't say to send it anywhere.")
    asked = chat.send(files=["report.pdf"], to="telegram")
    [card] = chat.asked
    assert card["review"] == {"decision": "ask", "reason": "You didn't say to send it anywhere."}
    assert asked.startswith("Approval: asked; approved by you. The reviewer asked you:")
    assert len(chat.telegram.documents) == 2


def test_hands_off_sends_without_asking(chat):
    chat.mode = "full"
    result = chat.send(files=["report.pdf"], to="telegram")
    assert result.startswith("Approval: allowed by Hands-off\nDelivery send-")
    assert chat.asked == [] and chat.reviews == [] and len(chat.telegram.documents) == 1


def test_a_shared_conversation_never_sends_on_hands_off(chat):
    from row_bot.tasks import record_thread_channel_ref

    record_thread_channel_ref(chat.conversation, channel="telegram", target="-100200300",
                              external_conversation_id="-100200300", shared=True)
    chat.mode = "full"
    chat.verdict = type(chat.verdict)("ask", "Others in this group would see it.")
    result = chat.send(files=["report.pdf"], to="telegram")

    [review] = chat.reviews  # Auto at most: the reviewer, then the person.
    assert "other people in this Telegram group will see them" in review["risks"]
    [card] = chat.asked
    assert card["question"] == "Send report.pdf to this Telegram group?"
    assert "Everyone in this Telegram group will be able to open it." in card["description"]
    assert card["risk_class"] == "high"
    assert not result.startswith("Approval: allowed by Hands-off")
    assert chat.telegram.documents[0][0] == "-100200300"  # This chat, never another.


def test_a_conversation_from_a_chat_app_sends_to_its_own_chat_there(chat):
    from row_bot.tasks import record_thread_channel_ref

    record_thread_channel_ref(chat.conversation, channel="telegram", target="777", shared=False)
    chat.send(files=["report.pdf"], to="telegram")
    assert [target for target, _path, _caption in chat.telegram.documents] == ["777"]


def test_without_a_linked_chat_nothing_is_sent_anywhere(chat):
    chat.telegram._default_target = ""
    result = chat.send(files=["report.pdf"], to="telegram")
    delivery = _delivery(result)
    assert (delivery.status, delivery.problem) == ("failed", "not_linked")
    assert chat.telegram.documents == [] and chat.asked == []


def test_a_file_too_big_for_the_app_is_refused_before_asking_with_a_way_out(chat, monkeypatch):
    monkeypatch.setitem(chat.deliveries.CHANNEL_LIMITS, "telegram", 1024)
    result = chat.send(files=["report.pdf"], to="telegram")
    assert "report.pdf (2 KB) is too big for Telegram (1 KB limit)." in result
    assert "Suggest sending it to their devices instead" in result
    assert chat.asked == [] and chat.telegram.documents == []
    delivery = _delivery(result)
    view = chat.deliveries.view(delivery)
    assert (view["problem"], view["limit_bytes"], view["can_send_to_devices"]) == ("too_large", 1024, True)

    copy = chat.deliveries.send_instead_to_devices(delivery.delivery_id)
    assert (copy.target, copy.status) == ("devices", "sent")
    again = chat.deliveries.view(chat.deliveries.get(delivery.delivery_id))
    assert (again["sent_instead"], again["can_send_to_devices"]) == (copy.delivery_id, False)
    assert chat.deliveries.send_instead_to_devices(delivery.delivery_id).delivery_id == copy.delivery_id


def test_a_chat_app_that_can_t_send_files_says_so(chat):
    result = chat.send(files=["report.pdf"], to="sms")
    assert "SMS can't send files from Row-Bot." in result
    assert chat.deliveries.view(_delivery(result))["problem"] == "cant_send_files"
    chat.telegram._running = False
    stopped = chat.send(files=["report.pdf"], to="telegram")
    assert "Telegram isn't connected right now." in stopped
    assert "no chat app called signal" in chat.send(files=["report.pdf"], to="signal")


def test_a_chat_app_failing_mid_send_is_reported_per_file(chat):
    chat.mode = "full"
    original = chat.telegram.send_document

    def flaky(target, path, caption=None):
        if Path(path).name == "notes.txt":
            raise RuntimeError("Bad Request: chat not found")
        original(target, path, caption)

    chat.telegram.send_document = flaky
    result = chat.send(files=["report.pdf", "docs/notes.txt"], to="telegram")
    delivery = _delivery(result)
    assert (delivery.status, delivery.problem) == ("partly_sent", "send_failed")
    assert delivery.detail == "Telegram didn't take notes.txt."  # Never the app's raw error.
    assert [item.sent for item in delivery.files] == [True, False]
    copy = chat.deliveries.send_instead_to_devices(delivery.delivery_id)
    assert [item.name for item in copy.files] == ["notes.txt"]  # Only what didn't go.


# ── Caps ────────────────────────────────────────────────────────────────────


def test_at_most_ten_files_go_in_one_reply(chat):
    from row_bot.application.attachments import register_attachment

    for index in range(11):
        register_attachment(chat.conversation, f"page-{index}.pdf", PDF)
    eleven = [f"page-{index}.pdf" for index in range(11)]
    assert "At most 10 files can be sent at once" in chat.send(files=eleven)
    assert chat.deliveries.list_deliveries() == []

    chat.send(files=eleven[:6])
    refused = chat.send(files=eleven[6:], to="telegram")
    assert "At most 10 files can be sent in one reply, and 6 already were" in refused
    assert chat.asked == [] and chat.telegram.documents == []
    assert len(chat.deliveries.list_deliveries()) == 1


def test_the_card_follows_a_send_file_step_by_its_delivery():
    from row_bot.application.conversation_traces import _public_specialization, specialize_tool_result

    for content in ("Approval: not needed (your own devices)\nDelivery send-0a1b2c3d: sent report.pdf …",
                    "Approval: asked; approved by you\nDelivery send-0a1b2c3d: sent report.pdf to your Telegram chat.",
                    "Delivery send-0a1b2c3d: not sent to Telegram. Telegram isn't connected right now."):
        public = _public_specialization(specialize_tool_result({"name": "send_file", "content": content}))
        assert (public["kind"], public["delivery_id"]) == ("delivery", "send-0a1b2c3d")
    assert specialize_tool_result({"name": "edit_video", "content": "Delivery send-0a1b2c3d: x"}) is None
    assert specialize_tool_result({"name": "send_file", "content": "Couldn't find report.pdf."}) is None
