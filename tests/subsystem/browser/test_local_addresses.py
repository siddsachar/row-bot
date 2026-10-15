"""Row-Bot's browser opens an address on this computer only once it's allowed in that chat (F4, D5).

Ask asks the person, Auto asks the reviewer first, Hands-off allows it and Block refuses. A grant
covers one origin in one conversation, for an hour; a server Row-Bot itself started in the chat
and listening on the port opens without asking. Other private-network addresses (other computers,
routers, cloud metadata) and Row-Bot's own app never open, at any level. Nothing launches a
browser here: navigation is a fake that asks the real network policy whether the page may load.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from row_bot.approval_review import ReviewVerdict
from row_bot.browser.service import BrowserSessionManager
from row_bot.tools import approval_gate, browser_tool

pytestmark = pytest.mark.subsystem


@pytest.fixture
def browser(monkeypatch):
    monkeypatch.setenv("ROW_BOT_PORT", "8080")
    manager = BrowserSessionManager()
    monkeypatch.setattr(browser_tool, "_session_manager", manager)
    state = SimpleNamespace(conversation="conversation-a", opened=[], asked=[], reviewed=[], mode="approve",
                            answer=True, verdict=ReviewVerdict("approve", "You asked to check your app."),
                            servers=[])
    monkeypatch.setattr(browser_tool, "_get_thread_id", lambda: state.conversation)
    monkeypatch.setattr(approval_gate, "current_approval_mode", lambda: state.mode)
    monkeypatch.setattr("langgraph.types.interrupt", lambda payload: state.asked.append(payload) or state.answer)
    monkeypatch.setattr("row_bot.approval_review.review",
                        lambda payload, **_kw: state.reviewed.append(payload) or state.verdict)
    monkeypatch.setattr("row_bot.background_processes.local_servers",
                        lambda conversation_id=None: [server for server in state.servers
                                                      if conversation_id in (None, server["conversation_id"])])
    session = manager.get_session(state.conversation)

    def navigate(url: str, thread_id: str = "default") -> str:
        decision = session._network_security.authorize(
            url, browser_session_id=session._browser_security_session_id, scope=thread_id)
        state.opened.append((thread_id, url, decision.allowed))
        return f"URL: {url}"

    monkeypatch.setattr(session, "navigate", navigate)
    state.session = session
    yield state
    manager.kill_all()


def _open(url: str) -> str:
    tools = {tool.name: tool for tool in browser_tool.BrowserTool().as_langchain_tools()}
    return tools["browser_navigate"].func(url)


def test_ask_asks_the_person_about_that_one_address_and_then_opens_it(browser):
    result = _open("localhost:5173/settings")

    [asked] = browser.asked
    assert asked["question"] == "Allow Row-Bot's browser to open localhost:5173 in this chat?"
    assert "only this address, only in this chat, for up to an hour" in asked["description"]
    assert browser.opened == [("conversation-a", "http://localhost:5173/settings", True)]
    assert result.endswith("URL: http://localhost:5173/settings")
    [grant] = browser.session.local_address_grants("conversation-a")
    assert (grant.address, grant.source) == ("localhost:5173", "you")

    # Allowed now: the same address in the same chat doesn't ask again.
    _open("http://localhost:5173/other")
    assert len(browser.asked) == 1


def test_a_denied_address_never_opens(browser):
    browser.answer = False
    result = _open("http://localhost:5173")

    assert "didn't allow localhost:5173" in result
    assert browser.opened == [] and browser.session.local_address_grants("conversation-a") == []


def test_the_grant_covers_one_origin_in_one_conversation(browser):
    _open("http://localhost:5173")
    assert len(browser.asked) == 1

    _open("http://localhost:5174")  # another port is another address
    assert len(browser.asked) == 2
    browser.conversation = "conversation-b"  # another chat asks again
    _open("http://localhost:5173")
    assert len(browser.asked) == 3

    # The network layer agrees: conversation B's grant doesn't open A's other page, and so on.
    policy, session_id = browser.session._network_security, browser.session._browser_security_session_id
    assert policy.authorize("http://localhost:5173/", browser_session_id=session_id, scope="conversation-b").allowed
    assert not policy.authorize("http://localhost:5174/", browser_session_id=session_id,
                                scope="conversation-b").allowed
    assert not policy.authorize("http://localhost:5173/", browser_session_id=session_id,
                                scope="conversation-c").allowed


def test_auto_lets_the_reviewer_allow_it_and_records_who_did(browser):
    browser.mode = "auto"
    _open("http://127.0.0.1:3000")

    assert browser.asked == []
    [reviewed] = browser.reviewed
    assert reviewed["question"] == "Allow Row-Bot's browser to open 127.0.0.1:3000 in this chat?"
    assert browser.opened == [("conversation-a", "http://127.0.0.1:3000", True)]
    assert [grant.source for grant in browser.session.local_address_grants("conversation-a")] == ["reviewer"]


def test_auto_asks_the_person_when_the_reviewer_does(browser):
    browser.mode = "auto"
    browser.verdict = ReviewVerdict("ask", "You didn't mention a local app.")
    browser.answer = False
    _open("http://localhost:5173")

    [asked] = browser.asked
    assert asked["review"] == {"decision": "ask", "reason": "You didn't mention a local app."}
    assert browser.opened == []


def test_hands_off_allows_it_without_asking(browser):
    browser.mode = "full"
    _open("http://[::1]:8000/")

    assert browser.asked == [] and browser.reviewed == []
    assert browser.opened == [("conversation-a", "http://[::1]:8000/", True)]
    assert [grant.source for grant in browser.session.local_address_grants("conversation-a")] == ["hands_off"]


def test_block_refuses_without_asking(browser):
    browser.mode = "block"
    result = _open("http://localhost:5173")

    assert result.startswith("BLOCKED: Opening localhost:5173 needs approval, and this chat is on Block.")
    assert browser.asked == [] and browser.opened == []


def test_a_server_row_bot_started_here_opens_without_asking(browser):
    browser.servers = [{"origin": "http://localhost:5173", "port": 5173, "process_id": "proc-0000000a",
                        "conversation_id": "conversation-a", "verified": True}]
    result = _open("http://localhost:5173")

    assert browser.asked == [] and browser.reviewed == []
    assert result.startswith("Opened without asking: Row-Bot started the server at localhost:5173 in this chat.")
    assert browser.opened == [("conversation-a", "http://localhost:5173", True)]
    assert [grant.source for grant in browser.session.local_address_grants("conversation-a")] == ["row_bot_server"]


@pytest.mark.parametrize("server", [
    # It printed the address but doesn't listen there: a printed address isn't proof.
    {"conversation_id": "conversation-a", "verified": False},
    # Listening, but started in another conversation.
    {"conversation_id": "conversation-b", "verified": True},
])
def test_a_printed_address_or_another_chats_server_still_asks(browser, server):
    browser.servers = [{"origin": "http://localhost:5173", "port": 5173, "process_id": "proc-0000000a", **server}]
    _open("http://localhost:5173")

    assert len(browser.asked) == 1


@pytest.mark.parametrize("mode", ["block", "approve", "auto", "full"])
@pytest.mark.parametrize("url", [
    "http://192.168.1.1/",  # a router page
    "http://10.0.0.20:8080/",  # another computer
    "http://169.254.169.254/latest/meta-data/",  # cloud metadata
    "http://[fd00::1]:3000/",
])
def test_other_private_network_addresses_stay_blocked_at_every_level(browser, mode, url):
    browser.mode = mode
    result = _open(url)

    assert result.startswith("BLOCKED: Row-Bot's browser can't open addresses on your local network")
    assert browser.asked == [] and browser.reviewed == [] and browser.opened == []
    assert browser.session.local_address_grants("conversation-a") == []


@pytest.mark.parametrize("mode", ["approve", "full"])
def test_row_bot_itself_never_opens_in_its_browser(browser, mode):
    browser.mode = mode
    result = _open("http://localhost:8080/app-v2/")

    assert result.startswith("BLOCKED: localhost:8080 is Row-Bot itself.")
    assert browser.asked == [] and browser.opened == []
    with pytest.raises(ValueError):
        browser.session.allow_local_address("conversation-a", "http://127.0.0.1:8080", source="you")


def test_a_grant_can_be_removed_and_ends_with_its_conversation(browser):
    _open("http://localhost:5173")
    _open("http://localhost:3000")
    first, second = browser.session.local_address_grants("conversation-a")

    assert browser.session.revoke_local_address("conversation-b", first.id) is False  # not that chat's
    assert browser.session.revoke_local_address("conversation-a", first.id) is True
    assert [grant.address for grant in browser.session.local_address_grants("conversation-a")] == ["localhost:3000"]
    _open("http://localhost:5173")  # removed: it asks again
    assert len(browser.asked) == 3

    assert browser.session.revoke_local_addresses("conversation-a") == 2
    policy, session_id = browser.session._network_security, browser.session._browser_security_session_id
    assert not policy.authorize("http://localhost:3000/", browser_session_id=session_id,
                                scope="conversation-a").allowed


def test_a_grant_runs_out_after_an_hour(browser, monkeypatch):
    _open("http://localhost:5173")
    [grant] = browser.session.local_address_grants("conversation-a")
    assert grant.expires_at - grant.granted_at == 3600

    clock = browser.session._network_security._clock
    monkeypatch.setattr(browser.session._network_security, "_clock", lambda: clock() + 3601)
    assert browser.session.local_address_grants("conversation-a") == []
    _open("http://localhost:5173")
    assert len(browser.asked) == 2


def test_the_live_reload_socket_of_an_allowed_server_is_covered_too(browser):
    _open("http://localhost:5173")
    policy, session_id = browser.session._network_security, browser.session._browser_security_session_id

    assert policy.authorize("ws://localhost:5173/", browser_session_id=session_id, scope="conversation-a").allowed
    assert not policy.authorize("ws://localhost:5174/", browser_session_id=session_id,
                                scope="conversation-a").allowed
    # Only reads: a grant never lets a page post forms or upload to the server.
    assert not policy.authorize("http://localhost:5173/api", browser_session_id=session_id, method="POST",
                                scope="conversation-a").allowed


@pytest.mark.parametrize("url", ["http://0.0.0.0:5173/", "http://[::]:5173/"])
def test_an_every_address_bind_says_to_open_localhost(browser, url):
    result = _open(url)

    assert result.endswith("Open http://localhost:5173 instead.")
    assert browser.asked == [] and browser.opened == []
