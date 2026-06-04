"""Arxiv retrieval tool — uses the ``arxiv`` package directly (no LangChain
ArxivRetriever) for reliable rate-limiting and newest-first sorting."""

from __future__ import annotations

import logging
import re

from row_bot.tools.base import BaseTool
from row_bot.tools import registry

logger = logging.getLogger(__name__)


class ArxivTool(BaseTool):

    @property
    def name(self) -> str:
        return "arxiv"

    @property
    def display_name(self) -> str:
        return "📚 Arxiv"

    @property
    def description(self) -> str:
        return (
            "Search arXiv for academic papers, sorted by newest first. "
            "Returns title, authors, date, abstract, and full-text HTML link "
            "for each result. Supports arXiv query syntax: ti:word (title), "
            "au:name (author), abs:word (abstract), cat:cs.AI (category). "
            "To read the full paper, pass the HTML link to the URL "
            "reader tool. Use this for research papers, scientific findings, "
            "ML/AI, physics, mathematics, computer science, or scholarly "
            "references."
        )

    @property
    def enabled_by_default(self) -> bool:
        return True

    @property
    def required_api_keys(self) -> dict[str, str]:
        return {}

    # ── Direct execute — bypasses get_retriever / ArxivRetriever ─────────
    def execute(self, query: str) -> str:
        import arxiv

        client = arxiv.Client(page_size=5, delay_seconds=3.0, num_retries=3)
        search = arxiv.Search(
            query=query,
            max_results=5,
            sort_by=arxiv.SortCriterion.SubmittedDate,
            sort_order=arxiv.SortOrder.Descending,
        )

        try:
            results = list(client.results(search))
        except Exception as exc:
            logger.warning("arXiv search failed: %s", exc)
            return f"arXiv search error: {exc}"

        if not results:
            return f"No arXiv papers found for: {query}"

        parts = []
        for i, r in enumerate(results, 1):
            short_id = r.get_short_id()
            # Strip version suffix for HTML URL (works without it)
            base_id = re.sub(r"v\d+$", "", short_id)
            html_url = f"https://arxiv.org/html/{base_id}"

            authors = ", ".join(a.name for a in r.authors[:5])
            if len(r.authors) > 5:
                authors += f" et al. ({len(r.authors)} authors)"

            parts.append(
                f"[{i}] {r.title}\n"
                f"Authors: {authors}\n"
                f"Published: {r.published.strftime('%Y-%m-%d')}\n"
                f"Categories: {r.primary_category}\n"
                f"Abstract: {r.summary}\n"
                f"Full text (HTML): {html_url}\n"
                f"PDF: {r.pdf_url}\n"
                f"SOURCE_URL: {r.entry_id}"
            )
        result = "\n\n---\n\n".join(parts)
        result += "\n\nTip: To read a paper's full text, pass its Full text (HTML) URL to read_url."
        return result


registry.register(ArxivTool())
