"""The arms' interventions (stale skills, on only): R reconciles, D notes the dates, F repairs after a failure.

All three start from the same question, :func:`conflicts`: which of a skill's linked memories changed after the skill
was last revised, in a way that touches a value the skill states? A change counts when the newer memory (an updated
description, a memory superseding another, or a new memory linked to the skill) states a value of a kind the skill
states (an email address, an amount, a time, a number with its unit) that the skill doesn't have, and the skill
references that memory by name or names a value the old memory had. Only values from a trusted source count (what the
person said, or the world's own sources; never the agent's own actions or replies: :mod:`.sources`); a change whose
new values come from nowhere trusted is logged (``untrusted_source``) and left alone. Dream's own merges and
enrichment never count.

- **R** (:func:`reconcile`, a Dream Cycle phase): for each change since the last night, the model drafts the skill
  with the current fact (or says it needs no change); a draft becomes a ``patch_skill`` proposal through
  :mod:`row_bot.evolution` (its bounded diff, rejection memory and approval gate). Every candidate is logged.
- **D** (:func:`date_note`): one line next to the skill wherever it is shown, naming both values and both dates, only
  when the memory is the newer of the two (a skill revised after the memory changed gets no line).
- **F** (:func:`repair`): after a run that used the skill failed its check, the model drafts a repair from that
  run's request, answer and the person's correction; the draft is a ``patch_skill`` proposal like R's.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from row_bot import stale_skills
from row_bot.stale_skills import facts, links, sources

logger = logging.getLogger(__name__)

RECONCILE_STATE = "reconcile.json"
#: At most this many drafts a night (each is one model call).
MAX_RECONCILE_DRAFTS = 12
_REPLY_TOKENS = 2048
_TEXT_CHARS = 600
_untrusted_logged: set[tuple[str, str]] = set()


def _skill(name: str) -> Any:
    from row_bot import skills

    if not skills.skills_loaded():
        skills.load_skills()
    return skills.get_skill(name)


def _user_skill_names() -> list[str]:
    from row_bot import skills

    if not skills.skills_loaded():
        skills.load_skills()
    return sorted(skill.name for skill in skills.get_manual_skills() if skill.source == "user")


def _short(text: object, limit: int = _TEXT_CHARS) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _added(old: str | None, new: str) -> str:
    """What a description gained, when it was only added to; else all of it."""

    old = str(old or "").strip()
    new = str(new or "").strip()
    return new[len(old):].strip(" .;,") or new if old and new.startswith(old) else new


def _entity(entity_id: str) -> dict[str, Any] | None:
    import row_bot.knowledge_graph as kg

    return kg.get_entity(entity_id)


def conflict(skill_text: str, change: dict[str, Any], linked: frozenset[str] = frozenset()) -> dict[str, Any] | None:
    """How ``change`` touches what a skill says (``linked``: the memories the skill is linked to):
    ``{"fresh": [...], "skill_says": [...]}``, or None."""

    skill_values = links.value_tokens(skill_text)
    new_values = links.value_tokens(change.get("new") or "")
    old_values = links.value_tokens(change.get("old") or "")
    kinds = set(skill_values.values())
    fresh = [token for token, kind in new_values.items() if kind in kinds and token not in skill_values]
    if not fresh:
        return None
    named_old = [token for token in skill_values if token in old_values and token not in new_values]
    fresh_kinds = {new_values[token] for token in fresh}
    entity = _entity(str(change.get("new_entity_id") or change.get("entity_id") or ""))
    named = bool(entity and (str(entity["id"]) in linked or str(change.get("entity_id")) in linked
                              or links.references(entity, skill_text)))
    shared = [token for token in skill_values if token in new_values or token in old_values]
    if not (named or named_old or shared):
        return None
    says = named_old or [token for token, kind in skill_values.items() if kind in fresh_kinds]
    return {"fresh": fresh, "skill_says": says[:3], "subject": str((entity or {}).get("subject") or "")}


def conflicts(name: str, *, since: str = "", until: str = "") -> list[dict[str, Any]]:
    """The skill's conflicts with memory changed after its last revision (and after ``since``), oldest first."""

    record = links.skill_record(name)
    skill = _skill(name)
    if record is None or skill is None:
        return []
    revised = str(record.get("revised_at") or "")
    text = str(skill.instructions or "")
    linked = frozenset(str(item.get("entity_id")) for item in record.get("references") or ())
    found = []
    for change in facts.changes(max(since, revised), until):
        touched = conflict(text, change, linked)
        if not touched:
            continue
        fresh = sources.trusted(touched["fresh"], str(change.get("origin") or ""))
        if not fresh:
            key = (name, str(change.get("new_validity") or ""))
            if key not in _untrusted_logged:
                _untrusted_logged.add(key)
                stale_skills.log("untrusted_source", skill=name, kind=change.get("kind"),
                                 entity_id=change.get("new_entity_id") or change.get("entity_id"),
                                 new_validity=change.get("new_validity"), changed_at=change.get("at"),
                                 origin=change.get("origin"), fresh=touched["fresh"])
            continue
        found.append({**change, **touched, "fresh": fresh, "skill": name, "skill_revised_at": revised})
    return found


# ── D: the date-aware note ───────────────────────────────────────────────────


def date_note(name: str) -> str:
    """One line for a shown skill whose linked memory is newer and says otherwise (arm D only); else ""."""

    if not stale_skills.arm_on("D"):
        return ""
    try:
        found = conflicts(name)
    except Exception:
        logger.warning("Stale skills: no date note for %s", name, exc_info=True)
        return ""
    if not found:
        return ""
    latest = found[-1]
    record = links.skill_record(name) or {}
    display = str(record.get("display_name") or name)
    skill = _skill(name)
    says = ", ".join(f'"{links.as_written(token, str(skill.instructions or ""))}"'
                     for token in latest["skill_says"]) or "an older value"
    memory = _short(_added(latest.get("old"), latest["new"]), 260)
    latest = {**latest, "fresh": [links.as_written(token, latest["new"]) for token in latest["fresh"]]}
    subject = latest.get("subject") or "a memory"
    line = (f"DATE CHECK for the skill \"{display}\": the skill was last revised on "
            f"{stale_skills.say_date(latest['skill_revised_at'])} and says {says}. The memory \"{subject}\" is "
            f"NEWER: it changed on {stale_skills.say_date(latest['at'])}, after the skill, and says: \"{memory}\". "
            f"Where the two differ, follow the newer memory ({', '.join(latest['fresh'][:3])}), not the skill.")
    stale_skills.log("date_note", skill=name, entity_id=latest.get("new_entity_id") or latest.get("entity_id"),
                     skill_revised_at=latest["skill_revised_at"], memory_changed_at=latest["at"],
                     new_validity=latest.get("new_validity"))
    return line


# ── R and F: drafts through evolution ────────────────────────────────────────


def _draft_answer() -> Any:
    from row_bot.providers.background import structured_answer

    return structured_answer("skill_patch", {
        "patch": {"type": "boolean", "description": "True when the skill must change."},
        "instructions": {"type": "string", "description": "The whole updated skill, or an empty string."},
        "reason": {"type": "string", "description": "One short sentence."},
    })


_RECONCILE_PROMPT = """\
A saved skill is a step-by-step procedure an assistant follows every time. A fact the skill may state has changed
in the assistant's memory since the skill was last revised.

The skill "{display}" (last revised on {revised}):
<<<
{instructions}
>>>

The memory "{subject}" changed on {changed}.
Before: {old}
Now: {new}

If the skill states something this change makes out of date, rewrite the skill so it uses the current fact and change
nothing else. If the change doesn't affect anything the skill says, don't patch it.
Reply with JSON only: {{"patch": true or false, "instructions": "<the whole updated skill, or empty>", "reason": "<one short sentence>"}}"""

_REPAIR_PROMPT = """\
A saved skill is a step-by-step procedure an assistant follows every time. A run that used this skill failed: the
person said the result was wrong.

The skill "{display}":
<<<
{instructions}
>>>

The person asked: {request}
The assistant's answer: {answer}
The person's correction: {correction}

If the skill's instructions led to this mistake, rewrite the skill so the next run gets it right and change nothing
else. If the skill was not the cause, don't patch it.
Reply with JSON only: {{"patch": true or false, "instructions": "<the whole updated skill, or empty>", "reason": "<one short sentence>"}}"""


def _parsed(raw: str) -> dict[str, Any]:
    from row_bot.providers.background import parse_json_object

    found = parse_json_object(str(raw or "")) or {}
    patch = found.get("patch")
    return {"patch": patch is True or str(patch).strip().lower() == "true",
            "instructions": str(found.get("instructions") or "").strip(), "reason": str(found.get("reason") or "")}


def _propose(skill: Any, draft: dict[str, Any], *, title: str, reason: str) -> dict[str, Any]:
    from row_bot import evolution

    current = str(skill.instructions or "").strip()
    if not draft["patch"] or not draft["instructions"] or draft["instructions"] == current:
        return {"outcome": "no_change", "reason": draft["reason"][:300]}
    proposal = evolution.build_patch_skill_proposal(target_skill=skill.name, updated_instructions=draft["instructions"],
                                                    reason=reason, title=title)
    validation = (proposal.get("preview") or {}).get("validation") or {}
    return {"outcome": "proposed" if proposal.get("status") == "ready" else "invalid", "proposal_id": proposal["id"],
            "status": proposal.get("status"), "errors": list(validation.get("errors") or ()),
            "changed_lines": (proposal.get("preview") or {}).get("changed_lines"), "reason": draft["reason"][:300]}


def reconcile(llm: Callable[..., str]) -> dict[str, Any]:
    """Tonight's reconciliation (arm R): a draft for each skill whose linked memory changed since last night."""

    if not stale_skills.arm_on("R"):
        return {}
    state = stale_skills.read_json(RECONCILE_STATE)
    since = str(state.get("last") or "")
    until = stale_skills.now().isoformat(timespec="seconds")
    found = [item for name in _user_skill_names() for item in conflicts(name, since=since, until=until)]
    # One draft a skill a night, from its latest change (the memory as it stands now); the earlier ones are logged.
    latest: dict[str, dict[str, Any]] = {}
    for item in sorted(found, key=lambda item: str(item["at"])):
        if item["skill"] in latest:
            earlier = latest[item["skill"]]
            stale_skills.log("patch_candidate", arm="R", skill=earlier["skill"], outcome="later_change_drafted",
                             entity_id=earlier.get("new_entity_id"), new_validity=earlier.get("new_validity"))
        latest[item["skill"]] = item
    # Changed descriptions first, then memories superseded, then new memories; oldest first within each.
    order = {"update": 0, "supersede": 1, "write": 2}
    candidates = sorted(latest.values(), key=lambda item: (order.get(item["kind"], 3), str(item["at"])))
    result: dict[str, Any] = {"since": since, "until": until, "candidates": len(found), "drafted": 0,
                              "proposed": 0, "skipped": 0}
    for index, candidate in enumerate(candidates):
        if result["drafted"] >= MAX_RECONCILE_DRAFTS:
            result["skipped"] += 1
            stale_skills.log("patch_candidate", arm="R", skill=candidate["skill"], outcome="over_cap",
                             entity_id=candidate.get("new_entity_id"), new_validity=candidate.get("new_validity"))
            continue
        skill = _skill(candidate["skill"])
        record = links.skill_record(candidate["skill"]) or {}
        display = str(record.get("display_name") or candidate["skill"])
        prompt = _RECONCILE_PROMPT.format(
            display=display, revised=stale_skills.say_date(candidate["skill_revised_at"]),
            instructions=str(skill.instructions or "").strip(), subject=candidate.get("subject") or "a memory",
            changed=stale_skills.say_date(candidate["at"]), old=_short(candidate.get("old") or "(no memory yet)"),
            new=_short(candidate["new"]))
        result["drafted"] += 1
        try:
            draft = _parsed(llm(prompt, answer=_draft_answer(), max_tokens=_REPLY_TOKENS))
            outcome = _propose(skill, draft, reason=f"Memory \"{candidate.get('subject')}\" changed on "
                               f"{stale_skills.say_date(candidate['at'])}: {_short(candidate['new'], 200)}",
                               title=f"Reconcile skill: {display} ({candidate.get('subject')}, "
                                     f"{stale_skills.say_date(candidate['at'])}, {index + 1})")
        except Exception as exc:
            logger.warning("Stale skills: reconcile draft for %s failed", candidate["skill"], exc_info=True)
            outcome = {"outcome": "error", "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        if outcome["outcome"] == "proposed":
            result["proposed"] += 1
        stale_skills.log("patch_candidate", arm="R", skill=candidate["skill"], kind=candidate["kind"],
                         entity_id=candidate.get("new_entity_id") or candidate.get("entity_id"),
                         old_validity=candidate.get("old_validity"), new_validity=candidate.get("new_validity"),
                         changed_at=candidate["at"], fresh=candidate["fresh"], skill_says=candidate["skill_says"],
                         **outcome)
    state["last"] = until
    stale_skills.write_json(RECONCILE_STATE, state)
    return result


def repair(name: str, *, request: str, answer: str, correction: str,
           llm: Callable[..., str] | None = None) -> dict[str, Any]:
    """A repair draft after a run that used skill ``name`` failed its check (arm F only)."""

    if not stale_skills.arm_on("F"):
        return {}
    skill = _skill(name)
    if skill is None or skill.source != "user":
        return {"outcome": "no_skill"}
    record = links.skill_record(name) or {}
    display = str(record.get("display_name") or name)
    prompt = _REPAIR_PROMPT.format(display=display, instructions=str(skill.instructions or "").strip(),
                                   request=_short(request), answer=_short(answer), correction=_short(correction))
    try:
        draft = _parsed((llm or _background)(prompt, answer=_draft_answer(), max_tokens=_REPLY_TOKENS))
        outcome = _propose(skill, draft, reason=f"A run using this skill failed: {_short(correction, 200)}",
                           title=f"Repair skill: {display} ({stale_skills.say_date(stale_skills.now())})")
    except Exception as exc:
        logger.warning("Stale skills: repair draft for %s failed", name, exc_info=True)
        outcome = {"outcome": "error", "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    stale_skills.log("patch_candidate", arm="F", skill=name, **outcome)
    return outcome


def _background(prompt: str, *, answer: Any = None, max_tokens: int | None = None) -> str:
    from langchain_core.messages import HumanMessage

    from row_bot import models
    from row_bot.providers.background import invoke_background

    return invoke_background("skill_repair", [HumanMessage(content=prompt)], answer=answer,
                             thread_model=str(models.get_current_model() or ""), max_tokens=max_tokens)


__all__ = ["MAX_RECONCILE_DRAFTS", "conflict", "conflicts", "date_note", "reconcile", "repair"]
