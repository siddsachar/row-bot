"""Pages (N5): ``page_create``, ``page_update`` and ``page_read``.

A page is an interactive plan, checklist, dashboard, comparison, form or small tool the agent makes for the
person, shown beside the chat (``row_bot.pages``). It runs sandboxed with no network and no tools; the
person's decisions on it reach the agent only when they click Send to Row-Bot. Making or changing a page
writes only to Row-Bot's own data for this chat, so it never asks.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from row_bot.tools import registry
from row_bot.tools.base import BaseTool, NullMeansUnset

Library = Literal["charts", "rowbot-style"]
_REMOTE = re.compile(r"<(?:script|link|img|iframe)\b[^>]*\b(?:src|href)\s*=\s*['\"]?(?:https?:)?//", re.IGNORECASE)


def _conversation() -> str:
    from row_bot.tool_outputs import current_thread_id

    return str(current_thread_id() or "")


def _remote_note(html: str | None) -> str:
    if html and _REMOTE.search(html):
        return ("\nNote: the page has no network, so anything it loads from the internet (scripts, styles, "
                "images) won't load. Inline it, or use libraries=[\"charts\"] for charts.")
    return ""


def _script_note(html: str | None) -> str:
    """A script that won't run leaves the page's sections empty (B423): say where, so it's fixed first."""
    from row_bot.page_scripts import problems

    found = problems(html or "")
    if not found:
        return ""
    return ("\nWarning: the page has a script that won't run, so parts of it will be empty: " + "; ".join(found[:3])
            + ". Fix it with page_update before you say the page is ready.")


def _page_create(title: str, html: str, data: Any = None, libraries: list[str] | None = None) -> str:
    from row_bot import pages

    conversation = _conversation()
    if not conversation:
        return "Making a page needs a conversation."
    try:
        page = pages.create(conversation, title, html, data, libraries)
    except pages.PageError as exc:
        return f"The page wasn't made: {exc}"
    return pages.summary(page, made=True) + _remote_note(html) + _script_note(html)


def _page_update(page: str, html: str | None = None, data: Any = None, title: str | None = None,
                 libraries: list[str] | None = None) -> str:
    from row_bot import pages

    conversation = _conversation()
    found = pages.find(conversation, page) if conversation else None
    if found is None:
        return f"There's no page {page!r} in this chat. Use its id (page-…) from page_create."
    # An empty title or HTML is left as it is, like one left out.
    title, html = title or None, html or None
    try:
        updated = pages.update(found, title=title, html=html, data=data, libraries=libraries,
                               data_given=data is not None)
    except pages.PageError as exc:
        return f"The page wasn't changed: {exc}"
    return pages.summary(updated, made=False) + _remote_note(html) + _script_note(html)


def _page_read(page: str, version: int | None = None, include_html: bool = False) -> str:
    from row_bot import pages

    version = version or None  # 0, like one left out, is the current version
    conversation = _conversation()
    found = pages.find(conversation, page) if conversation else None
    if found is None:
        return f"There's no page {page!r} in this chat. Use its id (page-…) from page_create."
    try:
        result: dict[str, Any] = {
            "page": found.page_id, "title": found.title, "version": found.version,
            "libraries": found.libraries, "state": found.state,
            "decisions_not_sent": [{"id": key, "choice": value["choice"], "note": value.get("note", ""),
                                    "label": value.get("label", "")} for key, value in found.decisions.items()],
            "last_sent": found.sent[-1]["decisions"] if found.sent else [],
            "versions": [{"version": item["version"], "what": item.get("what", ""), "title": item["title"]}
                         for item in found.versions],
        }
        if version is not None or include_html:
            result["html"] = pages.read_html(found, version)
            result["data"] = pages.read_data(found, version)
            if version is not None:
                result["shown_version"] = int(version)
    except pages.PageError as exc:
        return f"The page couldn't be read: {exc}"
    return json.dumps(result, ensure_ascii=False, indent=1)


def _board_show(name: str = "") -> str:
    """Show one of the person's pinned interfaces in this chat, from the Home Board, by its name."""
    from row_bot.application.boards import find_pin

    found, titles = find_pin(name)
    if not titles:
        return "Nothing is pinned to Home yet. Draw the interface, and the person can pin it."
    if found is None:
        listed = "; ".join(title for title in titles if title)
        return f"No pinned interface is called that. Pinned: {listed}."
    # The handle line: the chat shows the pinned interface itself, live, with its data filled in.
    return (f"Pinned interface ({found['pin_id']}): {found.get('title') or 'Interface'}\n"
            "It is shown in the chat as it is on Home, with the person's data filled in on their computer. "
            "Don't draw it again.")


class _BoardShowInput(BaseModel):
    name: str = Field(default="", description="Its name, in the person's words; empty lists what is pinned.")


class _CreateInput(BaseModel):
    title: str
    html: str = Field(description="One document, CSS and JS inline, no network; window.rowbot for data and state.")
    data: Any = Field(default=None, description="JSON for rowbot.data().")
    libraries: list[Library] = Field(default_factory=list, description=(
        "charts: Chart.js 4 (window.Chart). rowbot-style: Row-Bot's look (rb- classes)."))


class _UpdateInput(NullMeansUnset):
    # Each field given replaces the whole old value; one left out (or empty) stays.
    page: str = Field(description="Its id (page-…) or exact title.")
    html: str = ""
    data: Any = None
    title: str = ""
    libraries: list[Library] | None = None


class _ReadInput(NullMeansUnset):
    page: str = Field(description="Its id (page-…) or exact title.")
    version: int = Field(default=0, description="An earlier one (the last 10 are kept).")
    include_html: bool = Field(default=False, description="Also the HTML and data.")


class PagesTool(BaseTool):

    @property
    def name(self) -> str:
        return "pages"

    @property
    def display_name(self) -> str:
        return "🗂️ Pages"

    @property
    def description(self) -> str:
        return ("Make interactive pages beside the chat: plans with approvals, checklists, dashboards with charts, "
                "comparisons, forms and small tools. Pages have no network; the person's decisions come back "
                "when they click Send to Row-Bot.")

    @property
    def enabled_by_default(self) -> bool:
        return True

    @property
    def untrusted_tool_names(self) -> set[str]:
        # A page's state is written by the page's own code, from what it shows.
        return {"page_read"}

    def as_langchain_tools(self) -> list:
        return [
            StructuredTool.from_function(
                func=_page_create,
                name="page_create",
                description=(
                    "Make an interactive page beside the chat: a plan with Approve / Change / Skip, a checklist, "
                    "a dashboard, a comparison, a form or a small tool; unasked, whenever your answer is one. "
                    "skill_load('making_pages') first."
                ),
                args_schema=_CreateInput,
            ),
            StructuredTool.from_function(
                func=_page_update,
                name="page_update",
                description=(
                    "Change a page (new data, HTML, title or libraries); give only what changes, whole. A new "
                    "version: its state and decisions stay."
                ),
                args_schema=_UpdateInput,
            ),
            StructuredTool.from_function(
                func=_board_show,
                name="board_show",
                description=(
                    "Show an interface the person pinned to Home (their Board) in this chat, instantly, by its "
                    "name: \"show my trip planner\". Prefer it to drawing a pinned interface again."
                ),
                args_schema=_BoardShowInput,
            ),
            StructuredTool.from_function(
                func=_page_read,
                name="page_read",
                description="Read a page's saved state and the decisions the person hasn't sent yet.",
                args_schema=_ReadInput,
            ),
        ]

    def execute(self, query: str) -> str:
        return "Use page_create, page_update or page_read with their fields."


registry.register(PagesTool())
