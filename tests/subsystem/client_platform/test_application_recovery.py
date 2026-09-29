"""Durable admission and approval recovery through the production owners."""
from __future__ import annotations

import base64
import json
import threading
from concurrent.futures import ThreadPoolExecutor

from langchain_core.messages import AIMessage, HumanMessage
import pytest

from tests.contracts.client_platform.test_headless_lifecycle import command, platform, submit  # noqa: F401
from tests.helpers.client_platform_fakes import CheckpointCommit, ScriptedAgentStream, StreamBarrier, fixture_id


def test_chat_approval_uses_durable_claim_and_exact_resume(platform):
    identity = fixture_id("approved-native")
    fake = ScriptedAgentStream((("interrupt", {
                                   "__interrupt_id": "native-interrupt",
                                   "tool": "fixture_tool",
                                   "description": "Synthetic approval",
                                   "risk_class": "low",
                                   "scope": "One synthetic action.",
                                   "args": {"limit": 3, "path": "PRIVATE/path"},
                               }),),
                               (("token", "Approved result"), CheckpointCommit((AIMessage(content="Approved result", id=identity),), identity),
                                ("done", "Approved result")))
    accepted = submit(platform, fake, "approval")
    first = platform.registry.get(accepted["execution_id"])
    assert first.producer_done.wait(10)
    assert first.status == "waiting_approval"
    approval = platform.get_approval(first.approval_id)
    assert approval["action_digest"] != first.approval_id
    assert approval["action_label"] == "fixture_tool"
    assert approval["reason"] == "Synthetic approval"
    assert approval["risk_class"] == "low"
    assert approval["scope"] == "One synthetic action."
    assert approval["safe_argument_summary"] == '{"limit":3}'
    assert approval["requesting_trace_id"] == "native-interrupt"
    assert "PRIVATE" not in json.dumps(approval)
    receipt = platform.execute(owner_id="owner", idempotency_key="resolve-once", target=first.approval_id,
                               command=command("approval.resolve", "resolve-once", {"decision": "approve"}))
    resumed = platform.registry.get(receipt["execution_id"])
    assert resumed.producer_done.wait(10)
    assert resumed.status == "completed"
    assert platform.get_approval(first.approval_id)["status"] == "approved"
    assert len(fake.calls) == 2
    assert fake.calls[-1]["approved"] is True
    assert fake.calls[-1]["interrupt_ids"] == ("native-interrupt",)


def test_model_is_frozen_at_the_approval_for_a_cross_client_resume(platform, monkeypatch):
    import sqlite3
    from row_bot import models, threads
    from row_bot.providers.selection import model_ref
    original = model_ref("fixture", "original-model")
    replacement = model_ref("fixture", "replacement-model")
    monkeypatch.setattr(models, "get_current_model", lambda: original)
    identity = fixture_id("frozen-model-native")
    fake = ScriptedAgentStream(
        (("interrupt", {"__interrupt_id": "frozen-native-interrupt", "description": "Synthetic approval"}),),
        (CheckpointCommit((AIMessage(content="Complete", id=identity),), identity), ("done", "Complete")))
    calls = []

    def resume(tools, resumed_config, approved, **kwargs):
        calls.append(resumed_config["configurable"]["model_override"])
        yield from fake.resume(tools, resumed_config, approved, **kwargs)

    platform.stream_factory = fake.stream
    platform.resume_factory = resume
    accepted = platform.execute(owner_id="first-client", idempotency_key=fixture_id("frozen-model:key"),
                                target="conversation-a", command=command("conversation.submit", "frozen-model", {
                                    "submission_id": fixture_id("frozen-model"), "text": "Synthetic input",
                                    "attachment_refs": [],
                                    "model_selection": {"provider_id": "fixture", "model_ref": original}}))
    first = platform.registry.get(accepted["execution_id"])
    assert first.producer_done.wait(10) and first.status == "waiting_approval"
    assert platform.pending_approval_model_ref(first.approval_id, "conversation-a") == original
    # The default and the conversation's own selection move on before a second client approves.
    monkeypatch.setattr(models, "get_current_model", lambda: replacement)
    with sqlite3.connect(threads.DB_PATH) as connection:
        connection.execute("UPDATE thread_meta SET model_override=? WHERE thread_id='conversation-a'", (replacement,))
    receipt = platform.execute(owner_id="second-client", idempotency_key="frozen-approval", target=first.approval_id,
                               command=command("approval.resolve", "frozen-approval", {"decision": "approve"}))
    resumed = platform.registry.get(receipt["execution_id"])
    assert resumed.producer_done.wait(10) and resumed.status == "completed"
    assert calls == [original] and len(fake.calls) == 2
    assert fake.calls[-1]["interrupt_ids"] == ("frozen-native-interrupt",)


def test_prepared_admission_recovery_never_duplicates_or_dispatches(platform):
    from row_bot.runtime import admissions
    from row_bot.threads import append_checkpoint_messages, get_latest_checkpoint_messages
    missing = admissions.reserve("conversation-a", "missing-input", "generation-missing")
    matching = admissions.reserve("conversation-b", "present-input", "generation-present")
    assert append_checkpoint_messages("conversation-b", [HumanMessage(content="Synthetic", id="present-input")])
    admissions.recover("new-process")
    with admissions.transaction() as connection:
        states = {row["pass_id"]: row["state"] for row in connection.execute("SELECT pass_id,state FROM generation_passes")}
    assert states[missing["pass_id"]] == "cancelled"
    assert states[matching["pass_id"]] == "interrupted"
    assert [message.id for message in get_latest_checkpoint_messages("conversation-b")] == ["present-input"]
    assert admissions.reserve("conversation-a", "new-input", "new-generation")
    assert not platform.registry.active()


def test_lazy_content_pages_preserve_all_ordered_unicode_blocks(platform):
    from row_bot.threads import append_checkpoint_messages
    content = [{"type": "text", "text": "Synthetic 😀\n" * 35000}, {"type": "text", "text": " exact tail "}]
    identity = fixture_id("large-content")
    assert append_checkpoint_messages("conversation-a", [AIMessage(content=content, id=identity)])
    row = platform.transcript("conversation-a")["rows"][0]
    assert row["content_ref"] == identity
    chunks, cursor = [], None
    while True:
        page = platform.lazy_content("conversation-a", identity, limit_bytes=17003, cursor=cursor)
        chunk = base64.b64decode(page["data"])
        assert len(chunk) <= 17003
        chunks.append(chunk)
        if not page["has_more"]:
            break
        cursor = page["next_cursor"]
    assert json.loads(b"".join(chunks)) == content


def test_delete_closes_admission_before_uninterruptible_stop(platform):
    from row_bot.application.client_platform import ClientPlatformError
    from row_bot.runtime import admissions
    barrier = StreamBarrier()
    fake = ScriptedAgentStream((barrier,))
    accepted = submit(platform, fake, "delete-live")
    handle = platform.registry.get(accepted["execution_id"])
    try:
        assert barrier.entered.wait(10)
        result = platform.execute(owner_id="owner", idempotency_key="delete", target="conversation-a",
                                  command=command("conversation.delete", "delete"))
        assert result["status"] == "DeleteBlocked"
        assert admissions.deletion_state("conversation-a") == "admission_closed"
    finally:
        barrier.release.set()
        assert handle.producer_done.wait(10)
    with pytest.raises(ClientPlatformError, match="conversation_deleting"):
        submit(platform, ScriptedAgentStream(()), "after-delete")


def test_terminal_publication_precedes_quiescence_and_next_admission(platform, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = platform.projection.publish
    def publish(conversation_id, kind, payload, **kwargs):
        if kind == "generation.state" and payload["status"] == "completed":
            entered.set()
            assert release.wait(10)
        return original(conversation_id, kind, payload, **kwargs)
    monkeypatch.setattr(platform.projection, "publish", publish)
    identity = fixture_id("terminal-native")
    first = submit(platform, ScriptedAgentStream((CheckpointCommit((AIMessage(content="Result", id=identity),), identity),
                                                 ("done", "Result"))), "terminal-first")
    handle = platform.registry.get(first["execution_id"])
    assert entered.wait(10)
    try:
        assert not handle.producer_done.is_set()
        assert handle in platform.registry.active()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(submit, platform, ScriptedAgentStream(()), "terminal-second")
            assert not future.done()
            release.set()
            second = future.result(10)
            assert platform.registry.get(second["execution_id"]).producer_done.wait(10)
    finally:
        release.set()


def test_shutdown_fences_new_workers_until_new_registry():
    from row_bot.runtime.executions import GenerationRuntimeRegistry
    registry = GenerationRuntimeRegistry()
    handle = registry.register("conversation")
    registry.shutdown()
    assert handle in registry.active()
    with pytest.raises(ValueError, match="runtime_closed"):
        registry.register("another-conversation")
