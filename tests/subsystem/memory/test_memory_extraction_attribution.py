"""Extraction keeps who said what (P2: misattribution).

With the other side's trusted answers read (#939), a local model saved what the other speaker said
about themselves as the user's own facts: from Melanie's side of a LoCoMo conversation, "User's
identity: The user identifies as trans" was Caroline's fact, and the question "Would Melanie be
considered a member of the LGBTQ community?" flipped to wrong. Each line now names its side (and its
speaker, when the message has one), the reply says whose lines each fact came from, and a code guard
drops a fact only the other side said whenever it would be the user's. Memory safety: these stay in
the pull request lane.
"""
from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage

pytestmark = pytest.mark.subsystem


class _Model:
    """The default chat model as extraction sees it: answers with a fixed reply, records the prompts."""

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.memories: list[dict] = []

    def bind(self, **_kwargs):
        return self

    def invoke(self, messages):
        prompt = str(messages[-1].content)
        if "CONVERSATION:\n" not in prompt:  # reconcile: nothing saved is close
            return AIMessage(content='{"decisions": []}')
        self.prompts.append(prompt)
        return AIMessage(content=json.dumps({"memories": self.memories}))


@pytest.fixture
def stack(reload_for_data_dir, tmp_path, monkeypatch):
    data = tmp_path / "data"
    (kg,) = reload_for_data_dir(data, "row_bot.knowledge_graph")
    kg._skip_reindex = True
    monkeypatch.setattr(kg, "semantic_search", lambda *_args, **_kwargs: [])
    threads, memory, _evolution, extraction = reload_for_data_dir(
        data, "row_bot.threads", "row_bot.memory", "row_bot.memory_evolution", "row_bot.memory_extraction")
    from row_bot import models
    from row_bot.providers import background

    model = _Model()
    monkeypatch.setattr(models, "get_current_model", lambda: "model:openai:gpt-5")
    monkeypatch.setattr(models, "get_llm_for", lambda _ref, num_ctx=None, *, reasoning_plan=None: model)
    monkeypatch.setattr(background, "resolve_reasoning_capabilities_for_ref", lambda _ref: None)
    monkeypatch.setattr(threads, "get_workflow_thread_ids", lambda: set())
    return threads, extraction, kg, memory, model


def _entity(category: str, subject: str, content: str, said_by: str, aliases: str = "") -> dict:
    return {"category": category, "subject": subject, "content": content, "aliases": aliases, "said_by": said_by}


def _relation(source: str, kind: str, target: str, said_by: str) -> dict:
    return {"relation_type": kind, "source_subject": source, "target_subject": target, "confidence": 0.9,
            "said_by": said_by}


def _subjects(entries: list[dict]) -> list[str]:
    return [entry.get("subject") or f"{entry['source_subject']}-{entry['relation_type']}-{entry['target_subject']}"
            for entry in entries]


# ── The failing LoCoMo case, reconstructed ───────────────────────────────────

MELANIE = ("Hey Caroline! Since we last chatted, I've got lots going on. My kids and I took a trip to the beach "
           "last weekend, and I'm still painting whenever I can.")
CAROLINE = ("That sounds lovely, Mel! I went to an LGBTQ support group yesterday. As a trans woman, hearing "
            "everyone's stories was so powerful. It made me want to keep fighting for my community.")


def test_the_other_speakers_identity_is_never_saved_as_the_users(stack) -> None:
    threads, extraction, kg, _memory, model = stack
    threads.create_thread("locomo", thread_id="locomo", seed_default_skills=False)
    threads.append_checkpoint_messages("locomo", [
        HumanMessage(id="h1", content=MELANIE, name="Melanie"),
        AIMessage(id="a1", content=CAROLINE, name="Caroline"),
    ])
    # What Qwen replied in P2, with the side each fact came from.
    model.memories = [
        _entity("fact", "User's identity", "The user identifies as trans and is involved in the LGBTQ+ community",
                "assistant"),
        _entity("person", "Melanie's profile", "Melanie (Mel) is a trans individual actively involved in LGBTQ+ advocacy",
                "assistant"),
        _relation("User", "member_of", "LGBTQ support group", "assistant"),
        _entity("person", "Caroline", "Caroline is a trans woman who goes to an LGBTQ support group", "assistant"),
        _entity("organisation", "LGBTQ support group", "The support group Caroline goes to", "assistant"),
        _relation("Caroline", "member_of", "LGBTQ support group", "assistant"),
        _entity("person", "User", "User's name is Melanie; she is a parent who paints", "user", aliases="Melanie, Mel"),
        _entity("event", "Beach trip", "Melanie took her kids to the beach the weekend before", "user"),
    ]

    extraction.run_extraction()

    prompt = model.prompts[0]
    assert f"User (Melanie): {MELANIE}" in prompt and f"Assistant (Caroline): {CAROLINE}" in prompt
    assert "Facts about the user come only from the user's own lines." in prompt
    saved = {entity["subject"]: entity for entity in kg.list_entities(limit=100)}
    assert set(saved) == {"Caroline", "User", "Beach trip", "LGBTQ support group"}
    assert "trans" not in saved["User"]["description"]
    relations = {(rel["source_subject"], rel["relation_type"], rel["target_subject"]) for rel in kg.list_relations()}
    assert ("Caroline", "member_of", "LGBTQ support group") in relations
    assert not any(source == "User" for source, _kind, _target in relations)
    assert json.loads(saved["Caroline"]["properties"])["evidence_role"] == "assistant"
    assert json.loads(saved["User"]["properties"])["evidence_role"] == "user"


# ── A fixed set of two-speaker conversations ─────────────────────────────────

TWO_SPEAKERS = "User (Jo): I just got back from Porto.\nAssistant (Sam): Nice! I moved to Lisbon last year and I adore hiking."
WITH_ROW_BOT = "User: Can you recommend a vintage shop in Lisbon?\nAssistant: Try Nostalgia on Rua Augusta for records."

CASES = {
    "the other speaker's own facts stay theirs": (
        TWO_SPEAKERS,
        [_entity("person", "Sam", "Sam moved to Lisbon last year and loves hiking", "assistant"),
         _relation("Sam", "lives_in", "Lisbon", "assistant")],
        ["Sam", "Sam-lives_in-Lisbon"],
    ),
    "the other speaker's preference is never the user's": (
        TWO_SPEAKERS,
        [_entity("preference", "Hiking", "Loves hiking", "assistant"),
         _entity("person", "Jo", "Jo adores hiking", "assistant"),
         _relation("User", "lives_in", "Lisbon", "assistant"),
         _relation("Lisbon", "home_of", "Jo", "assistant")],
        [],
    ),
    "a place Row-Bot named for the user is kept, as Row-Bot's": (
        WITH_ROW_BOT,
        [_entity("place", "Nostalgia", "Vintage record shop on Rua Augusta the assistant recommended", "assistant"),
         _entity("preference", "Vintage shops", "The user likes vintage shops", "assistant")],
        ["Nostalgia"],
    ),
    "the user's own facts are kept whoever else speaks": (
        TWO_SPEAKERS,
        [_entity("person", "User", "User's name is Jo", "user", aliases="Jo"),
         _entity("preference", "Travel", "Jo enjoys city breaks", "user"),
         _entity("fact", "Porto trip", "Jo just got back from Porto", "user"),
         _relation("User", "visits", "Porto", "user")],
        ["User", "Travel", "Porto trip", "User-visits-Porto"],
    ),
    "a fact the reply did not attribute is read as the user's": (
        TWO_SPEAKERS,
        [{"category": "event", "subject": "Porto trip", "content": "Jo just got back from Porto"}],
        ["Porto trip"],
    ),
}


@pytest.mark.parametrize("name", list(CASES))
def test_two_speaker_conversations_keep_who_said_what(stack, name: str) -> None:
    _threads, extraction, _kg, _memory, model = stack
    conversation, reply, kept = CASES[name]
    model.memories = reply

    assert _subjects(extraction._extract_from_conversation(conversation)) == kept


def test_a_name_the_user_already_has_is_theirs_alone(stack) -> None:
    _threads, extraction, _kg, memory, model = stack
    saved = memory.save_memory("person", "User", "User's name is Melanie")
    memory.update_memory(saved["id"], "User's name is Melanie", aliases="Melanie, Mel")
    model.memories = [_entity("person", "Mel", "Mel is a trans individual", "assistant"),
                      _entity("person", "Caroline", "Caroline is a trans woman", "assistant")]

    entries = extraction._extract_from_conversation("User: Hi!\nAssistant: Hey Mel! I'm trans, by the way.")

    assert _subjects(entries) == ["Caroline"]


def test_with_no_words_from_the_other_side_every_fact_is_the_users(stack) -> None:
    _threads, extraction, _kg, _memory, model = stack
    # The answer of a turn that read outside content is a placeholder: nothing came from that side.
    model.memories = [_entity("preference", "Coffee order", "The user drinks flat whites", "assistant"),
                      _relation("User", "prefers", "Coffee order", "assistant")]

    entries = extraction._extract_from_conversation(
        "User: I always order a flat white.\nAssistant: [answer left out: this turn read outside content]")

    assert _subjects(entries) == ["Coffee order", "User-prefers-Coffee order"]
    assert {entry["said_by"] for entry in entries} == {"user"}


def test_the_reply_schema_asks_whose_lines_each_fact_came_from(stack) -> None:
    _threads, extraction, _kg, _memory, model = stack
    bound: list[dict] = []
    model.bind = lambda **kwargs: bound.append(kwargs) or model

    extraction._extract_from_conversation("User: hello")

    schema = json.dumps(bound, default=str)
    assert '"said_by"' in schema and '"assistant"' in schema and "self_knowledge" not in schema
