"""The company's knowledge graph, read in Knowledge home (Row-Bot for Teams, B8).

- The Company Server's ``GET /api/brain/spaces`` (the spaces the person may read, and which they own) and
  ``GET /api/brain/graph`` and ``GET /api/brain/entities/{id}`` (B7), with the device's bearer through the C1b
  client. The server filters everything to what the person may read; the app shows it as it comes, read only.
  Owners curate in the admin console.
- Not joined: None, and nothing is asked. Disconnected, locked down (C3a) or unreachable: a state Knowledge home
  puts into words. Disconnected and locked down ask nothing either.
- Nothing is kept: each read asks the server.
- ``to_canvas`` maps the server's nodes and edges onto the personal graph's (``KnowledgeGraphNode`` and
  ``KnowledgeGraphEdge``), so Knowledge home's canvas draws the company's graph as it draws the person's.

Nothing here imports the HTTP client until a company is joined.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import Callable, Sequence
from typing import Any
from urllib.parse import urlencode

from row_bot.company import policy, store

logger = logging.getLogger(__name__)

SPACES_PATH = "/api/brain/spaces"
GRAPH_PATH = "/api/brain/graph"
ENTITY_PATH = "/api/brain/entities/"
DEFAULT_LIMIT = 300
MAX_LIMIT = 1000
MAX_QUERY = 160
MAX_SPACES = 64
MAX_LISTED_SPACES = 512
MAX_RELATIONS = 200
MAX_MENTIONS = 50
# A merged entity answers with the one it was merged into; this many merges are followed at most.
MAX_MERGES = 4
SPACE_KINDS = frozenset({"company", "group"})


def _id(value: Any) -> str | None:
    """A UUID in its canonical form, or None."""
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


def _text(value: Any, limit: int) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def _count(value: Any) -> int:
    return value if type(value) is int and value >= 0 else 0


def _items(data: Any, key: str) -> list[Any]:
    items = data.get(key) if isinstance(data, dict) else None
    return items if isinstance(items, list) else []


def _revision(*parts: str) -> str:
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def to_canvas(data: Any) -> dict[str, Any]:
    """The server's graph (B7) as the personal graph's nodes, edges and counts.

    - A node: its id, name (``subject``), description, type, when it changed, relation count and whether it is
      unconnected, as the server gave them. The personal graph's own fields read the same for every company
      entity: from a document, never the person's hub. A retired entity reads as archived.
      ``revision`` changes when the entity does.
    - An edge: its id, ends, label and when it changed; only between two of the nodes.
    - Anything malformed is left out."""
    nodes: list[dict[str, Any]] = []
    for item in _items(data, "nodes"):
        if not isinstance(item, dict):
            continue
        node_id, subject = _id(item.get("id")), _text(item.get("subject"), 256)
        if node_id is None or not subject:
            continue
        status = "archived" if item.get("status") == "retired" else "active"
        updated_at = _text(item.get("updated_at"), 128)
        relations = _count(item.get("relation_count"))
        nodes.append({
            "id": node_id, "revision": _revision(node_id, updated_at, status, str(relations)), "subject": subject,
            "description": _text(item.get("description"), 1024),
            "entity_type": _text(item.get("entity_type"), 64) or "term", "source": "document",
            "updated_at": updated_at, "relation_count": relations, "orphan": item.get("orphan") is True,
            "is_user": False, "status": status, "tier": "resource", "source_label": "",
        })
        if len(nodes) == MAX_LIMIT:
            break
    shown = {node["id"] for node in nodes}
    edges: list[dict[str, Any]] = []
    for item in _items(data, "edges"):
        if not isinstance(item, dict):
            continue
        edge_id, source, target = _id(item.get("id")), _id(item.get("source_id")), _id(item.get("target_id"))
        if edge_id is None or source not in shown or target not in shown or source == target:
            continue
        edges.append({"id": edge_id, "source_id": source, "target_id": target,
                      "relation_type": _text(item.get("relation_type"), 64) or "related_to",
                      "updated_at": _text(item.get("updated_at"), 128)})
        if len(edges) == 3 * MAX_LIMIT:
            break
    total_entities = max(_count(data.get("total_entities")) if isinstance(data, dict) else 0, len(nodes))
    total_relations = max(_count(data.get("total_relations")) if isinstance(data, dict) else 0, len(edges))
    return {
        "nodes": nodes, "edges": edges,
        "total_entities": total_entities, "total_relations": total_relations,
        "shown_entities": len(nodes), "shown_relations": len(edges),
        "truncated": len(nodes) < total_entities or len(edges) < total_relations,
        "entity_types": sorted({node["entity_type"] for node in nodes})[:64],
    }


def _spaces(data: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for item in _items(data, "spaces"):
        if not isinstance(item, dict):
            continue
        space_id, name = _id(item.get("id")), _text(item.get("name"), 160)
        if space_id is None or not name or item.get("kind") not in SPACE_KINDS:
            continue
        found.append({"id": space_id, "name": name, "kind": item["kind"], "is_owner": item.get("is_owner") is True})
        if len(found) == MAX_LISTED_SPACES:
            break
    return found


def empty_graph(state: str, company: str | None = None, reason: str | None = None) -> dict[str, Any]:
    """A graph view with nothing to draw: not joined, or why the company can't be read now."""
    return {"schema_version": 1, "state": state, "company": company, "reason": reason, "spaces": [],
            "space_ids": [], **to_canvas(None)}


def empty_entity(state: str, company: str | None = None, reason: str | None = None) -> dict[str, Any]:
    """An entity view with no entity: not joined, not found, or why the company can't be read now."""
    return {"schema_version": 1, "state": state, "company": company, "reason": reason, "entity": None,
            "relations": [], "mentions": [], "mention_count": 0}


def _blocked(record: store.Record) -> tuple[str, str | None] | None:
    """Why the company isn't asked now (state, reason), or None when it may be."""
    if record.disconnected:
        return "disconnected", None
    status = policy.state()
    if status.state != "locked_down":
        return None
    if status.reason == "disconnected":
        return "disconnected", None
    return "locked_down", status.reason


def _read(record: store.Record, empty: Callable[..., dict[str, Any]],
          ask: Callable[[str], dict[str, Any]]) -> dict[str, Any]:
    """``ask(origin)`` unless the company can't be asked now; the client's failures as states."""
    blocked = _blocked(record)
    if blocked is not None:
        return empty(blocked[0], record.company, blocked[1])
    from row_bot.company import client

    try:
        return ask(record.origin)
    except client.Disconnected:
        return empty("disconnected", record.company)
    except client.Busy:
        return empty("unreachable", record.company, "busy")
    except client.Unreachable as error:
        logger.info("The company's knowledge couldn't be read (%s)", type(error).__name__)
        return empty("unreachable", record.company)
    except store.StorageUnavailable:
        return empty("unreachable", record.company, "storage_unavailable")


def _get(origin: str, path: str) -> dict[str, Any] | None:
    """The server's answer to a GET, or None for a 404 (not found, or a server without the company brain).
    Raises the client's Unreachable for any other answer that isn't 200."""
    from row_bot.company import client

    response = client.call(origin, "GET", path)
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise client.Unreachable(str(response.status_code))
    try:
        data = response.json()
    except ValueError:
        raise client.Unreachable("not json") from None
    if not isinstance(data, dict):
        raise client.Unreachable("not an object")
    return data


def graph(spaces: Sequence[str] = (), q: str = "", limit: int = DEFAULT_LIMIT) -> dict[str, Any] | None:
    """The company's graph in the spaces asked for (none: every space the person may read), whose entities'
    names or aliases contain ``q``, the ``limit`` most connected; None when no company is joined.

    A space the person may no longer read is left out (``space_ids`` says which are shown). A server without the
    company brain has nothing to show."""
    record = store.read_record()
    if record is None:
        return None
    wanted = [found for found in (_id(space) for space in spaces[:MAX_SPACES]) if found]
    needle = _text(q, MAX_QUERY)
    count = min(max(int(limit), 1), MAX_LIMIT)

    def ask(origin: str) -> dict[str, Any]:
        readable = _spaces(_get(origin, SPACES_PATH))
        ids = {space["id"] for space in readable}
        chosen = [space for space in dict.fromkeys(wanted) if space in ids]
        query = {"limit": str(count)}
        if chosen:
            query["spaces"] = ",".join(chosen)
        if needle:
            query["q"] = needle
        answer = _get(origin, f"{GRAPH_PATH}?{urlencode(query)}") if readable else None
        return {"schema_version": 1, "state": "ready", "company": record.company, "reason": None,
                "spaces": readable, "space_ids": chosen, **to_canvas(answer)}

    return _read(record, empty_graph, ask)


def _relation(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict) or item.get("status") != "active":
        return None
    peer = item.get("peer") if isinstance(item.get("peer"), dict) else {}
    peer_id, subject = _id(peer.get("id")), _text(peer.get("subject"), 256)
    if peer_id is None or not subject or peer.get("status") != "active" or item.get("direction") not in ("out", "in"):
        return None
    return {"relation_type": _text(item.get("relation_type"), 64) or "related_to",
            "direction": "outgoing" if item["direction"] == "out" else "incoming",
            "peer_id": peer_id, "peer_subject": subject, "peer_type": _text(peer.get("entity_type"), 64)}


def _citation(item: Any) -> dict[str, Any] | None:
    """A mention as a citation: where it was read ("From <space>: <document>") and the words around it."""
    if not isinstance(item, dict) or item.get("type") not in ("document", "fact"):
        return None
    space = item.get("space") if isinstance(item.get("space"), dict) else {}
    document = item.get("document") if isinstance(item.get("document"), dict) else {}
    title = _text(document.get("title"), 300) or _text(document.get("file_name"), 300) or None
    if item["type"] == "document":
        excerpt = _text(item.get("excerpt"), 2000)
    else:
        fact = item.get("fact") if isinstance(item.get("fact"), dict) else {}
        excerpt = _text(fact.get("text"), 2000)
    if not excerpt:
        return None
    return {"kind": item["type"], "space": _text(space.get("name"), 160) or "Company", "document": title,
            "excerpt": excerpt}


def _entity(data: dict[str, Any]) -> dict[str, Any] | None:
    entity = data.get("entity") if isinstance(data.get("entity"), dict) else {}
    space = data.get("space") if isinstance(data.get("space"), dict) else {}
    entity_id, subject, space_id = _id(entity.get("id")), _text(entity.get("subject"), 256), _id(space.get("id"))
    if entity_id is None or not subject or space_id is None:
        return None
    given = entity.get("aliases") if isinstance(entity.get("aliases"), list) else []
    aliases = [_text(alias, 160) for alias in given if _text(alias, 160)]
    return {
        "id": entity_id, "subject": subject, "entity_type": _text(entity.get("entity_type"), 64) or "term",
        "description": _text(entity.get("description"), 1024), "aliases": aliases[:16],
        "status": "archived" if entity.get("status") == "retired" else "active",
        "space_id": space_id, "space_name": _text(space.get("name"), 160) or "Company",
        "updated_at": _text(entity.get("updated_at"), 128), "edited_at": _text(entity.get("edited_at"), 128) or None,
    }


def entity(entity_id: str) -> dict[str, Any] | None:
    """One entity of the company's graph: its description, aliases, connections and where it was mentioned (as
    citations); None when no company is joined. A merged entity is followed to the one it was merged into."""
    record = store.read_record()
    if record is None:
        return None
    first = _id(entity_id)
    if first is None:
        return empty_entity("not_found", record.company)

    def ask(origin: str) -> dict[str, Any]:
        current: str | None = first
        for _ in range(MAX_MERGES + 1):
            data = _get(origin, ENTITY_PATH + current)
            found = _entity(data) if data is not None else None
            if found is None:
                break
            raw = data["entity"]
            if raw.get("status") == "merged_into":
                current = _id(raw.get("merged_into"))
                if current is None:
                    break
                continue
            relations = [row for row in map(_relation, _items(data, "relations")) if row][:MAX_RELATIONS]
            mentions = [row for row in map(_citation, _items(data, "mentions")) if row][:MAX_MENTIONS]
            return {"schema_version": 1, "state": "ready", "company": record.company, "reason": None,
                    "entity": found, "relations": relations, "mentions": mentions,
                    "mention_count": max(_count(data.get("mention_count")), len(mentions))}
        return empty_entity("not_found", record.company)

    return _read(record, empty_entity, ask)
