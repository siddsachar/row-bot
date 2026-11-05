"""Memory tool — save, search, list, update, delete, link, and explore memories.

Exposes multiple LangChain sub-tools so the agent can manage a persistent
personal knowledge graph across conversations.  Categories: person, preference,
fact, event, place, project, organisation, concept, skill, media.

v3.6+: Two new sub-tools — ``link_memories`` (create relations between
entities) and ``explore_connections`` (traverse the knowledge graph).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from row_bot.tool_outputs import bounded, compact_json
from row_bot.tools.base import BaseTool
from row_bot.tools import registry
import row_bot.memory as memory_db
import row_bot.knowledge_graph as kg
import row_bot.memory_evolution as memory_evo
from row_bot import stale_skills


# ── Pydantic schemas for structured input ────────────────────────────────────

# What each category is for, and when to save, link or update, is in the system prompt's MEMORY
# GUIDELINES; the schemas carry only the closed set of categories.
MemoryCategory = Literal[tuple(sorted(kg.VALID_CATEGORIES))]  # type: ignore[valid-type]
_LIST_MEMORIES_CHARS = 40_000  # Beyond this the list is saved, and read on with read_tool_output.


class _SaveMemoryInput(BaseModel):
    category: MemoryCategory = Field(description="Memory category.")  # type: ignore[valid-type]
    subject: str = Field(description="Short name or title, e.g. 'Mom', 'Coffee', 'Python 3.13'.")
    content: str = Field(description="What to remember, e.g. 'Mom's birthday is March 15'.")
    tags: str = Field(default="", description="Optional comma-separated tags.")


class _SearchMemoryInput(BaseModel):
    query: str = Field(description="Words or a phrase to search for.")
    category: MemoryCategory | None = Field(  # type: ignore[valid-type]
        default=None,
        description="Only this category. Omit to search all.",
    )


class _SearchMemoryAsOfInput(_SearchMemoryInput):
    as_of: str | None = Field(
        default=None,
        description="Only what memory said on this date (YYYY-MM-DD). Omit for now.",
    )


class _ListMemoriesInput(BaseModel):
    category: MemoryCategory | None = Field(  # type: ignore[valid-type]
        default=None,
        description="Only this category. Omit to list all.",
    )


class _UpdateMemoryInput(BaseModel):
    memory_id: str = Field(description="ID from recalled memories, search_memory or list_memories.")
    content: str = Field(description="New content; replaces the old.")
    subject: str | None = Field(default=None, description="New subject. Omit to keep.")
    entity_type: MemoryCategory | None = Field(  # type: ignore[valid-type]
        default=None,
        description="New category. Omit to keep.",
    )
    aliases: str | None = Field(default=None, description="Comma-separated aliases. Omit to keep.")
    tags: str | None = Field(default=None, description="Comma-separated tags. Omit to keep.")


class _DeleteMemoryInput(BaseModel):
    memory_id: str = Field(description="ID from search_memory or list_memories.")


class _LinkMemoriesInput(BaseModel):
    source_id: str = Field(description="Source entity: its subject name (preferred, e.g. 'Bob') or ID.")
    target_id: str = Field(description="Target entity: its subject name (e.g. 'User') or ID.")
    relation_type: str = Field(description="snake_case relation, e.g. 'father_of', 'lives_in', 'works_on'.")


class _ExploreConnectionsInput(BaseModel):
    entity_id: str = Field(description="Entity to explore: its subject name (preferred, e.g. 'User') or ID.")
    hops: int = Field(default=1, description="How far to go: 1 for direct links, up to 3.")


# ── Contradiction detection ──────────────────────────────────────────────────

_CONTRADICTION_PROMPT = """\
You are checking whether two pieces of information about "{subject}" contradict each other.

Existing: {old_content}
New: {new_content}

Do these contain contradictory facts (e.g. different dates, numbers, names, \
or mutually exclusive statements about the same attribute)?

If YES, reply with a single short sentence describing the conflict.
If NO (they are compatible or additive), reply with exactly: NO"""

_CONFLICT_FALLBACK = "The new information conflicts with what is already saved."


def _contradiction_answer():
    from row_bot.providers.background import structured_answer

    return structured_answer("contradiction_check", {
        "contradiction": {"type": "boolean",
                          "description": "True when the two pieces of information contradict each other."},
        "conflict": {"type": "string",
                     "description": "One short sentence describing the conflict, or an empty string."},
    })


def _contradiction_verdict(raw) -> str | None:
    """The conflict sentence, or None: from a schema reply, else from "NO" / a sentence."""
    from row_bot.providers.background import parse_json_object

    if isinstance(raw, list):
        raw = " ".join(
            b.get("text", "") if isinstance(b, dict) else str(b)
            for b in raw
        )
    raw = str(raw or "").strip()
    parsed = parse_json_object(raw)
    if parsed is not None and "contradiction" in parsed:
        flag = parsed.get("contradiction")
        if not (flag is True or str(flag).strip().lower() in {"true", "yes"}):
            return None
        return str(parsed.get("conflict") or "").strip() or _CONFLICT_FALLBACK
    if raw.upper().startswith("NO"):
        return None
    return raw


def _check_contradiction(old_content: str, new_content: str, subject: str, *, invoke=None, validate=None) -> str | None:
    """Return a conflict description if old and new content contradict, else None.

    Without ``invoke`` this is a background call (WP C1): the running turn's
    model (the default model outside a turn), the lowest reasoning effort and
    a short output cap.
    """
    try:
        if validate is not None:
            validate()
        from langchain_core.messages import HumanMessage

        prompt = _CONTRADICTION_PROMPT.format(
            subject=subject,
            old_content=old_content,
            new_content=new_content,
        )
        if invoke is not None:
            raw = invoke(prompt) or ""
        else:
            from row_bot.providers.background import active_turn_ids, invoke_background

            thread_id, generation_id = active_turn_ids()
            raw = invoke_background(
                "contradiction",
                [HumanMessage(content=prompt)],
                answer=_contradiction_answer(),
                thread_id=thread_id,
                generation_id=generation_id,
            )
        if validate is not None:
            validate()
        return _contradiction_verdict(raw)
    except Exception:
        if validate is not None or invoke is not None:
            raise
        # On failure, allow the merge to proceed (no false blocks)
        return None


# ── Incognito chats (N11) ────────────────────────────────────────────────────

INCOGNITO_NOT_SAVED = "Not saved: this chat is incognito, and the person chose not to save this to memory."


def _incognito_hold(tool: str, arguments: dict, text: str) -> str | None:
    """In an incognito chat only the person saves to memory: a card with the exact text, under every level.

    The reviewer never approves it and Hands-off never skips it. None when the write may go on
    (the chat is not incognito, or the person chose Save); otherwise what the tool answers.
    """
    from row_bot.threads import incognito_state
    from row_bot.tool_outputs import current_thread_id

    thread_id = current_thread_id()
    if not thread_id or not incognito_state(thread_id).incognito:
        return None
    from row_bot.tools.approval_gate import (
        INCOGNITO_MEMORY_QUESTION,
        INCOGNITO_MEMORY_REASON,
        call_binding,
        person_approval,
        with_approval,
    )

    payload = {"tool": tool, "label": "Save to memory", "description": text, "args": arguments,
               "question": INCOGNITO_MEMORY_QUESTION, "memory_save": {"text": text}}
    approved, line = person_approval(payload, binding=call_binding(tool, arguments), reason=INCOGNITO_MEMORY_REASON)
    return None if approved else with_approval(line, INCOGNITO_NOT_SAVED)


# ── Tool functions ───────────────────────────────────────────────────────────

def _save_memory(category: str, subject: str, content: str, tags: str = "") -> str:
    """Save a new memory, or update an existing one if a near-duplicate exists."""
    held = _incognito_hold("save_memory", {"category": category, "subject": subject, "content": content,
                                           "tags": tags}, f"{subject.strip()}: {content.strip()}")
    if held is not None:
        return held
    try:
        manual_props = memory_evo.merge_properties(
            {},
            {"status": "active"},
            source="live",
            entity_type=category,
            actor="manual",
            source_context={"actor": "manual"},
            high_authority=True,
        )
        # Deterministic dedup: exact category + normalised subject match
        existing = memory_db.find_by_subject(category, subject)
        if existing:
            # Same subject already stored — merge content intelligently.
            old_content = existing.get("content", "").strip()
            new_content = content.strip()

            if new_content.lower() in old_content.lower():
                # New content is already captured — keep existing as-is
                merged = old_content
            elif old_content.lower() in new_content.lower():
                # New content is a superset — replace entirely
                merged = new_content
            else:
                # Both have unique info — check for contradiction before merging
                conflict = _check_contradiction(old_content, new_content, subject)
                if conflict:
                    memory_evo.mark_needs_review(
                        existing["id"],
                        conflict,
                        actor="manual",
                        incoming={
                            "subject": subject,
                            "category": category,
                            "content": new_content,
                            "source": "live",
                        },
                    )
                    return (
                        f"⚠️ CONFLICT for '{subject}': {conflict}\n"
                        f"Existing content: {old_content}\n"
                        f"New content: {new_content}\n"
                        f"Ask the user which is correct before updating."
                    )
                # No contradiction — safe to combine
                merged = f"{old_content}. {new_content}".replace(". . ", ". ")

            result = memory_db.update_memory(
                existing["id"],
                merged,
                tags=tags if tags else None,
                source="live",
                properties=memory_evo.merge_properties(
                    existing.get("properties", {}),
                    manual_props,
                    source="live",
                    entity_type=existing.get("entity_type", existing.get("category", category)),
                    actor="manual",
                    source_context={"actor": "manual"},
                    high_authority=True,
                ),
            )
            if result:
                return (
                    f"Memory updated (merged with existing).\n"
                    f"ID: {result['id']}\n"
                    f"Category: {result['category']}\n"
                    f"Subject: {result['subject']}\n"
                    f"Content: {result['content']}"
                )

        result = memory_db.save_memory(
            category,
            subject,
            content,
            tags,
            source="live",
            properties=manual_props,
        )
        return (
            f"Memory saved successfully.\n"
            f"ID: {result['id']}\n"
            f"Category: {result['category']}\n"
            f"Subject: {result['subject']}\n"
            f"Content: {result['content']}"
        )
    except (RuntimeError, ValueError) as exc:
        return f"Error: {exc}"


def _as_of(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value).strip())
    except ValueError:
        return None
    # A date alone means the end of that day.
    return moment.replace(hour=23, minute=59, second=59) if len(str(value).strip()) <= 10 else moment


def _search_memory(query: str, category: str = "", as_of: str | None = None) -> str:
    """Search memories using hybrid retrieval (FAISS + graph expansion + SQL LIKE)."""
    results = kg.retrieve_memory_candidates(query, top_k=10, threshold=0.3, as_of=_as_of(as_of))
    if category:
        cat = category.lower().strip()
        results = [m for m in results
                   if m.get("category", m.get("entity_type", "")) == cat]
    if not results:
        return "No memories found matching that query."
    kg.touch_recalled([m["id"] for m in results if m.get("id")])
    entries = []
    for m in results:
        props = m.get("properties", "{}")
        if isinstance(props, str):
            try:
                props = json.loads(props or "{}")
            except (json.JSONDecodeError, TypeError):
                props = {}
        props = props if isinstance(props, dict) else {}
        score = m.get("score", "")
        status = props.get("status", "active")
        # Empty fields and the default status are left out (compact_json drops the empty ones).
        entry = {
            "id": m["id"],
            "category": m.get("category", m.get("entity_type", "")),
            "subject": m["subject"],
            "content": m.get("content", m.get("description", "")),
            "tags": m.get("tags", ""),
            "relevance": round(score, 3) if isinstance(score, float) else score,
            "source": m.get("source", ""),
            "status": "" if status == "active" else status,
            "confidence": props.get("confidence", ""),
            "tier": props.get("memory_tier", ""),
            "review_reason": props.get("review_reason", ""),
            "superseded_by": props.get("superseded_by", ""),
            "supersedes": props.get("supersedes", []),
            "updated": m.get("updated_at", "")[:16],
        }
        # Include relationship context when available (from graph expansion)
        if m.get("via") == "graph" and m.get("relations"):
            rel_strs = [f"{r['from']} → {r['type']} → {r['to']}"
                        for r in m["relations"]]
            entry["connected_via"] = "; ".join(rel_strs)
        entries.append(entry)
    return compact_json(entries)


def _list_memories(category: str = "") -> str:
    """List stored memories."""
    cat = category if category else None
    results = memory_db.list_memories(category=cat)
    if not results:
        return "No memories stored yet." if not cat else f"No memories in category '{cat}'."
    entries = []
    for m in results:
        entries.append({
            "id": m["id"],
            "category": m["category"],
            "subject": m["subject"],
            "content": m["content"],
            "tags": m["tags"],
            "updated": m["updated_at"][:16],
        })
    return bounded(compact_json(entries), tool_name="list_memories", max_chars=_LIST_MEMORIES_CHARS)


def _update_memory(
    memory_id: str,
    content: str,
    subject: str | None = None,
    entity_type: str | None = None,
    aliases: str | None = None,
    tags: str | None = None,
) -> str:
    """Update an existing memory's content and metadata."""
    existing = memory_db.get_memory(memory_id)
    held = _incognito_hold(
        "update_memory",
        {"memory_id": memory_id, "content": content, "subject": subject, "entity_type": entity_type,
         "aliases": aliases, "tags": tags},
        f"{(subject or (existing or {}).get('subject') or memory_id).strip()}: {content.strip()}",
    )
    if held is not None:
        return held
    existing_props = existing.get("properties", {}) if existing else {}
    merged_props = memory_evo.merge_properties(
        existing_props,
        {"status": "active", "last_user_modified_at": datetime.now().isoformat()},
        source=(existing or {}).get("source", "live"),
        entity_type=entity_type or (existing or {}).get("entity_type", (existing or {}).get("category", "")),
        actor="manual",
        source_context={"actor": "manual"},
        high_authority=True,
    )
    result = memory_db.update_memory(
        memory_id,
        content,
        subject=subject,
        category=entity_type,
        aliases=aliases,
        tags=tags,
        properties=merged_props,
    )
    if result is None:
        return f"Memory '{memory_id}' not found. Use search_memory or list_memories to find the correct ID."
    memory_evo.append_journal(
        "user_modified",
        entity_id=memory_id,
        actor="manual",
        reason="manual_memory_tool_update",
        source=result.get("source", "live"),
        new_status=merged_props.get("status"),
    )
    return (
        f"Memory updated successfully.\n"
        f"ID: {result['id']}\n"
        f"Subject: {result['subject']}\n"
        f"Type: {result.get('category', result.get('entity_type', ''))}\n"
        f"New content: {result['content']}"
    )


def _delete_memory(memory_id: str) -> str:
    """Delete a memory by ID."""
    if memory_db.delete_memory(memory_id):
        return f"Memory '{memory_id}' deleted."
    return f"Memory '{memory_id}' not found."


def _resolve_entity(name_or_id: str) -> dict | None:
    """Resolve an entity by subject name (preferred) or hex ID."""
    # Try by name first (most common path for the agent)
    entity = kg.find_by_subject(entity_type=None, subject=name_or_id)
    if entity:
        return entity
    # Fall back to direct ID lookup
    entity = kg.get_entity(name_or_id)
    if entity:
        return entity
    return None


def _link_memories(source_id: str, target_id: str, relation_type: str) -> str:
    """Create a relationship between two memories in the knowledge graph."""
    import time

    held = _incognito_hold("link_memories", {"source_id": source_id, "target_id": target_id,
                                             "relation_type": relation_type},
                           f"{source_id} {relation_type.replace('_', ' ')} {target_id}")
    if held is not None:
        return held
    try:
        # Resolve by name or ID
        source_entity = _resolve_entity(source_id)
        target_entity = _resolve_entity(target_id)
        if not source_entity:
            # Retry once after a short delay — the entity may have been
            # created by a parallel tool call in the same batch.
            time.sleep(0.5)
            source_entity = _resolve_entity(source_id)
        if not source_entity:
            return f"Error: source entity '{source_id}' not found. Use search_memory or list_memories to find the correct name or ID."
        if not target_entity:
            # Retry once after a short delay — the entity may have been
            # created by a parallel tool call in the same batch.
            time.sleep(0.5)
            target_entity = _resolve_entity(target_id)
        if not target_entity:
            return f"Error: target entity '{target_id}' not found. Use search_memory or list_memories to find the correct name or ID."

        rel = kg.add_relation(source_entity['id'], target_entity['id'], relation_type)
        if rel:
            return (
                f"Relationship created successfully.\n"
                f"{source_entity['subject']} --[{rel['relation_type']}]--> {target_entity['subject']}\n"
                f"Relation ID: {rel['id']}"
            )
        return "Error: could not create relationship (entities may not exist)."
    except Exception as exc:
        return f"Error creating relationship: {exc}"


def _explore_connections(entity_id: str, hops: int = 1) -> str:
    """Explore the knowledge graph around an entity."""
    try:
        entity = _resolve_entity(entity_id)
        if not entity:
            return f"Entity '{entity_id}' not found. Use search_memory or list_memories to find the correct name or ID."

        resolved_id = entity['id']
        hops = max(1, min(hops, 3))  # cap at 3 to prevent huge traversals
        neighbors = kg.get_neighbors(resolved_id, hops=hops)
        relations = kg.get_relations(resolved_id)

        parts = [f"**{entity.get('subject', '?')}** ({entity.get('entity_type', '?')})"]

        if relations:
            parts.append(f"\nRelationships ({len(relations)}):")
            for rel in relations:
                arrow = "-->" if rel["direction"] == "outgoing" else "<--"
                parts.append(
                    f"  {arrow} [{rel['relation_type']}] {rel['peer_subject']} "
                    f"(id: {rel['peer_id']})"
                )

        if neighbors:
            if len(neighbors) > len(relations):
                parts.append(f"\nNearby entities within {hops} hop(s): {len(neighbors)}")
                for n in neighbors[:15]:  # cap display
                    hop = n.get("hop", "?")
                    parts.append(
                        f"  [{hop} hop] {n.get('subject', '?')} ({n.get('entity_type', '?')}) "
                        f"(id: {n['id']})"
                    )
                if len(neighbors) > 15:
                    parts.append(f"  ... and {len(neighbors) - 15} more")

        if not relations and not neighbors:
            parts.append("\nNo connections found. Use link_memories to create relationships.")

        # Include Mermaid diagram for visual context
        mermaid = kg.to_mermaid(resolved_id, hops=hops, max_nodes=15)
        if mermaid and mermaid.count("\n") > 1:
            parts.append(f"\n```mermaid\n{mermaid}\n```")
            parts.append("\nInclude the mermaid diagram above verbatim in your response so the user can see the visual graph.")

        return "\n".join(parts)
    except Exception as exc:
        return f"Error exploring connections: {exc}"


# ── Tool class ───────────────────────────────────────────────────────────────

class MemoryTool(BaseTool):

    @property
    def name(self) -> str:
        return "memory"

    @property
    def display_name(self) -> str:
        return "🧠 Memory"

    @property
    def description(self) -> str:
        return (
            "Save and recall long-term memories about people, preferences, "
            "facts, events, places, and projects. Connect memories with "
            "relationships to build a personal knowledge graph. Use this to "
            "remember personal details the user shares across conversations "
            "and explore how they relate to each other."
        )

    @property
    def enabled_by_default(self) -> bool:
        return True

    @property
    def required_api_keys(self) -> dict[str, str]:
        return {}

    @property
    def destructive_tool_names(self) -> set[str]:
        return {"delete_memory"}

    def as_langchain_tools(self) -> list:
        # When to save, link, update or explore is in the system prompt's MEMORY GUIDELINES.
        return [
            StructuredTool.from_function(
                func=_save_memory,
                name="save_memory",
                description="Save a long-term memory about the person or their world.",
                args_schema=_SaveMemoryInput,
            ),
            StructuredTool.from_function(
                func=_search_memory,
                name="search_memory",
                description=(
                    "Search memories (semantic, keyword and graph) for a deeper or more focused look than "
                    "the memories recalled each turn."
                ),
                # "as of" is offered only while the stale-skills study's setting is on (off by default).
                args_schema=_SearchMemoryAsOfInput if stale_skills.enabled() else _SearchMemoryInput,
            ),
            StructuredTool.from_function(
                func=_list_memories,
                name="list_memories",
                description="List stored memories, e.g. for 'what do you remember?'.",
                args_schema=_ListMemoriesInput,
            ),
            StructuredTool.from_function(
                func=_update_memory,
                name="update_memory",
                description="Correct or extend an existing memory by its ID.",
                args_schema=_UpdateMemoryInput,
            ),
            StructuredTool.from_function(
                func=_delete_memory,
                name="delete_memory",
                description="Delete a memory by its ID, when the person asks to forget something.",
                args_schema=_DeleteMemoryInput,
            ),
            StructuredTool.from_function(
                func=_link_memories,
                name="link_memories",
                description="Link two memories with a relation in the knowledge graph.",
                args_schema=_LinkMemoriesInput,
            ),
            StructuredTool.from_function(
                func=_explore_connections,
                name="explore_connections",
                description=(
                    "Show how a memory connects to others: its relations, nearby entities and a Mermaid "
                    "graph."
                ),
                args_schema=_ExploreConnectionsInput,
            ),
        ]

    def execute(self, query: str) -> str:
        return "Use save_memory, search_memory, list_memories, update_memory, delete_memory, link_memories, or explore_connections instead."


registry.register(MemoryTool())
