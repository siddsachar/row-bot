"""The ``company_brain`` tool (Row-Bot for Teams, B3): ``company_brain_search``, the company brain's recall with
citations, offered only while a company is joined.

- A registry tool of its own, so the company's policy can switch it off as a group (``tools.disabled_groups``:
  ``company_brain``) or by its name (``tools.disabled_tools``: ``company_brain_search``).
- ``sync()`` registers it while this Row-Bot is joined and takes it out otherwise. The tools package imports this
  module at start-up (one look for the record file), and the company's policy calls it whenever the policy in
  force changes: a join's first policy, and Leave. With no company, ``enforce.keep_tools`` leaves it out as well,
  so a registration left from an earlier join is never offered.
- It reads, so it isn't gated. Its answers are the company's documents, so they come to the model inside the
  external-content fence (``untrusted_tool_names``).
"""

from __future__ import annotations

import logging

from langchain_core.tools import StructuredTool
from pydantic import Field

from row_bot.company import recall, store
from row_bot.tools.base import BaseTool, NullMeansUnset

logger = logging.getLogger(__name__)


class _SearchInput(NullMeansUnset):
    query: str = Field(description="What to look for in the company's documents and facts.")
    spaces: list[str] = Field(
        default_factory=list,
        description="Only these spaces, by name or id. Leave it empty to search every space the person can read.")


def _search(query: str, spaces: list[str] | None = None) -> str:
    return recall.tool_text(query, spaces or ())


class CompanyBrainTool(BaseTool):

    @property
    def name(self) -> str:
        return recall.GROUP

    @property
    def display_name(self) -> str:
        return "🏢 Company brain"

    @property
    def description(self) -> str:
        return "Search the documents and facts the person's company shared, with where each answer came from."

    @property
    def untrusted_tool_names(self) -> set[str]:
        return {recall.TOOL}

    def as_langchain_tools(self) -> list:
        return [StructuredTool.from_function(
            func=_search,
            name=recall.TOOL,
            description=(
                "Search the company brain: the documents and facts the person's company shared in its spaces "
                "(its teams, customers, systems, policies, projects and terms). Use it for questions about the "
                "company. Each result says its space and document; cite them when you use one."),
            args_schema=_SearchInput,
        )]

    def execute(self, query: str) -> str:
        return recall.tool_text(query)


TOOL = CompanyBrainTool()


def sync() -> None:
    """Offer the tool exactly while a company is joined: register it, or take it out."""
    from row_bot.tools import registry

    joined = store.read_record() is not None
    present = registry.get_tool(recall.GROUP) is not None
    if joined and not present:
        registry.register(TOOL)
        logger.info("The company brain's tool is offered (a company is joined)")
    elif present and not joined:
        registry.unregister(recall.GROUP)
        logger.info("The company brain's tool is no longer offered (no company is joined)")


sync()
