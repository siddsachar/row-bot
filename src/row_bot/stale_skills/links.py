"""Skill–memory links: which memories a user skill mentions (stale skills, on only).

A skill references a memory when its text contains the memory's name (its subject, an alias, or two words of the
subject in a row) or one of the values the memory states: an email address, an amount of money, a time of day, or a
number with its unit ("48 hours", "5 °C", "3 shifts"). Each reference is ``{entity_id, property, span}`` with
``property`` one of ``subject``, ``alias``, ``subject_part`` or ``value``.

The links are the skill's metadata, kept in ``stale_skills/skills.json`` with the skill's last revision (a skill's
SKILL.md front matter is rebuilt from its known keys on every update, so it can't hold them): a skill is linked when it
is created or patched (re-linked on every patch), a memory written later is linked to the skills that mention it, and
a skill saved before the setting was on is linked the first time it is asked about (its revision: the file's time).
A link stays when its memory changes (the skill was made from what the memory said); only a patch re-links.
The person's own memory (``User``) is never linked: it collects everything.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any

from row_bot import stale_skills

STORE = "skills.json"
_MONEY = r"(?:gbp|£|€|eur|usd|\$)\s?\d[\d,]*(?:\.\d+)?"
_TIME = (r"(?<![\d:.])(?:[01]?\d|2[0-3]):[0-5]\d(?![\d])(?:\s?[ap]m\b)?"
         r"|(?<![\d.:])(?:1[0-2]|0?[1-9])\s?(?:am|pm)\b")
_UNIT = (r"(?<![\w.])\d+(?:\.\d+)?\s?(?:-|\s)?(?:hours?|hrs?|days?|weeks?|minutes?|mins?|shifts?|°\s?c|degrees|kg|"
         r"litres?|loaves|boxes|percent|%)(?![\w])")
_EMAIL = r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"
TOKEN_KINDS = {"email": re.compile(_EMAIL, re.IGNORECASE), "money": re.compile(_MONEY, re.IGNORECASE),
               "time": re.compile(_TIME, re.IGNORECASE), "measure": re.compile(_UNIT, re.IGNORECASE)}
_WORD = re.compile(r"[a-z0-9][a-z0-9'-]*")
_STOP = frozenset({"the", "a", "an", "of", "and", "or", "for", "to", "our", "my", "your", "their", "new", "old",
                   "weekly", "current", "with", "from", "in", "on", "at", "is", "by", "this", "that", "user", "user's"})
_MIN_NAME = 4


_UNITS = {"hour": "hours", "hr": "hours", "day": "days", "week": "weeks", "minute": "minutes", "min": "minutes",
          "shift": "shifts", "degree": "°c", "°c": "°c", "kg": "kg", "litre": "litres", "loave": "loaves",
          "loaves": "loaves", "boxe": "boxes", "box": "boxes", "percent": "%", "%": "%"}


def _normal(token: str, kind: str) -> str:
    """One spelling per value: "£150" and "GBP 150" are "gbp 150"; "1pm" and "1:00 PM" are "13:00"; "48-hour" is
    "48 hours"."""

    text = " ".join(token.lower().split())
    if kind == "money":
        number = re.sub(r"[^\d.]", "", text).rstrip(".")
        number = number.rstrip("0").rstrip(".") if "." in number else number
        currency = "eur" if ("€" in text or "eur" in text) else "usd" if ("$" in text or "usd" in text) else "gbp"
        return f"{currency} {number}"
    if kind == "time":
        clock = re.match(r"(\d{1,2})[:.](\d{2})", text)
        if clock:
            hour = int(clock.group(1))
            hour = hour % 12 + 12 if "pm" in text and hour <= 12 else hour % 12 if "am" in text else hour
            return f"{hour:02d}:{clock.group(2)}"
        hour = int(re.match(r"\d+", text).group(0)) % 12 + (12 if "pm" in text else 0)
        return f"{hour:02d}:00"
    if kind == "measure":
        found = re.match(r"(\d+(?:\.\d+)?)\s?-?\s?(.+)", text)
        if found:
            unit = found.group(2).replace(" ", "").rstrip("s")
            return f"{found.group(1)} {_UNITS.get(unit, _UNITS.get(unit + 's', unit))}"
    return text


def value_tokens(text: str) -> dict[str, str]:
    """The values ``text`` states, normalised, with their kind: ``{"gbp 150": "money", ...}``."""

    found: dict[str, str] = {}
    for kind, pattern in TOKEN_KINDS.items():
        for match in pattern.finditer(str(text or "")):
            found.setdefault(_normal(match.group(0), kind), kind)
    return found


def as_written(token: str, text: str) -> str:
    """A normalised value (:func:`value_tokens`) as ``text`` writes it ("gbp 150" -> "GBP 150"), else the token."""

    for kind, pattern in TOKEN_KINDS.items():
        for match in pattern.finditer(str(text or "")):
            if _normal(match.group(0), kind) == token:
                return match.group(0).strip()
    return token


def _words(text: str) -> list[str]:
    return _WORD.findall(str(text or "").lower())


def _contains_name(text: str, name: str) -> bool:
    name = " ".join(str(name or "").split())
    if len(name) < _MIN_NAME or name.lower() in _STOP:
        return False
    return re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", text, re.IGNORECASE) is not None


def _is_user(entity: dict[str, Any]) -> bool:
    return " ".join(str(entity.get("subject") or "").lower().split()) == "user"


def _active(entity: dict[str, Any]) -> bool:
    from row_bot import memory_evolution

    return memory_evolution.get_properties(entity).get("status", "active") not in ("superseded", "archived")


def references(entity: dict[str, Any], text: str) -> list[dict[str, str]]:
    """How ``text`` (a skill's instructions) references ``entity``."""

    if _is_user(entity) or not _active(entity):
        return []
    found: list[dict[str, str]] = []
    subject = str(entity.get("subject") or "")
    if _contains_name(text, subject):
        found.append({"property": "subject", "span": subject})
    else:
        words = [word for word in _words(subject) if word not in _STOP]
        lowered = " ".join(_words(text))
        for first, second in zip(words, words[1:]):
            if f"{first} {second}" in lowered:
                found.append({"property": "subject_part", "span": f"{first} {second}"})
                break
    for alias in str(entity.get("aliases") or "").split(","):
        if alias.strip() and _contains_name(text, alias.strip()):
            found.append({"property": "alias", "span": alias.strip()})
    skill_values = value_tokens(text)
    for token in value_tokens(entity.get("description") or ""):
        if token in skill_values:
            found.append({"property": "value", "span": token})
    return [{"entity_id": str(entity["id"]), **item} for item in found]


def _instructions(skill: Any) -> str:
    return str(getattr(skill, "instructions", "") or "")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _entities() -> list[dict[str, Any]]:
    import row_bot.knowledge_graph as kg

    return kg.list_entities(limit=100_000)


def link(text: str, entities: list[dict[str, Any]] | None = None) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    for entity in entities if entities is not None else _entities():
        found.extend(references(entity, text))
    return found


def _store() -> dict[str, Any]:
    return stale_skills.read_json(STORE)


def skill_saved(skill: Any, *, created: bool) -> dict[str, Any] | None:
    """Link a user skill just created or patched (re-linked from scratch) and record its revision."""

    if skill is None or getattr(skill, "source", "user") != "user":
        return None
    text = _instructions(skill)
    store = _store()
    previous = store.get(skill.name) or {}
    sha = _sha(text)
    revised = not previous or previous.get("sha") != sha
    record = {"references": link(text), "sha": sha,
              "revised_at": stale_skills.now().isoformat(timespec="seconds") if revised
              else previous.get("revised_at"), "linked_at": stale_skills.now().isoformat(timespec="seconds"),
              "display_name": str(getattr(skill, "display_name", "") or skill.name)}
    store[skill.name] = record
    stale_skills.write_json(STORE, store)
    if revised:
        stale_skills.log("skill_create" if created else "patch" if previous else "link", skill=skill.name, sha=sha,
                         references=[item["entity_id"] for item in record["references"]])
    return record


def entity_changed(entity: dict[str, Any]) -> None:
    """Re-link one memory to every linked skill (a memory written after the skill can now be named by it)."""

    from row_bot import skills

    store = _store()
    if not store:
        return
    changed = False
    for name, record in store.items():
        skill = skills.get_skill(name)
        if skill is None:
            continue
        # Links are added here, never dropped: a skill made from a memory stays linked to it when the memory
        # changes (only a patch of the skill re-links it from scratch, and a deleted memory is unlinked).
        kept = list(record.get("references") or ())
        updated = kept + [item for item in references(entity, _instructions(skill)) if item not in kept]
        if updated != kept:
            record["references"] = updated
            changed = True
    if changed:
        stale_skills.write_json(STORE, store)


def entity_removed(entity_id: str) -> None:
    store = _store()
    changed = False
    for record in store.values():
        kept = [item for item in record.get("references") or () if item.get("entity_id") != entity_id]
        if kept != record.get("references"):
            record["references"] = kept
            changed = True
    if changed:
        stale_skills.write_json(STORE, store)


def skill_record(name: str) -> dict[str, Any] | None:
    """A user skill's links and last revision, linking it now if it was saved before the setting was on."""

    from row_bot import skills

    record = _store().get(name)
    skill = skills.get_skill(name)
    if record is not None or skill is None or getattr(skill, "source", "") != "user":
        return record
    record = skill_saved(skill, created=False)
    if record is not None and skill.path is not None:
        # Its revision is the file's: it was written before links were kept.
        try:
            stamp = datetime.fromtimestamp((skill.path / "SKILL.md").stat().st_mtime)
            record["revised_at"] = stamp.isoformat(timespec="seconds")
            store = _store()
            store[name] = record
            stale_skills.write_json(STORE, store)
        except OSError:
            pass
    return record


__all__ = ["STORE", "TOKEN_KINDS", "entity_changed", "entity_removed", "link", "references", "skill_record",
           "skill_saved", "value_tokens"]
