"""Long-term memory persistence layer — backward-compatible wrapper.

v3.6+: All data is stored in the **knowledge graph** (``knowledge_graph.py``)
as entities + relations.  This module delegates every public function to the
graph layer and maps column names so that callers who expect the old
``memories`` schema (``category``, ``content``) still work seamlessly.

Nothing downstream needs to change — ``memory_extraction.py``,
``tools/memory_tool.py``, ``agent.py``, and ``app.py`` all import
from this module and get the same signatures and return values.

Database lives at ``~/.thoth/memory.db`` (shared with knowledge_graph.py).
FAISS index lives at ``~/.thoth/memory_vectors/``.
"""

from __future__ import annotations

import logging

import knowledge_graph as _kg

logger = logging.getLogger(__name__)

# ── Re-export public constants ───────────────────────────────────────────────
DB_PATH = _kg.DB_PATH
VALID_CATEGORIES = _kg.VALID_CATEGORIES  # superset — still includes person/preference/fact/event/place/project


# ── Column mapping helpers ───────────────────────────────────────────────────
# The old schema had ``category`` and ``content``; the new schema uses
# ``entity_type`` and ``description``.  Every function below maps
# transparently so callers never notice the change.

def _entity_to_memory(entity: dict) -> dict:
    """Map an entity dict back to the legacy memory column names."""
    if not entity:
        return entity
    mem = dict(entity)
    # Add legacy aliases so callers can use either name
    mem["category"] = mem.get("entity_type", mem.get("category", ""))
    mem["content"] = mem.get("description", mem.get("content", ""))
    return mem


def _entities_to_memories(entities: list[dict]) -> list[dict]:
    return [_entity_to_memory(e) for e in entities]


# ── Schema bootstrap (no-op — knowledge_graph.py handles it) ────────────────
def _init_db() -> None:
    pass  # handled by knowledge_graph._init_db()

def _get_conn():
    return _kg._get_conn()


# ── Embedding & FAISS (delegated) ───────────────────────────────────────────

def _get_embedding_model():
    return _kg._get_embedding_model()

def _rebuild_memory_index() -> None:
    _kg.rebuild_index()

def _memory_text(row: dict) -> str:
    return _kg._entity_text(row)


# ── Core public API ─────────────────────────────────────────────────────────

def _normalize_subject(s: str) -> str:
    return _kg._normalize_subject(s)


def save_memory(
    category: str,
    subject: str,
    content: str,
    tags: str = "",
    source: str = "live",
    properties: dict | None = None,
) -> dict:
    """Create a new memory (entity) entry.

    Maps ``category`` → ``entity_type`` and ``content`` → ``description``
    internally, then returns the legacy column names.
    """
    entity = _kg.save_entity(
        entity_type=category,
        subject=subject,
        description=content,
        tags=tags,
        source=source,
        properties=properties,
    )
    return _entity_to_memory(entity)


def update_memory(
    memory_id: str,
    content: str,
    *,
    subject: str | None = None,
    tags: str | None = None,
    category: str | None = None,
    aliases: str | None = None,
    source: str | None = None,
    properties: dict | None = None,
) -> dict | None:
    """Update an existing memory (entity)."""
    entity = _kg.update_entity(
        memory_id,
        description=content,
        subject=subject,
        entity_type=category,
        aliases=aliases,
        tags=tags,
        source=source,
        properties=properties,
    )
    return _entity_to_memory(entity) if entity else None


def delete_memory(memory_id: str) -> bool:
    return _kg.delete_entity(memory_id)


def delete_memories(memory_ids: list[str]) -> tuple[int, list[tuple[str, str]]]:
    """Delete several memories at once.

    Returns ``(deleted_count, failures)``. Ids whose entity was already
    gone (``delete_memory`` returns False) are not counted as failures
    — they're idempotent no-ops.
    """
    deleted = 0
    failures: list[tuple[str, str]] = []
    for mid in memory_ids:
        try:
            if delete_memory(mid):
                deleted += 1
        except Exception as exc:
            failures.append((mid, str(exc)))
    return deleted, failures


def get_memory(memory_id: str) -> dict | None:
    entity = _kg.get_entity(memory_id)
    return _entity_to_memory(entity) if entity else None


def list_memories(category: str | None = None, limit: int = 50) -> list[dict]:
    return _entities_to_memories(_kg.list_entities(entity_type=category, limit=limit))


def count_memories() -> int:
    return _kg.count_entities()


def search_memories(query: str, category: str | None = None, limit: int = 20) -> list[dict]:
    return _entities_to_memories(_kg.search_entities(query, entity_type=category, limit=limit))


def semantic_search(query: str, top_k: int = 5, threshold: float = 0.5) -> list[dict]:
    return _entities_to_memories(_kg.semantic_search(query, top_k=top_k, threshold=threshold))


def find_by_subject(category: str | None, subject: str) -> dict | None:
    entity = _kg.find_by_subject(entity_type=category, subject=subject)
    return _entity_to_memory(entity) if entity else None


def find_duplicate(
    category: str,
    subject: str,
    content: str,
    threshold: float = 0.92,
) -> dict | None:
    entity = _kg.find_duplicate(entity_type=category, subject=subject, description=content, threshold=threshold)
    return _entity_to_memory(entity) if entity else None


def delete_all_memories() -> int:
    return _kg.delete_all_entities()


def consolidate_duplicates(threshold: float = 0.90) -> int:
    return _kg.consolidate_duplicates(threshold=threshold)
