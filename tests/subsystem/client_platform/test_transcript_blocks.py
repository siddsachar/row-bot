"""Bounded canonical rich transcript block projection."""
from __future__ import annotations

import json

from row_bot.api.v1.schemas import TranscriptRow
from row_bot.application.transcript_blocks import canonical_transcript_blocks


def _row(text: str) -> dict:
    return {
        "id": "assistant:checkpoint:fixture",
        "message_id": "fixture",
        "role": "assistant",
        "blocks": [{"type": "text", "text": text}],
    }


def test_markdown_mermaid_and_youtube_have_stable_ordered_public_identities():
    source = "# Result\n\n```mermaid\ngraph TD\nA-->B\n```\n\n[Demo](https://youtu.be/dQw4w9WgXcQ)"
    first = canonical_transcript_blocks(_row(source))
    second = canonical_transcript_blocks(_row(source))
    assert [block["type"] for block in first["blocks"]] == [
        "markdown",
        "mermaid",
        "youtube",
    ]
    assert [block["id"] for block in first["blocks"]] == [
        block["id"] for block in second["blocks"]
    ]
    assert first["blocks"][1]["source"] == "graph TD\nA-->B"
    assert first["blocks"][2]["video_id"] == "dQw4w9WgXcQ"
    TranscriptRow.model_validate(first)


def test_chart_marker_is_validated_as_inert_json_and_never_leaks_marker_text():
    figure = {"data": [{"type": "bar", "x": ["A"], "y": [1]}], "layout": {"title": "Safe"}}
    projected = canonical_transcript_blocks(
        _row("__CHART__:" + json.dumps(figure) + "\n\nChart ready")
    )
    assert [block["type"] for block in projected["blocks"]] == ["chart"]
    block = projected["blocks"][0]
    assert block["text"] == "Chart ready"
    assert json.loads(block["figure_json"])["data"][0]["type"] == "bar"
    assert "__CHART__" not in json.dumps(projected)
    TranscriptRow.model_validate(projected)


def test_remote_markdown_images_never_auto_load_and_suspicious_urls_are_labelled():
    projected = canonical_transcript_blocks(
        _row("![safe](https://example.test/p.png) ![secret](https://example.test/p?d=" + "A" * 240 + ")")
    )
    text = projected["blocks"][0]["text"]
    assert "![" not in text
    assert "[safe](https://example.test/p.png)" in text
    assert "Blocked suspicious image link" in text


def test_invalid_chart_remains_understandable_without_raw_payload():
    projected = canonical_transcript_blocks(_row("__CHART__:{bad}\n\nUseful summary"))
    assert [block["type"] for block in projected["blocks"]] == ["markdown"]
    assert projected["blocks"][0]["text"] == "Useful summary\n\nChart data is unavailable."
    assert "{bad}" not in projected["blocks"][0]["text"]


def test_a_tool_result_keeps_its_urls_plain_while_a_reply_links_them():
    """A tool's result shows as it was given in its step (N7): "Posted as @rowbot_ai: https://…", never
    "[https://…](https://…)". A reply still links a bare URL."""
    text = "Posted as @rowbot_ai: https://x.com/rowbot_ai/status/1\nPost ID: 1"
    tool = canonical_transcript_blocks({**_row(text), "id": "tool:checkpoint:fixture", "role": "tool",
                                        "tool_call_id": "call-1"})
    reply = canonical_transcript_blocks(_row(text))
    assert tool["blocks"][0]["text"] == text
    assert "[https://x.com/rowbot_ai/status/1](https://x.com/rowbot_ai/status/1)" in reply["blocks"][0]["text"]


def test_a_bold_bare_link_stays_bold_and_links_only_the_address():
    """Z.AI and Together wrote "**https://example.com**" (9 Nov): the link took the closing "**" in and the
    chat showed "[ example.com ]( example.com%2A%2A )"."""
    reply = canonical_transcript_blocks(_row("1. **https://example.com** — Example Domain\n"
                                             "See __https://iana.org/help__, or https://x.y/a."))
    text = reply["blocks"][0]["text"]
    assert "**[https://example.com](https://example.com)**" in text
    assert "__[https://iana.org/help](https://iana.org/help)__" in text
    assert "[https://x.y/a](https://x.y/a)." in text
