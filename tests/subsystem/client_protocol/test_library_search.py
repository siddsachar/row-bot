"""The Library's search for a word in a chat's title or messages: newest first, bounded per page. Fakes only."""
from __future__ import annotations

import pytest

from tests.subsystem.client_protocol.test_protocol_application import _client, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = pytest.mark.subsystem


def _chats(*chats: tuple[str, str, list[str]]) -> None:
    """Chats (id, title, messages), oldest first."""
    from langchain_core.messages import HumanMessage
    from row_bot import threads

    for identity, title, messages in chats:
        threads.create_thread(title, thread_id=identity, seed_default_skills=False)
        assert threads.append_checkpoint_messages(identity, [
            HumanMessage(id=f"{identity}-{index:03d}", content=text) for index, text in enumerate(messages)])


def _search(client, headers, query: str, cursor: str | None = None) -> dict:
    response = client.get("/api/v1/search", headers=headers,
                          params={"query": query, **({"cursor": cursor} if cursor else {})})
    assert response.status_code == 200, response.text
    return response.json()


def _hits(page: dict) -> list[tuple[str, str | None]]:
    return [(item["conversation_id"], item["message_id"]) for item in page["items"]]


def test_titles_and_messages_are_found_newest_first(service):
    """Found live: chats at the top of today's list were missing from the first page, which read chats in id
    order. Hits follow the Library, newest first (here ids sort oldest first), a chat's title before its
    messages."""
    _chats(("chat-1", "Old shopping list", ["Buy periwinkle paint"]),
           ("chat-2", "Planning a garden", ["Periwinkle grows in shade", "Go shopping for seeds"]),
           ("chat-3", "Shopping Note and Secret Word", ["The secret word is periwinkle"]))
    with _client(service) as client:
        _, headers = bootstrap(client)
        shopping = _search(client, headers, "shopping")
        periwinkle = _search(client, headers, "periwinkle")
    assert _hits(shopping) == [("chat-3", None), ("chat-2", "chat-2-001"), ("chat-1", None)]
    assert _hits(periwinkle) == [("chat-3", "chat-3-000"), ("chat-2", "chat-2-000"), ("chat-1", "chat-1-000")]
    assert not periwinkle["has_more"]


def test_a_long_chat_never_hides_another_chats_title_from_the_first_page(service):
    """Found live: "Corporate" (a title) said "No matches in this page". Titles are library data and are all
    matched at once; reading messages stays bounded (500 a page) and Continue search reaches the rest."""
    # The long chat is the newer one, and first in id order too.
    _chats(("chat-b", "Rewriting Corporate Jargon in Plain English", ["Corporate words, plainly"]),
           ("chat-a", "A long chat", [f"Line {index:03d}" for index in range(510)]))
    with _client(service) as client:
        _, headers = bootstrap(client)
        first = _search(client, headers, "corporate")
        assert _hits(first) == [("chat-b", None)]
        assert first["has_more"] and first["scanned_messages"] == 500
        rest = _search(client, headers, "corporate", first["next_cursor"])
    assert _hits(rest) == [("chat-b", "chat-b-000")]
    assert not rest["has_more"] and rest["scanned_messages"] == 11


def test_a_stopped_reply_reads_without_its_stop_marker(service):
    """Found live: Find showed "⏹️ *[Stopped]*" inside a stopped reply's excerpt."""
    from langchain_core.messages import AIMessage
    from row_bot import threads

    threads.create_thread("Lighthouse story", thread_id="chat-stop", seed_default_skills=False)
    assert threads.append_checkpoint_messages("chat-stop", [
        AIMessage(id="chat-stop-000", content="The keeper climbed the lighthouse stairs.\n\n⏹️ *[Stopped]*")])
    with _client(service) as client:
        _, headers = bootstrap(client)
        page = _search(client, headers, "keeper")
    assert [item["excerpt"] for item in page["items"]] == ["The keeper climbed the lighthouse stairs."]


@pytest.mark.slow
def test_todays_chats_lead_a_library_of_forty(service):
    """The live shape: about forty chats, today's on top, a page reading 32 chats' messages."""
    _chats(*[(f"chat-{index:02d}", f"Ordinary chat {index:02d}", [f"Ordinary question {index:02d}"]) for index in range(37)],
           ("chat-37", "Rewriting Corporate Jargon in Plain English", ["We will leverage synergies"]),
           ("chat-38", "Using useEffect Cleanup in React", ["How do I clean up in useEffect?"]),
           ("chat-39", "Shopping Note and Secret Word", ["Remember the secret word: periwinkle."]))
    with _client(service) as client:
        _, headers = bootstrap(client)
        found = {query: _hits(_search(client, headers, query))
                 for query in ("Shopping", "secret word", "useEffect", "Corporate", "periwinkle")}
    assert found == {"Shopping": [("chat-39", None)],
                     "secret word": [("chat-39", None), ("chat-39", "chat-39-000")],
                     "useEffect": [("chat-38", None), ("chat-38", "chat-38-000")],
                     "Corporate": [("chat-37", None)],
                     "periwinkle": [("chat-39", "chat-39-000")]}
