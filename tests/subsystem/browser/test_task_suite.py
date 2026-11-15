"""Row-Bot's browser on real tasks (N12-A): the agent's own tools on local fixture sites in real Chromium.

Each journey drives the tools as the agent does (reading refs from each result) and counts its
steps. The person's part (typing a password or card number in the Browser pane) goes through the
pane's own input path. Approvals are faked at the person and the reviewer, as the gate sees them.
Loopback only; skipped where no browser runtime is installed.
"""

from __future__ import annotations

import io
import re
from types import SimpleNamespace

import pytest

from tests.fixtures.browser_sites import PASSWORD, Sites

pytestmark = [pytest.mark.subsystem, pytest.mark.slow]


@pytest.fixture
def world(tmp_path, monkeypatch):
    from row_bot import threads
    from row_bot.agent import _current_thread_id_var
    from row_bot.approval_review import ReviewVerdict
    from row_bot.browser import service
    from row_bot.browser.runtime import check_managed_browser_runtime, check_packaged_browser_runtime
    from row_bot.tools import approval_gate, browser_tool

    if service._installed_channel() is None and not (
        check_packaged_browser_runtime().ready or check_managed_browser_runtime().ready
    ):
        pytest.skip("No browser runtime is installed")
    monkeypatch.setenv("ROW_BOT_PORT", "8080")
    monkeypatch.setattr(service, "PROFILE_DIR", tmp_path / "profile")
    # The person's typing holds Row-Bot's next step until they're still; a short still keeps the suite quick.
    monkeypatch.setattr(service, "PERSON_STILL_SECONDS", 0.3)
    state = SimpleNamespace(mode="ask", answer=True, asked=[], reviewed=[], steps=0,
                            verdict=ReviewVerdict("approve", "The person asked to book the plumber and pay the excess."))
    monkeypatch.setattr(approval_gate, "current_approval_mode", lambda: state.mode)
    monkeypatch.setattr("langgraph.types.interrupt", lambda payload: state.asked.append(payload) or state.answer)
    monkeypatch.setattr("row_bot.approval_review.review",
                        lambda payload, **_kw: state.reviewed.append(payload) or state.verdict)
    manager = service.BrowserSessionManager()
    monkeypatch.setattr(browser_tool, "_session_manager", manager)
    conversation = threads.create_thread("Book a plumber", seed_default_skills=False)
    token = _current_thread_id_var.set(conversation)
    with Sites(slow_seconds=1.5) as sites:
        state.sites, state.chat, state.manager = sites, conversation, manager
        state.engine = manager.get_session()
        for origin in (sites.service, sites.auth):
            state.engine.allow_local_address(conversation, origin, source="you")

        def agent(tool: str, **arguments):
            state.steps += 1
            return getattr(browser_tool, tool)(**arguments)

        state.agent = agent
        yield state
        manager.kill_all()
    _current_thread_id_var.reset(token)


def ref(result: str, pattern: str) -> str:
    for line in result.splitlines():
        if re.search(pattern, line):
            found = re.search(r"\[ref=(e\d+)\]", line)
            if found:
                return found.group(1)
    raise AssertionError(f"Nothing matches {pattern!r} in:\n{result}")


def person_types(world, selector: str, text: str, *, frame: str = "", enter: bool = True) -> None:
    """The person clicks a field in the Browser pane and types (the pane's own input path)."""

    engine = world.engine

    async def where():
        tab = engine._chat(world.chat).active
        target = next(f for f in tab.page.frames if frame in f.url) if frame else tab.page
        return await target.locator(selector).bounding_box()

    box = engine._run(where)
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    events = [{"type": "mouse", "action": "down", "x": x, "y": y, "button": "left", "clicks": 1},
              {"type": "mouse", "action": "up", "x": x, "y": y, "button": "left", "clicks": 1}]
    for character in text:
        events += [{"type": "key", "action": "down", "key": character}, {"type": "key", "action": "up", "key": character}]
    if enter:
        events += [{"type": "key", "action": "down", "key": "Enter"}, {"type": "key", "action": "up", "key": "Enter"}]
    assert engine.person_input(world.chat, events)["status"] == "ok"
    assert engine.person_still_for(world.chat) < 1  # Row-Bot's next step waits until they're still.


def sign_in(world) -> str:
    """The agent opens the account; the page asks for a password; the person signs in in the pane."""

    page = world.agent("browser_navigate", url=world.sites.service + "/account")
    assert "Logging you in" in page and 'textbox "Password"' in page
    refused = world.agent("browser_type", ref=ref(page, r'textbox "Password"'), text="guess")
    assert "never types passwords" in refused
    assert world.engine.pane_state(world.chat)["sign_in"] == "password"
    person_types(world, "#password", PASSWORD, frame="/widget")
    return world.agent("browser_wait_for", text="Your cover")


def test_signing_in_works_through_redirects_a_service_worker_and_an_iframe_and_lasts(world):
    """The HomeServe shape: it signs in, and stays signed in after the browser restarts."""

    page = sign_in(world)
    assert "Signed in as sam@example.com" in page
    assert world.sites.state.worker_fetches >= 1  # The service worker ran.
    assert world.sites.state.paid == [] and world.asked == []
    assert world.steps <= 4

    world.manager.kill_all()  # Row-Bot quits; the profile stays.
    world.engine = world.manager.get_session()
    page = world.agent("browser_navigate", url=world.sites.service + "/account")
    assert "Signed in as sam@example.com" in page


def test_a_multi_step_claim_from_problem_to_payment_and_summary(world):
    from row_bot.application.attachments import list_conversation_files, register_attachment
    from PIL import Image

    photo = io.BytesIO()
    Image.new("RGB", (64, 48), (200, 220, 240)).save(photo, format="JPEG")
    register_attachment(world.chat, "leak.jpg", photo.getvalue())
    page = sign_in(world)
    start = world.steps

    page = world.agent("browser_click", ref=ref(page, r'link "Make a claim"'))
    page = world.agent("browser_click", ref=ref(page, r'radio "Leak"'))
    page = world.agent("browser_click", ref=ref(page, r'button "Next"'))
    assert "Where is it?" in page
    page = world.agent("browser_select", ref=ref(page, r'combobox "Room"'), options=["Kitchen"])
    page = world.agent("browser_type", ref=ref(page, r"textbox \"What's happening"), text="Leak under the sink")
    page = world.agent("browser_click", ref=ref(page, r'button "Next"'))
    assert "Add a photo" in page

    page = world.agent("browser_upload", ref=ref(page, r'button "Photo of the problem"'), files=["leak.jpg"])
    assert world.asked[-1]["question"] == "Upload to 127.0.0.1?"
    assert "Uploaded leak.jpg" in page
    page = world.agent("browser_click", ref=ref(page, r'button "Next"'))
    assert world.sites.state.uploads and world.sites.state.uploads[0][0] == "leak.jpg"

    # The slots load slowly; the engineer's profile opens in a pop-up.
    page = world.agent("browser_wait_for", text="Available slots")
    page = world.agent("browser_click", ref=ref(page, r'button "View engineer profile"'))
    assert "A new tab opened" in page and "Alex Morgan" in page
    page = world.agent("browser_tabs", action="close", tab="t2")
    assert "Choose a visit" in page
    page = world.agent("browser_click", ref=ref(page, r'button "Tomorrow 8am'))
    assert "Excess to pay: £60.00" in page and "asks for card details" in page

    # Card details are the person's to type; paying asks first.
    refused = world.agent("browser_type", ref=ref(page, r'textbox "Card number"'), text="4242424242424242")
    assert "never types card numbers" in refused
    person_types(world, "#card", "4242424242424242", enter=False)
    person_types(world, "#expiry", "12/30", enter=False)
    person_types(world, "#cvc", "123", enter=False)
    asked_before = len(world.asked)
    page = world.agent("browser_click", ref=ref(page, r'button "Confirm and pay'))
    payment = world.asked[asked_before]
    assert payment["question"] == "Pay on 127.0.0.1?"
    assert 'click "Confirm and pay £60"' in payment["description"] and "charge you" in payment["description"]
    assert world.sites.state.paid == ["4242424242424242"]
    assert "Your claim is booked" in page

    page = world.agent("browser_download", ref=ref(page, r'link "Download your claim summary"'))
    assert "Downloaded claim-HC-1042.pdf" in page
    assert any(item["name"] == "claim-HC-1042.pdf" for item in list_conversation_files(world.chat))

    steps = world.steps - start
    assert steps <= 16, f"{steps} steps"
    # Only the upload and the payment asked.
    assert [entry["action"] for entry in world.asked] == ["upload", "pay"]


def _to_payment(world) -> str:
    page = sign_in(world)
    page = world.agent("browser_navigate", url=world.sites.service + "/claim/4")
    page = world.agent("browser_wait_for", text="Available slots")
    page = world.agent("browser_click", ref=ref(page, r'button "Tomorrow 8am'))
    person_types(world, "#card", "4242424242424242", enter=False)
    return page


def test_under_auto_the_reviewer_approves_a_payment_that_matches_the_request(world):
    world.mode = "auto"
    page = _to_payment(world)
    page = world.agent("browser_click", ref=ref(page, r'button "Confirm and pay'))
    assert world.asked == []  # The person wasn't asked.
    assert world.reviewed and world.reviewed[-1]["action"] == "pay"
    assert world.sites.state.paid == ["4242424242424242"] and "Your claim is booked" in page


def test_a_payment_the_person_refuses_never_happens(world):
    page = _to_payment(world)
    world.answer = False
    result = world.agent("browser_click", ref=ref(page, r'button "Confirm and pay'))
    assert "didn't click it" in result
    assert world.sites.state.paid == []


def test_a_page_can_reach_only_the_local_addresses_its_chat_allowed(world):
    """A granted page that redirects or fetches to another local address gets nothing from it."""

    with Sites() as other:
        page = world.agent("browser_navigate", url=world.sites.service + "/")
        assert "HomeCover" in page

        async def fetch_other():
            tab = world.engine._chat(world.chat).active
            return await tab.page.evaluate(
                "url => fetch(url).then(r => 'reached', () => 'blocked')", other.service + "/api/status")

        assert world.engine._run(fetch_other) == "blocked"
        assert other.state.requests == []

        async def go_other():
            tab = world.engine._chat(world.chat).active
            try:
                await tab.page.goto(other.service + "/", timeout=10_000)
            except Exception as exc:
                return str(exc)
            return "opened"

        assert "ERR_BLOCKED_BY_CLIENT" in world.engine._run(go_other)
        assert other.state.requests == []
