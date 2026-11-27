"""Present the finished work in the chat (finals first): one ``present`` tool, in its own group.

``present`` shows each file it names large in this reply and marks it final: the film, picture, page or
document the person asked for. It only marks and shows: it reaches nothing beyond this chat's files and the
workspace, found as every tool finds files (``media_sources.find``: a chat file or an output by its name,
``name@v2``, a workspace path inside the workspace, ``rec-…``, ``edit-…``, ``vid-…``), sends nothing anywhere
and never asks. With no files it lists this chat's outputs (``media_sources.outputs_text``), so a name is
never lost. A workspace file (a film the agent rendered with its own script) becomes the chat's own file, as a
version of its output (``file_deliveries.chat_output``), never a hidden extra copy. Phones and chat apps
stay with ``send_file``: a chat app's turn never binds ``present`` (``agent.get_agent_graph``).

What a tool makes on the way to a final is a working file (raw recordings, frames, contact sheets, voice
lines for a video, test renders): kept in the chat's files and folded away in the transcript. A recording
or a voice file the person asked for can itself be the final.
"""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import Field

from row_bot.tools import registry
from row_bot.tools.base import BaseTool, NullMeansUnset

# At most this many files in one call: a reply shows each large, and its step keeps at most eight.
MAX_FILES = 8
_THEN = "Finish anything else they asked for, then end with a line saying what it is."


def _present(files: list[str] | None = None, caption: str = "") -> str:
    from row_bot import file_deliveries as deliveries
    from row_bot.application.attachment_context import current_caches
    from row_bot.media_sources import SourceError, identity, outputs_text
    from row_bot.tool_outputs import current_thread_id

    conversation = str(current_thread_id() or "")
    caches = current_caches()
    if not conversation or caches is None or caches.conversation_id != conversation:
        return "Presenting needs a chat shown in Row-Bot; to send files to a chat app or a phone, use send_file."
    names = list(dict.fromkeys(str(value).strip() for value in files or [] if str(value or "").strip()))
    if not names:
        return outputs_text(conversation)
    if len(names) > MAX_FILES:
        return f"Present at most {MAX_FILES} files at once; nothing was presented."
    # A page made in this chat already shows as its card: naming it is no error (F18).
    pages = _pages_of(conversation, names)
    names = [name for name in names if name not in pages]
    if not names:
        one = len(pages) == 1
        return (f"{_page_note(pages)}, so there is nothing to present. Finish anything else the person asked for, "
                f"then end with one line about the {'page' if one else 'pages'}.")
    try:
        shown = [deliveries.chat_output(conversation, source) for source in deliveries.resolve(names, conversation)]
    except SourceError as exc:
        return f"{exc} Nothing was presented."
    words = " ".join(str(caption or "").split())[:300]
    caches.presented.extend({**item, "caption": words if index == 0 else ""} for index, item in enumerate(shown))
    # Each output first (W4): history masking keeps a result's first line.
    named = "\n".join([identity(item) for item in shown] + ([f"{_page_note(pages)}."] if pages else []))
    one = len(shown) == 1
    if all(item["was_final"] for item in shown):
        # A generated picture or video already shows in the chat: it is marked, never shown twice.
        return (f"{named}\nMarked final: {'it already shows' if one else 'they already show'} in this chat, so "
                f"{'it isn' if one else 'they aren'}'t shown twice. {_THEN}")
    return f"{named}\nPresented in this reply, large, where the person sees {'it' if one else 'them'}. {_THEN}"


def _pages_of(conversation: str, names: list[str]) -> dict[str, str]:
    """The names that are this chat's pages (``page-…``), each with its title."""
    from row_bot import pages

    found = {}
    for name in names:
        if name.startswith("page-") and (page := pages.find(conversation, name)) is not None:
            found[name] = page.title
    return found


def _page_note(pages: dict[str, str]) -> str:
    listed = ", ".join(f"{page} (“{title}”)" for page, title in pages.items())
    return f"{listed} already {'shows' if len(pages) == 1 else 'show'} in this chat as a page card"


class _PresentInput(NullMeansUnset):
    files: list[str] = Field(default_factory=list, description=(
        f"The finals, in order (up to {MAX_FILES}): a file in this chat by name, a workspace path, "
        "or rec-…, edit-…, vid-…."))
    caption: str = Field(default="", description="A short line shown with them.")


class PresentTool(BaseTool):

    @property
    def name(self) -> str:
        return "presentation"

    @property
    def display_name(self) -> str:
        return "🖼️ Present"

    @property
    def description(self) -> str:
        return "Show finished files in the chat, large, and mark them final."

    @property
    def enabled_by_default(self) -> bool:
        return True

    def as_langchain_tools(self) -> list:
        return [
            StructuredTool.from_function(
                func=_present,
                name="present",
                description=(
                    "Show the person the finished result, large, in this reply (in a Row-Bot chat; in a chat "
                    "app, send_file delivers): the film, picture, page or file they asked for, and mark it "
                    "final. Use it once the final is ready: every final must be presented, in the reply that "
                    "delivers it. Never present working material for a final (raw "
                    "recordings you'll cut, frames, contact sheets, review sheets, voice lines for a video, "
                    "test renders): it stays folded in the chat. With no files, it lists this chat's outputs."
                ),
                args_schema=_PresentInput,
            )
        ]

    def execute(self, query: str) -> str:
        return "Use present with its fields."


registry.register(PresentTool())
