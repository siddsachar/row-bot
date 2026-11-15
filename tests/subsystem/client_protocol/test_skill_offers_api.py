""""Save as a skill" through the client API (C1): read a chat's offers, draft one, Not now."""
from __future__ import annotations

import json

import pytest

from tests.helpers.skill_turns import Turn
from tests.subsystem.client_protocol.test_protocol_application import _client, _command, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = pytest.mark.subsystem

STEPS = [
    ("workspace_list_directory", {"path": "Receipts"}),
    ("workspace_read_file", {"path": "Receipts/taxi.pdf"}),
    ("calculate", {"expression": "42.5 + 120"}),
    ("workspace_write_file", {"path": "expenses.md", "content": "| Taxi | 42.50 |"}),
    ("export_to_pdf", {"path": "expenses.md"}),
]
DRAFT = {"name": "monthly_expense_report", "display_name": "Monthly expense report", "icon": "🧾",
         "description": "Builds a month's expense report.", "when_to_use": "When asked for expenses.",
         "inputs": ["the month"], "steps": ["List the receipts with `workspace_list_directory`.",
                                            "Add them up with `calculate`."], "checks": [], "tags": []}


@pytest.fixture
def chat(service, monkeypatch, tmp_path):
    from row_bot import models, skills
    from row_bot.providers import background

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    for name, path in [("USER_SKILLS_DIR", tmp_path / "data" / "skills"), ("BUNDLED_SKILLS_DIR", tmp_path / "b"),
                       ("TOOL_GUIDES_DIR", tmp_path / "g"), ("CONFIG_PATH", tmp_path / "data" / "skills.json")]:
        monkeypatch.setattr(skills, name, path)
    monkeypatch.setattr(skills, "_skills_cache", {})
    monkeypatch.setattr(models, "get_current_model", lambda: "model:openai:gpt-5")
    calls: list[str] = []

    def invoke(purpose, messages, **_kwargs):
        calls.append(purpose)
        return json.dumps(DRAFT)

    monkeypatch.setattr(background, "invoke_background", invoke)
    return calls


def _conversation(client, headers, turn: Turn) -> str:
    from row_bot import threads

    conversation = _command(client, headers, "conversation.create", {"title": "Expenses"}).json()["conversation_id"]
    assert threads.append_checkpoint_messages(conversation, turn.messages)
    return conversation


def test_a_reply_that_used_tools_drafts_a_skill_for_review(service, chat):
    with _client(service) as client:
        _, headers = bootstrap(client)
        turn = Turn("api").ask("Do my monthly expenses", STEPS)
        conversation = _conversation(client, headers, turn)
        base = f"/api/v1/conversations/{conversation}/skill-offers"

        assert client.get(base, headers=headers).json()["items"] == []
        drafted = client.post(f"{base}/draft", headers=headers, json={"message_id": turn.reply_id})

        assert drafted.status_code == 200, drafted.text
        [item] = drafted.json()["items"]
        assert (item["origin"], item["status"], item["message_id"]) == ("manual", "drafted", turn.reply_id)
        assert item["draft"]["display_name"] == "Monthly expense report"
        assert item["draft"]["source_title"] == "Expenses"
        assert client.get(base, headers=headers).json()["items"] == [item]
        assert chat == ["skill_draft"]


def test_drafting_a_reply_without_steps_or_dismissing_a_missing_offer_says_why(service, chat):
    with _client(service) as client:
        _, headers = bootstrap(client)
        turn = Turn("api").ask("What's the capital of France?", [], answer="Paris.")
        conversation = _conversation(client, headers, turn)
        base = f"/api/v1/conversations/{conversation}/skill-offers"

        empty = client.post(f"{base}/draft", headers=headers, json={"message_id": turn.reply_id})
        assert (empty.status_code, empty.json()["code"]) == (409, "skill_draft_no_steps")
        missing = client.post(f"{base}/offer_123/dismiss", headers=headers)
        assert (missing.status_code, missing.json()["code"]) == (404, "skill_offer_missing")
        assert client.get("/api/v1/conversations/nope/skill-offers", headers=headers).status_code == 404
        assert chat == []
