"""How Row-Bot learns: the improver's strategy (L4a; D15, E4).

A strategy is a small, versioned set of choices about **how the improvement loop works**, and nothing else:

- **issue priority**: how much each kind of evidence counts, whether the person's own feedback comes first,
  and how much evidence an issue needs before it is diagnosed;
- **method choice**: which kinds of change it may draft and which it tries first (a lesson, a way of working,
  a new or patched skill, or training);
- **example selection**: how many cases a diagnosis is shown, which first, how much of each, and whether the
  redo the person accepted is shown;
- **budget split**: how many issues a night diagnoses and how many drafts it tests, and how big each test is;
- **its own prompts**: its own words for finding and drafting fixes, added to the diagnosis instructions;
- **practice selection** (L4b): how many practice tasks it makes up from each issue and how many each idea's
  test runs, in a build with practice on (``learning.practice_enabled``); otherwise it has no effect, isn't
  described and is never what a new method changes.

**The fixed points are absent by construction** (E4): no field can say anything about the objective, the
limits (the monthly token limit, the trials' 20% share, a task's budget, the repeats), approvals, tools,
permissions, the hidden tests (the audit vault, the fresh problems, the frozen control), the rules for keeping
a change (the better rule, its margin) or the metric definitions (the checker and its version). The schema is a
closed list of fields with bounds: an unknown key at any level, a value out of bounds or a prompt that talks
about those things is refused (:class:`StrategyError`). Every part a strategy changes is read by the loop where
it already had a value (``diagnosis``, ``changes``); nothing in the loop reads a fixed point from a strategy.

Schema v2 adds practice selection; a v1 text reads with the first method's practice choices.

The first strategy (:data:`FIRST`) is the loop as L3a and L3b built it, so a person who never had a trial runs
exactly that.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace
from typing import Any

SCHEMA = "row_bot.learning.strategy"
SCHEMA_VERSION = 2

#: The kinds of change a strategy may draft (the diagnosis's draftable picks; "needs more examples" and "wait"
#: are always open).
METHODS: tuple[str, ...] = ("lesson", "way_of_working", "create_skill", "patch_skill", "train")
EXAMPLE_ORDERS: tuple[str, ...] = ("feedback_first", "newest_first")
PROMPT_LIMIT = 1200

# A strategy's own words may not talk about what no strategy can change (E4): its prompt is about finding and
# drafting fixes, never about limits, approvals, tools, permissions, the hidden tests, the keeping rules or the
# metric.
_FIXED_WORDS = re.compile(
    r"\b(?:approv\w*|permission\w*|consent|allow(?:ed|s)?\s+to|tool\s+access|tools?\s+(?:list|access)|"
    r"budget\w*|limit\w*|tokens?|spend\w*|cost\w*|quota|hidden|audit\w*|vault|fresh\s+problems?|control\s+arm|"
    r"frozen|benchmark\w*|metric\w*|checker|verifier\w*|score\w*|pass\s+rates?|promot\w*|margin|keep\s+rules?|"
    r"objective|hands[-\s]?off|auto(?:matic)?\s+mode|ignore\s+(?:the\s+)?(?:rules|instructions))\b",
    re.I)


class StrategyError(ValueError):
    """A strategy that isn't valid: ``code`` is stable (``unknown_field``, ``missing_field``, ``invalid_field``,
    ``out_of_range``, ``fixed_point``, ``e9``, ``newer``), ``detail`` says where."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


def _number(low: float, high: float, *, integer: bool = False) -> dict[str, Any]:
    return {"low": low, "high": high, "integer": integer}


@dataclass(frozen=True)
class IssuePriority:
    """Which issues the night diagnoses first, and how much evidence an issue needs."""

    corrections: float = field(default=3.0, metadata=_number(0.0, 10.0))
    pushback: float = field(default=2.0, metadata=_number(0.0, 10.0))  # stops, retries, turned-down actions
    failures: float = field(default=1.0, metadata=_number(0.0, 10.0))
    chats: float = field(default=1.0, metadata=_number(0.0, 10.0))
    feedback_first: bool = True
    min_feedback: int = field(default=2, metadata=_number(1, 4, integer=True))
    min_failures: int = field(default=3, metadata=_number(2, 6, integer=True))


@dataclass(frozen=True)
class MethodChoice:
    """Which kinds of change it may draft, in the order it tries them."""

    prefer: tuple[str, ...] = METHODS


@dataclass(frozen=True)
class ExampleSelection:
    """What each diagnosis is shown of the person's work."""

    shown: int = field(default=6, metadata=_number(2, 10, integer=True))
    order: str = "feedback_first"
    excerpt: int = field(default=500, metadata=_number(200, 1000, integer=True))
    show_redo: bool = True


@dataclass(frozen=True)
class BudgetSplit:
    """How a night spends its share: issues diagnosed, drafts tested, and how big each test is (the repeats
    and each task's budget are fixed points, not here)."""

    issues: int = field(default=6, metadata=_number(1, 10, integer=True))
    tests: int = field(default=2, metadata=_number(1, 6, integer=True))
    own_problems: int = field(default=8, metadata=_number(2, 16, integer=True))
    other_problems: int = field(default=4, metadata=_number(1, 8, integer=True))
    visible_own: int = field(default=4, metadata=_number(0, 8, integer=True))
    visible_other: int = field(default=1, metadata=_number(0, 4, integer=True))
    hidden_own: int = field(default=4, metadata=_number(0, 8, integer=True))
    harder_own: int = field(default=4, metadata=_number(0, 8, integer=True))


@dataclass(frozen=True)
class PracticeSelection:
    """Practice tasks it makes up from each issue it looks into (one call), and how many of them each idea's test
    runs besides the sample tasks (L4b)."""

    made: int = field(default=2, metadata=_number(0, 6, integer=True))
    tested: int = field(default=2, metadata=_number(0, 6, integer=True))


@dataclass(frozen=True)
class Prompts:
    """Its own words for finding and drafting fixes, added to the diagnosis's instructions."""

    diagnosis: str = ""


@dataclass(frozen=True)
class Strategy:
    """One learning method. ``version`` is the number people see ("Learning method v5")."""

    version: int = 1
    issues: IssuePriority = IssuePriority()
    methods: MethodChoice = MethodChoice()
    examples: ExampleSelection = ExampleSelection()
    budget: BudgetSplit = BudgetSplit()
    prompts: Prompts = Prompts()
    practice: PracticeSelection = PracticeSelection()

    def __post_init__(self) -> None:
        check(self)

    # ── Its text form ───────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        def part(value: Any) -> dict[str, Any]:
            return {item.name: (list(getattr(value, item.name)) if isinstance(getattr(value, item.name), tuple)
                                else getattr(value, item.name)) for item in fields(value)}

        return {"schema": SCHEMA, "schema_version": SCHEMA_VERSION, "version": self.version,
                **{item.name: part(getattr(self, item.name)) for item in fields(self) if item.name != "version"}}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False)

    def with_version(self, version: int) -> Strategy:
        return replace(self, version=int(version))

    # ── What people see ─────────────────────────────────────────────────

    def describe(self) -> str:
        """The method in plain words (How Row-Bot learns › Current method)."""

        words = []
        issues, methods, examples, budget = self.issues, self.methods, self.examples, self.budget
        if issues.feedback_first and issues.corrections >= max(issues.pushback, issues.failures):
            words.append("Looks first at mistakes you corrected")
        elif issues.failures > max(issues.corrections, issues.pushback):
            words.append("Looks first at steps that kept failing")
        else:
            words.append("Looks first at turns you stopped, retried or turned down")
        names = {"lesson": "a lesson", "way_of_working": "a way of working", "create_skill": "a new skill",
                 "patch_skill": "a skill edit", "train": "training"}
        tried = [names[item] for item in methods.prefer[:2]]
        if len(tried) == 2:
            words.append(f"tries {tried[0]} before {tried[1]}")
        elif tried:
            words.append(f"only tries {tried[0]}")
        if practising(self):
            words.append("practises on made-up tasks like the ones it got wrong")
        words.append(f"shows itself up to {examples.shown} examples of each mistake")
        diagnose, test = budget.issues, budget.tests
        words.append(f"looks into up to {diagnose} {'issue' if diagnose == 1 else 'issues'} a night and tests "
                     f"{test} {'idea' if test == 1 else 'ideas'}")
        text = ", ".join(words[:-1]) + ", and " + words[-1] + "."
        return text[0].upper() + text[1:]


_PARTS: dict[str, type] = {"issues": IssuePriority, "methods": MethodChoice, "examples": ExampleSelection,
                           "budget": BudgetSplit, "prompts": Prompts, "practice": PracticeSelection}


def practising(strategy: Strategy) -> bool:
    """Whether this method practises: practice is on in this build and it makes up tasks or tests on them."""

    from row_bot import learning as gate

    return gate.practice_enabled() and bool(strategy.practice.made or strategy.practice.tested)


def field_paths() -> list[str]:
    """Every field a strategy has ("issues.corrections", …): the whole of what a strategy can say."""

    return sorted(f"{name}.{item.name}" for name, part in _PARTS.items() for item in fields(part))


def check(strategy: Strategy) -> None:
    """Refuse a strategy with a value out of bounds or a prompt that talks about a fixed point."""

    if type(strategy.version) is not int or strategy.version < 1:
        raise StrategyError("invalid_field", "version")
    for name, kind in _PARTS.items():
        part = getattr(strategy, name)
        if type(part) is not kind:
            raise StrategyError("invalid_field", name)
        for item in fields(part):
            value = getattr(part, item.name)
            where = f"{name}.{item.name}"
            bounds = item.metadata
            if bounds:
                integer = bounds.get("integer", False)
                if type(value) is bool or not isinstance(value, (int, float)) or (integer and type(value) is not int):
                    raise StrategyError("invalid_field", where)
                if not bounds["low"] <= value <= bounds["high"]:
                    raise StrategyError("out_of_range", where)
            elif isinstance(item.default, bool):
                if type(value) is not bool:
                    raise StrategyError("invalid_field", where)
    prefer = strategy.methods.prefer
    if (type(prefer) is not tuple or not prefer or len(set(prefer)) != len(prefer)
            or any(item not in METHODS for item in prefer)):
        raise StrategyError("invalid_field", "methods.prefer")
    if strategy.examples.order not in EXAMPLE_ORDERS:
        raise StrategyError("invalid_field", "examples.order")
    text = strategy.prompts.diagnosis
    if type(text) is not str or len(text) > PROMPT_LIMIT or "\0" in text:
        raise StrategyError("invalid_field", "prompts.diagnosis")
    if _FIXED_WORDS.search(text):
        raise StrategyError("fixed_point", "prompts.diagnosis")
    from row_bot import evolution

    if evolution.injection_problems(text):
        raise StrategyError("e9", "prompts.diagnosis")


def from_dict(data: Mapping[str, Any]) -> Strategy:
    """A strategy from its text form, read strictly: only the fields above, each within its bounds."""

    if not isinstance(data, Mapping):
        raise StrategyError("invalid_field", "strategy")
    if data.get("schema") != SCHEMA:
        raise StrategyError("invalid_field", "schema")
    if type(data.get("schema_version")) is not int:
        raise StrategyError("missing_field", "schema_version")
    if data["schema_version"] > SCHEMA_VERSION:
        raise StrategyError("newer", f"strategy v{data['schema_version']}")
    allowed = {"schema", "schema_version", "version", *_PARTS}
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise StrategyError("unknown_field", unknown[0])
    values: dict[str, Any] = {}
    for name, kind in _PARTS.items():
        raw = data.get(name, {})
        if not isinstance(raw, Mapping):
            raise StrategyError("invalid_field", name)
        names = {item.name for item in fields(kind)}
        extra = sorted(set(raw) - names)
        if extra:
            raise StrategyError("unknown_field", f"{name}.{extra[0]}")
        given = {key: (tuple(value) if isinstance(value, list) else value) for key, value in raw.items()}
        try:
            values[name] = kind(**given)
        except TypeError as error:
            raise StrategyError("invalid_field", name) from error
    version = data.get("version", 1)
    return Strategy(version=version, **values)


#: The loop as L3a and L3b built it.
FIRST = Strategy()


def from_json(text: str) -> Strategy:
    try:
        data = json.loads(text)
    except ValueError as error:
        raise StrategyError("invalid_field", "json") from error
    return from_dict(data)


def differences(before: Strategy, after: Strategy) -> list[str]:
    """The fields ``after`` changed from ``before`` ("budget.tests"), in the schema's order."""

    found = []
    for path in field_paths():
        part, name = path.split(".")
        if getattr(getattr(before, part), name) != getattr(getattr(after, part), name):
            found.append(path)
    return found


#: What each changed field means for the person (History: "v5: practises before training").
CHANGE_WORDS: dict[str, str] = {
    "issues.corrections": "weighs the mistakes you corrected differently",
    "issues.pushback": "weighs turns you stopped or retried differently",
    "issues.failures": "weighs failing steps differently",
    "issues.chats": "weighs how many chats a mistake came up in differently",
    "issues.feedback_first": "changes whether your own feedback comes first",
    "issues.min_feedback": "changes how much of your feedback it waits for",
    "issues.min_failures": "changes how many failing steps it waits for",
    "methods.prefer": "tries a different kind of change first",
    "examples.shown": "shows itself a different number of examples",
    "examples.order": "looks at different examples first",
    "examples.excerpt": "reads more or less of each example",
    "examples.show_redo": "changes whether it looks at the redo you accepted",
    "budget.issues": "looks into a different number of issues a night",
    "budget.tests": "tests a different number of ideas a night",
    "budget.own_problems": "tests each idea on more or fewer of your own tasks",
    "budget.other_problems": "tests each idea on more or fewer other tasks",
    "budget.visible_own": "tests each idea on more or fewer sample tasks of its kind",
    "budget.visible_other": "tests each idea on more or fewer sample tasks of other kinds",
    "budget.hidden_own": "tests each idea on more or fewer held-back sample tasks",
    "budget.harder_own": "tests each idea on more or fewer harder sample tasks",
    "prompts.diagnosis": "words its own instructions differently",
    "practice.made": "makes up more or fewer practice tasks from each mistake",
    "practice.tested": "tests each idea on more or fewer practice tasks",
}


def change_words(before: Strategy, after: Strategy) -> str:
    """What ``after`` changes, in plain words (the first change; "and more" when there are several)."""

    changed = differences(before, after)
    if not changed:
        return "the same method"
    first = CHANGE_WORDS.get(changed[0], "changes how it learns")
    if changed[0] == "methods.prefer" and after.methods.prefer and after.methods.prefer[0] != before.methods.prefer[0]:
        names = {"lesson": "a lesson", "way_of_working": "a way of working", "create_skill": "a new skill",
                 "patch_skill": "a skill edit", "train": "training"}
        first = f"tries {names[after.methods.prefer[0]]} first"
    elif changed[0] == "methods.prefer":
        first = "changes which kinds of change it tries, and in what order"
    return first + (" and more" if len(changed) > 1 else "")


__all__ = [
    "BudgetSplit",
    "CHANGE_WORDS",
    "EXAMPLE_ORDERS",
    "ExampleSelection",
    "FIRST",
    "IssuePriority",
    "METHODS",
    "MethodChoice",
    "PROMPT_LIMIT",
    "PracticeSelection",
    "Prompts",
    "SCHEMA",
    "SCHEMA_VERSION",
    "Strategy",
    "StrategyError",
    "change_words",
    "check",
    "differences",
    "field_paths",
    "from_dict",
    "from_json",
    "practising",
]
