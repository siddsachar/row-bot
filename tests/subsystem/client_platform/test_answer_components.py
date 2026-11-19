"""Answer components (F3): fenced rowbot-* blocks become validated, inert blocks, or stay code."""
from __future__ import annotations

import json

import pytest

from row_bot.api.v1.schemas import TranscriptRow
from row_bot.application.transcript_blocks import MAX_BLOCKS, canonical_transcript_blocks


def _row(text: str, role: str = "assistant") -> dict:
    return {
        "id": f"{role}:checkpoint:fixture",
        "message_id": "fixture",
        "role": role,
        "blocks": [{"type": "text", "text": text}],
    }


def _fence(tag: str, body: object) -> str:
    return f"```rowbot-{tag}\n{body if isinstance(body, str) else json.dumps(body)}\n```"


def _project(text: str, role: str = "assistant") -> list[dict]:
    projected = canonical_transcript_blocks(_row(text, role))
    TranscriptRow.model_validate(projected)
    return projected["blocks"]


TABLE = {
    "title": "Laptops",
    "columns": [
        {"key": "name", "label": "Model"},
        {"key": "price", "label": "Price", "type": "currency"},
        {"key": "weight", "label": "Weight (kg)", "type": "number"},
    ],
    "rows": [
        {"name": "Air", "price": "£999", "weight": 1.24},
        {"name": "Pro", "price": "£1,599", "weight": 1600},
    ],
    "note": "Prices from the shop's page.",
    "actions": [{"label": "Compare batteries", "reply": "Compare their battery life"}],
}


def test_each_tag_becomes_its_own_block_between_the_prose_around_it():
    text = "\n\n".join([
        "Here is what I found.",
        _fence("table", TABLE),
        _fence("checklist", {"title": "Before Friday", "items": [
            {"id": "book", "text": "Book the room", "done": True}, {"text": "Send the agenda"}]}),
        _fence("figures", {"title": "October", "items": [
            {"label": "Spent", "value": 2140, "unit": "£", "delta": "-8% vs Sep", "tone": "good"},
            {"label": "Saved", "value": "a lot"}]}),
        _fence("progress", {"items": [{"label": "Running", "value": 42, "target": 50, "unit": "km"}]}),
        _fence("choices", {"prompt": "Which one?", "options": [
            {"label": "Air", "reply": "Tell me more about the Air"},
            {"label": "Pro", "reply": "Tell me more about the Pro"}]}),
        "That's all.",
    ])
    blocks = _project(text)
    assert [block["type"] for block in blocks] == [
        "markdown", "table", "checklist", "figures", "progress", "choices", "markdown"]

    table = blocks[1]
    assert table["title"] == "Laptops"
    assert [column["label"] for column in table["columns"]] == ["Model", "Price", "Weight (kg)"]
    assert table["columns"][1]["type"] == "currency"
    assert table["rows"] == [["Air", "£999", "1.24"], ["Pro", "£1,599", "1,600"]]
    assert table["note"] == "Prices from the shop's page."
    assert table["actions"] == [{"label": "Compare batteries", "reply": "Compare their battery life"}]
    assert table["text"].splitlines()[:3] == ["Laptops", "| Model | Price | Weight (kg) |", "| --- | --- | --- |"]

    checklist = blocks[2]
    assert checklist["items"] == [
        {"id": "book", "text": "Book the room", "done": True},
        {"id": "item-2", "text": "Send the agenda", "done": False},
    ]
    assert checklist["text"] == "Before Friday\n- [x] Book the room\n- [ ] Send the agenda"

    figures = blocks[3]
    assert figures["items"][0] == {
        "label": "Spent", "value": 2140.0, "unit": "£", "delta": "-8% vs Sep", "tone": "good"}
    assert figures["items"][1]["value"] == "a lot"
    assert figures["text"] == "October\nSpent: £2,140 (-8% vs Sep)\nSaved: a lot"

    assert blocks[4]["text"] == "Running: 42 km of 50 km (84%)"
    assert blocks[5]["options"][1] == {"label": "Pro", "reply": "Tell me more about the Pro"}
    assert blocks[5]["text"] == "Which one?\n1. Air\n2. Pro"
    assert "rowbot-" not in json.dumps(blocks)


def test_a_chart_becomes_the_existing_chart_block_with_the_given_series():
    blocks = _project(_fence("chart", {
        "title": "Weekly runs", "kind": "line", "x": ["Mon", "Tue", "Wed"],
        "series": [{"name": "Distance", "values": [5, None, 7.5]}, {"name": "Pace", "values": [6, 5.5, 5]}],
        "y_label": "km", "unit": "km",
    }))
    assert [block["type"] for block in blocks] == ["chart"]
    figure = json.loads(blocks[0]["figure_json"])
    assert [(trace["type"], trace["name"], trace["y"]) for trace in figure["data"]] == [
        ("scatter", "Distance", [5, None, 7.5]), ("scatter", "Pace", [6, 5.5, 5])]
    assert figure["data"][0]["x"] == ["Mon", "Tue", "Wed"]
    assert figure["layout"]["title"] == {"text": "Weekly runs"}
    assert figure["layout"]["yaxis"]["title"] == {"text": "km"}
    assert blocks[0]["text"].splitlines() == [
        "Weekly runs", "Mon: Distance 5 km, Pace 6 km", "Tue: Distance —, Pace 5.5 km", "Wed: Distance 7.5 km, Pace 5 km"]

    bar = json.loads(_project(_fence("chart", {
        "kind": "bar", "x": [2024, 2025], "series": [{"name": "A", "values": [1, 2]}, {"name": "B", "values": [3, 4]}],
    }))[0]["figure_json"])
    assert {trace["type"] for trace in bar["data"]} == {"bar"}
    assert bar["layout"]["barmode"] == "group"


@pytest.mark.parametrize("tag, body", [
    ("table", "{not json"),
    ("table", "NaN"),
    ("table", {**TABLE, "rows": [{"name": "x"}] * 201}),
    ("table", {**TABLE, "columns": [{"key": f"c{index}"} for index in range(13)]}),
    ("table", {**TABLE, "rows": [{"name": "x" * 201}]}),
    ("table", {**TABLE, "rows": [{"colour": "red"}]}),
    ("table", {**TABLE, "colour": "red"}),
    ("table", {**TABLE, "actions": [{"label": "Go", "reply": "Go"}] * 5}),
    ("table", {**TABLE, "actions": [{"label": "x" * 41, "reply": "Go"}]}),
    ("table", {**TABLE, "actions": [{"label": "Go", "reply": "x" * 501}]}),
    ("chart", {"kind": "pie", "x": ["a"], "series": [{"name": "A", "values": [1]}]}),
    ("chart", {"kind": "bar", "x": ["a", "b"], "series": [{"name": "A", "values": [1]}]}),
    ("chart", {"kind": "bar", "x": ["a"], "series": [{"name": f"S{index}", "values": [1]} for index in range(7)]}),
    ("checklist", {"items": []}),
    ("checklist", {"items": [{"text": "a", "done": "yes"}]}),
    ("checklist", {"items": [{"id": "a", "text": "a"}, {"id": "a", "text": "b"}]}),
    ("figures", {"items": [{"label": "x", "value": 1}] * 7}),
    ("figures", {"items": [{"label": "x", "value": 1, "tone": "great"}]}),
    ("progress", {"items": [{"label": "x", "value": -1, "target": 5}]}),
    ("progress", {"items": [{"label": "x", "value": 1, "target": 0}]}),
    ("choices", {"options": [{"label": "Only", "reply": "Only"}]}),
    ("choices", {"options": [{"label": str(index), "reply": "r"} for index in range(7)]}),
    ("figures", {"items": [{"label": "<b></b>", "value": 1}]}),
    ("choices", {"options": [{"label": "Clear", "reply": "/clear"}, {"label": "Keep", "reply": "Keep it"}]}),
    ("table", {**TABLE, "actions": [{"label": "Goal", "reply": "/goal Ship it"}]}),
])
def test_a_body_that_does_not_fit_stays_an_ordinary_code_block_with_its_text(tag, body):
    fence = _fence(tag, body)
    blocks = _project(f"Before\n\n{fence}\n\nAfter")
    assert [block["type"] for block in blocks] == ["markdown"]
    assert fence in blocks[0]["text"]


def test_an_unknown_tag_and_an_oversized_body_stay_code():
    huge = '{"columns": [{"key": "a"}], "rows": [{"a": "x"}]}' + " " * (128 * 1024)
    for fence in (_fence("map", {"a": 1}), _fence("table", huge)):
        blocks = _project(fence)
        assert [block["type"] for block in blocks] == ["markdown"]
        assert blocks[0]["text"] == fence


def test_markdown_html_and_links_inside_a_component_are_reduced_to_plain_text():
    blocks = _project(_fence("table", {
        "columns": [{"key": "a", "label": "**Name** <i>here</i>"}],
        "rows": [
            {"a": "[click](https://evil.test/x) <script>alert(1)</script> ![pixel](https://evil.test/p.png)"},
            {"a": "`code` and __bold__\nnext‮line"},
        ],
        "actions": [{"label": "[Go](https://evil.test)", "reply": "Go on"}],
    }))
    table = blocks[0]
    assert table["columns"][0]["label"] == "Name here"
    assert table["rows"] == [["click alert(1) pixel"], ["code and bold nextline"]]
    assert table["actions"][0]["label"] == "Go"
    serialized = json.dumps(blocks)
    assert "evil.test" not in serialized and "<" not in serialized


def test_block_ids_are_stable_across_projection_and_differ_by_content():
    text = "Intro\n\n" + _fence("checklist", {"items": [{"text": "One"}]})
    first, second = _project(text), _project(text)
    assert [block["id"] for block in first] == [block["id"] for block in second]
    changed = _project("Intro\n\n" + _fence("checklist", {"items": [{"text": "Two"}]}))
    assert changed[1]["id"] != first[1]["id"]


def test_only_a_reply_makes_components_a_tool_result_or_person_keeps_the_text():
    fence = _fence("choices", {"options": [{"label": "Yes", "reply": "Yes"}, {"label": "No", "reply": "No"}]})
    for role in ("tool", "user"):
        row = {**_row(fence, role), "tool_call_id": "call-1"} if role == "tool" else _row(fence, role)
        projected = canonical_transcript_blocks(row)
        assert [block["type"] for block in projected["blocks"]] == ["markdown"]
        assert projected["blocks"][0]["text"] == fence


def test_the_block_bound_still_holds_with_many_components():
    fence = _fence("progress", {"items": [{"label": "x", "value": 1, "target": 2}]})
    blocks = _project("\n\n".join([fence] * (MAX_BLOCKS + 20)))
    assert len(blocks) == MAX_BLOCKS
    assert {block["type"] for block in blocks} == {"progress"}


CHECKLIST = {"title": "Weekend in Leeds", "items": [{"text": "Waterproof jacket"}, {"text": "Power bank", "done": True}]}
CHOICES = {"prompt": "What's next?", "options": [{"label": "More cities", "reply": "More cities"},
                                                  {"label": "Thanks", "reply": "Thanks"}]}


def test_a_component_body_fenced_as_json_or_with_no_language_still_becomes_its_component():
    # What a local model wrote in the 5.3.4 Windows test (W14): the right shapes, in ```json fences.
    text = "\n\n".join([
        "Here are the five largest cities.",
        "```json\n" + json.dumps(TABLE, indent=2) + "\n```",
        "```\n" + json.dumps(CHECKLIST) + "\n```",
        "```JSON\n" + json.dumps(CHOICES) + "\n```",
        "Tap one whenever you're ready.",
    ])
    blocks = _project(text)
    assert [block["type"] for block in blocks] == ["markdown", "table", "checklist", "choices", "markdown"]
    assert blocks[1]["rows"] == [["Air", "£999", "1.24"], ["Pro", "£1,599", "1,600"]]
    assert blocks[2]["text"] == "Weekend in Leeds\n- [ ] Waterproof jacket\n- [x] Power bank"
    assert blocks[3]["options"][1] == {"label": "Thanks", "reply": "Thanks"}
    assert "```" not in json.dumps(blocks)


def test_a_reply_that_is_only_a_component_object_becomes_that_component():
    blocks = _project("\n" + json.dumps(CHOICES) + "\n")
    assert [block["type"] for block in blocks] == ["choices"]
    assert blocks[0]["prompt"] == "What's next?"


@pytest.mark.parametrize("text", [
    "```json\n{\"name\": \"Air\", \"price\": 999}\n```",  # Ordinary JSON: no component's shape.
    "```json\n" + json.dumps({**CHECKLIST, "colour": "red"}) + "\n```",  # A key no component has.
    "```json\n" + json.dumps([CHECKLIST]) + "\n```",  # Not one object.
    "```python\n" + json.dumps(CHECKLIST) + "\n```",  # Another language is that language's code.
    "```json\n" + json.dumps({"options": [{"label": "Clear", "reply": "/clear"},
                                           {"label": "Keep", "reply": "Keep"}]}) + "\n```",  # Still refused.
    "Here it is: " + json.dumps(CHECKLIST),  # An object inside prose is prose.
])
def test_untagged_json_that_is_not_exactly_one_component_stays_as_written(text):
    blocks = _project(text)
    assert [block["type"] for block in blocks] == ["markdown"]
    assert blocks[0]["text"] == text


def test_a_visual_is_never_inferred_and_a_person_or_tool_keeps_untagged_json():
    html = "```json\n{\"html\": \"<svg><circle r='4'/></svg>\"}\n```"
    assert [block["type"] for block in _project(html)] == ["markdown"]
    fenced = "```json\n" + json.dumps(CHOICES) + "\n```"
    for role in ("tool", "user"):
        row = {**_row(fenced, role), "tool_call_id": "call-1"} if role == "tool" else _row(fenced, role)
        assert [block["type"] for block in canonical_transcript_blocks(row)["blocks"]] == ["markdown"]


def test_a_visual_keeps_its_html_for_the_sandboxed_frame_and_names_itself():
    svg = '<svg viewBox="0 0 10 10"><title>Water <b>cycle</b></title><circle r="4"/></svg>'
    blocks = _project("A diagram:\n\n```rowbot-visual\n" + svg + "\n```")
    assert [block["type"] for block in blocks] == ["markdown", "visual"]
    assert blocks[1]["source"] == svg
    assert blocks[1]["title"] == "Water cycle"
    assert blocks[1]["text"] == "Visual: Water cycle"


def test_an_empty_or_oversized_visual_stays_code():
    for body in (" ", "<div>" + "x" * (200 * 1024) + "</div>"):
        fence = "```rowbot-visual\n" + body + "\n```"
        blocks = _project(fence)
        assert [block["type"] for block in blocks] == ["markdown"]
