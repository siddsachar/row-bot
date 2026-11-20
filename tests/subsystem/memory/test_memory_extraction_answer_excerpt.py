"""What extraction reads of a long trusted answer (P2: the smarter cap).

Reading long answers in full (up to 4,000 characters) cost BEAM 10-12x the extraction tokens and 3.5x
the time for no score gain. Extraction now reads an answer's first ~1,200 characters, then only the
later sentences that name a person, place, date, amount or a choice the user made, and never code
blocks, tables or long lists. The selection is deterministic; the per-turn trusted rule is unchanged.
"""
from __future__ import annotations

import pytest

from tests.fixtures.memory_stack import fresh_memory_stack

pytestmark = pytest.mark.subsystem

FILLER = "This is a general point about how good planning helps everyone in the long run. "


@pytest.fixture
def extraction(tmp_path, monkeypatch, reload_for_data_dir):
    return fresh_memory_stack(tmp_path, monkeypatch, reload_for_data_dir)["memory_extraction"]


def _read(extraction, answer: str) -> str:
    return extraction._format_conversation([{"role": "assistant", "content": answer, "trusted": True}])


def test_a_short_answer_is_read_whole(extraction) -> None:
    answer = "Try Nostalgia on Rua Augusta for records. It opens at 10 on Saturdays."
    assert _read(extraction, answer) == f"Assistant: {answer}"


def test_past_its_start_only_sentences_that_name_something_are_read(extraction) -> None:
    named = [
        "The two companies I mentioned are Patagonia and Southwest Airlines.",  # names
        "Your flight leaves on 14 July.",  # a date
        "The necklace was appraised at $5,000.",  # an amount
        "You picked the blue sofa in the end.",  # a choice the user made
        "We can meet on Friday after work.",  # a weekday
    ]
    answer = FILLER * 16 + " ".join(f"{FILLER}{sentence}" for sentence in named) + " " + FILLER * 10

    read = _read(extraction, answer)

    assert read.startswith("Assistant: " + FILLER.strip())
    for sentence in named:
        assert sentence in read
    # The filler is read only in the first ~1,200 characters.
    assert read.count(FILLER.strip()) <= 1_200 // len(FILLER) + 1 < answer.count(FILLER.strip())
    assert read.endswith(" [...]")


def test_code_tables_and_long_lists_are_never_read_but_a_short_list_is(extraction) -> None:
    code = "```python\n" + "def secret_rate():\n    return 42\n" * 30 + "```"
    table = "| Plan | Price |\n|---|---|\n| Basic | 10 |\n| Pro | 20 |"
    long_list = "\n".join(f"- Option {number}: Brightline" for number in range(1, 13))
    short_list = "- Lisbon\n- Porto"
    answer = f"Here is the script.\n{code}\nAnd the prices:\n{table}\nAll options:\n{long_list}\nYour two picks:\n{short_list}"

    read = _read(extraction, answer)

    assert "secret_rate" not in read and "[code left out]" in read
    assert "| Pro |" not in read and "[table left out]" in read
    assert "Option 7" not in read and "[list of 12 items left out]" in read
    assert "- Lisbon\n- Porto" in read


def test_the_selection_is_bounded_and_the_same_every_time(extraction) -> None:
    answer = " ".join(f"Meeting {number} with Dana is on {number} March." for number in range(1, 400))

    first, second = _read(extraction, answer), _read(extraction, answer)

    assert first == second
    assert len(first) <= len("Assistant: ") + extraction._ANSWER_CHARS + len(" [...]")


def test_a_long_code_heavy_answer_costs_far_fewer_tokens(extraction) -> None:
    # Shaped like a BEAM answer: some prose, a long code block, a recap that names the user's choice.
    answer = ("Sure, here is a revised version of your parser, with the changes we discussed. " * 4
              + "\n```python\n" + "    tokens.append(read_token(stream))\n" * 80 + "```\n"
              + FILLER * 8 + "You decided on Python 3.12 for the deployment on 3 March.")
    before = min(len(answer), 4_000)  # #939 read up to 4,000 characters

    after = len(_read(extraction, answer)) - len("Assistant: ")

    assert "You decided on Python 3.12 for the deployment on 3 March." in _read(extraction, answer)
    assert after <= before * 0.35  # about 4,000 characters (1,000 tokens) down to under 1,400 (350)
