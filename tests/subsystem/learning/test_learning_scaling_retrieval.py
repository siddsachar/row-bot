"""The scaling study's retrieval arm (PREREG-SCALING §4): the most similar rows by the vault's own novelty measure,
never a held-out request's row, and examples that fit the prompt section they go in."""

from __future__ import annotations

import json

import pytest

from tests.e2e.learning.scaling import retrieval

pytestmark = [pytest.mark.subsystem]

TEXTS = {
    "r1": "What is the vendor deadline in the Harbourside contract?",
    "r2": "Sum the donations from 2026-10-01 to 2026-10-07 in ledger.csv",
    "r3": "Close issue 14 with the comment fixed in build 212",
    "r4": "Sum the bank lines from 2026-09-01 to 2026-09-05 in ledger.csv",
    "r5": "Add a calendar event for Ana on 2026-11-20 from 10:00 to 11:00",
    "r6": "Archive every newsletter from news@example.test",
}


def _row(request: str, text: str, *, day: int = 3) -> dict:
    action = {"role": "assistant", "content": "", "weight": 1, "tool_calls": [
        {"id": "c1", "type": "function",
         "function": {"name": "tool_invoke", "arguments": json.dumps({"name": "workspace_read_file",
                                                                      "arguments": {"file_path": "ledger.csv"}})}}]}
    answer = {"role": "assistant", "content": "The total is 412.50.", "weight": 1}
    return {"id": f"ep_{request}", "person": "mara", "request": request, "day": day, "text": text,
            "row": {"messages": [{"role": "system", "content": "Row-Bot"}, {"role": "user", "content": text},
                                 action, {"role": "tool", "content": "date,amount", "tool_call_id": "c1"}, answer]}}


def test_it_picks_the_most_similar_rows_by_the_vaults_own_measure():
    index = retrieval.Index.build([_row(key, text) for key, text in TEXTS.items()], held_out=[])
    query = "Sum the donations from 2026-11-02 to 2026-11-06 in ledger.csv"

    picked = index.pick(query, 2)
    scored = dict(zip([entry.id for entry in index.entries], retrieval.scores(query, [e.text for e in index.entries])))

    assert [entry.request for entry in picked] == ["r2", "r4"]
    assert scored[picked[0].id] >= scored[picked[1].id] > max(value for key, value in scored.items()
                                                             if key not in {picked[0].id, picked[1].id})


def test_its_measure_is_the_one_the_vaults_novelty_check_thresholds():
    from row_bot.learning import audit

    novel = vars(audit)["_novel"]
    threshold = vars(audit)["NOVELTY_OVERLAP"]
    known = list(TEXTS.values())
    for query in ("Sum the donations from 2026-10-01 to 2026-10-07 in ledger.csv",
                  "Close issue 14 with the comment fixed in build 212 please",
                  "What is the capital of a country nobody named",
                  "Archive every newsletter from news@example.test today"):
        assert (max(retrieval.scores(query, known)) >= threshold) == (not novel(query, [[text] for text in known]))


def test_a_held_out_requests_row_is_never_retrieved():
    rows = [_row("r1", TEXTS["r1"]), _row("mara-d101-0", TEXTS["r2"], day=101)]

    with pytest.raises(retrieval.RetrievalError, match="held-out"):
        retrieval.Index.build(rows, held_out=["mara-d101-0"])


def test_four_examples_fit_learnings_prompt_section_whole():
    from row_bot.learning import behaviour

    long = "Sum every donation from " + "the main hall and " * 40 + "the shop"
    index = retrieval.Index.build([_row(f"r{n}", long + f" {n}") for n in range(4)], held_out=[])
    entries = [behaviour.Entry(f"example-{n}", slot, text) for n, (slot, text)
               in enumerate(retrieval.ways(index.pick(long, 4)))]

    section = behaviour.render(entries)
    assert len(section) <= behaviour.MAX_SECTION and not section.endswith("…")
    assert all(text in section for _, text in retrieval.ways(index.pick(long, 4)))
    assert "workspace_read_file" in section and "412.50" in section
