"""A chat's "Save as a skill" offers and drafts (C1), for the client.

The offers themselves are decided in ``row_bot.skill_offers``; here a readable conversation is
required, and an engine refusal becomes the client's error code. Approving, editing or rejecting
a draft goes through Settings › Skills' reviewed commands (``skill.proposal.*``).
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from row_bot.application.client_platform import ClientPlatformError


def _readable(service: Any, conversation_id: str) -> None:
    from row_bot.application.conversation_search import _require_readable

    _require_readable(service, conversation_id)


def read_skill_offers(service: Any, conversation_id: str) -> dict[str, Any]:
    from row_bot import skill_offers

    _readable(service, conversation_id)
    return skill_offers.conversation_offers(conversation_id)


def dismiss_skill_offer(service: Any, conversation_id: str, offer_id: str,
                        validate: Callable[[], None] | None = None) -> dict[str, Any]:
    from row_bot import skill_offers

    if validate:
        validate()
    _readable(service, conversation_id)
    try:
        return skill_offers.dismiss(conversation_id, offer_id)
    except skill_offers.SkillOfferError as error:
        raise ClientPlatformError(error.code) from None


def draft_skill(service: Any, conversation_id: str, body: dict[str, Any],
                validate: Callable[[], None] | None = None) -> dict[str, Any]:
    """Draft a skill from one reply's turn: an offer taken up, or the manual "Save as skill"."""
    from row_bot import skill_offers

    if validate:
        validate()
    _readable(service, conversation_id)
    try:
        return skill_offers.draft(conversation_id, str(body.get("message_id") or ""),
                                  offer_id=str(body.get("offer_id") or "") or None)
    except skill_offers.SkillOfferError as error:
        raise ClientPlatformError(error.code) from None


def draft_demo_skill(service: Any, conversation_id: str, demo_id: str, body: dict[str, Any],
                     validate: Callable[[], None] | None = None) -> dict[str, Any]:
    """Draft a skill from the steps the person showed in the Browser pane (C3)."""
    from row_bot import skill_offers

    if validate:
        validate()
    _readable(service, conversation_id)
    try:
        page = skill_offers.draft_from_demo(conversation_id, demo_id, str(body.get("purpose") or ""))
    except skill_offers.SkillOfferError as error:
        raise ClientPlatformError(error.code) from None
    # Drafted, the steps are forgotten: the Browser pane's line says so at once.
    from row_bot.application import client_browser

    client_browser._changed(client_browser._engine(start=False), conversation_id)  # noqa: SLF001
    return page


def try_skill(service: Any, name: str, validate: Callable[[], None] | None = None) -> dict[str, Any]:
    """Try it: a new chat with the saved skill pinned and its request ready in the composer."""
    from row_bot import skill_offers

    del service
    if validate:
        validate()
    try:
        return skill_offers.try_skill(name)
    except skill_offers.SkillOfferError as error:
        raise ClientPlatformError(error.code) from None
