"""The Browser pane's API: state, the live picture, the person's input and controls, signed-in sites.

The pane is the person's own view of Row-Bot's browser for one conversation:
every signed-in device of the owner may watch it and use it. Reads are
passive (they never start the browser); the controls start it when needed.
Errors are stable client-safe codes.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

from row_bot.application.client_platform import ClientPlatformError

logger = logging.getLogger(__name__)


class ClientBrowserError(ClientPlatformError):
    """A stable, client-safe Browser pane error."""


def _manager() -> Any:
    from row_bot.tools.browser_tool import get_session_manager

    return get_session_manager()


def _engine(*, start: bool) -> Any:
    manager = _manager()
    if not start and not manager.has_active_session():
        return None
    return manager.get_session()


def _enabled() -> None:
    from row_bot.tools import registry

    if not registry.is_enabled("browser"):
        raise ClientBrowserError("browser_disabled")


_IDLE = {
    "status": "stopped", "acting": "", "waiting": "", "url": "", "title": "", "site": "",
    "loading": False, "sign_in": "", "recording": False, "tabs": [], "revision": 0,
}


def pane_state(conversation_id: str) -> dict[str, Any]:
    """The pane's state; never starts the browser."""

    engine = _engine(start=False)
    state = dict(engine.pane_state(conversation_id)) if engine is not None else dict(_IDLE)
    return {"schema_version": 1, "conversation_id": conversation_id, **state}


def frame_view(frame: Any) -> dict[str, Any]:
    return {"seq": frame.seq, "css_width": frame.css_width, "css_height": frame.css_height,
            "image_base64": frame.data}


def _navigable(conversation_id: str, url: str, engine: Any) -> str:
    """The address the person typed, made openable; refuses what never opens."""

    from row_bot.browser import local_access
    from row_bot.browser.addresses import has_scheme
    from row_bot.browser.policy import navigation_policy

    selected = url.strip()
    if not selected.casefold().startswith(("http://", "https://")) and not has_scheme(selected):
        bare = "http://" + selected
        selected = bare if local_access.local_development_origin(bare) else "https://" + selected
    parsed = urlparse(selected)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ClientBrowserError("invalid_browser_url")
    decision, _reason = navigation_policy(selected)
    if decision == "block":
        raise ClientBrowserError("browser_navigation_denied")
    origin = local_access.local_development_origin(selected)
    if origin is not None:
        if local_access.is_row_bot_origin(origin):
            raise ClientBrowserError("browser_navigation_denied")
        if engine.local_address_grant(conversation_id, origin) is None:
            # The person opened it themselves: allowed in this chat, as if they'd approved it.
            engine.allow_local_address(conversation_id, origin, source="you")
    elif local_access.private_network_target(selected) or local_access.unspecified_refusal(selected):
        raise ClientBrowserError("browser_navigation_denied")
    return selected


def pane_action(conversation_id: str, action: str, *, url: str = "", tab: str = "") -> dict[str, Any]:
    """Run one of the pane's controls, then return the pane's state."""

    _enabled()
    engine = _engine(start=True)
    try:
        if action == "navigate":
            target = _navigable(conversation_id, url, engine)
            from row_bot.browser.history import append_browser_history
            from datetime import datetime, timezone

            append_browser_history(conversation_id, {"action": "navigate", "url": target, "by": "person",
                                                     "timestamp": datetime.now(timezone.utc).isoformat()})
            engine.person_action(conversation_id, "navigate", url=target)
        elif action in {"back", "forward", "reload", "stop_loading", "new_tab", "switch_tab", "close_tab", "restart"}:
            engine.person_action(conversation_id, action, tab=tab)
        else:
            raise ClientBrowserError("invalid_browser_command")
    except ClientBrowserError:
        raise
    except Exception:
        logger.info("A Browser pane control failed", exc_info=True)
        raise ClientBrowserError("browser_unavailable") from None
    return pane_state(conversation_id)


def pane_input(conversation_id: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    """Play the person's mouse and keys on the conversation's page."""

    _enabled()
    engine = _engine(start=False)
    if engine is None:
        return {"status": "no_page", "copied": ""}
    try:
        return engine.person_input(conversation_id, events)
    except Exception:
        logger.info("Browser pane input failed", exc_info=True)
        raise ClientBrowserError("browser_unavailable") from None


def sites() -> dict[str, Any]:
    """Sites with saved sign-ins; reads the stopped profile without starting the browser."""

    manager = _manager()
    engine = manager.engine() if manager.has_active_session() else None
    if engine is None:
        from row_bot.browser.service import BrowserEngine

        engine = BrowserEngine()  # Not started: reads the profile's cookie store only.
    try:
        return {"items": engine.sites()[:2000]}
    except Exception:
        logger.info("Signed-in sites couldn't be read", exc_info=True)
        raise ClientBrowserError("browser_unavailable") from None


def clear_sites(site: str | None) -> dict[str, Any]:
    """Sign out of one site (or every site): its cookies and stored data go."""

    from row_bot.browser.service import registrable_domain

    selected = registrable_domain(site) if site else None
    if site is not None and not selected:
        raise ClientBrowserError("invalid_browser_command")
    engine = _engine(start=True)
    try:
        cleared = engine.clear_sites(selected)
    except Exception:
        logger.info("Signing out of sites failed", exc_info=True)
        raise ClientBrowserError("browser_unavailable") from None
    return {"cleared": cleared, "items": engine.sites()[:2000]}
