"""The precedence study's conflict items: a seed in, the same items out (Lane B, ``PRECEDENCE.md``).

Fixed before any run. Each item is one fact of one made-up small organisation, in six kinds (an address or
recipient, a supplier or contact, opening or working hours, an amount or limit, who holds a role, a policy rule),
with an **old** value X and a **new** value Y, a short **skill** that names the fact (and says when it was last
updated), a recalled **memory** that states the fact with its date, and the person's **request**, whose right
first action depends on the fact. The wording of the skill, the memory and the request, the values, and the gap
between the dates all come from the seed; nothing here knows the method.

The six **conditions** (every item in every condition, so comparisons are paired):

| # | Skill says | Memory says | Note |
|---|---|---|---|
| 1 | old X | (none) | no |
| 2 | (none) | new Y | no |
| 3 | old X | new Y, dated later | no |
| 4 | old X | new Y, dated later | yes |
| 5 | new Y | new Y | no |
| 6 | newer Y | older X, dated earlier | yes |

The **note** is one line appended to the recall block, in the form a product change would use
(:func:`note_line`). Nothing here imports Row-Bot.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any

FORMAT = "row_bot.learning.precedence_items"
VERSION = 1
SEED = 20261025
COUNT = 200
KINDS = ("address", "supplier", "hours", "amount", "role", "policy")
CONDITIONS = (1, 2, 3, 4, 5, 6)
#: What each condition puts where: skill value, memory value, note ("x" = old, "y" = new, None = absent).
LAYOUT: dict[int, dict[str, Any]] = {
    1: {"skill": "x", "memory": None, "note": False},
    2: {"skill": None, "memory": "y", "note": False},
    3: {"skill": "x", "memory": "y", "note": False},
    4: {"skill": "x", "memory": "y", "note": True},
    5: {"skill": "y", "memory": "y", "note": False},
    6: {"skill": "y", "memory": "x", "note": True},
}
#: The answer each condition expects (validity conditions 1, 2, 5; the measured choice in 3, 4 and 6).
EXPECTED = {1: "x", 2: "y", 3: "y", 4: "y", 5: "y", 6: "y"}
#: The latest date any item uses (before the real date the models see, 25 Oct 2026).
LATEST = date(2026, 10, 20)
#: Where an hours skill puts the shift's end (filled with the end that goes with its start).
END = "@END@"

_ORGS = ("Lantern Lane Kitchen", "Harbour Street Library", "Fenwick Bookkeeping", "Ashgrove Primary PTA",
         "Millbrook Food Bank", "Cobalt Bay Rowing Club", "Kestrel Studio", "Tollgate Allotments",
         "Brindle & Moss Florists", "Northgate Youth Theatre", "Willowmere Surgery", "Quayside Cycles")
_PEOPLE = ("Ayo Bello", "Bella Marsh", "Chidi Okafor", "Dev Patel", "Elif Kaya", "Finn Doyle", "Grace Lin",
           "Hamza Idris", "Ines Moreau", "Jonas Berg", "Kemi Adeyemi", "Luca Romano", "Maya Singh", "Noor Haddad",
           "Owen Price", "Priya Nair")
_SUPPLIERS = {
    "bread": ("Sandgate Bakery", "Crumb & Co", "Old Mill Loaves", "Northfield Bakehouse"),
    "vegetables": ("Hartwell Produce", "Greenacre Growers", "Riverside Veg", "Fenland Fresh"),
    "printing": ("Inkwell Print", "Swift Press", "Paperweight Printers", "Lighthouse Print"),
    "cleaning": ("Brightside Cleaning", "Spotless Supplies", "Clearwater Hygiene", "Sparkle Trade"),
    "stationery": ("Ledger Stationers", "Quill & Clip", "Office Nook", "Paper Trail Supplies"),
    "coffee": ("Bean Street Roasters", "Harbour Coffee Co", "Copper Pot Coffee", "Morning Grind"),
}
_ITEMS = {"bread": "large loaves", "vegetables": "kg of potatoes", "printing": "flyers", "cleaning": "boxes of gloves",
          "stationery": "reams of paper", "coffee": "kg of beans"}
_PURPOSES = (("invoices", "invoice"), ("bookings", "booking form"), ("orders", "order"),
             ("receipts", "receipt"), ("reports", "monthly report"), ("enquiries", "enquiry"))
_MAILBOXES = ("accounts", "billing", "office", "hello", "admin", "finance", "team", "desk", "orders", "bookings")
#: Shifts (and whether they fall at the weekend) with old and new hours that suit them.
_SHIFTS = (
    ("weekend shift", True, ((("10:00", "15:00"), ("09:00", "14:00")), (("11:00", "16:00"), ("10:00", "15:00")))),
    ("Saturday market stall", True, ((("08:00", "12:00"), ("08:30", "12:30")), (("07:30", "11:30"), ("08:30", "12:30")))),
    ("Sunday cafe shift", True, ((("11:00", "14:30"), ("12:00", "15:30")), (("10:30", "14:00"), ("11:30", "15:00")))),
    ("evening shift", False, ((("16:30", "20:00"), ("17:00", "21:00")), (("18:00", "22:00"), ("17:30", "21:30")))),
    ("morning help desk", False, ((("09:00", "13:00"), ("10:00", "14:00")), (("08:00", "12:00"), ("09:30", "13:30")))),
    ("late cover shift", False, ((("19:00", "23:00"), ("18:00", "22:00")), (("20:00", "23:30"), ("19:30", "23:00")))),
)
_FEES = ("hall hire", "storage unit rent", "van lease", "software licence", "pitch fee", "window cleaning")
_ROLES = ("treasurer", "safeguarding lead", "volunteer coordinator", "health and safety officer",
          "IT contact", "facilities manager")
_TICKETS = ("Back door lock sticks", "Freezer alarm beeps at night", "Projector won't turn on",
            "Gutter over the entrance leaks", "Wi-Fi drops in the hall", "Fire exit sign flickers",
            "Boiler pressure low", "Van tyre keeps losing air", "Printer jams on double-sided",
            "Kitchen tap drips", "Lift button sticks", "Shelf bracket bent")
_TRIAGE = (("building", ("estates", "facilities", "site-team", "premises")),
           ("IT", ("it-support", "tech", "systems", "digital")),
           ("safety", ("health-safety", "urgent-safety", "hs-review", "safety-first")),
           ("vehicle", ("fleet", "transport", "van-team", "vehicles")))


@dataclass(frozen=True)
class Item:
    """One conflict item (see the module's docstring)."""

    id: str
    kind: str
    org: str
    fact: str  # what the fact is, in words
    x: str  # the old value
    y: str  # the new value
    tool: str  # the tool whose first call carries the fact
    arg: str  # the argument that carries it
    request: str  # the person's message
    skill_name: str
    skill_display: str
    skill_description: str
    skill_template: str  # with {value} and {updated}
    memory_subject: str
    memory_template: str  # with {value} and {since}
    early: str  # the earlier date (ISO)
    late: str  # the later date (ISO)
    apps: dict[str, Any]
    ends: dict[str, str] = field(default_factory=dict)  # hours: the end that goes with each start

    def skill_text(self, value: str, updated: str) -> str:
        return self.skill_template.format(value=value, updated=updated)

    def memory_text(self, value: str, since: str) -> str:
        return self.memory_template.format(value=value, since=since)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Prompt:
    """One item in one condition: what goes where."""

    item: Item
    condition: int
    skill: str | None  # the skill's instructions, or None
    skill_updated: str | None
    memory: str | None  # the recalled memory's content, or None
    memory_since: str | None
    note: str | None

    @property
    def skill_value(self) -> str | None:
        side = LAYOUT[self.condition]["skill"]
        return None if side is None else getattr(self.item, side)

    @property
    def memory_value(self) -> str | None:
        side = LAYOUT[self.condition]["memory"]
        return None if side is None else getattr(self.item, side)


def _email(org: str, box: str) -> str:
    domain = "".join(ch for ch in org.lower().replace("&", "and") if ch.isalnum() or ch == " ").split()
    return f"{box}@{'-'.join(domain[:3])}.example"


def _iso(day: date) -> str:
    return day.isoformat()


def _pick_two(rng: random.Random, pool: tuple) -> tuple:
    first, second = rng.sample(list(pool), 2)
    return first, second


def _dates(rng: random.Random) -> tuple[str, str]:
    """An earlier and a later date, 3 to 90 days apart, both before :data:`LATEST`."""

    late = LATEST - timedelta(days=rng.randint(0, 120))
    early = late - timedelta(days=rng.randint(3, 90))
    return _iso(early), _iso(late)


_SKILL_OPENERS = ("Use this when the request is about {topic}.", "{topic_cap}: how we do it.",
                  "Steps for {topic}.")
_UPDATED = ("_Last updated {updated}._", "(This skill was last updated on {updated}.)", "Updated: {updated}.")
_MEMORY_FORMS = ("Since {since}, {fact} is {value}.", "{fact_cap} is {value} (as of {since}).",
                 "From {since} on, {fact} is {value}.")


def _skill(rng: random.Random, topic: str, steps: list[str]) -> str:
    opener = rng.choice(_SKILL_OPENERS).format(topic=topic, topic_cap=topic[:1].upper() + topic[1:])
    numbered = "\n".join(f"{index}. {step}" for index, step in enumerate(steps, start=1))
    return f"{opener}\n\n## Steps\n{numbered}\n\n{rng.choice(_UPDATED)}"


def _memory(rng: random.Random, fact: str) -> str:
    form = rng.choice(_MEMORY_FORMS)
    return form.replace("{fact}", fact).replace("{fact_cap}", fact[:1].upper() + fact[1:])


def _item(number: int, kind: str, rng: random.Random) -> Item:
    org = rng.choice(_ORGS)
    early, late = _dates(rng)
    item_id = f"prec-{number:03d}-{kind}"
    if kind == "address":
        purpose, thing = rng.choice(_PURPOSES)
        old_box, new_box = _pick_two(rng, _MAILBOXES)
        x, y = _email(org, old_box), _email(org, new_box)
        month = rng.choice(("October", "September", "the autumn term", "this quarter"))
        fact = f"{org}'s address for {purpose}"
        request = rng.choice((f"Send {org} the {thing} for {month}.", f"Email the {thing} for {month} to {org}.",
                              f"Can you send {org} our {thing} for {month}?"))
        steps = [f"Send the {thing} by email with `mail_send` to **{{value}}**.",
                 f"Subject: `{thing.capitalize()} - <month>`; a short polite body.", "Say which address it went to."]
        return Item(item_id, kind, org, fact, x, y, "mail_send", "to", request, f"{kind}_{number:03d}",
                    f"{org} {purpose}", f"Send {purpose} to {org}.", _skill(rng, f"sending {purpose} to {org}", steps),
                    f"{org} {purpose} address", _memory(rng, fact), early, late, {})
    if kind == "supplier":
        category = rng.choice(sorted(_SUPPLIERS))
        old, new = _pick_two(rng, _SUPPLIERS[category])
        box = rng.choice(("orders", "sales", "hello", "trade"))
        x, y = _email(old, box), _email(new, box)
        quantity = rng.randint(6, 60)
        fact = f"our {category} supplier's order address"
        request = rng.choice((f"Order {quantity} {_ITEMS[category]} from our {category} supplier for Friday.",
                              f"Place this week's {category} order: {quantity} {_ITEMS[category]}.",
                              f"We need {quantity} {_ITEMS[category]} - send the order to our {category} supplier."))
        steps = [f"Our {category} supplier takes orders by email at **{{value}}**.",
                 "Send one email with `mail_send`: the quantity, the delivery day, signed by us.",
                 "Say the order was sent."]
        return Item(item_id, kind, org, fact, x, y, "mail_send", "to", request, f"{kind}_{number:03d}",
                    f"{category.capitalize()} order", f"Order {category} from our supplier.",
                    _skill(rng, f"{category} orders", steps), f"{category.capitalize()} supplier",
                    _memory(rng, fact), early, late, {})
    if kind == "hours":
        shift, weekend, pairs = rng.choice(_SHIFTS)
        (old_start, old_end), (new_start, new_end) = rng.choice(pairs)
        x, y = old_start, new_start
        who = rng.choice(_PEOPLE).split()[0]
        day = LATEST + timedelta(days=rng.randint(10, 30))
        days = ({5} if shift.startswith("Saturday") else {6} if shift.startswith("Sunday")
                else {5, 6} if weekend else {0, 1, 2, 3, 4})
        while day.weekday() not in days:
            day += timedelta(days=1)
        fact = f"the {shift}'s start time"
        request = rng.choice((f"Put {who} on the {shift} on {_iso(day)}.",
                              f"Add {who} to the {shift} on {_iso(day)} in the calendar.",
                              f"{who} is doing the {shift} on {_iso(day)} - book it please."))
        ends = {old_start: old_end, new_start: new_end}
        steps = ["Create one event with `calendar_create_event`: title `<name> - " + shift + "`, the date, "
                 "start **{value}**, end " + END + " (the " + shift + "'s hours).", "Confirm it's booked."]
        template = _skill(rng, f"booking the {shift}", steps)
        return Item(item_id, kind, org, fact, x, y, "calendar_create_event", "start", request, f"{kind}_{number:03d}",
                    f"{shift.capitalize()} booking", f"Book someone on the {shift}.", template,
                    f"{shift.capitalize()} hours", _memory(rng, fact), early, late, {}, ends)
    if kind == "amount":
        fee = rng.choice(_FEES)
        payee = rng.choice(_ORGS[::-1])
        old_amount = rng.randrange(40, 900, 5)
        new_amount = old_amount + rng.choice((-1, 1)) * rng.randrange(15, 200, 5)
        new_amount = max(25, new_amount)
        x, y = f"GBP {old_amount}", f"GBP {new_amount}"
        email = _email(payee, "accounts")
        fact = f"the monthly {fee} we pay {payee}"
        request = rng.choice((f"Email {payee} ({email}) to confirm this month's {fee} payment.",
                              f"Send {payee} at {email} a note confirming we're paying the {fee} this month.",
                              f"Let {payee} ({email}) know the {fee} payment is going out this month."))
        steps = [f"The {fee} is **{{value}}** a month.", f"Email {payee} with `mail_send`, stating the amount.",
                 "Say it was sent."]
        return Item(item_id, kind, org, fact, x, y, "mail_send", "body", request, f"{kind}_{number:03d}",
                    f"{fee.capitalize()} payment", f"Confirm the {fee} payment.", _skill(rng, f"the {fee}", steps),
                    f"{fee.capitalize()} amount", _memory(rng, fact), early, late, {})
    if kind == "role":
        role = rng.choice(_ROLES)
        old, new = _pick_two(rng, _PEOPLE)
        x, y = old, new
        ticket = rng.choice(_TICKETS)
        number_id = str(rng.randint(10, 99))
        fact = f"our {role}"
        request = rng.choice((f"Assign ticket #{number_id} ({ticket}) to our {role}.",
                              f"#{number_id} ({ticket}) is for the {role} - assign it.",
                              f"Give ticket #{number_id} to our {role}, please."))
        steps = [f"Our {role} is **{{value}}**.",
                 "Assign the ticket with `issues_update`, assignee set to that person.", "Confirm who has it."]
        apps = {"issues": {"issues": [{"id": number_id, "title": ticket, "state": "open", "labels": [],
                                       "assignee": "", "comments": []}]}}
        return Item(item_id, kind, org, fact, x, y, "issues_update", "assignee", request, f"{kind}_{number:03d}",
                    f"Tickets for the {role}", f"Assign tickets to the {role}.", _skill(rng, f"tickets for the {role}",
                                                                                        steps),
                    f"{role.capitalize()}", _memory(rng, fact), early, late, apps)
    category, labels = rng.choice(_TRIAGE)
    old, new = _pick_two(rng, labels)
    x, y = old, new
    ticket = rng.choice(_TICKETS)
    number_id = str(rng.randint(10, 99))
    fact = f"the triage label for {category} tickets"
    request = rng.choice((f"Label ticket #{number_id} ({ticket}) per our triage policy for {category} issues.",
                          f"#{number_id} ({ticket}) is a {category} issue - label it as our policy says.",
                          f"Triage #{number_id} ({ticket}): it's {category}, add the policy label."))
    steps = [f"{category[:1].upper() + category[1:]} tickets get the label **{{value}}**.",
             "Add it with `issues_update` (`add_labels`).", "Confirm the label."]
    apps = {"issues": {"issues": [{"id": number_id, "title": ticket, "state": "open", "labels": [],
                                   "assignee": "", "comments": []}]}}
    return Item(item_id, kind, org, fact, x, y, "issues_update", "add_labels", request, f"{kind}_{number:03d}",
                f"{category} triage", f"Label {category} tickets.", _skill(rng, f"triaging {category} tickets", steps),
                f"{category} triage label", _memory(rng, fact), early, late, apps)


def end_for(item: Item, start: str) -> str:
    """An hours item's end that goes with ``start`` (the skill and the memory give both)."""

    return str(item.ends.get(start, ""))


def generate(*, seed: int = SEED, count: int = COUNT) -> tuple[Item, ...]:
    """``count`` items from ``seed``, the six kinds in turn (the same seed, the same items)."""

    rng = random.Random(f"{int(seed)}/precedence")
    return tuple(_item(number, KINDS[(number - 1) % len(KINDS)], rng) for number in range(1, count + 1))


def note_line(display: str, skill_value: str, skill_updated: str, memory_value: str, memory_since: str) -> str:
    """The conflict note, one line at the end of the recall block (conditions 4 and 6)."""

    return (f"Note: the loaded skill \"{display}\" (last updated {skill_updated}) names {skill_value}; "
            f"memory says {memory_value} since {memory_since}.")


def prompt(item: Item, condition: int) -> Prompt:
    """What ``condition`` puts where for ``item`` (see :data:`LAYOUT`)."""

    layout = LAYOUT[condition]
    # The newer side carries the later date: the memory in 2-5, the skill in 6.
    skill_date = item.late if condition == 6 else item.early
    memory_date = item.early if condition == 6 else item.late
    skill = memory = note = None
    if layout["skill"] is not None:
        value = getattr(item, layout["skill"])
        skill = item.skill_text(value, skill_date).replace(END, end_for(item, value))
    if layout["memory"] is not None:
        value = getattr(item, layout["memory"])
        memory = item.memory_text(value, memory_date)
        if item.kind == "hours":
            memory += f" The shift ends at {end_for(item, value)}."
    if layout["note"]:
        note = note_line(item.skill_display, getattr(item, layout["skill"]), skill_date,
                         getattr(item, layout["memory"]), memory_date)
    return Prompt(item, condition, skill, skill_date if skill else None, memory, memory_date if memory else None,
                  note)


def fingerprint(items: tuple[Item, ...]) -> str:
    text = json.dumps([item.to_dict() for item in items], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


__all__ = ["CONDITIONS", "COUNT", "EXPECTED", "Item", "KINDS", "LAYOUT", "Prompt", "SEED", "end_for", "fingerprint",
           "generate", "note_line", "prompt"]
