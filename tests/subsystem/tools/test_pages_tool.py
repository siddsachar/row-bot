"""Pages (N5): page_create, page_update and page_read.

A page belongs to its chat. Each change is a new version (the last ten are kept); its state and the
person's decisions stay across versions. The caps hold: 1 MB of HTML, 1 MB of data, 256 KB of state.
A step's result names the page on its first line, so the chat shows its card.
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.subsystem

PLAN = """<!doctype html><html><head><title>Plan</title></head><body>
<ol id="steps"></ol><script>for (const s of rowbot.data().steps) steps.append(s.title)</script></body></html>"""
STEPS = {"title": "Launch plan", "steps": [{"id": "announce", "title": "Draft the announcement", "status": "todo"},
                                          {"id": "venue", "title": "Book the venue", "status": "todo"}]}


@pytest.fixture
def chat(tmp_path, monkeypatch):
    from row_bot import pages
    from row_bot.tools import pages_tool

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(pages, "_RECORDS", {})
    monkeypatch.setattr(pages, "_LOADED_ROOT", None)
    monkeypatch.setattr(pages, "_RATES", {})
    current = {"conversation": "chat-launch"}
    monkeypatch.setattr("row_bot.tool_outputs.current_thread_id", lambda: current["conversation"])
    return pages_tool, pages, current


def _page_id(result: str) -> str:
    first = result.splitlines()[0]
    assert first.startswith("Page page-"), result
    return first.split(":", 1)[0].removeprefix("Page ")


def test_a_page_is_made_in_its_chat_and_its_card_is_named_on_the_first_line(chat):
    tool, pages, _current = chat
    result = tool._page_create(title="Launch plan", html=PLAN, data=STEPS, libraries=["rowbot-style", "charts"])
    page = pages.get(_page_id(result))
    assert (page.conversation_id, page.title, page.version) == ("chat-launch", "Launch plan", 1)
    assert page.libraries == ["charts", "rowbot-style"]
    assert pages.read_data(page) == STEPS

    from row_bot.application.conversation_traces import specialize_tool_result

    found = specialize_tool_result({"name": "page_create", "content": result})
    assert (found.kind, found.page_id) == ("page", page.page_id)
    assert specialize_tool_result({"name": "page_read", "content": result}) is None


def test_a_fragment_becomes_a_whole_document_and_remote_loads_are_flagged(chat):
    tool, pages, _current = chat
    result = tool._page_create(title="Checklist", html='<script src="https://cdn.example/x.js"></script><p>Hi</p>')
    page = pages.get(_page_id(result))
    assert pages.read_html(page).startswith("<!doctype html>")
    assert "has no network" in result


def test_updates_make_versions_and_keep_state_and_decisions(chat):
    tool, pages, _current = chat
    page_id = _page_id(tool._page_create(title="Launch plan", html=PLAN, data=STEPS))
    pages.set_state(page_id, {"ticked": {"announce": True}})
    pages.decide(page_id, "venue", "change", "Smaller rooms near the station", "Book the venue")

    progress = {**STEPS, "steps": [{**STEPS["steps"][0], "status": "done"}, STEPS["steps"][1]]}
    updated = tool._page_update(page=page_id, data=progress)
    assert _page_id(updated) == page_id and "version 2" in updated.splitlines()[0]
    page = pages.get(page_id)
    assert pages.read_data(page) == progress and pages.read_html(page) == PLAN  # The HTML carries over.
    assert page.state == {"ticked": {"announce": True}}
    assert page.decisions["venue"]["note"] == "Smaller rooms near the station"
    assert pages.read_data(page, 1) == STEPS  # The earlier version is kept.

    by_title = tool._page_update(page="launch plan", title="Beta launch plan")
    assert _page_id(by_title) == page_id and pages.get(page_id).title == "Beta launch plan"
    assert "Nothing to change" in tool._page_update(page=page_id)


def test_only_the_last_ten_versions_are_kept(chat):
    tool, pages, _current = chat
    page_id = _page_id(tool._page_create(title="Numbers", html=PLAN, data={"n": 0}))
    for number in range(1, 12):
        tool._page_update(page=page_id, data={"n": number})
    page = pages.get(page_id)
    assert page.version == 12 and [item["version"] for item in page.versions] == list(range(3, 13))
    assert pages.read_data(page, 3) == {"n": 2}
    with pytest.raises(pages.PageError):
        pages.read_data(page, 2)
    assert "isn't kept" in tool._page_read(page=page_id, version=1)


def test_page_read_returns_its_state_and_the_decisions_not_yet_sent(chat):
    tool, pages, _current = chat
    page_id = _page_id(tool._page_create(title="Launch plan", html=PLAN, data=STEPS))
    pages.set_state(page_id, {"ticked": {"announce": True}})
    pages.decide(page_id, "announce", "approve", "", "Draft the announcement")
    read = json.loads(tool._page_read(page=page_id))
    assert read["state"] == {"ticked": {"announce": True}}
    assert read["decisions_not_sent"] == [{"id": "announce", "choice": "approve", "note": "",
                                           "label": "Draft the announcement"}]
    assert "html" not in read
    assert json.loads(tool._page_read(page=page_id, include_html=True))["html"] == PLAN


def test_a_page_of_another_chat_is_never_found(chat):
    tool, _pages, current = chat
    page_id = _page_id(tool._page_create(title="Launch plan", html=PLAN))
    current["conversation"] = "chat-other"
    for result in (tool._page_read(page=page_id), tool._page_update(page=page_id, title="Mine now"),
                   tool._page_read(page="Launch plan")):
        assert result.startswith("There's no page"), result


def test_the_caps_hold(chat):
    tool, pages, _current = chat
    big = "<p>" + "x" * (1024 * 1024) + "</p>"
    assert "over 1 MB" in tool._page_create(title="Big", html=big)
    assert "over 1 MB" in tool._page_create(title="Big data", html=PLAN, data={"rows": "x" * (1024 * 1024)})
    assert "Unknown library" in tool._page_create(title="Maps", html=PLAN, libraries=["maps"])
    page_id = _page_id(tool._page_create(title="Launch plan", html=PLAN))
    with pytest.raises(pages.PageError) as state:
        pages.set_state(page_id, {"notes": "x" * (256 * 1024)})
    assert state.value.code == "chat_page_too_large"
    with pytest.raises(pages.PageError) as decision:
        pages.decide(page_id, "../../etc", "approve")
    assert decision.value.code == "chat_page_invalid"
    assert pages.get(page_id).state == {} and pages.get(page_id).decisions == {}


def test_a_page_s_writes_are_rate_limited(chat):
    tool, pages, _current = chat
    page_id = _page_id(tool._page_create(title="Launch plan", html=PLAN))
    for number in range(pages.WRITES_PER_MINUTE):
        pages.set_state(page_id, {"n": number})
    with pytest.raises(pages.PageError) as limited:
        pages.decide(page_id, "announce", "approve")
    assert limited.value.code == "chat_page_rate_limited"


def test_deleting_the_chat_removes_its_pages(chat):
    tool, pages, _current = chat
    page_id = _page_id(tool._page_create(title="Launch plan", html=PLAN))
    folder = pages._folder(pages.get(page_id))
    assert folder.is_dir()
    assert pages.delete_conversation("chat-launch") == 1
    assert pages.get(page_id) is None and not folder.exists()


def test_a_read_only_profile_can_read_a_page_but_not_change_one():
    from row_bot.tools.profile_policy import dispatch_refusal

    profile = {"enabled": True, "tool_policy_json": {"capability": "read_only"}}
    assert dispatch_refusal(profile, "page_read", {"page": "page-1a2b3c4d"}, source="core", parent="pages") is None
    for name in ("page_create", "page_update"):
        assert dispatch_refusal(profile, name, {}, source="core", parent="pages").startswith("BLOCKED")


BROKEN = """<h1>Edinburgh in 3 days</h1><div id="days"></div>
<script>
const days = ["Old Town", "New Town", "Leith"];
days.forEach((name) => {
  $('days').append(make('article', {class: 'day'}, make('p', {}, name))
});
</script>"""
FIXED = BROKEN.replace("make('p', {}, name))", "make('p', {}, name)))")


def test_a_script_that_wont_run_is_named_with_where_so_its_fixed_first(chat):
    """B423: a missing ')' left the page's sections empty, and nothing said why."""
    tool, pages, _current = chat
    made = tool._page_create(title="Edinburgh", html=BROKEN)

    assert "Warning: the page has a script that won't run" in made
    assert "script 1, line 5, column 19: '(' is never closed before a '}'" in made
    page = _page_id(made)
    assert "Warning" not in tool._page_update(page=page, html=FIXED), "fixed, nothing to say"


@pytest.mark.parametrize("html", [
    "<script>const re = /[(]/g; const t = `a ${b.map((x) => (x + 1)).join(')')} }`; // (\nif (a / b) { f(')'); }</script>",
    '<script type="application/json">{"a": [</script><script src="app.js"></script>',
    "<p>No scripts at all.</p>",
])
def test_scripts_that_run_raise_no_warning(chat, html):
    tool, _pages, _current = chat
    assert "Warning" not in tool._page_create(title="Fine", html=html)
