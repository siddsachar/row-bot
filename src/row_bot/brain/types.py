"""The brain's records: layers, spaces, principals, items with provenance, validity and lineage, and the
recall and change-feed shapes. Every record checks itself when it is built (``serialization.check_fields``
plus the layer rules below), so no implementation can hold an invalid one.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Any, ClassVar

from .errors import InvalidRecord
from .serialization import Bounds, check_fields

_ID = r"[A-Za-z0-9][A-Za-z0-9._:@|+-]*"
# Opaque identifiers: item ids, space ids, principal ids, group names and connector ids.
Id = Annotated[str, Bounds(min_length=1, max_length=128, pattern=_ID)]
# An item's id: empty until the brain records it, then minted by the brain.
ItemId = Annotated[str, Bounds(max_length=128, pattern=f"(?:{_ID})?")]
Name = Annotated[str, Bounds(min_length=1, max_length=64, pattern=r"[a-z][a-z0-9_]*")]
Label = Annotated[str, Bounds(min_length=1, max_length=160)]
Groups = Annotated[frozenset[Id], Bounds(max_items=1024)]
Audience = Annotated[frozenset[Id], Bounds(max_items=64)]
Confidence = Annotated[float, Bounds(minimum=0.0, maximum=1.0)]
JsonObject = Annotated[Mapping[str, Any], Bounds(max_bytes=16_384, max_depth=8)]


class Layer(StrEnum):
    """Where an item lives: the person's own layers, or a team's or the company's shared one."""

    PERSONAL = "personal"
    WORK = "work"
    TEAM = "team"
    COMPANY = "company"


PERSON_LAYERS = frozenset({Layer.PERSONAL, Layer.WORK})
SHARED_LAYERS = frozenset({Layer.TEAM, Layer.COMPANY})


class Owner(StrEnum):
    """Who owns an item, derived from its layer: the person (personal) or the organisation (the rest)."""

    PERSON = "person"
    ORGANISATION = "organisation"


class ItemKind(StrEnum):
    """The kinds of item a brain holds."""

    FACT = "fact"
    RELATION = "relation"
    SKILL = "skill"
    SOURCE = "source"


class SourceKind(StrEnum):
    """Where an item came from."""

    CONVERSATION = "conversation"
    DOCUMENT = "document"
    CONNECTOR = "connector"
    TOOL = "tool"
    STATEMENT = "statement"
    PROMOTION = "promotion"
    IMPORT = "import"


class ChangeOp(StrEnum):
    """A change-feed operation: an item was written or changed, or it was retracted (forgotten)."""

    UPSERT = "upsert"
    RETRACT = "retract"


@dataclass(frozen=True)
class Space:
    """A brain space: a person's personal or work space (``space_id`` is the person's id), or a team's or the
    company's (``space_id`` is the team or company id)."""

    layer: Layer
    space_id: Id

    def __post_init__(self) -> None:
        check_fields(self)

    @property
    def owner(self) -> Owner:
        return Owner.PERSON if self.layer is Layer.PERSONAL else Owner.ORGANISATION

    @property
    def shared(self) -> bool:
        return self.layer in SHARED_LAYERS


@dataclass(frozen=True)
class ItemRef:
    """A reference to an item: its kind and its opaque id."""

    kind: ItemKind
    id: Id

    def __post_init__(self) -> None:
        check_fields(self)


@dataclass(frozen=True)
class Principal:
    """Who is asking: an authenticated person (or service) and the groups their identity provider gives."""

    id: Id
    groups: Groups = frozenset()

    def __post_init__(self) -> None:
        check_fields(self)


@dataclass(frozen=True)
class SpaceAccess:
    """A team or company space's access list: groups that read it, groups that may publish to it directly,
    and its display title for recall labels."""

    space: Space
    title: Label
    readers: Groups
    publishers: Groups = frozenset()

    def __post_init__(self) -> None:
        check_fields(self)
        if not self.space.shared:
            raise InvalidRecord("SpaceAccess: only team and company spaces have access lists")


@dataclass(frozen=True)
class Provenance:
    """Where an item came from: the citation recall shows."""

    source_kind: SourceKind
    source_ref: Annotated[str, Bounds(min_length=1, max_length=512)]
    captured_at: datetime
    actor: Id
    system: Id | None = None
    confidence: Confidence = 1.0

    def __post_init__(self) -> None:
        check_fields(self)


@dataclass(frozen=True)
class Validity:
    """Bi-temporal validity. ``valid_from``/``valid_to`` say when the item is true in the world; the brain sets
    ``recorded_at`` when it records the item and ``superseded_by``/``superseded_at`` when a newer one replaces
    it."""

    valid_from: datetime
    valid_to: datetime | None = None
    recorded_at: datetime | None = None
    superseded_by: ItemRef | None = None
    superseded_at: datetime | None = None

    def __post_init__(self) -> None:
        check_fields(self)
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise InvalidRecord("Validity: valid_to must be after valid_from")
        if (self.superseded_by is None) != (self.superseded_at is None):
            raise InvalidRecord("Validity: superseded_by and superseded_at go together")
        if self.superseded_at is not None and (self.recorded_at is None or self.superseded_at < self.recorded_at):
            raise InvalidRecord("Validity: an item is superseded only after it is recorded")


@dataclass(frozen=True)
class Lineage:
    """The items this one was derived from; forgetting any of them retracts this one too."""

    derived_from: Annotated[tuple[ItemRef, ...], Bounds(max_items=64)] = ()

    def __post_init__(self) -> None:
        check_fields(self)
        if len(set(self.derived_from)) != len(self.derived_from):
            raise InvalidRecord("Lineage: derived_from repeats an item")


@dataclass(frozen=True, kw_only=True)
class _Item:
    """Fields every item has. ``audience`` narrows reading within a team or company space to these groups;
    ``portable`` marks personal know-how with no company data, which leaves with the person."""

    KIND: ClassVar[ItemKind]

    space: Space
    provenance: Provenance
    validity: Validity
    id: ItemId = ""
    lineage: Lineage = Lineage()
    audience: Audience = frozenset()
    portable: bool = False

    def __post_init__(self) -> None:
        check_fields(self)
        name = type(self).__name__
        if (self.id == "") != (self.validity.recorded_at is None):
            raise InvalidRecord(f"{name}: an item has an id once the brain has recorded it, and only then")
        if self.portable and self.space.layer is not Layer.PERSONAL:
            raise InvalidRecord(f"{name}: only personal items can be portable")
        if self.audience and not self.space.shared:
            raise InvalidRecord(f"{name}: only team and company items have an audience")
        self._check()

    def _check(self) -> None:
        """Rules of one kind of item."""

    @property
    def ref(self) -> ItemRef:
        if not self.id:
            raise InvalidRecord(f"{type(self).__name__}: not recorded yet, so it has no ref")
        return ItemRef(self.KIND, self.id)


@dataclass(frozen=True, kw_only=True)
class Fact(_Item):
    """A fact about a subject (the knowledge graph's entity). ``owner_principal`` names who answers for a team
    or company fact, and ``verified_at`` when they last confirmed it."""

    KIND: ClassVar[ItemKind] = ItemKind.FACT

    subject: Annotated[str, Bounds(min_length=1, max_length=200)]
    entity_type: Name
    text: Annotated[str, Bounds(max_length=4_000)] = ""
    properties: JsonObject = field(default_factory=lambda: MappingProxyType({}))
    owner_principal: Id | None = None
    verified_at: datetime | None = None

    def _check(self) -> None:
        if self.owner_principal is not None and not self.space.shared:
            raise InvalidRecord("Fact: only team and company facts have an owner_principal")


@dataclass(frozen=True, kw_only=True)
class Relation(_Item):
    """A typed edge between two facts of the same space."""

    KIND: ClassVar[ItemKind] = ItemKind.RELATION

    source: ItemRef
    relation: Name
    target: ItemRef

    def _check(self) -> None:
        if self.source.kind is not ItemKind.FACT or self.target.kind is not ItemKind.FACT:
            raise InvalidRecord("Relation: source and target must be facts")


@dataclass(frozen=True, kw_only=True)
class Skill(_Item):
    """A procedure: a SKILL.md body, versioned. A new version supersedes the old one. ``signature`` and
    ``signature_key_id`` are placeholders for the signed publication of a reviewed skill."""

    KIND: ClassVar[ItemKind] = ItemKind.SKILL

    name: Annotated[str, Bounds(min_length=1, max_length=64, pattern=r"[a-z0-9][a-z0-9_-]*")]
    version: Annotated[str, Bounds(min_length=1, max_length=32, pattern=r"[0-9A-Za-z][0-9A-Za-z.+-]*")]
    description: Annotated[str, Bounds(min_length=1, max_length=500)]
    body: Annotated[str, Bounds(min_length=1, max_length=65_536)]
    reviewed_by: Id | None = None
    signature: Annotated[str, Bounds(min_length=1, max_length=1_024, pattern=r"[A-Za-z0-9_=-]+")] | None = None
    signature_key_id: Id | None = None

    def _check(self) -> None:
        if (self.signature is None) != (self.signature_key_id is None):
            raise InvalidRecord("Skill: signature and signature_key_id go together")


@dataclass(frozen=True, kw_only=True)
class SourceDoc(_Item):
    """An indexed document or connector item, by reference and content hash. In a team or company space its
    upstream ACL groups are the item's ``audience``."""

    KIND: ClassVar[ItemKind] = ItemKind.SOURCE

    uri: Annotated[str, Bounds(min_length=1, max_length=2_048)]
    title: Annotated[str, Bounds(max_length=300)]
    content_hash: Annotated[str, Bounds(min_length=71, max_length=71, pattern=r"sha256:[0-9a-f]{64}")]
    fetched_at: datetime


Item = Fact | Relation | Skill | SourceDoc
ITEM_TYPES: Mapping[ItemKind, type[Item]] = MappingProxyType(
    {cls.KIND: cls for cls in (Fact, Relation, Skill, SourceDoc)})


@dataclass(frozen=True)
class RecallQuery:
    """A recall: words to look for, who asks, optional space and kind filters, and time travel. ``as_of``
    shows what the brain held and was true then; ``include_superseded`` adds history (superseded or no
    longer valid)."""

    text: Annotated[str, Bounds(min_length=1, max_length=1_000)]
    principal: Principal
    spaces: Annotated[frozenset[Space], Bounds(max_items=64)] | None = None
    kinds: Annotated[frozenset[ItemKind], Bounds(max_items=4)] | None = None
    limit: Annotated[int, Bounds(minimum=1, maximum=100)] = 10
    as_of: datetime | None = None
    include_superseded: bool = False

    def __post_init__(self) -> None:
        check_fields(self)


@dataclass(frozen=True)
class RecallHit:
    """One recalled item with its score in [0, 1], layer, owner and a short label such as "From your notes".
    The item's provenance is the citation."""

    item: Item
    score: Annotated[float, Bounds(minimum=0.0, maximum=1.0)]
    layer: Layer
    owner: Owner
    label: Label
    owner_principal: Id | None = None
    verified_at: datetime | None = None

    def __post_init__(self) -> None:
        check_fields(self)
        if not self.item.id:
            raise InvalidRecord("RecallHit: the item must be recorded")

    @property
    def ref(self) -> ItemRef:
        return self.item.ref

    @property
    def provenance(self) -> Provenance:
        return self.item.provenance


@dataclass(frozen=True)
class ChangeEvent:
    """One entry of a space's change feed. ``seq`` increases within the space (with gaps where forgotten
    items' events were removed); an upsert carries the item as it then was, a retract carries none."""

    seq: Annotated[int, Bounds(minimum=1)]
    op: ChangeOp
    ref: ItemRef
    at: datetime
    item: Item | None = None

    def __post_init__(self) -> None:
        check_fields(self)
        if self.op is ChangeOp.UPSERT and (self.item is None or self.item.ref != self.ref):
            raise InvalidRecord("ChangeEvent: an upsert carries its item")
        if self.op is ChangeOp.RETRACT and self.item is not None:
            raise InvalidRecord("ChangeEvent: a retract carries no item")


@dataclass(frozen=True)
class ChangePage:
    """A page of a space's change feed. Pass ``next_seq`` as the next ``after_seq``; ``has_more`` says whether
    events follow it."""

    space: Space
    events: Annotated[tuple[ChangeEvent, ...], Bounds(max_items=1_000)]
    next_seq: Annotated[int, Bounds(minimum=0)]
    has_more: bool

    def __post_init__(self) -> None:
        check_fields(self)


# The records published as JSON Schema under contracts/brain/v1/.
SCHEMA_TYPES: tuple[type, ...] = (
    Space, ItemRef, Principal, SpaceAccess, Provenance, Validity, Lineage,
    Fact, Relation, Skill, SourceDoc, RecallQuery, RecallHit, ChangeEvent, ChangePage,
)
