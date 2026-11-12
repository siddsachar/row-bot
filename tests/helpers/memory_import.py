"""Memory import tests: a memory store in the test's own folder, a fake default chat model, and the service."""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from types import SimpleNamespace


def settle() -> None:
    """Wait for the import's background step (scan, conversations, preview) to finish."""
    for thread in threading.enumerate():
        if thread.name.startswith("row-bot-memory-import-"):
            thread.join(timeout=60)


def _reconcile_items(prompt: str) -> list[dict]:
    return json.loads(prompt.split("closest to each (JSON):\n", 1)[1].split("\n\nReply with only", 1)[0])


class FakeModel:
    """The person's default chat model: extraction finds one memory per message the person wrote, and
    reconciling replaces a saved memory a fact about "light theme" contradicts, skips one it repeats,
    updates one it adds to and adds the rest."""

    def __init__(self) -> None:
        self.extraction_prompts: list[str] = []
        self.reconcile_prompts: list[str] = []
        self.fail = False

    def __call__(self, purpose, messages, *, answer=None, **_how) -> str:
        assert purpose == "extraction"  # the default chat model's unattended purpose
        prompt = messages[0].content
        if self.fail:
            raise TimeoutError("the model did not answer")
        if answer is not None and answer.name == "memory_reconcile":
            self.reconcile_prompts.append(prompt)
            decisions = []
            for item in _reconcile_items(prompt):
                saved = item["saved"]
                if saved and "light theme" in item["text"]:
                    decisions.append({"fact": item["fact"], "action": "supersede", "memory_id": saved[0]["memory_id"],
                                      "subject": "Recording theme", "text": ""})
                elif saved and item["text"] == saved[0]["text"]:
                    decisions.append({"fact": item["fact"], "action": "skip", "memory_id": saved[0]["memory_id"],
                                      "subject": "", "text": ""})
                elif saved and saved[0]["text"].lower() in item["text"].lower():
                    decisions.append({"fact": item["fact"], "action": "update", "memory_id": saved[0]["memory_id"],
                                      "subject": "", "text": item["text"]})
                else:
                    decisions.append({"fact": item["fact"], "action": "add", "memory_id": "", "subject": "",
                                      "text": ""})
            return json.dumps({"decisions": decisions})
        self.extraction_prompts.append(prompt)
        memories = []
        for line in prompt.splitlines():
            if line.startswith("User: "):
                said = line.removeprefix("User: ").strip()
                memories.append({"category": "fact", "subject": said[:30], "content": f"Asked about {said}",
                                 "aliases": ""})
        return json.dumps({"memories": memories})


@contextmanager
def memory_import_stack(tmp_path, monkeypatch, reload_for_data_dir):
    kg, memory, evolution, _extraction, _reconcile = reload_for_data_dir(
        tmp_path / "data", "row_bot.knowledge_graph", "row_bot.memory", "row_bot.memory_evolution",
        "row_bot.memory_extraction", "row_bot.memory_reconcile")
    from row_bot.application import memory_import as service

    kg._skip_reindex = True
    close: dict[str, float] = {}

    def semantic_search(query, top_k=5, threshold=0.5, *, for_auto_recall=False, stats=None):
        if stats is not None:
            stats["mean"] = 0.40
        hits = []
        for entity_id, score in sorted(close.items(), key=lambda item: -item[1]):
            entity = kg.get_entity(entity_id)
            if entity is not None and score >= threshold:
                hits.append({**entity, "score": score})
        return hits[:top_k]

    monkeypatch.setattr(kg, "semantic_search", semantic_search)
    model = FakeModel()
    monkeypatch.setattr("row_bot.providers.background.invoke_background", model)
    monkeypatch.setattr(service, "_model", lambda: {"name": "fixture-model", "provider": "Local",
                                                    "on_this_computer": True})
    service.close_batches()
    yield SimpleNamespace(kg=kg, memory=memory, evolution=evolution, service=service, close=close, model=model,
                          root=tmp_path / "data" / service.FOLDER)
    settle()
    service.close_batches()
