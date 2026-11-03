"""A turn that stopped still says so after Row-Bot restarts (D6).

A turn's live state is in memory: after a restart, a chat whose last turn failed showed its messages and
nothing else, neither that the turn stopped nor Resume. The turn's own record now restores both, until a
later turn finishes.
"""
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from tests.contracts.client_platform.test_headless_lifecycle import platform, submit  # noqa: F401
from tests.helpers.client_platform_fakes import CheckpointCommit, ScriptedAgentStream, fixture_id

pytestmark = pytest.mark.subsystem


def _restart(platform) -> None:  # noqa: F811
    """What a restart leaves: the saved records, and a projection with nothing in memory."""
    from row_bot.projection.conversation import ConversationProjection

    platform.projection = ConversationProjection(platform.server_epoch)


def test_a_failed_turn_still_offers_resume_after_a_restart(platform):  # noqa: F811
    failed = platform.registry.get(submit(platform, ScriptedAgentStream((("error", "The model stopped answering"),)),
                                          "fails")["execution_id"])
    assert failed.producer_done.wait(10)
    assert platform.snapshot("conversation-a")["generation"]["status"] == "interrupted"

    _restart(platform)

    shown = platform.snapshot("conversation-a")["generation"]
    assert (shown["status"], shown["generation_id"], shown["quiesced"]) == ("interrupted", failed.generation_id, True)

    answer = fixture_id("after-restart-answer")
    later = platform.registry.get(submit(platform, ScriptedAgentStream((
        CheckpointCommit((AIMessage(content="Done.", id=answer),), answer), ("done", "Done."))),
        "works")["execution_id"])
    assert later.producer_done.wait(10)
    _restart(platform)
    assert platform.snapshot("conversation-a")["generation"] is None, "a later turn finished: nothing to resume"
