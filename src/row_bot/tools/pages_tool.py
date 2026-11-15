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
from row_bot.tools.base import BaseTool

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
    try:
        updated = pages.update(found, title=title, html=html, data=data, libraries=libraries,
                               data_given=data is not None)
    except pages.PageError as exc:
        return f"The page wasn't changed: {exc}"
    return pages.summary(updated, made=False) + _remote_note(html) + _script_note(html)


def _page_read(page: str, version: int | None = None, include_html: bool = False) -> str:
    from row_bot import pages

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


class _CreateInput(BaseModel):
    title: str = Field(description="A short title, shown on the card and above the page.")
    html: str = Field(description=(
        "One HTML document with inline CSS and JS (up to 1 MB). No network: nothing loads from the internet. "
        "Use window.rowbot for data, state and decisions."))
    data: Any = Field(default=None, description="JSON the page reads with rowbot.data() (up to 1 MB).")
    libraries: list[Library] = Field(default_factory=list, description=(
        "charts: Chart.js 4 as window.Chart. rowbot-style: Row-Bot's look in light and dark (rb- classes)."))


class _UpdateInput(BaseModel):
    page: str = Field(description="The page's id (page-…), or its exact title.")
    html: str | None = Field(default=None, description="The whole new HTML document, if it changes.")
    data: Any = Field(default=None, description="The whole new data, if it changes (progress, new numbers).")
    title: str | None = Field(default=None, description="A new title, if it changes.")
    libraries: list[Library] | None = Field(default=None, description="The libraries, if they change.")


class _ReadInput(BaseModel):
    page: str = Field(description="The page's id (page-…), or its exact title.")
    version: int | None = Field(default=None, description="An earlier version to read (the last 10 are kept).")
    include_html: bool = Field(default=False, description="Also return the HTML and data.")


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
                    "Make an interactive page the person opens beside the chat: a plan with Approve / Change / "
                    "Skip, a checklist, a dashboard, a comparison, a form or a small tool. Use it on your own, "
                    "unasked, whenever your answer is one of these. Load the Making a page skill first for the "
                    "templates and rules."
                ),
                args_schema=_CreateInput,
            ),
            StructuredTool.from_function(
                func=_page_update,
                name="page_update",
                description=(
                    "Change a page: new data (progress, fresh numbers), HTML, title or libraries. A new "
                    "version; its state and decisions stay."
                ),
                args_schema=_UpdateInput,
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
