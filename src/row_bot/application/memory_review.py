"""Review memory: a clean-up the person runs over their whole long-term memory, then approves in a list.

A review is a *run*. A background job proposes one action per memory, each with a plain reason:

* ``keep``: nothing to change (most memories);
* ``remove``: test data from development, out of date, not about the person, a workflow's run log, or the
  same as another memory;
* ``rewrite``: right but badly written (run-together words, "next week" with no date, the old name Thoth),
  rewritten to at most 600 characters;
* ``merge``: the same as another memory in other words; the two become one (the Dream Cycle's merge);
* ``split``: the person's own record grown into a long list of appended sentences becomes a short profile
  plus separate memories, each checked against the rest so nothing is saved twice.

Cheap checks run first (test patterns, already replaced or archived, squeezed run-log text, relative time
with no date, near-duplicate pairs by calibrated similarity); then the person's default chat model reads the
memories in batches. Memories are data, never instructions.

Two kinds of memory are never changed unless the person picks it themselves, with a confirmation naming it:
a *long record* (longer than the 1,200 characters the model is shown: a workflow's register of who was
emailed is one, and losing it could mean emailing someone twice) and a ``self_knowledge`` memory (it may be
a Learning lesson, tested in its exact text). They are never rewritten.

Nothing changes before Apply. Apply first copies memory.db with SQLite's backup (``memory.db.pre-review-
<timestamp>``) and changes nothing if that fails; it refuses when a memory changed since the review was
made; it writes a receipt holding every removed or changed memory's full row and relations, so Undo puts
them back exactly, for 30 days. Removal goes through ``delete_entity`` (Learning's lesson marking runs);
merges through the Dream Cycle's merge (the lesson hand-over kept).

State lives in the data folder under ``memory_review/<run_id>/``: the proposals until the run is applied or
let go, then only the receipt.

For a maintainer: ``python -m row_bot.application.memory_review --propose --data-dir <dir>`` prints a
review's proposals as JSON without applying anything.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)

FOLDER = "memory_review"
LONG_RECORD_CHARS = 1_200  # What the model is shown of a memory (the Dream Cycle's pair bound).
TEXT_LIMIT = 600  # A rewritten or merged memory.
PROFILE_CHARS = 1_500  # The person's own record after a split.
BATCH = 8  # Memories read in one model call.
SPLIT_CHUNK_CHARS = 4_000  # Of the person's record, per model call.
MANY_MEMORIES = 300  # Past this, the first step also says how many model calls a review takes.
PAIR_LIMIT = 500
NEIGHBOURS = 3  # Tidying up after an import: the closest saved memories looked at with each imported one.
NEIGHBOUR_CALIBRATED_MIN = 0.25
VIEW_TEXT_CHARS = 2_000
IDLE_SECONDS = 24 * 3600
KEEP_SECONDS = 30 * 24 * 3600
RUNS_HELD = 4
BACKUP_PREFIX = "memory.db.pre-review-"
_RUN_ID = re.compile(r"[0-9a-f]{32}")
_BATCH_ID = re.compile(r"[0-9a-f]{32}")
_now = time.time
_LOCK = threading.RLock()

REMOVE_REASONS = ("test_data", "outdated", "not_about_the_person", "run_log", "duplicate")
_PERSONAL = frozenset({"person", "fact", "preference"})
_USER_NAMES = frozenset({"user", "me", "the user", "myself"})
_SPLIT_CATEGORIES = frozenset({"preference", "project", "fact", "person", "event", "place", "organisation",
                               "skill", "concept"})
_LINKS = {"preference": "prefers", "project": "works_on", "skill": "has_skill", "person": "knows"}

# Cheap signals --------------------------------------------------------------------------------------------

_TEST_SURE = re.compile(r"__MTEST_|\blorem ipsum\b|@example\.(?:com|org|net)\b|\bexample\.(?:com|org|net)\b",
                        re.IGNORECASE)
_TEST_WORDS = re.compile(r"\b(?:tests?|testing|fixtures?|dummy|placeholder|sample (?:user|person|data)|"
                         r"john doe|jane doe|foo ?bar)\b", re.IGNORECASE)
_SQUEEZED = re.compile(r"[A-Za-z0-9]{28,}")
_RELATIVE = re.compile(
    r"\b(?:today|tonight|tomorrow|yesterday|currently|right now|at the moment|nowadays|recently|soon|"
    r"this (?:week|weekend|month|year|morning|afternoon|evening)|"
    r"(?:next|last) (?:week|weekend|month|year|night|monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b",
    re.IGNORECASE)
_DATED = re.compile(
    r"\b(?:19|20)\d{2}\b|\b\d{1,2}[/.]\d{1,2}[/.]\d{2,4}\b|"
    r"\b\d{1,2}(?:st|nd|rd|th)? (?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\b|"
    r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]* \d{1,2}(?:st|nd|rd|th)?\b", re.IGNORECASE)
_OLD_NAME = re.compile(r"\bthoth\b", re.IGNORECASE)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
# A way to reach the person: the label of a contact detail next to its value, or a home address. A sentence
# that only mentions email or Telegram ("sends outreach emails", "a Telegram channel") is not a contact detail.
_CONTACT_LABELS = re.compile(
    r"\b(?:telegram|whatsapp|signal|skype|discord)\b[^.]{0,40}?\b(?:id|number|handle|user ?name)\b|"
    r"\b(?:lives at|home address|postal address|street address|postcode|post code|zip code)\b",
    re.IGNORECASE)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
# Records grown by appending were joined with ".." and no space ("...gmail.com..User's name"): those are
# sentence ends too (an ellipsis "..." is not).
_GLUED = re.compile(r"(?<![.])\.\.(?![.])\s*")


def _has_contact(text: str) -> bool:
    if _EMAIL.search(text) or _CONTACT_LABELS.search(text):
        return True
    return any(sum(char.isdigit() for char in match.group()) >= 9 for match in _PHONE.finditer(text))


def _sentences(text: str) -> list[str]:
    """A record's sentences, including ones an earlier append glued together."""
    return [part.strip() for part in _SENTENCE.split(_GLUED.sub(". ", _norm(text))) if part.strip()]


def _props(entity: dict) -> dict:
    from row_bot.memory_evolution import get_properties

    return get_properties(entity)


def _status(entity: dict) -> str:
    from row_bot.memory_evolution import normalize_status

    return normalize_status(_props(entity).get("status"))


def _is_user(entity: dict) -> bool:
    names = [entity.get("subject") or "", *(entity.get("aliases") or "").split(",")]
    return any(" ".join(name.lower().split()) in _USER_NAMES for name in names)


def _is_lesson(entity: dict) -> bool:
    return str(entity.get("entity_type") or "") == "self_knowledge"


def _long(entity: dict) -> bool:
    return len(entity.get("description") or "") > LONG_RECORD_CHARS


def _family(entity: dict) -> str:
    kind = str(entity.get("entity_type") or "")
    return "personal" if kind in _PERSONAL else kind


def _norm(text: str) -> str:
    return " ".join(str(text or "").split())


def signals(entity: dict) -> list[str]:
    """What the cheap checks see in one memory, in words the model reads as hints."""
    text = f"{entity.get('subject') or ''} {entity.get('description') or ''}"
    found = []
    if _TEST_SURE.search(text) or _TEST_WORDS.search(text):
        found.append("test or fixture words")
    kind = str(entity.get("entity_type") or "")
    squeezed = len(_SQUEEZED.findall(entity.get("description") or ""))
    if squeezed >= 3 or (kind in {"preference", "project"} and _long(entity)):
        found.append("looks like an automated run log")
    elif squeezed:
        found.append("words run together")
    if _RELATIVE.search(text) and not _DATED.search(text):
        found.append("relative time without a date")
    if _OLD_NAME.search(text):
        found.append("old app name Thoth")
    return found


# Places ---------------------------------------------------------------------------------------------------

def _root() -> Path:
    from row_bot.application.client_platform import ClientPlatformError
    from row_bot.data_paths import get_row_bot_data_dir

    root = get_row_bot_data_dir() / FOLDER
    if root.is_symlink():
        raise ClientPlatformError("review_storage_unavailable")
    root.mkdir(mode=0o700, exist_ok=True)
    return root


def _remove(path: Path) -> None:
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    except OSError:
        logger.warning("Memory review files stay until the next sweep", exc_info=True)


def _write_json(path: Path, value: Any) -> None:
    handle, temporary = tempfile.mkstemp(prefix="tmp-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as sink:
            json.dump(value, sink, ensure_ascii=False)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# The model ------------------------------------------------------------------------------------------------

def _model() -> dict | None:
    """The default chat model, named before anything is sent to it."""
    try:
        from row_bot.providers.background import background_model_ref
        from row_bot.providers.selection import parse_model_ref, provider_display_label

        reference = background_model_ref("dream")
        parsed = parse_model_ref(reference) if reference else None
    except Exception:
        logger.debug("Memory review: the default chat model could not be read", exc_info=True)
        return None
    if not parsed:
        return None
    provider, model_id = parsed
    return {"name": model_id[:200], "provider": provider_display_label(provider)[:120],
            "on_this_computer": provider in {"ollama", "local"}}


class ModelUnavailable(RuntimeError):
    pass


def _call(prompt: str, invoke: Callable[[str], str] | None, *, answer: Any = None, max_tokens: int = 4096) -> str:
    if invoke is not None:
        return invoke(prompt) or ""
    from langchain_core.messages import HumanMessage
    from row_bot.providers.background import invoke_background

    return invoke_background("dream", [HumanMessage(content=prompt)], answer=answer, max_tokens=max_tokens)


def _review_answer():
    from row_bot.providers.background import json_object, structured_answer

    decision = json_object({
        "memory": {"type": "string"},
        "action": {"type": "string", "enum": ["keep", "remove", "rewrite", "merge"]},
        "reason": {"type": "string"},
        "text": {"type": "string"},
        "into": {"type": "string"},
    })
    return structured_answer("memory_review", {"decisions": {"type": "array", "items": decision}})


def _split_answer():
    from row_bot.providers.background import json_object, structured_answer

    memory = json_object({"category": {"type": "string"}, "subject": {"type": "string"}, "text": {"type": "string"}})
    return structured_answer("memory_review_split", {
        "profile": {"type": "array", "items": {"type": "string"}},
        "memories": {"type": "array", "items": memory},
    })


# Proposals ------------------------------------------------------------------------------------------------

def _shown(entity: dict) -> str:
    return (entity.get("description") or "")[:VIEW_TEXT_CHARS]


def _proposal(entity: dict, action: str, code: str, reason: str, **extra: Any) -> dict:
    marks = []
    if _long(entity):
        marks.append("long_record")
    if _is_lesson(entity):
        marks.append("lesson")
    if _is_user(entity):
        marks.append("person_record")
    proposal = {
        "proposal_id": f"{'split' if action == 'split' else 'memory'}:{entity['id']}",
        "memory_id": entity["id"],
        "action": action,
        "reason_code": code,
        "reason": reason[:300],
        "subject": str(entity.get("subject") or "")[:200],
        "category": str(entity.get("entity_type") or ""),
        "text": _shown(entity),
        "length": len(entity.get("description") or ""),
        "proposed_text": None,
        "into": None,
        "marks": marks,
        # A long record, a lesson and the person's own record change only when the person picks it.
        "selected": action not in {"keep", "split"} and not marks,
        "removable": action == "keep" and bool({"long_record", "lesson"} & set(marks))
                     and "person_record" not in marks,
        "split": None,
        "involved": {entity["id"]: str(entity.get("updated_at") or "")},
    }
    proposal.update(extra)
    return proposal


def _target(entity: dict) -> dict:
    return {"memory_id": entity["id"], "subject": str(entity.get("subject") or "")[:200],
            "text": _shown(entity)}


def _first_pass(entity: dict, by_id: dict[str, dict]) -> dict | None:
    """The proposal the cheap checks settle on their own, or None for the model to read it."""
    found = signals(entity)
    if _is_user(entity):
        return _proposal(entity, "keep", "person_record", "Your own record")
    if _is_lesson(entity):
        return _proposal(entity, "keep", "lesson", "A lesson Row-Bot learned; kept exactly as it is")
    if _long(entity):
        length = len(entity.get("description") or "")
        hint = "; it looks like a workflow's log" if "looks like an automated run log" in found else ""
        return _proposal(entity, "keep", "long_record",
                         f"Kept — long record, check it yourself ({length:,} characters{hint})")
    if str(entity.get("source") or "").startswith("document:"):
        return _proposal(entity, "keep", "document", "From a document; it goes when the document is removed")
    status = _status(entity)
    if status == "superseded":
        newer = by_id.get(str(_props(entity).get("superseded_by") or ""))
        reason = (f"Replaced by a newer memory: “{newer.get('subject', '')}”" if newer
                  else "Replaced by a newer memory")
        return _proposal(entity, "remove", "outdated", reason, into=_target(newer) if newer else None)
    if status == "archived":
        return _proposal(entity, "remove", "outdated", "Archived earlier; Row-Bot no longer uses it")
    if _TEST_SURE.search(f"{entity.get('subject') or ''} {entity.get('description') or ''}"):
        return _proposal(entity, "remove", "test_data", "Test data from development")
    return None


def _rewrite_reason(entity: dict) -> str:
    found = signals(entity)
    if "relative time without a date" in found:
        word = _RELATIVE.search(f"{entity.get('subject') or ''} {entity.get('description') or ''}")
        return f"Says “{word.group().lower()}” without a date; the date is written out" if word else \
            "The date is written out"
    if "old app name Thoth" in found:
        return "Uses the old name Thoth; now says Row-Bot"
    if "looks like an automated run log" in found or "words run together" in found:
        return "Words run together; rewritten to read clearly"
    return "Rewritten to read clearly"


_REMOVE_WORDS = {
    "test_data": "Test data from development",
    "outdated": "Out of date",
    "not_about_the_person": "Not about you: general knowledge or a passing topic",
    "run_log": "A workflow's run log, not something about you",
}


# Words two names can share without being about the same thing.
_GENERIC_NAME_WORDS = frozenset({
    "ai", "the", "and", "of", "for", "to", "in", "on", "with", "row", "bot", "rowbot", "app", "apps", "agent",
    "agents", "project", "plan", "plans", "style", "user", "users", "my", "new", "old", "personal", "system",
})
CLOSE_ENOUGH_WITHOUT_A_NAME = 0.85  # calibrated similarity at which two differently named memories may merge


def _names(entity: dict) -> set[str]:
    words = f"{entity.get('subject') or ''} {(entity.get('aliases') or '').replace(',', ' ')}"
    return {word for word in re.findall(r"[a-z0-9]+", words.casefold())
            if len(word) > 1 and word not in _GENERIC_NAME_WORDS}


def _same_thing_allowed(entity: dict, other: dict, closeness: dict[frozenset, float]) -> bool:
    """Whether two memories may become one: a pair the near-duplicate search found, and a distinctive word in
    both names (for two people, always), unless they are very close. Measured on a real profile (Nov 2026):
    without this the model merged two different people and related-but-different topics, such as "Linux
    support" into "Docker" or a company into a team."""
    close = closeness.get(frozenset((entity["id"], other["id"])))
    if close is None:
        return False
    if _names(entity) & _names(other):
        return True
    if entity.get("entity_type") == "person" or other.get("entity_type") == "person":
        return False
    return close >= CLOSE_ENOUGH_WITHOUT_A_NAME


def _decided(entity: dict, decision: dict, offered: dict[str, dict],
             closeness: dict[frozenset, float] | None = None) -> dict:
    """One model decision as a proposal; an answer it can't act on keeps the memory."""
    action = str(decision.get("action") or "").strip().lower()
    reason = str(decision.get("reason") or "").strip().lower()
    text = _norm(decision.get("text"))
    other = offered.get(str(decision.get("into") or "").strip())
    if other is not None and other["id"] == entity["id"]:
        other = None
    if other is not None and (action == "merge" or reason == "duplicate") \
            and not _same_thing_allowed(entity, other, closeness or {}):
        return _proposal(entity, "keep", "keep", "Nothing to change")
    if action == "remove" and reason in REMOVE_REASONS:
        if reason == "duplicate":
            if other is None:
                return _proposal(entity, "keep", "keep", "Nothing to change")
            return _proposal(entity, "remove", "duplicate", f"Same as “{other.get('subject', '')}”",
                             into=_target(other))
        if reason == "outdated" and other is not None:
            return _proposal(entity, "remove", "outdated", f"Replaced by a newer memory: “{other.get('subject', '')}”",
                             into=_target(other))
        return _proposal(entity, "remove", reason, _REMOVE_WORDS[reason])
    if action == "rewrite" and text and len(text) <= TEXT_LIMIT and text != _norm(entity.get("description")):
        return _proposal(entity, "rewrite", "rewrite", _rewrite_reason(entity), proposed_text=text)
    if (action == "merge" and other is not None and text and len(text) <= TEXT_LIMIT
            and _family(other) == _family(entity)):
        return _proposal(entity, "merge", "merge", f"Same as “{other.get('subject', '')}”; the two become one",
                         proposed_text=text, into=_target(other),
                         involved={entity["id"]: str(entity.get("updated_at") or ""),
                                   other["id"]: str(other.get("updated_at") or "")})
    return _proposal(entity, "keep", "keep", "Nothing to change")


def _batches(pool: list[dict], pairs: list[tuple[dict, dict, float]]) -> list[list[dict]]:
    """Memories in model batches, each near-duplicate pair in the same batch."""
    groups: list[list[dict]] = []
    placed: set[str] = set()
    for older, newer, _similarity in pairs:
        groups.append([older, newer])
        placed.update((older["id"], newer["id"]))
    groups += [[entity] for entity in pool if entity["id"] not in placed]
    batches: list[list[dict]] = []
    for group in groups:
        if not batches or len(batches[-1]) + len(group) > BATCH:
            batches.append([])
        batches[-1].extend(group)
    return batches


def _review_prompt(batch: list[dict], similar: dict[str, str]) -> str:
    from row_bot.prompts import MEMORY_REVIEW_PROMPT

    items = []
    for entity in batch:
        item = {"id": entity["id"], "category": entity.get("entity_type", ""), "subject": entity.get("subject", ""),
                "text": entity.get("description") or "", "saved_on": str(entity.get("updated_at") or "")[:10],
                "signals": signals(entity)}
        if entity["id"] in similar:
            item["similar_to"] = similar[entity["id"]]
        items.append(item)
    return MEMORY_REVIEW_PROMPT.format(today=date.today().isoformat(), limit=TEXT_LIMIT,
                                       items=json.dumps(items, ensure_ascii=False, indent=1))


def _ask_batch(batch: list[dict], similar: dict[str, str], invoke: Callable[[str], str] | None) -> dict[str, dict]:
    from row_bot.providers.background import list_field

    try:
        raw = _call(_review_prompt(batch, similar), invoke, answer=None if invoke else _review_answer())
    except Exception as exc:
        raise ModelUnavailable(type(exc).__name__) from exc
    decisions = list_field(raw, "decisions")
    if decisions is None:
        raise ModelUnavailable("no decisions")
    found: dict[str, dict] = {}
    for decision in decisions:
        if isinstance(decision, dict):
            found.setdefault(str(decision.get("memory") or "").strip(), decision)
    return found


def _settle(proposals: dict[str, dict], by_id: dict[str, dict]) -> None:
    """One change per memory: a memory another merges into stays; one going away takes nothing in."""
    gone: set[str] = set()
    targets: set[str] = set()
    for memory_id, proposal in proposals.items():
        action = proposal["action"]
        if action not in {"remove", "merge"}:
            continue
        into = (proposal.get("into") or {}).get("memory_id")
        folds = action == "merge" or proposal["reason_code"] == "duplicate"
        if memory_id in targets or (folds and (into in gone or into not in proposals)):
            entity = by_id[memory_id]
            proposals[memory_id] = _proposal(entity, "keep", "keep", "Nothing to change")
            continue
        gone.add(memory_id)
        if folds:
            targets.add(into)
            kept = proposals[into]
            if kept["action"] in {"remove", "merge", "rewrite"}:
                proposals[into] = _proposal(by_id[into], "keep", "keep",
                                            f"Kept; “{proposal['subject']}” {'merges into it' if action == 'merge' else 'repeats it'}")


# The person's own record ----------------------------------------------------------------------------------

def _split_chunks(sentences: list[str]) -> list[str]:
    chunks: list[str] = []
    for sentence in sentences:
        if chunks and len(chunks[-1]) + len(sentence) + 1 <= SPLIT_CHUNK_CHARS:
            chunks[-1] += " " + sentence
        else:
            chunks.append(sentence[:SPLIT_CHUNK_CHARS * 2])
    return chunks


def _subject_from(text: str) -> str:
    words = re.sub(r"^(?:the user|user|they|he|she)\s+(?:is|are|has|have|was)?\s*", "", text, flags=re.I).split()
    subject = " ".join(words[:5]).rstrip(".,;:!?")
    return (subject[:1].upper() + subject[1:])[:80] or "About you"


def _split(user: dict, invoke: Callable[[str], str] | None, step: Callable[[], None], run_id: str) -> dict:
    """The person's own record as a short profile and separate memories; raises ModelUnavailable."""
    import row_bot.memory_evolution as memory_evo
    import row_bot.memory_reconcile as reconcile
    from row_bot.prompts import MEMORY_REVIEW_SPLIT_PROMPT
    from row_bot.providers.background import list_field

    sentences = _sentences(user.get("description"))
    # Contact details stay on the person's record: the model never sees them, so they can't be spread.
    contacts = [sentence for sentence in sentences if _has_contact(sentence)]
    rest = [sentence for sentence in sentences if not _has_contact(sentence)]
    chunks = _split_chunks(rest)
    profile: list[str] = []
    facts: list[tuple[str, str, str]] = []
    for number, chunk in enumerate(chunks, 1):
        prompt = MEMORY_REVIEW_SPLIT_PROMPT.format(part=number, parts=len(chunks), today=date.today().isoformat(),
                                                   text=chunk)
        try:
            raw = _call(prompt, invoke, answer=None if invoke else _split_answer())
        except Exception as exc:
            raise ModelUnavailable(type(exc).__name__) from exc
        lines = list_field(raw, "profile")
        found = list_field(raw, "memories")
        if not lines and not found:
            # Nothing came back for this part: trimming the record now would lose what it says.
            raise ModelUnavailable("no split")
        profile += [_norm(line) for line in lines or [] if isinstance(line, str) and _norm(line)]
        for item in found or []:
            if not isinstance(item, dict):
                continue
            category = str(item.get("category") or "").strip().lower()
            subject = _norm(item.get("subject"))[:80]
            text = _norm(item.get("text"))[:TEXT_LIMIT]
            if not text:
                continue
            if _has_contact(text):
                profile.append(text)  # never spread into another memory
                continue
            if category not in _SPLIT_CATEGORIES:
                category = "fact"
            if not subject or subject.lower() in _USER_NAMES:
                subject = _subject_from(text)
            facts.append((category, subject, text))
        step()
    kept: list[str] = list(dict.fromkeys(contacts))
    size = sum(len(line) + 1 for line in kept)
    for line in dict.fromkeys(profile):
        if line in kept:
            continue
        if size + len(line) + 1 <= PROFILE_CHARS:
            kept.append(line)
            size += len(line) + 1
        else:
            facts.append(("fact", _subject_from(line), line[:TEXT_LIMIT]))
    seen: set[str] = set()
    memories = []
    for category, subject, text in facts:
        if text.lower() in seen:
            continue
        seen.add(text.lower())
        source = "review"
        properties = memory_evo.normalize_properties(
            {"extraction_method": "review", "review_run": run_id,
             "source_context": {"kind": "review", "display_name": "Review memory", "run_id": run_id}},
            source=source, entity_type=category)
        memories.append(reconcile.NewMemory(category, subject, text, source=source, properties=properties))
    planned = []
    if memories:
        try:
            planned = reconcile.plan(memories, invoke=(lambda prompt: _call(prompt, invoke)) if invoke else (
                lambda prompt: _call(prompt, None, answer=reconcile._answer(), max_tokens=2048)), purpose="dream")
        except reconcile.ReconcileUnavailable as exc:
            raise ModelUnavailable("reconcile") from exc
    split_memories = []
    involved = {user["id"]: str(user.get("updated_at") or "")}
    for action, memory, target, value in planned:
        entry = {"category": memory.category, "subject": memory.subject, "text": memory.content,
                 "action": "skip", "saved_subject": None, "saved_text": None, "new_text": None, "target": None}
        if action == "add":
            entry.update(action="add", subject=value or memory.subject)
        elif action == "update" and target is not None:
            entry.update(action="update", saved_subject=target.get("subject") or "",
                         saved_text=_shown(target), new_text=value[:TEXT_LIMIT * 2], target=target["id"])
            involved[target["id"]] = str(target.get("updated_at") or "")
        elif target is not None:
            # Already saved, or a saved memory says it differently: that one, newer, is kept.
            entry.update(saved_subject=target.get("subject") or "", saved_text=_shown(target))
        split_memories.append(entry)
    text = " ".join(kept)
    adds = sum(1 for entry in split_memories if entry["action"] == "add")
    return _proposal(
        user, "split", "split",
        f"Your own record has grown to {len(user.get('description') or ''):,} characters; it becomes a short "
        f"profile and {adds:,} separate memor{'y' if adds == 1 else 'ies'}. Contact details stay on it",
        proposed_text=text, split={"contacts_kept": len(contacts), "memories": split_memories},
        involved=involved)


# Scope ----------------------------------------------------------------------------------------------------

def _entities() -> list[dict]:
    import row_bot.knowledge_graph as kg

    return list(kg.iter_entities_snapshot())


def _imported(entities: list[dict], batch_id: str) -> list[dict]:
    return [entity for entity in entities if str(_props(entity).get("import_batch") or "") == batch_id]


def _scope(entities: list[dict], import_batch: str | None) -> list[dict]:
    """Every memory, or one import's memories with the saved ones closest to each."""
    if not import_batch:
        return entities
    import row_bot.knowledge_graph as kg

    by_id = {entity["id"]: entity for entity in entities}
    chosen = {entity["id"]: entity for entity in _imported(entities, import_batch)}
    for entity in list(chosen.values()):
        stats: dict = {}
        try:
            hits = kg.semantic_search(kg._entity_text(entity), top_k=NEIGHBOURS + 1, threshold=0.0, stats=stats)
        except Exception:
            logger.info("Memory review: matching by meaning is unavailable; only the imported memories are read")
            break
        mean = stats.get("mean")
        mean = float(mean) if isinstance(mean, (int, float)) and mean < 1 else 0.0
        for hit in hits:
            other = by_id.get(str(hit.get("id") or ""))
            if other is None or other["id"] in chosen:
                continue
            if (float(hit.get("score") or 0.0) - mean) / (1 - mean) >= NEIGHBOUR_CALIBRATED_MIN:
                chosen[other["id"]] = other
    return list(chosen.values())


def propose(*, import_batch: str | None = None, invoke: Callable[[str], str] | None = None,
            progress: Callable[[int, int], None] | None = None, run_id: str = "", split: bool = True) -> dict:
    """Every proposal for a review, without writing anything: ``{"proposals": [...], "warnings": [...]}``.

    ``invoke`` (prompt → reply) stands in for the default chat model; ``progress(done, total)`` counts model
    calls and may raise to stop. Raises ``ModelUnavailable`` when the model answered none of them. With
    ``split`` False the person's own record is only kept.
    """
    import row_bot.dream_cycle as dream

    entities = _entities()
    by_id = {entity["id"]: entity for entity in entities}
    scope = _scope(entities, import_batch)
    proposals: dict[str, dict] = {}
    pool: list[dict] = []
    user = None
    for entity in scope:
        settled = _first_pass(entity, by_id)
        if settled is None:
            pool.append(entity)
            proposals[entity["id"]] = _proposal(entity, "keep", "keep", "Nothing to change")
        else:
            proposals[entity["id"]] = settled
            if split and settled["reason_code"] == "person_record" \
                    and len(entity.get("description") or "") > PROFILE_CHARS:
                user = entity
    try:
        pairs = dream._find_pair_candidates(pool, limit=PAIR_LIMIT) if len(pool) > 1 else []
    except Exception:
        logger.info("Memory review: near-duplicates could not be looked for", exc_info=True)
        pairs = []
    similar: dict[str, str] = {}
    closeness: dict[frozenset, float] = {}
    for older, newer, similarity in pairs:
        similar[older["id"]], similar[newer["id"]] = newer["id"], older["id"]
        closeness[frozenset((older["id"], newer["id"]))] = float(similarity)
    batches = _batches(pool, pairs)
    split_calls = len(_split_chunks([part for part in _sentences(user.get("description"))
                                     if part.strip()])) if user else 0
    total = len(batches) + split_calls
    done = 0
    if progress:
        progress(0, total)
    warnings: list[str] = []
    asked = failed = 0
    for batch in batches:
        asked += 1
        offered = {entity["id"]: entity for entity in batch}
        try:
            decisions = _ask_batch(batch, similar, invoke)
        except ModelUnavailable:
            failed += 1
            for entity in batch:
                proposals[entity["id"]] = _proposal(entity, "keep", "unchecked",
                                                    "Row-Bot couldn't check this one; kept as it is")
        else:
            for entity in batch:
                proposals[entity["id"]] = _decided(entity, decisions.get(entity["id"]) or {}, offered, closeness)
        done += 1
        if progress:
            progress(done, total)
    if asked and failed == asked:
        raise ModelUnavailable("every call failed")
    if failed:
        warnings.append(f"Row-Bot couldn't check {failed * BATCH:,} or fewer memories; they are kept as they are.")
    _settle(proposals, {**by_id})
    if user is not None:
        def step() -> None:
            nonlocal done
            done += 1
            if progress:
                progress(done, total)

        try:
            proposals[user["id"]] = _split(user, invoke, step, run_id)
        except ModelUnavailable:
            warnings.append("Your own record couldn't be split this time; it is kept as it is.")
    ordered = sorted(proposals.values(), key=lambda item: (item["subject"].casefold(), item["memory_id"]))
    return {"proposals": ordered, "warnings": warnings}


def overview(import_batch: str | None = None) -> dict:
    """What a review would read, and with what model, shown before it runs."""
    if import_batch is not None and not _BATCH_ID.fullmatch(import_batch):
        raise _error("invalid_query")
    entities = _entities()
    scope = _imported(entities, import_batch) if import_batch else entities
    by_id = {entity["id"]: entity for entity in entities}
    read = sum(1 for entity in scope if _first_pass(entity, by_id) is None)
    if import_batch:
        read = min(len(entities), read * (1 + NEIGHBOURS))
    users = [entity for entity in scope if _is_user(entity) and len(entity.get("description") or "") > PROFILE_CHARS]
    split_calls = sum(math.ceil(len(entity.get("description") or "") / SPLIT_CHUNK_CHARS) for entity in users)
    return {
        "memories": len(scope),
        "model_calls": math.ceil(read / BATCH) + split_calls if read or split_calls else 0,
        "many": len(scope) > MANY_MEMORIES,
        "import_batch": import_batch,
        "model": _model(),
        "recent": recent(),
    }


# Runs -----------------------------------------------------------------------------------------------------

@dataclass(eq=False)
class _Run:
    id: str
    session_id: str
    folder: Path
    touched: float
    import_batch: str | None = None
    job: dict | None = None
    cancel: threading.Event | None = None
    proposals: list[dict] | None = None
    revision: str | None = None
    warnings: list[str] = field(default_factory=list)
    stale: bool = False
    receipt: dict | None = None


_RUNS: dict[str, _Run] = {}


class _Cancelled(Exception):
    pass


def _error(code: str) -> Exception:
    from row_bot.application.client_platform import ClientPlatformError

    return ClientPlatformError(code)


def _counts(proposals: list[dict] | None) -> dict:
    counts = {action: 0 for action in ("remove", "merge", "rewrite", "split", "keep")}
    for proposal in proposals or []:
        counts[proposal["action"]] += 1
    return counts


def _view(run: _Run) -> dict:
    return {
        "run_id": run.id,
        "status": "applied" if run.receipt is not None else "open",
        "import_batch": run.import_batch,
        "job": dict(run.job) if run.job else None,
        "counts": _counts(run.proposals),
        "revision": run.revision,
        "stale": run.stale,
        "warnings": run.warnings[:20],
        "model": _model(),
        "receipt": _receipt_view(run.receipt) if run.receipt else None,
    }


def _run(session_id: str, run_id: str) -> _Run:
    run = _RUNS.get(run_id)
    if run is None:
        raise _error("review_expired")
    if run.session_id != session_id:
        raise _error("not_found")
    run.touched = _now()
    return run


def _retire(run: _Run) -> None:
    if run.cancel is not None:
        run.cancel.set()
    _RUNS.pop(run.id, None)
    if run.receipt is None:
        _remove(run.folder)
    else:
        _remove(run.folder / "proposals.json")


def _backups() -> list[Path]:
    import row_bot.knowledge_graph as kg

    folder = Path(kg.DB_PATH).parent
    return sorted(folder.glob(BACKUP_PREFIX + "*"), key=lambda path: path.name)


def sweep() -> None:
    """Runs let go or abandoned, receipts past their 30 days, and backups older than that but the newest."""
    try:
        with _LOCK:
            now = _now()
            for run in list(_RUNS.values()):
                running = run.job is not None and run.job.get("status") == "running"
                if run.receipt is None and not running and now - run.touched > IDLE_SECONDS:
                    _retire(run)
            for entry in _root().iterdir():
                if not _RUN_ID.fullmatch(entry.name) or entry.is_symlink() or not entry.is_dir():
                    continue
                if entry.name in _RUNS:
                    continue
                receipt = _read_json(entry / "receipt.json")
                if not isinstance(receipt, dict) or now - float(receipt.get("applied_ts") or 0) > KEEP_SECONDS:
                    _remove(entry)
                else:
                    _remove(entry / "proposals.json")
            for backup in _backups()[:-1]:
                if now - backup.stat().st_mtime > KEEP_SECONDS:
                    _remove(backup)
    except OSError:
        logger.warning("Memory reviews could not be swept", exc_info=True)


def close_runs() -> None:
    """At shutdown: a review never applied doesn't outlive the server."""
    with _LOCK:
        for run in list(_RUNS.values()):
            _retire(run)


def begin(session_id: str, *, import_batch: str | None = None, invoke: Callable[[str], str] | None = None) -> dict:
    """Start a review in the background. A window has one at a time: starting again lets its earlier one go."""
    if import_batch is not None and not _BATCH_ID.fullmatch(import_batch):
        raise _error("invalid_command")
    if invoke is None and _model() is None:
        raise _error("model_selection_required")
    with _LOCK:
        for run in [item for item in _RUNS.values() if item.session_id == session_id and item.receipt is None]:
            _retire(run)
        sweep()
        if sum(1 for item in _RUNS.values() if item.receipt is None) >= RUNS_HELD:
            raise _error("rate_limited")
        run_id = uuid4().hex
        folder = _root() / run_id
        folder.mkdir(mode=0o700)
        run = _Run(run_id, session_id, folder, _now(), import_batch=import_batch)
        _RUNS[run_id] = run
        cancel = threading.Event()
        run.cancel = cancel
        run.job = {"kind": "review", "status": "running", "done": 0, "total": None, "code": None}

        def progress(done: int, total: int) -> None:
            if cancel.is_set():
                raise _Cancelled
            with _LOCK:
                if run.cancel is cancel and run.job is not None:
                    run.job.update(done=done, total=total)

        def work() -> None:
            outcome: dict[str, Any] = {"status": "completed"}
            try:
                found = propose(import_batch=import_batch, invoke=invoke, progress=progress, run_id=run_id)
                with _LOCK:
                    if cancel.is_set() or run.cancel is not cancel:
                        return
                    run.proposals = found["proposals"]
                    run.warnings = found["warnings"]
                    run.revision = hashlib.sha256(json.dumps(run.proposals, sort_keys=True).encode()).hexdigest()
                    _write_json(run.folder / "proposals.json", {"revision": run.revision,
                                                               "proposals": run.proposals})
            except _Cancelled:
                outcome = {"status": "cancelled"}
            except ModelUnavailable:
                outcome = {"status": "failed", "code": "review_model_unavailable"}
            except Exception:
                logger.error("Memory review failed", exc_info=True)
                outcome = {"status": "failed", "code": "review_failed"}
            with _LOCK:
                if run.job is not None and run.cancel is cancel:
                    run.job.update(outcome)
                    run.touched = _now()

        threading.Thread(target=work, name="row-bot-memory-review", daemon=True).start()
        return _view(run)


def status(session_id: str, run_id: str) -> dict:
    with _LOCK:
        return _view(_run(session_id, run_id))


def cancel_job(session_id: str, run_id: str) -> dict:
    with _LOCK:
        run = _run(session_id, run_id)
        if run.job is not None and run.job.get("status") == "running" and run.cancel is not None:
            run.cancel.set()
            run.job.update(status="cancelled")
            run.cancel = None
        return _view(run)


def discard(session_id: str, run_id: str) -> None:
    with _LOCK:
        run = _run(session_id, run_id)
        if run.receipt is None:
            _retire(run)


_PUBLIC = ("proposal_id", "memory_id", "action", "reason_code", "reason", "subject", "category", "text", "length",
           "proposed_text", "into", "marks", "selected", "removable")


def _proposal_view(proposal: dict) -> dict:
    view = {key: proposal[key] for key in _PUBLIC}
    split = proposal.get("split")
    view["split"] = None if split is None else {
        "contacts_kept": split["contacts_kept"],
        "memories": [{key: entry[key] for key in ("category", "subject", "text", "action", "saved_subject",
                                                    "saved_text", "new_text")} for entry in split["memories"]],
    }
    return view


def proposals(session_id: str, run_id: str) -> dict:
    with _LOCK:
        run = _run(session_id, run_id)
        if run.proposals is None:
            raise _error("review_busy" if run.job and run.job.get("status") == "running" else "review_expired")
        return {"run_id": run.id, "revision": run.revision,
                "items": [_proposal_view(proposal) for proposal in run.proposals]}


# Apply and Undo -------------------------------------------------------------------------------------------

def _backup_path() -> Path:
    import row_bot.knowledge_graph as kg

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = Path(kg.DB_PATH).with_name(BACKUP_PREFIX + stamp)
    number = 1
    while path.exists():
        number += 1
        path = Path(kg.DB_PATH).with_name(f"{BACKUP_PREFIX}{stamp}-{number}")
    return path


def backup() -> Path:
    """Copy memory.db with SQLite's own backup, so a write in progress can't tear the copy."""
    import row_bot.knowledge_graph as kg

    target = _backup_path()
    try:
        source = sqlite3.connect(kg.DB_PATH)
        try:
            copy = sqlite3.connect(str(target))
            try:
                source.backup(copy)
            finally:
                copy.close()
        finally:
            source.close()
    except (OSError, sqlite3.Error) as exc:
        logger.warning("Memory review: memory could not be backed up (%s); nothing was changed", type(exc).__name__)
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
        raise _error("review_backup_failed") from exc
    return target


_RELATION_KEYS = ("id", "source_id", "target_id", "relation_type", "confidence", "properties", "source",
                  "created_at", "updated_at")


def _relations(entity_id: str) -> list[dict]:
    import row_bot.knowledge_graph as kg

    rows = {}
    for relation in kg.get_relations(entity_id, direction="both"):
        rows[relation["id"]] = {key: relation[key] for key in _RELATION_KEYS}
    return list(rows.values())


def _held(entity_ids: Iterable[str]) -> dict:
    """What Undo needs to put memories back: their rows, relations and recall stamps."""
    import row_bot.knowledge_graph as kg

    ids = list(dict.fromkeys(entity_ids))
    rows = [row for row in (kg.get_entity(entity_id) for entity_id in ids) if row is not None]
    relations: dict[str, dict] = {}
    for entity_id in ids:
        for relation in _relations(entity_id):
            relations[relation["id"]] = relation
    return {"kind": "restore", "entities": rows, "relations": list(relations.values()), "remove_relations": [],
            "recall": kg.recall_stamps(ids)}


def _receipt_view(receipt: dict) -> dict:
    return {key: receipt.get(key) for key in (
        "run_id", "command_id", "status", "applied_at", "undone_at", "undo_until", "removed", "merged", "rewritten",
        "split", "added", "failed", "backup")}


def _chosen(run: _Run, choices: list[dict]) -> list[tuple[dict, dict]]:
    """The approved proposals with the person's edits; refuses what can't be approved."""
    by_id = {proposal["proposal_id"]: proposal for proposal in run.proposals or []}
    picked: list[tuple[dict, dict]] = []
    seen: set[str] = set()
    for choice in choices:
        proposal = by_id.get(str(choice.get("proposal_id") or ""))
        if proposal is None or proposal["proposal_id"] in seen:
            raise _error("invalid_command")
        seen.add(proposal["proposal_id"])
        text = choice.get("text")
        if choice.get("remove"):
            # Only a long record or a lesson, picked by the person and confirmed by its name.
            if not proposal["removable"] or _norm(choice.get("confirm")) != _norm(proposal["subject"]):
                raise _error("invalid_command")
        elif proposal["action"] == "keep":
            raise _error("invalid_command")
        if text is not None:
            text = _norm(text)
            limit = PROFILE_CHARS if proposal["action"] == "split" else TEXT_LIMIT
            if proposal["action"] not in {"rewrite", "merge", "split"} or not text or len(text) > limit:
                raise _error("invalid_command")
        picked.append((proposal, {"remove": bool(choice.get("remove")), "text": text}))
    if not picked:
        raise _error("invalid_command")
    return picked


def apply(session_id: str, run_id: str, *, command_id: str, revision: str, choices: list[dict],
          validate: Callable[[], None] | None = None) -> dict:
    """Make the approved changes, after a backup; applying a run twice changes nothing the second time."""
    import row_bot.dream_cycle as dream
    import row_bot.knowledge_graph as kg
    import row_bot.memory_evolution as memory_evo
    import row_bot.memory_reconcile as reconcile

    with _LOCK:
        run = _run(session_id, run_id)
        if run.receipt is not None:
            return _receipt_view(run.receipt)
        if run.job is not None and run.job.get("status") == "running":
            raise _error("review_busy")
        if run.proposals is None or run.revision != revision:
            raise _error("review_stale")
        picked = _chosen(run, choices)
        # A memory changed since the review was made: review again rather than overwrite that change.
        for proposal, _choice in picked:
            for entity_id, updated_at in proposal["involved"].items():
                current = kg.get_entity(entity_id)
                if current is None or str(current.get("updated_at") or "") != updated_at:
                    run.stale = True
                    raise _error("review_stale")
        if validate is not None:
            validate()
        copy = backup()
        now = _now()
        receipt: dict[str, Any] = {
            "run_id": run.id, "command_id": command_id, "status": "applied",
            "applied_at": datetime.fromtimestamp(now).isoformat(timespec="seconds"), "applied_ts": now,
            "undo_until": datetime.fromtimestamp(now + KEEP_SECONDS).isoformat(timespec="seconds"),
            "undone_at": None, "removed": 0, "merged": 0, "rewritten": 0, "split": 0, "added": 0, "failed": 0,
            "backup": copy.name, "changes": [],
        }
        for proposal, choice in picked:
            if validate is not None:
                validate()
            entity = kg.get_entity(proposal["memory_id"])
            try:
                if choice["remove"] or proposal["action"] == "remove":
                    held = _held([entity["id"]])
                    if not kg.delete_entity(entity["id"]):
                        raise RuntimeError("not deleted")
                    receipt["changes"].append(held)
                    receipt["removed"] += 1
                elif proposal["action"] == "rewrite":
                    held = _held([entity["id"]])
                    properties = memory_evo.merge_properties(
                        entity.get("properties"), {"last_reviewed_at": memory_evo.now_iso(), "review_run": run.id},
                        source=entity.get("source", ""), entity_type=entity.get("entity_type", ""), actor="review",
                        source_context={"kind": "review", "run_id": run.id})
                    if kg.update_entity(entity["id"], choice["text"] or proposal["proposed_text"],
                                        properties=properties,
                                        expected_updated_at=str(entity.get("updated_at") or "")) is None:
                        raise RuntimeError("changed")
                    receipt["changes"].append(held)
                    receipt["rewritten"] += 1
                elif proposal["action"] == "merge":
                    survivor = kg.get_entity(proposal["into"]["memory_id"])
                    held = _held([entity["id"], survivor["id"]])
                    before = {relation["id"] for relation in _relations(survivor["id"])}
                    if dream.merge_into(survivor, entity, choice["text"] or proposal["proposed_text"]) is None:
                        raise RuntimeError("not merged")
                    held["remove_relations"] = [relation["id"] for relation in _relations(survivor["id"])
                                                if relation["id"] not in before]
                    receipt["changes"].append(held)
                    receipt["merged"] += 1
                elif proposal["action"] == "split":
                    _apply_split(entity, proposal, choice, receipt, run.id, reconcile, memory_evo)
            except Exception:
                logger.warning("Memory review: one change could not be made", exc_info=True)
                receipt["failed"] += 1
        run.receipt = receipt
        run.proposals = None
        _write_json(run.folder / "receipt.json", receipt)
        _remove(run.folder / "proposals.json")
        changed = [entity["id"] for change in receipt["changes"] for entity in change.get("entities") or []]
        changed += [change["id"] for change in receipt["changes"] if change["kind"] == "created"]
        memory_evo.append_journal(
            "review", entity_ids=changed[:200], actor="review", source="review",
            reason="Review memory: the approved changes were made",
            details={"run_id": run.id, "backup": copy.name, **{key: receipt[key] for key in (
                "removed", "merged", "rewritten", "split", "added", "failed")}})
        return _receipt_view(receipt)


def _apply_split(user: dict, proposal: dict, choice: dict, receipt: dict, run_id: str, reconcile: Any,
                 memory_evo: Any) -> None:
    import row_bot.knowledge_graph as kg

    targets = [entry["target"] for entry in proposal["split"]["memories"] if entry["action"] == "update"]
    receipt["changes"].append(_held([user["id"], *targets]))
    properties = memory_evo.normalize_properties(
        {"extraction_method": "review", "review_run": run_id,
         "source_context": {"kind": "review", "display_name": "Review memory", "run_id": run_id}}, source="review")
    for entry in proposal["split"]["memories"]:
        if entry["action"] == "skip":
            continue
        memory = reconcile.NewMemory(entry["category"], entry["subject"], entry["text"], source="review",
                                     properties={**properties})
        if entry["action"] == "add":
            [outcome] = reconcile.apply([("add", memory, None, entry["subject"])], actor="review")
            receipt["changes"].append({"kind": "created", "id": outcome.entity_id})
            receipt["added"] += 1
            link = _LINKS.get(entry["category"])
            if link:
                kg.add_relation(user["id"], outcome.entity_id, link, source="review")
        else:
            target = kg.get_entity(entry["target"])
            reconcile.apply([("update", memory, target, entry["new_text"])], actor="review")
    user_properties = memory_evo.merge_properties(
        user.get("properties"), {"last_reviewed_at": memory_evo.now_iso(), "review_run": run_id},
        source=user.get("source", ""), entity_type=user.get("entity_type", ""), actor="review",
        source_context={"kind": "review", "run_id": run_id})
    kg.update_entity(user["id"], choice["text"] or proposal["proposed_text"], properties=user_properties)
    receipt["split"] += 1


def _stored(run_id: str) -> tuple[_Run | None, Path, dict]:
    if not _RUN_ID.fullmatch(run_id):
        raise _error("not_found")
    run = _RUNS.get(run_id)
    folder = _root() / run_id
    receipt = run.receipt if run is not None else _read_json(folder / "receipt.json")
    if not isinstance(receipt, dict):
        raise _error("not_found")
    return run, folder, receipt


def undo(run_id: str, *, validate: Callable[[], None] | None = None) -> dict:
    """Put back exactly what a review removed or changed, and remove what it added, from any window."""
    import row_bot.knowledge_graph as kg
    import row_bot.memory_evolution as memory_evo

    with _LOCK:
        run, folder, receipt = _stored(run_id)
        if receipt.get("status") == "undone":
            return _receipt_view(receipt)
        if _now() - float(receipt.get("applied_ts") or 0) > KEEP_SECONDS:
            raise _error("review_expired")
        for change in reversed(receipt.get("changes") or []):
            if validate is not None:
                validate()
            if change["kind"] == "created":
                if kg.get_entity(change["id"]) is not None:
                    kg.delete_entity(change["id"])
                continue
            kg.restore_entities(change["entities"], change["relations"],
                                remove_relations=change.get("remove_relations") or [],
                                recall=change.get("recall") or {}, validate=validate)
        receipt["status"] = "undone"
        receipt["undone_at"] = datetime.fromtimestamp(_now()).isoformat(timespec="seconds")
        _write_json(folder / "receipt.json", receipt)
        if run is not None:
            run.receipt = receipt
        changed = [entity["id"] for change in receipt.get("changes") or [] for entity in change.get("entities") or []]
        memory_evo.append_journal("review_undo", entity_ids=changed[:200], actor="review", source="review",
                                  reason="The review was undone", details={"run_id": run_id})
        return _receipt_view(receipt)


def recent() -> list[dict]:
    """Reviews applied in the last 30 days, newest first: each can still be undone."""
    with _LOCK:
        sweep()
        found = []
        for entry in _root().iterdir():
            if _RUN_ID.fullmatch(entry.name) and entry.is_dir() and not entry.is_symlink():
                run = _RUNS.get(entry.name)
                receipt = run.receipt if run is not None else _read_json(entry / "receipt.json")
                if isinstance(receipt, dict):
                    found.append(receipt)
        found.sort(key=lambda receipt: float(receipt.get("applied_ts") or 0), reverse=True)
        return [_receipt_view(receipt) for receipt in found[:10]]


# For a maintainer -----------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """``--propose --data-dir <dir>``: print a review's proposals as JSON; nothing is applied."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(prog="python -m row_bot.application.memory_review", description=main.__doc__)
    parser.add_argument("--propose", action="store_true", required=True, help="Propose; never applies anything.")
    parser.add_argument("--data-dir", required=True, help="The Row-Bot data folder to read.")
    parser.add_argument("--import-batch", default=None, help="Only one import's memories and their neighbours.")
    parser.add_argument("--signals-only", action="store_true",
                        help="Only the cheap checks: no model call (the model's memories show as kept).")
    args = parser.parse_args(argv)
    if "row_bot.knowledge_graph" in sys.modules and os.environ.get("ROW_BOT_DATA_DIR") != args.data_dir:
        parser.error("start a fresh Python: memory was already opened in another data folder")
    os.environ["ROW_BOT_DATA_DIR"] = str(Path(args.data_dir).expanduser().resolve())
    os.environ.setdefault("ROW_BOT_INSTALL_COUNT", "0")
    invoke = (lambda prompt: json.dumps({"decisions": []})) if args.signals_only else None
    found = propose(import_batch=args.import_batch, invoke=invoke, split=not args.signals_only)
    for proposal in found["proposals"]:
        proposal.pop("involved", None)
    json.dump(found, sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
