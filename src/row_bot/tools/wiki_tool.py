"""Wiki Vault tool — search, read, rebuild, and manage the Obsidian-compatible vault.

Exposes sub-tools so the agent can interact with the exported wiki:
search articles, read specific entries, trigger a full rebuild,
export conversations, and report vault statistics.
"""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from row_bot.tools.base import BaseTool
from row_bot.tools import registry


# ── Pydantic schemas ─────────────────────────────────────────────────────────

class _SearchWikiInput(BaseModel):
    query: str = Field(
        description="Search query — matched against all wiki article contents."
    )
    max_results: int = Field(
        default=10,
        description="Maximum number of results to return.",
    )


class _ReadArticleInput(BaseModel):
    subject: str = Field(
        description="Entity subject/name to look up (e.g. 'Mom', 'Python 3.13')."
    )


class _RebuildVaultInput(BaseModel):
    pass


class _VaultStatsInput(BaseModel):
    pass


class _ExportConversationInput(BaseModel):
    title: str = Field(
        default="",
        description="Optional title for the exported conversation.",
    )


# ── Tool functions ───────────────────────────────────────────────────────────

def _search_wiki(query: str, max_results: int = 10) -> str:
    """Search wiki vault articles."""
    import row_bot.wiki_vault as wiki_vault

    if not wiki_vault.is_enabled():
        return "Wiki vault is not enabled. Enable it in Settings → Knowledge → Wiki Vault."

    results = wiki_vault.search_vault(query, max_results=max_results)
    if not results:
        return f"No wiki articles found matching '{query}'."

    lines = [f"Found {len(results)} result(s):\n"]
    for r in results:
        lines.append(f"**{r['title']}** ({r['path']})")
        lines.append(f"  …{r['snippet']}…")
        lines.append("")
    return "\n".join(lines)


def _read_article(subject: str) -> str:
    """Read a specific wiki article by entity name."""
    import row_bot.wiki_vault as wiki_vault

    if not wiki_vault.is_enabled():
        return "Wiki vault is not enabled. Enable it in Settings → Knowledge → Wiki Vault."

    content = wiki_vault.read_article(subject)
    if content is None:
        return f"No wiki article found for '{subject}'. The entity may not exist or may be too sparse for a full article."
    return content


def _rebuild_vault() -> str:
    """Trigger a full vault rebuild — re-exports all entities."""
    import row_bot.wiki_vault as wiki_vault

    if not wiki_vault.is_enabled():
        return "Wiki vault is not enabled. Enable it in Settings → Knowledge → Wiki Vault."

    stats = wiki_vault.rebuild_vault()
    return (
        f"Wiki vault rebuilt successfully.\n"
        f"Total entities: {stats['total']}\n"
        f"Full articles: {stats['exported']}\n"
        f"Sparse (in index): {stats['sparse']}\n"
        f"Entity types: {stats['types']}"
    )


def _vault_stats() -> str:
    """Report wiki vault statistics."""
    import row_bot.wiki_vault as wiki_vault

    stats = wiki_vault.get_vault_stats()
    if not stats.get("enabled"):
        return "Wiki vault is not enabled. Enable it in Settings → Knowledge → Wiki Vault."

    return (
        f"Wiki Vault Status:\n"
        f"  Vault path: {stats['vault_path']}\n"
        f"  Wiki articles: {stats['articles']}\n"
        f"  Exported conversations: {stats['conversations']}\n"
        f"  Enabled: {stats['enabled']}"
    )


def _export_conversation(title: str = "") -> str:
    """Export the current conversation to the wiki vault."""
    import row_bot.wiki_vault as wiki_vault

    if not wiki_vault.is_enabled():
        return "Wiki vault is not enabled. Enable it in Settings → Knowledge → Wiki Vault."

    # Access the current thread from the agent's context
    try:
        from row_bot.agent import _current_thread_id_var
        thread_id = _current_thread_id_var.get()
    except Exception:
        thread_id = None

    if not thread_id:
        return "No active conversation to export."

    try:
        from row_bot.threads import _list_threads
        from langchain_core.messages import HumanMessage, AIMessage

        # Load messages from the LangGraph checkpointer
        from row_bot.agent import get_agent_graph
        graph = get_agent_graph()
        config = {"configurable": {"thread_id": thread_id}}
        state = graph.get_state(config)
        messages = state.values.get("messages", []) if state and state.values else []

        if not messages:
            return "Current conversation has no messages to export."

        msg_dicts = []
        for m in messages:
            role = getattr(m, "type", "unknown")
            content = m.content if isinstance(m.content, str) else str(m.content)
            if content.strip():
                msg_dicts.append({"role": role, "content": content})

        conv_title = title or thread_id
        # Try to get thread name from DB
        try:
            threads = _list_threads()
            for t in threads:
                if t.get("id") == thread_id or t.get("thread_id") == thread_id:
                    conv_title = title or t.get("name", thread_id)
                    break
        except Exception:
            pass

        path = wiki_vault.export_conversation(thread_id, msg_dicts, title=conv_title)
        if path:
            return f"Conversation exported to: {path}"
        return "Failed to export conversation."
    except Exception as exc:
        return f"Failed to export conversation: {exc}"


# ── Tool class ───────────────────────────────────────────────────────────────

class WikiTool(BaseTool):
    @property
    def name(self) -> str:
        return "wiki"

    @property
    def display_name(self) -> str:
        return "📚 Wiki Vault"

    @property
    def description(self) -> str:
        return (
            "Search and read the Obsidian-compatible wiki vault — an exported "
            "knowledge base of all memories and documents as inter-linked "
            "markdown files. Rebuild the vault or export conversations."
        )

    @property
    def enabled_by_default(self) -> bool:
        return True

    @property
    def required_api_keys(self) -> dict[str, str]:
        return {}

    def as_langchain_tools(self) -> list:
        return [
            StructuredTool.from_function(
                func=_read_article,
                name="wiki_read",
                description=(
                    "Read the full wiki article for a specific entity by name. "
                    "Returns the markdown content including frontmatter, "
                    "description, and connections."
                ),
                args_schema=_ReadArticleInput,
            ),
            StructuredTool.from_function(
                func=_rebuild_vault,
                name="wiki_rebuild",
                description=(
                    "Rebuild the entire wiki vault from scratch — re-exports "
                    "all entities as markdown files and regenerates all indexes. "
                    "Use when the user asks to refresh or rebuild their wiki."
                ),
                args_schema=_RebuildVaultInput,
            ),
            StructuredTool.from_function(
                func=_vault_stats,
                name="wiki_stats",
                description=(
                    "Get statistics about the wiki vault: number of articles, "
                    "conversations exported, vault path, and enabled status."
                ),
                args_schema=_VaultStatsInput,
            ),
            StructuredTool.from_function(
                func=_export_conversation,
                name="wiki_export_conversation",
                description=(
                    "Export the current conversation to the wiki vault as a "
                    "markdown file. Optionally provide a title."
                ),
                args_schema=_ExportConversationInput,
            ),
        ]

    def execute(self, query: str) -> str:
        return _search_wiki(query)


registry.register(WikiTool())
