"""Review memory tests: a memory store in the test's own folder, a fake default chat model, and the service."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from types import SimpleNamespace


def settle() -> None:
    """Wait for a review's background job to finish."""
    for thread in threading.enumerate():
        if thread.name == "row-bot-memory-review":
            thread.join(timeout=60)


def _between(prompt: str, start: str, end: str) -> str:
    return prompt.split(start, 1)[1].split(end, 1)[0]


class FakeReviewModel:
    """The person's default chat model, deciding from words in each memory:

    "obsolete" → remove (outdated); "weather" → remove (not about the person); "MERGE-ME" with a similar
    memory → merge into it; "next week" → rewrite with the date; "Thoth" → rewrite to Row-Bot; the rest kept.
    Splitting the person's record keeps lines starting "Name"/"Lives"/"Works" as the profile and makes every
    other sentence a memory. Reconciling adds each fact unless a saved memory says it word for word."""

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.fail = False

    def __call__(self, purpose, messages, *, answer=None, **_how) -> str:
        assert purpose == "dream"  # the default chat model's unattended purpose
        prompt = messages[0].content
        self.prompts.append(prompt)
        if self.fail:
            raise TimeoutError("the model did not answer")
        if "Memories (JSON):\n" in prompt:
            return json.dumps({"decisions": [self._decide(item) for item in json.loads(
                _between(prompt, "Memories (JSON):\n", "\n\nReply with only"))]})
        if "\nLines:\n" in prompt:  # the profile written once: each line once, in order
            lines = _between(prompt, "\nLines:\n", "\n\nReply with only").splitlines()
            return json.dumps({"profile": list(dict.fromkeys(line.removeprefix("- ") for line in lines))})
        if "\nText:\n" in prompt:
            text = _between(prompt, "\nText:\n", "\n\nReply with only")
            profile, memories = [], []
            for sentence in (part.strip() for part in text.split(". ") if part.strip()):
                sentence = sentence.rstrip(".") + "."
                if sentence.startswith(("Name", "Lives", "Works")):
                    profile.append(sentence)
                else:
                    category = "preference" if "prefers" in sentence.lower() else "fact"
                    memories.append({"category": category, "subject": " ".join(sentence.split()[:3]),
                                     "text": sentence})
            return json.dumps({"profile": profile, "memories": memories})
        items = json.loads(_between(prompt, "closest to each (JSON):\n", "\n\nReply with only"))
        decisions = []
        for item in items:
            same = [saved for saved in item["saved"] if saved["text"].rstrip(".") == item["text"].rstrip(".")]
            decisions.append({"fact": item["fact"], "action": "skip" if same else "add",
                              "memory_id": same[0]["memory_id"] if same else "", "subject": "", "text": ""})
        return json.dumps({"decisions": decisions})

    @staticmethod
    def _decide(item: dict) -> dict:
        text = item["text"]
        decision = {"memory": item["id"], "action": "keep", "reason": "", "text": "", "into": ""}
        if "obsolete" in text:
            decision.update(action="remove", reason="outdated")
        elif "weather" in text:
            decision.update(action="remove", reason="not_about_the_person")
        elif "MERGE-ME" in text and item.get("similar_to"):
            decision.update(action="merge", into=item["similar_to"],
                            text="Takes a flat white with oat milk, every morning.")
        elif "next week" in text:
            decision.update(action="rewrite", text=text.replace("next week", f"the week after {item['saved_on']}"))
        elif "Thoth" in text:
            decision.update(action="rewrite", text=text.replace("Thoth", "Row-Bot"))
        return decision

    def review_prompts(self) -> list[str]:
        return [prompt for prompt in self.prompts if "Memories (JSON):\n" in prompt]


def snapshot(db_path: str) -> dict:
    """Every saved memory, relation and recall stamp, exactly as stored."""
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        return {table: sorted((dict(row) for row in connection.execute(f"SELECT * FROM {table}")),
                              key=lambda row: str(row.get("id") or row.get("entity_id")))
                for table in ("entities", "relations", "knowledge_recall_stamps")}
    finally:
        connection.close()


@contextmanager
def memory_review_stack(tmp_path, monkeypatch, reload_for_data_dir):
    kg, memory, evolution, _reconcile, dream = reload_for_data_dir(
        tmp_path / "data", "row_bot.knowledge_graph", "row_bot.memory", "row_bot.memory_evolution",
        "row_bot.memory_reconcile", "row_bot.dream_cycle")
    from row_bot.application import memory_review as service

    kg._skip_reindex = True
    pairs: dict[frozenset, float] = {}

    def semantic_search(query, top_k=5, threshold=0.5, *, for_auto_recall=False, stats=None):
        """Memories are close only when a test says so (``near(a, b)``); the mean is 0.40."""
        if stats is not None:
            stats["mean"] = 0.40
        asking = [entity for entity in kg.list_entities(limit=1000) if kg._entity_text(entity) == query]
        hits = []
        for entity in asking:
            for pair, score in pairs.items():
                if entity["id"] in pair and score >= threshold:
                    [other_id] = pair - {entity["id"]}
                    other = kg.get_entity(other_id)
                    if other is not None:
                        hits.append({**other, "score": score})
        return sorted(hits, key=lambda hit: -hit["score"])[:top_k]

    def near(first: dict, second: dict, score: float = 0.92) -> None:
        pairs[frozenset((first["id"], second["id"]))] = score

    monkeypatch.setattr(kg, "semantic_search", semantic_search)
    model = FakeReviewModel()
    monkeypatch.setattr("row_bot.providers.background.invoke_background", model)
    monkeypatch.setattr(service, "_model", lambda: {"name": "fixture-model", "provider": "Local",
                                                    "on_this_computer": True})
    service.close_runs()
    yield SimpleNamespace(kg=kg, memory=memory, evolution=evolution, dream=dream, service=service, model=model,
                          near=near, root=tmp_path / "data" / service.FOLDER, data=tmp_path / "data")
    settle()
    service.close_runs()
