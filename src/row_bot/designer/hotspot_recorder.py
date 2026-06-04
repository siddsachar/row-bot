"""Phase 2.2.J — Hotspot recorder for interactive designer modes.

When the user clicks an element inside the preview iframe while the
project is in an interactive mode (landing / app_mockup / storyboard),
the designer can open a small popover that lets them attach a
`data-row-bot-action` to the clicked element — navigate to another route,
toggle a state key, or play a media asset — without writing HTML by
hand.

This module exposes pure helpers that can be exercised without the UI:

- ``build_hotspot_recorder_spec(project, detail)`` — given the click
  payload posted from the interaction bridge, return a dict describing
  what the popover should offer the user.

- ``record_hotspot(project, *, source_route, element_id, action, target)``
  — apply the recorded hotspot to the project's active HTML.

The UI layer in ``designer.editor`` is expected to render the popover
and call ``record_hotspot`` when the user confirms.
"""

from __future__ import annotations

from typing import Any

from row_bot.designer.state import (
    DesignerInteraction,
    DesignerProject,
)

INTERACTIVE_MODES = {"landing", "app_mockup", "storyboard"}

# Actions the popover offers. Order matters — it drives UI order too.
HOTSPOT_ACTION_CHOICES: list[tuple[str, str]] = [
    ("navigate", "Navigate to screen"),
    ("toggle_state", "Toggle on/off state"),
    ("play_media", "Play media"),
    ("clear", "Clear interaction"),
]


def is_interactive_project(project: DesignerProject) -> bool:
    """Return True when the project is in an interactive mode."""
    return getattr(project, "mode", "deck") in INTERACTIVE_MODES


def _active_route_id(project: DesignerProject) -> str:
    if not project.pages:
        return ""
    idx = max(0, min(project.active_page, len(project.pages) - 1))
    return (getattr(project.pages[idx], "route_id", "") or "").strip()


def _route_options(project: DesignerProject, *, exclude: str = "") -> list[tuple[str, str]]:
    """Return [(route_id, label)] for all pages except ``exclude``."""
    out: list[tuple[str, str]] = []
    excl = (exclude or "").strip().lower()
    for page in project.pages:
        rid = (getattr(page, "route_id", "") or "").strip()
        if not rid:
            continue
        if rid.lower() == excl:
            continue
        title = (getattr(page, "title", "") or rid).strip() or rid
        out.append((rid, title))
    return out


def _resolve_selector(detail: dict[str, Any]) -> str:
    """Prefer the stable ``data-row-bot-element-id`` attribute; fall back to
    an xpath-ish locator.

    The interaction bridge posts a detail payload with keys ``elementId``
    (the UUID we stamp via ``ensure_element_identifier``) and ``xpath``.
    Both may be missing for synthetic events, so the recorder has to
    handle partial data gracefully.
    """
    element_id = (detail or {}).get("elementId") or ""
    element_id = str(element_id).strip()
    if element_id:
        return f'[data-row-bot-element-id="{element_id}"]'
    xpath = str((detail or {}).get("xpath") or "").strip()
    return xpath


def build_hotspot_recorder_spec(
    project: DesignerProject,
    detail: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build a popover spec from a preview element-click payload.

    Returns a dict with:
      - available (bool): False if hotspot recording cannot proceed.
      - reason (str): populated when available is False.
      - source_route (str): route id the user was viewing.
      - selector (str): ready-to-use CSS selector for patching.
      - element_id (str): the stable element identifier, if any.
      - element_tag (str): the clicked element's tag (for the header).
      - element_text (str): short preview of the element text.
      - existing_action (str): existing data-row-bot-action value, if any.
      - action_choices (list[tuple[str,str]]): popover action options.
      - route_choices (list[tuple[str,str]]): navigate target options.
    """

    detail = detail or {}
    if not is_interactive_project(project):
        return {
            "available": False,
            "reason": "Hotspot recorder is only available in interactive modes.",
        }
    if not project.pages:
        return {"available": False, "reason": "Project has no pages."}

    source_route = _active_route_id(project)
    if not source_route:
        return {
            "available": False,
            "reason": "Active page has no route_id.",
        }

    selector = _resolve_selector(detail)
    if not selector:
        return {
            "available": False,
            "reason": "Clicked element is not identifiable.",
        }

    element_id = str(detail.get("elementId") or "").strip()
    element_tag = str(detail.get("tag") or "").strip() or "element"
    element_text = str(detail.get("text") or "").strip()[:60]
    existing_action = str(detail.get("currentAction") or detail.get("action") or "").strip()

    route_choices = _route_options(project, exclude=source_route)

    return {
        "available": True,
        "reason": "",
        "source_route": source_route,
        "selector": selector,
        "element_id": element_id,
        "element_tag": element_tag,
        "element_text": element_text,
        "existing_action": existing_action,
        "action_choices": list(HOTSPOT_ACTION_CHOICES),
        "route_choices": route_choices,
    }


def _patch_html_action(
    html: str,
    selector: str,
    action_value: str | None,
) -> tuple[str, bool]:
    """Set (or clear) data-row-bot-action on the first element matching
    ``selector``.

    ``selector`` may be either a CSS selector or a raw
    data-row-bot-element-id token. When ``action_value`` is ``None`` the
    attribute is removed.
    """

    from bs4 import BeautifulSoup

    from row_bot.designer.html_ops import ensure_element_identifier

    soup = BeautifulSoup(html or "", "html.parser")
    sel = (selector or "").strip()
    if not sel:
        return html, False

    target = None
    try:
        target = soup.select_one(sel)
    except Exception:
        target = None
    if target is None:
        target = soup.find(attrs={"data-row-bot-element-id": sel})
    if target is None:
        return html, False

    ensure_element_identifier(target)
    if action_value is None:
        if "data-row-bot-action" in target.attrs:
            del target.attrs["data-row-bot-action"]
    else:
        target["data-row-bot-action"] = action_value
    return str(soup), True


def record_hotspot(
    project: DesignerProject,
    *,
    source_route: str,
    selector: str,
    action: str,
    target: str = "",
    event: str = "click",
    transition: str = "fade",
) -> tuple[bool, str]:
    """Apply a hotspot recording to the project.

    Returns ``(ok, message)``. The caller is responsible for invoking
    ``save_project`` / ``prepare_project_mutation`` since those belong
    to the UI layer; this helper only mutates the in-memory project.
    """

    if not is_interactive_project(project):
        return False, "Project is not in an interactive mode."

    action = (action or "").strip().lower()
    allowed = {"navigate", "toggle_state", "play_media", "clear"}
    if action not in allowed:
        return False, f"Unknown action '{action}'. Allowed: {sorted(allowed)}."

    src_idx = -1
    src_key = (source_route or "").strip().lower()
    for idx, page in enumerate(project.pages):
        if (getattr(page, "route_id", "") or "").strip().lower() == src_key:
            src_idx = idx
            break
    if src_idx < 0:
        return False, f"Source route '{source_route}' not found."

    if action == "navigate":
        tgt = (target or "").strip()
        if not tgt:
            return False, "Navigate action requires a target route."
        found = any(
            (getattr(p, "route_id", "") or "").strip().lower() == tgt.lower()
            for p in project.pages
        )
        if not found:
            return False, f"Navigate target '{tgt}' is not a known route."
        action_value: str | None = f"navigate:{tgt}"
    elif action == "clear":
        action_value = None
    else:
        # toggle_state / play_media accept a free-form token as target.
        tgt = (target or "").strip()
        action_value = f"{action}:{tgt}" if tgt else action

    new_html, ok = _patch_html_action(
        project.pages[src_idx].html, selector, action_value
    )
    if not ok:
        return False, f"Selector '{selector}' matched no element on '{source_route}'."

    project.pages[src_idx].html = new_html
    project.pages[src_idx].thumbnail_b64 = None

    if action_value is not None:
        project.interactions.append(
            DesignerInteraction(
                source_route=source_route,
                selector=selector,
                event=event or "click",
                action=action if action != "clear" else "navigate",
                target=(target or "").strip(),
                transition=transition or "fade",
            )
        )

    if action == "clear":
        return True, f"Cleared interaction on '{selector}' within '{source_route}'."
    if action == "navigate":
        return (
            True,
            f"Linked '{selector}' on '{source_route}' → navigate to '{target}'.",
        )
    return True, f"Set {action}:{target} on '{selector}' within '{source_route}'."
