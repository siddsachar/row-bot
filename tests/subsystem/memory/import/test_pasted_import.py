from __future__ import annotations

import json

import pytest

from row_bot.memory_import import ENTITY_TYPES, SOURCES, UNIVERSAL_PROMPT, scan_files, scan_text


pytestmark = pytest.mark.subsystem


def _facts(scan):
    return [(fact.category, fact.date, fact.text) for fact in scan.facts]


def test_universal_prompt_answer_reads_the_first_fenced_block_and_maps_categories() -> None:
    answer = """Sure, here is everything.

```text
category | date | fact
identity | 2026-01-02 | Works night shifts as a nurse in Porto
person | unknown | Has a partner called Robin
preference | 2026-03-04 | Likes jazz | and blues
instruction | unknown | Always answer in metric units
project | unknown | Is building a greenhouse
skill | unknown | Uses Python and Rust
event | 2026-12-20 | Is flying to Madeira for Christmas
place | unknown | Grew up in a fishing village
other | 2026-02-30 | Collects old maps
hobby | unknown | Plays chess on Fridays
Speaks Portuguese and English
```

```
identity | unknown | A second block that is ignored
```
This is the complete set."""

    scan = scan_text(answer)

    assert _facts(scan) == [
        ("fact", "2026-01-02", "Works night shifts as a nurse in Porto"),
        ("person", None, "Has a partner called Robin"),
        ("preference", "2026-03-04", "Likes jazz | and blues"),
        ("preference", None, "Always answer in metric units"),
        ("project", None, "Is building a greenhouse"),
        ("skill", None, "Uses Python and Rust"),
        ("event", "2026-12-20", "Is flying to Madeira for Christmas"),
        ("place", None, "Grew up in a fishing village"),
        ("fact", None, "Collects old maps"),
        ("fact", None, "Plays chess on Fridays"),
        ("fact", None, "Speaks Portuguese and English"),
    ]
    by_text = {fact.text: fact for fact in scan.facts}
    assert by_text["Always answer in metric units"].tags == ("instruction",)
    assert by_text["Works night shifts as a nurse in Porto"].subject_hint == "User"
    assert all(fact.category in ENTITY_TYPES and fact.source == "pasted" for fact in scan.facts)
    assert scan.more_remain is False
    assert scan.counts["pasted"].facts == 11


def test_answer_that_says_more_remain_is_flagged() -> None:
    scan = scan_text("```\npreference | unknown | Likes tea\n```\nThere are more memories remaining; ask me to go on.")

    assert scan.more_remain is True
    assert any("more memories remain" in warning for warning in scan.warnings)


def test_anthropic_style_dated_lines_are_read() -> None:
    scan = scan_text("```\n[2026-03-01] - Prefers short answers\n[unknown] - Lives near the coast\n"
                     "[March 5, 2026] - Started learning the piano\n```")

    assert _facts(scan) == [
        ("fact", "2026-03-01", "Prefers short answers"),
        ("fact", None, "Lives near the coast"),
        ("fact", "2026-03-05", "Started learning the piano"),
    ]


def test_google_style_sections_and_evidence_dates_are_read() -> None:
    answer = """1. Demographics Information:
The user's name is Sam.
– Evidence: User said "call me Sam". Date: [2026-02-11].
2. Interests & Preferences:
The user enjoys trail running.
3. Relationships:
The user has a sister who is a vet.
5. Instructions:
Never use emoji in answers.
– Evidence: User said "no emoji please". Date: [2026-04-01]."""

    scan = scan_text(answer)

    assert _facts(scan) == [
        ("fact", "2026-02-11", "The user's name is Sam."),
        ("preference", None, "The user enjoys trail running."),
        ("person", None, "The user has a sister who is a vet."),
        ("preference", "2026-04-01", "Never use emoji in answers."),
    ]
    assert scan.facts[-1].tags == ("instruction",)


def test_plain_lists_and_markdown_headings_are_read_and_duplicates_dropped() -> None:
    scan = scan_text("## Preferences\n- Likes **strong** coffee\n* Likes strong coffee\n\n## Projects\n"
                     "1. Writing a novel\n\n   \n")

    assert _facts(scan) == [("preference", None, "Likes strong coffee"), ("project", None, "Writing a novel")]


def test_lines_that_look_like_secrets_are_dropped_and_counted() -> None:
    scan = scan_text("\n".join([
        "My password is hunter2-tango",
        "API key: sk-proj-abcdefghijklmnopqrstuvwx",
        "Card 4111 1111 1111 1111 for groceries",
        "Bank account number 12345678",
        "Lives in a flat with 2 cats",
        "Was born in 1990 and moved in 2015",
    ]))

    assert [fact.text for fact in scan.facts] == ["Lives in a flat with 2 cats", "Was born in 1990 and moved in 2015"]
    assert any(warning.startswith("4 lines that looked like") for warning in scan.warnings)


def test_long_facts_are_split_on_sentences_and_capped() -> None:
    text = " ".join(f"Walk {number} goes along the river with friends on a quiet weekend morning." for number in range(12))
    scan = scan_text("other | unknown | " + text)

    assert len(scan.facts) > 1
    assert all(len(fact.text) <= 500 for fact in scan.facts)
    assert " ".join(fact.text for fact in scan.facts) == text


def test_pasted_export_json_is_recognised_by_its_structure() -> None:
    pasted = json.dumps({"results": [{"id": "x1", "memory": "Prefers tea to coffee", "user_id": "alex"}]})

    scan = scan_text(pasted)

    assert [(fact.text, fact.source) for fact in scan.facts] == [("Prefers tea to coffee", "mem0")]


def test_a_text_file_is_read_like_a_pasted_answer(tmp_path) -> None:
    notes = tmp_path / "answer.txt"
    notes.write_text("skill | unknown | Repairs bicycles\n", encoding="utf-8")

    scan = scan_files([notes])

    assert [(fact.category, fact.text, fact.source_label) for fact in scan.facts] == [
        ("skill", "Repairs bicycles", "Text file")]


def test_prompt_and_catalogue_describe_every_route() -> None:
    assert "category | date | fact" in UNIVERSAL_PROMPT
    names = {source.id: source for source in SOURCES}
    assert names["claude"].export == "memories" and not names["claude"].prompt_route
    assert names["chatgpt"].export == "history" and names["chatgpt"].prompt_route
    assert names["other"].export == "none"
    assert all(source.how_to_get for source in SOURCES)
