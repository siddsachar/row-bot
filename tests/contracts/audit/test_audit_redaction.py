from __future__ import annotations

import pytest

from row_bot.audit import (
    MASK, Action, InvalidAuditEvent, RedactionLevel, from_dict, looks_like_secret, mask_text, redact, to_dict,
)
from tests.contracts.audit.examples import example

pytestmark = pytest.mark.contract

_TOOL_METADATA = {"approval_level", "surface", "risk", "app", "rule_id"}

# What survives at each level. Only Work tool actions hold anything above metadata.
TABLE = {
    RedactionLevel.METADATA: _TOOL_METADATA,
    RedactionLevel.SUMMARIES: _TOOL_METADATA | {"summary"},
    RedactionLevel.FULL: _TOOL_METADATA | {"summary", "arguments"},
}


@pytest.mark.parametrize("level", list(RedactionLevel), ids=lambda level: level.value)
def test_the_redaction_table_for_work_tool_actions(level):
    full = example(Action.TOOL_CALLED)

    redacted = redact(full, level)

    assert set(redacted.details) == TABLE[level]
    assert redacted.redaction is level
    assert from_dict(to_dict(redacted)) == redacted
    assert set(full.details) == TABLE[RedactionLevel.FULL]  # Pure: the original is unchanged.


@pytest.mark.parametrize("action", [action for action in Action if action is not Action.TOOL_CALLED],
                         ids=lambda action: action.value)
def test_every_other_action_is_metadata_only(action):
    event = example(action)

    assert redact(event, RedactionLevel.METADATA) == event
    with pytest.raises(InvalidAuditEvent):  # And a free-text detail can't be smuggled in.
        example(action, redaction=RedactionLevel.FULL, details={**event.details, "summary": "free text"})


def test_redaction_only_removes():
    metadata = redact(example(Action.TOOL_CALLED), "metadata")

    assert redact(metadata, RedactionLevel.FULL) is metadata
    assert redact(redact(example(Action.TOOL_CALLED), "summaries"), "summaries").details.keys() == TABLE[
        RedactionLevel.SUMMARIES]


def test_free_text_is_refused_unless_the_level_allows_it():
    details = dict(example(Action.TOOL_CALLED).details)

    with pytest.raises(InvalidAuditEvent, match="arguments: needs redaction level full"):
        example(Action.TOOL_CALLED, redaction="summaries", details=details)
    del details["arguments"]
    with pytest.raises(InvalidAuditEvent, match="summary: needs redaction level summaries"):
        example(Action.TOOL_CALLED, redaction="metadata", details=details)
    assert example(Action.TOOL_CALLED, redaction="summaries", details=details).details["summary"]


SECRETS = [
    "sk-proj-abcdefghijklmnopqrstuvwx",
    "sk_live_abcdefghijklmnop1234",
    "ghp_abcdefghijklmnopqrstuvwxyz0123",
    "github_pat_11ABCDEFG0123456789_abcdefghij",
    "xoxb-1234567890-abcdefghij",
    "AKIAABCDEFGHIJKLMNOP",
    "AIzaSyA-abcdefghijklmnopqrstuvwxyz01234",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop",
    "Bearer abcdefghijklmnop.qrstuv",
    "https://priya:hunter2hunter2@git.example.com/repo",
    "password=correcthorsebattery",
    "api_key: 0123456789abcdef",
    '{"client_secret": "x"}',
    "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBg\n-----END PRIVATE KEY-----",
]


@pytest.mark.parametrize("secret", SECRETS)
def test_secret_shaped_text_is_always_masked(secret):
    event = example(Action.TOOL_CALLED, details={
        **example(Action.TOOL_CALLED).details, "summary": f"Called the API with {secret} today"})

    summary = event.details["summary"]
    assert MASK in summary and not looks_like_secret(summary)
    assert summary.startswith("Called the API with ")
    assert mask_text(summary) == summary  # Masking is stable, so a masked event re-checks and re-hashes alike.
    assert from_dict(to_dict(event)) == event
    assert redact(event, "summaries").details["summary"] == summary


def test_tool_arguments_are_masked_by_name_and_by_shape():
    arguments = {
        "api_key": "short",
        "headers": {"Authorization": "anything", "Accept": "text/plain"},
        "env": {"HOME": "/home/priya-work"},
        "max_tokens": 500,
        "note": "use ghp_abcdefghijklmnopqrstuvwxyz0123 for this",
        "nested": [{"token": "t0k3n"}, {"query": "refunds"}],
    }
    event = example(Action.TOOL_CALLED, details={**example(Action.TOOL_CALLED).details, "arguments": arguments})

    assert to_dict(event)["details"]["arguments"] == {
        "api_key": MASK,
        "headers": {"Authorization": MASK, "Accept": MASK},
        "env": {"HOME": MASK},
        "max_tokens": 500,
        "note": f"use {MASK} for this",
        "nested": [{"token": MASK}, {"query": "refunds"}],
    }


def test_text_that_merely_mentions_secrets_is_kept():
    for text in ("Rotated the API key for Jira", "max_tokens is 500", "password reset email sent", "sk-1"):
        assert mask_text(text) == text
