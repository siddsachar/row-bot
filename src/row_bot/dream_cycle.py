"""Dream Cycle — nightly background knowledge refinement.

Runs during a configurable quiet window (default 1–5 AM local time) when
the system is idle.  Each night it:

1. **Compares similar memories** — across the whole graph, the closest pairs
   by meaning (calibrated to the embedding model) go to the model, several
   per call: the "same" are merged, a newer one that "replaces" an older one
   marks it superseded (nothing deleted), and "different" is remembered.
2. **Enriches thin descriptions** — entities (<80 chars) that appear in
   multiple conversations get richer descriptions.
3. **Decays stale inferences** — an inferred relation loses 10% per 30 days
   unused and is removed below 0.3.
4. **Infers relationships** — entity pairs named together in one passage of a
   conversation, with no edge, are evaluated for a connection.

Last, it **prepares tomorrow** for Proactive Row-Bot (``proactive.overnight``): read-only briefs for the
next day's meetings and drafted replies for the follow-ups due, waiting for their moments; nothing at
Proactive's Off level, and it stops when the person comes back.

The conversations are read once per night, for both 2 and 4.  Nothing in the
person's memory is deleted except merged duplicates and pruned inferences.
All changes are tagged with ``source="dream_*"`` for traceability and
logged to a persistent dream journal (``~/.row-bot/dream_journal.json``).

Architecture mirrors ``memory_extraction.py``: daemon thread, direct LLM
calls (no agent overhead), conservative thresholds, batch-capped.
"""

from __future__ import annotations

import bisect
import difflib
import hashlib
import json
import logging
import pathlib
import re
import sys
import threading
import uuid
from datetime import datetime, timedelta, timezone

from row_bot.data_paths import get_row_bot_data_dir

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

_DATA_DIR = get_row_bot_data_dir()
_DATA_DIR.mkdir(parents=True, exist_ok=True)

_CONFIG_FILE = _DATA_DIR / "dream_config.json"
_JOURNAL_FILE = _DATA_DIR / "dream_journal.json"
_REJECTION_CACHE_FILE = _DATA_DIR / "dream_rejections.json"

# Defaults.  Keys older versions saved (merge_threshold, batch_size, _batch_offset) are ignored.
_DEFAULT_CONFIG = {
    "enabled": True,
    "window_start": 1,      # 1 AM local time
    "window_end": 5,         # 5 AM local time
    "enrich_min_chars": 80,
    "infer_confidence": 0.80,
    "min_entities": 20,
}

_JOURNAL_MAX_ENTRIES = 100
_CHECK_INTERVAL_S = 30 * 60  # Check every 30 minutes


def _load_config() -> dict:
    """Load dream cycle config, falling back to defaults."""
    cfg = dict(_DEFAULT_CONFIG)
    if _CONFIG_FILE.exists():
        try:
            stored = json.loads(_CONFIG_FILE.read_text())
            cfg.update(stored)
        except Exception:
            pass
    return cfg


def _save_config(cfg: dict) -> None:
    _CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


def get_config() -> dict:
    """Public accessor for UI."""
    return _load_config()


def set_enabled(enabled: bool) -> None:
    cfg = _load_config()
    cfg["enabled"] = enabled
    _save_config(cfg)


def set_window(start: int, end: int) -> None:
    if not isinstance(start, int) or not isinstance(end, int) or not (0 <= start <= 23) or not (0 <= end <= 23):
        raise ValueError("Dream window hours must be integers from 0 through 23.")
    if start == end:
        raise ValueError("Dream window start and end must differ.")
    cfg = _load_config()
    cfg["window_start"] = start
    cfg["window_end"] = end
    _save_config(cfg)


def is_enabled() -> bool:
    return _load_config().get("enabled", True)


# ── Dream journal ────────────────────────────────────────────────────────────

def _load_journal() -> list[dict]:
    if _JOURNAL_FILE.exists():
        try:
            return json.loads(_JOURNAL_FILE.read_text())
        except Exception:
            pass
    return []


def _save_journal(entries: list[dict]) -> None:
    # Keep only the most recent entries
    entries = entries[-_JOURNAL_MAX_ENTRIES:]
    _JOURNAL_FILE.write_text(json.dumps(entries, indent=2))


def _append_journal(entry: dict) -> None:
    journal = _load_journal()
    journal.append(entry)
    _save_journal(journal)


def get_journal(limit: int = 10) -> list[dict]:
    """Return the most recent dream journal entries."""
    entries = _load_journal()
    return entries[-limit:]


def get_dream_status() -> dict:
    """Return dream cycle status for Activity panel."""
    cfg = _load_config()
    journal = _load_journal()
    last = journal[-1] if journal else None
    return {
        "enabled": cfg.get("enabled", True),
        "window": f"{cfg.get('window_start', 1)}:00 – {cfg.get('window_end', 5)}:00",
        "last_run": last.get("timestamp") if last else None,
        "last_summary": last.get("summary") if last else None,
    }


# ── Evidence cache ──────────────────────────────────────────────────────────
#
# A merge, enrichment or inference the model turned down is not asked again
# while what it read is unchanged: each record keeps a hash of the model that
# answered (C1's resolver for "dream") and the exact prompt (the entities'
# content and the conversation evidence).  New evidence, an edited entity, a
# changed prompt or another default model asks again, and so does a record
# older than _EVIDENCE_MAX_AGE_DAYS.  Insights keep the hash of the snapshot
# last read.
# Kept in dream_rejections.json:
#   {"version": 2, "merge": {pair: {"evidence", "at"}}, "enrich": {entity id: ...},
#    "infer": {pair: ...}, "insights": {"evidence", "at"}}
# Records from before (pair -> time only) are dropped: those pairs are asked once more.

_EVIDENCE_CACHE_VERSION = 2
_EVIDENCE_KINDS = ("merge", "enrich", "infer")
_EVIDENCE_MAX_AGE_DAYS = 30


def evidence_hash(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8", "surrogatepass")).hexdigest()[:32]


def _pair_key(entity_a_id: str, entity_b_id: str) -> str:
    return "|".join(sorted([str(entity_a_id), str(entity_b_id)]))


def _valid_record(value) -> bool:
    return isinstance(value, dict) and isinstance(value.get("evidence"), str) and isinstance(value.get("at"), str)


def _load_rejection_cache() -> dict:
    """The evidence cache, with an empty table per kind."""
    raw: object = {}
    if _REJECTION_CACHE_FILE.exists():
        try:
            raw = json.loads(_REJECTION_CACHE_FILE.read_text())
        except Exception:
            raw = {}
    cache: dict = {"version": _EVIDENCE_CACHE_VERSION, **{kind: {} for kind in _EVIDENCE_KINDS}}
    if not isinstance(raw, dict) or raw.get("version") != _EVIDENCE_CACHE_VERSION:
        return cache
    for kind in _EVIDENCE_KINDS:
        records = raw.get(kind)
        if isinstance(records, dict):
            cache[kind] = {str(key): record for key, record in records.items() if _valid_record(record)}
    if _valid_record(raw.get("insights")):
        cache["insights"] = raw["insights"]
    return cache


def _save_rejection_cache(cache: dict) -> None:
    _REJECTION_CACHE_FILE.write_text(json.dumps(cache, indent=2))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fresh(record: dict) -> bool:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=_EVIDENCE_MAX_AGE_DAYS)).isoformat()
    return str(record.get("at") or "") > cutoff


def _dream_model_ref() -> str:
    """The model Dream's calls go to (provider and model, from C1's resolver)."""
    try:
        from row_bot.providers.background import background_model_ref

        return str(background_model_ref("dream") or "")
    except Exception:
        logger.debug("Could not resolve Dream's model", exc_info=True)
        return ""


def _rejection_evidence(prompt: str, model_ref: str | None) -> str:
    model = _dream_model_ref() if model_ref is None else model_ref
    return evidence_hash(f"{model}\n{prompt}")


def _record_rejection(kind: str, key: str, prompt: str, model_ref: str | None = None) -> None:
    """Remember that the model turned *key* down after reading *prompt*."""
    cache = _load_rejection_cache()
    cache[kind][key] = {"evidence": _rejection_evidence(prompt, model_ref), "at": _now_iso()}
    for name in _EVIDENCE_KINDS:
        cache[name] = {item: record for item, record in cache[name].items() if _fresh(record)}
    try:
        _save_rejection_cache(cache)
    except OSError:
        logger.debug("Could not save the Dream evidence cache", exc_info=True)


def _rejected_with_same_evidence(kind: str, key: str, prompt: str, cache: dict | None = None,
                                 model_ref: str | None = None) -> bool:
    """Whether the same model turned *key* down after reading this same *prompt*.

    ``model_ref`` saves resolving Dream's model again when checking many candidates.
    """
    record = (cache if cache is not None else _load_rejection_cache()).get(kind, {}).get(key)
    return bool(record) and record["evidence"] == _rejection_evidence(prompt, model_ref) and _fresh(record)


def _answered(raw: str) -> bool:
    """Whether a reply is a whole answer: plain text, or a JSON object that parses.

    A JSON reply cut short by the output cap (or an empty one) is not, so the
    candidate is asked again next cycle rather than remembered as turned down.
    """
    from row_bot.providers.background import parse_json_object

    text = str(raw or "").strip()
    if not text:
        return False
    return not re.sub(r"^```(?:json)?\s*", "", text).startswith("{") or parse_json_object(text) is not None


# ── Idle detection ───────────────────────────────────────────────────────────

def _is_idle() -> bool:
    """Check that heavyweight dream work will not compete with the user."""
    try:
        from row_bot.memory_extraction import is_app_idle
        return is_app_idle()
    except Exception:
        return True  # If we can't check, assume idle


def _in_dream_window() -> bool:
    """Check if current local time is within the dream window."""
    cfg = _load_config()
    hour = datetime.now().hour
    start = cfg.get("window_start", 1)
    end = cfg.get("window_end", 5)
    if start <= end:
        return start <= hour < end
    else:
        # Wraps midnight, e.g. 23:00 - 03:00
        return hour >= start or hour < end


def _already_ran_today() -> bool:
    """Check if a dream cycle already completed today."""
    journal = _load_journal()
    if not journal:
        return False
    last = journal[-1]
    try:
        last_dt = datetime.fromisoformat(last["timestamp"])
        return last_dt.date() == datetime.now().date()
    except (KeyError, ValueError):
        return False


def _should_dream() -> bool:
    """All conditions met for a dream cycle?

    "Busy" is Row-Bot's own state: the idle check and the generations in
    flight.  Ollama's ``/api/ps`` lists the models it keeps loaded (name,
    size, ``size_vram``, ``expires_at``) with no count of requests in flight,
    and a model stays loaded for its keep-alive after every reply, so a
    loaded model says nothing about use.  Dream no longer asks it (F48).
    """
    if not is_enabled():
        return False
    if _already_ran_today():
        return False
    if not _in_dream_window():
        return False
    if not _is_idle():
        return False
    if not _model_chosen():
        logger.info("Dream cycle deferred — no model is chosen yet")
        return False
    # Never run while any agent generation is in flight — a long dream
    # cycle competes with live requests for LLM bandwidth and can
    # starve the UI.
    from row_bot.runtime import executions

    active = executions.generation_registry.active()
    if active:
        logger.info(
            "Dream cycle deferred — %d active generation(s) in flight",
            len(active),
        )
        return False
    # Personal-model training (Learning, preview) holds the computer: Dream waits for it.
    # Read only when Learning's lease was ever loaded, so this costs nothing otherwise.
    training_holds = getattr(sys.modules.get("row_bot.learning.lease"), "training_holds", None)
    if training_holds is not None and training_holds():
        logger.info("Dream cycle deferred — a personal model is training")
        return False
    return True


_dreaming = threading.Event()


def is_dreaming() -> bool:
    """Whether a dream cycle is running now (Learning's training starts after it)."""
    return _dreaming.is_set()


# ── LLM helper ───────────────────────────────────────────────────────────────

NO_MODEL_SKIP = "Skipped — no model is chosen yet. Choose a model in Row-Bot."


def _model_chosen() -> bool:
    """Nothing is preset (decision 9): Dream Cycle waits for a chosen model."""
    from row_bot.models import get_current_model

    return bool(str(get_current_model() or "").strip())


def _llm_call(prompt: str, *, answer=None, max_tokens: int | None = None) -> str:
    """Make one Dream Cycle call and return the reply text, thinking removed.

    An unattended background call (WP C1): the default chat model at the
    lowest reasoning effort, a bounded reply, and a JSON schema (``answer``)
    where the provider takes one; callers keep their text parsers.
    """
    from langchain_core.messages import HumanMessage
    from row_bot.providers.background import invoke_background

    return invoke_background("dream", [HumanMessage(content=prompt)], answer=answer, max_tokens=max_tokens)


# Reply shapes for the providers that take a JSON schema.
_INFER_MAX_TOKENS = 512
_INSIGHTS_MAX_TOKENS = 6144


def _description_answer():
    from row_bot.providers.background import structured_answer

    return structured_answer("entity_description", {"description": {"type": "string"}})


def _infer_answer():
    from row_bot.providers.background import structured_answer

    return structured_answer("relation_inference", {
        "has_relation": {"type": "boolean"},
        "relation_type": {"type": "string"},
        "source": {"type": "string"},
        "target": {"type": "string"},
        "confidence": {"type": "number"},
        "evidence": {"type": "string"},
    })


def _compare_answer():
    from row_bot.providers.background import json_object, structured_answer

    decision = json_object({
        "pair": {"type": "integer"},
        "decision": {"type": "string", "enum": list(_PAIR_DECISIONS)},
        "reason": {"type": "string"},
    })
    return structured_answer("memory_pairs", {"decisions": {"type": "array", "items": decision}})


def _insights_answer():
    from row_bot.providers.background import json_object, structured_answer

    skill_draft = json_object({
        "name": {"type": "string"},
        "display_name": {"type": "string"},
        "icon": {"type": "string"},
        "description": {"type": "string"},
        "instructions": {"type": "string"},
    })
    insight = json_object({
        "category": {"type": "string", "enum": [
            "error_pattern", "skill_proposal", "tool_config", "knowledge_quality",
            "usage_pattern", "system_health"]},
        "severity": {"type": "string", "enum": ["info", "warning", "critical"]},
        "title": {"type": "string"},
        "body": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "suggestion": {"type": "string"},
        "confidence": {"type": "number"},
        "auto_fixable": {"type": "boolean"},
        "skill_draft": {"anyOf": [skill_draft, {"type": "null"}]},
    })
    return structured_answer("dream_insights", {"insights": {"type": "array", "items": insight}})


# ── OP1: Similar memories — same, replaced or different ─────────────────────
#
# Every night looks at the whole graph: each memory's nearest neighbours by
# meaning (one search per memory).  A raw score means little on its own (a
# local model scores unrelated memories about 0.45, a cloud model far lower),
# so it is calibrated against the query's mean similarity to every memory:
# (score - mean) / (1 - mean).  The closest pairs go to the model, several per
# call, and each comes back "same", "replaces" or "different".

_PAIR_NEIGHBOURS = 5            # nearest memories looked at for each memory
_PAIR_MIN_SIMILARITY = 0.6      # calibrated similarity a pair needs to be asked about
_PAIRS_PER_NIGHT = 20
_PAIRS_PER_CALL = 5
_PAIR_DESCRIPTION_CHARS = 1200
_PAIR_DECISIONS = ("same", "replaces", "different")
# Person, fact and preference memories are often the same thing saved under another type.
_PERSONAL_TYPES = frozenset({"person", "fact", "preference"})
# A self_knowledge memory may be a Learning lesson, tested in its exact text: it is
# never rewritten or superseded, and only merged with a near-identical one.
_LESSON_TYPE = "self_knowledge"
_LESSON_NEAR_IDENTICAL = 0.9


def _is_user(entity: dict) -> bool:
    """The canonical User record (by subject or alias), which Dream never pairs."""
    names = [entity.get("subject") or "", *(entity.get("aliases") or "").split(",")]
    return any(" ".join(name.lower().split()) == "user" for name in names)


def _active(entity: dict) -> bool:
    from row_bot.memory_evolution import get_properties, normalize_status

    return normalize_status(get_properties(entity).get("status")) not in {"archived", "superseded"}


def _is_lesson(entity: dict) -> bool:
    return str(entity.get("entity_type") or "") == _LESSON_TYPE


def _type_family(entity: dict) -> str:
    kind = str(entity.get("entity_type") or "")
    return "personal" if kind in _PERSONAL_TYPES else kind


def _near_identical(entity_a: dict, entity_b: dict) -> bool:
    def words(entity: dict) -> str:
        return " ".join(re.sub(r"[^\w\s]", " ", str(entity.get("description") or "").lower()).split())

    return difflib.SequenceMatcher(None, words(entity_a), words(entity_b)).ratio() >= _LESSON_NEAR_IDENTICAL


def _older_newer(entity_a: dict, entity_b: dict) -> tuple[dict, dict]:
    def saved(entity: dict) -> tuple[str, str]:
        return str(entity.get("created_at") or ""), str(entity["id"])

    return (entity_a, entity_b) if saved(entity_a) <= saved(entity_b) else (entity_b, entity_a)


def _pair_block(older: dict, newer: dict) -> str:
    def memory(label: str, entity: dict) -> str:
        saved = str(entity.get("created_at") or "")[:10] or "unknown"
        return (f'{label} memory — type: {entity.get("entity_type", "")}, saved {saved}, '
                f'subject: "{entity.get("subject", "")}"\n'
                f'Description: {str(entity.get("description") or "")[:_PAIR_DESCRIPTION_CHARS]}')

    return f"{memory('Older', older)}\n{memory('Newer', newer)}"


def _pair_evidence(entity_a: dict, entity_b: dict) -> str:
    """What the model reads about one pair (the rules and both memories), for the evidence cache."""
    from row_bot.prompts import DREAM_COMPARE_PROMPT

    return f"{DREAM_COMPARE_PROMPT}\n{_pair_block(*_older_newer(entity_a, entity_b))}"


def _compare_prompt(pairs: list[tuple[dict, dict, float]]) -> str:
    from row_bot.prompts import DREAM_COMPARE_PROMPT

    blocks = "\n\n".join(f"Pair {number}\n{_pair_block(older, newer)}"
                         for number, (older, newer, _similarity) in enumerate(pairs, 1))
    return DREAM_COMPARE_PROMPT.format(pairs=blocks)


def _find_pair_candidates(entities: list[dict], *, limit: int = _PAIRS_PER_NIGHT) -> list[tuple[dict, dict, float]]:
    """The closest pairs of memories across the whole graph: ``[(older, newer, similarity)]``.

    One search per memory (its nearest neighbours, of the same type family);
    archived and superseded memories, the User record and memories longer than
    the model is shown are left out.  A pair
    is kept when its calibrated similarity reaches ``_PAIR_MIN_SIMILARITY``;
    the closest come first, each memory in at most one pair, and a pair the
    model turned down is skipped while both memories read the same.
    """
    import row_bot.knowledge_graph as kg

    # A memory longer than the model is shown is never paired: a merge would rewrite what it didn't see, and
    # a replacement would hide all of it (a long record, such as a register a workflow keeps).
    pool = {entity["id"]: entity for entity in entities if _active(entity) and not _is_user(entity)
            and len(entity.get("description") or "") <= _PAIR_DESCRIPTION_CHARS}
    closest: dict[str, tuple[float, str, str]] = {}
    for entity in pool.values():
        stats: dict = {}
        try:
            hits = kg.semantic_search(kg._entity_text(entity), top_k=_PAIR_NEIGHBOURS + 1, threshold=0.0,
                                      stats=stats)
        except Exception as exc:
            # The index or the embedding model is unavailable: every other search would fail the same way.
            logger.info("Dream: memory search unavailable, comparing no more pairs tonight (%s)", exc)
            break
        mean = stats.get("mean")
        mean = float(mean) if isinstance(mean, (int, float)) and mean < 1 else 0.0
        for hit in hits:
            other = pool.get(str(hit.get("id") or ""))
            if other is None or other["id"] == entity["id"] or _type_family(other) != _type_family(entity):
                continue
            if (_is_lesson(entity) or _is_lesson(other)) and not _near_identical(entity, other):
                continue
            similarity = (float(hit.get("score") or 0.0) - mean) / (1 - mean)
            if similarity < _PAIR_MIN_SIMILARITY:
                continue
            key = _pair_key(entity["id"], other["id"])
            if key not in closest or similarity > closest[key][0]:
                closest[key] = (similarity, entity["id"], other["id"])

    rejections = _load_rejection_cache()
    model_ref = _dream_model_ref()
    paired: set[str] = set()
    pairs: list[tuple[dict, dict, float]] = []
    for key, (similarity, id_a, id_b) in sorted(closest.items(), key=lambda item: (-item[1][0], item[0])):
        if len(pairs) >= limit:
            break
        if id_a in paired or id_b in paired:
            continue
        older, newer = _older_newer(pool[id_a], pool[id_b])
        if _rejected_with_same_evidence("merge", key, _pair_evidence(older, newer), rejections, model_ref=model_ref):
            continue
        pairs.append((older, newer, similarity))
        paired.update((id_a, id_b))
    return pairs


def _decide_pairs(pairs: list[tuple[dict, dict, float]]) -> list[tuple[dict, dict, float, str]]:
    """One model decision per pair, ``_PAIRS_PER_CALL`` pairs per call.

    A pair with no usable decision (a failed call, a reply cut short) is left
    out, so it is asked again another night.
    """
    from row_bot.providers.background import list_field

    decided: list[tuple[dict, dict, float, str]] = []
    for start in range(0, len(pairs), _PAIRS_PER_CALL):
        chunk = pairs[start:start + _PAIRS_PER_CALL]
        try:
            raw = _llm_call(_compare_prompt(chunk), answer=_compare_answer())
        except Exception as exc:
            logger.warning("Dream compare LLM call failed: %s", exc)
            continue
        answers: dict[int, str] = {}
        for item in list_field(raw, "decisions") or []:
            if not isinstance(item, dict):
                continue
            try:
                number = int(item.get("pair"))
            except (TypeError, ValueError):
                continue
            decision = str(item.get("decision") or "").strip().lower()
            if 1 <= number <= len(chunk) and decision in _PAIR_DECISIONS:
                answers.setdefault(number, decision)
        for number, (older, newer, similarity) in enumerate(chunk, 1):
            if number in answers:
                decided.append((older, newer, similarity, answers[number]))
    return decided


def _supersede(older: dict, newer: dict, similarity: float) -> dict | None:
    """The newer memory changes the older one: the older is kept, marked superseded."""
    from row_bot import memory_evolution

    old, new = memory_evolution.mark_superseded(
        older["id"], newer["id"], reason="A newer memory changes this one (Dream Cycle)", actor="dream_cycle")
    if not old or not new:
        return None
    return {
        "old_id": older["id"],
        "old_subject": older.get("subject", ""),
        "new_id": newer["id"],
        "new_subject": newer.get("subject", ""),
        "old_description": str(older.get("description") or "")[:200],
        "new_description": str(newer.get("description") or "")[:200],
        "score": round(similarity, 4),
    }


def _settle_pairs(pairs: list[tuple[dict, dict, float]], summary: dict, status) -> None:
    """Ask about *pairs* and act on each answer, recording merges and supersessions in *summary*."""
    for older, newer, similarity, decision in _decide_pairs(pairs):
        try:
            lesson = _is_lesson(older) or _is_lesson(newer)
            if decision == "same":
                result = _merge_entities(older, newer)
                if result:
                    result["score"] = round(similarity, 4)
                    summary["merges"].append(result)
                    status(f"Merged: '{result['duplicate_subject']}' → "
                           f"'{result['survivor_subject']}' ({similarity:.2f})")
            elif decision == "replaces" and not lesson:
                result = _supersede(older, newer, similarity)
                if result:
                    summary["superseded"].append(result)
                    status(f"Replaced: '{result['old_subject']}' by a newer memory "
                           f"'{result['new_subject']}' ({similarity:.2f})")
            else:
                # "different" — or "replaces" for a lesson, which is never superseded.
                _record_rejection("merge", _pair_key(older["id"], newer["id"]), _pair_evidence(older, newer))
        except Exception as exc:
            summary["errors"].append(f"Compare error: {exc}")


def _merge_prompt(entity_a: dict, entity_b: dict) -> tuple[str, dict, dict]:
    """The merge prompt, with the survivor (the older entity) and the duplicate."""
    from row_bot.prompts import DREAM_MERGE_PROMPT

    # Determine survivor (older by created_at)
    a_created = entity_a.get("created_at", "")
    b_created = entity_b.get("created_at", "")
    if a_created <= b_created:
        survivor, duplicate = entity_a, entity_b
    else:
        survivor, duplicate = entity_b, entity_a

    prompt = DREAM_MERGE_PROMPT.format(
        entity_type=survivor.get("entity_type", ""),
        subject_a=survivor.get("subject", ""),
        description_a=survivor.get("description", ""),
        subject_b=duplicate.get("subject", ""),
        description_b=duplicate.get("description", ""),
    )
    return prompt, survivor, duplicate


def _merge_entities(entity_a: dict, entity_b: dict) -> dict | None:
    """Merge two entities, keeping the older one. Returns merge log entry.

    Two lessons (self_knowledge) merge without a word of the survivor changed:
    its text is what Learning tests.  A lesson never merges with another type.
    """
    prompt, survivor, duplicate = _merge_prompt(entity_a, entity_b)
    lessons = _is_lesson(survivor), _is_lesson(duplicate)
    if any(lessons) and not all(lessons):
        return None

    if all(lessons):
        merged_desc = str(survivor.get("description") or "")
    else:
        # LLM: synthesize best description from both
        try:
            from row_bot.providers.background import text_field

            raw = _llm_call(prompt, answer=_description_answer())
            merged_desc = text_field(raw, "description")
            if not merged_desc or len(merged_desc) < 10:
                if _answered(raw):
                    _record_rejection("merge", _pair_key(survivor["id"], duplicate["id"]),
                                      _pair_evidence(survivor, duplicate))
                return None
        except Exception as exc:
            logger.warning("Dream merge LLM call failed: %s", exc)
            return None

    return merge_into(survivor, duplicate, merged_desc)


def merge_into(survivor: dict, duplicate: dict, merged_desc: str) -> dict | None:
    """Fold *duplicate* into *survivor*: its relations move over, its subject becomes an alias, the survivor
    reads *merged_desc* (a lesson is left exactly as it is), and the duplicate is deleted.

    The Dream Cycle's merge and Review memory's both end here, so a lesson that was the duplicate is handed
    over to the survivor (O7) before the delete marks it gone.
    """
    import row_bot.knowledge_graph as kg

    lessons = _is_lesson(survivor), _is_lesson(duplicate)
    if any(lessons) and not all(lessons):
        return None
    dup_subj = duplicate.get("subject", "").strip()
    surv_subj = survivor.get("subject", "").strip()

    # Union aliases
    a_aliases = set(a.strip() for a in (survivor.get("aliases", "") or "").split(",") if a.strip())
    b_aliases = set(a.strip() for a in (duplicate.get("aliases", "") or "").split(",") if a.strip())
    # Add the duplicate's subject as an alias if different
    if dup_subj.lower() != surv_subj.lower():
        b_aliases.add(dup_subj)
    merged_aliases = ", ".join(sorted(a_aliases | b_aliases))

    # Re-point all relations from duplicate to survivor
    try:
        dup_rels = kg.get_relations(duplicate["id"], direction="both")
        for rel in dup_rels:
            src = rel["source_id"]
            tgt = rel["target_id"]
            rtype = rel["relation_type"]
            # Determine new endpoints
            new_src = survivor["id"] if src == duplicate["id"] else src
            new_tgt = survivor["id"] if tgt == duplicate["id"] else tgt
            # Skip self-loops
            if new_src == new_tgt:
                continue
            # Add the relation to survivor (ignores duplicates via UNIQUE index)
            kg.add_relation(
                new_src, new_tgt, rtype,
                source="dream_merge",
                confidence=rel.get("confidence", 0.8),
            )
    except Exception as exc:
        logger.debug("Re-pointing relations failed: %s", exc)

    # Update survivor with merged description + aliases (a lesson is left exactly as it is)
    if not all(lessons):
        try:
            kg.update_entity(
                survivor["id"],
                merged_desc,
                aliases=merged_aliases if merged_aliases else None,
            )
        except Exception as exc:
            logger.warning("Dream merge update failed: %s", exc)
            return None
    else:
        merged_aliases = str(survivor.get("aliases") or "")

    # A lesson in use that was the duplicate moves to the survivor (O7), before the delete marks it gone.
    if str(duplicate.get("entity_type") or "") == "self_knowledge":
        from row_bot import learning

        learning.lesson_memory_deleted_soon(duplicate["id"], survivor["id"])

    # Delete the duplicate
    try:
        deleted = kg.delete_entity(duplicate["id"])
        if not deleted:
            logger.warning(
                "Dream merge: delete_entity(%s) returned False — zombie entity",
                duplicate["id"],
            )
            return None
    except Exception as exc:
        logger.debug("Dream merge delete failed: %s", exc)
        return None

    return {
        "survivor_id": survivor["id"],
        "survivor_subject": surv_subj,
        "duplicate_id": duplicate["id"],
        "duplicate_subject": dup_subj,
        "merged_description": merged_desc[:200],
        "aliases": merged_aliases,
    }


# ── Conversations, read once per night ──────────────────────────────────────
#
# One pass over the conversations collects what enrichment and inference both
# need: the sentences naming each thin memory, and the passages naming two
# memories together.  Only bounded excerpts are kept, never whole conversations.

_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|(?=\b(?:User|Assistant):)")
_MENTION_CHARS = 500          # sentences naming a memory, per conversation
_MENTION_CONVERSATIONS = 3
_PASSAGE_CHARS = 700          # one passage naming both memories of a pair
_PAIR_EXCERPTS = 3
_MAX_TRACKED_PAIRS = 10_000


class _Conversations:
    """What one read of the conversations found."""

    def __init__(self) -> None:
        # Entity id -> up to 3 excerpts (one per conversation) of the sentences naming it.
        self.mentions: dict[str, list[str]] = {}
        # (id, id) -> [conversations with a passage naming both, the longest such passages (up to 3)].
        self.pairs: dict[tuple[str, str], list] = {}


def _entity_names(entity: dict) -> set[str]:
    names = {str(entity.get("subject") or "").strip().lower()}
    names.update(alias.strip().lower() for alias in str(entity.get("aliases") or "").split(","))
    return {name for name in names if len(name) >= 2 and name != "user"}


def _name_pattern(name: str) -> re.Pattern:
    return re.compile(r"(?<!\w)" + re.escape(name) + r"(?!\w)", re.IGNORECASE)


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    """``(start, end)`` of each sentence or conversation turn, whitespace trimmed."""
    spans: list[tuple[int, int]] = []
    start = 0
    for match in [*_SENTENCE_BREAK.finditer(text), None]:
        end = len(text) if match is None else match.start()
        piece = text[start:end]
        if piece.strip():
            spans.append((start + len(piece) - len(piece.lstrip()), start + len(piece.rstrip())))
        if match is not None:
            start = max(start, match.end())
    return spans


def _read_conversation(text: str, patterns: dict[str, tuple[re.Pattern, list[str]]],
                       mention_ids: set[str], found: _Conversations) -> None:
    lower = text.lower()
    present = [(pattern, ids) for name, (pattern, ids) in patterns.items() if name in lower]
    if not present:
        return
    spans = _sentence_spans(text)
    starts = [start for start, _end in spans]
    # Sentence index -> {entity id: (start, end) of its first mention there}
    named: dict[int, dict[str, tuple[int, int]]] = {}
    for pattern, ids in present:
        for match in pattern.finditer(text):
            index = bisect.bisect_right(starts, match.start()) - 1
            if index < 0 or match.end() > spans[index][1]:
                continue
            for entity_id in ids:
                named.setdefault(index, {}).setdefault(entity_id, match.span())

    # Enrichment: the sentences naming each thin memory, in order, up to _MENTION_CHARS.
    sentences_of: dict[str, list[int]] = {}
    for index in sorted(named):
        for entity_id in named[index]:
            if entity_id in mention_ids:
                sentences_of.setdefault(entity_id, []).append(index)
    for entity_id, indices in sentences_of.items():
        excerpts = found.mentions.setdefault(entity_id, [])
        if len(excerpts) >= _MENTION_CONVERSATIONS:
            continue
        kept: list[str] = []
        total = 0
        for index in indices:
            sentence = text[spans[index][0]:spans[index][1]]
            kept.append(sentence)
            total += len(sentence)
            if total >= _MENTION_CHARS:
                break
        excerpts.append(" ".join(kept))

    # Inference: a passage naming both, in one sentence or two adjacent ones (the first such).
    passages: dict[tuple[str, str], tuple[int, str, int, str]] = {}
    for index in sorted(named):
        here = named[index]
        after = named.get(index + 1, {})
        for entity_a in here:
            for entity_b, where in [*((other, index) for other in here), *((other, index + 1) for other in after)]:
                if entity_a == entity_b:
                    continue
                key = tuple(sorted((entity_a, entity_b)))
                if key not in passages:
                    passages[key] = (index, entity_a, where, entity_b)
    for key, (index_a, entity_a, index_b, entity_b) in passages.items():
        first, last = min(index_a, index_b), max(index_a, index_b)
        span_a, span_b = named[index_a][entity_a], named[index_b][entity_b]
        low, high = min(span_a[0], span_b[0]), max(span_a[1], span_b[1])
        if high - low > _PASSAGE_CHARS:
            continue  # named too far apart to be one passage
        begin, finish = spans[first][0], spans[last][1]
        if finish - begin > _PASSAGE_CHARS:
            begin = max(begin, low - (_PASSAGE_CHARS - (high - low)) // 2)
            finish = min(finish, begin + _PASSAGE_CHARS)
        excerpt = text[begin:finish].strip()
        entry = found.pairs.get(key)
        if entry is None:
            if len(found.pairs) >= _MAX_TRACKED_PAIRS:
                continue
            entry = found.pairs[key] = [0, []]
        entry[0] += 1
        # The longest passages, ties by text: the same conversations give the same evidence in any order.
        entry[1] = sorted([*entry[1], excerpt], key=lambda passage: (len(passage), passage),
                          reverse=True)[:_PAIR_EXCERPTS]


def _read_conversations(entities: list[dict], mention_ids: set[str] | frozenset[str] = frozenset()) -> _Conversations:
    """Read every conversation once: mentions of the *mention_ids* memories, and passages naming two of *entities*."""
    from row_bot.threads import _list_threads
    from row_bot.memory_extraction import _get_thread_messages, _format_conversation

    names: dict[str, set[str]] = {}
    for entity in entities:
        for name in _entity_names(entity):
            names.setdefault(name, set()).add(entity["id"])
    patterns = {name: (_name_pattern(name), sorted(ids)) for name, ids in names.items()}
    wanted = set(mention_ids)
    found = _Conversations()
    if not patterns:
        return found
    for tid, *_rest in _list_threads() or []:
        try:
            messages = _get_thread_messages(tid)
            if not messages:
                continue
            _read_conversation(_format_conversation(messages), patterns, wanted, found)
        except Exception:
            logger.debug("Dream could not read conversation %s", tid, exc_info=True)
    return found


def _find_conversation_mentions(subject: str, aliases: str = "") -> list[str]:
    """Up to 3 excerpts (one per conversation) of the sentences naming *subject* or an alias."""
    entity = {"id": "_mentioned", "subject": subject, "aliases": aliases}
    return _read_conversations([entity], {"_mentioned"}).mentions.get("_mentioned", [])


# ── OP2: Description enrichment ─────────────────────────────────────────────

# Names whose appearance in an enriched description is checked against the evidence.
_NAMED_TYPES = frozenset({"person", "place", "organisation"})
_ENRICH_PER_NIGHT = 20  # thin memories with evidence asked about per night


def _other_names(entities: list[dict]) -> set[str]:
    """The names (subjects and aliases) of the people, places and organisations in memory."""
    names: set[str] = set()
    for entity in entities:
        if str(entity.get("entity_type") or "") in _NAMED_TYPES and not _is_user(entity):
            names.update(name for name in _entity_names(entity) if len(name) >= 3)
    return names


def _prepare_enrichment_inputs(entities: list[dict], min_chars: int) -> tuple[list[dict], set[str]]:
    """Thin memories, those turned down before last, and the names enrichment is checked for.

    A lesson (self_knowledge) is never rewritten, and the User record is left to extraction.
    """
    thin = [entity for entity in entities
            if not _is_lesson(entity) and not _is_user(entity) and _active(entity)
            and len(entity.get("description", "") or "") < min_chars]
    # Entities whose enrichment the model turned down go last, so the others get their turn.
    turned_down = _load_rejection_cache()["enrich"]
    thin.sort(key=lambda entity: (entity["id"] in turned_down, entity.get("updated_at", "")))
    return thin, _other_names(entities)


def _validate_enrichment(
    enriched: str,
    entity: dict,
    other_names: set[str],
    excerpts: list[str],
) -> bool:
    """Return True if *enriched* passes the cross-entity contamination check.

    A person, place or organisation named in the new text must come from the
    evidence: the conversation excerpts, the current description, or the
    entity's known relations.  Another one is contamination.
    """
    import row_bot.knowledge_graph as kg

    enriched_lower = enriched.lower()
    sources = " ".join([*excerpts, str(entity.get("description") or "")]).lower()
    allowed = _entity_names(entity)
    try:
        for relation in kg.get_relations(entity["id"], direction="both"):
            peer = (relation.get("peer_subject", "") or "").strip().lower()
            if peer:
                allowed.add(peer)
    except Exception:
        pass

    for name in other_names:
        name = name.strip().lower()
        if not name or name in allowed or name not in enriched_lower:
            continue
        pattern = _name_pattern(name)
        if pattern.search(enriched_lower) and not pattern.search(sources):
            logger.info(
                "Dream enrich REJECTED for '%s': names '%s', which the evidence does not",
                entity.get("subject", ""), name,
            )
            return False
    return True


def _enrich_prompt(entity: dict, excerpts: list[str]) -> str:
    """The enrichment prompt: the entity, its known relations and the conversation excerpts."""
    import row_bot.knowledge_graph as kg
    from row_bot.prompts import DREAM_ENRICH_PROMPT

    # Build relationship context so the LLM knows entity boundaries
    rel_lines = []
    try:
        rels = kg.get_relations(entity["id"])
        for r in rels[:10]:
            arrow = "→" if r["direction"] == "outgoing" else "←"
            rel_lines.append(f"  {arrow} {r['relation_type']} → {r['peer_subject']}")
    except Exception:
        pass
    relationships_text = "\n".join(rel_lines) if rel_lines else "(none known)"

    # In a fixed order: a renamed conversation (listed first) is not new evidence.
    context = "\n---\n".join(sorted(excerpts[:3]))
    return DREAM_ENRICH_PROMPT.format(
        entity_type=entity.get("entity_type", ""),
        subject=entity.get("subject", ""),
        current_description=entity.get("description", ""),
        relationships=relationships_text,
        conversation_excerpts=context,
    )


def _enrich_entity(
    entity: dict,
    excerpts: list[str],
    other_names: set[str] | None = None,
) -> dict | None:
    """Enrich an entity's description using conversation context.

    A reply that fails a check below is remembered as turned down for this
    prompt, so the same entity and evidence are not sent again.  A lesson
    (self_knowledge) is never rewritten.
    """
    import row_bot.knowledge_graph as kg

    if _is_lesson(entity):
        return None
    prompt = _enrich_prompt(entity, excerpts)

    def turned_down() -> None:
        _record_rejection("enrich", str(entity["id"]), prompt)

    try:
        from row_bot.providers.background import text_field

        raw = _llm_call(prompt, answer=_description_answer())
        enriched = text_field(raw, "description")
        if not enriched or len(enriched) < 10:
            if _answered(raw):
                turned_down()
            return None
    except Exception as exc:
        logger.warning("Dream enrich LLM call failed: %s", exc)
        return None

    # Safety: new description must be at least as long as the old one
    old_desc = entity.get("description", "") or ""
    if len(enriched) < len(old_desc):
        turned_down()
        return None

    # Identity check: reject if the LLM returned the exact same text
    if enriched.strip() == old_desc.strip():
        logger.info(
            "Dream enrich skipped identity (no change): '%s' (%d chars)",
            entity.get("subject", ""), len(old_desc),
        )
        turned_down()
        return None

    # Layer 2: Cross-entity contamination check (deterministic)
    if other_names is not None:
        if not _validate_enrichment(enriched, entity, other_names, excerpts):
            turned_down()
            return None

    # Layer 3: Fact-grounding verification — reject sentences that
    # introduce facts not mentioned in any excerpt or the old description.
    # Split new content into sentences and check each one has evidence.
    import re as _re_enrich
    old_lower = old_desc.lower()
    excerpts_lower = " ".join(excerpts).lower()
    new_sentences = _re_enrich.split(r"(?<=[.!?])\s+", enriched)
    ungrounded = []
    for sentence in new_sentences:
        s = sentence.strip().lower()
        if not s or len(s) < 15:
            continue  # Skip short fragments
        # Check if key words from this sentence appear in sources
        # Extract meaningful words (>3 chars, not stopwords)
        words = [w for w in _re_enrich.findall(r"\b\w{4,}\b", s)
                 if w not in {"this", "that", "with", "from", "have", "been",
                              "also", "they", "their", "about", "which", "when",
                              "where", "what", "into", "more", "some", "other",
                              "than", "very", "each", "most"}]
        if not words:
            continue
        # At least 40% of meaningful words must appear in sources
        in_sources = sum(1 for w in words if w in old_lower or w in excerpts_lower)
        ratio = in_sources / len(words) if words else 0
        if ratio < 0.4:
            ungrounded.append(sentence.strip()[:80])

    if ungrounded:
        logger.warning(
            "Dream enrich rejected for '%s': %d ungrounded sentence(s): %s",
            entity.get("subject", ""), len(ungrounded),
            "; ".join(ungrounded[:3]),
        )
        turned_down()
        return None

    try:
        kg.update_entity(entity["id"], enriched)
    except Exception as exc:
        logger.warning("Dream enrich update failed: %s", exc)
        return None

    return {
        "entity_id": entity["id"],
        "subject": entity.get("subject", ""),
        "old_length": len(old_desc),
        "new_length": len(enriched),
        "old_description": old_desc[:200],
        "new_description": enriched[:200],
    }


# ── OP3: Relationship inference ──────────────────────────────────────────────

def _find_cooccurring_pairs(entities: list[dict], conversations: _Conversations) -> list[tuple[dict, dict, str, int]]:
    """Entity pairs named together in a passage of a conversation, with no edge yet.

    Returns ``(entity_a, entity_b, excerpt, co_occurrence_count)`` tuples, the
    most conversations first.  The excerpt is always a passage naming both
    (the read keeps no other), and the count is how many conversations hold one.
    """
    import re as _re
    import row_bot.knowledge_graph as kg
    from collections import Counter as _Counter

    _VAGUE_EDGE_TYPES = {"related_to", "associated_with"}
    _HUB_CAP = 3  # max appearances per entity across selected pairs

    pairs: list[tuple[dict, dict, str, int]] = []
    entity_use_count: _Counter = _Counter()

    # Build entity lookup by ID for quick access
    entity_by_id: dict[str, dict] = {e["id"]: e for e in entities}
    rejections = _load_rejection_cache()
    model_ref = _dream_model_ref()

    # Sort by co-occurrence count descending (most evidence first), ties by pair
    ranked_pairs = sorted(conversations.pairs.items(), key=lambda item: (-item[1][0], item[0]))

    for pair_key, (co_count, excerpts) in ranked_pairs:
        if len(pairs) >= 25:
            break

        ea_id, eb_id = pair_key

        # Hub diversity cap: skip if either entity already used 3 times
        if entity_use_count[ea_id] >= _HUB_CAP or entity_use_count[eb_id] >= _HUB_CAP:
            continue

        ea = entity_by_id.get(ea_id)
        eb = entity_by_id.get(eb_id)
        if not ea or not eb or not excerpts:
            continue

        # Pre-flight merge check: if A's description mentions B's subject
        # (or vice versa) AND they share the same entity_type, these are
        # likely duplicates — skip inference.  Cross-type mentions are
        # normal (e.g. a project description mentioning a skill).
        _desc_a = (ea.get("description", "") or "").lower()
        _desc_b = (eb.get("description", "") or "").lower()
        _subj_a = (ea.get("subject", "") or "").strip().lower()
        _subj_b = (eb.get("subject", "") or "").strip().lower()
        _same_type = ea.get("entity_type") == eb.get("entity_type")
        if _same_type and _subj_b and len(_subj_b) > 2 and _re.search(r"\b" + _re.escape(_subj_b) + r"\b", _desc_a):
            logger.info(
                "Dream skip infer (probable duplicate): '%s' description mentions '%s'",
                ea.get("subject", ""), eb.get("subject", ""),
            )
            continue
        if _same_type and _subj_a and len(_subj_a) > 2 and _re.search(r"\b" + _re.escape(_subj_a) + r"\b", _desc_b):
            logger.info(
                "Dream skip infer (probable duplicate): '%s' description mentions '%s'",
                eb.get("subject", ""), ea.get("subject", ""),
            )
            continue

        # Check if they already have a meaningful (non-vague) relation
        try:
            existing = kg.get_relations(ea["id"], direction="both")
            has_meaningful_edge = any(
                r.get("peer_id") == eb["id"]
                and r.get("relation_type", "related_to") not in _VAGUE_EDGE_TYPES
                for r in existing
            )
            if has_meaningful_edge:
                continue
        except Exception:
            continue

        # Multi-excerpt evidence: up to 3 passages (the longest first) for rich context.
        if co_count >= 3:
            evidence = "\n\n---\n\n".join(excerpts[:3])
        else:
            evidence = excerpts[0]

        # Skip a pair the model turned down after reading this same evidence
        if _rejected_with_same_evidence("infer", _pair_key(ea_id, eb_id),
                                        _infer_prompt(ea, eb, evidence, co_count), rejections, model_ref=model_ref):
            continue

        pairs.append((ea, eb, evidence, co_count))
        entity_use_count[ea_id] += 1
        entity_use_count[eb_id] += 1

    return pairs


def _infer_prompt(entity_a: dict, entity_b: dict, excerpt: str, co_occurrence_count: int) -> str:
    from row_bot.prompts import DREAM_INFER_PROMPT

    return DREAM_INFER_PROMPT.format(
        type_a=entity_a.get("entity_type", ""),
        subject_a=entity_a.get("subject", ""),
        description_a=entity_a.get("description", ""),
        type_b=entity_b.get("entity_type", ""),
        subject_b=entity_b.get("subject", ""),
        description_b=entity_b.get("description", ""),
        conversation_excerpt=excerpt[:1000],
        co_occurrence_count=co_occurrence_count,
    )


def _infer_relation(entity_a: dict, entity_b: dict, excerpt: str,
                    confidence: float = 0.7, co_occurrence_count: int = 1) -> dict | None:
    """Ask the LLM if two entities are related, given conversation evidence.

    The LLM returns confidence, evidence quote, and directionality.
    Vague relation types are rejected.  Confidence below 0.75 is rejected.
    A reply that gives no relation is remembered as turned down for this
    prompt; a failed call or a reply cut short is not.
    """
    import row_bot.knowledge_graph as kg

    # Banned vague relation types that add noise to the graph
    _BANNED_TYPES = {
        "related_to", "associated_with", "connected_to", "linked_to",
        "has_relation", "involves", "correlates_with",
    }

    # ── Tautology guard: reject if one subject is a substring of the other ──
    # Checked before the call: it does not depend on the reply.
    _subj_a_low = (entity_a.get("subject", "") or "").strip().lower()
    _subj_b_low = (entity_b.get("subject", "") or "").strip().lower()
    if _subj_a_low and _subj_b_low and len(_subj_a_low) > 2 and len(_subj_b_low) > 2:
        if _subj_a_low in _subj_b_low or _subj_b_low in _subj_a_low:
            logger.info(
                "Dream infer rejected tautology: '%s' ↔ '%s' (substring match)",
                entity_a.get("subject", ""), entity_b.get("subject", ""),
            )
            return None

    prompt = _infer_prompt(entity_a, entity_b, excerpt, co_occurrence_count)

    def turned_down() -> None:
        _record_rejection("infer", _pair_key(entity_a["id"], entity_b["id"]), prompt)

    try:
        raw = _llm_call(prompt, answer=_infer_answer(), max_tokens=_INFER_MAX_TOKENS)
    except Exception as exc:
        logger.warning("Dream infer LLM call failed: %s", exc)
        return None

    # Parse JSON response
    try:
        import row_bot.knowledge_graph as _kg_parse
        _json_str = _kg_parse.extract_json_block(raw, "{")
        if not _json_str:
            if _answered(raw):
                turned_down()
            return None
        result = json.loads(_json_str)
        if not result.get("has_relation"):
            turned_down()
            return None
        rel_type = result.get("relation_type", "").strip().lower().replace(" ", "_")
        if not rel_type:
            turned_down()
            return None
    except (json.JSONDecodeError, AttributeError):
        if _answered(raw):
            turned_down()
        return None

    # ── Post-filter: reject vague relation types ─────────────────────
    if rel_type in _BANNED_TYPES:
        logger.info(
            "Dream infer rejected vague type '%s' for %s → %s",
            rel_type, entity_a.get("subject", ""), entity_b.get("subject", ""),
        )
        turned_down()
        return None

    # ── Dynamic confidence from LLM ──────────────────────────────────
    llm_confidence = result.get("confidence")
    if isinstance(llm_confidence, (int, float)):
        final_confidence = max(0.0, min(1.0, float(llm_confidence)))
    else:
        final_confidence = confidence  # Fallback to config default

    # Reject low-confidence inferences
    if final_confidence < 0.80:
        logger.info(
            "Dream infer rejected low confidence (%.2f) for %s → %s",
            final_confidence, entity_a.get("subject", ""), entity_b.get("subject", ""),
        )
        turned_down()
        return None

    # ── Directionality from LLM ──────────────────────────────────────
    llm_source = (result.get("source", "") or "").strip().lower()
    llm_target = (result.get("target", "") or "").strip().lower()
    subj_a = (entity_a.get("subject", "") or "").strip().lower()
    subj_b = (entity_b.get("subject", "") or "").strip().lower()

    # If LLM says source=B and target=A, swap the direction
    if llm_source == subj_b and llm_target == subj_a:
        source_entity, target_entity = entity_b, entity_a
    else:
        # Default: entity_a → entity_b (or LLM confirmed A → B)
        source_entity, target_entity = entity_a, entity_b

    # ── Evidence tracking ────────────────────────────────────────────
    evidence = (result.get("evidence", "") or "").strip()
    rel_properties = {}
    if evidence:
        rel_properties["evidence"] = evidence[:500]
    rel_properties["co_occurrences"] = co_occurrence_count

    # Add the relation
    try:
        rel = kg.add_relation(
            source_entity["id"], target_entity["id"], rel_type,
            source="dream_infer",
            confidence=final_confidence,
            properties=rel_properties,
        )
        if not rel:
            return None
    except Exception as exc:
        logger.debug("Dream infer add_relation failed: %s", exc)
        return None

    return {
        "source_id": source_entity["id"],
        "source_subject": source_entity.get("subject", ""),
        "target_id": target_entity["id"],
        "target_subject": target_entity.get("subject", ""),
        "relation_type": rel_type,
        "confidence": final_confidence,
        "evidence": evidence,
        "co_occurrences": co_occurrence_count,
    }


# ── OP5: Insights analysis ──────────────────────────────────────────────────

def _collect_system_snapshot() -> str:
    """Gather a text snapshot of recent system activity for insights analysis."""
    sections: list[str] = []

    # 1. Recent logs (warnings and errors only)
    try:
        from row_bot.logging_config import read_recent_logs
        logs = read_recent_logs(100)
        error_logs = [
            entry for entry in logs
            if entry.get("level", "").upper() in ("WARNING", "ERROR", "CRITICAL")
        ]
        if error_logs:
            lines = []
            for entry in error_logs[:30]:
                ts = entry.get("ts", "?")
                lvl = entry.get("level", "?")
                msg = entry.get("msg", "")[:200]
                tool = entry.get("tool", "")
                tool_label = f" [{tool}]" if tool else ""
                lines.append(f"  [{ts}]{tool_label} {lvl}: {msg}")
            sections.append("RECENT WARNINGS/ERRORS (last 7 days):\n" + "\n".join(lines))
            tool_counts: dict[str, int] = {}
            for entry in error_logs:
                tool = (entry.get("tool", "") or "").strip()
                if not tool:
                    continue
                tool_counts[tool] = tool_counts.get(tool, 0) + 1
            if tool_counts:
                count_lines = [
                    f"  {tool}: {count}"
                    for tool, count in sorted(tool_counts.items(), key=lambda item: (-item[1], item[0]))
                ]
                sections.append("TOOL ERROR COUNTS:\n" + "\n".join(count_lines))
            else:
                sections.append("TOOL ERROR COUNTS: None")
        else:
            sections.append("RECENT WARNINGS/ERRORS: None")
            sections.append("TOOL ERROR COUNTS: None")
    except Exception:
        sections.append("RECENT WARNINGS/ERRORS: (unavailable)")
        sections.append("TOOL ERROR COUNTS: (unavailable)")

    # 2. Knowledge graph stats
    try:
        import row_bot.knowledge_graph as kg
        entity_count = kg.count_entities()
        relation_count = kg.count_relations()
        sections.append(
            f"KNOWLEDGE GRAPH: {entity_count} entities, {relation_count} relations"
        )
    except Exception:
        sections.append("KNOWLEDGE GRAPH: (unavailable)")

    # 3. Document library
    try:
        from row_bot.documents import load_processed_files
        processed_files = load_processed_files()
        sections.append(f"DOCUMENT LIBRARY: {len(processed_files)} indexed files")
    except Exception:
        sections.append("DOCUMENT LIBRARY: (unavailable)")

    # 4. Task history
    try:
        from row_bot.tasks import list_tasks
        tasks = list_tasks()
        if tasks:
            lines = []
            for t in tasks[:15]:
                name = t.get("name", "?")
                enabled = t.get("enabled", False)
                last_run = t.get("last_run") or "never"
                last_status = t.get("last_status")
                if not last_status:
                    last_status = "never_run" if last_run == "never" else "no_history"
                lines.append(f"  {name}: enabled={enabled}, last_run={last_run}, status={last_status}")
            sections.append("TASKS:\n" + "\n".join(lines))
        else:
            sections.append("TASKS: None configured")
    except Exception:
        sections.append("TASKS: (unavailable)")

    # 5. Active model and provider runtime
    try:
        from row_bot.models import get_current_model, get_context_size, get_cloud_provider, is_model_local
        from row_bot.providers.selection import provider_display_label

        model = get_current_model()
        local = is_model_local(model)
        provider_id = "local" if local else (get_cloud_provider(model) or "provider")
        provider_label = provider_display_label(provider_id)
        sections.append(
            f"MODEL: {model}; provider={provider_label}; type={'local' if local else 'provider'}; "
            f"context={get_context_size(model)}"
        )
    except Exception:
        sections.append("MODEL: (unavailable)")

    # 6. Provider connections and Quick Choices
    try:
        from row_bot.providers.status import provider_status_cards
        from row_bot.providers.model_visibility import read_model_visibility

        cards = provider_status_cards()
        configured = [card for card in cards if card.get("configured")]
        def _models_snapshot(card: dict) -> str:
            model_count = card.get("model_count")
            if model_count is None:
                return "models=unknown"
            return f"models={model_count}"

        lines = [
            f"  {card.get('display_name')}: source={card.get('source') or 'unknown'}, "
            f"runtime_enabled={card.get('runtime_enabled')}, {_models_snapshot(card)}"
            for card in configured[:12]
        ]
        quick_count = len(read_model_visibility().favourites)
        if lines:
            sections.append(
                f"PROVIDERS: {len(configured)} configured, {quick_count} status-tool Quick Choice(s)\n"
                + "\n".join(lines)
            )
        else:
            sections.append(f"PROVIDERS: none configured, {quick_count} status-tool Quick Choice(s)")
    except Exception:
        sections.append("PROVIDERS: (unavailable)")

    # 7. Media model defaults
    try:
        from row_bot.tools import registry as tool_registry
        from row_bot.tools.image_gen_tool import _get_configured_selection as _image_selection
        from row_bot.tools.video_gen_tool import _get_configured_selection as _video_selection

        sections.append(
            "MEDIA MODELS: "
            f"image={_image_selection()} (tool={'enabled' if tool_registry.is_enabled('image_gen') else 'disabled'}), "
            f"video={_video_selection()} (tool={'enabled' if tool_registry.is_enabled('video_gen') else 'disabled'})"
        )
    except Exception:
        sections.append("MEDIA MODELS: (unavailable)")

    # 8. Skills
    try:
        from row_bot.skills import get_manual_skill_statuses
        skill_statuses = get_manual_skill_statuses()
        enabled = [skill for skill, is_enabled in skill_statuses if is_enabled]
        skill_names = [skill.display_name or skill.name for skill, _ in skill_statuses]
        sections.append(
            f"SKILLS: {len(enabled)} enabled / {len(skill_statuses)} total — "
            + ", ".join(skill_names[:20])
        )
    except Exception:
        sections.append("SKILLS: (unavailable)")

    # 9. Channels
    try:
        from row_bot.channels.registry import configured_channels, running_channels
        configured = configured_channels()
        running = running_channels()
        if configured:
            lines = [f"  {ch.label}: running={ch in running}" for ch in configured]
            sections.append("CHANNELS:\n" + "\n".join(lines))
        else:
            sections.append("CHANNELS: None configured")
    except Exception:
        sections.append("CHANNELS: (unavailable)")

    # 10. Last dream cycle
    try:
        journal = _load_journal()
        if journal:
            last = journal[-1]
            sections.append(
                f"LAST DREAM CYCLE: {last.get('timestamp', '?')} — {last.get('summary', '?')}"
            )
        else:
            sections.append("LAST DREAM CYCLE: Never run")
    except Exception:
        sections.append("LAST DREAM CYCLE: (unavailable)")

    # 11. Existing active insights (so LLM avoids duplicates)
    try:
        from row_bot.insights import get_active_insights
        active = get_active_insights()
        if active:
            lines = [f"  [{i['category']}] {i['title']}" for i in active[:10]]
            sections.append("EXISTING ACTIVE INSIGHTS:\n" + "\n".join(lines))
    except Exception:
        pass

    return "\n\n".join(sections)


def _insights_evidence(prompt: str) -> str:
    """The hash of what the insights call reads, less the last cycle's summary line
    (its time and duration differ every night)."""
    return evidence_hash("\n".join(
        line for line in prompt.splitlines() if not line.startswith("LAST DREAM CYCLE:")))


def _remember_insights_evidence(evidence: str) -> None:
    cache = _load_rejection_cache()
    cache["insights"] = {"evidence": evidence, "at": _now_iso()}
    try:
        _save_rejection_cache(cache)
    except OSError:
        logger.debug("Could not save the Dream evidence cache", exc_info=True)


def _prune_insights(insights, status) -> None:
    """Insight maintenance, which needs no model."""
    try:
        pruned = insights.auto_prune()
        if pruned:
            status(f"Auto-pruned {pruned} stale insight(s)")
    except Exception:
        pass


def _run_insights_phase(cycle_id: str, on_status=None) -> dict:
    """Phase 5: Analyze system state and generate insights.

    Returns a dict with 'insights_added' and 'insights_merged' counts, and
    'skipped' when the snapshot is the one the last analysis read.
    """
    from row_bot.prompts import DREAM_INSIGHTS_PROMPT
    import row_bot.insights as insights

    result = {"insights_added": 0, "insights_merged": 0, "errors": []}

    def _status(msg: str):
        logger.info("Dream [%s]: %s", cycle_id, msg)
        if on_status:
            on_status(msg)

    _status("Phase 5: Analyzing system for insights…")

    # Collect data
    try:
        snapshot = _collect_system_snapshot()
    except Exception as exc:
        result["errors"].append(f"Snapshot collection failed: {exc}")
        return result

    # Get assistant name for prompt
    try:
        from row_bot.identity import get_assistant_name
        name = get_assistant_name()
    except Exception:
        name = "Row-Bot"

    prompt = DREAM_INSIGHTS_PROMPT.format(
        assistant_name=name,
        snapshot=snapshot,
    )

    # Nothing changed since the last analysis: the same snapshot gives the same insights.
    evidence = _insights_evidence(prompt)
    previous = _load_rejection_cache().get("insights")
    if previous and previous["evidence"] == evidence and _fresh(previous):
        result["skipped"] = True
        _prune_insights(insights, _status)
        _status("Insights phase: skipped, nothing changed since the last analysis")
        return result

    # LLM call
    try:
        raw = _llm_call(prompt, answer=_insights_answer(), max_tokens=_INSIGHTS_MAX_TOKENS)
    except Exception as exc:
        result["errors"].append(f"Insights LLM call failed: {exc}")
        return result

    # Parse JSON array
    try:
        from row_bot.providers.background import list_field

        items = list_field(raw, "insights")
        if items is None:
            # Try to extract JSON array from response
            import row_bot.knowledge_graph as _kg_parse
            json_str = _kg_parse.extract_json_block(raw, "[")
            if not json_str:
                # Fallback: try the whole response
                json_str = raw.strip()
            items = json.loads(json_str)
        if not isinstance(items, list):
            items = []
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning("Dream insights: failed to parse LLM response: %s", exc)
        result["errors"].append(f"JSON parse error: {exc}")
        return result

    # Store each insight, with the model in use: it may stop applying (B124).
    from row_bot.models import get_current_model

    model = get_current_model()
    for item in items[:5]:  # Cap at 5 per cycle
        try:
            added = insights.add_insight(
                category=item.get("category", "system_health"),
                severity=item.get("severity", "info"),
                title=item.get("title", "Untitled insight")[:100],
                body=item.get("body", ""),
                evidence=item.get("evidence", []),
                suggestion=item.get("suggestion", ""),
                auto_fixable=item.get("auto_fixable", False),
                confidence=float(item.get("confidence", 0.5)),
                source_cycle=cycle_id,
                skill_draft=item.get("skill_draft"),
                found_with_model=model,
            )
            if added:
                if added.get("_merged"):
                    result["insights_merged"] += 1
                else:
                    result["insights_added"] += 1
                _status(f"Insight: [{added['category']}] {added['title']}")
        except Exception as exc:
            result["errors"].append(f"Insight store error: {exc}")

    _remember_insights_evidence(evidence)
    _prune_insights(insights, _status)
    insights.set_last_analysis()
    _status(f"Insights phase: {result['insights_added']} new, {result['insights_merged']} merged")
    return result


# ── OP4: Confidence decay on stale inferences ───────────────────────────────
#
# Each inferred relation keeps its own decay stamp (``decayed_at`` in its
# properties); ``updated_at`` is left alone.  It loses 10% per 30 days unused
# and is removed below 0.3.  One re-stated since (``updated_at`` newer than
# its stamp) starts over.  Times compare as aware UTC; the naive timestamps
# saved elsewhere are local time.

_DECAY_EVERY_DAYS = 30
_DECAY_FACTOR = 0.9         # 10% less per period
_DECAY_DELETE_BELOW = 0.3   # removed when confidence drops below this


def _as_utc(value) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(value or ""))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.astimezone()  # a naive stored time is local time
    return moment.astimezone(timezone.utc)


def _decay_inferences(status=None) -> tuple[list[dict], list[dict]]:
    """Decay and prune Dream's inferred relations: ``(decayed, pruned)`` journal entries."""
    import row_bot.knowledge_graph as kg

    now = datetime.now(timezone.utc)
    period = timedelta(days=_DECAY_EVERY_DAYS)
    conn = kg._get_conn()
    try:
        rows = [dict(row) for row in conn.execute(
            "SELECT id, relation_type, confidence, properties, updated_at "
            "FROM relations WHERE source = 'dream_infer'").fetchall()]
    finally:
        conn.close()

    decayed: list[dict] = []
    to_prune: list[dict] = []
    updates: list[tuple[float, str, str]] = []
    for row in rows:
        try:
            props = json.loads(row["properties"] or "{}")
        except (TypeError, ValueError):
            props = {}
        props = props if isinstance(props, dict) else {}
        stated = _as_utc(row["updated_at"])
        stamp = _as_utc(props.get("decayed_at"))
        since = stated if stamp is None or (stated is not None and stated > stamp) else stamp
        if since is None:
            continue
        periods = int((now - since) / period)
        if periods < 1:
            continue
        old_conf = float(row["confidence"] or 0.0)
        new_conf = round(old_conf * _DECAY_FACTOR ** periods, 4)
        if new_conf < _DECAY_DELETE_BELOW:
            to_prune.append({"relation_id": row["id"], "relation_type": row["relation_type"],
                             "old_confidence": old_conf})
            continue
        props["decayed_at"] = (since + periods * period).isoformat()
        updates.append((new_conf, json.dumps(props), row["id"]))
        decayed.append({"relation_id": row["id"], "relation_type": row["relation_type"],
                        "old_confidence": old_conf, "new_confidence": new_conf})

    if updates:
        conn = kg._get_conn()
        try:
            conn.executemany("UPDATE relations SET confidence = ?, properties = ? WHERE id = ?", updates)
            conn.commit()
        finally:
            conn.close()

    pruned: list[dict] = []
    for entry in to_prune:
        if kg.delete_relation(entry["relation_id"]):
            pruned.append(entry)
            if status:
                status(f"Pruned stale inference: {entry['relation_type']} (conf {entry['old_confidence']:.2f})")
    return decayed, pruned


# ── Main dream cycle ────────────────────────────────────────────────────────

def run_dream_cycle(on_status=None) -> dict:
    """Execute one dream cycle. Returns a summary dict.

    Parameters
    ----------
    on_status : callable, optional
        Called with status strings for UI/logging feedback.
    """
    _dreaming.set()
    try:
        return _run_dream_cycle(on_status)
    finally:
        _dreaming.clear()


def _person_back() -> bool:
    """Stop preparing: the person came back, a turn started, or the loop is stopping."""
    from row_bot.runtime import executions

    return _dream_stop.is_set() or not _is_idle() or bool(executions.generation_registry.active())


def _prepare_tomorrow(summary: dict, status) -> None:
    """Phase 6: Proactive Row-Bot prepares the day ahead, read-only (``proactive.overnight``): briefs for
    tomorrow's meetings and drafts for the follow-ups due, waiting for their moments. Nothing at Off."""
    status("Phase 6: Preparing tomorrow…")
    try:
        from row_bot.proactive import overnight

        prepared = overnight.prepare_tomorrow(should_stop=_person_back)
    except Exception as exc:
        logger.warning("Overnight preparation failed", exc_info=True)
        summary["errors"].append(f"Preparing tomorrow: {type(exc).__name__}")
        return
    summary["prepared"] = prepared
    if prepared:
        status(f"Prepared {prepared} for tomorrow")


def _with_prepared(summary: dict) -> None:
    if summary.get("prepared"):
        summary["summary"] += f"; prepared {summary['prepared']} for tomorrow"


BACKUP_NAME = "memory.db.pre-dream-whole-graph"


def _backup_before_first_whole_graph_cycle(cfg: dict) -> bool:
    """Copy memory.db once, before the first cycle that compares the whole graph (5.3.4) changes anything.

    That first night may merge or replace up to a night's pairs of memories saved long before; the copy is
    how they come back. Taken with SQLite's own backup, so a write in progress can't tear it. False when the
    copy failed: the cycle then waits for another night.
    """
    import sqlite3

    import row_bot.knowledge_graph as kg

    if cfg.get("whole_graph_backup"):
        return True
    target = pathlib.Path(kg.DB_PATH).with_name(BACKUP_NAME)
    try:
        if not target.is_file():
            source = sqlite3.connect(kg.DB_PATH)
            copy = sqlite3.connect(str(target))
            try:
                source.backup(copy)
            finally:
                copy.close()
                source.close()
        cfg["whole_graph_backup"] = target.name
        _save_config(cfg)
        logger.info("Dream Cycle: memory backed up to %s before the first whole-graph cycle", target.name)
        return True
    except (OSError, sqlite3.Error) as exc:
        logger.warning("Dream Cycle: memory could not be backed up first (%s)", type(exc).__name__)
        return False


def _run_dream_cycle(on_status=None) -> dict:
    import row_bot.knowledge_graph as kg

    cycle_id = uuid.uuid4().hex[:8]
    start_time = datetime.now(timezone.utc)
    cfg = _load_config()

    summary = {
        "cycle_id": cycle_id,
        "timestamp": start_time.isoformat(),
        "merges": [],
        "superseded": [],
        "enrichments": [],
        "inferred_relations": [],
        "errors": [],
        "summary": "",
        "duration_s": 0,
    }

    def _status(msg: str):
        logger.info("Dream [%s]: %s", cycle_id, msg)
        if on_status:
            on_status(msg)

    if not _model_chosen():
        _status(NO_MODEL_SKIP)
        summary["summary"] = NO_MODEL_SKIP
        summary["duration_s"] = (datetime.now(timezone.utc) - start_time).total_seconds()
        return summary

    # Check minimum entity count
    entity_count = kg.count_entities()
    min_entities = cfg.get("min_entities", 20)
    if entity_count < min_entities:
        _status(f"Skipped — only {entity_count} entities (min: {min_entities})")
        # Shown on Settings › Preferences: plain words, not "entities".
        summary["summary"] = (
            f"Skipped — waits for {min_entities} saved memories; there are {entity_count} so far"
        )
        _prepare_tomorrow(summary, _status)  # Tomorrow's preparation doesn't wait for memories.
        _with_prepared(summary)
        summary["duration_s"] = (datetime.now(timezone.utc) - start_time).total_seconds()
        _append_journal(summary)
        return summary

    all_entities = kg.list_entities(limit=100_000)
    _status(f"Starting dream cycle — {entity_count} entities")
    if not _backup_before_first_whole_graph_cycle(cfg):
        summary["errors"].append("Skipped: memory could not be backed up before the first whole-graph cycle")
        summary["summary"] = "Skipped — memory could not be backed up first; it will try again next night"
        _prepare_tomorrow(summary, _status)
        _with_prepared(summary)
        summary["duration_s"] = (datetime.now(timezone.utc) - start_time).total_seconds()
        _append_journal(summary)
        return summary

    # Discover candidates before deferring this cycle's projection writes.
    _status("Phase 1: Comparing similar memories…")
    candidates = _find_pair_candidates(all_entities)
    with kg.projection_batch(drain_on_exit=False):

        _settle_pairs(candidates, summary, _status)

        # What is left after the merges and replacements, read again.
        entities = [entity for entity in kg.list_entities(limit=100_000)
                    if _active(entity) and not _is_user(entity)]

        # ── OP2: Description enrichment ──────────────────────────────
        _status("Phase 2: Enriching thin descriptions…")
        min_chars = cfg.get("enrich_min_chars", 80)
        thin_entities, other_names = _prepare_enrichment_inputs(entities, min_chars)
        # The one read of the conversations tonight, for enrichment and inference.
        conversations = _read_conversations(entities, {entity["id"] for entity in thin_entities})
        model_ref = _dream_model_ref()

        asked = 0
        for entity in thin_entities:
            if asked >= _ENRICH_PER_NIGHT:
                break
            excerpts = conversations.mentions.get(entity["id"], [])
            if len(excerpts) < 2:
                continue  # Need 2+ conversations as evidence
            if _rejected_with_same_evidence("enrich", str(entity["id"]), _enrich_prompt(entity, excerpts),
                                            model_ref=model_ref):
                continue  # Turned down before by this model, and nothing new to read
            asked += 1

            try:
                result = _enrich_entity(entity, excerpts, other_names)
                if result:
                    summary["enrichments"].append(result)
                    _status(
                        f"Enriched: '{result['subject']}' "
                        f"({result['old_length']} → {result['new_length']} chars)"
                    )
            except Exception as exc:
                summary["errors"].append(f"Enrich error: {exc}")

        # ── OP3: Confidence decay on stale inferences ────────────────
        _status("Phase 3: Decaying stale inferences…")
        summary["decayed"] = []
        summary["pruned"] = []
        try:
            summary["decayed"], summary["pruned"] = _decay_inferences(_status)
            if summary["decayed"] or summary["pruned"]:
                _status(
                    f"Decayed {len(summary['decayed'])} stale inference(s), "
                    f"pruned {len(summary['pruned'])}"
                )
        except Exception as exc:
            summary["errors"].append(f"Decay error: {exc}")

        # ── OP4: Relationship inference ──────────────────────────────
        _status("Phase 4: Inferring relationships…")
        infer_confidence = cfg.get("infer_confidence", 0.7)
        pairs = _find_cooccurring_pairs(entities, conversations)

        for entity_a, entity_b, excerpt, co_count in pairs[:15]:  # Cap inferences per cycle
            try:
                result = _infer_relation(
                    entity_a, entity_b, excerpt,
                    confidence=infer_confidence,
                    co_occurrence_count=co_count,
                )
                if result:
                    summary["inferred_relations"].append(result)
                    _status(
                        f"Inferred: '{result['source_subject']}' "
                        f"--[{result['relation_type']}]--> "
                        f"'{result['target_subject']}' "
                        f"(conf={result['confidence']:.2f}, "
                        f"co_occ={result.get('co_occurrences', '?')})"
                    )
                # A pair the model turned down is remembered with its evidence (_infer_relation).
            except Exception as exc:
                summary["errors"].append(f"Infer error: {exc}")

        # ── OP5: Insights analysis ───────────────────────────────────
        try:
            insights_result = _run_insights_phase(cycle_id, on_status=on_status)
            summary["insights"] = insights_result
            if insights_result.get("errors"):
                summary["errors"].extend(insights_result["errors"])
        except Exception as exc:
            summary["errors"].append(f"Insights error: {exc}")

    try:
        projection = kg.repair_projections(max_entities=1000, cancelled=_dream_stop.is_set)
        if not projection["complete"]:
            summary["errors"].append("Saved knowledge has pending projection repair")
    except Exception as exc:
        summary["errors"].append("Saved knowledge projection repair failed")
        logger.warning("Post-dream projection repair failed: %s", exc)

    _prepare_tomorrow(summary, _status)

    # Compose summary text
    m = len(summary["merges"])
    e = len(summary["enrichments"])
    r = len(summary["inferred_relations"])
    d = len(summary.get("decayed", []))
    p = len(summary.get("pruned", []))
    ins = summary.get("insights", {})
    i_add = ins.get("insights_added", 0)
    i_merge = ins.get("insights_merged", 0)
    errs = len(summary["errors"])
    duration = (datetime.now(timezone.utc) - start_time).total_seconds()
    summary["duration_s"] = round(duration, 1)
    parts = [f"{m} merge(s), {e} enrichment(s), {r} inference(s)"]
    if summary["superseded"]:
        parts.append(f"{len(summary['superseded'])} replaced by newer memories")
    if d or p:
        parts.append(f"{d} decayed, {p} pruned")
    if i_add or i_merge:
        parts.append(f"{i_add} insight(s), {i_merge} merged")
    if summary.get("prepared"):
        parts.append(f"{summary['prepared']} prepared for tomorrow")
    if errs:
        parts.append(f"{errs} error(s)")
    parts.append(f"in {duration:.0f}s")
    summary["summary"] = ", ".join(parts)

    _status(f"Dream cycle complete — {summary['summary']}")
    _append_journal(summary)
    return summary


# ── Background daemon ────────────────────────────────────────────────────────

_dream_thread: threading.Thread | None = None
_dream_stop = threading.Event()


def start_dream_loop() -> None:
    """Start the daemon thread that checks for dream conditions."""
    global _dream_thread
    if _dream_thread is not None and _dream_thread.is_alive():
        return

    _dream_stop.clear()

    def _loop():
        while not _dream_stop.wait(timeout=_CHECK_INTERVAL_S):
            if not _should_dream():
                continue
            logger.info("Dream conditions met — starting dream cycle…")
            try:
                result = run_dream_cycle()
                logger.info("Dream cycle complete: %s", result.get("summary", ""))
            except Exception as exc:
                logger.warning("Dream cycle failed: %s", exc)

    _dream_thread = threading.Thread(target=_loop, daemon=True, name="row-bot-dream-cycle")
    _dream_thread.start()
    logger.info(
        "Dream cycle daemon started — window %d:00–%d:00, checks every %d min",
        _load_config().get("window_start", 1),
        _load_config().get("window_end", 5),
        _CHECK_INTERVAL_S // 60,
    )


def stop_dream_loop() -> None:
    """Signal the dream loop thread to stop."""
    _dream_stop.set()
