"""The packs' hidden splits (E10). Sealed: only the audit vault (``learning.audit``) reads them.

Eight tasks per pack, written apart from the visible ones (their own documents, data,
functions and app states), so a change tuned on the visible split meets problems it
hasn't seen. Drafting, practice, training and strategy code can't import this module
(``learning.sealing``); ``evaluation`` reaches these tasks only through the vault,
which hands back aggregates.
"""

from __future__ import annotations

from typing import Any

from row_bot.learning.packs import PackTask, Solution, Verifier
from row_bot.learning.packs.document_lookups import _doc
from row_bot.learning.packs.small_coding import _tests
from row_bot.learning.packs.structured_transforms import _csv, _json
from row_bot.learning.packs.tool_workflows import _call, _count, _event, _issue, _mail
from row_bot.learning.sealing import Seal, seal_module

# ── Document lookups ─────────────────────────────────────────────────────────

LAB_DOCS: dict[str, str] = {
    "docs/saf-020-safety.md": _doc("SAF-020", "Lab safety rules", "Safety", "2026-01-12", """
Goggles are required in rooms B1 to B4.
At most 2 people work at one fume hood.
Chemical waste is picked up every Tuesday.
"""),
    "docs/saf-011-safety.md": _doc("SAF-011", "Lab safety rules", "Safety", "2024-03-01", """
Superseded by SAF-020.
At most 3 people work at one fume hood.
Chemical waste is picked up every Friday.
""", status="superseded"),
    "docs/eq-140-microscopes.md": _doc("EQ-140", "Microscope booking", "Equipment", "2025-10-02", """
Book through the portal up to 14 days ahead, at most 3 hours per booking.
The confocal microscope is in room C2; the electron microscope is in room B1.
"""),
    "docs/gr-077-aurora.md": _doc("GR-077", "Grant Aurora", "Research office", "2025-05-20", """
Funder: North Sea Foundation. Principal investigator: Dr. Hana Kovac.
Total: 340,000 EUR over 3 years. The progress report is due on 2026-09-30.
"""),
    "docs/gr-078-aurora-amendment.md": _doc("GR-078", "Grant Aurora amendment", "Research office", "2026-02-11", """
The progress report deadline is extended to 2026-11-15.
The budget increases by 25,000 EUR.
"""),
    "docs/mtg-410-lab.md": _doc("MTG-410", "Lab meeting", "Lab", "2026-02-20", """
Journal club moves to Wednesdays.
Sample freezer F3 is out of service until 2026-03-15.
"""),
    "docs/mtg-411-lab.md": _doc("MTG-411", "Lab meeting", "Lab", "2026-02-27", """
Freezer F3 was repaired early and is back in service on 2026-03-01.
Oskar Ruiz is the new lab manager.
"""),
    "docs/it-210-retention.md": _doc("IT-210", "Data retention", "IT", "2025-08-14", """
Raw microscopy data is kept for 7 years; processed data for 3 years.
Backups run every night at 02:00.
"""),
}


def _doc_task(number: int, question: str, verifier: Verifier, answer: str, wrong: str) -> PackTask:
    return PackTask(
        id=f"hdoc_{number:02d}", pack="document_lookups", split="hidden",
        prompt="Use the documents in the docs/ folder of the workspace. " + question, verifier=verifier,
        reference=Solution(answer=answer), decoy=Solution(answer=wrong), files=LAB_DOCS, tools=("files",),
        max_steps=10,
    )


_DOC_TASKS = (
    _doc_task(1, "Under the current safety rules, at most how many people may work at one fume hood? Reply with "
                 "only the number.", Verifier("answer", 2, match="number"), "2", "3"),
    _doc_task(2, "When is the Grant Aurora progress report due? Reply with only the date as YYYY-MM-DD.",
              Verifier("answer", "2026-11-15"), "2026-11-15", "2026-09-30"),
    _doc_task(3, "What is Grant Aurora's total budget now, in EUR? Reply with only the number.",
              Verifier("answer", 365_000, match="number"), "365000", "340000"),
    _doc_task(4, "Who is the lab manager now? Reply with only the name.", Verifier("answer", "Oskar Ruiz"),
              "Oskar Ruiz", "Hana Kovac"),
    _doc_task(5, "When is freezer F3 back in service? Reply with only the date as YYYY-MM-DD.",
              Verifier("answer", "2026-03-01"), "2026-03-01", "2026-03-15"),
    _doc_task(6, "For how many years is raw microscopy data kept? Reply with only the number.",
              Verifier("answer", 7, match="number"), "7", "3"),
    _doc_task(7, "Which room is the confocal microscope in? Reply with only the room.", Verifier("answer", "C2"),
              "C2", "B1"),
    _doc_task(8, "Under the current safety rules, on which weekday is chemical waste picked up? Reply with only "
                 "the weekday.", Verifier("answer", "Tuesday"), "Tuesday", "Friday"),
)

# ── Structured transforms ────────────────────────────────────────────────────

BOOKS = [["title", "author", "year"], ["Tidewater", "N. Osei", "2019"], ["Glass Rivers", "A. Brandt", "2021"],
         ["Low Orbit", "N. Osei", "2015"], ["Salt Roads", "A. Brandt", "2018"], ["Quiet Hours", "J. Park", "2022"]]
TEMPS = [["city", "celsius"], ["Reykjavik", "-2.5"], ["Lisbon", "17"], ["Nairobi", "24.4"]]
USERS = [{"email": "zoe@example.test", "active": True}, {"email": "abe@example.test", "active": False},
         {"email": "kai@example.test", "active": True}, {"email": "eli@example.test", "active": True}]
SALES = [["region", "month", "amount"], ["east", "jan", "120"], ["west", "jan", "80"], ["east", "feb", "95"],
         ["north", "feb", "40"], ["west", "feb", "110"]]
ENV = "# service settings\nAPP_NAME=ledger\n\nPORT=9000\n# debug off in production\nDEBUG=false\n"
HOURS = [["person", "hours"], ["ana", "6"], ["bo", "8"], ["ana", "4"], ["cy", "10"], ["bo", "2"]]
WORDS = "Orbit tide orbit RIVER salt\n"
NESTED = {"a": {"b": 1, "c": {"d": 2}}, "e": 3}


def _hours() -> str:
    totals: dict[str, int] = {}
    for person, hours in HOURS[1:]:
        totals[person] = totals.get(person, 0) + int(hours)
    ordered = sorted(totals.items(), key=lambda item: (-item[1], item[0]))
    return _csv([["person", "hours"]] + [[person, hours] for person, hours in ordered])


def _st_task(number: int, prompt: str, files: dict[str, str], path: str, match: str, expected: Any,
             wrong: str) -> PackTask:
    text = expected if isinstance(expected, str) else _json(expected)
    stored = expected if match == "json" else text
    return PackTask(
        id=f"hst_{number:02d}", pack="structured_transforms", split="hidden",
        prompt=prompt + " Write only that file, then reply with: done",
        verifier=Verifier("file", stored, match=match, path=path),
        reference=Solution(answer="done", files={path: text}), decoy=Solution(answer="done", files={path: wrong}),
        files=files, tools=("files",), max_steps=8,
    )


_ST_TASKS = (
    _st_task(1, "Read books.csv and write by_author.json: a JSON object mapping each author to the list of their "
                "titles sorted alphabetically.", {"books.csv": _csv(BOOKS)}, "by_author.json", "json",
             {"N. Osei": ["Low Orbit", "Tidewater"], "A. Brandt": ["Glass Rivers", "Salt Roads"],
              "J. Park": ["Quiet Hours"]},
             _json({"N. Osei": ["Tidewater", "Low Orbit"], "A. Brandt": ["Glass Rivers", "Salt Roads"],
                    "J. Park": ["Quiet Hours"]})),
    _st_task(2, "Read temps.csv and write temps_f.csv with the header city,fahrenheit, converting Celsius to "
                "Fahrenheit with one decimal, in the same order.", {"temps.csv": _csv(TEMPS)}, "temps_f.csv", "csv",
             "city,fahrenheit\nReykjavik,27.5\nLisbon,62.6\nNairobi,75.9\n",
             "city,fahrenheit\nReykjavik,28.5\nLisbon,62.6\nNairobi,75.9\n"),
    _st_task(3, "Read users.json and write active_emails.txt with the email of every active user, one per line, "
                "sorted alphabetically.", {"users.json": _json(USERS)}, "active_emails.txt", "lines",
             "eli@example.test\nkai@example.test\nzoe@example.test\n",
             "zoe@example.test\nkai@example.test\neli@example.test\n"),
    _st_task(4, "Read sales.csv and write region_totals.json: a JSON object mapping each region to its total "
                "amount, as numbers.", {"sales.csv": _csv(SALES)}, "region_totals.json", "json",
             {"east": 215, "west": 190, "north": 40}, _json({"east": 215, "west": 110, "north": 40})),
    _st_task(5, "Read service.env (KEY=VALUE lines; lines starting with # are comments) and write env.json: a "
                "JSON object of every key and its value as a string.", {"service.env": ENV}, "env.json", "json",
             {"APP_NAME": "ledger", "PORT": "9000", "DEBUG": "false"},
             _json({"APP_NAME": "ledger", "PORT": 9000, "DEBUG": False})),
    _st_task(6, "Read timesheet.csv and write hours.csv with the header person,hours: each person's total hours, "
                "most hours first and ties by name.", {"timesheet.csv": _csv(HOURS)}, "hours.csv", "csv", _hours(),
             "person,hours\nbo,10\nana,10\ncy,10\n"),
    _st_task(7, "Read words.txt and write lengths.json: a JSON object mapping each distinct word, lower-cased, to "
                "its number of letters.", {"words.txt": WORDS}, "lengths.json", "json",
             {"orbit": 5, "tide": 4, "river": 5, "salt": 4},
             _json({"Orbit": 5, "tide": 4, "orbit": 5, "RIVER": 5, "salt": 4})),
    _st_task(8, "Read nested.json and write flat.json: the same values with nested keys joined by dots, for "
                "example {\"a\": {\"b\": 1}} becomes {\"a.b\": 1}.", {"nested.json": _json(NESTED)}, "flat.json",
             "json", {"a.b": 1, "a.c.d": 2, "e": 3}, _json({"a.b": 1, "a.c": {"d": 2}, "e": 3})),
)

# ── Small coding tasks ───────────────────────────────────────────────────────


def _code_task(number: int, name: str, prompt: str, tests: str, reference: str, decoy: str) -> PackTask:
    from textwrap import dedent

    return PackTask(
        id=f"hcode_{number:02d}", pack="small_coding", split="hidden",
        prompt=f"In the workspace, write solution.py with {prompt} Use only the Python standard library. "
               "When the file is written, reply with: done",
        verifier=Verifier("code_tests", _tests(name, tests), path="solution.py"),
        reference=Solution(answer="done", files={"solution.py": dedent(reference).lstrip()}),
        decoy=Solution(answer="done", files={"solution.py": dedent(decoy).lstrip()}), tools=("files",), max_steps=8,
    )


_CODE_TASKS = (
    _code_task(1, "is_palindrome", "a function is_palindrome(text) that ignores case and anything that is not a "
                  "letter or digit.", """
        def test_yes(self):
            self.assertTrue(is_palindrome("A man, a plan, a canal: Panama"))
            self.assertTrue(is_palindrome(""))
        def test_no(self):
            self.assertFalse(is_palindrome("ab"))
            self.assertFalse(is_palindrome("1a2"))
        """, """
        def is_palindrome(text):
            chars = [c.lower() for c in text if c.isalnum()]
            return chars == chars[::-1]
        """, """
        def is_palindrome(text):
            return text.lower() == text.lower()[::-1]
        """),
    _code_task(2, "count_vowels", "a function count_vowels(text) that counts the letters a, e, i, o and u in "
                  "either case.", """
        def test_mixed(self):
            self.assertEqual(count_vowels("Education IS fun"), 7)
        def test_none(self):
            self.assertEqual(count_vowels("rhythm"), 0)
        """, """
        def count_vowels(text):
            return sum(1 for c in text.lower() if c in "aeiou")
        """, """
        def count_vowels(text):
            return sum(1 for c in text if c in "aeiou")
        """),
    _code_task(3, "fizzbuzz", "a function fizzbuzz(n) that returns a list of strings for 1 to n: 'Fizz' for "
                  "multiples of 3, 'Buzz' for multiples of 5, 'FizzBuzz' for both, else the number.", """
        def test_fifteen(self):
            result = fizzbuzz(15)
            self.assertEqual(result[:5], ["1", "2", "Fizz", "4", "Buzz"])
            self.assertEqual(result[14], "FizzBuzz")
        def test_zero(self):
            self.assertEqual(fizzbuzz(0), [])
        """, """
        def fizzbuzz(n):
            out = []
            for i in range(1, n + 1):
                word = ("Fizz" if i % 3 == 0 else "") + ("Buzz" if i % 5 == 0 else "")
                out.append(word or str(i))
            return out
        """, """
        def fizzbuzz(n):
            out = []
            for i in range(1, n + 1):
                out.append("Fizz" if i % 3 == 0 else "Buzz" if i % 5 == 0 else str(i))
            return out
        """),
    _code_task(4, "rotate", "a function rotate(matrix) that returns a new matrix (a list of lists) rotated 90 "
                  "degrees clockwise; it need not be square.", """
        def test_square(self):
            self.assertEqual(rotate([[1, 2], [3, 4]]), [[3, 1], [4, 2]])
        def test_wide(self):
            self.assertEqual(rotate([[1, 2, 3]]), [[1], [2], [3]])
        def test_empty(self):
            self.assertEqual(rotate([]), [])
        """, """
        def rotate(matrix):
            return [list(row) for row in zip(*matrix[::-1])]
        """, """
        def rotate(matrix):
            return [list(row) for row in zip(*matrix)]
        """),
    _code_task(5, "anagram_groups", "a function anagram_groups(words) that groups words that are anagrams of each "
                  "other: each group sorted, and the groups sorted by their first word.", """
        def test_groups(self):
            self.assertEqual(anagram_groups(["tea", "eat", "tan", "ate", "nat", "bat"]),
                             [["ate", "eat", "tea"], ["bat"], ["nat", "tan"]])
        def test_empty(self):
            self.assertEqual(anagram_groups([]), [])
        """, """
        def anagram_groups(words):
            groups = {}
            for word in words:
                groups.setdefault("".join(sorted(word)), []).append(word)
            return sorted(sorted(group) for group in groups.values())
        """, """
        def anagram_groups(words):
            groups = {}
            for word in words:
                groups.setdefault("".join(sorted(word)), []).append(word)
            return list(groups.values())
        """),
    _code_task(6, "binary_search", "a function binary_search(items, target) that returns the index of target in "
                  "the sorted list items, or -1 when it is absent.", """
        def test_found(self):
            self.assertEqual(binary_search([1, 3, 5, 7, 9], 7), 3)
            self.assertEqual(binary_search([1, 3, 5, 7, 9], 1), 0)
            self.assertEqual(binary_search([1, 3, 5, 7, 9], 9), 4)
        def test_missing(self):
            self.assertEqual(binary_search([1, 3, 5], 4), -1)
            self.assertEqual(binary_search([], 1), -1)
        """, """
        def binary_search(items, target):
            low, high = 0, len(items) - 1
            while low <= high:
                middle = (low + high) // 2
                if items[middle] == target:
                    return middle
                if items[middle] < target:
                    low = middle + 1
                else:
                    high = middle - 1
            return -1
        """, """
        def binary_search(items, target):
            low, high = 0, len(items) - 1
            while low < high:
                middle = (low + high) // 2
                if items[middle] == target:
                    return middle
                if items[middle] < target:
                    low = middle + 1
                else:
                    high = middle - 1
            return -1
        """),
    _code_task(7, "compress_spaces", "a function compress_spaces(text) that replaces every run of whitespace with "
                  "one space and strips both ends.", """
        def test_runs(self):
            self.assertEqual(compress_spaces("  a\\t\\tb \\n c  "), "a b c")
        def test_empty(self):
            self.assertEqual(compress_spaces("   "), "")
        """, """
        def compress_spaces(text):
            return " ".join(text.split())
        """, """
        def compress_spaces(text):
            while "  " in text:
                text = text.replace("  ", " ")
            return text.strip()
        """),
    _code_task(8, "parse_kv", "a function parse_kv(text) that parses 'key=value' pairs separated by semicolons "
                  "into a dict of strings, trimming spaces and skipping empty parts.", """
        def test_pairs(self):
            self.assertEqual(parse_kv("a=1; b = two ;;c=3"), {"a": "1", "b": "two", "c": "3"})
        def test_equals_in_value(self):
            self.assertEqual(parse_kv("url=x=y"), {"url": "x=y"})
        def test_empty(self):
            self.assertEqual(parse_kv(""), {})
        """, """
        def parse_kv(text):
            out = {}
            for part in text.split(";"):
                if part.strip():
                    key, _, value = part.partition("=")
                    out[key.strip()] = value.strip()
            return out
        """, """
        def parse_kv(text):
            out = {}
            for part in text.split(";"):
                if part.strip():
                    key, value = part.split("=")[:2]
                    out[key.strip()] = value.strip()
            return out
        """),
)

# ── Tool workflows ───────────────────────────────────────────────────────────

H_CALENDAR = {"events": [
    _event(1, "Board prep", "2026-04-02", "14:00", "15:00", ("cfo@example.test",)),
    _event(2, "Old standup", "2026-04-01", "09:00", "09:15", ("team@example.test",)),
    _event(3, "Old standup", "2026-04-02", "09:00", "09:15", ("team@example.test",)),
    _event(4, "Standup", "2026-04-03", "09:30", "09:45", ("team@example.test",)),
    _event(5, "Board prep", "2026-04-09", "14:00", "15:00", ("cfo@example.test",)),
]}
H_MAILBOX = {"messages": [
    _mail("m_1", "omar@client.example", "Shipment 4471", "Hi, any news on shipment 4471? Omar", "2026-04-01"),
    _mail("m_2", "orders@kestrel.example", "Dispatched: 4471", "Order 4471 shipped. Tracking number TRK-55821-K.",
          "2026-04-01"),
    _mail("m_3", "promo@deals.example", "Spring sale", "Save now", "2026-04-02"),
    _mail("m_4", "promo@deals.example", "Last chance", "Ends today", "2026-04-03"),
    _mail("m_5", "ines@example.test", "Supplier visit",
          "Kestrel is visiting on 2026-04-07 from 10:00 to 11:30, please block the time. Ines", "2026-04-02"),
]}
H_TRACKER = {"issues": [
    _issue(2, "Request timeout on upload", labels=("bug",)),
    _issue(4, "Old onboarding copy", labels=("stale",)),
    _issue(5, "Unused icon set", labels=("stale",), assignee="bo"),
    _issue(6, "Timeout settings page", labels=("feature",)),
    _issue(9, "Broken link in footer", labels=("bug",), state="closed"),
    _issue(10, "Stale cache after logout", labels=("bug",)),
]}


def _tw_task(number: int, prompt: str, apps: dict[str, Any], tools: tuple[str, ...], checks: list[dict[str, Any]],
             reference: tuple[dict[str, Any], ...], decoy: tuple[dict[str, Any], ...], *,
             verifier: Verifier | None = None, answer: str = "done", wrong_answer: str = "done") -> PackTask:
    return PackTask(
        id=f"htool_{number:02d}", pack="tool_workflows", split="hidden",
        prompt=prompt + ("" if verifier is not None else " When you are finished, reply with: done"),
        verifier=verifier or Verifier("app_state", checks),
        reference=Solution(answer=answer, calls=reference), decoy=Solution(answer=wrong_answer, calls=decoy),
        apps=apps, tools=tools, max_steps=10,
    )


_E, _M, _I = "calendar.events", "mail.messages", "issues.issues"
_TW_TASKS = (
    _tw_task(1, "Move the Board prep on 2026-04-02 to 2026-04-03 at the same time.", {"calendar": H_CALENDAR},
             ("calendar",),
             [_count(_E, 1, title="Board prep", date="2026-04-03", start="14:00"),
              _count(_E, 1, title="Board prep", date="2026-04-09"), _count(_E, 0, title="Board prep", date="2026-04-02")],
             (_call("calendar_update_event", event_id="evt_1", date="2026-04-03"),),
             (_call("calendar_update_event", event_id="evt_5", date="2026-04-03"),)),
    _tw_task(2, "Delete every Old standup event. Keep the new Standup.", {"calendar": H_CALENDAR}, ("calendar",),
             [_count(_E, 0, title="Old standup"), _count(_E, 1, title="Standup"), _count(_E, 3)],
             (_call("calendar_delete_event", event_id="evt_2"), _call("calendar_delete_event", event_id="evt_3")),
             (_call("calendar_delete_event", event_id="evt_2"), _call("calendar_delete_event", event_id="evt_4"))),
    _tw_task(3, "Omar asked about shipment 4471. Reply to him with the tracking number from Kestrel's email, using "
                "the subject Re: Shipment 4471.", {"mail": H_MAILBOX}, ("mail",),
             [_count(_M, 1, folder="sent", to="omar@client.example", body={"contains": "TRK-55821-K"})],
             (_call("mail_send", to="omar@client.example", subject="Re: Shipment 4471",
                    body="Hi Omar, it shipped. Tracking number: TRK-55821-K."),),
             (_call("mail_send", to="orders@kestrel.example", subject="Re: Shipment 4471",
                    body="Tracking number: TRK-55821-K."),)),
    _tw_task(4, "Archive the promotional emails from promo@deals.example. Leave the rest of the inbox alone.",
             {"mail": H_MAILBOX}, ("mail",),
             [_count(_M, 0, folder="inbox", **{"from": "promo@deals.example"}), _count(_M, 3, folder="inbox")],
             (_call("mail_archive", message_id="m_3"), _call("mail_archive", message_id="m_4")),
             (_call("mail_archive", message_id="m_3"),)),
    _tw_task(5, "Close every open issue labelled stale.", {"issues": H_TRACKER}, ("issues",),
             [_count(_I, 0, state="open", labels=["stale"]), _count(_I, 1, id=10, state="open"),
              _count(_I, 3, state="closed")],
             (_call("issues_update", issue_id="4", state="closed"), _call("issues_update", issue_id="5", state="closed")),
             (_call("issues_update", issue_id="4", state="closed"), _call("issues_update", issue_id="10", state="closed"))),
    _tw_task(6, "Add the label backend to the bug about request timeouts.", {"issues": H_TRACKER}, ("issues",),
             [_count(_I, 1, id=2, labels=["bug", "backend"]), _count(_I, 1, id=6, labels=["feature"])],
             (_call("issues_update", issue_id="2", add_labels=["backend"]),),
             (_call("issues_update", issue_id="6", add_labels=["backend"]),)),
    _tw_task(7, "Ines asked me to block time for the supplier visit. Add it to my calendar as Kestrel visit.",
             {"calendar": H_CALENDAR, "mail": H_MAILBOX}, ("calendar", "mail"),
             [_count(_E, 1, title="Kestrel visit", date="2026-04-07", start="10:00", end="11:30"), _count(_E, 6)],
             (_call("calendar_create_event", title="Kestrel visit", date="2026-04-07", start="10:00", end="11:30"),),
             (_call("calendar_create_event", title="Kestrel visit", date="2026-04-07", start="10:00", end="11:00"),)),
    _tw_task(8, "How many open issues are labelled bug? Reply with only the number.", {"issues": H_TRACKER},
             ("issues",), [], (_call("issues_list", state="open", label="bug"),),
             (_call("issues_list", state="all", label="bug"),),
             verifier=Verifier("answer", 2, match="number"), answer="2", wrong_answer="3"),
)

TASKS: tuple[PackTask, ...] = _DOC_TASKS + _ST_TASKS + _CODE_TASKS + _TW_TASKS

seal_module(__name__, Seal(name=__name__, readers=frozenset({"row_bot.learning.audit"})))
