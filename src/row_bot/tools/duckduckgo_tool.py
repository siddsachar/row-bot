"""DuckDuckGo web search tool — no API key required."""

from __future__ import annotations

from row_bot.tools.base import BaseTool
from row_bot.tools import registry


class DuckDuckGoTool(BaseTool):

    @property
    def name(self) -> str:
        return "duckduckgo"

    @property
    def display_name(self) -> str:
        return "🦆 DuckDuckGo"

    @property
    def description(self) -> str:
        return (
            "Search the web using DuckDuckGo (no API key needed). "
            "Use this for current events, news, real-time data, prices, weather, "
            "recent developments, or any question requiring up-to-date information."
        )

    @property
    def enabled_by_default(self) -> bool:
        return True

    @property
    def required_api_keys(self) -> dict[str, str]:
        return {}

    @property
    def untrusted_tool_names(self) -> set[str]:
        return {self.name}

    def execute(self, query: str) -> str:
        try:
            return keyless_search(query)
        except KeylessSearchFailed:
            return KEYLESS_SEARCH_FAILED


# What the model reads when the keyless search gets nothing: a next step, not a stack trace (A2).
KEYLESS_SEARCH_FAILED = (
    "Web search couldn't get results just now (the search engines limited requests or found nothing). "
    "Try different words in a minute, or read a page you know with read_url."
)


class KeylessSearchFailed(Exception):
    """The keyless search reached no engine that answered, or none had results."""


def keyless_search(query: str) -> str:
    """Search the web with no API key (``ddgs`` across several engines), as numbered results.

    Raises ``KeylessSearchFailed`` when every engine refused, timed out or found nothing.
    """
    from ddgs.exceptions import DDGSException
    from langchain_community.tools import DuckDuckGoSearchResults
    from langchain_community.utilities import DuckDuckGoSearchAPIWrapper

    wrapper = DuckDuckGoSearchAPIWrapper(max_results=8)
    search = DuckDuckGoSearchResults(
        api_wrapper=wrapper,
        output_format="list",
    )
    try:
        results = search.invoke(query)
    except DDGSException as exc:
        raise KeylessSearchFailed(str(exc)) from exc

    if not results:
        return f"No results found for: {query}"

    parts = []
    for i, r in enumerate(results, 1):
        snippet = r.get("snippet", "")
        title = r.get("title", "")
        link = r.get("link", "Unknown")
        parts.append(
            f"[Result {i}] {title}\n"
            f"{snippet}\n"
            f"SOURCE_URL: {link}"
        )
    return "\n\n---\n\n".join(parts)


registry.register(DuckDuckGoTool())
