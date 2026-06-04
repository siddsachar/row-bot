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

    def execute(self, query: str) -> str:
        from tavily import TavilyClient
        import os

        client = TavilyClient(api_key=os.environ.get("TAVILY_API_KEY", ""))
        response = client.search(query, max_results=8)
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


registry.register(WebSearchTool())
