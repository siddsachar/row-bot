"""Organisation policy (EN-P1): one versioned policy IT pushes with the tools it already uses, read from the
OS-managed places, verified, and failing closed.

``effective_policy()`` is ``NoPolicy`` (no organisation policy: nothing changes), ``OrgPolicy`` or
``Lockdown(reason)``, each with ``source`` and ``signer`` and the ``allows_*`` / ``limit_*`` checks. It is read
once per process; ``reload_policy()`` reads it again. With no policy present it costs one stat per source and
imports nothing heavy. Sources, precedence and signatures: ``sources``; settings, defaults and lockdown
values: ``schema``; the published schema and admin templates: ``contracts/policy/v1/``.

Nothing in the app calls this yet: enforcement at the seams is POL-2.
"""

from __future__ import annotations

import threading

from row_bot.policy.model import IgnoredSource, Lockdown, NoPolicy, OrgPolicy, Policy, PolicySource
from row_bot.policy.schema import DEFAULTS, LOCKDOWN, Settings

__all__ = [
    "DEFAULTS", "LOCKDOWN", "IgnoredSource", "Lockdown", "NoPolicy", "OrgPolicy", "Policy", "PolicySource",
    "Settings", "effective_policy", "reload_policy",
]

_lock = threading.Lock()
_cached: Policy | None = None


def effective_policy() -> Policy:
    """This computer's organisation policy, read on first use and kept for the life of the process."""
    global _cached
    with _lock:
        if _cached is None:
            from row_bot.policy.sources import load_policy, os_system

            _cached = load_policy(os_system())
        return _cached


def reload_policy() -> Policy:
    """Forget the policy read so far and read it again."""
    global _cached
    with _lock:
        _cached = None
    return effective_policy()
