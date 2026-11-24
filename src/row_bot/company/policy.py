"""What the company's policy changes in this app (Row-Bot for Teams; C3 fills it in)."""

from __future__ import annotations


def hides_personal_providers() -> bool:
    """Whether the company hides this Row-Bot's own providers (personal keys and consumer sign-ins).

    Asked where the model lists are built (``provider.add_rows``). C3 answers it from the company's policy;
    until then it is never so."""
    return False
