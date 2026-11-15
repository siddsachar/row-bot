"""The conversations one started or proposed, read over the client API (5.3.1 C)."""

from __future__ import annotations

import pytest

from tests.subsystem.client_protocol.test_protocol_application import _client, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = pytest.mark.subsystem


def test_a_conversations_proposals_are_read_with_their_state(service, monkeypatch):  # noqa: F811
    from row_bot import linked_conversations, threads
    from row_bot.application import client_platform

    monkeypatch.setattr(client_platform, "client_platform_service", service)
    monkeypatch.setattr(linked_conversations, "_SCHEMA_READY", None)
    chat = threads.create_thread("Launch", seed_default_skills=False)
    proposal = linked_conversations._insert(source_id=chat, kind="proposal", status="proposed",
                                            title="Make the demo video", message="Script it.")
    with _client(service) as client:
        _, headers = bootstrap(client)
        page = client.get(f"/api/v1/conversations/{chat}/links", headers=headers)
        missing = client.get("/api/v1/conversations/not-a-chat/links", headers=headers)

    assert page.status_code == 200, page.text
    assert page.json()["items"] == [{"link_id": proposal["id"], "title": "Make the demo video",
                                     "conversation_id": None, "state": "proposed", "proposed": True,
                                     "mode": "fresh"}]
    assert missing.status_code == 404
