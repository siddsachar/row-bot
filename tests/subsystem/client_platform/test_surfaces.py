"""Surfaces (generative UI): a ```rowbot-ui reply becomes a checked A2UI 1.0 surface block, or stays code."""
from __future__ import annotations

import json

import pytest

from row_bot.api.v1.schemas import TranscriptRow
from row_bot.application import surfaces
from row_bot.application.answer_components import components_as_text
from row_bot.application.transcript_blocks import canonical_transcript_blocks

pytestmark = pytest.mark.subsystem


def _project(text: str, role: str = "assistant") -> list[dict]:
    projected = canonical_transcript_blocks({
        "id": f"{role}:checkpoint:fixture", "message_id": "fixture", "role": role,
        "blocks": [{"type": "text", "text": text}]})
    TranscriptRow.model_validate(projected)
    return projected["blocks"]


def _fence(body: object, language: str = "rowbot-ui") -> str:
    return f"```{language}\n{body if isinstance(body, str) else json.dumps(body)}\n```"


TRIP = {
    "version": "1.0",
    "createSurface": {
        "surfaceId": "trip_budget",
        "catalogId": surfaces.BASIC_CATALOG_ID,
        "components": [
            {"id": "root", "component": "Column", "children": ["heading", "budget", "total", "days", "book"]},
            {"id": "heading", "component": "Text", "text": "## Lisbon in March"},
            {"id": "budget", "component": "Slider", "label": "Budget", "min": 500, "max": 3000,
             "value": {"@path": "/budget"}},
            {"id": "total", "component": "Text",
             "text": {"@call": "formatString", "args": {"value": "Total ${/budget}"}}},
            {"id": "days", "component": "List", "children": {"path": "/days", "componentId": "day"}},
            {"id": "day", "component": "Text", "text": {"@path": "name"}},
            {"id": "book", "component": "Button", "child": "book_label", "variant": "primary",
             "action": {"event": {"name": "book_hotel", "context": {"budget": {"@path": "/budget"}}}}},
            {"id": "book_label", "component": "Text", "text": "Book the hotel"},
        ],
        "dataModel": {"budget": 1500, "days": [{"name": "Alfama"}, {"name": "Belém"}]},
    },
    "title": "Lisbon trip",
}


def test_a_ui_fence_becomes_one_surface_block_between_the_prose_around_it():
    blocks = _project("Here's a plan.\n\n" + _fence(TRIP) + "\n\nEnjoy the trip.")
    assert [block["type"] for block in blocks] == ["markdown", "surface", "markdown"]
    surface = blocks[1]
    assert surface["surface_id"] == "trip_budget" and surface["title"] == "Lisbon trip"
    tree = json.loads(surface["surface_json"])
    assert tree["catalogId"] == surfaces.CATALOG_ID
    assert [component["id"] for component in tree["components"]] == [
        "root", "heading", "budget", "total", "days", "day", "book", "book_label"]
    assert tree["dataModel"]["days"][1]["name"] == "Belém"
    # Its words, for copying and search, read the data model it holds.
    assert surface["text"].splitlines() == [
        "Lisbon trip", "## Lisbon in March", "Budget: 1,500", "Total 1,500", "Alfama", "Belém", "[Book the hotel]"]


def test_near_a2ui_is_healed_into_a_surface_a_client_can_draw():
    healed = _project(_fence({"components": [
        {"id": "card-1", "component": "Card", "child": "line-1"},  # No root, ids that aren't identifiers.
        {"id": "line-1", "component": {"Column": {"children": "hello"}}},  # 0.8's nested component, one child.
        {"id": "hello", "component": "Text", "text": {"path": "/greeting"}, "colour": "red"},  # 0.8's path.
        {"id": "ghost", "component": "Hologram"},  # Not in the catalog.
        {"id": "go", "component": "Button", "child": "hello", "action": {"name": "wave", "context": [
            {"key": "who", "value": "me"}]}},  # Not reachable from the root: left out.
    ], "dataModel": {"greeting": "Hi"}}))
    assert [block["type"] for block in healed] == ["surface"]
    tree = json.loads(healed[0]["surface_json"])
    assert [(component["id"], component["component"]) for component in tree["components"]] == [
        ("root", "Card"), ("line_1", "Column"), ("hello", "Text")]
    assert tree["components"][1]["children"] == ["hello"]
    assert tree["components"][2] == {"id": "hello", "component": "Text", "text": {"@path": "/greeting"}}
    assert healed[0]["text"] == "Hi"


def test_an_untagged_a2ui_envelope_from_a_local_model_is_a_surface_too():
    blocks = _project(_fence(TRIP, "json"))
    assert [block["type"] for block in blocks] == ["surface"]


@pytest.mark.parametrize("body", [
    "not json",
    {"components": []},
    {"createSurface": {"surfaceId": "s", "components": [{"id": "root", "component": "Hologram"}]}},
    # A function the catalog doesn't have, where a value belongs.
    {"components": [{"id": "root", "component": "Text", "text": {"@call": "fetch", "args": {"url": "x"}}}]},
    # openUrl needs the person's click: never as a value.
    {"components": [{"id": "root", "component": "Text", "text": {"@call": "openUrl", "args": {"url": "x"}}}]},
    # A component that contains itself.
    {"components": [{"id": "root", "component": "Card", "child": "root"}]},
    {"components": [{"id": "root", "component": "Text", "text": "x" * (surfaces.MAX_STRING_CHARS + 1)}]},
    {"createSurface": {"surfaceId": "s", "components": [{"id": "root", "component": "Text", "text": "a"}]},
     "deleteSurface": {"surfaceId": "s"}},
])
def test_a_body_that_is_not_a_drawable_surface_stays_a_code_block(body):
    text = _fence(body)
    blocks = _project(text)
    assert [block["type"] for block in blocks] == ["markdown"]
    assert blocks[0]["text"].strip() == text


def test_json_lines_of_messages_fold_into_one_surface():
    lines = "\n".join(json.dumps(message) for message in [
        {"version": "1.0", "createSurface": {"surfaceId": "status", "components": [
            {"id": "root", "component": "Card", "child": "line"}]}},
        {"version": "1.0", "updateComponents": {"surfaceId": "status", "components": [
            {"id": "line", "component": "Text", "text": {"@path": "/state"}}]}},
        {"version": "1.0", "updateDataModel": {"surfaceId": "status", "path": "/state", "value": "Booked"}},
        {"version": "1.0", "updateDataModel": {"surfaceId": "other", "path": "/state", "value": "Ignored"}},
    ])
    [block] = _project(_fence(lines))
    assert block["type"] == "surface" and block["text"] == "Booked"


def test_a_button_may_open_a_link_only_on_its_own_press():
    [block] = _project(_fence({"components": [
        {"id": "root", "component": "Button", "child": "label",
         "action": {"functionCall": {"@call": "openUrl", "args": {"url": "https://example.com"}}}},
        {"id": "label", "component": "Text", "text": "Open the site"}]}))
    tree = json.loads(block["surface_json"])
    assert tree["components"][0]["action"]["functionCall"]["@call"] == "openUrl"


def test_a_surface_reaches_a_chat_app_as_its_words_never_its_json():
    text = components_as_text("Plan below.\n\n" + _fence(TRIP))
    assert "createSurface" not in text and "{" not in text
    assert "Lisbon in March" in text and "[Book the hotel]" in text


def test_a_press_becomes_a2ui_s_action_message_and_only_for_what_the_surface_declares():
    tree = surfaces.read_surface(json.dumps(TRIP))
    model, shown = surfaces.action_message(tree, "book_hotel", {"budget": 1800}, "book", "2026-11-24T10:00:00+00:00")
    message = json.loads(model.split("```json\n", 1)[1].rsplit("\n```", 1)[0])
    assert message == {"version": "1.0", "action": {
        "name": "book_hotel", "surfaceId": "trip_budget", "sourceComponentId": "book",
        "timestamp": "2026-11-24T10:00:00+00:00", "context": {"budget": 1800}}}
    assert "not approval of any tool call" in model
    assert shown.splitlines() == ["Book the hotel · Lisbon trip", "budget: 1,800"]
    with pytest.raises(surfaces.SurfaceError):
        surfaces.action_message(tree, "delete_everything", {}, "book", "now")
    with pytest.raises(surfaces.SurfaceError):
        surfaces.action_message(tree, "book_hotel", {"card_number": "4242"}, "book", "now")
