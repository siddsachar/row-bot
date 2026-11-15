"""The personal model's dataset: the person's own good work, as training rows (D6, O11, O18, E9).

**Where examples come from.** One example per captured turn (an episode) of the main model's
good work: the turn completed, nothing in it failed, was stopped, retried, denied or corrected
(no active Signal on it), and it is the main model's (never a personal model's own answers). The
row is the turn's last model call **exactly as Row-Bot sent it** (``capture.read_request``: the
system prompt, the tool schemas, the conversation, the turn context) followed by the reply the
model gave, in OpenAI's chat format, so every row carries the real envelope. Replies read from
past chats (O11) are examples too: the reply the person saw and kept, with the conversation as
stored (no envelope was captured for them). A correction becomes a target only once a corrected
action exists (L3a): until then Teach notes are counted, never invented into answers.

**Always left out**: decisions the audit vault holds (``held_out``, O18; the vault takes its share
of new work first, so nothing is trained on and then tested), conversations opted out, deleted or
purged (their records are gone, and each conversation is checked against scope again, so one that
became someone else's or whose source was switched off is left out too), and turns whose request
can no longer be read. Tool and web content is context, never a target (E9): loss falls only on
the assistant's own messages, and a reply that repeats fenced external content is left out.

**Targets** (``weight`` 1, as OpenAI's fine-tuning format marks them): the assistant messages of
the turn itself (its tool calls and its answer); earlier turns' replies in the history are context
(``weight`` 0). For a past chat only the final answer is a target (its tool calls were made
without the schemas the model would see). Rows carry no reasoning (O24): thinking text is removed
and the engine masks after the empty thinking block. Paths in a target's file arguments are kept
relative to the workspace (O33).

**Length** (O23): a row longer than the recipe's training length (16K for the 8 GB tier) is left
out, never cut: measured exactly with the engine's own tokenizer and chat template when the
engine is installed (``measure``), estimated otherwise.

**Splits**: grouped by conversation and chronological: the newest conversations (about a tenth of
the rows) are the validation slice, the rest trains; with fewer than five conversations everything
trains. Below the recipe's minimum the Personal model says "Needs more examples".

**Shape** (O71): rows are trained in the shape the personal model serves in. Each row is
re-rendered into the serving envelope of the recipe's ``serve_context`` (``shape.render``: the
small-context profile's prompt and tool exposure at 32K, a call to a tool now behind the bridge
written as the ``tool_search`` / ``tool_invoke`` steps the served model would take). A row whose
rendering changed anything is **derived**, never the main model's raw output (E3); the manifest
names the rendering and counts derived rows. ``shape="captured"`` keeps rows exactly as captured
(the paper's ablation compares the two). The picture a model that sees images gets after a browser or
computer action (5.3) is left out of a served row, as the text-only personal model is served without it;
a captured row can't hold it, so such a turn is left out there (``unsupported_content``).

**Identity**: deterministic and content-addressed: the same records and recipe give the same rows
and the same ``ds_…`` id. The ``DataSpec`` records which providers' answers it holds (D7) and the
rendering the rows have.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import shutil
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from row_bot.learning.contracts import DataSpec, Decision, Episode
from row_bot.learning.training import shape as shape_module

logger = logging.getLogger(__name__)

# 2 (L2w): rows are rendered in the serving shape (O71); held-out corrections' next turns are left out (O87).
COMPILER_VERSION = 2
FORMAT = "row-bot-dataset"
EXAMPLES_FILE = "examples.jsonl"
VALIDATION_FILE = "validation.jsonl"
MANIFEST_FILE = "dataset.json"
# Validation only once there are enough conversations to hold some back.
MIN_VALIDATION_CONVERSATIONS = 5
# About how many characters a token of Row-Bot's envelope is (L0d: 41.4K characters of system prompt,
# tool schemas and messages were 10,752 Qwen3.5 tokens); a little low, so an estimate errs long.
CHARS_PER_TOKEN = 3.5
# Requests of these purposes are the model answering the person (L0b); synthesis is partial.
REPLY_PURPOSES = frozenset({"agent", "chat_only", "finalization", "historical"})
# Providers whose terms may limit training on their answers (D7).
TERMS_PROVIDERS = {
    "openai": "OpenAI", "codex": "OpenAI", "anthropic": "Anthropic", "claude_subscription": "Anthropic",
    "google": "Google",
}
PERSONAL_MODEL_PREFIX = "row-bot-personal"
_EXTERNAL_FENCE = "<EXTERNAL_CONTENT"
_THINK = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)
_PATH_KEYS = frozenset({"path", "file", "file_path", "filename", "filepath", "directory", "dir", "folder",
                        "cwd", "source", "destination", "target", "src", "dst", "paths", "files"})


class DatasetError(RuntimeError):
    """The dataset couldn't be compiled or written; ``code`` is stable."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


# ── What a captured message becomes ─────────────────────────────────────────


class _Unsupported(ValueError):
    """Content a training row can't hold (an image, say)."""


def _text(content: Any) -> str:
    """A message's text: a string, or its text parts joined (cache markers split a prompt into
    parts). Reasoning parts are dropped; anything else (an image) can't be a row."""

    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
                continue
            if not isinstance(part, Mapping):
                raise _Unsupported("part")
            kind = str(part.get("type") or "")
            if kind == "text":
                parts.append(str(part.get("text") or ""))
            elif kind in {"thinking", "reasoning", "redacted_thinking"}:
                continue
            else:
                raise _Unsupported(kind or "part")
        return "".join(parts)
    raise _Unsupported("content")


def _no_reasoning(text: str) -> str:
    """O24: rows carry no reasoning (inline thinking is removed)."""

    return _THINK.sub("", text) if "<think" in text.lower() else text


def _data(message: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]]:
    kind = str(message.get("type") or "")
    data = message.get("data") if isinstance(message.get("data"), Mapping) else message
    return kind, data


def _tool_calls(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for call in data.get("tool_calls") or ():
        if not isinstance(call, Mapping):
            continue
        name = str(call.get("name") or "")
        if not name:
            continue
        args = call.get("args") if isinstance(call.get("args"), Mapping) else {}
        calls.append({
            "id": str(call.get("id") or f"call_{len(calls)}"),
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
        })
    return calls


def openai_message(message: Mapping[str, Any]) -> dict[str, Any] | None:
    """One captured LangChain message (``message_to_dict``) in OpenAI's chat format, as an
    OpenAI-compatible endpoint receives it. None for a message no provider is sent."""

    kind, data = _data(message)
    if kind == "system":
        return {"role": "system", "content": _text(data.get("content"))}
    if kind in {"human", "user"}:
        return {"role": "user", "content": _text(data.get("content"))}
    if kind in {"ai", "assistant", "AIMessageChunk"}:
        result: dict[str, Any] = {"role": "assistant", "content": _no_reasoning(_text(data.get("content"))).strip()}
        calls = _tool_calls(data)
        if calls:
            result["tool_calls"] = calls
        return result
    if kind == "tool":
        return {"role": "tool", "content": _text(data.get("content")), "tool_call_id": str(data.get("tool_call_id") or "")}
    return None


def _page_picture(message: Mapping[str, Any]) -> bool:
    """The browser's or the computer's picture after an action (``pictures.is_page_picture``): the text-only
    personal model is served without it, so its served rows leave it out."""

    from row_bot.learning.pictures import is_page_picture

    return is_page_picture(message)


def _is_person(message: Mapping[str, Any]) -> bool:
    """A message the person sent: not Row-Bot's turn context, nor a note it sent the model (that stays in
    the row as sent, but the turn's targets still start after the person's own message)."""

    from row_bot.learning.corrections import is_row_bot_note
    from row_bot.turn_context import is_person_message

    kind, data = _data(message)
    return is_person_message({**dict(data), "type": kind}) and not is_row_bot_note(message)


# ── Paths (O33) ─────────────────────────────────────────────────────────────


def _relative(value: str, roots: Sequence[str]) -> str | None:
    """``value`` relative to a workspace root it lies in (with ``/``), or None."""

    for root in roots:
        for flavour in (PureWindowsPath, PurePosixPath):
            try:
                path, base = flavour(value), flavour(root)
                if not path.is_absolute() or not base.is_absolute():
                    continue
                relative = path.relative_to(base)
            except ValueError:
                continue
            text = relative.as_posix()
            return text if text not in ("", ".") else "."
    return None


def _relative_arguments(arguments: Any, roots: Sequence[str]) -> tuple[Any, int]:
    if not roots:
        return arguments, 0
    changed = 0
    if isinstance(arguments, dict):
        result = {}
        for key, value in arguments.items():
            if str(key).lower() in _PATH_KEYS:
                if isinstance(value, str):
                    relative = _relative(value, roots)
                    if relative is not None:
                        value, changed = relative, changed + 1
                elif isinstance(value, list):
                    items = []
                    for item in value:
                        relative = _relative(item, roots) if isinstance(item, str) else None
                        items.append(item if relative is None else relative)
                        changed += relative is not None
                    value = items
            elif isinstance(value, (dict, list)):
                value, inner = _relative_arguments(value, roots)
                changed += inner
            result[key] = value
        return result, changed
    if isinstance(arguments, list):
        items = []
        for item in arguments:
            item, inner = _relative_arguments(item, roots)
            changed += inner
            items.append(item)
        return items, changed
    return arguments, 0


def _relative_calls(message: dict[str, Any], roots: Sequence[str]) -> int:
    changed = 0
    for call in message.get("tool_calls") or ():
        function = call.get("function") or {}
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except ValueError:
            continue
        updated, count = _relative_arguments(arguments, roots)
        if count:
            function["arguments"] = json.dumps(updated, ensure_ascii=False)
            changed += count
    return changed


# ── The compiled dataset ────────────────────────────────────────────────────


@dataclass(frozen=True)
class Example:
    """One training row and where it came from (ids only)."""

    id: str  # the episode (turn) it is
    conversation_id: str
    thread_id: str
    started_at: float
    provider: str
    historical: bool
    row: Mapping[str, Any]
    estimated_tokens: int
    tokens: int | None = None
    # Re-rendered into the serving shape (O71): derived, never the model's raw output (E3).
    derived: bool = False


@dataclass(frozen=True)
class CompiledDataset:
    """The dataset: its rows split for training and validation, and what was left out and why."""

    id: str
    sha256: str
    recipe: str
    max_seq_length: int
    train: tuple[Example, ...]
    validation: tuple[Example, ...]
    left_out: Mapping[str, int]
    providers: tuple[str, ...]
    corrections_waiting: int = 0
    measured: bool = False
    paths_made_relative: int = 0
    # How the rows were shaped (``shape.describe``) and what rendering changed (counts).
    rendering: Mapping[str, Any] = field(default_factory=lambda: shape_module.describe(None))

    @property
    def examples(self) -> int:
        """Rows that train (the validation slice is held back)."""

        return len(self.train)

    @property
    def conversations(self) -> tuple[str, ...]:
        return tuple(sorted({item.conversation_id for item in (*self.train, *self.validation)}))

    @property
    def terms_note(self) -> bool:
        """D7: the data holds answers from a provider whose terms may limit training on them."""

        return any(provider in TERMS_PROVIDERS for provider in self.providers)

    @property
    def rendering_name(self) -> str:
        return str(self.rendering.get("name") or "captured")

    def data_spec(self) -> DataSpec:
        return DataSpec(dataset_id=self.id, sha256=self.sha256, examples=self.examples,
                        sources=("main_model_work",) if self.train else (), providers=self.providers[:64],
                        rendering=self.rendering_name)

    def manifest(self) -> dict[str, Any]:
        """What the dataset is, without its content: counts, ids and providers (for the release)."""

        dates = [item.started_at for item in (*self.train, *self.validation)]
        rows = (*self.train, *self.validation)
        return {
            "format": FORMAT, "version": COMPILER_VERSION, "id": self.id, "sha256": self.sha256,
            "recipe": self.recipe, "max_seq_length": self.max_seq_length, "measured": self.measured,
            "examples": self.examples, "validation": len(self.validation),
            "historical": sum(item.historical for item in self.train),
            # The shape the rows are in, and which rows rendering changed (derived, E3).
            "rendering": dict(self.rendering), "derived": sum(item.derived for item in rows),
            "derived_ids": sorted(item.id for item in rows if item.derived),
            "left_out": dict(sorted(self.left_out.items())), "providers": list(self.providers),
            "terms_note": self.terms_note, "corrections_waiting": self.corrections_waiting,
            "paths_made_relative": self.paths_made_relative,
            "first_at": min(dates) if dates else None, "last_at": max(dates) if dates else None,
            "conversations": list(self.conversations),
            "example_ids": sorted(item.id for item in self.train),
            "validation_ids": sorted(item.id for item in self.validation),
        }


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _provider(model_ref: str | None) -> str:
    from row_bot.providers.selection import parse_model_ref

    parsed = parse_model_ref(str(model_ref or "")) if model_ref else None
    return str(parsed[0]) if parsed else "unknown"


def personal_model_ref(model_ref: str | None) -> bool:
    """Whether a model reference names one of Learning's own personal models."""

    text = str(model_ref or "").lower()
    return PERSONAL_MODEL_PREFIX in text


def estimate_tokens(row: Mapping[str, Any]) -> int:
    return math.ceil(len(_canonical(row).decode("utf-8")) / CHARS_PER_TOKEN)


Measure = Callable[[Sequence[Mapping[str, Any]], int], Sequence[tuple[int, int]]]


class _Book:
    """A store's records, read once."""

    def __init__(self, store: Any, episodes: Sequence[Episode] = ()) -> None:
        self.store = store
        self.decisions: dict[str, list[Decision]] = defaultdict(list)
        for decision in store.find("decision", limit=100_000):
            if isinstance(decision, Decision):
                self.decisions[decision.episode_id].append(decision)
        for items in self.decisions.values():
            items.sort(key=lambda item: (item.sequence, item.created_at, item.id))
        self.signalled_episodes: set[str] = set()
        self.signalled_decisions: set[str] = set()
        self.corrections_waiting = 0
        for signal in store.find("signal", limit=100_000):
            if getattr(signal, "withdrawn_at", None) is not None:
                continue
            if signal.episode_id:
                self.signalled_episodes.add(signal.episode_id)
            if signal.decision_id:
                self.signalled_decisions.add(signal.decision_id)
            if signal.kind == "teach" and (signal.note or "").strip():
                self.corrections_waiting += 1
        self.held = store.held_out_ids()
        # Corrections held out as hidden problems (O87): the turn after one (the person's
        # correction and the redo) would teach its corrected action, so it is left out too.
        self.held_corrections = store.held_out_correction_ids()
        # What each held reply looked like (its tool call ids, its text), by conversation: a later
        # turn's history repeats it, and no row may hold it even as context (O18).
        self.held_replies: dict[str, list[tuple[float, frozenset[str], str]]] = defaultdict(list)
        # The turn after a held-out correction counts as held for later turns' history too (O87).
        redone = {episode.id for episode in episodes if episode.follows_decision_id in self.held_corrections}
        for episode_id, items in self.decisions.items():
            for item in items:
                if item.kind == "model_call" and (item.id in self.held or episode_id in redone):
                    self.held_replies[item.thread_id].append((item.created_at, *_reply_signature(store, item)))

    def held_before(self, thread_id: str, at: float) -> list[tuple[frozenset[str], str]]:
        return [(ids, text) for created, ids, text in self.held_replies.get(thread_id, ()) if created <= at]


def _reply_signature(store: Any, decision: Decision) -> tuple[frozenset[str], str]:
    """A reply's tool call ids and text, to find it again in a later request's history."""

    raw = store.read_blob(decision.repaired_output_blob or decision.raw_output_blob or "")
    try:
        message = openai_message(json.loads(raw.decode("utf-8"))) if raw else None
    except (_Unsupported, ValueError, TypeError, AttributeError):
        message = None
    if not message:
        return frozenset(), ""
    ids = frozenset(str(call.get("id") or "") for call in message.get("tool_calls") or () if call.get("id"))
    return ids, str(message.get("content") or "").strip()


def _repeats_held(message: Mapping[str, Any], held: Sequence[tuple[frozenset[str], str]]) -> bool:
    ids = {str(call.get("id") or "") for call in message.get("tool_calls") or ()}
    text = str(message.get("content") or "").strip()
    return any((ids & held_ids) or (text and text == held_text) for held_ids, held_text in held)


def _scope_reason(conversation: str, *, historical: bool, goal: bool, store: Any, lookup: Any,
                  settings: Any, cache: dict[tuple[str, bool, bool], str | None]) -> str | None:
    """Why scope would no longer record this conversation (None: still the owner's own work)."""

    key = (conversation, historical, goal)
    if key in cache:
        return cache[key]
    from row_bot.learning import scope

    reason: str | None = None
    if store.is_chat_opted_out(conversation):
        reason = "opted_out"
    else:
        facts = scope.describe_thread(conversation, historical=historical, goal=goal, lookup=lookup)
        # A chat that turned incognito keeps what Learning recorded before it did, as memory keeps what it saved
        # (N11): incognito stops the recording, not the use of what came before.
        decision = scope.decide(replace(facts, incognito=False), replace(settings, enabled=True, paused=False))
        if not decision.allowed:
            reason = {
                scope.UNKNOWN_SOURCE: "conversation_gone",
                scope.SHARED_CONVERSATION: "someone_else",
                scope.NOT_OWNER: "someone_else",
                scope.OWNER_UNKNOWN: "someone_else",
                scope.MY_CHATS_OFF: "source_off",
                scope.WORKFLOWS_AND_GOALS_OFF: "source_off",
                scope.PAST_CHATS_OFF: "source_off",
            }.get(decision.reason, "not_in_scope")
    cache[key] = reason
    return reason


@dataclass(frozen=True)
class PastFrame:
    """O113 (C3): what an imported past-chat row is framed with to train in the serving shape: the newest
    live Agent request's system prompt and tools (the import keeps each step's calls and results, never the
    prompt or the tools it was sent)."""

    system: tuple[str, ...]
    tools: tuple[Mapping[str, Any], ...]

    @property
    def schemas(self) -> dict[str, Mapping[str, Any]]:
        return {str((tool.get("function") or {}).get("name") or ""): (tool.get("function") or {}).get("parameters")
                or {} for tool in self.tools}


def _agent_request(episode: Episode, book: _Book) -> tuple[str, tuple[Mapping[str, Any], ...]] | None:
    """A finished live turn's newest Agent request with a system prompt and tools: (its system text, its tools)."""

    from row_bot.learning import capture

    calls = [item for item in book.decisions.get(episode.id, []) if item.kind == "model_call"]
    for call in reversed(calls):
        request = capture.read_request(book.store, call)
        if not request or request.get("purpose") != "agent" or not request.get("tools"):
            continue
        system: list[str] = []
        for message in request.get("messages") or ():
            try:
                converted = openai_message(message) if isinstance(message, Mapping) else None
            except (_Unsupported, ValueError, TypeError, KeyError):
                converted = None
            if converted is not None and converted["role"] == "system":
                system.append(str(converted["content"]))
        tools = tuple(dict(item) for item in request["tools"] if isinstance(item, Mapping))
        if system and tools:
            return "\n\n".join(text for text in system if text), tools
    return None


def _paragraphs(text: str) -> list[str]:
    return [part for part in text.split("\n\n") if part.strip()]


def past_frame(episodes: Sequence[Episode], book: _Book) -> PastFrame | None:
    """What imported rows are framed with: the newest finished live turn's Agent request's tools and the
    static part of its system prompt, or None (nothing live yet: imported rows wait for it).

    A live prompt's sections are stable for a conversation (what changes each turn, recalled memories
    among it, goes in the turn-context message, never here), but some belong to that conversation alone:
    an Agent Profile, a Designer guide, the skills it keeps, a workflow's background notes, a compaction
    summary's guidance. An imported chat had none of them, so it keeps only the parts a live Agent request
    of another conversation also has; with no other conversation yet, the prompt is taken as it is."""

    live = [item for item in reversed(list(episodes))
            if not item.historical and item.fidelity != "partial" and item.status == "completed"]
    newest: tuple[Episode, str, tuple[Mapping[str, Any], ...]] | None = None
    shared: set[str] | None = None
    for episode in live:
        found = _agent_request(episode, book)
        if found is None:
            continue
        if newest is None:
            newest = (episode, *found)
            continue
        if episode.conversation_id != newest[0].conversation_id:
            shared = set(_paragraphs(found[0]))
            break
    if newest is None:
        return None
    _episode, text, tools = newest
    if shared is not None:
        kept = [part for part in _paragraphs(text) if part in shared]
        text = "\n\n".join(kept) or text
    return PastFrame((text,), tools)


_JSON_TYPES: dict[str, Any] = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list,
                               "object": dict}


def _arguments_fit(arguments: Any, schema: Mapping[str, Any]) -> bool:
    """Whether a past call's arguments fit the tool's schema as offered now: an object with every required
    key, no key the schema doesn't name (unless it allows more), and each named key of its declared type."""

    if not isinstance(arguments, dict):
        return False
    properties = schema.get("properties") or {}
    if any(key not in arguments for key in schema.get("required") or ()):
        return False
    if properties and not schema.get("additionalProperties") and any(key not in properties for key in arguments):
        return False
    for key, value in arguments.items():
        expected = (properties.get(key) or {}).get("type") if isinstance(properties.get(key), Mapping) else None
        if isinstance(expected, str) and expected in _JSON_TYPES and value is not None:
            if not isinstance(value, _JSON_TYPES[expected]) or (expected in ("integer", "number")
                                                                and isinstance(value, bool)):
                return False
    return True


def _past_steps(messages: list[dict[str, Any]], frame: PastFrame, counts: Counter[str] | None) -> None:
    """An imported step stays a target only when its tool is offered now and its arguments fit; else it is
    context only (counted)."""

    schemas = frame.schemas
    for message in messages:
        if message.get("role") != "assistant" or not message.get("weight") or not message.get("tool_calls"):
            continue
        why = None
        for call in message["tool_calls"]:
            function = call.get("function") or {}
            name = str(function.get("name") or "")
            if name not in schemas:
                why = "tool_not_offered"
                break
            raw = function.get("arguments")
            try:
                arguments = json.loads(raw or "{}") if isinstance(raw, str) else raw
            except ValueError:
                arguments = None
            if not _arguments_fit(arguments, schemas[name]):
                why = "arguments_invalid"
                break
        if why:
            message["weight"] = 0
        if counts is not None:
            counts[why or "kept"] += 1


def _example(episode: Episode, book: _Book, *, roots: Sequence[str],
             envelope: shape_module.Envelope | None = None,
             stats: shape_module.Stats | None = None, frame: PastFrame | None = None,
             past_steps: Counter[str] | None = None) -> tuple[Example | None, str, int]:
    """One episode as an example, or why it is left out. Returns (example, reason, paths changed).

    With an ``envelope`` the row is rendered in the serving shape (O71); ``stats`` adds up what that
    changed. With a ``frame`` an imported past-chat row is framed and rendered like a live one (O113),
    its tool steps after the person's message kept as targets; ``past_steps`` counts what became of them."""

    from row_bot.learning import capture

    decisions = book.decisions.get(episode.id, [])
    if episode.status != "completed":
        return None, f"not_{'finished' if episode.status == 'running' else episode.status}", 0
    if episode.incomplete:
        return None, "incomplete", 0
    if episode.fidelity == "partial" and not episode.historical:
        return None, "partial", 0
    if episode.id in book.signalled_episodes or any(item.id in book.signalled_decisions for item in decisions):
        return None, "corrected_or_stopped", 0
    if episode.follows_decision_id and episode.follows_decision_id in book.held_corrections:
        return None, "held_out", 0  # O87: the turn after a held-out correction shows its corrected action
    retried = {item.retry_of for item in decisions if item.retry_of}
    calls = [item for item in decisions if item.kind == "model_call" and item.id not in retried]
    if any(item.status != "ok" for item in calls):
        return None, "reply_failed", 0
    if any(item.kind == "tool_call" and item.status != "ok" for item in decisions):
        return None, "tool_failed", 0
    if any(item.kind == "approval" and item.status != "ok" for item in decisions):
        return None, "corrected_or_stopped", 0
    if not calls:
        return None, "no_reply", 0
    # O18: a decision the audit vault holds is never in a row, as target or as context. The row
    # stops just before the first one (its request: the turn so far), so the turn's earlier
    # replies still teach; a turn whose first reply is held gives no row.
    held = next((index for index, item in enumerate(calls) if item.id in book.held), None)
    if held == 0 or any(item.id in book.held for item in decisions if item.kind != "model_call"):
        return None, "held_out", 0
    last = calls[held] if held is not None else calls[-1]
    model_ref = last.model_ref or episode.model_ref
    if personal_model_ref(model_ref):
        return None, "personal_model", 0
    request = capture.read_request(book.store, last)
    if request is None:
        return None, "purged", 0
    if request.get("purpose") not in REPLY_PURPOSES:
        return None, "not_a_reply", 0
    output_sha = last.repaired_output_blob or last.raw_output_blob
    raw = book.store.read_blob(output_sha or "") if held is None else b"null"
    if raw is None:
        return None, "purged", 0
    try:
        output = json.loads(raw.decode("utf-8"))
        sent = [message for message in request["messages"] if isinstance(message, Mapping)]
        pictures = 0
        if envelope is not None and (not episode.historical or frame is not None):
            # The browser's or the computer's picture: the personal model is served without it (text only).
            pictures = sum(1 for message in sent if _page_picture(message))
            sent = [message for message in sent if not _page_picture(message)]
        person = max((index for index, message in enumerate(sent) if _is_person(message)), default=-1)
        messages: list[dict[str, Any]] = []
        starts: list[int] = []  # where each of the person's messages is in ``messages``
        system: list[str] = []
        for index, message in enumerate(sent):
            converted = openai_message(message)
            if converted is None:
                continue
            if converted["role"] == "system":
                # One system message, first (as a system-first endpoint receives them).
                system.append(str(converted["content"]))
                continue
            if converted["role"] == "assistant":
                converted["weight"] = 1 if index > person and (not episode.historical or frame is not None) else 0
            elif _is_person(message):
                starts.append(len(messages))
            messages.append(converted)
        # O18: an earlier turn's reply the vault holds is in this history too: the row starts at the
        # person's next message after it (Row-Bot's own compaction drops history the same way).
        held_replies = book.held_before(episode.thread_id, last.created_at)
        if held_replies:
            seen = [index for index, message in enumerate(messages) if message["role"] == "assistant"
                    and not message.get("weight") and _repeats_held(message, held_replies)]
            if seen:
                start = next((index for index in starts if index > seen[-1]), None)
                if start is None:
                    return None, "held_out", 0
                messages = messages[start:]
        answer = openai_message(output) if output is not None else None
    except _Unsupported:
        return None, "unsupported_content", 0
    except (ValueError, TypeError, KeyError):
        return None, "unreadable", 0
    if held is None:
        if answer is None or answer["role"] != "assistant":
            return None, "unreadable", 0
        if not answer.get("content") and not answer.get("tool_calls"):
            return None, "empty_reply", 0
        if episode.historical and answer.get("tool_calls") and frame is None:
            return None, "past_chat_tool_step", 0
        answer["weight"] = 1
        messages.append(answer)
    else:
        while messages and messages[-1]["role"] != "assistant":  # the row ends at its last reply
            messages.pop()
    tools: list[dict[str, Any]] | None = [dict(item) for item in request.get("tools") or ()
                                          if isinstance(item, Mapping)] or None
    agent = request.get("purpose") == "agent"
    if episode.historical and frame is not None:
        # O113: framed as the newest live Agent request was (its prompt and tools), then rendered like it.
        system, tools, agent = list(frame.system), [dict(item) for item in frame.tools], True
        _past_steps(messages, frame, past_steps)
    derived = False
    if envelope is not None and (not episode.historical or frame is not None):
        # O71: the row as the personal model will be served (its prompt, its tool exposure, its calls).
        try:
            system, messages, tools, rendered = shape_module.render(
                system, messages, tools, envelope, agent=agent)
        except shape_module.RenderError:
            return None, "unrenderable", 0
        rendered.pictures_removed = pictures
        derived = rendered.changed
        if stats is not None and derived:
            for name in ("tools_deferred", "calls_bridged", "searches_added", "state_removed", "pictures_removed"):
                setattr(stats, name, getattr(stats, name) + getattr(rendered, name))
            stats.system_changed = stats.system_changed or rendered.system_changed
    if system:
        # One system message, first (as a system-first endpoint receives them).
        messages.insert(0, {"role": "system", "content": "\n\n".join(text for text in system if text)})
    targets = [message for message in messages if message.get("role") == "assistant" and message.get("weight")]
    if not targets:
        return None, "held_out" if held is not None else "no_target", 0
    if any(_EXTERNAL_FENCE in str(message.get("content") or "") for message in targets):
        return None, "tool_content_in_answer", 0
    changed = sum(_relative_calls(message, roots) for message in targets)
    row: dict[str, Any] = {"messages": messages}
    if tools:
        row["tools"] = tools
    return Example(
        id=episode.id, conversation_id=episode.conversation_id, thread_id=episode.thread_id,
        started_at=float(episode.started_at), provider=_provider(model_ref), historical=episode.historical,
        row=row, estimated_tokens=estimate_tokens(row), derived=derived,
    ), "", changed


def _split(examples: list[Example], share: float) -> tuple[list[Example], list[Example]]:
    """Grouped by conversation, chronological: the newest conversations validate."""

    by_conversation: dict[str, list[Example]] = defaultdict(list)
    for item in examples:
        by_conversation[item.conversation_id].append(item)
    if len(by_conversation) < MIN_VALIDATION_CONVERSATIONS or share <= 0:
        return examples, []
    order = sorted(by_conversation, key=lambda key: (max(item.started_at for item in by_conversation[key]), key))
    wanted = max(1, math.floor(len(examples) * share))
    held: set[str] = set()
    count = 0
    for conversation in reversed(order):
        size = len(by_conversation[conversation])
        if held and count + size > wanted:
            break
        held.add(conversation)
        count += size
        if count >= wanted:
            break
    train = [item for item in examples if item.conversation_id not in held]
    validation = [item for item in examples if item.conversation_id in held]
    return train, validation


def compile_dataset(
    store: Any,
    recipe: Any,
    *,
    lookup: Any = None,
    settings: Any = None,
    measure: Measure | None = None,
    workspace_roots: Sequence[str] = (),
    refresh_vault: bool = True,
    shape: str = "serving",
    envelope: shape_module.Envelope | None = None,
) -> CompiledDataset:
    """The dataset for ``recipe`` from what Learning recorded (see the module's docstring).

    ``measure`` counts each row's tokens and target tokens with the engine (exact); without it the
    length is estimated. ``refresh_vault`` lets the audit vault take its share first (O18).
    ``shape``: ``serving`` renders rows in the serving envelope of the recipe's ``serve_context``
    (``envelope`` overrides Row-Bot's own, for tests); ``captured`` keeps them as captured.
    """

    from row_bot import learning as gate
    from row_bot.learning import holdout

    if not gate.store_exists(store.root):
        raise DatasetError("no_data")
    if shape not in shape_module.SHAPES:
        raise DatasetError("unknown_shape")
    # O87: corrections held out as hidden problems are marked before anything reads the marks.
    holdout.mark_corrections(store)
    if refresh_vault:
        try:
            from row_bot.learning import evaluation

            evaluation.refresh_audit(store=store, asked_by="training data")
        except Exception as error:  # noqa: BLE001 - nothing trains on what the vault may still claim
            logger.warning("Learning couldn't update its hidden tests before compiling training data (%s)",
                           type(error).__name__)
            raise DatasetError("vault_unavailable") from error
    settings = settings if settings is not None else gate.read_settings(store.root)
    if shape == "serving" and envelope is None:
        envelope = shape_module.envelope_for(int(recipe.serve_context))
    elif shape == "captured":
        envelope = None
    episodes = [item for item in store.find("episode", limit=100_000) if isinstance(item, Episode)]
    episodes.sort(key=lambda item: (item.started_at, item.id))
    book = _Book(store, episodes)
    # O113: imported rows train in the serving shape, framed like the newest live Agent request; until there is
    # one they wait (left out as "past_chat_waiting"). The captured shape (the paper's ablation) keeps them as
    # they were imported.
    frame = past_frame(episodes, book) if envelope is not None else None
    past_steps: Counter[str] = Counter()
    left_out: Counter[str] = Counter()
    scope_cache: dict[tuple[str, bool, bool], str | None] = {}
    candidates: list[Example] = []
    changes: dict[str, shape_module.Stats] = {}
    paths = 0
    for episode in episodes:
        reason = _scope_reason(episode.conversation_id, historical=episode.historical,
                               goal=episode.source == "goal", store=store, lookup=lookup, settings=settings,
                               cache=scope_cache)
        if reason is None and episode.historical and envelope is not None and frame is None:
            reason = "past_chat_waiting"
        if reason is None:
            stats = shape_module.Stats()
            example, reason, changed = _example(episode, book, roots=workspace_roots, envelope=envelope,
                                                stats=stats, frame=frame, past_steps=past_steps)
            if example is not None:
                candidates.append(example)
                changes[example.id] = stats
                paths += changed
                continue
        left_out[reason] += 1
    max_seq = int(recipe.max_seq_length)
    measured = measure is not None
    kept: list[Example] = []
    if measure is not None and candidates:
        lengths = list(measure([item.row for item in candidates], max_seq))
        if len(lengths) != len(candidates):
            raise DatasetError("measure_failed")
        for item, (tokens, target_tokens) in zip(candidates, lengths):
            if tokens < 0:
                left_out["unrenderable"] += 1
            elif tokens > max_seq:
                left_out["too_long"] += 1
            elif target_tokens <= 0:
                left_out["no_target"] += 1
            else:
                kept.append(replace(item, tokens=int(tokens)))
    else:
        for item in candidates:
            if item.estimated_tokens > max_seq:
                left_out["too_long"] += 1
            else:
                kept.append(item)
    train, validation = _split(kept, float(recipe.validation_share))
    rendering = shape_module.describe(envelope)
    if envelope is not None:
        rendered = [changes[item.id] for item in kept if item.derived and item.id in changes]
        rendering.update({
            "rows": len(rendered),
            "tools_deferred": sum(item.tools_deferred for item in rendered),
            "calls_bridged": sum(item.calls_bridged for item in rendered),
            "searches_added": sum(item.searches_added for item in rendered),
            "system_rewritten": sum(item.system_changed for item in rendered),
            "state_removed": sum(item.state_removed for item in rendered),
            "pictures_removed": sum(item.pictures_removed for item in rendered),
            # O113: imported tool steps kept as targets, and those that became context only (and why).
            "past_steps": {key: past_steps[key] for key in ("kept", "tool_not_offered", "arguments_invalid")},
        })
    body = {
        "compiler": COMPILER_VERSION, "recipe": recipe.name, "max_seq_length": max_seq,
        "rendering": rendering.get("name"),
        "train": [item.row for item in train], "validation": [item.row for item in validation],
    }
    digest = hashlib.sha256(_canonical(body)).hexdigest()
    providers = tuple(sorted({item.provider for item in kept}))
    return CompiledDataset(
        id=f"ds_{digest[:32]}", sha256=digest, recipe=recipe.name, max_seq_length=max_seq, train=tuple(train),
        validation=tuple(validation), left_out=dict(left_out), providers=providers,
        corrections_waiting=book.corrections_waiting, measured=measured, paths_made_relative=paths,
        rendering=rendering,
    )


def write(dataset: CompiledDataset, folder: Path) -> int:
    """Write the rows (``examples.jsonl``, ``validation.jsonl``) and the manifest into a job's lease
    folder; returns how many rows train. The manifest names the conversations, so deleting one of
    them can find the runs that hold it."""

    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for name, items in ((EXAMPLES_FILE, dataset.train), (VALIDATION_FILE, dataset.validation)):
        text = "".join(json.dumps(item.row, ensure_ascii=False, sort_keys=True) + "\n" for item in items)
        temporary = folder / f".{name}.tmp"
        temporary.write_bytes(text.encode("utf-8"))
        temporary.replace(folder / name)
    manifest = folder / MANIFEST_FILE
    manifest.write_bytes(json.dumps(dataset.manifest(), indent=1, sort_keys=True).encode("utf-8"))
    return dataset.examples


def read_manifest(folder: Path) -> dict[str, Any] | None:
    try:
        value = json.loads((Path(folder) / MANIFEST_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) and value.get("format") == FORMAT else None


# ── A summary for the Personal model view ───────────────────────────────────


@dataclass(frozen=True)
class DatasetSummary:
    """How many examples there are, how many training needs, and how many are new since the
    newest version was trained (counts only)."""

    examples: int
    needed: int
    new_examples: int
    left_out: int
    at: float
    dataset_id: str | None = None
    by_reason: Mapping[str, int] = field(default_factory=dict)

    @property
    def enough(self) -> bool:
        return self.examples >= self.needed


def summarize(dataset: CompiledDataset, recipe: Any, *, trained_ids: Iterable[str] = (), at: float = 0.0) -> DatasetSummary:
    known = set(trained_ids)
    return DatasetSummary(
        examples=dataset.examples, needed=int(recipe.min_examples),
        # New work, wherever the split puts it (the newest conversations validate first).
        new_examples=sum(1 for item in (*dataset.train, *dataset.validation) if item.id not in known),
        left_out=sum(dataset.left_out.values()), at=at, dataset_id=dataset.id,
        by_reason=dict(dataset.left_out),
    )


# ── Deletion reaches training runs ──────────────────────────────────────────


def runs_holding(thread_ids: Iterable[str], root: Path | None = None) -> list[str]:
    """Training runs whose leased data came from any of these conversations."""

    from row_bot.learning.training import jobs as jobs_module

    wanted = {str(item) for item in thread_ids if item}
    folder = jobs_module.runs_dir(root)
    found: list[str] = []
    if not wanted or not folder.is_dir():
        return found
    for entry in sorted(folder.iterdir()):
        manifest = read_manifest(entry / "data")
        if manifest is None:
            continue
        if wanted & set(manifest.get("conversations") or ()):
            found.append(entry.name)
    return found


def forget_conversations(thread_ids: Iterable[str], root: Path | None = None) -> int:
    """A conversation was deleted or switched off: a run that hasn't finished training on it is
    discarded (its leased rows and checkpoints go), so nothing trains on it again; the next run
    is planned without it. A version already trained keeps its files and is marked through its
    release (Retrain without it, L2c2). Returns how many runs were discarded."""

    from row_bot.learning.training import jobs as jobs_module

    found = runs_holding(thread_ids, root)
    if not found:
        return 0
    discarded = 0
    runner = __import__("sys").modules.get("row_bot.learning.runner")
    supervisor = runner.loaded().supervisor if runner is not None and runner.loaded() is not None else None
    jobs = supervisor.jobs if supervisor is not None else jobs_module.TrainingJobs(root)
    for job_id in found:
        job = jobs.get(job_id) if jobs.store.exists() else None
        if job is not None and job.state in ("planned", "waiting", *jobs_module.LIVE, "paused"):
            try:
                if supervisor is not None and supervisor.live_job_id() == job_id:
                    supervisor.stop_now("you")
                    job = jobs.get(job_id) or job
                jobs.move(job_id, "stopped", reason="source_deleted")
            except Exception:  # noqa: BLE001 - the data goes whatever the state says
                logger.debug("Learning couldn't stop a run whose data was deleted", exc_info=True)
            discarded += 1
            jobs_module.remove_run(job_id, root)
        elif job is None:
            jobs_module.remove_run(job_id, root)
        else:
            # Trained already: the leased rows are no longer needed (the release keeps only ids).
            data = jobs_module.run_dir(job_id, root) / "data"
            if data.is_dir() and not data.is_symlink():
                shutil.rmtree(data, ignore_errors=True)
    if discarded:
        logger.info("Learning discarded %d training run(s) whose data came from a deleted chat", discarded)
    return discarded


__all__ = [
    "COMPILER_VERSION",
    "CompiledDataset",
    "DatasetError",
    "DatasetSummary",
    "EXAMPLES_FILE",
    "Example",
    "MANIFEST_FILE",
    "TERMS_PROVIDERS",
    "VALIDATION_FILE",
    "compile_dataset",
    "estimate_tokens",
    "forget_conversations",
    "openai_message",
    "personal_model_ref",
    "read_manifest",
    "runs_holding",
    "summarize",
    "write",
]
