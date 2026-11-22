"""Company policy: the settings a Company Server sets for its people's Row-Bots, shared by the server (which
stores and merges them) and the app (which enforces them).

A company policy plus optional overrides per IdP group; ``resolve`` merges them additively into one person's
effective ``Policy``. Settings, defaults, merge rules and lockdown: ``model``; the published JSON Schemas:
``schema`` and ``contracts/policy/v1/``.

Standard library only, and nothing else from ``row_bot``, so the Company Server can use it without the app.
"""

from .model import (
    ALL_MODELS, APP_MODES, APPROVAL_LEVELS, DEFAULT_POLICY, MAX_DOCUMENT_BYTES, Apps, Approvals, Data, Document,
    Models, Offline, Policy, PolicyError, Tools, canonical_json, document_to_dict, loads, lockdown_policy,
    parse_override, parse_policy, policy_to_dict, resolve,
)
from .schema import json_schema, settings_table

__all__ = [
    "ALL_MODELS", "APPROVAL_LEVELS", "APP_MODES", "DEFAULT_POLICY", "MAX_DOCUMENT_BYTES", "Apps", "Approvals",
    "Data", "Document", "Models", "Offline", "Policy", "PolicyError", "Tools", "canonical_json",
    "document_to_dict", "json_schema", "loads", "lockdown_policy", "parse_override", "parse_policy",
    "policy_to_dict", "resolve", "settings_table",
]
