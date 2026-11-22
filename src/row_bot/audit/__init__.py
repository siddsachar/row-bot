"""The Row-Bot for Teams audit event contract, shared by connected Row-Bots (C4) and the Company Server's audit
store (S5).

Typed records from a closed vocabulary (``vocabulary``), strict JSON and redaction (``event``), RFC 8785
canonical bytes (``jcs``), the hash chain and its verifier (``chain``) and the JSON Schemas (``schema``).
Standard library only, and nothing else from ``row_bot``, so the server can use it without the app. It emits
nothing and stores nothing.

Only company-governed actions are recorded. ``AuditLayer`` is ``team | company | org``, with no personal layer,
and every record checks itself again before it is written out.
"""

from .chain import ChainLink, ChainReport, chain_hash, genesis_hash, make_link, verify_chain
from .event import (
    Actor, AuditEvent, InvalidAuditEvent, Target, canonical_bytes, format_time, from_dict, new_event_id, redact,
    to_dict,
)
from .jcs import NotCanonicalisable, canonical_json
from .masking import MASK, looks_like_secret, mask_text
from .schema import json_schemas
from .vocabulary import (
    SPECS, Action, ActionSpec, ActorKind, AuditLayer, Category, DecidedBy, Detail, Outcome, RedactionLevel,
    TargetKind,
)

__all__ = [
    "MASK", "SPECS", "Action", "ActionSpec", "Actor", "ActorKind", "AuditEvent", "AuditLayer", "Category",
    "ChainLink", "ChainReport", "DecidedBy", "Detail", "InvalidAuditEvent", "NotCanonicalisable", "Outcome",
    "RedactionLevel", "Target", "TargetKind", "canonical_bytes", "canonical_json", "chain_hash", "format_time",
    "from_dict", "genesis_hash", "json_schemas", "looks_like_secret", "make_link", "mask_text", "new_event_id",
    "redact", "to_dict", "verify_chain",
]
