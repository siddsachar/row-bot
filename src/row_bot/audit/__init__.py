"""The org audit event contract (AUD-1), shared by devices (AUD-2) and the Org server's audit store (ORG-7).

Typed records from a closed vocabulary (``vocabulary``), strict JSON and redaction (``event``), RFC 8785
canonical bytes (``jcs``), the hash chain and its verifier (``chain``), the OCSF mapping (``ocsf``) and the JSON
Schemas (``schema``). Standard library only, and nothing else from ``row_bot``, so the server can use it without
the app. It emits nothing and stores nothing.

A Personal-layer event can't be built: ``AuditLayer`` has no ``personal``, and every record checks itself again
before it is written out.
"""

from .chain import ChainLink, ChainReport, chain_hash, genesis_hash, make_link, verify_chain
from .event import (
    Actor, AuditEvent, InvalidAuditEvent, Target, canonical_bytes, format_time, from_dict, new_event_id, redact,
    to_dict,
)
from .jcs import NotCanonicalisable, canonical_json
from .masking import MASK, looks_like_secret, mask_text
from .ocsf import OCSF_VERSION, to_ocsf
from .schema import json_schemas
from .vocabulary import (
    SPECS, Action, ActionSpec, ActorKind, AuditLayer, Category, DecidedBy, Detail, Outcome, RedactionLevel,
    TargetKind,
)

__all__ = [
    "MASK", "OCSF_VERSION", "SPECS", "Action", "ActionSpec", "Actor", "ActorKind", "AuditEvent", "AuditLayer",
    "Category", "ChainLink", "ChainReport", "DecidedBy", "Detail", "InvalidAuditEvent", "NotCanonicalisable",
    "Outcome", "RedactionLevel", "Target", "TargetKind", "canonical_bytes", "canonical_json", "chain_hash",
    "format_time", "from_dict", "genesis_hash", "json_schemas", "looks_like_secret", "make_link", "mask_text",
    "new_event_id", "redact", "to_dict", "to_ocsf", "verify_chain",
]
