"""What Row-Bot's browser asks before doing (D19), in plain words.

Paying, buying, booking with a charge, sending, posting, deleting and changing account settings
ask; everything else (the steps of a form, links, search) just runs.
"""

from __future__ import annotations

import pytest

from row_bot.browser.policy import PageFacts, approval_request, classify, navigation_policy, upload_consequence

pytestmark = pytest.mark.subsystem

CLAIM = PageFacts(url="https://www.homeserve.co.uk/claim/2", title="Make a claim")
CHECKOUT = PageFacts(url="https://www.homeserve.co.uk/claim/5", card_fields=True, shows_price=True)


@pytest.mark.parametrize(("control", "role", "page", "kind"), [
    ("Confirm and pay £60", "button", CHECKOUT, "pay"),
    ("Buy now", "button", CLAIM, "pay"),
    ("Place order", "button", CLAIM, "pay"),
    ("Continue", "button", CHECKOUT, "pay"),  # Card details on the form: going on pays.
    ("Book appointment", "button", PageFacts(url="https://dentist.example/book", shows_price=True), "book"),
    ("Send", "button", CLAIM, "send"),
    ("Post", "button", CLAIM, "send"),
    ("Submit claim", "button", CLAIM, "send"),
    ("Delete account", "button", CLAIM, "delete"),
    ("Cancel subscription", "button", CLAIM, "delete"),
    ("Change password", "button", CLAIM, "account"),
    ("Save changes", "button", PageFacts(url="https://github.com/settings/profile"), "account"),
    ("Pay now", "link", CHECKOUT, "pay"),
])
def test_consequential_actions_ask(control, role, page, kind):
    consequence = classify(action="click", control=control, role=role, page=page)
    assert consequence is not None and consequence.kind == kind


@pytest.mark.parametrize(("action", "control", "role", "page"), [
    ("click", "Make a claim", "link", CLAIM),
    ("click", "Leak", "radio", CLAIM),
    ("click", "Next", "button", CLAIM),
    ("click", "Book a plumber", "button", PageFacts(url="https://www.homeserve.co.uk/")),  # No charge shown.
    ("click", "Send us a message", "link", CLAIM),  # A link opens the page; its button asks.
    ("click", "Save changes", "button", CLAIM),  # Not an account page.
    ("submit", "Search", "searchbox", PageFacts(url="https://www.google.com/", search_form=True)),
    ("select", "Kitchen", "combobox", CHECKOUT),
    ("click", "Post code", "textbox", CLAIM),
])
def test_ordinary_steps_just_run(action, control, role, page):
    assert classify(action=action, control=control, role=role, page=page) is None


def test_enter_in_a_field_is_judged_by_the_forms_own_button():
    page = PageFacts(url="https://mail.example.com/compose", form_submit_label="Send")
    consequence = classify(action="submit", control="Message", role="textbox", page=page)
    assert consequence is not None and consequence.kind == "send"


def test_the_card_says_what_will_happen_on_which_site():
    consequence = classify(action="click", control="Confirm and pay £60", page=CHECKOUT)
    card = approval_request("browser_click", consequence, url="https://www.homeserve.co.uk/claim/5?step=pay")

    assert card["question"] == "Pay on homeserve.co.uk?"
    assert card["description"] == 'Row-Bot will click "Confirm and pay £60" on homeserve.co.uk. This can charge you.'
    assert card["origin_and_path"] == "https://www.homeserve.co.uk/claim/5"  # No query in the card.
    assert card["risk_class"] == "high"
    upload = approval_request("browser_upload", upload_consequence(CLAIM.url, ["leak.jpg"]), url=CLAIM.url,
                              verb="upload")
    assert upload["question"] == "Upload to homeserve.co.uk?"
    assert upload["description"].startswith("Row-Bot will upload leak.jpg on homeserve.co.uk.")


def test_a_send_in_a_sandbox_just_runs_and_its_other_asks_name_the_sandbox(monkeypatch):
    """Sending in a sandbox (to its Row-Bot's chat, or a made-up customer) reaches no one, so it never waits on
    a card mid-demo; paying or deleting there still asks, saying nothing leaves the computer."""
    from row_bot import sandboxes

    page = PageFacts(url="http://127.0.0.1:62293/app-v2/conversations/x", title="Row-Bot")
    monkeypatch.setattr(sandboxes, "name_at", lambda url: "Fernleaf Teas" if ":62293/" in url else "")
    assert classify(action="click", control="Send", page=page) is None
    assert classify(action="submit", control="Message", page=PageFacts(
        url=page.url, title="Row-Bot", form_submit_label="Send")) is None

    card = approval_request("browser_click", classify(action="click", control="Delete order", page=page),
                            url=page.url)
    assert card["question"] == "Delete in the Fernleaf Teas sandbox?"
    assert card["description"] == ('Row-Bot will click "Delete order" in the Fernleaf Teas sandbox. It stays in '
                                   "the sandbox's made-up data: nothing leaves this computer.")
    elsewhere = PageFacts(url="http://127.0.0.1:8000/", title="Shop")
    assert classify(action="click", control="Send", page=elsewhere).question() == "Send on 127.0.0.1?"


@pytest.mark.parametrize(("url", "decision"), [
    ("https://accounts.example.com/sign-in?continue=https%3A%2F%2Fapp.example.com%2F&session=abc123", "allow"),
    ("https://www.google.com/search?q=" + "a" * 900, "allow"),  # Long sign-in and search addresses open.
    ("https://user:secret@example.com/", "block"),
    ("ftp://example.com/file", "block"),
    ("javascript:alert(1)", "block"),
])
def test_navigation_refuses_only_what_no_browser_should_open(url, decision):
    assert navigation_policy(url)[0] == decision


def test_a_file_page_says_to_serve_its_folder_instead():
    decision, why = navigation_policy("file:///C:/Users/me/site/index.html")

    assert decision == "block" and "python -m http.server 8000" in why and "opens without asking" in why
