"""Reconcile new memories with what is already saved, before anything is written.

Every new fact (from a chat, the save_memory tool or an import) is compared with the few saved memories it
is closest to, and one model call decides, per fact:

* ``add``: nothing saved covers it; it is saved as its own memory under a short, specific subject;
* ``update``: it adds to one saved memory and both stay true; that memory is rewritten, bounded;
* ``supersede``: it changes or contradicts one saved memory; the new one is saved and the old one is kept
  as history (``superseded``), so recall shows the current fact and "what did I use to…" still finds the old;
* ``skip``: a saved memory already says it.

This replaces appending ``old. new`` to a record that matched by name, which grew one person record past
30,000 characters and left contrary preferences side by side (memory audit, Nov 2026). Facts with nothing
close are added without a model call; a fact a saved memory with its name already contains is skipped, and
one that repeats such a memory and adds to it replaces its text, both without one.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable

logger = logging.getLogger(__name__)

RECONCILE_CANDIDATES = 4  # Saved memories a new fact is compared with.
RECONCILE_BATCH = 8  # New facts decided in one model call.
MEMORY_TEXT_CHARS = 600  # A rewritten memory, and what of a saved one the model is shown.
USER_PROFILE_CHARS = 1_500  # The User record holds who the person is; other facts get their own memory.
# A saved memory is a candidate when its meaning stands this far above the fact's mean similarity to every
# memory (the same calibration as recall, so it holds for any embedding model).
CANDIDATE_CALIBRATED_MIN = 0.25
_ACTIONS = frozenset({"add", "update", "supersede", "skip"})
_USER_SUBJECTS = frozenset({"user", "me", "the user", "myself"})


class ReconcileUnavailable(RuntimeError):
    """The model gave no usable answer: nothing was written, so the caller can try again later."""


@dataclass
class NewMemory:
    category: str
    subject: str
    content: str
    aliases: str = ""
    tags: str = ""
    source: str = "extraction"
    properties: dict = field(default_factory=dict)


@dataclass
class Outcome:
    action: str  # add | update | supersede | skip
    memory: NewMemory
    entity_id: str  # The memory that now holds the fact.
    previous: dict | None = None  # The saved memory it updated, replaced or matched.
    text: str = ""  # What the memory now says.


def _norm(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def _words(text: str) -> str:
    """For comparing texts: lower case, single spaces, no closing punctuation."""
    return _norm(text).rstrip(" .!?;,")


def _is_document(source: str) -> bool:
    return str(source or "").startswith("document:")


def _is_user(subject: str) -> bool:
    return _norm(subject) in _USER_SUBJECTS


def candidates_for(memory: NewMemory) -> list[dict]:
    """The saved memories a new fact is compared with: the one with its name, then the closest in meaning.

    A document's facts are compared only with other documents' and a chat's only with the person's own, so
    a document never rewrites or knocks out something the person said. ``self_knowledge`` memories (Learning
    lessons among them) are never candidates. The person's own record is a
    candidate only while it is short, and only for a fact about the person.
    """
    import row_bot.knowledge_graph as kg

    doc = _is_document(memory.source)
    found: dict[str, dict] = {}

    def keep(entity: dict | None) -> None:
        if not entity or entity["id"] in found or _is_document(entity.get("source", "")) != doc:
            return
        if entity.get("entity_type") == "self_knowledge":
            # Any such memory may be a Learning lesson, tested in its exact text: never rewritten or
            # replaced here (the Dream Cycle's merge hands a lesson over; this does not).
            return
        props = kg_props(entity)
        if str(props.get("status") or "active") in {"superseded", "archived"}:
            return
        if _is_user(entity.get("subject", "")) and (
            not _is_user(memory.subject) or len(entity.get("description") or "") >= USER_PROFILE_CHARS
        ):
            return
        found[entity["id"]] = entity

    keep(kg.find_by_subject(None, memory.subject))
    try:
        stats: dict = {}
        hits = kg.semantic_search(f"{memory.subject}: {memory.content}", top_k=RECONCILE_CANDIDATES + 2,
                                  threshold=0.0, stats=stats)
    except Exception:
        logger.debug("Reconcile candidates: matching by meaning is unavailable", exc_info=True)
        hits = []
        stats = {}
    mean = stats.get("mean")
    for hit in hits:
        score = float(hit.get("score") or 0)
        if mean is not None and mean < 1.0:
            if (score - mean) / (1.0 - mean) < CANDIDATE_CALIBRATED_MIN:
                continue
        elif score < 0.8:
            continue
        keep(hit)
    return list(found.values())[:RECONCILE_CANDIDATES]


def kg_props(entity: dict) -> dict:
    raw = entity.get("properties") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except (TypeError, ValueError):
            raw = {}
    return raw if isinstance(raw, dict) else {}


def _answer():
    from row_bot.providers.background import json_object, structured_answer

    decision = json_object({
        "fact": {"type": "integer"},
        "action": {"type": "string", "enum": sorted(_ACTIONS)},
        "memory_id": {"type": "string", "description": "The saved memory updated, replaced or matched; empty for add."},
        "subject": {"type": "string", "description": "For add: a short specific subject; otherwise empty."},
        "text": {"type": "string", "description": "For update: the memory rewritten; otherwise empty."},
    })
    return structured_answer("memory_reconcile", {"decisions": {"type": "array", "items": decision}})


def _shown(text: str) -> str:
    """What the model is shown of a saved memory; a longer one says it was shortened."""
    return text if len(text) <= MEMORY_TEXT_CHARS else text[:MEMORY_TEXT_CHARS].rstrip() + " … (shortened)"


def _prompt(batch: list[tuple[NewMemory, list[dict]]]) -> str:
    from row_bot.prompts import MEMORY_RECONCILE_PROMPT

    items = []
    for number, (memory, candidates) in enumerate(batch, 1):
        items.append({
            "fact": number,
            "category": memory.category,
            "subject": memory.subject,
            "text": memory.content[:MEMORY_TEXT_CHARS],
            "saved": [{
                "memory_id": entity["id"],
                "category": entity.get("entity_type", ""),
                "subject": entity.get("subject", ""),
                "text": _shown(entity.get("description") or ""),
                "saved_on": str(entity.get("updated_at") or "")[:10],
            } for entity in candidates],
        })
    return MEMORY_RECONCILE_PROMPT.format(
        today=date.today().isoformat(),
        limit=MEMORY_TEXT_CHARS,
        items=json.dumps(items, ensure_ascii=False, indent=1),
    )


def _decide(batch: list[tuple[NewMemory, list[dict]]], *, invoke: Callable[[str], str] | None,
            purpose: str) -> dict[int, dict]:
    """The model's decision for each fact of *batch*, by its number (1-based). Raises when it can't answer."""
    from langchain_core.messages import HumanMessage
    from row_bot.providers.background import active_turn_ids, invoke_background, list_field

    prompt = _prompt(batch)
    try:
        if invoke is not None:
            raw = invoke(prompt) or ""
        else:
            thread_id, generation_id = active_turn_ids()
            raw = invoke_background(purpose, [HumanMessage(content=prompt)], answer=_answer(),
                                    thread_id=thread_id, generation_id=generation_id)
    except Exception as exc:
        raise ReconcileUnavailable(f"The model did not answer ({type(exc).__name__})") from exc
    decisions = list_field(raw, "decisions")
    if decisions is None:
        raise ReconcileUnavailable("The model's answer held no decisions")
    by_fact: dict[int, dict] = {}
    for decision in decisions:
        if isinstance(decision, dict):
            try:
                by_fact.setdefault(int(decision.get("fact")), decision)
            except (TypeError, ValueError):
                continue
    return by_fact


def _contained(memory: NewMemory, candidates: list[dict]) -> dict | None:
    """A saved memory with the fact's name that already says it, word for word."""
    content = _words(memory.content)
    for entity in candidates:
        if _norm(entity.get("subject")) == _norm(memory.subject) and content in _words(entity.get("description")):
            return entity
    return None


def _longer_than_shown(entity: dict) -> bool:
    return len(entity.get("description") or "") > MEMORY_TEXT_CHARS


def _fuller(memory: NewMemory, candidates: list[dict]) -> dict | None:
    """A saved memory with the fact's name whose whole text the fact repeats and adds to."""
    content = _words(memory.content)
    for entity in candidates:
        saved = _words(entity.get("description"))
        if _norm(entity.get("subject")) == _norm(memory.subject) and saved and saved in content:
            return entity
    return None


def plan(memories: list[NewMemory], *, invoke: Callable[[str], str] | None = None,
         purpose: str = "memory_reconcile") -> list[tuple[str, NewMemory, dict | None, str]]:
    """What to do with each new fact, without writing: (action, fact, saved memory, text or subject).

    For ``add`` the last item is the subject to save under; for ``update`` the rewritten text. A reply that
    names a memory that was not offered, or an action it can't do, adds the fact as its own memory: nothing
    the person said is dropped. Raises ``ReconcileUnavailable`` when the model gives no usable reply, before
    anything is written, so the caller can try again later.
    """
    planned: list[tuple[str, NewMemory, dict | None, str]] = []
    pending: list[tuple[NewMemory, list[dict]]] = []
    for memory in memories:
        candidates = candidates_for(memory)
        same = _contained(memory, candidates)
        fuller = _fuller(memory, candidates)
        if same is not None:
            planned.append(("skip", memory, same, ""))
        elif fuller is not None:
            planned.append(("update", memory, fuller, memory.content))
        elif not candidates and not _is_user(memory.subject):
            planned.append(("add", memory, None, memory.subject.strip()))
        else:
            pending.append((memory, candidates))
    for start in range(0, len(pending), RECONCILE_BATCH):
        batch = pending[start:start + RECONCILE_BATCH]
        decisions = _decide(batch, invoke=invoke, purpose=purpose)
        for number, (memory, candidates) in enumerate(batch, 1):
            decision = decisions.get(number) or {}
            action = str(decision.get("action") or "").strip().lower()
            offered = {entity["id"]: entity for entity in candidates}
            target = offered.get(str(decision.get("memory_id") or "").strip())
            text = " ".join(str(decision.get("text") or "").split())
            if action in {"update", "supersede"} and target is not None and _longer_than_shown(target):
                # The model saw only the start of this memory: rewriting it would cut the rest, and replacing
                # it would hide all of it (a long record, such as a log a workflow keeps). Kept as it is.
                planned.append(("add", memory, None, _add_subject(memory, str(decision.get("subject") or ""))))
            elif action == "update" and target is not None and text:
                planned.append(("update", memory, target, text[:MEMORY_TEXT_CHARS * 2]))
            elif action == "supersede" and target is not None:
                planned.append(("supersede", memory, target, _add_subject(memory, str(decision.get("subject") or ""))))
            elif action == "skip" and target is not None:
                planned.append(("skip", memory, target, ""))
            else:
                planned.append(("add", memory, None, _add_subject(memory, str(decision.get("subject") or ""))))
    return planned


def _add_subject(memory: NewMemory, suggested: str) -> str:
    """The subject a new memory is saved under: never the person's own record once that is full."""
    subject = " ".join((suggested or "").split())[:80] or memory.subject.strip()
    if _is_user(subject):
        import row_bot.knowledge_graph as kg

        user = kg.find_by_subject(None, "User")
        if user is not None and len(user.get("description") or "") >= USER_PROFILE_CHARS:
            words = memory.content.split()
            subject = " ".join(words[:6]).rstrip(".,;:") or "About the person"
    return subject


def apply(planned: list[tuple[str, NewMemory, dict | None, str]], *, actor: str = "extraction",
          validate: Callable[[], None] | None = None) -> list[Outcome]:
    """Write what ``plan`` decided. A replaced memory is kept, marked superseded by the new one."""
    import row_bot.memory as memory_db
    import row_bot.memory_evolution as memory_evo

    strict = {"validate": validate} if validate is not None else {}
    outcomes: list[Outcome] = []
    for action, memory, target, value in planned:
        if validate is not None:
            validate()
        if action in {"add", "supersede"}:
            saved = memory_db.save_memory(memory.category, value or memory.subject, memory.content, memory.tags,
                                          source=memory.source, properties=memory.properties, **strict)
            if memory.aliases:
                memory_db.update_memory(saved["id"], memory.content, aliases=memory.aliases, **strict)
            if action == "supersede" and target is not None:
                memory_evo.mark_superseded(target["id"], saved["id"], reason="A newer statement replaces it",
                                           actor=actor, **strict)
            outcomes.append(Outcome(action, memory, saved["id"], target, memory.content))
            continue
        assert target is not None
        aliases = _merged_aliases(target.get("aliases") or "", memory.aliases)
        properties = memory_evo.merge_properties(
            target.get("properties", {}), memory.properties, source=target.get("source", ""),
            entity_type=target.get("entity_type", ""), actor=actor,
            source_context=memory.properties.get("source_context") if memory.properties else None,
            high_authority=actor == "manual",
        )
        text = value if action == "update" else (target.get("description") or "")
        memory_db.update_memory(target["id"], text, properties=properties,
                                **({"aliases": aliases} if aliases != (target.get("aliases") or "") else {}),
                                **strict)
        outcomes.append(Outcome(action, memory, target["id"], target, text))
    return outcomes


def _merged_aliases(old: str, new: str) -> str:
    names = [name.strip() for name in old.split(",") if name.strip()]
    seen = {name.lower() for name in names}
    for name in (part.strip() for part in new.split(",")):
        if name and name.lower() not in seen:
            names.append(name)
            seen.add(name.lower())
    return ", ".join(names)


def reconcile(memories: list[NewMemory], *, actor: str = "extraction", invoke: Callable[[str], str] | None = None,
              purpose: str = "memory_reconcile", validate: Callable[[], None] | None = None) -> list[Outcome]:
    """Decide and write: ``plan`` then ``apply``."""
    return apply(plan(memories, invoke=invoke, purpose=purpose), actor=actor, validate=validate)
