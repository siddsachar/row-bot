"""Dream Cycle asks the model about a candidate again only when what it would read changed (WP C2, F47).

Every night used to re-send the same thin entities for enrichment and, after a
3–7 day pause, the same rejected pairs for inference, and the insights call ran
on a snapshot that had not changed. A merge, enrichment or inference the model
turned down is now remembered with a hash of the exact prompt it read.
"""
from __future__ import annotations

import os
import re

import pytest
from langchain_core.messages import AIMessage, HumanMessage

pytestmark = pytest.mark.subsystem


class _Model:
    """Dream's model calls (``_llm_call``): records each prompt and turns every candidate down."""

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.description = '{"description": ""}'  # a whole answer with nothing usable in it

    def __call__(self, prompt: str, **_kwargs) -> str:
        self.prompts.append(prompt)
        if "self-analysis engine" in prompt:
            return '{"insights": []}'
        if "co-occur in" in prompt:
            return '{"has_relation": false}'
        return self.description

    def asked(self, marker: str, since: int = 0) -> list[str]:
        return [prompt for prompt in self.prompts[since:] if marker in prompt]


@pytest.fixture
def dream(reload_for_data_dir, tmp_path, monkeypatch):
    threads, kg, dream_cycle = reload_for_data_dir(
        tmp_path / "data", "row_bot.threads", "row_bot.knowledge_graph", "row_bot.dream_cycle")
    import row_bot.insights as insights
    from row_bot import models

    monkeypatch.setattr(kg, "_skip_reindex", True)
    monkeypatch.setattr(kg, "semantic_search", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(kg, "repair_projections", lambda **_kwargs: {"complete": True})
    monkeypatch.setattr(models, "get_current_model", lambda: "model:openai:gpt-5")
    monkeypatch.setattr(insights, "auto_prune", lambda: 0)
    monkeypatch.setattr(insights, "set_last_analysis", lambda: None)
    model = _Model()
    monkeypatch.setattr(dream_cycle, "_llm_call", model)
    return threads, kg, dream_cycle, model


def _conversation(threads, thread_id: str, text: str) -> None:
    threads.create_thread(thread_id, thread_id=thread_id, seed_default_skills=False)
    assert threads.append_checkpoint_messages(thread_id, [
        HumanMessage(id=f"{thread_id}-h", content=text), AIMessage(id=f"{thread_id}-a", content="Noted.")])


def _small_graph(threads, kg, dream_cycle, monkeypatch) -> None:
    """Three thin entities that two conversations mention together."""
    dream_cycle._save_config({**dream_cycle._load_config(), "min_entities": 2})
    nights = iter(range(1, 10))
    monkeypatch.setattr(dream_cycle, "_collect_system_snapshot",
                        lambda: f"KNOWLEDGE GRAPH: 3 entities\n\nLAST DREAM CYCLE: night {next(nights)}")
    for subject, kind, description in [("Alice", "person", "Alice, a friend."),
                                       ("Bob", "person", "Bob, a neighbour."),
                                       ("Acme", "organisation", "Acme, a company.")]:
        kg.save_entity(kind, subject, description, source="test")
    _conversation(threads, "t1", "Alice works at Acme with Bob.")
    _conversation(threads, "t2", "Alice and Bob visited Acme last spring.")


def test_a_second_night_with_nothing_new_asks_the_model_nothing(dream, monkeypatch) -> None:
    threads, kg, dream_cycle, model = dream
    _small_graph(threads, kg, dream_cycle, monkeypatch)

    dream_cycle.run_dream_cycle()

    assert len(model.asked("thin description")) == 3
    infer = model.asked("co-occur in")
    assert len(infer) == 3
    assert len(model.asked("self-analysis engine")) == 1
    # The rules come first, so every inference call shares them as a cacheable prefix.
    prefix = os.path.commonprefix(infer)
    assert "Allowed relation types" in prefix and '{"has_relation": false}' in prefix
    assert len(prefix) >= infer[0].index("Two entities co-occur")

    threads._save_thread_meta("t1", "Renamed")  # lists it first: the same evidence in another order
    night_two = len(model.prompts)
    dream_cycle.run_dream_cycle()
    assert model.prompts[night_two:] == []

    # A new conversation about Alice and Acme: only what it changes is asked again.
    _conversation(threads, "t3", "Alice joined the board of Acme.")
    night_three = len(model.prompts)
    dream_cycle.run_dream_cycle()

    enriched = model.asked("thin description", night_three)
    assert sorted(prompt.split('Subject: "')[1].split('"')[0] for prompt in enriched) == ["Acme", "Alice"]
    [pair] = model.asked("co-occur in", night_three)
    assert "co-occur in 3 conversation(s)" in pair
    assert set(re.findall(r'subject: "(\w+)"', pair.split("Two entities co-occur")[1])) == {"Alice", "Acme"}
    assert model.asked("self-analysis engine", night_three) == []


def test_a_candidate_one_model_turned_down_is_asked_again_once_after_the_default_model_changes(
        dream, monkeypatch) -> None:
    threads, kg, dream_cycle, model = dream
    from row_bot import models

    _small_graph(threads, kg, dream_cycle, monkeypatch)
    dream_cycle.run_dream_cycle()
    turned_down = len(model.asked("thin description")) + len(model.asked("co-occur in"))
    assert turned_down == 6

    asked = len(model.prompts)
    dream_cycle.run_dream_cycle()  # the same model: nothing is asked again
    assert model.prompts[asked:] == []

    monkeypatch.setattr(models, "get_current_model", lambda: "model:anthropic:claude-opus-5-5")
    asked = len(model.prompts)
    dream_cycle.run_dream_cycle()  # another default model: each candidate gets one fresh look
    again = model.prompts[asked:]
    assert len([prompt for prompt in again if "thin description" in prompt or "co-occur in" in prompt]) == 6

    asked = len(model.prompts)
    dream_cycle.run_dream_cycle()
    assert model.prompts[asked:] == []


def test_a_merge_the_model_turned_down_waits_for_new_content(dream, monkeypatch) -> None:
    _threads, kg, dream_cycle, model = dream
    first = kg.save_entity("person", "Sam", "Sam plays chess.", source="test")
    second = kg.save_entity("person", "Sam", "Sam plays chess on Sundays.", source="test")
    def search(_text, top_k, threshold, stats=None):
        stats["mean"] = 0.45
        return [{**kg.get_entity(entity["id"]), "score": 0.95} for entity in (first, second)]

    monkeypatch.setattr(kg, "semantic_search", search)

    def candidates():
        return dream_cycle._find_pair_candidates(kg.list_entities(limit=100))

    model.description = '{"description": "Sam plays ch'  # cut short by the cap: not an answer
    [(entity_a, entity_b, _score)] = candidates()
    assert dream_cycle._merge_entities(entity_a, entity_b) is None
    assert len(candidates()) == 1

    model.description = '{"description": "Sam"}'  # an answer, but nothing worth storing
    [(entity_a, entity_b, _score)] = candidates()
    assert dream_cycle._merge_entities(entity_a, entity_b) is None
    assert candidates() == []

    kg.update_entity(second["id"], "Sam plays chess on Sundays at the club.")
    assert len(candidates()) == 1


def test_insights_skip_a_snapshot_the_last_analysis_read(dream, monkeypatch) -> None:
    _threads, _kg, dream_cycle, model = dream
    snapshot = {"graph": "KNOWLEDGE GRAPH: 30 entities", "night": 1}
    monkeypatch.setattr(dream_cycle, "_collect_system_snapshot",
                        lambda: f"{snapshot['graph']}\n\nLAST DREAM CYCLE: night {snapshot['night']}")

    assert "skipped" not in dream_cycle._run_insights_phase("night-1")
    assert len(model.prompts) == 1

    snapshot["night"] = 2  # only the last cycle's own line changed
    assert dream_cycle._run_insights_phase("night-2")["skipped"] is True
    assert len(model.prompts) == 1

    snapshot["graph"] = "KNOWLEDGE GRAPH: 31 entities"
    monkeypatch.setattr(dream_cycle, "_llm_call", lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError()))
    assert dream_cycle._run_insights_phase("night-3")["errors"]  # no reply: not remembered
    monkeypatch.setattr(dream_cycle, "_llm_call", model)
    assert "skipped" not in dream_cycle._run_insights_phase("night-4")
    assert len(model.prompts) == 2
