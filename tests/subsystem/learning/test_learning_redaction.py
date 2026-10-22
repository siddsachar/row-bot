"""Secrets never reach Learning's data: not a blob, a record or a log line. Credential arguments
are dropped, secret-shaped text and the secrets this Row-Bot holds are redacted before anything is
written, and what was redacted is marked so."""

from __future__ import annotations

import logging

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from row_bot.learning import redaction
from row_bot.learning.store import LearningStore
from tests.fixtures.learning import all_blob_text, all_record_text, read_json_blob

pytestmark = pytest.mark.subsystem

# Made-up credentials in the shapes the redaction must catch. They are put together at run time so
# the source holds no token-shaped literal (secret scanners rightly refuse those).
OPENAI_KEY = "-".join(("sk", "proj", "AbCdEf0123456789GhIjKlMnOp"))
GITHUB_TOKEN = "_".join(("ghp", "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"))
SLACK_TOKEN = "-".join(("xox" + "b", "123456789012", "abcdefghijklmnop"))
BEARER = "Bearer " + ".".join(("eyJ" + "hbGciOiJIUzI1NiJ9", "eyJ" + "zdWIiOiIxMjM0NTY3ODkwIn0", "fakeSignature0123"))
AWS_KEY_ID = "AKIA" + "ABCDEFGHIJKLMNOP"
PRIVATE_KEY = "\n".join(("-----BEGIN RSA " + "PRIVATE KEY-----", "MIIEow", "-----END RSA " + "PRIVATE KEY-----"))
PASSWORD = "correct-horse-battery-staple"
# A saved key with no telling shape: only knowing it finds it.
SAVED_KEY = "plain0looking0value0f0r0tavily0search"
ALL = (OPENAI_KEY, GITHUB_TOKEN, SLACK_TOKEN, BEARER.split(" ", 1)[1], PASSWORD, SAVED_KEY)


@pytest.fixture
def saved_key(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", SAVED_KEY)
    redaction.forget_known_secrets()
    yield SAVED_KEY
    redaction.forget_known_secrets()


@pytest.mark.parametrize("secret", [OPENAI_KEY, GITHUB_TOKEN, SLACK_TOKEN, BEARER, AWS_KEY_ID, PRIVATE_KEY])
def test_secret_shaped_text_is_redacted(secret):
    text, changed = redaction.redact_text(f"Use {secret} for the call.")
    assert changed is True and secret not in text and text.startswith("Use ")


def test_a_secret_quoted_under_a_credential_name_is_redacted_and_other_text_is_kept():
    text, changed = redaction.redact_text('{"api_key": "abc123", "city": "Leeds", "max_tokens": 50}')
    assert changed is True and "abc123" not in text
    assert '"city": "Leeds"' in text and '"max_tokens": 50' in text
    assert redaction.redact_text("The weather in Leeds is mild.") == ("The weather in Leeds is mild.", False)


def test_credential_arguments_are_dropped(saved_key):
    arguments, changed = redaction.redact_arguments({
        "url": "https://api.example.com/v1",
        "password": PASSWORD,
        "headers": {"Authorization": BEARER, "Accept": "application/json"},
        "env": {"DATABASE_URL": "postgres://u:p@host/db"},
        "body": {"query": "weather", "note": f"key={saved_key}"},
    })
    assert changed is True
    assert arguments["url"] == "https://api.example.com/v1"
    assert arguments["password"] == arguments["headers"] == arguments["env"] == redaction.REDACTED
    assert arguments["body"]["query"] == "weather" and saved_key not in str(arguments)


def test_counts_named_like_tokens_in_a_message_are_kept():
    usage = {"usage_metadata": {"input_tokens": 120, "output_tokens": 40}, "content": "fine"}
    assert redaction.redact_content(usage) == (usage, False)


def test_a_tool_call_with_a_key_and_a_reply_echoing_a_token_leave_no_secret_anywhere(
    writer, chats, learning_on, saved_key, caplog, monkeypatch
):
    caplog.set_level(logging.DEBUG)
    chats.chat("chat-1")
    turn = writer.begin_turn(thread_id="chat-1", surface="normal_chat", person_message=True)
    request = [SystemMessage(content="You are Row-Bot."),
               HumanMessage(content=f"My GitHub token is {GITHUB_TOKEN}, please check my repos.")]
    call = {"id": "c1", "name": "http_request",
            "args": {"url": "https://api.github.com/user/repos", "api_key": OPENAI_KEY,
                     "headers": {"Authorization": BEARER}, "password": PASSWORD}}
    reply = AIMessage(content="", id="ai-1", tool_calls=[{**call, "type": "tool_call"}])
    writer.model_call(turn, messages=request, response=reply)
    writer.tool_call(turn, call=call, result=ToolMessage(
        content=f"200 OK. Your Slack bot token is {SLACK_TOKEN}; search key {saved_key}.", tool_call_id="c1"))
    answer = AIMessage(content=f"Done. For reference your key is {OPENAI_KEY} and {saved_key}.", id="ai-2")
    writer.model_call(turn, messages=[*request, reply], response=answer)
    writer.approval(turn, payload={"tool": "http_request", "description": f"POST with token={SLACK_TOKEN}"},
                    mode="approve", response=True, line="Approval: asked; approved by you")
    writer.end_turn(turn, "completed")

    # A failing write must not put content into the log either.
    original = LearningStore.put_many
    failures = iter([OSError(f"disk full while writing {OPENAI_KEY}")])

    def fail_once(self, records):
        error = next(failures, None)
        if error is not None:
            raise error
        return original(self, records)

    monkeypatch.setattr(LearningStore, "put_many", fail_once)
    writer.drain()
    turn2 = writer.begin_turn(thread_id="chat-1", surface="normal_chat", person_message=True)
    writer.model_call(turn2, messages=request, response=answer)
    writer.tool_call(turn2, call=call, result=ToolMessage(content=f"key {saved_key}", tool_call_id="c1"))
    writer.end_turn(turn2, "completed")
    writer.drain()

    blobs, records = all_blob_text(learning_on), all_record_text(learning_on)
    assert blobs and records
    for secret in ALL:
        assert secret not in blobs
        assert secret not in records
        assert secret not in caplog.text
    store = LearningStore(learning_on)
    decisions = store.find("decision")
    assert decisions and {item.fidelity for item in decisions} == {"redacted"}
    assert {item.fidelity for item in store.find("episode")} == {"redacted"}
    tool = next(item for item in decisions if item.kind == "tool_call")
    arguments = read_json_blob(store, tool.arguments_blob)["args"]
    assert arguments["url"] == "https://api.github.com/user/repos"
    assert arguments["api_key"] == arguments["password"] == arguments["headers"] == redaction.REDACTED


def test_nothing_redacted_is_exact(writer, chats, learning_on):
    chats.chat("chat-1")
    turn = writer.begin_turn(thread_id="chat-1", surface="normal_chat")
    writer.model_call(turn, messages=[HumanMessage(content="What's 2 + 2?")], response=AIMessage(content="4"))
    writer.drain()
    [decision] = LearningStore(learning_on).find("decision")
    assert decision.fidelity == "exact"
