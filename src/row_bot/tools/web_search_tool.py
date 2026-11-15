"""Web search retrieval tool (Tavily)."""

from __future__ import annotations

from row_bot.tools.base import BaseTool
from row_bot.tools import registry


class WebSearchTool(BaseTool):

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def display_name(self) -> str:
        return "🔍 Web Search"

    @property
    def description(self) -> str:
        return (
            "Search the live web for up-to-date information. "
            "Use this for current events, news, real-time data, prices, weather, "
            "product info, recent developments, or anything that may have changed "
            "since other knowledge sources were last updated."
        )

    @property
    def enabled_by_default(self) -> bool:
        return False

    @property
    def required_api_keys(self) -> dict[str, str]:
        return {"Tavily API Key": "TAVILY_API_KEY"}

    @property
    def untrusted_tool_names(self) -> set[str]:
        return {self.name}

    def execute(self, query: str) -> str:
        import requests
        from tavily import TavilyClient
        from tavily.errors import (ForbiddenError, InvalidAPIKeyError, MissingAPIKeyError, TimeoutError,
                                   UsageLimitExceededError)
        from row_bot.api_keys import get_key

        key = get_key("TAVILY_API_KEY")
        if not key:
            # Without a key Tavily isn't asked at all; the keyless search answers instead (A2).
            return _keyless_fallback(query, "no API key")
        try:
            response = TavilyClient(api_key=key).search(query, max_results=8)
        except (InvalidAPIKeyError, MissingAPIKeyError, ForbiddenError):
            return _keyless_fallback(query, "its API key was refused")
        except UsageLimitExceededError:
            return _keyless_fallback(query, "its usage limit is reached")
        except (TimeoutError, requests.exceptions.RequestException):
            return _keyless_fallback(query, "it couldn't be reached")
        results = response.get("results", [])

        if not results:
            return f"No results found for: {query}"

        parts = []
        for i, r in enumerate(results, 1):
            title = r.get("title", "")
            snippet = r.get("content", "")
            link = r.get("url", "Unknown")
            parts.append(
                f"[Result {i}] {title}\n"
                f"{snippet}\n"
                f"SOURCE_URL: {link}"
            )
        return "\n\n---\n\n".join(parts)


def _keyless_fallback(query: str, reason: str) -> str:
    """Tavily can't answer: search without a key, saying so in one line the model may pass on (A2).

    The keyless search is the DuckDuckGo tool's; when the person switched that off, its engines aren't asked.
    """
    from row_bot.tools.duckduckgo_tool import KeylessSearchFailed, keyless_search

    if not registry.is_enabled("duckduckgo"):
        return (
            f"Web search couldn't run: Tavily isn't available ({reason}), and the keyless search (DuckDuckGo) "
            "is switched off. Read a page you know with read_url instead."
        )
    try:
        results = keyless_search(query)
    except KeylessSearchFailed:
        return (
            f"Web search couldn't get results just now: Tavily isn't available ({reason}), and the keyless "
            "search engines limited requests or found nothing. Try different words in a minute, or read a "
            "page you know with read_url."
        )
    return f"Tavily isn't available ({reason}); results from the keyless search:\n\n{results}"


registry.register(WebSearchTool())
