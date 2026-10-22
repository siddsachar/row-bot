"""Kinds of work: one small taxonomy for the personal model's routing (L2c2) and diagnosis (L3a).

The four task-pack families (D30) plus **other**:

- ``document_lookups`` "Document lookups": finding something in the person's documents and files;
- ``structured_transforms`` "Structured transforms": turning data from one shape into another
  (CSV, JSON, tables, lists);
- ``small_coding`` "Small coding tasks": writing or fixing a small piece of code;
- ``tool_workflows`` "Tool workflows": doing something in an app (calendar, mail, an issue
  tracker, the person's connected apps);
- ``other``: anything else, and anything the classifier isn't sure about. **Other always goes
  to the main model.**

:func:`classify` decides a new request's kind **at turn start, without a model call**: it is
deterministic and cheap (a few regular expressions over the person's message, plus what the turn
already knows: the apps it may use, its attachments, a code folder bound to the chat, and the
kind of the conversation's previous request). Each family scores its own signals; the request
gets a family only when that family's score clears a floor **and** beats the runner-up by a
margin. Ties, weak signals and anything mixed are ``other``. A short follow-up with no signals
of its own ("and the other one?") keeps the conversation's previous kind, only when that was
decided within the last few minutes.

The rules (fixed, reviewed; a change is a code change with its tests):

======================  ============================================================  =====
Family                  Signals                                                       Score
======================  ============================================================  =====
Document lookups        "according to", "in the document/policy/contract/report…",    3
                        "what does … say", "look up", "find … in"; a document
                        attached (.pdf .docx .md .txt .rtf .odt)
                        document words alone (policy, contract, handbook, notes)      1 each
Structured transforms   "convert", "transform", "reformat", "turn … into", "as        3
                        JSON/CSV/a table"; a data file attached or named
                        (.csv .json .tsv .xlsx .yaml .ini .xml)
                        data words (columns, rows, fields, JSON, CSV)                 1 each
Small coding tasks      a code fence, a function or class definition, a code file     3
                        named (.py .js .ts …), a code folder bound to the chat
                        code words (function, bug, unit test, refactor, script…)      1 each
Tool workflows          an app the turn may use is named, or a calendar, mail or      3
                        issue action ("schedule a meeting", "reply to", "close
                        issue 12", "archive the newsletter")
                        app words (calendar, inbox, email, ticket, invite)            1 each
======================  ============================================================  =====

Floor 3, margin 2. Scores are capped per family so one long message can't win on repetition.
"""

from __future__ import annotations

import re
import time
from collections import OrderedDict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from threading import Lock
from typing import Literal

Kind = Literal["document_lookups", "structured_transforms", "small_coding", "tool_workflows", "other"]

DOCUMENT_LOOKUPS: Kind = "document_lookups"
STRUCTURED_TRANSFORMS: Kind = "structured_transforms"
SMALL_CODING: Kind = "small_coding"
TOOL_WORKFLOWS: Kind = "tool_workflows"
OTHER: Kind = "other"

#: Every kind, the four families first (the task packs' order), then other.
KINDS: tuple[Kind, ...] = (DOCUMENT_LOOKUPS, STRUCTURED_TRANSFORMS, SMALL_CODING, TOOL_WORKFLOWS, OTHER)
FAMILIES: tuple[Kind, ...] = KINDS[:-1]

#: What people see (UX "Words": "Kind of work").
LABELS: Mapping[str, str] = {
    DOCUMENT_LOOKUPS: "Document lookups",
    STRUCTURED_TRANSFORMS: "Structured transforms",
    SMALL_CODING: "Small coding tasks",
    TOOL_WORKFLOWS: "Tool workflows",
    OTHER: "Other",
}
#: Lower-case, for a sentence: "Use v1 for document lookups".
PHRASES: Mapping[str, str] = {key: value[0].lower() + value[1:] for key, value in LABELS.items()}

FLOOR = 3
MARGIN = 2
CAP = 6
#: A follow-up keeps the conversation's previous kind only this soon after it.
FOLLOW_UP_SECONDS = 10 * 60
FOLLOW_UP_WORDS = 12


def label(kind: str) -> str:
    """"Document lookups" for ``document_lookups``; unknown kinds read as "Other"."""

    return LABELS.get(kind, LABELS[OTHER])


def phrase(kind: str) -> str:
    """"document lookups", for a sentence."""

    return PHRASES.get(kind, PHRASES[OTHER])


def from_label(value: str | None) -> Kind:
    """The kind for a label (a pack task's ``kind_of_work``, an episode's), else other."""

    text = str(value or "").strip().lower()
    for kind, name in LABELS.items():
        if text in (kind, name.lower()):
            return kind  # type: ignore[return-value]
    return OTHER


def from_pack(pack: str) -> Kind:
    """The kind a task pack tests (``packs.PACKS`` keys are the family ids)."""

    return pack if pack in FAMILIES else OTHER  # type: ignore[return-value]


# ── Signals ─────────────────────────────────────────────────────────────────

_DOC_EXT = (".pdf", ".docx", ".doc", ".md", ".txt", ".rtf", ".odt", ".pptx", ".html")
_DATA_EXT = (".csv", ".json", ".tsv", ".xlsx", ".xls", ".yaml", ".yml", ".ini", ".xml", ".toml", ".jsonl")
_CODE_EXT = (".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".rb", ".c", ".cpp", ".cs", ".php",
             ".sh", ".ps1", ".sql", ".kt", ".swift")


def _words(*items: str) -> re.Pattern[str]:
    return re.compile(r"\b(?:" + "|".join(items) + r")\b", re.IGNORECASE)


_DOC_STRONG = _words(
    r"according to", r"(?:in|from) (?:the|my|our|this|that) (?:document|doc|policy|contract|report|handbook|"
    r"manual|memo|notes|minutes|spec|specification|file|pdf|agreement|guide|faq)s?",
    r"what does (?:the|my|our|this|that) \w+(?: \w+)? say", r"look up", r"find (?:the|out|where|which|what|in)",
    r"which (?:document|file|policy|version)", r"(?:current|latest|approved|superseded) (?:version|policy|draft)",
)
_DOC_WEAK = _words(r"documents?", r"docs?", r"polic(?:y|ies)", r"contracts?", r"handbook", r"manual", r"memo",
                   r"notes", r"minutes", r"reports?", r"pdf", r"invoices?", r"agreement", r"clause", r"section")
_TRANSFORM_STRONG = _words(
    r"convert", r"transform", r"reformat", r"re-format", r"turn (?:this|these|it|them|the \w+) into",
    r"(?:as|into|to) (?:a )?(?:json|csv|tsv|yaml|table|markdown table|list|spreadsheet)",
    r"extract (?:the )?\w+(?: \w+)? into", r"group (?:them|these|the \w+) by", r"sort (?:them|these|the \w+) by",
    r"merge (?:the|these) (?:files|lists|tables)", r"dedupe|deduplicate", r"pivot", r"transpose",
)
_TRANSFORM_WEAK = _words(r"json", r"csv", r"tsv", r"yaml", r"columns?", r"rows?", r"fields?", r"keys?",
                         r"table", r"spreadsheet", r"format")
_CODE_STRONG = re.compile(
    r"```|^\s*(?:def|class|function|import|from \S+ import|public |private |const |let |#include)\b|"
    r"\b(?:write|fix|implement|refactor|debug) (?:a |the |this |my )?(?:\w+ )?(?:function|method|class|script|"
    r"test|unit test|module|bug)\b|\btraceback\b|\bstack trace\b|\bunit tests?\b",
    re.IGNORECASE | re.MULTILINE,
)
_CODE_WEAK = _words(r"function", r"method", r"bug", r"code", r"script", r"python", r"javascript",
                    r"typescript", r"regex", r"compile", r"refactor", r"exception", r"variable", r"api")
_TOOL_STRONG = _words(
    r"schedule (?:a|the|an) \w+", r"(?:add|create|put|book|move|cancel) (?:a |an |the )?(?:\w+ )?"
    r"(?:event|meeting|appointment|invite|call)", r"(?:reply|respond) to", r"send (?:a |an |the )?(?:email|mail|"
    r"message|reply|note)", r"forward (?:the|this|that)", r"archive (?:the|this|that|my)",
    r"(?:open|close|reopen|label|assign|comment on) (?:the |an |a )?(?:issue|ticket|bug report)s?",
    r"(?:my|the) (?:calendar|inbox|mailbox)", r"mark (?:it|them|the \w+) as (?:read|done|closed)",
)
_TOOL_WEAK = _words(r"calendar", r"inbox", r"e-?mails?", r"mail", r"tickets?", r"issues?", r"invites?",
                    r"meetings?", r"appointments?", r"tracker", r"slack", r"telegram", r"github", r"jira",
                    r"linear", r"notion", r"trello", r"todo", r"reminder")


def _count(pattern: re.Pattern[str], text: str) -> int:
    return len(pattern.findall(text))


def _named(text: str, extensions: Sequence[str]) -> int:
    lowered = text.lower()
    return sum(1 for extension in extensions if re.search(r"[\w-]" + re.escape(extension) + r"\b", lowered))


@dataclass(frozen=True)
class Request:
    """What a new turn knows at its start, before any model call."""

    text: str
    #: Attached file names (``report.pdf``), never their content.
    attachments: Sequence[str] = ()
    #: Apps the turn may use and the person named (``calendar``, ``gmail``, an MCP server…).
    apps: Sequence[str] = ()
    #: A code folder (a Developer workspace) is bound to the chat.
    code_folder: bool = False
    #: The conversation's previous request's kind, and when it was decided (epoch seconds).
    previous: str | None = None
    previous_at: float | None = None


@dataclass(frozen=True)
class Classification:
    """A kind and why (the families' scores; ``reason`` is a short code for tests and logs)."""

    kind: Kind
    reason: str
    scores: Mapping[str, int] = field(default_factory=dict)

    @property
    def sure(self) -> bool:
        return self.kind != OTHER


def scores(request: Request) -> dict[str, int]:
    """Each family's score for a request (capped)."""

    text = str(request.text or "")[:8_000]
    files = " ".join(str(name) for name in request.attachments)
    found = {
        DOCUMENT_LOOKUPS: 3 * _count(_DOC_STRONG, text) + _count(_DOC_WEAK, text)
        + 3 * _named(files, _DOC_EXT),
        STRUCTURED_TRANSFORMS: 3 * _count(_TRANSFORM_STRONG, text) + _count(_TRANSFORM_WEAK, text)
        + 3 * _named(files, _DATA_EXT) + 2 * _named(text, _DATA_EXT),
        SMALL_CODING: 3 * _count(_CODE_STRONG, text) + _count(_CODE_WEAK, text)
        + 3 * _named(files + " " + text, _CODE_EXT) + (3 if request.code_folder else 0),
        TOOL_WORKFLOWS: 3 * _count(_TOOL_STRONG, text) + _count(_TOOL_WEAK, text)
        + (3 if request.apps else 0),
    }
    return {kind: min(CAP, value) for kind, value in found.items()}


def classify(request: Request, *, now: float | None = None) -> Classification:
    """The request's kind of work (see the module's rules). ``other`` when unsure."""

    found = scores(request)
    ranked = sorted(found.items(), key=lambda item: (-item[1], FAMILIES.index(item[0])))  # type: ignore[arg-type]
    (best, top), (_, second) = ranked[0], ranked[1]
    if top >= FLOOR and top - second >= MARGIN:
        return Classification(best, "signals", found)  # type: ignore[arg-type]
    if top == 0 and request.previous in FAMILIES and request.previous_at is not None:
        moment = time.time() if now is None else float(now)
        words = len(str(request.text or "").split())
        if 0 <= moment - float(request.previous_at) <= FOLLOW_UP_SECONDS and words <= FOLLOW_UP_WORDS:
            return Classification(request.previous, "follow_up", found)  # type: ignore[arg-type]
    if top >= FLOOR:
        return Classification(OTHER, "mixed", found)
    return Classification(OTHER, "weak" if top else "no_signals", found)


class RecentKinds:
    """Each conversation's previous request's kind, in memory (the newest 512 conversations)."""

    def __init__(self, size: int = 512) -> None:
        self._items: OrderedDict[str, tuple[str, float]] = OrderedDict()
        self._size = size
        self._lock = Lock()

    def get(self, conversation_id: str) -> tuple[str | None, float | None]:
        with self._lock:
            found = self._items.get(conversation_id)
        return (found[0], found[1]) if found else (None, None)

    def note(self, conversation_id: str, kind: str, at: float | None = None) -> None:
        if not conversation_id:
            return
        with self._lock:
            self._items[conversation_id] = (kind, time.time() if at is None else float(at))
            self._items.move_to_end(conversation_id)
            while len(self._items) > self._size:
                self._items.popitem(last=False)

    def forget(self, conversation_id: str) -> None:
        with self._lock:
            self._items.pop(conversation_id, None)


RECENT = RecentKinds()


def classify_turn(conversation_id: str, text: str, *, attachments: Iterable[str] = (), apps: Iterable[str] = (),
                  code_folder: bool = False, now: float | None = None) -> Classification:
    """Classify a new turn in a conversation, remembering its kind for the next follow-up."""

    previous, previous_at = RECENT.get(conversation_id)
    found = classify(Request(str(text or ""), tuple(attachments), tuple(apps), code_folder, previous, previous_at),
                     now=now)
    RECENT.note(conversation_id, found.kind, now)
    return found


__all__ = [
    "Classification",
    "DOCUMENT_LOOKUPS",
    "FAMILIES",
    "FLOOR",
    "KINDS",
    "LABELS",
    "MARGIN",
    "OTHER",
    "PHRASES",
    "RECENT",
    "RecentKinds",
    "Request",
    "SMALL_CODING",
    "STRUCTURED_TRANSFORMS",
    "TOOL_WORKFLOWS",
    "classify",
    "classify_turn",
    "from_label",
    "from_pack",
    "label",
    "phrase",
    "scores",
]
