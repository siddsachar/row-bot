"""The stale-skills pilot's world timeline: one seeded person, 30 days, two scheduled fact changes (Lane B).

Fixed before any run (``.local/plans/paper-ideas/MEMORY-SKILLS.md`` §4 "Pilot"): one seeded person (Mara,
S1's charity-kitchen coordinator) over 30 simulated days on S1's harness, with two **recurring tasks**, each
of which gets a skill that names one fact, and two **fact changes** that touch them:

- **bread order** (every weekday): an email with tomorrow's order to Sandgate Bakery. The fact is Sandgate's
  orders address: ``hello@sandgate-bakery.example`` (what the past's emails used), and from **day 10**
  ``orders@sandgate-bakery.example``.
- **weekend shift** (Monday, Wednesday, Friday): a volunteer put on the weekend shift in the kitchen
  calendar. The fact is the weekend shift's hours: 10:00-15:00 (the rota policy, version 3), and from
  **day 19** 09:00-14:00 (version 4).

Each change reaches the world the way it would: on its morning the person tells Row-Bot in a chat of its own
(an announcement, not a task), and the world itself changes (Sandgate's email in the inbox from that day; the
rota policy document rewritten as version 4). Before the change the old value is the right one; from the
change day on, the new one. **The ground truth is this timeline, never the simulated person or the
verifier**: :mod:`pilot_scorer` reads values from :meth:`Timeline.value`.

The past (S1's 30-day past for Mara, seed 20261022) stays as S1 generated it: it is the history Row-Bot
already holds (its emails to Sandgate went to the old address), so memory starts with the old fact. The live
days hold only the two recurring tasks and the two announcements: the cap (3M ChatGPT-lane tokens) leaves no
room for S1's other requests, and they touch neither fact.

The skill: each task's skill is made through the simulated person's consent on that task's ``consent_run``-th
passed run (2 or 3, seeded), or seeded on that day if the product can't make one that names the fact
(:data:`SEEDED_SKILLS`). Nothing here imports Row-Bot beyond the pack format.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from tests.e2e.learning.seeded import generator
from tests.e2e.learning.seeded.person import draw

PERSON = "mara"
SEED = 20261022
DAYS = 30
FORMAT = "row_bot.learning.pilot_stale_skills_timeline"
VERSION = 1

SANDGATE_OLD = "hello@sandgate-bakery.example"
SANDGATE_NEW = "orders@sandgate-bakery.example"
WEEKEND_OLD = {"start": "10:00", "end": "15:00"}
WEEKEND_NEW = {"start": "09:00", "end": "14:00"}
ROTA_POLICY = "policies/volunteer-rota-policy-2026.md"


@dataclass(frozen=True)
class FactChange:
    """One scheduled change: ``fact`` holds ``old`` before ``day`` and ``new`` from ``day`` on."""

    fact: str
    task: str
    day: int
    old: Any
    new: Any
    announcement: str

    def to_dict(self) -> dict[str, Any]:
        return {"fact": self.fact, "task": self.task, "day": self.day, "old": self.old, "new": self.new,
                "announcement": self.announcement}


CHANGES: tuple[FactChange, ...] = (
    FactChange(
        fact="sandgate_orders_email", task="bread_order", day=10, old=SANDGATE_OLD, new=SANDGATE_NEW,
        announcement=("Heads up: Sandgate have changed their address for orders. From today bread orders go to "
                      f"{SANDGATE_NEW} - the old {SANDGATE_OLD.split('@')[0]}@ one isn't checked any more."),
    ),
    FactChange(
        fact="weekend_shift_hours", task="weekend_shift", day=19, old=WEEKEND_OLD, new=WEEKEND_NEW,
        announcement=("FYI the trustees changed the weekend shift last night: from now on it runs 09:00 to 14:00 "
                      "instead of 10 to 3. The rota policy is version 4 now."),
    ),
)

#: Which weekdays (Monday 0) each recurring task is asked on.
SCHEDULE: dict[str, tuple[int, ...]] = {"bread_order": (0, 1, 2, 3, 4), "weekend_shift": (0, 2, 4)}
#: Each task's fact.
TASK_FACT = {change.task: change.fact for change in CHANGES}

#: The skill seeded when the product can't make one that names the fact (``{value}`` parts filled with the
#: value in effect on the day it is seeded).
SEEDED_SKILLS: dict[str, dict[str, str]] = {
    "bread_order": {
        "name": "sandgate_bread_order",
        "display_name": "Sandgate bread order",
        "description": "Send tomorrow's bread order to Sandgate Bakery by email.",
        "instructions": (
            "Use this when Mara asks to send the bread order to Sandgate Bakery.\n\n"
            "## Steps\n"
            "1. Take the number of large loaves from Mara's message.\n"
            "2. Send one email with `mail_send` to Sandgate Bakery's orders address, **{email}**.\n"
            "3. Subject: `Bread order for <tomorrow's date>`. Body: the number of large loaves, for delivery "
            "tomorrow, signed Mara, Lantern Lane Community Kitchen.\n"
            "4. Tell Mara it was sent and to which address.\n"
        ),
    },
    "weekend_shift": {
        "name": "weekend_shift_booking",
        "display_name": "Weekend shift booking",
        "description": "Put a volunteer on the weekend shift in the kitchen calendar.",
        "instructions": (
            "Use this when Mara asks to put a volunteer on the weekend shift.\n\n"
            "## Steps\n"
            "1. Take the volunteer's name and the date from Mara's message.\n"
            "2. Create one event with `calendar_create_event`: title `<name> - weekend shift`, the date, "
            "**start {start}, end {end}** (the weekend shift's hours).\n"
            "3. Tell Mara it's in the calendar.\n"
        ),
    },
}

_BREAD_PHRASINGS = (
    "Send Sandgate tomorrow's bread order: {n} large loaves.",
    "Can you do the Sandgate bread order for tomorrow? {n} large loaves please.",
    "Bread order to Sandgate for tomorrow - {n} large loaves.",
)
_SHIFT_PHRASINGS = (
    "Put {who} on the weekend shift on {date}.",
    "Can you add {who} to the weekend shift on {date} in the kitchen calendar?",
    "{who} is doing the weekend shift on {date} - put it in the calendar please.",
)
_WHOS = ("Ayo", "Bella", "Chidi", "Dev", "Elif", "Finn", "Grace", "Hamza")
_ROTA_V4_NOTE = "Approved by the trustees on {date}. This is the current policy and replaces version 3."


@dataclass(frozen=True)
class Tell:
    """An announcement: the person tells Row-Bot about a change, in a chat of its own, on the change's morning."""

    id: str
    day: int
    order: int
    fact: str
    prompt: str


@dataclass(frozen=True)
class Timeline:
    """The pilot's world: the changes, the recurring requests, the announcements and the world's own changes."""

    seed: int
    days: int
    changes: tuple[FactChange, ...]
    requests: tuple[generator.Request, ...]
    tells: tuple[Tell, ...]
    consent_run: dict[str, int]
    history: generator.History = field(repr=False)

    # ── Ground truth ──────────────────────────────────────────────────

    def change(self, fact: str) -> FactChange:
        return next(item for item in self.changes if item.fact == fact)

    def value(self, fact: str, day: int) -> Any:
        """The fact's value in effect on ``day`` (the change's own day already has the new one)."""

        change = self.change(fact)
        return change.new if day >= change.day else change.old

    def after_change(self, fact: str, day: int) -> bool:
        return day >= self.change(fact).day

    # ── The world ─────────────────────────────────────────────────────

    def files_changed_on(self, day: int) -> dict[str, str]:
        """Workspace files the world rewrites on the morning of ``day``."""

        change = self.change("weekend_shift_hours")
        return {ROTA_POLICY: rota_policy_v4(self.history.start_files[ROTA_POLICY], change.day)} if day == change.day else {}

    def by_day(self) -> dict[int, list[Any]]:
        """Each day's items in the order they are asked: announcements, then the recurring tasks."""

        found: dict[int, list[Any]] = {}
        for item in sorted((*self.tells, *self.requests), key=lambda item: (item.day, item.order)):
            found.setdefault(item.day, []).append(item)
        return found

    def to_dict(self) -> dict[str, Any]:
        return {"format": FORMAT, "version": VERSION, "person": PERSON, "seed": self.seed, "days": self.days,
                "changes": [item.to_dict() for item in self.changes], "consent_run": self.consent_run,
                "tells": [item.__dict__ for item in self.tells],
                "requests": [item.to_dict() for item in self.requests],
                "past": self.history.fingerprint()}

    def fingerprint(self) -> str:
        text = json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()


def rota_policy_v4(text: str, day: int) -> str:
    """The rota policy as version 4: the weekend hours changed, nothing else."""

    when = generator.day_date(day)
    stamp = f"{when.day} {when.strftime('%B %Y')}"
    old = f"Saturday or Sunday ({WEEKEND_OLD['start']}-{WEEKEND_OLD['end']})"
    new = f"Saturday or Sunday ({WEEKEND_NEW['start']}-{WEEKEND_NEW['end']})"
    if old not in text or "(version 3)" not in text:
        raise generator.GeneratorError("the rota policy isn't version 3 as S1 wrote it")
    text = text.replace("(version 3)", "(version 4)").replace(old, new)
    return re.sub(r"Approved by the trustees on [^.]+\. This is the current policy and\s+replaces version 2\.",
                  _ROTA_V4_NOTE.format(date=stamp), text)


def sandgate_letter(day: int) -> dict[str, Any]:
    """Sandgate's email about the new address, in the inbox from the change's morning."""

    when = generator.day_date(day).isoformat()
    return {"id": "msg_sandgate_address", "folder": "inbox", "from": SANDGATE_OLD, "to": "me@example.test",
            "subject": "New address for orders",
            "body": (f"Hello Mara, from today please send all bread orders to {SANDGATE_NEW}. This address "
                     "won't be checked for orders any more. Thanks, Sandgate Bakery"),
            "date": when}


def apps_on(start_apps: dict[str, Any], day: int) -> dict[str, Any]:
    """The fake apps as a request on ``day`` finds them (Sandgate's letter in the inbox from its day)."""

    apps = json.loads(json.dumps(start_apps))
    if day >= next(item.day for item in CHANGES if item.fact == "sandgate_orders_email"):
        apps["mail"]["messages"].append(sandgate_letter(next(item.day for item in CHANGES
                                                            if item.fact == "sandgate_orders_email")))
    return apps


def _matching(apps: dict[str, Any], collection: str, where: dict[str, Any]) -> int:
    from row_bot.learning.packs.verify import _matches  # noqa: PLC2701 - the verifier's own rule

    app, _, name = collection.partition(".")
    items = list(((apps.get(app) or {}).get(name)) or [])
    return sum(1 for item in items if all(_matches(item.get(key), wanted) for key, wanted in where.items()))


def _bread_request(day: int, order: int, rng: random.Random, apps: dict[str, Any], email: str,
                   stale: str | None) -> generator.Request:
    from row_bot.learning.packs import PackTask, Solution, Verifier, validate

    loaves = rng.randint(14, 40)
    prompt = rng.choice(_BREAD_PHRASINGS).format(n=loaves)
    tomorrow = generator.day_date(day) + timedelta(days=1)
    must = f"{loaves}"
    where = {"folder": "sent", "to": email, "body": {"contains": must}}
    checks = [{"in": "mail.messages", "where": where, "count": _matching(apps, "mail.messages", where) + 1}]
    call = {"tool": "mail_send", "args": {"to": email, "subject": f"Bread order for {tomorrow.isoformat()}",
                                          "body": f"Hello,\n\nPlease deliver {loaves} large loaves tomorrow.\n\nMara"}}
    wrong = dict(call["args"], to=stale) if stale else dict(call["args"], body=call["args"]["body"].replace(
        f"{loaves} large", f"{loaves + 1} large"))
    request_id = f"{PERSON}-pilot-d{day:02d}-bread"
    task = PackTask(id=request_id, pack="tool_workflows", split="visible", prompt=prompt,
                    verifier=Verifier("app_state", checks), reference=Solution(answer="Sent.", calls=(call,)),
                    decoy=Solution(answer="Sent.", calls=({"tool": "mail_send", "args": wrong},)),
                    files={}, apps=apps, tools=("mail",), max_steps=8)
    validate(task)
    return generator.Request(id=request_id, person=PERSON, day=day, order=order, template="pilot_bread_order",
                             intended="tool_workflows", prompt=prompt,
                             intent=f"One email to {email} ordering {loaves} large loaves for tomorrow.",
                             task=task, inputs={})


def _shift_request(day: int, order: int, rng: random.Random, apps: dict[str, Any], hours: dict[str, str],
                   stale: dict[str, str] | None) -> generator.Request:
    from row_bot.learning.packs import PackTask, Solution, Verifier, validate

    who = rng.choice(_WHOS)
    today = generator.day_date(day)
    weekend = [today + timedelta(days=step) for step in range(1, 8) if (today + timedelta(days=step)).weekday() >= 5]
    when = rng.choice(weekend)
    prompt = rng.choice(_SHIFT_PHRASINGS).format(who=who, date=when.isoformat())
    title = f"{who} - weekend shift"
    call = {"tool": "calendar_create_event",
            "args": {"title": title, "date": when.isoformat(), "start": hours["start"], "end": hours["end"]}}
    where = {"title": {"contains": who}, "date": when.isoformat(), "start": hours["start"]}
    checks = [{"in": "calendar.events", "where": where, "count": _matching(apps, "calendar.events", where) + 1}]
    wrong = (dict(call["args"], start=stale["start"], end=stale["end"]) if stale
             else dict(call["args"], date=(when + timedelta(days=7)).isoformat()))
    request_id = f"{PERSON}-pilot-d{day:02d}-shift"
    task = PackTask(id=request_id, pack="tool_workflows", split="visible", prompt=prompt,
                    verifier=Verifier("app_state", checks), reference=Solution(answer="Added.", calls=(call,)),
                    decoy=Solution(answer="Added.", calls=({"tool": "calendar_create_event", "args": wrong},)),
                    files={}, apps=apps, tools=("calendar",), max_steps=8)
    validate(task)
    return generator.Request(id=request_id, person=PERSON, day=day, order=order, template="pilot_weekend_shift",
                             intended="tool_workflows", prompt=prompt,
                             intent=(f"One calendar event '{title}' on {when.isoformat()} from {hours['start']} "
                                     f"to {hours['end']} (the weekend shift's hours on {today.isoformat()})."),
                             task=task, inputs={})


def build(*, seed: int = SEED, days: int = DAYS) -> Timeline:
    """The pilot's timeline for ``seed``: the same seed, the same timeline."""

    history = generator.generate(PERSON, seed=seed, days=0)
    rng = random.Random(f"{int(seed)}/{PERSON}/pilot")
    requests: list[generator.Request] = []
    tells: list[Tell] = []
    for day in range(1, days + 1):
        weekday = generator.day_date(day).weekday()
        order = 0
        for change in CHANGES:
            if change.day == day:
                tells.append(Tell(f"{PERSON}-pilot-d{day:02d}-tell-{change.fact}", day, order, change.fact,
                                  change.announcement))
                order += 1
        apps = apps_on(history.start_apps, day)
        if weekday in SCHEDULE["bread_order"]:
            change = next(item for item in CHANGES if item.task == "bread_order")
            after = day >= change.day
            requests.append(_bread_request(day, order, rng, apps, change.new if after else change.old,
                                           change.old if after else None))
            order += 1
        if weekday in SCHEDULE["weekend_shift"]:
            change = next(item for item in CHANGES if item.task == "weekend_shift")
            after = day >= change.day
            requests.append(_shift_request(day, order, rng, apps, change.new if after else change.old,
                                           change.old if after else None))
            order += 1
    consent = {task: 2 + int(draw(seed, PERSON, task, "consent_run") < 0.5) for task in SCHEDULE}
    return Timeline(seed=int(seed), days=days, changes=CHANGES, requests=tuple(requests), tells=tuple(tells),
                    consent_run=consent, history=history)


def seeded_skill(task: str, day: int, timeline: Timeline) -> dict[str, str]:
    """The fallback skill for ``task`` as it would read on ``day`` (the fact's value then)."""

    spec = dict(SEEDED_SKILLS[task])
    value = timeline.value(TASK_FACT[task], day)
    values = {"email": value} if isinstance(value, str) else dict(value)
    spec["instructions"] = spec["instructions"].format(**values)
    return spec


_WEEKEND_WORDS = re.compile(r"weekend|saturday|sunday", re.IGNORECASE)


def mentions(fact: str, value: Any, text: str) -> bool:
    """Whether ``text`` states ``value`` of ``fact``: the address as written; for the weekend hours, a text
    about the weekend shift that gives its start (``09:00``, ``9:00``, ``9am``)."""

    text = str(text or "")
    if isinstance(value, str):
        return value.lower() in text.lower()
    if not _WEEKEND_WORDS.search(text):
        return False
    hour = int(str(value["start"]).split(":")[0])
    pattern = rf"(?<![\d:])(0?{hour}[:.]00|{hour}\s?(am|a\.m\.))(?!\d)"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def weekday_name(day: int) -> str:
    return generator.day_date(day).strftime("%A")


def first_day() -> date:
    return generator.day_date(1)


__all__ = ["CHANGES", "DAYS", "FactChange", "PERSON", "SCHEDULE", "SEED", "SEEDED_SKILLS", "TASK_FACT", "Tell",
           "Timeline", "apps_on", "build", "mentions", "rota_policy_v4", "sandgate_letter", "seeded_skill"]
