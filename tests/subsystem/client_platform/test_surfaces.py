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
    assert shown.splitlines() == ["Book the hotel · Lisbon trip", "Budget: 1,800"]
    with pytest.raises(surfaces.SurfaceError):
        surfaces.action_message(tree, "delete_everything", {}, "book", "now")
    with pytest.raises(surfaces.SurfaceError):
        surfaces.action_message(tree, "book_hotel", {"card_number": "4242"}, "book", "now")


def test_row_bot_s_own_components_bind_to_the_data_model_and_read_as_words():
    [block] = _project(_fence({"title": "October", "components": [
        {"id": "root", "component": "Column", "children": ["spent", "goal", "split", "costs", "trend"]},
        {"id": "spent", "component": "Metric", "label": "Spent", "value": {"@path": "/spent"}, "unit": "£",
         "delta": "-8% vs Sep", "tone": "good"},
        {"id": "goal", "component": "ProgressBar", "label": "Holiday fund", "value": 900, "max": 1500, "unit": "£"},
        {"id": "split", "component": "Breakdown", "items": {"@path": "/parts"}, "currency": "GBP"},
        {"id": "costs", "component": "Table", "title": "Biggest costs",
         "columns": [{"key": "item", "label": "Item"}, "cost"], "rows": {"@path": "/rows"}},
        {"id": "trend", "component": "Chart", "kind": "bar", "title": "By month", "x": ["Sep", "Oct"],
         "series": [{"name": "Total", "values": {"@path": "/totals"}}], "unit": "£"},
    ], "dataModel": {"spent": 2140, "parts": [{"label": "Rent", "amount": 1200}, {"label": "Food", "amount": 512}],
                     "rows": [{"item": "Rent", "cost": 1200}], "totals": [2330, 2140]}}))
    tree = json.loads(block["surface_json"])
    chart = next(component for component in tree["components"] if component["id"] == "trend")
    assert chart["series"] == [{"name": "Total", "values": {"@path": "/totals"}}]
    table = next(component for component in tree["components"] if component["id"] == "costs")
    assert table["columns"][1] == {"key": "cost", "label": "cost", "type": "text"}  # Healed: a bare key.
    assert block["text"].splitlines() == [
        "October", "Spent: £2,140 (-8% vs Sep)", "Holiday fund: £900 of £1,500", "Rent: 1,200", "Food: 512",
        "Biggest costs", "| Item | cost |", "| --- | --- |", "| Rent | 1,200 |",
        "Chart: By month", "Sep: Total 2,330", "Oct: Total 2,140"]


# ── What local models write (G8): healed into what they meant ─────────────────


def test_what_models_get_wrong_heals_into_what_they_meant():
    surface = surfaces.read_surface(json.dumps({
        "title": "Tasks", "surfaceId": "tasks", "components": [
            {"id": "root", "component": "Column", "children": ["note", "list"]},
            # A Text's words written as its "value".
            {"id": "note", "component": "Text", "value": "Open tasks"},
            {"id": "list", "component": "List", "children": {"path": "/tasks", "componentId": "row"}},
            {"id": "row", "component": "Text", "text": {"@path": "title"}}],
        # A source written into the data model, and a data key written as a path.
        "dataModel": {"tasks": {"@call": "rowbot.tasks"}, "/budget": {"rent": 1200}}}))

    assert surface["sources"] == {"/tasks": {"@call": "rowbot.tasks", "args": {}}}
    assert surface["dataModel"] == {"budget": {"rent": 1200}}
    assert surfaces.read_path(surface["dataModel"], "/budget/rent") == 1200
    assert surfaces.surface_text(surface).startswith("Open tasks")


def test_a_table_and_a_chart_fit_the_rows_real_columns():
    rows = [{"date": "2026-10-02", "what": "Groceries", "amount": 61.9},
            {"date": "2026-10-11", "what": "Train", "amount": 487.23}]
    surface = surfaces.read_surface(json.dumps({
        "title": "October", "surfaceId": "october",
        "sources": {"/spend": {"@call": "rowbot.file", "args": {"path": "bank/oct.csv"}}},
        "components": [
            {"id": "root", "component": "Column", "children": ["chart", "table"]},
            # Columns named without seeing the file, and a chart naming columns but no rows.
            {"id": "chart", "component": "Chart", "kind": "bar", "x": "day", "series": [{"name": "Spent",
                                                                                     "values": "total"}]},
            {"id": "table", "component": "Table", "rows": {"@path": "/spend"},
             "columns": [{"key": "date", "label": "Day"}, {"key": "category", "label": "Category"}]}]}))
    chart = next(component for component in surface["components"] if component["component"] == "Chart")
    assert chart["rows"] == {"@path": "/spend"}  # the one source's rows

    filled = {**surface, "dataModel": {"spend": rows}}  # as the person's computer fills it in
    assert surfaces.surface_text(filled).splitlines() == [
        "Chart", "2026-10-02: Amount 61.9", "2026-10-11: Amount 487.23",
        "| Day | What | Amount |", "| --- | --- | --- |",
        "| 2026-10-02 | Groceries | 61.9 |", "| 2026-10-11 | Train | 487.23 |"]


def test_a_chart_bound_to_a_column_inside_rows_reads_the_rows():
    surface = surfaces.read_surface(json.dumps({
        "title": "October", "surfaceId": "october",
        "sources": {"/spend": {"@call": "rowbot.file", "args": {"path": "bank/oct.csv"}}},
        "components": [{"id": "root", "component": "Chart", "kind": "bar", "x": {"@path": "/spend/date"},
                        "series": [{"name": "Spent", "values": {"@path": "/spend/amount"}}]}]}))
    (chart,) = surface["components"]
    assert (chart["rows"], chart["x"], chart["series"][0]["values"]) == ({"@path": "/spend"}, "date", "amount")

    # A list the data model holds itself stays as drawn.
    drawn = surfaces.read_surface(json.dumps({
        "title": "Plain", "surfaceId": "plain", "dataModel": {"chart": {"days": ["Mon", "Tue"], "km": [3, 5]}},
        "components": [{"id": "root", "component": "Chart", "kind": "bar", "x": {"@path": "/chart/days"},
                        "series": [{"name": "Km", "values": {"@path": "/chart/km"}}]}]}))
    assert "rows" not in drawn["components"][0]


def test_paths_into_a_source_written_as_text_read_that_source():
    # As qwen3.8:27b wrote it: the source named by its key, the rows and columns as plain-text paths.
    surface = surfaces.read_surface(json.dumps({
        "title": "October", "surfaceId": "october", "dataModel": {},
        "sources": {"rows": {"@call": "rowbot.file", "args": {"path": "bank/oct.csv"}}},
        "components": [
            {"id": "root", "component": "Column", "children": ["chart", "table", "note"]},
            {"id": "chart", "component": "Chart", "kind": "line", "x": "/rows/date",
             "series": [{"name": "Spending", "values": "/rows/amount"}]},
            {"id": "table", "component": "Table", "rows": "/rows", "columns": [{"key": "date", "label": "Date"}]},
            {"id": "note", "component": "Text", "text": "/rows is read on this computer"}]}))
    by_id = {component["id"]: component for component in surface["components"]}

    assert list(surface["sources"]) == ["/rows"]
    assert (by_id["chart"]["rows"], by_id["chart"]["x"], by_id["chart"]["series"][0]["values"]) == (
        {"@path": "/rows"}, "date", "amount")
    assert by_id["table"]["rows"] == {"@path": "/rows"}
    assert by_id["note"]["text"] == "/rows is read on this computer"  # other text stays as written


def test_an_input_written_with_a_plain_value_takes_the_persons_change():
    surface = surfaces.read_surface(json.dumps({
        "title": "Packing", "surfaceId": "packing",
        "components": [
            {"id": "root", "component": "Column", "children": ["tent", "notes", "kit", "split"]},
            {"id": "tent", "component": "CheckBox", "label": "Tent", "value": False},
            {"id": "notes", "component": "TextField", "label": "Notes", "value": {"@path": "/notes"}},
            {"id": "kit", "component": "List", "children": {"path": "/kit", "componentId": "item"}},
            {"id": "item", "component": "CheckBox", "label": {"@path": "name"}, "value": False},
            {"id": "split", "component": "Breakdown", "items": [{"label": "Rent", "amount": 1500}]},
        ],
        "dataModel": {"notes": "", "kit": [{"name": "Stove"}]},
    }))
    by_id = {component["id"]: component for component in surface["components"]}
    assert by_id["tent"]["value"] == {"@path": "/tent"}
    assert surface["dataModel"]["tent"] is False
    assert by_id["notes"]["value"] == {"@path": "/notes"}  # already bound: left as drawn
    assert by_id["item"]["value"] == {"@path": "item"}  # inside a repeated item: its own item's place
    assert "item" not in surface["dataModel"]
    assert by_id["split"]["items"] == {"@path": "/split"}
    assert surface["dataModel"]["split"] == [{"label": "Rent", "amount": 1500}]


def test_a_press_shows_a_checklists_ticks_in_words_never_their_json():
    surface = surfaces.read_surface(json.dumps({
        "title": "Water", "surfaceId": "water",
        "components": [
            {"id": "root", "component": "Column", "children": ["one", "review"]},
            {"id": "one", "component": "CheckBox", "label": "Glass 1", "value": {"@path": "/glasses/1"}},
            {"id": "label", "component": "Text", "text": "How did I do?"},
            {"id": "review", "component": "Button", "child": "label", "action": {"event": {
                "name": "review", "context": {"glasses": {"@path": "/glasses"}, "goal": 8}}}},
        ],
        "dataModel": {"glasses": {"1": True, "2": True, "3": False}},
    }))
    _, shown = surfaces.action_message(surface, "review", {"glasses": {"1": True, "2": True, "3": False}, "goal": 8},
                                       "review", "2026-11-27T09:42:00Z")
    assert shown.splitlines() == ["How did I do? · Water", "Glasses: 2 of 3 ticked", "Goal: 8"]


def _spending_with(buttons: list[dict], extra: list[dict] | None = None) -> dict:
    return surfaces.read_surface(json.dumps({
        "title": "Spending", "surfaceId": "spend",
        "components": [
            {"id": "root", "component": "Column", "children": [*(b["id"] for b in buttons), "rows"]},
            {"id": "word", "component": "Text", "text": "Go"},
            {"id": "rows", "component": "List", "children": {"path": "/spend", "componentId": "item"}},
            *buttons, *(extra or []),
        ],
        "dataModel": {"month": "October"},
        "sources": {"/spend": {"@call": "rowbot.file", "args": {"path": "bank/oct.csv"}}},
    }))


@pytest.mark.parametrize("context", [
    {"total": {"@path": "/spend/total"}},
    {"total": {"@call": "formatString", "args": {"value": "Total ${/spend/total}"}}},
])
def test_a_press_never_carries_what_this_computer_filled_in(context):
    surface = _spending_with([{"id": "send", "component": "Button", "child": "word",
                               "action": {"event": {"name": "send", "context": {**context, "month": {"@path": "/month"}}}}}])
    assert surfaces.declared_actions(surface)["send"]["context"] == {"month"}
    with pytest.raises(surfaces.SurfaceError):
        surfaces.action_message(surface, "send", {"total": "1,945.18", "month": "October"}, "send", "t")
    model, shown = surfaces.action_message(surface, "send", {"month": "October"}, "send", "t")
    assert "1,945.18" not in model and "October" in model and "Month: October" in shown


def test_a_button_repeated_over_a_source_sends_none_of_its_items_values():
    surface = _spending_with([], [
        {"id": "label", "component": "Text", "text": "Check"},
        {"id": "item", "component": "Button", "child": "label", "action": {"event": {
            "name": "check", "context": {"amount": {"@path": "amount"}, "kind": "row"}}}},
    ])
    assert surfaces.declared_actions(surface)["check"]["context"] == {"kind"}


def test_a_key_private_on_one_button_stays_out_for_every_button_of_that_name():
    surface = _spending_with([
        {"id": "a", "component": "Button", "child": "word",
         "action": {"event": {"name": "go", "context": {"value": {"@path": "/month"}}}}},
        {"id": "b", "component": "Button", "child": "word",
         "action": {"event": {"name": "go", "context": {"value": {"@path": "/spend/total"}}}}},
    ])
    assert surfaces.declared_actions(surface)["go"]["context"] == set()
