"""Pack: document lookups (D30), visible split.

A small synthetic company's documents in ``docs/``. Each task asks one question whose
answer is in exactly one current document; the traps are real ones: two documents
with the same title (one superseded), a draft next to an approved version, notes that
were later changed, and figures that must be combined. Answers are checked exactly.
"""

from __future__ import annotations

from row_bot.learning.packs import PackTask, Solution, Verifier


def _doc(doc_id: str, title: str, owner: str, updated: str, body: str, status: str = "current") -> str:
    return f"ID: {doc_id}\nTitle: {title}\nOwner: {owner}\nUpdated: {updated}\nStatus: {status}\n\n{body.strip()}\n"


DOCS: dict[str, str] = {
    "docs/pol-114-travel.md": _doc("POL-114", "Travel policy", "Finance", "2026-02-10", """
Client dinners: up to 85 EUR per person.
Hotels: a nightly cap of 180 EUR in capital cities and 130 EUR elsewhere.
Rail: first class is allowed for journeys longer than 4 hours.
Claims must be filed within 30 days of the trip.
"""),
    "docs/pol-097-travel.md": _doc("POL-097", "Travel policy", "Finance", "2024-06-01", """
This policy is superseded by POL-114.
Client dinners: up to 60 EUR per person.
Hotels: a nightly cap of 150 EUR in capital cities and 110 EUR elsewhere.
Claims must be filed within 45 days of the trip.
""", status="superseded"),
    "docs/mtg-031-ops.md": _doc("MTG-031", "Weekly ops sync", "Operations", "2026-03-02", """
Attendees: Ines Duarte, Tomas Berg, Mara Lindqvist.
Decisions:
- The warehouse audit moves to 18 March 2026.
- Ines Duarte owns the supplier review.
- The budget for new scanners is approved: 4,200 EUR.
"""),
    "docs/mtg-032-ops.md": _doc("MTG-032", "Weekly ops sync", "Operations", "2026-03-09", """
Attendees: Ines Duarte, Tomas Berg.
Decisions:
- The warehouse audit moves again, to 25 March 2026.
- The scanner order was placed with Kestrel Supply.
- The next sync is cancelled; syncs resume on 23 March 2026.
"""),
    "docs/hr-210-leave.md": _doc("HR-210", "Leave policy", "People", "2025-11-20", """
Annual leave is 26 days a year.
Up to 5 unused days carry over into the next year; carried-over days expire on 31 March.
Parental leave is 20 weeks at full pay.
"""),
    "docs/it-045-laptops.md": _doc("IT-045", "Laptop refresh", "IT", "2026-01-15", """
Laptops are replaced every 4 years.
Engineers may request machines with 32 GB of memory.
Order through the IT portal, category "Hardware > Laptops".
Old laptops are wiped and donated to Brightside Schools.
"""),
    "docs/prj-778-heron-brief.md": _doc("PRJ-778", "Project Heron brief", "Finance", "2025-12-01", """
Goal: move billing to the new ledger by 30 June 2026.
Sponsor: Mara Lindqvist.
Budget: 120,000 EUR.
Main risk: the vendor API allows at most 50 requests per second.
"""),
    "docs/prj-779-heron-status.md": _doc("PRJ-779", "Project Heron status", "Finance", "2026-03-12", """
Phase 1 (design) is done. Phase 2 (data backfill) is 40% done.
The go-live date moves to 14 July 2026.
Spend to date: 47,500 EUR.
"""),
    "docs/fac-012-access.md": _doc("FAC-012", "Office access", "Facilities", "2025-09-30", """
Doors open from 07:00 to 21:00 on weekdays.
Weekend access needs a badge with the W flag: ask facilities@example.test.
Visitors collect their badge at reception on floor 2.
"""),
    "docs/sup-300-suppliers.md": _doc("SUP-300", "Supplier list", "Operations", "2026-02-01", """
| Supplier | Supplies | Contract ends |
| Kestrel Supply | scanners, label printers | 2027-01-31 |
| Northwind Paper | paper, toner | 2026-05-15 |
| Blue Harbor IT | laptops, monitors | 2026-11-30 |
"""),
    "docs/fin-501-q1-budget.md": _doc("FIN-501", "Q1 budget", "Finance", "2026-01-05", """
Approved budget for Q1 2026:
- Marketing: 38,000 EUR
- Operations: 52,500 EUR
- Engineering: 91,000 EUR
- Contingency: 7.5% of the total above.
"""),
    "docs/fin-502-q1-budget-draft.md": _doc("FIN-502", "Q1 budget (draft)", "Finance", "2025-12-10", """
Draft, not approved:
- Marketing: 45,000 EUR
- Operations: 50,000 EUR
- Engineering: 88,000 EUR
""", status="draft"),
}

_IN_DOCS = "Use the documents in the docs/ folder of the workspace. "


def _task(number: int, question: str, verifier: Verifier, answer: str, wrong: str) -> PackTask:
    return PackTask(
        id=f"doc_{number:02d}", pack="document_lookups", split="visible", prompt=_IN_DOCS + question,
        verifier=verifier, reference=Solution(answer=answer), decoy=Solution(answer=wrong), files=DOCS,
        tools=("files",), max_steps=10,
    )


def _number(value: float) -> Verifier:
    return Verifier("answer", value, match="number")


def _exact(value: str) -> Verifier:
    return Verifier("answer", value, match="exact")


TASKS: tuple[PackTask, ...] = (
    _task(1, "Under the current travel policy, what is the per-person limit for client dinners in EUR? "
             "Reply with only the number.", _number(85), "85", "60"),
    _task(2, "Under the current travel policy, within how many days of a trip must a claim be filed? "
             "Reply with only the number.", _number(30), "30", "45"),
    _task(3, "When is the warehouse audit now scheduled? Reply with only the date as YYYY-MM-DD.",
          _exact("2026-03-25"), "2026-03-25", "2026-03-18"),
    _task(4, "Who owns the supplier review? Reply with only the person's name.",
          _exact("Ines Duarte"), "Ines Duarte", "Mara Lindqvist"),
    _task(5, "How many unused days of annual leave carry over into the next year? Reply with only the number.",
          _number(5), "5", "26"),
    _task(6, "Who receives old laptops after they are wiped? Reply with only the name.",
          _exact("Brightside Schools"), "Brightside Schools", "Blue Harbor IT"),
    _task(7, "What is Project Heron's current go-live date? Reply with only the date as YYYY-MM-DD.",
          _exact("2026-07-14"), "2026-07-14", "2026-06-30"),
    _task(8, "How much of Project Heron's budget is left, in EUR? Reply with only the number.",
          _number(72_500), "72500", "47500"),
    _task(9, "Which supplier's contract ends first? Reply with only the supplier's name.",
          _exact("Northwind Paper"), "Northwind Paper", "Blue Harbor IT"),
    _task(10, "What is the approved Q1 budget for Operations, in EUR? Reply with only the number.",
          _number(52_500), "52,500", "50,000"),
    _task(11, "Under the current travel policy, what is the nightly hotel cap in a capital city, in EUR? "
              "Reply with only the number.", _number(180), "180", "150"),
    _task(12, "Which email address do I ask for weekend office access? Reply with only the address.",
          _exact("facilities@example.test"), "facilities@example.test", "reception@example.test"),
    _task(13, "At most how many requests per second does the vendor API in the Project Heron brief allow? "
              "Reply with only the number.", _number(50), "50", "40"),
    _task(14, "What is the ID of the travel policy that applies now? Reply with only the ID.",
          _exact("POL-114"), "POL-114", "POL-097"),
    _task(15, "What is the approved Q1 budget for Marketing, Operations and Engineering together, before "
              "contingency, in EUR? Reply with only the number.", _number(181_500), "181500", "183000"),
    _task(16, "Which documents are drafts or superseded? Reply with only a JSON array of their IDs, sorted "
              "alphabetically.", Verifier("json_answer", ["FIN-502", "POL-097"]), '["FIN-502", "POL-097"]',
          '["POL-097"]'),
)
