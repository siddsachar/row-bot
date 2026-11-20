"""A paid picture or video's approval card shows its price, roughly (D4): read by its id, as a phone reads it."""
# ruff: noqa: F811 -- reused pytest fixture.
from __future__ import annotations

from uuid import uuid4

import pytest

from tests.subsystem.client_protocol.test_protocol_application import (  # noqa: F401
    ScriptedAgentStream,
    _client,
    _command,
    service,
)
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = pytest.mark.subsystem

PRICE = {"status": "known", "low_usd": 0.053, "high_usd": 0.053, "basis": "OpenAI's list price, checked 15 Nov 2026"}


def _card(service, price):
    fake = ScriptedAgentStream((("interrupt", [{
        "__interrupt_id": "picture-interrupt", "tool": "generate_image", "label": "Make a picture on OpenAI",
        "question": "Make a picture on OpenAI? It costs money on your OpenAI account.",
        "description": 'Row-Bot will make a picture with gpt-image-2 on OpenAI: "a lighthouse".',
        "risk_class": "medium", "args": {"prompt": "a lighthouse", "model": "gpt-image-2", "provider": "OpenAI"},
        "price": price,
    }]),))
    service.stream_factory, service.resume_factory = fake.stream, fake.resume
    with _client(service) as client:
        _, headers = bootstrap(client)
        conversation = _command(client, headers, "conversation.create", {"title": "Lighthouse"}).json()[
            "conversation_id"]
        submitted = _command(client, headers, "conversation.submit", {
            "submission_id": str(uuid4()), "text": "Draw a lighthouse",
            "model_selection": {"provider_id": "fixture", "model_ref": "fixture::model"}}, target=conversation)
        handle = service.registry.get(submitted.json()["execution_id"])
        assert handle.producer_done.wait(5)
        view = client.get(f"/api/v1/approvals/{handle.approval_id}", headers=headers)
    assert view.status_code == 200, view.text
    return view.json()


def test_a_paid_pictures_card_carries_its_price(service):
    view = _card(service, PRICE)

    assert view["price"] == PRICE
    assert view["question"] == "Make a picture on OpenAI? It costs money on your OpenAI account."


def test_a_card_says_when_the_price_is_unknown(service):
    assert _card(service, {"status": "unknown"})["price"] == {"status": "unknown"}


@pytest.mark.parametrize("price", [
    {"status": "known", "low_usd": 0.4, "high_usd": 0.1},
    {"status": "known", "low_usd": -1, "high_usd": 0.1},
    {"status": "known", "low_usd": "0.40", "high_usd": "0.40"},
    {"status": "free"},
    "about $0.40",
])
def test_a_price_that_doesnt_add_up_is_never_shown(service, price):
    assert _card(service, price).get("price") is None
