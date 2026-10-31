"""The agent's browser tools (N12-A): passwords, approvals (D19), the person's control, local addresses (F4).

Row-Bot never types a password, one-time code or card number. Paying, sending, deleting and the
rest ask by the approval level: Ask asks the person, Auto the reviewer first, Hands-off runs it,
Block refuses. The page is shared: while the person uses it the agent waits. An address on this computer opens
once allowed in that chat; the local network and Row-Bot itself never do. Nothing launches a
browser: the engine's page work is a fake that records what would have run.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from row_bot.approval_review import ReviewVerdict
from row_bot.browser import guard
from row_bot.browser import service as service_module
from row_bot.browser.service import ActionResult, BrowserEngine, BrowserSessionManager
from row_bot.cancellation import CancellationScope, use_cancellation_scope
from row_bot.tools import approval_gate, browser_tool

pytestmark = pytest.mark.subsystem

CHECKOUT = {"role": "button", "name": "Confirm and pay £60", "url": "https://www.homeserve.co.uk/claim/5",
            "secret": "", "card": True, "price": True, "search": False, "submit": "Confirm and pay £60"}
NEXT = {"role": "button", "name": "Next", "url": "https://www.homeserve.co.uk/claim/1", "secret": "",
        "card": False, "price": False, "search": False, "submit": "Next"}


class FakeEngine(BrowserEngine):
    """The real engine's grants, the person's use of the page and pane state; page work is recorded, not run."""

    def __init__(self, state) -> None:
        super().__init__()
        self.state = state

    def inspect(self, chat_id, ref):
        return dict(self.state.targets.get(ref, {})) or None

    def act(self, chat_id, label, step, *, settle=True, timeout=120.0):
        if not self.state.browser:
            raise RuntimeError("Row-Bot's browser couldn't start.")
        self.state.acts.append((chat_id, label))
        return ActionResult(f"{label}: done")

    def current_url(self, chat_id="default"):
        return self.state.url

    def model_picture(self, chat_id):
        return None


@pytest.fixture
def browser(monkeypatch):
    monkeypatch.setenv("ROW_BOT_PORT", "8080")
    state = SimpleNamespace(conversation="conversation-a", acts=[], asked=[], reviewed=[], mode="approve",
                            answer=True, verdict=ReviewVerdict("approve", "You asked to book the plumber."),
                            servers=[], targets={}, url="https://www.homeserve.co.uk/claim/5", browser=True)
    manager = BrowserSessionManager()
    engine = FakeEngine(state)
    manager._engine = engine
    monkeypatch.setattr(browser_tool, "_session_manager", manager)
    monkeypatch.setattr(browser_tool, "_get_thread_id", lambda: state.conversation)
    monkeypatch.setattr(approval_gate, "current_approval_mode", lambda: state.mode)
    monkeypatch.setattr("langgraph.types.interrupt", lambda payload: state.asked.append(payload) or state.answer)
    monkeypatch.setattr("row_bot.approval_review.review",
                        lambda payload, **_kw: state.reviewed.append(payload) or state.verdict)
    monkeypatch.setattr("row_bot.background_processes.local_servers",
                        lambda conversation_id=None: [server for server in state.servers
                                                      if conversation_id in (None, server["conversation_id"])])
    state.engine = engine
    yield state
    engine.close()


def _tool(name: str, **arguments) -> str:
    tools = {tool.name: tool for tool in browser_tool.BrowserTool().as_langchain_tools()}
    return tools[name].func(**arguments)


# ── Passwords, codes and card numbers ─────────────────────────────────────────


@pytest.mark.parametrize(("secret", "words"), [
    ("password", "never types passwords"), ("code", "one-time code"), ("card", "never types card numbers")])
def test_the_agent_never_types_a_password_code_or_card_number(browser, secret, words):
    browser.targets["e3"] = {**NEXT, "role": "textbox", "name": "Password", "secret": secret}

    result = _tool("browser_type", ref="e3", text="hunter2")

    assert words in result and "ask_person" in result
    assert browser.acts == []  # Nothing was typed.
    assert browser.engine.pane_state("conversation-a")["sign_in"] == secret  # The pane says "Sign in here".
    assert "Row-Bot never" in _tool("browser_press", ref="e3", key="a") or secret == "code"
    assert browser.acts == []
    _tool("browser_press", ref="e3", key="Tab")  # Moving on is fine.
    assert browser.acts == [("conversation-a", "Pressing Tab")]


# ── Approvals (D19) ───────────────────────────────────────────────────────────


def test_paying_asks_the_person_in_plain_words_and_runs_once_they_say_yes(browser):
    browser.targets["e9"] = CHECKOUT

    result = _tool("browser_click", ref="e9")

    [card] = browser.asked
    assert card["question"] == "Pay on homeserve.co.uk?"
    assert card["description"] == 'Row-Bot will click "Confirm and pay £60" on homeserve.co.uk. This can charge you.'
    assert result == 'Clicking button "Confirm and pay £60": done'


def test_a_payment_the_person_refuses_never_runs(browser):
    browser.targets["e9"] = CHECKOUT
    browser.answer = False

    result = _tool("browser_click", ref="e9")

    assert "The person said no" in result
    assert browser.acts == []
    assert browser.engine.pane_state("conversation-a")["waiting"] == ""


def test_under_auto_the_reviewer_decides_and_asks_the_person_when_unsure(browser):
    browser.targets["e9"] = CHECKOUT
    browser.mode = "auto"
    _tool("browser_click", ref="e9")
    assert browser.asked == [] and len(browser.reviewed) == 1 and len(browser.acts) == 1

    browser.verdict = ReviewVerdict("ask", "The person asked to check cover, not to pay.")
    _tool("browser_click", ref="e9")
    assert len(browser.asked) == 1 and len(browser.acts) == 2


def test_hands_off_runs_it_and_block_refuses_it(browser):
    browser.targets["e9"] = CHECKOUT
    browser.mode = "full"
    _tool("browser_click", ref="e9")
    assert browser.asked == [] and browser.reviewed == [] and len(browser.acts) == 1

    browser.mode = "block"
    result = _tool("browser_click", ref="e9")
    assert result.startswith("BLOCKED: Pay on homeserve.co.uk needs approval")
    assert len(browser.acts) == 1


def test_without_chrome_or_edge_the_browser_asks_once_to_download_its_own_then_goes_on(browser, monkeypatch):
    """5.3.1 beta: on a Mac without Chrome or Edge the browser only said it couldn't start; it now offers its own,
    as ffmpeg and the voice do."""
    from row_bot.browser import runtime

    browser.targets["e2"] = NEXT
    browser.browser = False
    downloads: list[str] = []

    def install(**_kw):
        downloads.append("chromium")
        browser.browser = True
        return runtime.BrowserRuntimeInstallResult(True, "Installed.")

    monkeypatch.setattr(service_module, "can_start", lambda: browser.browser)
    monkeypatch.setattr(runtime, "install_managed_browser_runtime", install)

    browser.answer = False
    refused = _tool("browser_click", ref="e2")
    assert "The person didn't allow downloading Row-Bot's browser; nothing was downloaded." in refused
    assert "Settings › System › Browser and computer use" in refused and downloads == []
    [card] = browser.asked
    assert card["question"] == ("Using websites needs a browser, and this computer has no Chrome or Edge. "
                                "Download Row-Bot's own (about 300 MB) now?")

    browser.mode = "block"
    assert _tool("browser_click", ref="e2").startswith("BLOCKED: using websites needs a browser")

    browser.mode, browser.answer = "approve", True
    done = _tool("browser_click", ref="e2")
    assert done.endswith('Clicking button "Next": done') and downloads == ["chromium"]
    assert _tool("browser_click", ref="e2") == 'Clicking button "Next": done', "once: it's there now"
    assert len(browser.asked) == 2


def test_a_browser_that_is_there_but_fails_to_start_offers_no_download(browser, monkeypatch):
    browser.targets["e2"] = NEXT
    browser.browser = False
    monkeypatch.setattr(service_module, "can_start", lambda: True)

    assert _tool("browser_click", ref="e2").startswith("Row-Bot's browser couldn't start.")
    assert browser.asked == []


def test_ordinary_steps_run_without_asking(browser):
    browser.targets["e2"] = NEXT
    browser.targets["e5"] = {**NEXT, "role": "radio", "name": "Leak"}
    _tool("browser_click", ref="e5")
    _tool("browser_click", ref="e2")
    _tool("browser_select", ref="e7", options=["Kitchen"])
    assert browser.asked == [] and len(browser.acts) == 3


def test_in_a_sandbox_auto_goes_ahead_ask_still_asks_and_block_refuses(browser, monkeypatch):
    """5.3.2: a demo stalled on "Send in the Demo tea shop sandbox?" each time Row-Bot sent a request to the
    sandbox's own Row-Bot; the reviewer took it for the customer's reply. A sandbox's made-up data never leaves this
    computer, so on Auto Row-Bot goes ahead as the person would."""
    from row_bot import sandboxes

    monkeypatch.setattr(sandboxes, "name_at", lambda url: "Demo tea shop" if ":62293/" in url else "")
    browser.targets["e5"] = {**NEXT, "role": "textbox", "name": "Message", "submit": "Send",
                             "url": "http://127.0.0.1:62293/app-v2/conversations/x"}
    for level in ("auto", "full"):
        browser.mode = level
        _tool("browser_type", ref="e5", text="Check today's orders", submit=True)
    assert browser.asked == [] and browser.reviewed == [] and len(browser.acts) == 2

    browser.mode = "approve"
    _tool("browser_type", ref="e5", text="Check today's orders", submit=True)
    assert browser.asked[-1]["question"] == "Send in the Demo tea shop sandbox?", "Ask still asks the person"
    browser.mode = "block"
    assert _tool("browser_type", ref="e5", text="Hello", submit=True).startswith("BLOCKED: Send in the Demo tea")

    browser.mode = "auto"
    browser.targets["e9"] = CHECKOUT  # Outside a sandbox the reviewer still decides.
    _tool("browser_click", ref="e9")
    assert len(browser.reviewed) == 1


def test_pressing_enter_in_a_message_box_asks_like_its_send_button(browser):
    browser.targets["e4"] = {**NEXT, "role": "textbox", "name": "Message", "submit": "Send",
                             "url": "https://mail.example.com/compose"}
    _tool("browser_type", ref="e4", text="On my way", submit=True)
    assert browser.asked[-1]["question"] == "Send on mail.example.com?"
    _tool("browser_type", ref="e4", text="On my way")
    assert len(browser.asked) == 1  # Typing alone never asks.


def test_uploading_a_file_asks_and_names_it(browser, monkeypatch):
    source = SimpleNamespace(name="leak.jpg", size_bytes=5, mime_type="image/jpeg", ref="chat:1", path=None)
    monkeypatch.setattr("row_bot.file_deliveries.resolve", lambda files, conversation: [source])
    monkeypatch.setattr(browser_tool, "_payload", lambda item: {"name": item.name, "mimeType": "image/jpeg",
                                                                 "buffer": b"12345"})
    _tool("browser_upload", ref="e12", files=["leak.jpg"])
    assert browser.asked[-1]["question"] == "Upload to homeserve.co.uk?"
    assert "leak.jpg" in browser.asked[-1]["description"]
    assert browser.acts == [("conversation-a", "Uploading leak.jpg")]


# ── The shared page: the person uses it whenever they like ─────────────────────


@pytest.fixture
def page_open(browser, monkeypatch):
    """A page open in the chat: the person's input is played (recorded), not refused."""

    monkeypatch.setattr(BrowserEngine, "running", property(lambda self: True))
    browser.engine._chat("conversation-a").active = SimpleNamespace(page=None)
    played: list[list[dict]] = []
    monkeypatch.setattr(browser.engine, "_run", lambda job, **_kw: played.append(True) or {"status": "ok",
                                                                                            "copied": ""})
    return played


def test_hovering_never_makes_row_bot_wait_and_the_person_is_never_refused(browser, page_open):
    engine = browser.engine
    move = {"type": "mouse", "action": "move", "x": 10, "y": 10}
    assert engine.person_input("conversation-a", [move, move])["status"] == "ok"
    assert engine.person_still_for("conversation-a") == float("inf")  # Hovering isn't using the page.
    # Mid-action too: the person is never told to wait or take anything over.
    engine._chat("conversation-a").acting = 'Clicking button "Next"'
    assert engine.person_input("conversation-a", [{"type": "key", "action": "down", "key": "a"}])["status"] == "ok"
    assert engine.person_still_for("conversation-a") < 1
    assert len(page_open) == 2


def test_while_the_person_uses_the_page_the_agent_waits_until_they_are_still(browser, page_open, monkeypatch):
    monkeypatch.setattr(service_module, "PERSON_STILL_SECONDS", 0.4)
    browser.targets["e2"] = NEXT
    engine = browser.engine
    engine.person_input("conversation-a", [{"type": "mouse", "action": "down", "x": 5, "y": 5}])
    waiting: list[str] = []

    def look() -> None:
        time.sleep(0.15)
        waiting.append(engine.pane_state("conversation-a")["waiting"])

    threading.Thread(target=look).start()
    started = time.monotonic()
    result = _tool("browser_click", ref="e2")

    assert waiting == ["Waiting while you use the page"]
    assert time.monotonic() - started >= 0.2
    assert result.endswith(": done") and engine.pane_state("conversation-a")["waiting"] == ""


def test_stop_ends_the_wait_while_the_person_uses_the_page(browser, page_open):
    browser.targets["e2"] = NEXT
    browser.engine.person_input("conversation-a", [{"type": "key", "action": "down", "key": "a"}])
    scope = CancellationScope()
    threading.Timer(0.2, scope.cancel).start()
    with use_cancellation_scope(scope):
        result = _tool("browser_click", ref="e2")
    assert result == service_module.STOPPED and browser.acts == []


def test_without_a_page_the_persons_input_has_nothing_to_play(browser):
    result = browser.engine.person_input("conversation-a", [{"type": "key", "action": "down", "key": "a"}])
    assert result == {"status": "no_page", "copied": ""}


# ── Addresses on this computer (F4) ───────────────────────────────────────────


def test_ask_asks_about_one_address_once_then_it_opens(browser):
    _tool("browser_navigate", url="localhost:5173")
    assert browser.asked[-1]["question"] == "Allow Row-Bot's browser to open localhost:5173 in this chat?"
    assert browser.acts == [("conversation-a", "Opening http://localhost:5173")]
    _tool("browser_navigate", url="http://localhost:5173/settings")
    assert len(browser.asked) == 1 and len(browser.acts) == 2
    [grant] = browser.engine.local_address_grants("conversation-a")
    assert (grant.address, grant.source) == ("localhost:5173", "you")


def test_a_file_address_says_to_serve_its_folder_and_asks_nothing(browser):
    # A file:/// address is not a host called "file" on the person's network.
    result = _tool("browser_navigate", url="file:///D:/Shop/workspace/hello.html")

    assert result.startswith("BLOCKED: Files don't open in Row-Bot's browser.") and "http.server" in result
    assert browser.asked == [] and browser.acts == []


def test_a_denied_address_never_opens(browser):
    browser.answer = False
    result = _tool("browser_navigate", url="http://localhost:5173")
    assert "didn't allow localhost:5173" in result and browser.acts == []


def test_a_grant_covers_one_origin_in_one_conversation(browser):
    _tool("browser_navigate", url="http://localhost:5173")
    granted = browser.engine._granted
    assert guard.allowed("http://localhost:5173/app", "conversation-a", granted)
    assert guard.allowed("ws://localhost:5173/hmr", "conversation-a", granted)
    assert guard.allowed("http://localhost:5173/api", "conversation-a", granted, "POST")  # Forms work too.
    assert not guard.allowed("http://localhost:5174/", "conversation-a", granted)
    assert not guard.allowed("http://localhost:5173/", "conversation-b", granted)
    browser.conversation = "conversation-b"
    _tool("browser_navigate", url="http://localhost:5173")
    assert len(browser.asked) == 2


def test_auto_and_hands_off_allow_it_and_record_who_did(browser):
    browser.mode = "auto"
    _tool("browser_navigate", url="http://localhost:5173")
    browser.mode = "full"
    _tool("browser_navigate", url="http://localhost:3000")
    assert browser.asked == []
    assert [(g.address, g.source) for g in browser.engine.local_address_grants("conversation-a")] == [
        ("localhost:5173", "reviewer"), ("localhost:3000", "hands_off")]


def test_block_refuses_a_local_address(browser):
    browser.mode = "block"
    assert _tool("browser_navigate", url="http://localhost:5173").startswith("BLOCKED: Opening localhost:5173")
    assert browser.acts == []


def test_a_server_row_bot_started_in_this_chat_opens_without_asking(browser):
    browser.servers = [{"origin": "http://localhost:5173", "port": 5173, "process_id": "proc-0000000a",
                        "conversation_id": "conversation-a", "verified": True}]
    result = _tool("browser_navigate", url="http://localhost:5173")
    assert result.startswith("Opened without asking: Row-Bot started the server at localhost:5173")
    assert browser.asked == []


@pytest.mark.parametrize("mode", ["block", "approve", "auto", "full"])
@pytest.mark.parametrize("url", ["http://169.254.169.254/latest/meta-data/", "http://metadata.google.internal/",
                                 "http://[fe80::1]/"])
def test_a_cloud_metadata_address_never_opens_at_any_level(browser, mode, url):
    browser.mode = mode
    result = _tool("browser_navigate", url=url)
    assert result.startswith("BLOCKED: Row-Bot's browser never opens a cloud metadata address")
    assert browser.asked == [] and browser.reviewed == [] and browser.acts == []


@pytest.mark.parametrize(("url", "address"), [
    ("http://192.168.1.1/", "192.168.1.1"), ("http://10.0.0.20:8080/", "10.0.0.20:8080"),
    ("http://[fd00::1]:3000/", "[fd00::1]:3000"), ("http://printer.local/", "printer.local"),
    ("http://100.101.102.103:3000/", "100.101.102.103:3000"), ("http://jira/", "jira"),
])
def test_an_address_on_the_persons_network_asks_once_like_localhost(browser, url, address):
    """5.3.1 B3: a router, a NAS, another computer, Tailscale or an intranet name; asked once per chat."""
    _tool("browser_navigate", url=url)
    assert browser.asked[-1]["label"] == "Open an address on your network"
    assert browser.asked[-1]["question"] == f"Allow Row-Bot's browser to open {address} in this chat?"
    assert len(browser.acts) == 1
    _tool("browser_navigate", url=url)
    assert len(browser.asked) == 1
    granted = browser.engine._granted
    assert guard.allowed(url, "conversation-a", granted)
    assert not guard.allowed(url, "conversation-b", granted)


def test_block_refuses_an_address_on_the_network_and_hands_off_opens_it(browser):
    browser.mode = "block"
    assert _tool("browser_navigate", url="http://192.168.1.1/").startswith("BLOCKED: Opening 192.168.1.1")
    browser.mode = "full"
    _tool("browser_navigate", url="http://192.168.1.1/")
    assert browser.asked == [] and len(browser.acts) == 1


@pytest.mark.parametrize("mode", ["approve", "full"])
def test_row_bot_itself_never_opens_in_its_browser(browser, mode):
    browser.mode = mode
    assert _tool("browser_navigate", url="http://localhost:8080/app-v2/").startswith(
        "BLOCKED: localhost:8080 is Row-Bot itself.")
    with pytest.raises(ValueError):
        browser.engine.allow_local_address("conversation-a", "http://127.0.0.1:8080", source="you")


def test_a_grant_can_be_removed_runs_out_after_an_hour_and_ends_with_its_conversation(browser, monkeypatch):
    _tool("browser_navigate", url="http://localhost:5173")
    _tool("browser_navigate", url="http://localhost:3000")
    first, second = browser.engine.local_address_grants("conversation-a")
    assert second.expires_at - second.granted_at == 3600
    assert browser.engine.revoke_local_address("conversation-b", first.id) is False
    assert browser.engine.revoke_local_address("conversation-a", first.id) is True
    assert [g.address for g in browser.engine.local_address_grants("conversation-a")] == ["localhost:3000"]

    now = time.time()
    monkeypatch.setattr(service_module.time, "time", lambda: now + 3601)
    assert browser.engine.local_address_grants("conversation-a") == []
    browser.engine.allow_local_address("conversation-a", "http://localhost:3000", source="you")
    assert browser.engine.revoke_local_addresses("conversation-a") == 1


@pytest.mark.parametrize("url", ["http://0.0.0.0:5173/", "http://[::]:5173/"])
def test_an_every_address_bind_says_to_open_localhost(browser, url):
    assert _tool("browser_navigate", url=url).endswith("Open http://localhost:5173 instead.")
    assert browser.asked == [] and browser.acts == []
