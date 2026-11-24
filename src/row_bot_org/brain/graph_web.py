"""The company knowledge graph's routes (``row_bot_org.brain``, migration 0009).

For anyone signed in (a browser session or a device's bearer), always as a member (never an admin's powers):

- ``GET /api/brain/graph?spaces=<id>,<id>&q=&limit=&retired=``: the entities and relations they may read, the most
  related first (``limit``, 300 by default), ``q`` keeping those whose name or an alias holds it;
- ``GET /api/brain/entities/{id}``: one entity, its relations, and where it was found, as citations.

For a space's owners, and admins, in a browser session only (a bearer gets 403 ``browser_session_required``):

- ``PATCH /api/brain/entities/{id}``: its ``subject``, ``entity_type``, ``description`` and ``aliases``;
- ``POST /api/brain/entities/{id}/merge`` (``{"into"}``), ``.../retire`` and ``.../restore``;
- ``POST /api/brain/spaces/{id}/relations`` (``{"source_id", "target_id", "relation_type"}``), and
  ``POST /api/brain/relations/{id}/retire`` and ``.../restore``.

Anything the caller can't read answers 404 exactly as what doesn't exist. Each change is audited as
``graph.changed`` in its own transaction (ids and the kind of change only).
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import Field, field_validator

from row_bot.audit import Action, Target, TargetKind
from row_bot.audit.vocabulary import ENTITY_FIELDS
from row_bot_org import audit
from row_bot_org.brain import graph, graph_store, store
from row_bot_org.brain.service import excerpt
from row_bot_org.brain.web import (
    MAX_LIST, Caller, _actor, _Body, _error, _layer, _managed, _plain, _uuid, manager_connection,
)
from row_bot_org.identity.web import principal_connection

DEFAULT_NODES = 300
MAX_NODES = 1000


def _subject(value: str) -> str:
    name = graph.clean_name(value)
    if name is None:
        raise ValueError(f"must be 1 to {graph.MAX_NAME} characters, with no control characters")
    return name


def _type(value: str) -> str:
    if value not in graph.ENTITY_TYPES:
        raise ValueError("must be one of: " + ", ".join(graph.ENTITY_TYPES))
    return value


class EntityChange(_Body):
    subject: str | None = None
    entity_type: str | None = None
    description: str | None = None
    # The whole list: what isn't in it is removed.
    aliases: list[str] | None = Field(None, max_length=graph.MAX_ALIASES)

    @field_validator("subject")
    @classmethod
    def _check_subject(cls, value: str | None) -> str | None:
        return None if value is None else _subject(value)

    @field_validator("entity_type")
    @classmethod
    def _check_type(cls, value: str | None) -> str | None:
        return None if value is None else _type(value)

    @field_validator("description")
    @classmethod
    def _check_description(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None if value is None else ""
        return _plain(value, longest=graph.MAX_DESCRIPTION, lines=True)

    @field_validator("aliases")
    @classmethod
    def _check_aliases(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else [_subject(alias) for alias in value]


class MergeIn(_Body):
    into: uuid.UUID


class RelationIn(_Body):
    source_id: uuid.UUID
    target_id: uuid.UUID
    relation_type: str

    @field_validator("relation_type")
    @classmethod
    def _check_relation_type(cls, value: str) -> str:
        if value not in graph.RELATION_TYPES:
            raise ValueError("must be one of: " + ", ".join(graph.RELATION_TYPES))
        return value


def _citation(mention: graph_store.MentionRow, space: dict[str, str], subject: str) -> dict[str, Any]:
    if mention.document is not None:
        return {"type": "document", "space": space, "document": mention.document, "chunk": mention.chunk_ordinal,
                "excerpt": excerpt(mention.chunk_text or "", near=subject)}
    return {"type": "fact", "space": space, "fact": mention.fact, "document": mention.fact_document}


def graph_router() -> APIRouter:
    router = APIRouter()
    manager = Depends(manager_connection, scope="function")
    member = Depends(principal_connection, scope="function")

    def changed(caller: Caller, space: store.SpaceRow, kind: TargetKind, ref: str, change: str,
                **details: str) -> None:
        audit.emit(caller.conn, Action.GRAPH_CHANGED, actor=_actor(caller), layer=_layer(space),
                   target=Target(kind, ref), device_id=caller.principal.device_id,
                   details={"change": change, "space_id": space.id, **details})

    def curated_entity(caller: Caller, entity_id: str) -> tuple[graph_store.EntityRow, store.SpaceRow] | JSONResponse:
        """The entity (locked) and its space, when the caller manages it; else the refusal."""
        if caller.principal.device_id is not None:
            return _error(403, "browser_session_required")
        found = _uuid(entity_id)
        entity = None if found is None else graph_store.get_entity(caller.conn, found)
        if entity is None:
            return _error(404, "entity_not_found")
        space = _managed(caller, entity.space_id)
        if isinstance(space, JSONResponse):
            return space
        return graph_store.get_entity(caller.conn, entity.id, lock=True), space

    def curated_relation(caller: Caller, relation_id: str) -> tuple[graph_store.RelationRow,
                                                                    store.SpaceRow] | JSONResponse:
        if caller.principal.device_id is not None:
            return _error(403, "browser_session_required")
        found = _uuid(relation_id)
        relation = None if found is None else graph_store.get_relation(caller.conn, found)
        if relation is None:
            return _error(404, "relation_not_found")
        space = _managed(caller, relation.space_id)
        return space if isinstance(space, JSONResponse) else (relation, space)

    # -- reading (members) -----------------------------------------------------------------------

    @router.get("/api/brain/graph")
    def whole_graph(spaces: str | None = None, q: str | None = Query(None, max_length=graph.MAX_NAME),
                    limit: int = Query(DEFAULT_NODES, ge=1, le=MAX_NODES), retired: bool = False,
                    conn: psycopg.Connection = member) -> JSONResponse:
        named = None
        if spaces is not None:
            ids = [_uuid(part.strip()) for part in spaces.split(",") if part.strip()]
            if len(ids) > MAX_LIST:
                return _error(422, "too_many_spaces")
            if None in ids:
                return _error(404, "space_not_found")
            named = sorted(set(ids))
            if len(store.get_spaces(conn, named)) != len(named):
                return _error(404, "space_not_found")
        query = q.strip() if q is not None and q.strip() else None
        found = graph_store.graph_view(conn, spaces=named, query=query, limit=limit, retired=retired)
        shown = store.get_spaces(conn, sorted({node["space_id"] for node in found["nodes"]}))
        found["spaces"] = [{"id": space.id, "name": space.name, "kind": space.kind}
                           for space in sorted(shown.values(), key=lambda s: (s.kind != store.COMPANY,
                                                                              s.name.lower(), s.id))]
        return JSONResponse(found)

    @router.get("/api/brain/entities/{entity_id}")
    def entity(entity_id: str, conn: psycopg.Connection = member) -> JSONResponse:
        found = _uuid(entity_id)
        row = None if found is None else graph_store.get_entity(conn, found)
        space = None if row is None else store.get_space(conn, row.space_id)
        if row is None or space is None:
            return _error(404, "entity_not_found")
        about = {"id": space.id, "name": space.name}
        mentions, count = graph_store.entity_mentions(conn, row)
        return JSONResponse({"entity": row.to_json(), "space": {**about, "kind": space.kind},
                             "relations": graph_store.entity_relations(conn, row),
                             "mentions": [_citation(mention, about, row.name) for mention in mentions],
                             "mention_count": count})

    # -- curation (owners and admins, in a browser session) --------------------------------------

    @router.patch("/api/brain/entities/{entity_id}")
    def change_entity(entity_id: str, body: EntityChange, caller: Caller = manager) -> JSONResponse:
        found = curated_entity(caller, entity_id)
        if isinstance(found, JSONResponse):
            return found
        current, space = found
        if current.status == graph_store.MERGED:
            return _error(409, "entity_merged", merged_into=current.merged_into)
        name = body.subject if body.subject is not None else current.name
        kind = body.entity_type if body.entity_type is not None else current.entity_type
        description = body.description if body.description is not None else current.description
        # A rename keeps the old name as an alias (extraction finds the entity by it), unless the aliases are set.
        aliases = graph.merge_aliases(name, body.aliases) if body.aliases is not None else graph.merge_aliases(
            name, list(current.aliases), [current.name])
        same = graph_store.entity_by_name(caller.conn, space.id, name, kind)
        if same is not None and same.id != current.id:
            return _error(409, "entity_exists", entity_id=same.id)
        fields = [field for field, before, after in (
            ("subject", current.name, name), ("entity_type", current.entity_type, kind),
            ("description", current.description, description), ("aliases", list(current.aliases), aliases))
            if before != after]
        if not fields:
            return JSONResponse(current.to_json())
        try:
            with caller.conn.transaction():
                updated = graph_store.update_entity(caller.conn, current.id, name=name, entity_type=kind,
                                                    description=description, aliases=aliases,
                                                    description_written=body.description is not None,
                                                    aliases_written=body.aliases is not None)
        except psycopg.errors.UniqueViolation:
            return _error(409, "entity_exists")
        changed(caller, space, TargetKind.ENTITY, updated.id, "updated",
                fields=",".join(field for field in ENTITY_FIELDS if field in fields))
        return JSONResponse(updated.to_json())

    @router.post("/api/brain/entities/{entity_id}/merge")
    def merge(entity_id: str, body: MergeIn, caller: Caller = manager) -> JSONResponse:
        found = curated_entity(caller, entity_id)
        if isinstance(found, JSONResponse):
            return found
        source, space = found
        if source.status == graph_store.MERGED:
            return _error(409, "entity_merged", merged_into=source.merged_into)
        target = graph_store.get_entity(caller.conn, str(body.into), lock=True)
        # Another space's entity, or one the caller can't read, is unknown here exactly as a missing one.
        if (target is None or target.space_id != space.id or target.id == source.id
                or target.status == graph_store.MERGED):
            return _error(422, "unknown_entity")
        merged = graph_store.merge_entity(caller.conn, source, target)
        changed(caller, space, TargetKind.ENTITY, source.id, "merged", merged_into=merged.id)
        return JSONResponse(merged.to_json())

    def entity_status(entity_id: str, caller: Caller, status: str, change: str) -> JSONResponse:
        found = curated_entity(caller, entity_id)
        if isinstance(found, JSONResponse):
            return found
        current, space = found
        if current.status == graph_store.MERGED:
            return _error(409, "entity_merged", merged_into=current.merged_into)
        row, done = graph_store.set_entity_status(caller.conn, current.id, status)
        if done:
            changed(caller, space, TargetKind.ENTITY, row.id, change)
        return JSONResponse(row.to_json())

    @router.post("/api/brain/entities/{entity_id}/retire")
    def retire_entity(entity_id: str, caller: Caller = manager) -> JSONResponse:
        return entity_status(entity_id, caller, graph_store.RETIRED, "retired")

    @router.post("/api/brain/entities/{entity_id}/restore")
    def restore_entity(entity_id: str, caller: Caller = manager) -> JSONResponse:
        return entity_status(entity_id, caller, graph_store.ACTIVE, "restored")

    @router.post("/api/brain/spaces/{space_id}/relations")
    def add_relation(space_id: str, body: RelationIn, caller: Caller = manager) -> JSONResponse:
        if caller.principal.device_id is not None:
            return _error(403, "browser_session_required")
        space = _managed(caller, space_id)
        if isinstance(space, JSONResponse):
            return space
        ends = [graph_store.get_entity(caller.conn, str(end)) for end in (body.source_id, body.target_id)]
        if body.source_id == body.target_id or any(
                end is None or end.space_id != space.id or end.status == graph_store.MERGED for end in ends):
            return _error(422, "unknown_entity")
        relation, added = graph_store.add_relation(caller.conn, space_id=space.id, source_id=ends[0].id,
                                                   target_id=ends[1].id, relation_type=body.relation_type)
        if not added:
            return _error(409, "relation_exists", relation_id=relation.id)
        changed(caller, space, TargetKind.RELATION, relation.id, "created")
        return JSONResponse(relation.to_json(), status_code=201)

    def relation_status(relation_id: str, caller: Caller, status: str, change: str) -> JSONResponse:
        found = curated_relation(caller, relation_id)
        if isinstance(found, JSONResponse):
            return found
        current, space = found
        row, done = graph_store.set_relation_status(caller.conn, current.id, status)
        if done:
            changed(caller, space, TargetKind.RELATION, row.id, change)
        return JSONResponse(row.to_json())

    @router.post("/api/brain/relations/{relation_id}/retire")
    def retire_relation(relation_id: str, caller: Caller = manager) -> JSONResponse:
        return relation_status(relation_id, caller, graph_store.RETIRED, "retired")

    @router.post("/api/brain/relations/{relation_id}/restore")
    def restore_relation(relation_id: str, caller: Caller = manager) -> JSONResponse:
        return relation_status(relation_id, caller, graph_store.ACTIVE, "restored")

    return router


__all__ = ["DEFAULT_NODES", "MAX_NODES", "EntityChange", "MergeIn", "RelationIn", "graph_router"]
