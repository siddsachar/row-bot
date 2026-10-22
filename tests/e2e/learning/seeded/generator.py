"""The seeded people's histories: a seed in, the same history out (S1).

Each made-up person (``people/<id>/``: ``person.json``, ``apps.json`` and the workspace documents
under ``files/``) gets, from one seed,

- a **30-day past**: conversations (their requests, Row-Bot's tool calls, results and answers,
  a few mistakes the person corrected in their next message), the documents and data that arrived
  in the workspace, and the fake apps' state, ready for the past-chat import (partial fidelity). The
  replies are the main model's own words where ``people/<id>/past_replies.json`` keeps one for the
  seed (written once by ``phrasing.py``; a template's few words otherwise: a model trained on those
  learns to answer everything in a few words);
- a **live stream**: requests over simulated days, each with the person's hidden intent and a
  checkable right outcome (a pack-style :class:`~row_bot.learning.packs.PackTask`: the product's own
  verifiers decide, and every request carries a reference that passes and a decoy that fails).

The world moves on in order: each morning brings its data (donations, bank lines, bug-log rows,
delivery notes, receipts, level files, newsletters, new tickets), and after each request the world
takes the request's *right* outcome, so the next request starts from where a correct assistant would
have left it. Days are fixed dates (the last past day is :data:`LAST_PAST_DAY`), so the history does
not depend on when it is generated. Nothing here imports more of Row-Bot than the pack format, the
fake apps and the kinds of work.
"""

from __future__ import annotations

import ast
import csv
import hashlib
import io
import json
import operator
import random
import re
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from tests.e2e.learning.seeded import coding_bank

PEOPLE_DIR = Path(__file__).resolve().parent / "people"
FORMAT = "row_bot.learning.seeded_history"
VERSION = 1
PAST_DAYS = 30
#: The last day of the past; live day 1 is the day after (a Monday).
LAST_PAST_DAY = date(2026, 11, 1)
#: How far back the data logs (donations, bank lines, bug log) start before live day 1.
LEDGER_DAYS = 75

KINDS_OF_WORK = ("document_lookups", "structured_transforms", "small_coding", "tool_workflows", "other")
#: Which pack format each template's check uses (the kind it is *meant* as; routing classifies the words).
TEMPLATE_PACK = {
    "fact_lookup": "document_lookups", "ledger_total": "document_lookups", "ledger_json": "structured_transforms",
    "note_to_csv": "structured_transforms", "calendar_add": "tool_workflows", "mail_send": "tool_workflows",
    "mail_archive": "tool_workflows", "issue_close": "tool_workflows", "issue_label": "tool_workflows",
    "arithmetic": "document_lookups", "move_file": "structured_transforms", "code_function": "small_coding",
}
TEMPLATE_KIND = {**TEMPLATE_PACK, "arithmetic": "other", "move_file": "other"}


class GeneratorError(ValueError):
    """A person's data that the generator can't use."""


# ── Records ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ToolStep:
    """One tool call of a past turn and what it returned."""

    name: str
    args: dict[str, Any]
    result: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "args": self.args, "result": self.result}


@dataclass(frozen=True)
class Request:
    """One request of a person, past or live, and how to tell it was done right."""

    id: str
    person: str
    day: int  # live: 1, 2, ...; past: -30 ... -1
    order: int
    template: str
    intended: str  # the kind of work it is meant as
    prompt: str
    intent: str  # what the person really wants (the simulated person's brief; never sent as is)
    task: Any  # PackTask: verifier, reference, decoy, the input files and the apps before it
    inputs: dict[str, str]  # workspace files placed (or refreshed) before the request
    approvals: tuple[str, ...] = ()  # workspace paths the person approves an action on (a move)
    # Past only: how it went (tool steps, the answer; a mistake and the person's correction first).
    steps: tuple[ToolStep, ...] = ()
    answer: str = ""
    mistake: tuple[tuple[ToolStep, ...], str, str] | None = None

    @property
    def date(self) -> date:
        return day_date(self.day)

    def to_dict(self) -> dict[str, Any]:
        found = {
            "id": self.id, "person": self.person, "day": self.day, "date": self.date.isoformat(), "order": self.order,
            "template": self.template, "intended": self.intended, "prompt": self.prompt, "intent": self.intent,
            "task": self.task.to_dict(), "inputs": self.inputs, "approvals": list(self.approvals),
        }
        if self.day < 0:
            found["steps"] = [step.to_dict() for step in self.steps]
            found["answer"] = self.answer
            if self.mistake is not None:
                steps, answer, correction = self.mistake
                found["mistake"] = {"steps": [step.to_dict() for step in steps], "answer": answer,
                                    "correction": correction}
        return found


@dataclass(frozen=True)
class Conversation:
    """A past conversation: one to three requests on one day."""

    id: str
    day: int
    title: str
    requests: tuple[Request, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "day": self.day, "date": day_date(self.day).isoformat(), "title": self.title,
                "requests": [request.to_dict() for request in self.requests]}


@dataclass(frozen=True)
class History:
    """One person's past and live stream for one seed."""

    person: dict[str, Any]
    seed: int
    days: int
    past: tuple[Conversation, ...]
    stream: tuple[Request, ...]
    # The workspace and apps on live day 1 morning (before the first live request).
    start_files: dict[str, str] = field(default_factory=dict)
    start_apps: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"format": FORMAT, "version": VERSION, "person": self.person["id"], "seed": self.seed,
                "days": self.days, "past": [item.to_dict() for item in self.past],
                "stream": [item.to_dict() for item in self.stream], "start_files": self.start_files,
                "start_apps": self.start_apps}

    def fingerprint(self) -> str:
        """SHA-256 of the whole history (same seed, same history)."""

        text = json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def by_day(self) -> dict[int, list[Request]]:
        found: dict[int, list[Request]] = {}
        for request in self.stream:
            found.setdefault(request.day, []).append(request)
        return found


def day_date(day: int) -> date:
    """The calendar date of a simulated day (past days are negative, live days from 1)."""

    return LAST_PAST_DAY + timedelta(days=day if day > 0 else day + 1)


# ── People ───────────────────────────────────────────────────────────────────


def people() -> list[str]:
    """The seeded people's ids, in a fixed order."""

    return sorted(item.name for item in PEOPLE_DIR.iterdir() if (item / "person.json").is_file())


def _text(path: Path) -> str:
    # Checked out with CRLF on Windows: the history is the same on every system.
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def load_person(person_id: str) -> dict[str, Any]:
    """A person's ``person.json``, their ``apps.json`` and the documents in their workspace."""

    folder = PEOPLE_DIR / person_id
    if not (folder / "person.json").is_file():
        raise GeneratorError(f"no seeded person {person_id!r}")
    person = json.loads(_text(folder / "person.json"))
    if person.get("id") != person_id or person.get("fictional") is not True:
        raise GeneratorError(f"{person_id}: person.json names another person or isn't marked fictional")
    person["apps"] = json.loads(_text(folder / "apps.json"))
    files: dict[str, str] = {}
    root = folder / "files"
    for item in sorted(root.rglob("*")):
        if item.is_file():
            files[item.relative_to(root).as_posix()] = _text(item)
    person["files"] = files
    unknown = set(person.get("work") or {}) - set(TEMPLATE_PACK)
    if unknown:
        raise GeneratorError(f"{person_id}: unknown work templates {sorted(unknown)}")
    return person


# ── Small helpers ────────────────────────────────────────────────────────────

_OPERATORS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}


def evaluate(expression: str, values: dict[str, float]) -> float:
    """``a*b+c`` with the given values: + - * / and parentheses only."""

    def walk(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.BinOp) and type(node.op) in _OPERATORS:
            return _OPERATORS[type(node.op)](walk(node.left), walk(node.right))
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.Name) and node.id in values:
            return float(values[node.id])
        raise GeneratorError(f"unsupported expression {expression!r}")

    return walk(ast.parse(expression, mode="eval"))


def _number(value: float, decimals: int) -> str:
    if decimals <= 0:
        return str(int(round(value)))
    return f"{value:.{decimals}f}"


#: ``people/<id>/past_replies.json``: the past's replies in the main model's own words (``phrasing.py``).
REPLIES_FILE = "past_replies.json"
REPLIES_FORMAT = "row_bot.learning.seeded_past_replies"
_FACT = re.compile(r"[^\s,;()]*[0-9@/#][^\s,;()]*")


def facts_of(template: str, answer: str) -> list[str]:
    """What a reply must keep of a template reply: every token with a digit, ``@``, ``/`` or ``#`` (numbers,
    dates, paths, addresses, issue numbers) and the right answer itself."""

    found = [token.strip(".:!?'\"*`") for token in _FACT.findall(template)]
    if answer and not any(answer in token for token in found):
        found.append(answer)
    return [token for token in found if token]


def keeps_facts(template: str, reply: str, answer: str) -> bool:
    """Whether ``reply`` says everything ``template`` does (thousands separators and Markdown aside)."""

    plain = re.sub(r"(?<=\d),(?=\d{3}\b)", "", reply).replace("*", "").replace("`", "").lower()
    return bool(reply.strip()) and all(fact.lower() in plain for fact in facts_of(template, answer))


def answer_of(request: Request) -> str:
    """The right answer itself, for a request checked by its answer (empty for one checked otherwise)."""

    return str(request.task.reference.answer or "") if request.task.verifier.kind == "answer" else ""


def past_replies(person_id: str, seed: int) -> dict[str, dict[str, str]]:
    """The kept replies for this person and seed (none for another seed: its past differs)."""

    path = PEOPLE_DIR / person_id / REPLIES_FILE
    try:
        data = json.loads(_text(path))
    except (OSError, ValueError):
        return {}
    if data.get("format") != REPLIES_FORMAT or int(data.get("seed") or 0) != int(seed):
        return {}
    return dict(data.get("replies") or {})


def _calculated(value: float) -> str:
    """A result as Row-Bot's calculator prints it (no trailing ``.0`` on a whole number)."""

    return str(int(value)) if float(value).is_integer() else str(value)


def _pick_range(rng: random.Random, spec: list[float]) -> float:
    low, high, step = (float(item) for item in spec)
    count = int(round((high - low) / step)) + 1
    return round(low + step * rng.randrange(count), 6)


def _csv_text(header: list[str], rows: list[list[str]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue()


def _weighted(rng: random.Random, weights: dict[str, float]) -> str:
    names = sorted(weights)
    return rng.choices(names, weights=[float(weights[name]) for name in names], k=1)[0]


def _say(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


# ── The world (what a correct assistant would have left) ─────────────────────


class World:
    """A person's workspace files and fake apps, moved on day by day and request by request."""

    def __init__(self, person: dict[str, Any], rng: random.Random) -> None:
        from row_bot.learning.packs.fake_apps import FakeApps

        self.person = person
        self.rng = rng
        self.files: dict[str, str] = dict(person["files"])
        self.apps = FakeApps(person["apps"])
        self.ledger_rows: list[list[str]] = []
        self.notes: list[str] = []  # note files that arrived, oldest first
        self.converted: set[str] = set()
        self.moved: set[str] = set()
        self.code_uses: dict[str, int] = {}
        self.title_uses = 0
        self._ledger_built = False

    # ── Ledger ────────────────────────────────────────────────────────

    def build_ledger(self) -> None:
        """Every row of the data log from its start to the end of time (filtered by day when read)."""

        spec = self.person.get("ledger")
        if not spec or self._ledger_built:
            return
        rng = random.Random(f"{self.rng.random()}-ledger")
        decimals = int(spec.get("decimals", 2))
        first = day_date(1) - timedelta(days=LEDGER_DAYS)
        rows: list[list[str]] = []
        for offset in range(LEDGER_DAYS + 400):
            when = first + timedelta(days=offset)
            for category in sorted(spec["categories"]):
                item = spec["categories"][category]
                low, high = item["per_day"]
                for _ in range(rng.randint(int(low), int(high))):
                    amount = rng.uniform(float(item["amount"][0]), float(item["amount"][1]))
                    party = rng.choice(item["parties"])
                    values = {spec["party_column"]: party, spec["category_column"]: category,
                              spec["amount_column"]: _number(amount, decimals), "date": when.isoformat()}
                    rows.append([values[column] for column in spec["columns"]])
        self.ledger_rows = rows
        self._ledger_built = True

    def matching(self, collection: str, where: dict[str, Any]) -> int:
        """How many items of ``app.collection`` match ``where`` now (the app checks' own matching)."""

        from row_bot.learning.packs.verify import _matches  # noqa: PLC2701 - the verifier's own rule

        app, _, name = collection.partition(".")
        items = list(((self.apps.state().get(app) or {}).get(name)) or [])
        return sum(1 for item in items if all(_matches(item.get(key), wanted) for key, wanted in where.items()))

    def ledger_until(self, when: date) -> str:
        spec = self.person["ledger"]
        rows = [row for row in self.ledger_rows if row[spec["columns"].index("date")] <= when.isoformat()]
        return _csv_text(list(spec["columns"]), rows)

    def ledger_window(self, start: date, end: date) -> dict[str, float]:
        spec = self.person["ledger"]
        columns = spec["columns"]
        totals: dict[str, float] = {}
        for row in self.ledger_rows:
            when = row[columns.index("date")]
            if start.isoformat() <= when <= end.isoformat():
                category = row[columns.index(spec["category_column"])]
                totals[category] = totals.get(category, 0.0) + float(row[columns.index(spec["amount_column"])])
        decimals = int(spec.get("decimals", 2))
        return {key: round(value, max(decimals, 0)) for key, value in sorted(totals.items())}

    # ── Mornings ──────────────────────────────────────────────────────

    def morning(self, day: int) -> None:
        """What arrives this morning: the data log grows, maybe a note, newsletters, a ticket."""

        self.build_ledger()
        if self.person.get("ledger"):
            self.files[self.person["ledger"]["path"]] = self.ledger_until(day_date(day))
        if self.person.get("notes") and self.rng.random() < 0.6:
            self.new_note(day)
        mail = self.person.get("mail") or {}
        for letter in mail.get("newsletters") or ():
            if self.rng.random() < 0.3:
                self._mail_in(letter["from"], letter["subject"], f"News for {day_date(day).isoformat()}.", day)
        issues = self.person.get("issues") or {}
        if issues.get("new_titles") and self.rng.random() < 0.3:
            self.new_ticket(issues["new_titles"])

    def _mail_in(self, sender: str, subject: str, body: str, day: int) -> str:
        state = self.apps._state  # noqa: SLF001 - the world owns its apps
        number = 1 + sum(1 for message in state["mail"]["messages"] if message["id"].startswith("msg_"))
        message = {"id": f"msg_{number}", "folder": "inbox", "from": sender, "to": "me@example.test",
                   "subject": subject, "body": body, "date": day_date(day).isoformat()}
        state["mail"]["messages"].append(message)
        return message["id"]

    def new_ticket(self, titles: list[str]) -> str:
        state = self.apps._state  # noqa: SLF001
        issues = state["issues"]["issues"]
        number = 1 + max((int(issue["id"]) for issue in issues), default=0)
        title = titles[self.title_uses % len(titles)]
        if self.title_uses >= len(titles):
            title = f"{title} (again)"
        self.title_uses += 1
        issues.append({"id": str(number), "title": title, "state": "open", "labels": [], "assignee": "", "comments": []})
        return str(number)

    def new_note(self, day: int) -> str:
        spec = self.person["notes"]
        when = day_date(day).isoformat()
        if spec.get("format") == "json_tiles":
            name = f"{spec['prefix']}-{when}"
            path = f"{spec['dir']}/{name}.json"
            if path in self.files:
                return path
            low, high = spec["tiles"]
            seen: set[tuple[int, int]] = set()
            tiles = []
            while len(tiles) < self.rng.randint(int(low), int(high)):
                x, y = self.rng.randrange(16), self.rng.randrange(12)
                if (x, y) in seen:
                    continue
                seen.add((x, y))
                tiles.append({"x": x, "y": y, "type": self.rng.choice(spec["types"])})
            self.files[path] = json.dumps({"name": name, "tiles": tiles}, indent=1) + "\n"
        else:
            path = f"{spec['dir']}/{spec['prefix']}-{when}.txt"
            if path in self.files:
                return path
            decimals = int(spec.get("decimals", 0))
            items = self.rng.sample(spec["items"], k=min(len(spec["items"]), self.rng.randint(3, 6)))
            lines = [f"{spec['title']} - {when}", ""]
            for item in items:
                quantity = self.rng.uniform(*item["quantity"]) if decimals else self.rng.randint(*item["quantity"])
                lines.append(spec["line"].format(item=item["item"], unit=item["unit"],
                                                 quantity=_number(float(quantity), decimals)))
            self.files[path] = "\n".join(lines) + "\n"
        self.notes.append(path)
        return path

    def note_rows(self, path: str) -> tuple[list[str], list[list[str]]]:
        """The note's CSV header and rows, as the request asks for them."""

        spec = self.person["notes"]
        if spec.get("format") == "json_tiles":
            tiles = json.loads(self.files[path])["tiles"]
            return list(spec["columns"]), [[str(tile[column]) for column in spec["columns"]] for tile in tiles]
        columns = spec["columns"]
        mapping = columns if isinstance(columns, dict) else {name: name for name in columns}
        rows = []
        for line in self.files[path].split("\n")[2:]:
            if not line.strip():
                continue
            rows.append([_parse_note_line(spec, line)[source] for source in mapping.values()])
        return list(mapping), rows


def _parse_note_line(spec: dict[str, Any], line: str) -> dict[str, str]:
    """The note line's item, unit and quantity (the line format is the person's)."""

    for item in spec["items"]:
        pattern = spec["line"].format(item=item["item"], unit=item["unit"], quantity="\x00")
        head, _, tail = pattern.partition("\x00")
        if line.startswith(head) and line.endswith(tail):
            quantity = line[len(head): len(line) - len(tail)] if tail else line[len(head):]
            return {"item": item["item"], "unit": item["unit"], "quantity": quantity}
    raise GeneratorError(f"a note line the generator didn't write: {line!r}")


# ── Templates: each builds one request from the world, or None when it can't today ──


class Builder:
    """Makes requests of one person's templates from the world and applies their right outcome."""

    def __init__(self, person: dict[str, Any], world: World, rng: random.Random) -> None:
        self.person = person
        self.world = world
        self.rng = rng

    # Each template returns (prompt, intent, verifier, reference, decoy, inputs, tools, approvals, steps, answer).

    def build(self, template: str, day: int) -> dict[str, Any] | None:
        return getattr(self, f"_t_{template}")(day)

    def _phrase(self, options: list[str], **values: Any) -> str:
        return self.rng.choice(options).format(**values)

    def _t_fact_lookup(self, day: int) -> dict[str, Any] | None:
        from row_bot.learning.packs import Solution, Verifier

        fact = self.rng.choice(self.person["facts"])
        prompt = self.rng.choice(fact["phrasings"])
        answer = _say(fact["answer"])
        steps = tuple(ToolStep("workspace_read_file", {"file_path": path}, self.world.files[path])
                      for path in fact["files"])
        return {
            "prompt": prompt, "intent": fact["intent"],
            "verifier": Verifier("answer", fact["answer"], fact["match"]),
            "reference": Solution(answer=answer), "decoy": Solution(answer=str(fact["decoy"])),
            "inputs": {path: self.world.files[path] for path in fact["files"]}, "tools": ("files",),
            "steps": steps, "answer": self._answer_sentence(answer),
        }

    @staticmethod
    def _answer_sentence(answer: str) -> str:
        return f"It's {answer}."

    def _window(self, day: int) -> tuple[date, date]:
        end = day_date(day) - timedelta(days=self.rng.randint(1, 9))
        return end - timedelta(days=self.rng.choice((2, 4, 6))), end

    def _t_ledger_total(self, day: int) -> dict[str, Any] | None:
        from row_bot.learning.packs import Solution, Verifier

        spec = self.person["ledger"]
        for _ in range(8):
            start, end = self._window(day)
            totals = self.world.ledger_window(start, end)
            category = self.rng.choice(sorted(spec["categories"]))
            if totals.get(category):
                break
        else:
            return None
        decimals = int(spec.get("decimals", 2))
        answer = _number(totals[category], decimals)
        wider = self.world.ledger_window(start, end + timedelta(days=1)).get(category, 0.0)
        others = sum(totals.values())
        decoy_value = wider if abs(wider - totals[category]) > 1e-9 else others
        if abs(decoy_value - totals[category]) < 1e-9:
            return None
        path = spec["path"]
        return {
            "prompt": self._phrase(spec["phrasings_total"], category=category, start=start.isoformat(),
                                   end=end.isoformat()),
            "intent": f"The sum of the {category} rows dated {start.isoformat()} to {end.isoformat()} inclusive.",
            "verifier": Verifier("answer", float(answer), "number"),
            "reference": Solution(answer=answer), "decoy": Solution(answer=_number(decoy_value, decimals)),
            "inputs": {path: self.world.files[path]}, "tools": ("files",),
            "steps": (ToolStep("workspace_read_file", {"file_path": path}, self.world.files[path]),),
            "answer": f"The total is {answer}.",
        }

    def _t_ledger_json(self, day: int) -> dict[str, Any] | None:
        from row_bot.learning.packs import Solution, Verifier

        spec = self.person["ledger"]
        start, end = self._window(day)
        totals = self.world.ledger_window(start, end)
        shifted = self.world.ledger_window(start + timedelta(days=1), end)
        if not totals or totals == shifted:
            return None
        prompt =self._phrase(spec["phrasings_json"], start=start.isoformat(), end=end.isoformat())
        out = re.search(r"[\w./-]+\.json", prompt).group(0)
        path = spec["path"]
        written = json.dumps(totals, indent=1) + "\n"
        return {
            "prompt": prompt,
            "intent": f"A JSON object of {spec['category_column']} to total for {start.isoformat()} to "
                      f"{end.isoformat()} inclusive, in {out}.",
            "verifier": Verifier("file", totals, "json", path=out),
            "reference": Solution(answer=f"Wrote {out}.", files={out: written}),
            "decoy": Solution(answer=f"Wrote {out}.", files={out: json.dumps(shifted) + "\n"}),
            "inputs": {path: self.world.files[path]}, "tools": ("files",),
            "steps": (ToolStep("workspace_read_file", {"file_path": path}, self.world.files[path]),
                      ToolStep("workspace_write_file", {"file_path": out, "text": written},
                               f"File written successfully to {out}.")),
            "answer": f"Done: {out} has the totals per {spec['category_column']}.",
            "writes": {out: written},
        }

    def _t_note_to_csv(self, day: int) -> dict[str, Any] | None:
        from row_bot.learning.packs import Solution, Verifier

        spec = self.person["notes"]
        waiting = [path for path in self.world.notes if path not in self.world.converted and path in self.world.files]
        path = waiting[-1] if waiting else self.world.new_note(day)
        header, rows = self.world.note_rows(path)
        stem = Path(path).stem
        out = f"{spec['out_dir']}/{stem}.csv"
        text = _csv_text(header, rows)
        decoy = _csv_text(header, rows[:-1]) if len(rows) > 1 else _csv_text(header[::-1], rows)
        self.world.converted.add(path)
        return {
            "prompt": self._phrase(spec["phrasings"], note=path, out=out),
            "intent": f"Every line of {path} as a row of {out}, header {','.join(header)}, values as written.",
            "verifier": Verifier("file", text, "csv_rows", path=out),
            "reference": Solution(answer=f"Wrote {out}.", files={out: text}),
            "decoy": Solution(answer=f"Wrote {out}.", files={out: decoy}),
            "inputs": {path: self.world.files[path]}, "tools": ("files",),
            "steps": (ToolStep("workspace_read_file", {"file_path": path}, self.world.files[path]),
                      ToolStep("workspace_write_file", {"file_path": out, "text": text},
                               f"File written successfully to {out}.")),
            "answer": f"Done: {out} has {len(rows)} rows.",
            "writes": {out: text},
        }

    def _future_date(self, day: int, weekend: bool) -> date:
        for _ in range(60):
            when = day_date(day) + timedelta(days=self.rng.randint(1, 14))
            if (when.weekday() >= 5) == weekend:
                return when
        raise GeneratorError("no date")

    def _t_calendar_add(self, day: int) -> dict[str, Any] | None:
        from row_bot.learning.packs import Solution, Verifier

        spec = self.person["calendar"]
        who = self.rng.choice(spec["whos"])
        what = self.rng.choice(spec["whats"])
        when = self._future_date(day, bool(what.get("weekend")))
        title = what["title"].format(who=who)
        call = {"tool": "calendar_create_event",
                "args": {"title": title, "date": when.isoformat(), "start": what["start"], "end": what["end"]}}
        wrong = dict(call["args"], date=(when + timedelta(days=1)).isoformat())
        where = {"title": {"contains": who}, "date": when.isoformat(), "start": what["start"]}
        checks = [{"in": "calendar.events", "where": where, "count": self.world.matching("calendar.events", where) + 1}]
        return {
            "prompt": self._phrase(spec["phrasings"], who=who, what=what["name"], date=when.isoformat()),
            "intent": f"One calendar event for {who} on {when.isoformat()} from {what['start']} to {what['end']}.",
            "verifier": Verifier("app_state", checks),
            "reference": Solution(answer="Added.", calls=(call,)),
            "decoy": Solution(answer="Added.", calls=({"tool": "calendar_create_event", "args": wrong},)),
            "inputs": {}, "tools": ("calendar",), "calls": (call,), "answer": f"Added {title} on {when.isoformat()}.",
        }

    def _t_mail_send(self, day: int) -> dict[str, Any] | None:
        from row_bot.learning.packs import Solution, Verifier

        spec = self.person.get("mail") or {}
        if not spec.get("contacts"):
            return None
        contact = self.rng.choice(spec["contacts"])
        quantity = self.rng.randint(2, 40) if "INV" not in contact["must"] else self.rng.randint(1040, 1399)
        purpose = contact["purpose"].format(quantity=quantity)
        must = contact["must"].format(quantity=quantity)
        others = [item for item in spec["contacts"] if item["email"] != contact["email"]]
        wrong_to = (others[0]["email"] if others else "someone@example.test")
        call = {"tool": "mail_send", "args": {"to": contact["email"], "subject": purpose[:1].upper() + purpose[1:60],
                                              "body": f"Hello,\n\nA quick note to {purpose}.\n\nThanks"}}
        if must.lower() not in call["args"]["body"].lower():
            call["args"]["body"] += f"\n({must})"
        where = {"folder": "sent", "to": contact["email"], "body": {"contains": must}}
        checks = [{"in": "mail.messages", "where": where, "count": self.world.matching("mail.messages", where) + 1}]
        return {
            "prompt": self._phrase(spec["phrasings"], name=contact["name"], email=contact["email"], purpose=purpose),
            "intent": f"One email to {contact['email']} that says '{must}'.",
            "verifier": Verifier("app_state", checks),
            "reference": Solution(answer="Sent.", calls=(call,)),
            "decoy": Solution(answer="Sent.", calls=({"tool": "mail_send", "args": dict(call["args"], to=wrong_to)},)),
            "inputs": {}, "tools": ("mail",), "calls": (call,), "answer": f"Sent to {contact['email']}.",
        }

    def _t_mail_archive(self, day: int) -> dict[str, Any] | None:
        from row_bot.learning.packs import Solution, Verifier

        spec = self.person.get("mail") or {}
        letters = spec.get("newsletters") or []
        if not letters:
            return None
        letter = self.rng.choice(letters)
        state = self.world.apps._state  # noqa: SLF001
        inbox = [message for message in state["mail"]["messages"]
                 if message["folder"] == "inbox" and message["from"] == letter["from"]]
        while len(inbox) < 2:
            self.world._mail_in(letter["from"], letter["subject"], "More news.", day)  # noqa: SLF001
            inbox = [message for message in state["mail"]["messages"]
                     if message["folder"] == "inbox" and message["from"] == letter["from"]]
        archived = sum(1 for message in state["mail"]["messages"]
                       if message["folder"] == "archive" and message["from"] == letter["from"])
        calls = tuple({"tool": "mail_archive", "args": {"message_id": message["id"]}} for message in inbox)
        checks = [{"in": "mail.messages", "where": {"folder": "inbox", "from": letter["from"]}, "count": 0},
                  {"in": "mail.messages", "where": {"folder": "archive", "from": letter["from"]},
                   "count": archived + len(inbox)}]
        return {
            "prompt": self._phrase(spec["archive_phrasings"], sender=letter["from"]),
            "intent": f"Every inbox message from {letter['from']} archived ({len(inbox)} now), nothing else touched.",
            "verifier": Verifier("app_state", checks),
            "reference": Solution(answer="Archived.", calls=calls),
            "decoy": Solution(answer="Archived.", calls=calls[-1:]),
            "inputs": {}, "tools": ("mail",), "calls": (({"tool": "mail_list", "args": {"folder": "inbox"}},) + calls),
            "answer": f"Archived {len(inbox)} emails from {letter['from']}.",
        }

    def _open_issue(self) -> dict[str, Any] | None:
        issues = [issue for issue in self.world.apps._state["issues"]["issues"]  # noqa: SLF001
                  if issue["state"] == "open"]
        if not issues:
            spec = self.person.get("issues") or {}
            if not spec.get("new_titles"):
                return None
            self.world.new_ticket(spec["new_titles"])
            return self._open_issue()
        return self.rng.choice(issues)

    def _t_issue_close(self, day: int) -> dict[str, Any] | None:
        from row_bot.learning.packs import Solution, Verifier

        spec = self.person.get("issues") or {}
        issue = self._open_issue()
        if issue is None:
            return None
        fix = self.rng.choice(spec["fixes"])
        others = [item for item in self.world.apps._state["issues"]["issues"]  # noqa: SLF001
                  if item["state"] == "open" and item["id"] != issue["id"]]
        calls = ({"tool": "issues_update", "args": {"issue_id": issue["id"], "state": "closed"}},
                 {"tool": "issues_comment", "args": {"issue_id": issue["id"], "body": fix}})
        wrong = others[0]["id"] if others else issue["id"]
        decoy_calls = ({"tool": "issues_update", "args": {"issue_id": wrong, "state": "closed"}},) if others else ()
        checks = [{"in": "issues.issues", "where": {"id": issue["id"], "state": "closed"}, "count": 1},
                  {"in": "issues.issues", "where": {"id": issue["id"], "comments": {"contains": fix}}, "count": 1}]
        return {
            "prompt": self._phrase(spec["phrasings"], id=issue["id"], title=issue["title"], fix=fix),
            "intent": f"Issue #{issue['id']} closed with a comment saying '{fix}'; no other issue changed.",
            "verifier": Verifier("app_state", checks),
            "reference": Solution(answer="Closed.", calls=calls),
            "decoy": Solution(answer="Closed.", calls=decoy_calls),
            "inputs": {}, "tools": ("issues",), "calls": calls, "answer": f"Closed #{issue['id']} with your comment.",
        }

    def _t_issue_label(self, day: int) -> dict[str, Any] | None:
        from row_bot.learning.packs import Solution, Verifier

        spec = self.person.get("issues") or {}
        issue = self._open_issue()
        if issue is None or not spec.get("labels"):
            return None
        label = self.rng.choice(spec["labels"])
        assignee = self.rng.choice(spec["assignees"])
        wrong = next(item for item in spec["assignees"] if item != assignee)
        call = {"tool": "issues_update", "args": {"issue_id": issue["id"], "add_labels": [label],
                                                  "assignee": assignee}}
        where = {"id": issue["id"], "labels": {"contains": label}, "assignee": assignee}
        if self.world.matching("issues.issues", where):
            return None  # already so: nothing to ask
        checks = [{"in": "issues.issues", "where": where, "count": 1}]
        return {
            "prompt": self._phrase(spec["label_phrasings"], id=issue["id"], label=label, assignee=assignee),
            "intent": f"Issue #{issue['id']} labelled {label} and assigned to {assignee}.",
            "verifier": Verifier("app_state", checks),
            "reference": Solution(answer="Done.", calls=(call,)),
            "decoy": Solution(answer="Done.", calls=({"tool": "issues_update",
                                                      "args": dict(call["args"], assignee=wrong)},)),
            "inputs": {}, "tools": ("issues",), "calls": (call,), "answer": f"#{issue['id']}: {label}, {assignee}.",
        }

    def _t_arithmetic(self, day: int) -> dict[str, Any] | None:
        from row_bot.learning.packs import Solution, Verifier

        spec = self.rng.choice(self.person["arithmetic"])
        names = [name for name in ("a", "b", "c") if name in spec]
        values = {name: _pick_range(self.rng, spec[name]) for name in names}
        decimals = int(spec.get("decimals", 2))
        value = evaluate(spec["answer"], values)
        last = names[-1]
        slipped = evaluate(spec["answer"], {**values, last: values[last] + float(spec[last][2])})
        answer, decoy = _number(value, decimals), _number(slipped, decimals)
        if answer == decoy:
            return None
        shown = {name: _say(values[name]) for name in names}
        # The main model works sums out with the calculator (as it does live), so the past does too.
        expression = re.sub(r"\b[a-c]\b", lambda found: shown[found.group()], spec["answer"]).replace("*", " * ")
        return {
            "prompt": spec["phrasing"].format(**shown),
            "intent": f"The number {answer}" + (f" (rounded to {decimals} decimals)." if decimals > 0 else "."),
            "verifier": Verifier("answer", float(answer), "number", tolerance=0.5 * 10 ** -max(decimals, 0)),
            "reference": Solution(answer=answer), "decoy": Solution(answer=decoy),
            "inputs": {}, "tools": ("files",), "answer": f"That comes to {answer}.",
            "steps": (ToolStep("calculate", {"expression": expression},
                               f"{expression} = {_calculated(evaluate(spec['answer'], values))}"),),
        }

    def _t_move_file(self, day: int) -> dict[str, Any] | None:
        from row_bot.learning.packs import Solution, Verifier

        spec = self.person.get("moves")
        if not spec:
            return None
        candidates = [path for path in self.world.notes if path in self.world.files and path not in self.world.moved
                      and path.startswith(spec["from_dir"] + "/")]
        if not candidates:
            return None
        source = candidates[0]
        name = Path(source).name
        target = f"{spec['to_dir']}/{name}"
        content = self.world.files[source]
        self.world.moved.add(source)
        return {
            "prompt": self._phrase(spec["phrasings"], file=source, to_dir=spec["to_dir"]),
            "intent": f"{source} moved (not copied) to {target}.",
            "verifier": Verifier("file", content, "text", path=target),
            "reference": Solution(answer="Moved.", files={target: content}),
            "decoy": Solution(answer="Moved.", files={f"archive/{name}": content}),
            "inputs": {source: content}, "tools": ("files",), "approvals": (source,),
            "steps": (ToolStep("workspace_move_file", {"source_path": source, "destination_path": target},
                               f"File moved successfully from {source} to {target}."),),
            "answer": f"Moved {source} to {spec['to_dir']}/.", "moves": {source: target},
        }

    def _t_code_function(self, day: int) -> dict[str, Any] | None:
        from row_bot.learning.packs import Solution, Verifier

        spec = self.person.get("coding") or {}
        bank = coding_bank.problems(spec.get("domain", ""))
        if not bank:
            return None
        problem = self.rng.choice(bank)
        uses = self.world.code_uses.get(problem["id"], 0)
        self.world.code_uses[problem["id"]] = uses + 1
        module = problem["module"] + (f"_{uses + 1}" if uses else "")
        path = f"{spec.get('dir', 'tools')}/{module}.py"
        tests = coding_bank.tests_for(problem["name"], path, problem["tests"])
        reference = coding_bank.code(problem["reference"])
        prompt = (f"In {path}, write a function {problem['description']} Use only the Python standard library."
                  if self.rng.random() < 0.5 else
                  f"Need {problem['name']}() in {path}: {problem['description']} Standard library only.")
        return {
            "prompt": prompt,
            "intent": f"{problem['name']} in {path} doing exactly what was described, edge cases included.",
            "verifier": Verifier("code_tests", tests, path=path),
            "reference": Solution(answer="done", files={path: reference}),
            "decoy": Solution(answer="done", files={path: coding_bank.code(problem["decoy"])}),
            "inputs": {}, "tools": ("files",), "max_steps": 10,
            "steps": (ToolStep("workspace_write_file", {"file_path": path, "text": reference},
                               f"File written successfully to {path}."),),
            "answer": f"Done: {problem['name']} is in {path}.", "writes": {path: reference},
        }


def _steps_for_calls(world: World, calls: tuple[dict[str, Any], ...]) -> tuple[ToolStep, ...]:
    """Play app calls on the world's apps, as a past turn's tool steps."""

    return tuple(ToolStep(call["tool"], dict(call["args"]), world.apps.call(call["tool"], dict(call["args"])))
                 for call in calls)


def _make_request(person: dict[str, Any], builder: Builder, template: str, day: int, order: int,
                  request_id: str) -> Request | None:
    from row_bot.learning.packs import PackTask, validate

    world = builder.world
    built = builder.build(template, day)
    if built is None:
        return None
    # After building: a template may have brought a ticket or a newsletter in first.
    apps_before = world.apps.state()
    task = PackTask(
        id=request_id, pack=TEMPLATE_PACK[template], split="visible", prompt=built["prompt"],
        verifier=built["verifier"], reference=built["reference"], decoy=built["decoy"],
        files=dict(built["inputs"]), apps=apps_before, tools=tuple(built["tools"]),
        max_steps=int(built.get("max_steps", 8)),
    )
    validate(task)
    # The right outcome becomes the world's: files written, moves, app calls.
    for path, text in (built.get("writes") or {}).items():
        world.files[path] = text
    for source, target in (built.get("moves") or {}).items():
        world.files[target] = world.files.pop(source)
    steps = tuple(built.get("steps") or ()) + _steps_for_calls(world, tuple(built.get("calls") or ()))
    return Request(id=request_id, person=person["id"], day=day, order=order, template=template,
                   intended=TEMPLATE_KIND[template], prompt=built["prompt"], intent=built["intent"], task=task,
                   inputs=dict(built["inputs"]), approvals=tuple(built.get("approvals") or ()), steps=steps,
                   answer=str(built.get("answer") or built["reference"].answer))


def _past_mistake(request: Request, rng: random.Random) -> Request:
    """A past request Row-Bot first got wrong (its decoy), corrected in the person's next message."""

    if request.task.verifier.kind not in ("answer",) or not request.task.decoy.answer:
        return request
    correction = rng.choice(("That's not right - ", "No, ", "Hmm, not quite: ")) + request.intent[:1].lower() + \
        request.intent[1:]
    return replace(request, mistake=(request.steps, f"It's {request.task.decoy.answer}.", correction))


def generate(person_id: str, *, seed: int, days: int, requests_per_day: tuple[int, int] | None = None,
             phrased: bool = True) -> History:
    """The person's 30-day past and ``days`` live days, from ``seed`` (the same seed, the same history).

    ``phrased``: the past's replies are the main model's own words where ``past_replies.json`` keeps one for
    this seed (and it says what the template's reply says); otherwise the template's reply."""

    if days < 0:
        raise GeneratorError("days can't be negative")
    person = load_person(person_id)
    replies = past_replies(person_id, seed) if phrased else {}
    rng = random.Random(f"{int(seed)}/{person_id}")
    world = World(person, random.Random(f"{int(seed)}/{person_id}/world"))
    builder = Builder(person, world, rng)
    policy = person.get("policy") or {}
    weights = {name: float(weight) for name, weight in (person.get("work") or {}).items()}
    past: list[Conversation] = []
    for day in range(-PAST_DAYS, 0):
        world.morning(day)
        low, high = policy.get("past_conversations_per_day", [3, 5])
        for number in range(rng.randint(int(low), int(high))):
            turns: list[Request] = []
            for turn in range(rng.choice((1, 1, 1, 2, 2, 3))):
                request = None
                for _ in range(6):
                    template = _weighted(rng, weights)
                    request = _make_request(person, builder, template, day, turn,
                                            f"{person_id}-p{-day:02d}-{number}-{turn}")
                    if request is not None:
                        break
                if request is None:
                    continue
                if rng.random() < float(policy.get("past_mistake_share", 0.1)):
                    request = _past_mistake(request, rng)
                kept = replies.get(request.id)
                if kept and kept.get("template") == request.answer and kept.get("reply"):
                    request = replace(request, answer=str(kept["reply"]))
                turns.append(request)
            if turns:
                title = turns[0].prompt[:48].rstrip()
                past.append(Conversation(f"{person_id}-past-{-day:02d}-{number}", day, title, tuple(turns)))
    world.morning(1)
    start_files = dict(world.files)
    start_apps = world.apps.state()
    stream: list[Request] = []
    low, high = requests_per_day or tuple(policy.get("requests_per_day", [2, 4]))
    for day in range(1, days + 1):
        if day > 1:
            world.morning(day)
        for order in range(rng.randint(int(low), int(high))):
            for _ in range(6):
                template = _weighted(rng, weights)
                request = _make_request(person, builder, template, day, order, f"{person_id}-d{day:02d}-{order}")
                if request is not None:
                    stream.append(request)
                    break
    return History(person={key: value for key, value in person.items() if key not in ("files", "apps")},
                   seed=int(seed), days=days, past=tuple(past), stream=tuple(stream),
                   start_files=start_files, start_apps=start_apps)


def write_history(history: History, folder: Path) -> Path:
    """The history as one JSON file a reader can inspect."""

    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"history-{history.person['id']}-{history.seed}-{history.days}d.json"
    path.write_text(json.dumps(history.to_dict(), indent=1, sort_keys=True, ensure_ascii=False) + "\n",
                    encoding="utf-8", newline="\n")
    return path


__all__ = [
    "Conversation",
    "GeneratorError",
    "History",
    "KINDS_OF_WORK",
    "LAST_PAST_DAY",
    "PAST_DAYS",
    "Request",
    "TEMPLATE_KIND",
    "ToolStep",
    "World",
    "day_date",
    "evaluate",
    "generate",
    "load_person",
    "people",
    "write_history",
]
