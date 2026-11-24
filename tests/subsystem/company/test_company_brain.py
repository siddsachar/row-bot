"""The company's knowledge graph in Knowledge home (B8): read with the device's bearer from the company's server
(B1a's spaces, B7's graph and entities), filtered by it to what the person may read, and drawn as the personal
graph; an entity's connections and its mentions as citations; with no company joined nothing is asked, and a
disconnected, locked-down or unreachable company says so. The server is a fake (``httpx.MockTransport``)."""

from __future__ import annotations

import json

import pytest

from row_bot.api.v1 import schemas as dto
from row_bot.company import brain, policy
from tests.fixtures.company_server import (
    ADA,
    ATLAS,
    COMPANY,
    COMPANY_SPACE,
    OLD,
    ORION,
    SALES_SPACE,
)

pytestmark = [pytest.mark.subsystem]

GRAPH, SPACES = "/api/brain/graph", "/api/brain/spaces"
UNKNOWN = "e0000000-0000-4000-8000-0000000000ff"


def _wire(model, value) -> None:
    """``value`` passes the wire record, as the client API sends it."""
    model.model_validate_json(json.dumps(value))


def _asked(server, since: int) -> list:
    return server.requests[since:]


def _paths(requests) -> list[str]:
    return [request.url.path for request in requests]


def _with_bearer_only(requests) -> None:
    for request in requests:
        assert request.headers["authorization"].startswith("Bearer rboa_") and "cookie" not in request.headers


def _ids(view: dict) -> list[str]:
    return [node["id"] for node in view["nodes"]]


def test_with_no_company_there_is_no_graph_or_entity_and_nothing_is_asked(company_server):
    assert brain.graph() is None
    assert brain.graph([SALES_SPACE], "atlas", 10) is None
    assert brain.entity(ATLAS) is None
    assert company_server.requests == []


def test_the_graph_is_read_with_the_bearer_and_comes_as_the_personal_graphs_nodes_and_edges(company_server):
    company_server.join_here()
    since = len(company_server.requests)

    view = brain.graph()

    asked = _asked(company_server, since)
    assert _paths(asked) == [SPACES, GRAPH]
    _with_bearer_only(asked)
    assert dict(asked[1].url.params) == {"limit": "300"}  # Every space the person reads.
    assert (view["state"], view["company"], view["reason"]) == ("ready", COMPANY, None)
    assert view["spaces"] == [
        {"id": COMPANY_SPACE, "name": "Company", "kind": "company", "is_owner": False},
        {"id": SALES_SPACE, "name": "Sales", "kind": "group", "is_owner": True},
    ]
    assert view["space_ids"] == []
    assert _ids(view) == [ATLAS, ADA, ORION]  # The most connected first, as the server ranks them.
    assert {(edge["source_id"], edge["target_id"], edge["relation_type"]) for edge in view["edges"]} == {
        (ADA, ATLAS, "leads"), (ORION, ATLAS, "uses")}
    assert (view["total_entities"], view["total_relations"], view["truncated"]) == (3, 2, False)
    assert view["entity_types"] == ["customer", "person", "project"]
    for node in view["nodes"]:
        _wire(dto.KnowledgeGraphNode, node)  # Exactly what Knowledge home's canvas draws.
    _wire(dto.CompanyGraphView, view)


def test_the_space_picker_search_and_limit_reach_the_server_and_a_space_no_longer_readable_is_left_out(
        company_server):
    company_server.join_here()

    since = len(company_server.requests)
    sales = brain.graph([SALES_SPACE, "not-a-space", UNKNOWN.replace("e", "5")], "  orion  ", 5000)
    (asked,) = [request for request in _asked(company_server, since) if request.url.path == GRAPH]
    assert dict(asked.url.params) == {"spaces": SALES_SPACE, "q": "orion", "limit": "1000"}
    assert (_ids(sales), sales["space_ids"], sales["edges"]) == ([ORION], [SALES_SPACE], [])

    # Every space asked for is gone: the server is asked for every space the person reads, and says so.
    company_server.brain_spaces = [space for space in company_server.brain_spaces if space["id"] != SALES_SPACE]
    since = len(company_server.requests)
    left = brain.graph([SALES_SPACE])
    (asked,) = [request for request in _asked(company_server, since) if request.url.path == GRAPH]
    assert "spaces" not in asked.url.params
    assert (_ids(left), left["space_ids"]) == ([ATLAS, ADA], [])

    nothing = brain.graph(q="nobody by this name")
    assert (nothing["state"], nothing["nodes"], nothing["total_entities"]) == ("ready", [], 0)


def test_a_graph_larger_than_the_limit_says_how_much_is_shown(company_server):
    company_server.join_here()

    view = brain.graph(limit=1)

    assert _ids(view) == [ATLAS]
    assert (view["shown_entities"], view["total_entities"], view["truncated"]) == (1, 3, True)
    assert (view["shown_relations"], view["total_relations"]) == (0, 2)


def test_a_server_without_the_company_brain_or_with_no_documents_has_nothing_to_show(company_server):
    company_server.join_here()
    company_server.brain_nodes, company_server.brain_edges = [], []
    empty = brain.graph()
    assert (empty["state"], empty["nodes"], empty["total_entities"], len(empty["spaces"])) == ("ready", [], 0, 2)

    company_server.brain_spaces = None  # Every brain route 404s.
    since = len(company_server.requests)
    without = brain.graph()
    assert _paths(_asked(company_server, since)) == [SPACES]
    assert (without["state"], without["nodes"], without["spaces"]) == ("ready", [], [])


def test_to_canvas_maps_the_servers_nodes_and_edges_onto_the_personal_graphs():
    node = {"id": ATLAS.upper(), "space_id": COMPANY_SPACE, "subject": "Atlas", "description": "The  billing\nrebuild.",
            "entity_type": "project", "aliases": ["Project Atlas"], "status": "active",
            "updated_at": "2026-11-20T09:00:00+00:00", "relation_count": 2, "orphan": False}
    data = {
        "nodes": [node, {**node, "id": ADA, "subject": "Ada", "entity_type": "person", "status": "retired",
                         "relation_count": 0, "orphan": True},
                  {**node, "id": "not-a-uuid"}, {**node, "id": ORION, "subject": ""}, "not a node"],
        "edges": [{"id": "f0000000-0000-4000-8000-000000000001", "source_id": ADA, "target_id": ATLAS,
                   "relation_type": "leads", "updated_at": "2026-11-21T09:00:00+00:00", "status": "active"},
                  {"id": "f0000000-0000-4000-8000-000000000002", "source_id": ORION, "target_id": ATLAS,
                   "relation_type": "uses", "updated_at": "2026-11-21T09:00:00+00:00"},
                  {"id": "f0000000-0000-4000-8000-000000000003", "source_id": ATLAS, "target_id": ATLAS,
                   "relation_type": "uses", "updated_at": "2026-11-21T09:00:00+00:00"}],
        "total_entities": 9, "total_relations": 4,
    }

    canvas = brain.to_canvas(data)

    atlas, ada = canvas["nodes"]
    assert atlas == {
        "id": ATLAS, "revision": atlas["revision"], "subject": "Atlas", "description": "The billing rebuild.",
        "entity_type": "project", "source": "document", "updated_at": "2026-11-20T09:00:00+00:00",
        "relation_count": 2, "orphan": False, "is_user": False, "status": "active", "tier": "resource",
        "source_label": "",
    }
    assert (ada["status"], ada["orphan"], ada["relation_count"]) == ("archived", True, 0)  # Retired.
    assert canvas["edges"] == [{"id": "f0000000-0000-4000-8000-000000000001", "source_id": ADA, "target_id": ATLAS,
                                "relation_type": "leads", "updated_at": "2026-11-21T09:00:00+00:00"}]
    assert {key: canvas[key] for key in ("total_entities", "total_relations", "shown_entities", "shown_relations",
                                         "truncated", "entity_types")} == {
        "total_entities": 9, "total_relations": 4, "shown_entities": 2, "shown_relations": 1, "truncated": True,
        "entity_types": ["person", "project"]}
    for item in canvas["nodes"]:
        _wire(dto.KnowledgeGraphNode, item)
    for item in canvas["edges"]:
        _wire(dto.KnowledgeGraphEdge, item)
    # The revision follows the entity: a change gives another one.
    changed = brain.to_canvas({"nodes": [{**node, "updated_at": "2026-11-23T09:00:00+00:00"}]})["nodes"][0]
    assert changed["revision"] != atlas["revision"] and len(changed["revision"]) == 64
    assert brain.to_canvas(None)["nodes"] == [] and brain.to_canvas({"nodes": "x"})["truncated"] is False


def _atlas_answer() -> dict:
    """B7's ``GET /api/brain/entities/{id}`` for Atlas."""
    peer = {"entity_type": "person", "status": "active"}
    return {
        "entity": {"id": ATLAS, "space_id": COMPANY_SPACE, "subject": "Atlas", "entity_type": "project",
                   "description": "The billing platform rebuild.", "aliases": ["Project Atlas"], "status": "active",
                   "merged_into": None, "edited_by": "00000000-0000-4000-8000-000000000009",
                   "edited_at": "2026-11-22T09:00:00+00:00", "created_at": "2026-11-01T09:00:00+00:00",
                   "updated_at": "2026-11-22T09:00:00+00:00"},
        "space": {"id": COMPANY_SPACE, "name": "Company", "kind": "company"},
        "relations": [
            {"id": "f1", "relation_type": "leads", "direction": "in", "status": "active", "origin": "extracted",
             "peer": {**peer, "id": ADA, "subject": "Ada Lovelace"}, "updated_at": "2026-11-20T09:00:00+00:00"},
            {"id": "f2", "relation_type": "uses", "direction": "in", "status": "active", "origin": "manual",
             "peer": {**peer, "id": ORION, "subject": "Orion Foods", "entity_type": "customer"},
             "updated_at": "2026-11-21T09:00:00+00:00"},
            # A retired relation, and one to a retired entity: an owner's curation, not shown to readers.
            {"id": "f3", "relation_type": "replaces", "direction": "out", "status": "retired", "origin": "extracted",
             "peer": {**peer, "id": ADA, "subject": "Ada Lovelace"}, "updated_at": "2026-11-21T09:00:00+00:00"},
            {"id": "f4", "relation_type": "replaces", "direction": "out", "status": "active", "origin": "extracted",
             "peer": {**peer, "id": OLD, "subject": "Old billing", "status": "retired"},
             "updated_at": "2026-11-21T09:00:00+00:00"},
        ],
        "mentions": [
            {"type": "document", "space": {"id": COMPANY_SPACE, "name": "Company"},
             "document": {"id": "d1", "title": "Platform plan 2027", "file_name": "plan.pdf"}, "chunk": 2,
             "excerpt": "Atlas replaces the old billing system by June."},
            {"type": "fact", "space": {"id": SALES_SPACE, "name": "Sales"},
             "fact": {"id": "x1", "text": "Orion Foods runs its invoicing on Atlas."}, "document": None},
            {"type": "document", "space": {"id": COMPANY_SPACE, "name": "Company"},
             "document": {"id": "d2", "title": "", "file_name": "kickoff.txt"}, "chunk": 0,
             "excerpt": "Atlas   kickoff\nnotes"},
        ],
        "mention_count": 7,
    }


def test_an_entity_shows_its_connections_and_where_it_was_mentioned_as_citations(company_server):
    company_server.join_here()
    company_server.brain_entities[ATLAS] = _atlas_answer()
    since = len(company_server.requests)

    view = brain.entity(ATLAS)

    asked = _asked(company_server, since)
    assert _paths(asked) == [f"/api/brain/entities/{ATLAS}"]
    _with_bearer_only(asked)
    assert view == {
        "schema_version": 1, "state": "ready", "company": COMPANY, "reason": None,
        "entity": {"id": ATLAS, "subject": "Atlas", "entity_type": "project",
                   "description": "The billing platform rebuild.", "aliases": ["Project Atlas"], "status": "active",
                   "space_id": COMPANY_SPACE, "space_name": "Company", "updated_at": "2026-11-22T09:00:00+00:00",
                   "edited_at": "2026-11-22T09:00:00+00:00"},
        "relations": [
            {"relation_type": "leads", "direction": "incoming", "peer_id": ADA, "peer_subject": "Ada Lovelace",
             "peer_type": "person"},
            {"relation_type": "uses", "direction": "incoming", "peer_id": ORION, "peer_subject": "Orion Foods",
             "peer_type": "customer"},
        ],
        "mentions": [
            {"kind": "document", "space": "Company", "document": "Platform plan 2027",
             "excerpt": "Atlas replaces the old billing system by June."},
            {"kind": "fact", "space": "Sales", "document": None, "excerpt": "Orion Foods runs its invoicing on Atlas."},
            {"kind": "document", "space": "Company", "document": "kickoff.txt", "excerpt": "Atlas kickoff notes"},
        ],
        "mention_count": 7,
    }
    _wire(dto.CompanyEntityView, view)


def test_a_merged_entity_leads_to_the_one_it_was_merged_into_and_an_unknown_one_is_not_found(company_server):
    company_server.join_here()
    company_server.brain_entities[ATLAS] = _atlas_answer()
    merged = _atlas_answer()
    merged["entity"].update(id=OLD, subject="Old billing", status="merged_into", merged_into=ATLAS)
    merged.update(relations=[], mentions=[], mention_count=0)
    company_server.brain_entities[OLD] = merged

    followed = brain.entity(OLD)
    assert (followed["state"], followed["entity"]["id"], followed["entity"]["subject"]) == ("ready", ATLAS, "Atlas")

    assert brain.entity(UNKNOWN) == brain.empty_entity("not_found", COMPANY)
    since = len(company_server.requests)
    assert brain.entity("../../api/me")["state"] == "not_found"  # Not an id: nothing is asked.
    assert _asked(company_server, since) == []


def test_a_revoked_row_bot_reads_disconnected_and_then_asks_nothing(company_server):
    record = company_server.join_here()
    company_server.revoke(record.device_id)

    view = brain.graph()

    assert (view["state"], view["company"], view["nodes"]) == ("disconnected", COMPANY, [])
    since = len(company_server.requests)
    assert brain.graph()["state"] == "disconnected"
    assert brain.entity(ATLAS)["state"] == "disconnected"
    assert _asked(company_server, since) == []


def test_locked_down_asks_nothing_and_says_why(company_server):
    company_server.policy["offline"]["max_days"] = 0
    company_server.join_here()
    company_server.offline = True
    assert policy.refresh().state == "locked_down"  # A failed refresh with no offline days.
    company_server.offline = False
    since = len(company_server.requests)

    view = brain.graph()
    detail = brain.entity(ATLAS)

    assert (view["state"], view["reason"], view["nodes"]) == ("locked_down", "lease_expired", [])
    assert (detail["state"], detail["reason"], detail["entity"]) == ("locked_down", "lease_expired", None)
    assert _asked(company_server, since) == []
    _wire(dto.CompanyGraphView, view)


def test_an_unreachable_server_keeps_the_row_bot_joined_and_says_so(company_server):
    company_server.join_here()
    company_server.offline = True

    view = brain.graph()
    detail = brain.entity(ATLAS)

    assert (view["state"], view["reason"], view["company"]) == ("unreachable", None, COMPANY)
    assert detail["state"] == "unreachable"
    company_server.offline = False
    assert brain.graph()["state"] == "ready"  # Retry.
