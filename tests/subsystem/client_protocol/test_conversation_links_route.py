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


def test_a_proposed_conversations_card_reads_its_state_after_background_reads_use_up_their_budget(
        service, monkeypatch):  # noqa: F811
    """A reply sets off many reads at once; the card still learns it may offer Start (5.3.1 C)."""
    from fastapi.testclient import TestClient

    from row_bot import linked_conversations, threads
    from row_bot.api.v1.routes import create_client_platform_app
    from row_bot.api.v1.security import ClientSecurity, ProtocolError
    from row_bot.application import client_platform
    from tests.helpers.access import person_window

    monkeypatch.setattr(client_platform, "client_platform_service", service)
    monkeypatch.setattr(linked_conversations, "_SCHEMA_READY", None)
    chat = threads.create_thread("Launch", seed_default_skills=False)
    linked_conversations._insert(source_id=chat, kind="proposal", status="proposed",
                                 title="Make the demo video", message="Script it.")
    security = ClientSecurity("fixture-instance", clock=lambda: 10)
    app = create_client_platform_app(service, security=security, choices=lambda: {"models": [], "capabilities": []},
                                     session_authenticator=person_window)
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 12345)) as client:
        handshake, headers = bootstrap(client)
        current = security._sessions[handshake["client_session_id"]]
        with pytest.raises(ProtocolError, match="rate_limited"):
            for _ in range(31):
                security.rate(current, "query")
        page = client.get(f"/api/v1/conversations/{chat}/links", headers=headers)

    assert page.status_code == 200, page.text
    assert page.json()["items"][0]["state"] == "proposed"
