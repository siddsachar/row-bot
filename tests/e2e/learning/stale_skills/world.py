"""The stale-skills world timeline (Design A generator v2): one seeded person's 120 days, ground truth only (Lane B).

Six people, each with a **catalogue** (``catalogues/<person>.json``): S1's three (Mara, Idris, Wren; their 30-day
past, workspace and apps from ``tests/e2e/learning/seeded/people``) and three new ones made the same way (Tobi, Ana,
Rhys; ``people/<person>`` here, in S1's format, read by S1's generator). Fixed by the pre-registration
(``.local/plans/paper-ideas/PREREG-DESIGN-A.md`` §4) and the user's decisions of 26 Oct. From one seed, the same world
(same seed, same :meth:`Timeline.fingerprint`):

- **Recurring procedures** (sixteen a person): each is a task the person asks for on fixed weekdays whose right first
  action depends on one fact of one of six kinds (an address, a supplier, hours, an amount, who holds a role, a
  policy rule). **The eight procedures that change run twice a week; the eight that never change, once.**
- **Change events** by the pre-registration's rule (:func:`change_days`): 8 per person, on days drawn uniformly
  from 20-110 at least 7 days apart, each touching a different procedure; the changing procedures cover all six
  kinds (one of each, then two more). Which procedures change, on which day and to which value all come from the
  seed, blind to any arm.
- **How a change arrives**: seeded 50/50 per change, told in a chat of its own or mentioned in passing inside
  another request that day (told when no other procedure is asked that weekday); the world's sources change that
  morning either way (the contacts page, the procedure's policy document, a letter in the inbox).
- **Newer-skill events** (H-A3's wrong-switch runs; the user's decision, 26 Oct): two a person, where the person edits
  a skill directly to a new value and memory is never told. Two of the procedures that never change in the world
  (not a policy: its document states the rule) are drawn, on days drawn uniformly from 40-110 at least 7 days apart,
  each to a new value from its pool; from that morning the person's procedure uses the new value (the right
  outcome), the skill is the newer of the two and memory the older. No world source names those procedures' facts
  (the contacts page leaves them out), and the other six procedures that never change stay the controls.
- **Canonical entities with aliases**, **homonyms** (two people with one first name), **preference updates** and
  **misstated-then-corrected facts**.
- **Rendering**: each procedure's first run states the fact; later runs don't. Wording comes from templates, or from
  a phrasing model's kept variants (``phrasing.py``; two phrasing models supported).
- **Consent**: the simulated person accepts "save this as a skill?" on a procedure's 2nd or 3rd passed run (seeded).
- **Probes** on days 30, 60, 90 and 120 (current fact, as-of fact, alias and homonym, update detection,
  abstention), each with its expected answer from the timeline.

``build(..., smoke=True)`` is the two-day smoke world (phase 2): the person's first two changes on days 1 and 2, told
first thing each day; those procedures and two that never change run on both days without stating their facts; a
skill naming the old value is seeded for each of them the first morning, except one unchanged procedure whose skill
comes through consent on its first passed run; the person edits the other unchanged procedure's skill on the second
morning (a newer-skill event).

**Ground truth comes only from this timeline** (:meth:`Timeline.value`), never from the simulated person or the
product's verifier. Nothing here imports Row-Bot beyond the pack format.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from tests.e2e.learning.seeded import generator
from tests.e2e.learning.seeded.person import draw
from tests.e2e.learning.seeded.pilot_timeline import _matching

FORMAT = "row_bot.learning.stale_skills_timeline"
VERSION = 2
CATALOGUE_FORMAT = "row_bot.learning.stale_skills_catalogue"
HERE = Path(__file__).resolve().parent
CATALOGUES = HERE / "catalogues"
#: The three new people, in S1's format (S1's own three stay in ``seeded/people``).
PEOPLE_DIR = HERE / "people"
S1_PEOPLE = ("mara", "idris", "wren")
PEOPLE = ("mara", "idris", "wren", "tobi", "ana", "rhys")
PERSON = "mara"
SEED = 20261022
DAYS = 120
SMOKE_DAYS = 2
KINDS = ("address", "supplier", "hours", "amount", "role", "policy")
#: The pre-registration's change rule (§4).
CHANGES_PER_PERSON = 8
CHANGE_FIRST, CHANGE_LAST, CHANGE_GAP = 20, 110, 7
#: Newer-skill events (the person edits a skill; memory is never told).
EDITS_PER_PERSON = 2
EDIT_FIRST, EDIT_LAST = 40, 110
EDIT = "skill_edit"
PROBE_DAYS = (30, 60, 90, 120)
#: Probes per category on each probe day.
PROBES_PER_CATEGORY = 2
PROBE_CATEGORIES = ("current", "as_of", "alias_homonym", "update", "abstention")
WINDOW_DAYS = 14  # the primary metric's window: the change day and the 13 days after (§6)
#: Where a person's runs are numbered (``<person>-sa-d<day>-<procedure>``).
RUN_PREFIX = "sa"

Value = dict[str, Any]


# ── The catalogue ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Procedure:
    """One recurring procedure and its fact (see the module's docstring)."""

    id: str
    kind: str
    title: str  # what the person calls the task ("the bread order")
    fact: str  # the fact in words ("Sandgate Bakery's orders address")
    party: str  # who or what the fact belongs to, canonical ("Sandgate Bakery", "the treasurer")
    aliases: tuple[str, ...]  # other names the person uses for the party
    pool: tuple[Value, ...]  # the values the fact can take; pool[0] is the old one when fixed_old
    fixed_old: bool  # pool[0] is what S1's world already says (its past, its documents)
    intro: str  # the first run's words: states the fact ({value})
    short: tuple[str, ...]  # later runs' words: never state the fact
    variable: str  # "n", "date", "who_date" or "none"
    low: int = 0
    high: int = 0
    unit: str = ""  # how ``n`` is said ("large loaves")
    to: str = ""  # amount and policy: the fixed recipient
    event: str = ""  # hours: the event's title ({who})
    keyword: str = ""  # hours: what the event's title must contain
    doc: str = ""  # a document that states a policy (S1's, or one this world writes on day 1)
    doc_rule: str = ""  # the policy's sentence in that document ({n})
    letter_from: str = ""  # who writes to the inbox when it changes ({old_email}/{new_email})

    @property
    def action(self) -> str:
        return "event" if self.kind == "hours" else "mail"


@dataclass(frozen=True)
class Catalogue:
    """One person's world data: procedures, the declarative facts and the contacts page."""

    person: str
    first_name: str
    policy_by: str  # who changes a policy, as the person says it ("The trustees")
    seed: int
    runbook: str
    runbook_title: str
    runbook_intro: str
    whos: tuple[str, ...]
    procedures: tuple[Procedure, ...]
    preferences: tuple[dict[str, Any], ...]
    misstated: tuple[dict[str, Any], ...]
    homonyms: tuple[dict[str, Any], ...]
    unknown: tuple[dict[str, str], ...]

    def procedure(self, procedure_id: str) -> Procedure:
        return next(item for item in self.procedures if item.id == procedure_id)


def _procedure(data: Mapping[str, Any]) -> Procedure:
    fields = dict(data)
    for key in ("aliases", "pool", "short"):
        fields[key] = tuple(fields[key])
    fields.pop("doc_initial", None)
    return Procedure(**fields)


@lru_cache(maxsize=None)
def catalogue(person: str = PERSON) -> Catalogue:
    """A person's catalogue (``catalogues/<person>.json``)."""

    data = json.loads((CATALOGUES / f"{person}.json").read_text(encoding="utf-8"))
    if data.get("format") != CATALOGUE_FORMAT or data.get("person") != person:
        raise generator.GeneratorError(f"{person}: not a stale-skills catalogue")
    runbook = data["runbook"]
    return Catalogue(person=person, first_name=data["first_name"], policy_by=data["policy_by"], seed=int(data["seed"]),
                     runbook=runbook["path"],
                     runbook_title=runbook["title"], runbook_intro=runbook["intro"], whos=tuple(data["whos"]),
                     procedures=tuple(_procedure(item) for item in data["procedures"]),
                     preferences=tuple(data["preferences"]), misstated=tuple(data["misstated"]),
                     homonyms=tuple(data["homonyms"]), unknown=tuple(data["unknown"]))


@contextmanager
def people_dir(person: str) -> Iterator[None]:
    """S1's generator reading this person (a new person's folder is here, not in S1's)."""

    saved = generator.PEOPLE_DIR
    if person not in S1_PEOPLE:
        generator.PEOPLE_DIR = PEOPLE_DIR
    try:
        yield
    finally:
        generator.PEOPLE_DIR = saved


def use_people_dir(person: str) -> None:
    """For a run's whole process: S1's code reads this person from the right folder."""

    if person not in S1_PEOPLE:
        generator.PEOPLE_DIR = PEOPLE_DIR


# Mara's, for callers that name her catalogue directly.
PROCEDURES: tuple[Procedure, ...] = catalogue(PERSON).procedures
PROCEDURE = {item.id: item for item in PROCEDURES}
RUNBOOK = catalogue(PERSON).runbook

#: How a change is told (``{party}``, ``{fact}``, ``{old}``, ``{new}``, ``{new_name}``, ``{old_name}``,
#: ``{new_email}``): one template per kind; the phrasing model's variants keep the same fields.
TELL_TEMPLATES: dict[str, str] = {
    "address": "Heads up: {party} have a new address - from today use {new}. The old one ({old}) isn't checked any more.",
    "supplier": "We've switched {party}: from today it's {new_name}, not {old_name}. Orders go to {new_email}.",
    "hours": "From now on {fact} are {new} instead of {old}.",
    "amount": "Finance have changed {fact}: it's {new} from this week (it was {old}).",
    "role": "{new_name} has taken over as {party} from {old_name} - their email is {new_email}.",
    "policy": "{policy_by} changed {fact}: it's now {new} (it was {old}). The policy document is updated.",
}
IN_PASSING = "{prompt} Oh, and FYI: {tell}"


# ── Saying and recognising values ────────────────────────────────────────────

#: A rule's unit: how it is written after the number, and what a text may write instead.
UNITS: dict[str, tuple[str, str]] = {
    "hours": (" hours", r"(?:-|\s)?(?:hours?|hrs?\b|h\b)"),
    "°C": (" °C", r"\s?(?:°\s?c|º\s?c|degrees|deg\b|c\b)"),
    "shifts": (" shifts", r"\s(?:\w+\s)?shifts?"),
    "days": (" days", r"(?:-|\s)(?:\w+\s)?days?"),
    "minutes": (" minutes", r"(?:-|\s)?(?:minutes?|mins?\b)"),
    "%": ("%", r"\s?(?:%|percent\b|per\s?cent\b)"),
    "years": (" years", r"(?:-|\s)?(?:years?|yrs?\b)"),
    "reviewers": (" reviewers", r"\s(?:\w+\s)?(?:reviewers?|approvals?|approvers?)"),
    "patients": (" patients", r"\s(?:\w+\s)?patients?"),
    "children": (" children", r"\s(?:\w+\s)?(?:children|pupils|kids)"),
    "people": (" people", r"\s(?:\w+\s)?(?:people|guests|visitors|persons)"),
}
_ONES = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
         "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen")
_TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")


def number_words(number: int) -> str:
    """The number in words (below 100), else ""."""

    if number < 20:
        return _ONES[number]
    if number < 100:
        return _TENS[number // 10] + ("" if number % 10 == 0 else "-" + _ONES[number % 10])
    return ""


def say(kind: str, value: Value, *, with_name: bool = False) -> str:
    """A value in words, as the person or a document says it."""

    if kind in ("address", "supplier", "role"):
        return f"{value['name']} ({value['email']})" if with_name else str(value["email"])
    if kind == "hours":
        return f"{value['start']}-{value['end']}"
    if kind == "amount":
        return f"GBP {value['amount']}"
    return f"{value['number']}{UNITS[value['unit']][0]}"


_MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
_MONTH_AFTER = rf"\s?(?:st|nd|rd|th)?\s+(?:of\s+)?{_MONTHS}\b"
_MONTH_BEFORE = rf"\b{_MONTHS}\s+$"


def _hour_pattern(value: str) -> str:
    hour, minute = (int(part) for part in value.split(":"))
    twelve = hour % 12 or 12
    suffix = "pm" if hour >= 12 else "am"
    options = [rf"{hour:02d}[:.]{minute:02d}", rf"(?<!\d){hour}[:.]{minute:02d}"]
    options.append(rf"(?<!\d){twelve}\s?{suffix}\b" if minute == 0 else rf"(?<!\d){twelve}[:.]{minute:02d}\s?{suffix}\b")
    return "|".join(f"(?:{option})" for option in options)


def states(kind: str, value: Value, text: str) -> bool:
    """Whether ``text`` states ``value`` (the kind's own rule): the email address as written; the start time in any
    common form; the amount as a number; the rule's number with its unit (or the number in words)."""

    text = str(text or "")
    if kind in ("address", "supplier", "role"):
        return str(value["email"]).lower() in text.lower()
    if kind == "hours":
        return re.search(rf"(?<![\d:])(?:{_hour_pattern(value['start'])})(?![\d])", text, re.IGNORECASE) is not None
    if kind == "amount":
        # The amount as a number, but not a day of a date or a part of a time ("week of 2026-11-16", "16 Nov").
        plain = text.replace(",", "")
        for match in re.finditer(rf"(?<![\d.:/-]){value['amount']}(?:\.00)?(?![\d:/-])", plain):
            before, after = plain[:match.start()].lower(), plain[match.end():].lower()
            if re.match(_MONTH_AFTER, after) or re.search(_MONTH_BEFORE, before):
                continue
            return True
        return False
    number = int(value["number"])
    words = [str(number)] + ([number_words(number)] if number_words(number) else [])
    unit = UNITS[value["unit"]][1]
    return any(re.search(rf"(?<![\w.]){re.escape(word)}{unit}", text, re.IGNORECASE) for word in words)


# ── Records ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Change:
    """A change event: ``procedure``'s fact holds ``old`` before ``day`` and ``new`` from ``day`` on."""

    procedure: str
    kind: str
    day: int
    old: Value
    new: Value
    channel: str  # "told" (a chat of its own) or "in_passing" (in another request's message that day; drawn, and
    # "told" when no other procedure is asked that weekday); a newer-skill event's is "skill_edit"

    def to_dict(self) -> dict[str, Any]:
        return {"procedure": self.procedure, "kind": self.kind, "day": self.day, "old": self.old, "new": self.new,
                "channel": self.channel}


@dataclass(frozen=True)
class Tell:
    """The person telling Row-Bot something, in a chat of its own: one message, or a misstatement and its fix."""

    id: str
    day: int
    order: int
    about: str  # a procedure id, a preference, a misstated fact or a homonym
    what: str  # change | preference | misstated | homonym
    messages: tuple[str, ...]


@dataclass(frozen=True)
class Probe:
    """A declarative question on a probe day, with its expected answer from the timeline."""

    id: str
    day: int
    order: int
    category: str
    fact: str
    question: str
    expected: tuple[str, ...]  # what a right answer states (all of them)
    wrong: tuple[str, ...]  # values a right answer must not state instead (older, misstated, the other homonym)
    kind: str  # how values are recognised (a procedure kind, or "text")

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class Timeline:
    """One person's world over the live days (see the module's docstring)."""

    person: str
    seed: int
    days: int
    schedule: dict[str, tuple[int, ...]]
    changes: tuple[Change, ...]
    start: dict[str, Value]  # each procedure's value on day 1
    runs: tuple[generator.Request, ...]
    tells: tuple[Tell, ...]
    probes: tuple[Probe, ...]
    consent_run: dict[str, int]
    facts: dict[str, list[tuple[int, Any]]]  # every fact's values: [(from day, value), ...]
    phrasing: str
    history: generator.History = field(repr=False)
    catalogue: Catalogue = field(repr=False, default_factory=catalogue)
    smoke: bool = False
    seeded_skills: tuple[str, ...] = ()  # smoke: procedures whose skill is seeded the first morning
    arrivals: dict[str, int] = field(default_factory=dict)  # the day each change reached the conversation
    edits: tuple[Change, ...] = ()  # newer-skill events: the person edits the skill; memory is never told

    # ── Ground truth ──────────────────────────────────────────────────

    def procedure(self, procedure_id: str) -> Procedure:
        return self.catalogue.procedure(procedure_id)

    @property
    def procedures(self) -> tuple[Procedure, ...]:
        return self.catalogue.procedures

    def change_of(self, procedure: str) -> Change | None:
        return next((item for item in self.changes if item.procedure == procedure), None)

    def edit_of(self, procedure: str) -> Change | None:
        """The procedure's newer-skill event (the person edits its skill), or None."""

        return next((item for item in self.edits if item.procedure == procedure), None)

    def shift_of(self, procedure: str) -> Change | None:
        """When the procedure's value moves: its change in the world, or the person's edit of its skill."""

        return self.change_of(procedure) or self.edit_of(procedure)

    def value(self, procedure: str, day: int) -> Value:
        """The procedure's fact in effect on ``day`` (a change's or an edit's own day already has the new value)."""

        shift = self.shift_of(procedure)
        if shift is not None and day >= shift.day:
            return shift.new
        return self.start[procedure]

    def fact_value(self, fact: str, day: int) -> Any:
        """Any fact's value on ``day`` (procedures, preferences, misstated facts, homonyms): None before it is known."""

        found = None
        for since, value in self.facts[fact]:
            if day >= since:
                found = value
        return found

    def procedure_of(self, run_id: str) -> str:
        return next(item.template.removeprefix("stale_") for item in self.runs if item.id == run_id)

    def by_day(self) -> dict[int, list[Any]]:
        """Each day's items in the order they come: what the person tells, the runs, then the probes."""

        found: dict[int, list[Any]] = {}
        for item in sorted((*self.tells, *self.runs, *self.probes), key=lambda item: (item.day, item.order)):
            found.setdefault(item.day, []).append(item)
        return found

    # ── The world ─────────────────────────────────────────────────────

    def runbook(self, day: int) -> str:
        return runbook_text(self, day)

    def files_on(self, day: int) -> dict[str, str]:
        """Workspace files the world writes on the morning of ``day`` (day 1: the contacts page and this world's
        policy documents; a change's day: the contacts page and the procedure's own document)."""

        changed = [item for item in self.changes if item.day == day]
        if day != 1 and not changed:
            return {}
        files = {self.catalogue.runbook: self.runbook(day)}
        if day == 1:
            for path in sorted({item.doc for item in self.procedures if item.doc}):
                if path not in self.history.start_files:
                    files[path] = self.document(path, 1)
        for change in changed:
            procedure = self.procedure(change.procedure)
            if procedure.doc:
                files[procedure.doc] = self.document(procedure.doc, day)
        return files

    def document(self, path: str, day: int) -> str:
        """A policy document as it reads at the end of ``day`` (every change to it so far applied)."""

        text = self.history.start_files.get(path) or initial_document(self, path)
        for change in sorted(self.changes, key=lambda item: item.day):
            procedure = self.procedure(change.procedure)
            if procedure.doc == path and change.day <= day:
                text = rewrite_document(procedure, text, change, change.day)
        return text

    def letters(self, day: int) -> list[dict[str, Any]]:
        """The inbox's letters about the changes up to ``day``."""

        return [letter(self.catalogue, change) for change in sorted(self.changes, key=lambda item: item.day)
                if change.day <= day]

    def apps_on(self, day: int) -> dict[str, Any]:
        return _apps_for(self.history, self.catalogue, list(self.changes), day)

    # ── Record ────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {"format": FORMAT, "version": VERSION, "person": self.person, "seed": self.seed, "days": self.days,
                "phrasing": self.phrasing, "smoke": self.smoke, "seeded_skills": list(self.seeded_skills),
                "arrivals": self.arrivals, "edits": [item.to_dict() for item in self.edits],
                "schedule": {key: list(value) for key, value in self.schedule.items()},
                "changes": [item.to_dict() for item in self.changes], "start": self.start,
                "consent_run": self.consent_run, "tells": [item.__dict__ for item in self.tells],
                "runs": [item.to_dict() for item in self.runs], "probes": [item.to_dict() for item in self.probes],
                "facts": self.facts, "past": self.history.fingerprint()}

    def fingerprint(self) -> str:
        text = json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── The rule ─────────────────────────────────────────────────────────────────


def change_days(rng: random.Random, *, count: int = CHANGES_PER_PERSON, first: int = CHANGE_FIRST,
                last: int = CHANGE_LAST, gap: int = CHANGE_GAP) -> list[int]:
    """``count`` days drawn uniformly from ``first``-``last``, at least ``gap`` days apart (the pre-registration's
    rule: uniform over the allowed sets, by rejection)."""

    for _ in range(100_000):
        days = sorted(rng.randint(first, last) for _ in range(count))
        if all(later - earlier >= gap for earlier, later in zip(days, days[1:])):
            return days
    raise generator.GeneratorError("no change days fit the rule")


def changing_procedures(rng: random.Random, procedures: Iterable[Procedure] = PROCEDURES,
                        count: int = CHANGES_PER_PERSON) -> list[str]:
    """Which procedures change: one of each kind, then the rest drawn from those left (seeded)."""

    pool = list(procedures)
    chosen = [rng.choice([item for item in pool if item.kind == kind]).id for kind in KINDS]
    rest = sorted(item.id for item in pool if item.id not in chosen)
    chosen += rng.sample(rest, count - len(chosen))
    rng.shuffle(chosen)
    return chosen


# ── Rendering helpers ────────────────────────────────────────────────────────


def runbook_text(timeline: Timeline, day: int) -> str:
    when = generator.day_date(day)
    lines = [f"# {timeline.catalogue.runbook_title}", "", timeline.catalogue.runbook_intro,
             f"Last updated {when.day} {when.strftime('%B %Y')}.", ""]
    for procedure in timeline.procedures:
        if timeline.edit_of(procedure.id) is not None:
            continue  # a newer-skill event's fact: no world source names it
        value = timeline.value(procedure.id, day)
        lines.append(f"- **{procedure.fact[:1].upper() + procedure.fact[1:]}** ({procedure.title}): "
                     f"{say(procedure.kind, value, with_name=procedure.kind in ('supplier', 'role'))}")
    lines.append("")
    return "\n".join(lines)


def initial_document(timeline: Timeline, path: str) -> str:
    """A policy document this world writes on day 1 (one that isn't in the person's S1 workspace)."""

    title = Path(path).stem.replace("-", " ").capitalize()
    lines = [f"# {title}", "", f"{timeline.catalogue.runbook_intro.split(':')[0]}: the rules we work to.", ""]
    for procedure in timeline.procedures:
        if procedure.doc == path:
            lines.append(f"- {procedure.doc_rule.format(n=timeline.start[procedure.id]['number'])}.")
    lines.append("")
    return "\n".join(lines)


def rewrite_document(procedure: Procedure, text: str, change: Change, day: int) -> str:
    """The policy document with the rule changed (nothing else), as the world would rewrite it."""

    before = procedure.doc_rule.format(n=change.old["number"])
    after = procedure.doc_rule.format(n=change.new["number"])
    if before not in text:
        raise generator.GeneratorError(f"{procedure.doc} doesn't say {before!r}")
    when = generator.day_date(day)
    note = f"\n\nChanged on {when.day} {when.strftime('%B %Y')}: {procedure.fact} is now {say('policy', change.new)}.\n"
    return text.replace(before, after).rstrip("\n") + note


def edit_skill_text(procedure: Procedure, text: str, edit: Change) -> tuple[str, bool]:
    """The skill as the person edits it to the new value: every way it states the old value replaced (and, for a
    supplier or a role, the old name), or, when it states none, a dated line added. Returns the text and whether a
    value was replaced."""

    old, new = edit.old, edit.new
    if procedure.kind in ("address", "supplier", "role"):
        patterns = [(re.escape(str(old["email"])), str(new["email"]))]
        if old.get("name") and new.get("name") and old["name"] != new["name"]:
            patterns.append((re.escape(str(old["name"])), str(new["name"])))
    elif procedure.kind == "hours":
        patterns = [(rf"(?<![\d:])(?:{_hour_pattern(old[key])})(?![\d])", str(new[key])) for key in ("start", "end")]
    elif procedure.kind == "amount":
        patterns = [(rf"(?<![\d.:/-]){old['amount']}(?:\.00)?(?![\d:/-])", str(new["amount"]))]
    else:
        raise generator.GeneratorError(f"{procedure.id}: a policy's skill isn't edited (its document states the rule)")
    # One pass over all of them, so a value moved onto another's old value (09:00-10:00 to 10:00-11:00) moves once.
    either = "|".join(f"(?P<v{number}>{pattern})" for number, (pattern, _) in enumerate(patterns))
    edited = re.sub(either, lambda match: patterns[int(match.lastgroup[1:])][1], text, flags=re.IGNORECASE)
    if edited != text:
        return edited, True
    when = generator.day_date(edit.day)
    line = (f"Updated on {when.day} {when.strftime('%B %Y')}: {procedure.fact} is now "
            f"{say(procedure.kind, new, with_name=procedure.kind in ('supplier', 'role'))}.")
    return text.rstrip("\n") + "\n\n" + line + "\n", False


def letter(catalogue_: Catalogue, change: Change) -> dict[str, Any]:
    procedure = catalogue_.procedure(change.procedure)
    sender = procedure.letter_from.format(old_email=change.old.get("email", ""), new_email=change.new.get("email", ""))
    name = catalogue_.first_name
    if change.kind == "address":
        body = (f"Hello {name}, from today please use {say('address', change.new)} - {say('address', change.old)} "
                "won't be checked any more.")
    elif change.kind == "supplier":
        body = (f"Hello {name}, welcome aboard as a customer of {change.new['name']}. Please send your orders to "
                f"{change.new['email']}.")
    elif change.kind == "role":
        body = (f"Hi {name}, {change.new['name']} is taking over as {procedure.party} from {change.old['name']}. "
                f"Please send everything for {procedure.party} to {change.new['email']}.")
    else:
        body = (f"Hi {name}, a change from today: {procedure.fact} is now {say(change.kind, change.new)} "
                f"(it was {say(change.kind, change.old)}).")
    return {"id": f"msg_change_{change.procedure}", "folder": "inbox", "from": sender, "to": "me@example.test",
            "subject": f"Change: {procedure.fact}", "body": body, "date": generator.day_date(change.day).isoformat()}


def tell_text(change: Change, template: str | None = None, catalogue_: Catalogue | None = None) -> str:
    catalogue_ = catalogue_ or catalogue(PERSON)
    procedure = catalogue_.procedure(change.procedure)
    fields = {"policy_by": catalogue_.policy_by, "party": procedure.party, "fact": procedure.fact,
              "old": say(change.kind, change.old),
              "new": say(change.kind, change.new), "new_name": change.new.get("name", ""),
              "old_name": change.old.get("name", ""), "new_email": change.new.get("email", "")}
    return (template or TELL_TEMPLATES[change.kind]).format(**fields)


def _placeholders(text: str) -> set[str]:
    return set(re.findall(r"\{(\w+)\}", text))


class Phrasings:
    """Templates and a phrasing model's kept variants (``phrasing.py``): a slot's options are its template and every
    kept variant. ``None`` means templates only."""

    def __init__(self, data: Mapping[str, Any] | None = None) -> None:
        self.data = dict(data or {})
        self.label = str(self.data.get("model") or "templates")

    def options(self, slot: str, template: str) -> list[str]:
        kept = (self.data.get("slots") or {}).get(slot) or {}
        if kept.get("template") != template:
            return [template]  # the template changed since the variants were written: they don't apply
        variants = [item for item in kept.get("variants") or ()
                    if _placeholders(item) == _placeholders(template) and keeps_meaning(template, item)]
        return [template, *variants]


#: Words that turn a request into another one (a question, a draft, a check) when the template doesn't have them:
#: the first main run's phrasings had "Find out from finance how much ..." and "Draft a maintenance summary ...".
_OTHER_ACTION = re.compile(r"\b(draft|write up|put together|find out|check|what|how much|confirm|look up|update the|"
                           r"add to the|let \{alias\} know)\b", re.IGNORECASE)


def keeps_meaning(template: str, variant: str) -> bool:
    """Whether a phrasing keeps the request's action: no word that asks for something else, unless the template
    has it too."""

    lowered = template.lower()
    return not any(match.group(0).lower() not in lowered for match in _OTHER_ACTION.finditer(variant))


def slots(catalogue_: Catalogue | None = None) -> dict[str, str]:
    """Every phrasable message's template, by slot id (what ``phrasing.py`` asks a model to vary)."""

    found: dict[str, str] = {}
    for procedure in (catalogue_ or catalogue(PERSON)).procedures:
        for number, text in enumerate(procedure.short):
            found[f"short/{procedure.id}/{number}"] = text
    for kind, text in TELL_TEMPLATES.items():
        found[f"tell/{kind}"] = text
    return found


# ── Building the timeline ────────────────────────────────────────────────────

_WEEKDAY_PAIRS = ((0, 3), (1, 4), (0, 2), (2, 4), (1, 3))


def _date_after(day: int, rng: random.Random, *, weekday_only: bool) -> date:
    today = generator.day_date(day)
    options = [today + timedelta(days=step) for step in range(1, 8)
               if not weekday_only or (today + timedelta(days=step)).weekday() < 5]
    return rng.choice(options)


def _monday(day: int) -> date:
    today = generator.day_date(day)
    return today - timedelta(days=today.weekday())


def _run(person: Catalogue, procedure: Procedure, day: int, order: int, rng: random.Random, apps: dict[str, Any],
         value: Value, old: Value | None, first: bool, phrasings: Phrasings, words: random.Random,
         extra: str = "") -> generator.Request:
    from row_bot.learning.packs import PackTask, Solution, Verifier, validate

    fields: dict[str, Any] = {"alias": rng.choice((procedure.party, *procedure.aliases)), "to": procedure.to}
    if procedure.variable == "n":
        fields["n"] = rng.randint(procedure.low, procedure.high)
    when = None
    if procedure.variable in ("date", "who_date"):
        when = (_monday(day) if procedure.kind == "amount"
                else _date_after(day, rng, weekday_only=procedure.id != "deep_clean"))
        fields["date"] = when.isoformat()
    if procedure.variable == "who_date":
        fields["who"] = rng.choice(person.whos)
    if first:
        fields.update(value=say(procedure.kind, value), name=value.get("name", ""))
        prompt = procedure.intro.format(**fields)
    else:
        # The words have their own draws, so a phrasing file changes the wording and nothing else.
        slot = words.randrange(len(procedure.short))
        options = phrasings.options(f"short/{procedure.id}/{slot}", procedure.short[slot])
        prompt = words.choice(options).format(**fields)
    if extra:
        prompt = IN_PASSING.format(prompt=prompt, tell=extra)
    run_id = f"{person.person}-{RUN_PREFIX}-d{day:03d}-{procedure.id}"
    if procedure.action == "event":
        title = procedure.event.format(who=fields.get("who", ""))
        call = {"tool": "calendar_create_event",
                "args": {"title": title, "date": when.isoformat(), "start": value["start"], "end": value["end"]}}
        where = {"title": {"contains": procedure.keyword}, "date": when.isoformat(), "start": value["start"]}
        checks = [{"in": "calendar.events", "where": where, "count": _matching(apps, "calendar.events", where) + 1}]
        wrong = (dict(call["args"], start=old["start"], end=old["end"]) if old
                 else dict(call["args"], date=(when + timedelta(days=7)).isoformat()))
        intent = (f"One calendar event '{title}' on {when.isoformat()} from {value['start']} to {value['end']} "
                  f"({procedure.fact} on {generator.day_date(day).isoformat()}).")
        tools = ("calendar",)
    else:
        to = value["email"] if procedure.kind in ("address", "supplier", "role") else procedure.to
        must = str(fields["n"]) if procedure.variable == "n" else str(value.get("amount", value.get("number")))
        subject = procedure.title[4:].capitalize() if procedure.title.startswith("the ") else procedure.title
        body = {"amount": f"Hello, please send this week's {procedure.fact.removeprefix('the ')}: "
                          f"{say(procedure.kind, value)}.",
                "policy": f"Hello all, a reminder: {procedure.fact.removeprefix('the ')} is {say(procedure.kind, value)}."}
        text = body.get(procedure.kind) or f"Hello, {fields.get('n')} {procedure.unit}. Thanks, {person.first_name}"
        call = {"tool": "mail_send", "args": {"to": to, "subject": subject, "body": text}}
        where = {"folder": "sent", "to": to, "body": {"contains": must}}
        # S1's past left sent emails in the apps (its reminders, say): one more than those.
        checks = [{"in": "mail.messages", "where": where, "count": _matching(apps, "mail.messages", where) + 1}]
        if old and procedure.kind in ("address", "supplier", "role"):
            wrong = dict(call["args"], to=old["email"])
        elif old:
            wrong = dict(call["args"], body=text.replace(say(procedure.kind, value), say(procedure.kind, old)))
        else:
            wrong = dict(call["args"], to="someone@example.test")
        intent = (f"One email to {to} for {procedure.title}"
                  + (f" ({fields['n']} {procedure.unit})" if procedure.variable == "n" else "")
                  + (f", stating {procedure.fact}: {say(procedure.kind, value)}" if procedure.kind in ("amount", "policy")
                     else f" - {procedure.fact} on {generator.day_date(day).isoformat()} is "
                          f"{say(procedure.kind, value, with_name=True)}") + ".")
        tools = ("mail",)
    task = PackTask(id=run_id, pack="tool_workflows", split="visible", prompt=prompt,
                    verifier=Verifier("app_state", checks), reference=Solution(answer="Done.", calls=(call,)),
                    decoy=Solution(answer="Done.", calls=({"tool": call["tool"], "args": wrong},)),
                    files={}, apps=apps, tools=tools, max_steps=10)
    validate(task)
    return generator.Request(id=run_id, person=person.person, day=day, order=order, template=f"stale_{procedure.id}",
                             intended="tool_workflows", prompt=prompt, intent=intent, task=task, inputs={})


def _history(person: str, seed: int) -> generator.History:
    with people_dir(person):
        return generator.generate(person, seed=seed, days=0)


def build(*, person: str = PERSON, seed: int | None = None, days: int = DAYS, phrasing: Mapping[str, Any] | None = None,
          smoke: bool = False) -> Timeline:
    """``person``'s timeline for ``seed`` (the same seed and phrasing data, the same timeline); ``smoke``: the
    two-day smoke world (see the module's docstring)."""

    cat = catalogue(person)
    seed = cat.seed if seed is None else int(seed)
    phrasings = Phrasings(phrasing)
    history = _history(person, seed)
    procedures = cat.procedures

    def rng(name: str) -> random.Random:
        return random.Random(f"{int(seed)}/{person}/stale/{name}")

    # Change events by the rule; the changing procedures run twice a week, the others once (fixed weekdays, Monday 0).
    days_drawn = change_days(rng("change_days"))
    which = changing_procedures(rng("changing"), procedures)
    plan = rng("schedule")
    schedule = {item.id: (tuple(plan.choice(_WEEKDAY_PAIRS)) if item.id in which else (plan.randrange(5),))
                for item in procedures}
    values = rng("values")
    start: dict[str, Value] = {}
    changes: list[Change] = []
    channel_rng = rng("channels")
    by_procedure = dict(zip(which, days_drawn))
    for procedure in procedures:
        old = procedure.pool[0] if procedure.fixed_old else values.choice(procedure.pool)
        start[procedure.id] = old
        if procedure.id in by_procedure:
            new = values.choice([item for item in procedure.pool if item != old])
            day = by_procedure[procedure.id]
            drawn = "in_passing" if channel_rng.random() < 0.5 else "told"
            changes.append(Change(procedure.id, procedure.kind, day, old, new, drawn))
    changes.sort(key=lambda item: item.day)
    consent = {item.id: 2 + int(draw(seed, person, item.id, "stale_consent") < 0.5) for item in procedures}
    seeded: tuple[str, ...] = ()
    edits: list[Change] = []
    if smoke:
        # The first two changes move to days 1 and 2 (told first thing); they and two unchanged procedures run on
        # both days; the others don't run.
        days = SMOKE_DAYS
        changes = [Change(item.procedure, item.kind, day, item.old, item.new, "told")
                   for day, item in zip((1, 2), changes[:2])]
        unchanged = sorted(item.id for item in procedures if item.id not in which)
        edited = next(item for item in unchanged if cat.procedure(item).kind != "policy")
        running = [item.procedure for item in changes] + [edited, next(item for item in unchanged if item != edited)]
        everyday = tuple(sorted({generator.day_date(day).weekday() for day in range(1, SMOKE_DAYS + 1)}))
        schedule = {item.id: everyday if item.id in running else () for item in procedures}
        seeded = tuple(running[:-1])
        consent = {item.id: (1 if item.id == running[-1] else 99) for item in procedures}
        # The person edits the seeded skill of the first unchanged procedure on the second morning.
        procedure = cat.procedure(edited)
        new = rng("edits").choice([item for item in procedure.pool if item != start[edited]])
        edits = [Change(edited, procedure.kind, 2, start[edited], new, EDIT)]
    else:
        # Newer-skill events: their own draws, so the changes above stay as they were.
        edit_rng = rng("edits")
        editable = sorted(item.id for item in procedures if item.id not in which and item.kind != "policy")
        chosen = edit_rng.sample(editable, EDITS_PER_PERSON)
        days_edited = change_days(edit_rng, count=EDITS_PER_PERSON, first=EDIT_FIRST, last=EDIT_LAST, gap=CHANGE_GAP)
        for procedure_id, day in zip(chosen, days_edited):
            procedure = cat.procedure(procedure_id)
            new = edit_rng.choice([item for item in procedure.pool if item != start[procedure_id]])
            edits.append(Change(procedure_id, procedure.kind, day, start[procedure_id], new, EDIT))
    change_by = {item.procedure: item for item in changes}
    edit_by = {item.procedure: item for item in edits}

    # Declarative events: preferences (stated early, changed once), misstated facts, the homonyms.
    events = rng("events")
    tells: list[Tell] = []
    facts: dict[str, list[tuple[int, Any]]] = {}
    for procedure in procedures:
        shift = change_by.get(procedure.id) or edit_by.get(procedure.id)
        facts[procedure.id] = [(1, start[procedure.id])] + ([(shift.day, shift.new)] if shift else [])
    day_items: dict[int, list[tuple[str, Any]]] = {}
    for homonym in cat.homonyms:
        day = events.randint(2, 6)
        facts[homonym["id"]] = [(day, homonym["email"])]
        day_items.setdefault(day, []).append(("homonym", homonym))
    for preference in cat.preferences:
        first, later = events.randint(2, 12), events.randint(30, 105)
        facts[preference["id"]] = [(first, preference["values"][0]), (later, preference["values"][1])]
        day_items.setdefault(first, []).append(("preference", (preference, 0)))
        day_items.setdefault(later, []).append(("preference", (preference, 1)))
    for item in cat.misstated:
        day = events.randint(15, 100)
        wrong, right = events.choice(item["pairs"])
        facts[item["id"]] = [(day, right)]
        day_items.setdefault(day, []).append(("misstated", (item, wrong, right)))
    if smoke:
        day_items = {}

    request_rng = rng("requests")
    tell_rng = rng("tell_words")
    words_rng = rng("request_words")
    runs: list[generator.Request] = []
    ran: set[str] = set()
    said: dict[str, str] = {}
    waiting: list[Change] = []
    arrivals: dict[str, int] = {}
    for day in range(1, days + 1):
        order = 0
        weekday = generator.day_date(day).weekday()
        todays = [item for item in procedures if weekday in schedule[item.id]]
        for what, payload in day_items.get(day, []):
            if what == "homonym":
                tells.append(Tell(f"{person}-{RUN_PREFIX}-d{day:03d}-tell-{payload['id']}", day, order,
                                  payload["id"], what, (payload["say"],)))
            elif what == "preference":
                preference, index = payload
                value = preference["values"][index]
                text = (preference["say_first"] if index == 0 else preference["say_change"]).format(value=value)
                tells.append(Tell(f"{person}-{RUN_PREFIX}-d{day:03d}-tell-{preference['id']}-{index}", day, order,
                                  preference["id"], what, (text,)))
            else:
                item, wrong, right = payload
                tells.append(Tell(f"{person}-{RUN_PREFIX}-d{day:03d}-tell-{item['id']}", day, order, item["id"], what,
                                  (item["say"].format(wrong=wrong), item["fix"].format(wrong=wrong, right=right))))
            order += 1
        passing: dict[str, str] = {}  # host procedure -> what it carries
        for change in [item for item in changes if item.day == day]:
            said[change.procedure] = tell_text(change, tell_rng.choice(phrasings.options(
                f"tell/{change.kind}", TELL_TEMPLATES[change.kind])), cat)
            waiting.append(change)
        for change in list(waiting):
            hosts = [item.id for item in todays if item.id != change.procedure and item.id not in passing]
            if change.channel == "in_passing" and hosts:
                # Mentioned in the first request of the day; a change on a day with no other request (a weekend)
                # is mentioned with the next one: no run of its procedure comes between.
                passing[hosts[0]] = said[change.procedure]
            elif change.channel == "in_passing" and day < days:
                continue
            else:
                tells.append(Tell(f"{person}-{RUN_PREFIX}-d{day:03d}-tell-{change.procedure}", day, order,
                                  change.procedure, "change", (said[change.procedure],)))
                order += 1
            arrivals[change.procedure] = day
            waiting.remove(change)
        apps = None
        hosts_first = sorted(todays, key=lambda item: (item.id not in passing, procedures.index(item)))
        for procedure in hosts_first:
            apps = apps if apps is not None else _apps_for(history, cat, changes, day)
            change = change_by.get(procedure.id) or edit_by.get(procedure.id)
            after = change is not None and day >= change.day
            value = change.new if after else start[procedure.id]
            first = procedure.id not in ran and not smoke  # the smoke never states a fact in a request
            runs.append(_run(cat, procedure, day, order, request_rng, apps, value, change.old if after else None,
                             first, phrasings, words_rng, passing.get(procedure.id, "")))
            ran.add(procedure.id)
            order += 1
    timeline = Timeline(person=person, seed=int(seed), days=days, schedule=schedule, changes=tuple(changes),
                        start=start, runs=tuple(runs), tells=tuple(tells), probes=(), consent_run=consent,
                        facts=facts, phrasing=phrasings.label, history=history, catalogue=cat, smoke=smoke,
                        seeded_skills=seeded, arrivals=arrivals, edits=tuple(edits))
    probes = () if smoke else tuple(probe for day in PROBE_DAYS if day <= days
                                    for probe in make_probes(timeline, day, rng(f"probes/{day}"), order=500))
    return Timeline(**{**timeline.__dict__, "probes": probes})


def _apps_for(history: generator.History, cat: Catalogue, changes: list[Change], day: int) -> dict[str, Any]:
    apps = json.loads(json.dumps(history.start_apps))
    apps.setdefault("mail", {}).setdefault("messages", [])
    apps.setdefault("calendar", {}).setdefault("events", [])
    apps["mail"]["messages"].extend(letter(cat, item) for item in changes if item.day <= day)
    return apps


# ── Probes ───────────────────────────────────────────────────────────────────


def _when(day: int) -> str:
    when = generator.day_date(day)
    return f"{when.day} {when.strftime('%B %Y')}"


def _values(procedure: Procedure, timeline: Timeline) -> list[Value]:
    return [value for _, value in timeline.facts[procedure.id]]


def make_probes(timeline: Timeline, day: int, rng: random.Random, *, order: int = 500) -> list[Probe]:
    """Day ``day``'s probes: two per category, each with its expected answer from the timeline."""

    cat = timeline.catalogue
    changed = [item for item in timeline.changes if item.day <= day]
    # Update detection asks about the changes of the last 30 days first, then older ones.
    recent = sorted(changed, key=lambda item: (item.day <= day - 30, -item.day))
    probes: list[Probe] = []

    def add(category: str, fact: str, question: str, expected: Iterable[str], wrong: Iterable[str], kind: str) -> None:
        probes.append(Probe(f"{timeline.person}-{RUN_PREFIX}-d{day:03d}-probe-{category}-{len(probes)}", day,
                            order + len(probes), category, fact, question, tuple(expected), tuple(wrong), kind))

    def procedure_json(value: Value) -> str:
        return json.dumps(value, sort_keys=True)

    # Current fact: changed procedures first, then the declarative facts known by then, then unchanged ones.
    candidates: list[tuple[str, str]] = [("procedure", item.procedure) for item in changed]
    candidates += [("declarative", item["id"]) for item in (*cat.preferences, *cat.misstated)
                   if timeline.fact_value(item["id"], day) is not None]
    rng.shuffle(candidates)
    unchanged = [item.id for item in cat.procedures if timeline.shift_of(item.id) is None]
    candidates += [("procedure", rng.choice(unchanged))]
    for source, fact in candidates[:PROBES_PER_CATEGORY]:
        if source == "procedure":
            procedure = cat.procedure(fact)
            now = timeline.value(fact, day)
            others = [procedure_json(value) for value in _values(procedure, timeline) if value != now]
            add("current", fact, f"What's {procedure.fact} now?", [procedure_json(now)], others, procedure.kind)
        else:
            spec = next(item for item in (*cat.preferences, *cat.misstated) if item["id"] == fact)
            now = timeline.fact_value(fact, day)
            if fact in {item["id"] for item in cat.misstated}:
                wrong = [pair[0] for pair in spec["pairs"] if pair[1] == now]
                add("current", fact, f"Remind me: what's {spec['fact']}?", [str(now)], wrong, "date")
            else:
                marker = dict(zip(spec["values"], spec["markers"]))
                add("current", fact, f"Remind me: what's {spec['fact']}?", [marker[now]],
                    [marker[value] for value in spec["values"] if value != now], "marker")
    # As-of fact: a changed procedure's value on a day before its change.
    for change in (rng.sample(changed, min(PROBES_PER_CATEGORY, len(changed))) if changed else []):
        procedure = cat.procedure(change.procedure)
        before = max(1, change.day - rng.randint(2, 7))
        add("as_of", change.procedure, f"What was {procedure.fact} on {_when(before)}?",
            [procedure_json(change.old)], [procedure_json(change.new)], procedure.kind)
    # Alias and homonym: a party by another name; one of the homonyms by name.
    procedure = cat.procedure(rng.choice([item.id for item in cat.procedures
                                          if item.kind in ("address", "supplier", "role")
                                          and timeline.edit_of(item.id) is None]))
    alias = rng.choice(procedure.aliases)
    now = timeline.value(procedure.id, day)
    add("alias_homonym", procedure.id, f"What's the email address for {alias}?", [procedure_json(now)],
        [procedure_json(value) for value in _values(procedure, timeline) if value != now], procedure.kind)
    named = rng.choice(cat.homonyms)
    other = next(item for item in cat.homonyms if item is not named)
    first_name = named["name"].split()[0]
    add("alias_homonym", named["id"], f"What's the email address of the {first_name} who is {named['role']}?",
        [named["email"]], [other["email"]], "email")
    # Update detection: a recent change, both values.
    for change in recent[:PROBES_PER_CATEGORY]:
        procedure = cat.procedure(change.procedure)
        add("update", change.procedure, f"Has {procedure.fact} changed lately? If it has, what was it before and what "
            "is it now?", [procedure_json(change.new), procedure_json(change.old)], [], procedure.kind)
    # Abstention: facts never stated.
    for item in rng.sample(list(cat.unknown), PROBES_PER_CATEGORY):
        add("abstention", item["id"], item["question"], [], [], "abstain")
    return probes


__all__ = ["EDIT", "EDITS_PER_PERSON", "Catalogue", "Change", "DAYS", "KINDS", "PEOPLE", "PERSON", "PROBE_DAYS", "PROCEDURE", "PROCEDURES",
           "Phrasings", "Probe", "Procedure", "SEED", "SMOKE_DAYS", "Tell", "Timeline", "UNITS", "WINDOW_DAYS", "build",
           "catalogue", "change_days", "changing_procedures", "edit_skill_text", "letter", "people_dir", "say", "slots", "states",
           "tell_text", "use_people_dir"]
