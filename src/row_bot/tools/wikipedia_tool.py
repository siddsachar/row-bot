"""Wikipedia retrieval tool."""

from __future__ import annotations

import logging

from row_bot.tools.base import BaseTool
from row_bot.tools import registry

logger = logging.getLogger(__name__)


def _configure_wikipedia_client() -> None:
    """Make the legacy wikipedia package use the modern HTTPS API endpoint."""
    try:
        import wikipedia.wikipedia as wiki_impl

        api_url = str(getattr(wiki_impl, "API_URL", "") or "")
        if api_url.startswith("http://"):
            wiki_impl.API_URL = api_url.replace("http://", "https://", 1)
    except Exception:
        logger.debug("Wikipedia client HTTPS configuration skipped", exc_info=True)


class WikipediaTool(BaseTool):

    @property
    def name(self) -> str:
        return "wikipedia"

    @property
    def display_name(self) -> str:
        return "🌐 Wikipedia"

    @property
    def description(self) -> str:
        return (
            "Search Wikipedia for encyclopedic knowledge, definitions, "
            "historical events, biographies, geography, science concepts, "
            "and other general-purpose factual information. Use this for "
            "specific encyclopedia lookups or source-backed answers; answer "
            "broad conceptual questions directly unless the user asks for "
            "sources or current/reference material."
        )

    @property
    def enabled_by_default(self) -> bool:
        return True

    @property
    def required_api_keys(self) -> dict[str, str]:
        return {}

    def get_retriever(self, **kwargs):
        from langchain_community.retrievers.wikipedia import WikipediaRetriever

        retriever = WikipediaRetriever()
        _configure_wikipedia_client()
        return retriever

    def execute(self, query: str) -> str:
        """Run Wikipedia with a defensive error boundary.

        The upstream wikipedia package can raise raw JSON parsing errors when
        the API returns an empty/non-JSON response. Convert that into a useful
        tool result so the agent can recover instead of retrying the same call.
        """
        try:
            return super().execute(query)
        except Exception as exc:
            logger.warning("Wikipedia lookup failed for %r: %s", query, exc, exc_info=True)
            return (
                "Wikipedia lookup is temporarily unavailable for this query. "
                f"Reason: {type(exc).__name__}: {exc}. "
                "Do not retry the Wikipedia tool for the same query. If the "
                "user asked a broad or general question, answer from general "
                "knowledge instead; if they asked for citations, say that "
                "Wikipedia could not be reached."
            )


registry.register(WikipediaTool())
