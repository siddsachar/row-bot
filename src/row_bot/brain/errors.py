"""The errors every brain implementation raises, and nothing else for these cases."""

from __future__ import annotations

# One fixed message for a ref or space the caller may not use: a missing item and an item the caller may
# not read must be indistinguishable, so nothing about its existence leaks.
DENIED = "not found, or not allowed"


class BrainError(Exception):
    """Base class of the brain's errors."""


class AccessDenied(BrainError):
    """The principal may not do this, or the item or space does not exist; the two look the same."""

    def __init__(self) -> None:
        super().__init__(DENIED)


class InvalidRecord(BrainError, ValueError):
    """A record, query or argument broke the contract: a wrong type, size, time or layer rule."""


class Conflict(BrainError):
    """The change no longer applies, for example superseding an item that is already superseded."""
