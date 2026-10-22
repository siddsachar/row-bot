"""Learning's records: plain, versioned, validated data.

Every record is a frozen dataclass with a ``KIND``, a ``SCHEMA_VERSION`` and one
canonical text form:

- **JSONL** for the event-like records captured or produced as work happens:
  :class:`Episode`, :class:`Decision`, :class:`Signal`, :class:`TestRun` and
  :class:`Attempt` (the archive);
- a **manifest** (pretty JSON) for the things people review and keep:
  :class:`LessonRecord`, :class:`TaskFixture`, :class:`Release` and :class:`Job`.

Both forms round-trip losslessly through :meth:`Record.to_dict` /
:meth:`Record.from_dict`. Parsing is strict: an unknown kind or key, a missing
key or a wrong type is rejected with :class:`ContractError`. Versions follow one
rule: a newer version than this app knows is rejected (``newer``); a version
below ``MINIMUM_SCHEMA_VERSION`` is rejected (``retired``); a version in between
is migrated one step at a time by the class's ``MIGRATIONS`` and rejected if a
step is missing. Adding a field is a version bump with a migration step.

Records hold data and validation only. They have no place for credentials: no
field holds a key, token or password, and no field is a free-form mapping.
Content (what a model saw, its raw output, tool arguments and results) lives in
content-addressed blobs; L0b's capture redacts secrets *before* it writes a blob
or builds a record and labels the result's ``fidelity`` (``exact`` when nothing
was removed, ``redacted`` when something was, ``partial`` when the record is
incomplete, as for imported past chats). Free-text fields (a Teach note, a lesson,
a hypothesis) are bounded and are redacted by whoever writes them.

Internally a person's correction is a :class:`Signal`, never "feedback" (that
word belongs to ``send_feedback`` reports).
"""

from __future__ import annotations

import dataclasses
import functools
import json
import math
import re
import types
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal, Union, get_args, get_origin, get_type_hints

MAX_RECORD_BYTES = 8 * 1024 * 1024

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class ContractError(ValueError):
    """A record that is not valid; ``code`` is stable, ``detail`` says where."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


def new_id(prefix: str) -> str:
    """A fresh record id such as ``ep_3f2c…``."""

    if not re.fullmatch(r"[a-z]{1,16}", prefix):
        raise ContractError("invalid_field", "id prefix")
    return f"{prefix}_{uuid.uuid4().hex}"


def valid_thread_id(value: str) -> bool:
    """Whether a conversation id is safe to store and name files with."""

    # The same rule as thread_cleanup.normalize_thread_id: ids name files too.
    return (
        isinstance(value, str)
        and 0 < len(value) <= 512
        and value == value.strip()
        and value not in {".", ".."}
        and "/" not in value
        and "\\" not in value
        and not any(ord(char) < 32 or ord(char) == 127 for char in value)
    )


# ── Field metadata ──────────────────────────────────────────────────────────


def _field(
    default: Any = dataclasses.MISSING,
    *,
    text: int | None = None,
    pattern: str | None = None,
    blob: bool = False,
    items: int | None = None,
    minimum: float | None = None,
    maximum: float | None = None,
) -> Any:
    """A dataclass field with Learning's validation metadata.

    ``pattern``: ``id`` (record ids), ``thread`` (conversation ids) or ``sha256``.
    ``blob``: the value names a blob in ``learning/blobs`` (counted by the store).
    """

    meta = {
        key: value
        for key, value in {
            "text": text,
            "pattern": pattern,
            "blob": blob or None,
            "items": items,
            "minimum": minimum,
            "maximum": maximum,
        }.items()
        if value is not None
    }
    if default is dataclasses.MISSING:
        return field(metadata=meta)
    return field(default=default, metadata=meta)


# ── Generic validation and codec ────────────────────────────────────────────


def _optional_inner(hint: Any) -> Any | None:
    """``X`` for ``X | None``; None for any other hint."""

    if get_origin(hint) in (Union, types.UnionType):
        args = [arg for arg in get_args(hint) if arg is not type(None)]
        if len(args) == 1 and len(get_args(hint)) == 2:
            return args[0]
    return None


@functools.cache
def _hints(cls: type) -> dict[str, Any]:
    return get_type_hints(cls)


def _check_scalar(hint: Any, value: Any, meta: Mapping[str, Any], where: str) -> Any:
    if get_origin(hint) is Literal:
        if type(value) is not str or value not in get_args(hint):
            raise ContractError("invalid_field", where)
        return value
    if hint is str:
        if type(value) is not str:
            raise ContractError("invalid_field", where)
        if "\0" in value or len(value) > int(meta.get("text", 512)):
            raise ContractError("invalid_field", where)
        pattern = meta.get("pattern")
        if pattern == "id" and not _ID.fullmatch(value):
            raise ContractError("invalid_field", where)
        if pattern == "sha256" and not _SHA256.fullmatch(value):
            raise ContractError("invalid_field", where)
        if pattern == "thread" and not valid_thread_id(value):
            raise ContractError("invalid_field", where)
        return value
    if hint is bool:
        if type(value) is not bool:
            raise ContractError("invalid_field", where)
        return value
    if hint is int:
        if type(value) is not int:
            raise ContractError("invalid_field", where)
    elif hint is float:
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ContractError("invalid_field", where)
        value = float(value)
    else:
        raise ContractError("invalid_field", f"{where} has an unsupported type")
    if "minimum" in meta and value < meta["minimum"]:
        raise ContractError("invalid_field", where)
    if "maximum" in meta and value > meta["maximum"]:
        raise ContractError("invalid_field", where)
    return value


def _check(hint: Any, value: Any, meta: Mapping[str, Any], where: str) -> Any:
    """Validate one value against its hint; returns it normalised (floats)."""

    inner = _optional_inner(hint)
    if inner is not None:
        return None if value is None else _check(inner, value, meta, where)
    if get_origin(hint) is tuple:
        item_hint = get_args(hint)[0]
        if type(value) is not tuple or len(value) > int(meta.get("items", 1024)):
            raise ContractError("invalid_field", where)
        item_meta = {key: item for key, item in meta.items() if key != "items"}
        return tuple(_check(item_hint, item, item_meta, f"{where}[]") for item in value)
    if isinstance(hint, type) and dataclasses.is_dataclass(hint):
        if type(value) is not hint:
            raise ContractError("invalid_field", where)
        return value
    return _check_scalar(hint, value, meta, where)


def _encode(value: Any) -> Any:
    if isinstance(value, _Part):
        return value._fields_dict()
    if isinstance(value, tuple):
        return [_encode(item) for item in value]
    return value


def _decode(hint: Any, raw: Any, where: str) -> Any:
    """JSON value → Python value of the hint's shape (lists → tuples, objects →
    parts). Types are checked by the record's constructor afterwards."""

    inner = _optional_inner(hint)
    if inner is not None:
        return None if raw is None else _decode(inner, raw, where)
    if get_origin(hint) is tuple:
        if type(raw) is not list:
            raise ContractError("invalid_field", where)
        item_hint = get_args(hint)[0]
        return tuple(_decode(item_hint, item, f"{where}[]") for item in raw)
    if isinstance(hint, type) and issubclass(hint, _Part):
        return hint._from_fields(raw, where)
    return raw


@dataclass(frozen=True)
class _Part:
    """A validated value object. Subclasses declare fields with :func:`_field`."""

    def __post_init__(self) -> None:
        hints = _hints(type(self))
        for item in dataclasses.fields(self):
            where = f"{type(self).__name__}.{item.name}"
            checked = _check(hints[item.name], getattr(self, item.name), item.metadata, where)
            if checked is not getattr(self, item.name):
                object.__setattr__(self, item.name, checked)
        self._validate()

    def _validate(self) -> None:
        """Cross-field rules; subclasses override."""

    def _fields_dict(self) -> dict[str, Any]:
        return {item.name: _encode(getattr(self, item.name)) for item in dataclasses.fields(self)}

    @classmethod
    def _from_fields(cls, raw: Any, where: str = "") -> Any:
        where = where or cls.__name__
        if not isinstance(raw, dict):
            raise ContractError("invalid_record", where)
        names = {item.name for item in dataclasses.fields(cls)}
        unknown = sorted(set(raw) - names)
        if unknown:
            raise ContractError("unknown_field", f"{where}.{unknown[0]}")
        hints = _hints(cls)
        values: dict[str, Any] = {}
        for item in dataclasses.fields(cls):
            if item.name not in raw:
                required = (
                    item.default is dataclasses.MISSING and item.default_factory is dataclasses.MISSING
                )
                if required:
                    raise ContractError("missing_field", f"{where}.{item.name}")
                continue
            values[item.name] = _decode(hints[item.name], raw[item.name], f"{where}.{item.name}")
        try:
            return cls(**values)
        except ContractError:
            raise
        except (TypeError, ValueError) as error:
            raise ContractError("invalid_record", where) from error

    def blob_refs(self) -> frozenset[str]:
        """Every blob this value names, nested parts included."""

        found: set[str] = set()
        for item in dataclasses.fields(self):
            value = getattr(self, item.name)
            values = value if isinstance(value, tuple) else (value,)
            for entry in values:
                if isinstance(entry, _Part):
                    found |= entry.blob_refs()
                elif item.metadata.get("blob") and isinstance(entry, str):
                    found.add(entry)
        return frozenset(found)


@dataclass(frozen=True)
class Record(_Part):
    """A top-level record with a kind, a schema version and a text form."""

    KIND: ClassVar[str] = ""
    SCHEMA_VERSION: ClassVar[int] = 1
    MINIMUM_SCHEMA_VERSION: ClassVar[int] = 1
    # One step per older version: MIGRATIONS[v] turns a v dict into a v + 1 dict.
    MIGRATIONS: ClassVar[Mapping[int, Callable[[dict[str, Any]], dict[str, Any]]]] = {}
    FORM: ClassVar[Literal["jsonl", "manifest"]] = "jsonl"

    def to_dict(self) -> dict[str, Any]:
        return {"record": self.KIND, "schema_version": self.SCHEMA_VERSION, **self._fields_dict()}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Any:
        if not isinstance(data, Mapping):
            raise ContractError("invalid_record", cls.KIND)
        if data.get("record") != cls.KIND:
            raise ContractError("wrong_record_kind", str(data.get("record"))[:64])
        version = data.get("schema_version")
        if type(version) is not int:
            raise ContractError("missing_schema_version", cls.KIND)
        if version > cls.SCHEMA_VERSION:
            raise ContractError("unsupported_schema_version", f"{cls.KIND} v{version} is newer than this app")
        if version < cls.MINIMUM_SCHEMA_VERSION:
            raise ContractError("unsupported_schema_version", f"{cls.KIND} v{version} is retired")
        body = dict(data)
        while version < cls.SCHEMA_VERSION:
            step = cls.MIGRATIONS.get(version)
            if step is None:
                raise ContractError("unsupported_schema_version", f"{cls.KIND} v{version} has no migration")
            body = dict(step(dict(body)))
            version += 1
            body["schema_version"] = version
        body.pop("record", None)
        body.pop("schema_version", None)
        return cls._from_fields(body, cls.KIND)


# ── Shared parts ────────────────────────────────────────────────────────────

Fidelity = Literal["exact", "redacted", "partial"]
RefKind = Literal[
    "episode",
    "decision",
    "signal",
    "lesson",
    "fixture",
    "test_run",
    "attempt",
    "release",
    "job",
    "memory",
    "skill",
    "thread",
]


@dataclass(frozen=True)
class Ref(_Part):
    """A pointer to another record or object (evidence, inputs)."""

    kind: RefKind
    id: str = _field(pattern="thread")


@dataclass(frozen=True)
class Cost(_Part):
    """What an attempt or job spent. Local model calls count as zero tokens."""

    tokens: int = _field(0, minimum=0)
    gpu_seconds: float = _field(0.0, minimum=0)


@dataclass(frozen=True)
class Pin(_Part):
    """One versioned component of a release (a base model, a recipe, a runtime)."""

    name: str = _field(text=256)
    version: str = _field("", text=128)
    sha256: str | None = _field(None, pattern="sha256")


DataSource = Literal["main_model_work", "corrections", "practice", "replay", "general"]


@dataclass(frozen=True)
class DataSpec(_Part):
    """The data a release was built from (D6)."""

    dataset_id: str = _field(pattern="id")
    sha256: str | None = _field(None, pattern="sha256")
    examples: int = _field(0, minimum=0)
    sources: tuple[DataSource, ...] = _field((), items=8)
    # Providers whose outputs are in the data, for the provider-terms note (D7).
    providers: tuple[str, ...] = _field((), text=64, items=64)
    # The shape the rows were trained in (O71): "serving-v1@32768" (the serving envelope at that
    # context; its rows are derived, E3) or "captured" (as the main model was sent them).
    rendering: str | None = _field(None, text=64)


@dataclass(frozen=True)
class ReleaseIdentity(_Part):
    """What makes a release reproducible (L2c). Every part is optional for now."""

    base: Pin | None = None
    tokenizer: Pin | None = None
    template: Pin | None = None
    adapter: Pin | None = None
    quantization: str | None = _field(None, text=64)
    runtime: Pin | None = None
    way_of_working: Pin | None = None
    lessons: tuple[str, ...] = _field((), pattern="id", items=4096)
    data: DataSpec | None = None
    recipe: Pin | None = None
    tests: tuple[str, ...] = _field((), pattern="id", items=4096)


# ── Records ─────────────────────────────────────────────────────────────────

EpisodeSource = Literal["my_chat", "channel_chat", "workflow", "goal"]
EpisodeStatus = Literal["running", "completed", "failed", "stopped", "cancelled"]


@dataclass(frozen=True)
class Episode(Record):
    """One unit of the person's work: a turn, a workflow run or a goal step.

    ``conversation_id`` is the conversation it belongs to (for a delegated
    Agent's thread, its parent conversation); deleting or excluding that
    conversation purges the episode.

    ``follows_decision_id`` (v2) is a next-message marker: the reply (a model
    call decision) that the person's message starting this episode came after.
    It says only that the message followed the reply; whether it corrected the
    reply is decided later, never at capture.
    """

    KIND: ClassVar[str] = "episode"
    SCHEMA_VERSION: ClassVar[int] = 2
    # v1 had no next-message marker: it reads as None.
    MIGRATIONS: ClassVar[Mapping[int, Callable[[dict[str, Any]], dict[str, Any]]]] = {1: lambda data: data}
    FORM: ClassVar[Literal["jsonl", "manifest"]] = "jsonl"

    id: str = _field(pattern="id")
    thread_id: str = _field(pattern="thread")
    source: EpisodeSource
    started_at: float = _field(minimum=0)
    conversation_id: str = _field("", text=512)
    channel: str | None = _field(None, text=64)
    parent_episode_id: str | None = _field(None, pattern="id")
    root_episode_id: str | None = _field(None, pattern="id")
    agent_run_id: str | None = _field(None, pattern="id")
    kind_of_work: str | None = _field(None, text=128)
    model_ref: str | None = _field(None, text=512)
    status: EpisodeStatus = "running"
    ended_at: float | None = _field(None, minimum=0)
    fidelity: Fidelity = "exact"
    # Read from a past chat (D26), not captured live: always partial fidelity.
    historical: bool = False
    # The capture queue was full and something was dropped (L0b).
    incomplete: bool = False
    follows_decision_id: str | None = _field(None, pattern="id")

    def __post_init__(self) -> None:
        if self.conversation_id == "":
            object.__setattr__(self, "conversation_id", self.thread_id)
        super().__post_init__()

    def _validate(self) -> None:
        if not valid_thread_id(self.conversation_id):
            raise ContractError("invalid_field", "Episode.conversation_id")
        if self.historical and self.fidelity != "partial":
            raise ContractError("invalid_record", "a past chat is partial fidelity")
        if self.ended_at is not None and self.ended_at < self.started_at:
            raise ContractError("invalid_record", "Episode ends before it starts")
        if self.source == "channel_chat" and self.channel is None:
            raise ContractError("invalid_record", "a channel chat names its channel")
        if self.source == "my_chat" and self.channel is not None:
            raise ContractError("invalid_record", "a chat in the app has no channel")


DecisionKind = Literal["model_call", "tool_call", "approval"]
DecisionStatus = Literal["pending", "ok", "error", "denied", "cancelled", "stopped", "timeout"]
ExecutedOutput = Literal["raw", "repaired", "none"]
ApprovalLevel = Literal["ask", "auto", "hands_off"]
DecidedBy = Literal["person", "reviewer", "hands_off", "policy"]
ApprovalOutcome = Literal["approved", "denied", "expired", "cancelled"]


@dataclass(frozen=True)
class Decision(Record):
    """One captured boundary (E3): a model call, a tool call or an approval.

    ``raw_output_blob`` is what the model actually generated. When Row-Bot
    repaired that output (a malformed tool call fixed up, say), the fix is
    ``repaired_output_blob`` and ``executed`` is ``"repaired"``: a repaired call
    is never labelled as model-generated, and :attr:`model_generated_blob`
    only ever returns the raw output.
    """

    KIND: ClassVar[str] = "decision"
    FORM: ClassVar[Literal["jsonl", "manifest"]] = "jsonl"

    id: str = _field(pattern="id")
    episode_id: str = _field(pattern="id")
    thread_id: str = _field(pattern="thread")
    sequence: int = _field(minimum=0)
    kind: DecisionKind
    created_at: float = _field(minimum=0)
    conversation_id: str = _field("", text=512)
    fidelity: Fidelity = "exact"
    # The content came from tool output or the web, not the person (E9).
    untrusted: bool = False
    status: DecisionStatus = "ok"
    model_ref: str | None = _field(None, text=512)
    tool_schema_hash: str | None = _field(None, pattern="sha256")
    request_blob: str | None = _field(None, pattern="sha256", blob=True)
    raw_output_blob: str | None = _field(None, pattern="sha256", blob=True)
    repaired_output_blob: str | None = _field(None, pattern="sha256", blob=True)
    executed: ExecutedOutput = "none"
    tool_name: str | None = _field(None, text=256)
    tool_call_id: str | None = _field(None, text=256)
    arguments_blob: str | None = _field(None, pattern="sha256", blob=True)
    result_blob: str | None = _field(None, pattern="sha256", blob=True)
    result_handle: str | None = _field(None, text=256)
    approval_level: ApprovalLevel | None = None
    decided_by: DecidedBy | None = None
    approval_outcome: ApprovalOutcome | None = None
    retry_of: str | None = _field(None, pattern="id")
    ledger_call_id: str | None = _field(None, text=256)
    incomplete: bool = False

    def __post_init__(self) -> None:
        if self.conversation_id == "":
            object.__setattr__(self, "conversation_id", self.thread_id)
        super().__post_init__()

    @property
    def model_generated_blob(self) -> str | None:
        """What the model itself produced; never the repaired output."""

        return self.raw_output_blob

    def _validate(self) -> None:
        if not valid_thread_id(self.conversation_id):
            raise ContractError("invalid_field", "Decision.conversation_id")
        if (self.executed == "repaired") != (self.repaired_output_blob is not None):
            raise ContractError("invalid_record", "a repaired output is executed as repaired")
        if self.executed == "raw" and self.raw_output_blob is None:
            raise ContractError("invalid_record", "raw output executed but not captured")
        if self.repaired_output_blob is not None and self.repaired_output_blob == self.raw_output_blob:
            raise ContractError("invalid_record", "a repair differs from the raw output")
        if self.kind == "tool_call" and not self.tool_name:
            raise ContractError("invalid_record", "a tool call names its tool")
        if self.kind != "approval" and (
            self.approval_level or self.decided_by or self.approval_outcome
        ):
            raise ContractError("invalid_record", "approval fields belong to approvals")


SignalKind = Literal["teach", "next_message_correction", "approval_denied", "stop", "retry"]
TeachCategory = Literal["wrong_answer", "wrong_tool_or_step", "ignored_instructions", "other"]


@dataclass(frozen=True)
class Signal(Record):
    """The person telling Row-Bot something went wrong.

    ``teach`` is explicit (Teach Row-Bot); the others are implicit: correcting
    it in the next message, denying an approval, stopping a turn, or Retry.
    A signal is evidence, never an immediate change.
    """

    KIND: ClassVar[str] = "signal"
    FORM: ClassVar[Literal["jsonl", "manifest"]] = "jsonl"

    id: str = _field(pattern="id")
    kind: SignalKind
    thread_id: str = _field(pattern="thread")
    created_at: float = _field(minimum=0)
    conversation_id: str = _field("", text=512)
    episode_id: str | None = _field(None, pattern="id")
    decision_id: str | None = _field(None, pattern="id")
    category: TeachCategory | None = None
    note: str | None = _field(None, text=2000)
    # Undo on "Thanks. Row-Bot will learn from this."
    withdrawn_at: float | None = _field(None, minimum=0)

    def __post_init__(self) -> None:
        if self.conversation_id == "":
            object.__setattr__(self, "conversation_id", self.thread_id)
        super().__post_init__()

    @property
    def explicit(self) -> bool:
        return self.kind == "teach"

    def _validate(self) -> None:
        if not valid_thread_id(self.conversation_id):
            raise ContractError("invalid_field", "Signal.conversation_id")
        if self.kind != "teach" and (self.category is not None or self.note is not None):
            raise ContractError("invalid_record", "only Teach carries a category or note")


LessonStatus = Literal["draft", "testing", "ready", "in_use", "off", "not_used", "deleted"]
LessonOrigin = Literal["learning", "agent_saved", "person"]


@dataclass(frozen=True)
class LessonRecord(Record):
    """A lesson's provenance and tests (E5). The lesson itself, once in use, is a
    ``self_knowledge`` memory (``memory_id``); deleting that memory marks this
    record deleted."""

    KIND: ClassVar[str] = "lesson"
    FORM: ClassVar[Literal["jsonl", "manifest"]] = "manifest"

    id: str = _field(pattern="id")
    title: str = _field(text=200)
    text: str = _field(text=2000)
    origin: LessonOrigin
    created_at: float = _field(minimum=0)
    updated_at: float = _field(minimum=0)
    status: LessonStatus = "draft"
    kind_of_work: str | None = _field(None, text=128)
    memory_id: str | None = _field(None, text=128)
    proposal_id: str | None = _field(None, pattern="id")
    evidence: tuple[Ref, ...] = _field((), items=1024)
    test_run_ids: tuple[str, ...] = _field((), pattern="id", items=1024)
    source_thread_ids: tuple[str, ...] = _field((), pattern="thread", items=50_000)
    memory_deleted_at: float | None = _field(None, minimum=0)

    def _validate(self) -> None:
        if self.status == "in_use" and not self.memory_id:
            raise ContractError("invalid_record", "a lesson in use is a memory")
        if self.updated_at < self.created_at:
            raise ContractError("invalid_record", "LessonRecord updated before it was created")


FixtureOrigin = Literal["replay", "pack", "practice"]


@dataclass(frozen=True)
class TaskFixture(Record):
    """One test task (E11): a replayed decision from the person's own work, a
    task-pack task or a practice task. Replay fixtures belong to their chat."""

    KIND: ClassVar[str] = "fixture"
    FORM: ClassVar[Literal["jsonl", "manifest"]] = "manifest"

    id: str = _field(pattern="id")
    origin: FixtureOrigin
    input_blob: str = _field(pattern="sha256", blob=True)
    verifier: str = _field(text=128)
    created_at: float = _field(minimum=0)
    pack: str | None = _field(None, text=64)
    kind_of_work: str | None = _field(None, text=128)
    expected_blob: str | None = _field(None, pattern="sha256", blob=True)
    source_decision_id: str | None = _field(None, pattern="id")
    thread_id: str | None = _field(None, pattern="thread")
    conversation_id: str | None = _field(None, pattern="thread")
    novelty_checked: bool = False

    def __post_init__(self) -> None:
        if self.conversation_id is None and self.thread_id is not None:
            object.__setattr__(self, "conversation_id", self.thread_id)
        super().__post_init__()

    def _validate(self) -> None:
        if self.origin == "replay" and (self.thread_id is None or self.source_decision_id is None):
            raise ContractError("invalid_record", "a replay fixture names its chat and decision")
        if self.origin != "replay" and self.thread_id is not None:
            raise ContractError("invalid_record", "only replay fixtures come from a chat")


TestSubject = Literal["lesson", "release", "attempt", "strategy", "baseline"]
TestSuite = Literal["replay", "pack", "practice", "audit"]
RunStatus = Literal["running", "completed", "failed", "stopped"]


@dataclass(frozen=True)
class TestRun(Record):
    """One test of a change against a fixed baseline: counts with their
    denominator, and what it cost."""

    __test__: ClassVar[bool] = False  # not a pytest class

    KIND: ClassVar[str] = "test_run"
    FORM: ClassVar[Literal["jsonl", "manifest"]] = "jsonl"

    id: str = _field(pattern="id")
    subject_kind: TestSubject
    suite: TestSuite
    started_at: float = _field(minimum=0)
    subject_id: str | None = _field(None, pattern="id")
    baseline_id: str | None = _field(None, pattern="id")
    pack: str | None = _field(None, text=64)
    model_ref: str | None = _field(None, text=512)
    status: RunStatus = "running"
    passed: int = _field(0, minimum=0)
    failed: int = _field(0, minimum=0)
    total: int = _field(0, minimum=0)
    budget_tokens: int | None = _field(None, minimum=0)
    cost: Cost = field(default_factory=Cost)
    comparison_set: str | None = _field(None, pattern="id")
    results_blob: str | None = _field(None, pattern="sha256", blob=True)
    ended_at: float | None = _field(None, minimum=0)
    source_thread_ids: tuple[str, ...] = _field((), pattern="thread", items=50_000)

    def _validate(self) -> None:
        if self.passed + self.failed > self.total:
            raise ContractError("invalid_record", "more results than tasks")
        if self.ended_at is not None and self.ended_at < self.started_at:
            raise ContractError("invalid_record", "TestRun ends before it starts")


ChangeKind = Literal[
    "lesson", "skill", "way_of_working", "training", "code_fix", "practice", "strategy", "wait"
]
AttemptOutcome = Literal["pending", "improved", "not_better", "worse", "unclear", "failed", "stopped"]


@dataclass(frozen=True)
class Attempt(Record):
    """One improvement attempt in the archive (D15), kept or not.

    Every attempt is recorded with its lineage (``parent_ids``), hypothesis,
    evidence, outcome, cost and the reason it was or wasn't kept. Only an
    attempt that improved can be kept.
    """

    KIND: ClassVar[str] = "attempt"
    FORM: ClassVar[Literal["jsonl", "manifest"]] = "jsonl"

    id: str = _field(pattern="id")
    change_kind: ChangeKind
    hypothesis: str = _field(text=4000)
    created_at: float = _field(minimum=0)
    parent_ids: tuple[str, ...] = _field((), pattern="id", items=64)
    strategy_release_id: str | None = _field(None, pattern="id")
    kind_of_work: str | None = _field(None, text=128)
    evidence: tuple[Ref, ...] = _field((), items=1024)
    outcome: AttemptOutcome = "pending"
    kept: bool = False
    reason: str = _field("", text=2000)
    cost: Cost = field(default_factory=Cost)
    test_run_ids: tuple[str, ...] = _field((), pattern="id", items=1024)
    release_id: str | None = _field(None, pattern="id")
    finished_at: float | None = _field(None, minimum=0)
    source_thread_ids: tuple[str, ...] = _field((), pattern="thread", items=50_000)

    def _validate(self) -> None:
        if self.kept and self.outcome != "improved":
            raise ContractError("invalid_record", "only an attempt that improved is kept")
        if self.id in self.parent_ids:
            raise ContractError("invalid_record", "an attempt is not its own parent")
        if len(set(self.parent_ids)) != len(self.parent_ids):
            raise ContractError("invalid_record", "repeated parent")


ReleaseKind = Literal["model", "behaviour", "strategy"]
ReleaseStatus = Literal["candidate", "trial", "in_use", "not_used", "retired"]


@dataclass(frozen=True)
class Release(Record):
    """A versioned thing Learning made: a personal-model version (``model``), a
    way of working (``behaviour``) or a learning method (``strategy``)."""

    KIND: ClassVar[str] = "release"
    # v2 (L2w): the data spec names the rendering its rows had; a v1 release reads as unknown (None).
    SCHEMA_VERSION: ClassVar[int] = 2
    MIGRATIONS: ClassVar[Mapping[int, Callable[[dict[str, Any]], dict[str, Any]]]] = {1: lambda data: data}
    FORM: ClassVar[Literal["jsonl", "manifest"]] = "manifest"

    id: str = _field(pattern="id")
    kind: ReleaseKind
    version: int = _field(minimum=1)
    created_at: float = _field(minimum=0)
    label: str = _field("", text=128)
    parent_ids: tuple[str, ...] = _field((), pattern="id", items=64)
    attempt_id: str | None = _field(None, pattern="id")
    status: ReleaseStatus = "candidate"
    identity: ReleaseIdentity = field(default_factory=ReleaseIdentity)
    # The overlay (behaviour) or strategy spec (strategy); models keep files outside the store.
    content_blob: str | None = _field(None, pattern="sha256", blob=True)
    source_thread_ids: tuple[str, ...] = _field((), pattern="thread", items=50_000)

    def _validate(self) -> None:
        if self.id in self.parent_ids:
            raise ContractError("invalid_record", "a release is not its own parent")
        if len(set(self.parent_ids)) != len(self.parent_ids):
            raise ContractError("invalid_record", "repeated parent")


JobKind = Literal["training", "test", "practice", "diagnosis", "past_chat_import", "meta_trial"]
JobState = Literal[
    "planned",
    "waiting",
    "preparing",
    "running",
    "pausing",
    "paused",
    "evaluating",
    "ready",
    "trial",
    "in_use",
    "not_used",
    "failed",
    "stopped",
    "unknown_remote",
]
JobBackend = Literal["local", "hugging_face", "none"]
# Why a job is pausing or paused (L2a). The first four resume by themselves (in quiet
# hours, or when Row-Bot is free again after Train now); the last three wait for the
# person's Continue or Discard.
JobPauseReason = Literal[
    "in_use",  # the person used Row-Bot: paused at the next step
    "local_gpu",  # a chat needed the same graphics card: stopped at once
    "learning_paused",
    "learning_off",
    "you",  # the person's Stop
    "app_closed",  # Row-Bot closed (or crashed) while it trained
    "worker_stopped",  # the training engine stopped unexpectedly
]
# What started it: quiet hours (D12), or the person's Train now.
JobTrigger = Literal["quiet_hours", "train_now"]
# How its last resume went when the newest saved step couldn't be used.
JobRecovery = Literal["previous_checkpoint", "start_over"]


@dataclass(frozen=True)
class JobTransition(_Part):
    """One timestamped move of a job's state, with its reason code."""

    state: JobState
    at: float = _field(minimum=0)
    reason: str | None = _field(None, text=64)


@dataclass(frozen=True)
class Job(Record):
    """A persisted piece of Learning work and where it stands (L2a).

    v2 adds what training needs to survive a restart and to say where it stands:
    the version it trains, what started it, why it paused, its timings and loss,
    how its last resume went and its timestamped transitions (the newest 200).
    """

    KIND: ClassVar[str] = "job"
    SCHEMA_VERSION: ClassVar[int] = 2
    # v1 had no training fields: they read as unset.
    MIGRATIONS: ClassVar[Mapping[int, Callable[[dict[str, Any]], dict[str, Any]]]] = {1: lambda data: data}
    FORM: ClassVar[Literal["jsonl", "manifest"]] = "manifest"

    id: str = _field(pattern="id")
    kind: JobKind
    created_at: float = _field(minimum=0)
    updated_at: float = _field(minimum=0)
    state: JobState = "planned"
    backend: JobBackend = "local"
    release_id: str | None = _field(None, pattern="id")
    attempt_id: str | None = _field(None, pattern="id")
    spec_blob: str | None = _field(None, pattern="sha256", blob=True)
    steps_done: int = _field(0, minimum=0)
    steps_total: int | None = _field(None, minimum=0)
    checkpoint_step: int | None = _field(None, minimum=0)
    # A remote job's durable request id (never a credential).
    remote_request_id: str | None = _field(None, text=256)
    error_code: str | None = _field(None, text=64)
    cost: Cost = field(default_factory=Cost)
    # v2: training.
    version: int | None = _field(None, minimum=1)
    trigger: JobTrigger = "quiet_hours"
    pause_reason: JobPauseReason | None = None
    started_at: float | None = _field(None, minimum=0)
    finished_at: float | None = _field(None, minimum=0)
    progress_at: float | None = _field(None, minimum=0)
    seconds_per_step: float | None = _field(None, minimum=0)
    loss: float | None = _field(None, minimum=0)
    recovered: JobRecovery | None = None
    transitions: tuple[JobTransition, ...] = _field((), items=200)

    def _validate(self) -> None:
        if self.updated_at < self.created_at:
            raise ContractError("invalid_record", "Job updated before it was created")
        if self.steps_total is not None and self.steps_done > self.steps_total:
            raise ContractError("invalid_record", "more steps done than planned")
        if self.state == "unknown_remote" and self.backend != "hugging_face":
            raise ContractError("invalid_record", "only a remote job can be unknown")
        if self.pause_reason is not None and self.state not in ("pausing", "paused"):
            raise ContractError("invalid_record", "only a pausing or paused job has a pause reason")
        if self.checkpoint_step is not None and self.checkpoint_step > self.steps_done:
            raise ContractError("invalid_record", "a checkpoint ahead of the steps done")


RECORD_TYPES: dict[str, type[Record]] = {
    cls.KIND: cls
    for cls in (Episode, Decision, Signal, LessonRecord, TaskFixture, TestRun, Attempt, Release, Job)
}


# ── Text forms ──────────────────────────────────────────────────────────────


def _reject_constant(name: str) -> Any:
    raise ContractError("invalid_record", f"{name} is not a number")


def _loads(text: str) -> Any:
    if len(text.encode("utf-8")) > MAX_RECORD_BYTES:
        raise ContractError("record_too_large")
    try:
        return json.loads(text, parse_constant=_reject_constant)
    except ContractError:
        raise
    except (ValueError, RecursionError) as error:
        raise ContractError("invalid_json") from error


def record_from_dict(data: Mapping[str, Any]) -> Record:
    """Any record from its dict form, by its ``kind``."""

    if not isinstance(data, Mapping):
        raise ContractError("invalid_record")
    cls = RECORD_TYPES.get(str(data.get("record")))
    if cls is None:
        raise ContractError("unknown_record_kind", str(data.get("record"))[:64])
    return cls.from_dict(data)


def to_json(record: Record) -> str:
    """The compact canonical JSON of a record (also one JSONL line)."""

    text = json.dumps(
        record.to_dict(), sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )
    if len(text.encode("utf-8")) > MAX_RECORD_BYTES:
        raise ContractError("record_too_large", record.KIND)
    return text


def from_json(text: str) -> Record:
    return record_from_dict(_loads(text))


def dumps_jsonl(records: Iterable[Record]) -> str:
    """JSONL: one record per line, newline-terminated."""

    return "".join(to_json(record) + "\n" for record in records)


def loads_jsonl(text: str) -> list[Record]:
    return [from_json(line) for line in text.splitlines() if line.strip()]


def to_manifest(record: Record) -> str:
    """A record as a readable manifest file (sorted keys, two-space indent)."""

    return json.dumps(record.to_dict(), sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def from_manifest(text: str) -> Record:
    return record_from_dict(_loads(text))


def to_text(record: Record) -> str:
    """The record in its own form: a JSONL line or a manifest."""

    return to_manifest(record) if record.FORM == "manifest" else to_json(record) + "\n"


def from_text(text: str) -> Record:
    """A record from either form (both are JSON)."""

    return record_from_dict(_loads(text))


def owner_threads(record: Record) -> tuple[str, ...]:
    """The conversations a record belongs to: deleting or excluding one of them
    purges it. Empty for records that are only *derived* from conversations."""

    if isinstance(record, (Episode, Decision, Signal)):
        return tuple(dict.fromkeys((record.thread_id, record.conversation_id)))
    if isinstance(record, TaskFixture) and record.thread_id is not None:
        return tuple(dict.fromkeys((record.thread_id, record.conversation_id or record.thread_id)))
    return ()


def derived_from_threads(record: Record) -> tuple[str, ...]:
    """The conversations a derived record was built from: deleting one marks it."""

    return tuple(getattr(record, "source_thread_ids", ()) or ())


__all__ = [
    "Attempt",
    "ContractError",
    "Cost",
    "DataSpec",
    "Decision",
    "Episode",
    "Job",
    "JobTransition",
    "LessonRecord",
    "MAX_RECORD_BYTES",
    "Pin",
    "RECORD_TYPES",
    "Record",
    "Ref",
    "Release",
    "ReleaseIdentity",
    "Signal",
    "TaskFixture",
    "TestRun",
    "derived_from_threads",
    "dumps_jsonl",
    "from_json",
    "from_manifest",
    "from_text",
    "loads_jsonl",
    "new_id",
    "owner_threads",
    "record_from_dict",
    "to_json",
    "to_manifest",
    "to_text",
    "valid_thread_id",
]
