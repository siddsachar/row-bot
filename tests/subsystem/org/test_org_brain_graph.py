"""The company knowledge graph end to end: documents and facts read by the brain's worker through the gateway's
chat model (the fake endpoint of tests/fixtures/model_endpoints.py, answering from a script), metered to the
uploader; strict reading, dedupe and mentions; orphans retired on delete, replace and a retired fact; owners'
curation and its audit; the graph API's bounds and filter; waiting without a chat model or budget, with no loop;
and the worker's limits and stop. Security-sensitive (nobody sees a space's graph they can't read): these run in the
pull request lane's Org job, on a real (throwaway) Postgres as the runtime role.

As in test_org_brain.py, the worker's thread is off: each test runs its passes with ``process_pending``.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from tests.fixtures.postgres import import_psycopg

psycopg = import_psycopg()
pytest.importorskip("joserfc", reason="joserfc is not installed: uv sync --locked --all-extras --group test --group org")

from fastapi.testclient import TestClient  # noqa: E402

from row_bot_org.brain import graph_store  # noqa: E402
from row_bot_org.brain import service as brain_service  # noqa: E402
from row_bot_org.db.rls import principal_transaction  # noqa: E402
from tests.fixtures.model_endpoints import (  # noqa: E402
    INPUT_TOKENS, KEY, OUTPUT_TOKENS, FakeEndpoint, running_endpoint,
)
from tests.fixtures.oidc_idp import FakeIdp, running_idp  # noqa: E402
from tests.helpers.org_audit import stored_events, summary  # noqa: E402
from tests.subsystem.org.test_org_brain import (  # noqa: E402
    admin_of, company_space, me, new_space, org_server, person, process, runtime_pool, superuser_sql, uploaded,
)
from tests.subsystem.org.test_org_device_sign_in import bearer, join, row_bot  # noqa: E402

pytestmark = pytest.mark.postgres

ENTITY_NOT_FOUND = (404, {"error": "entity_not_found"})
SPACE_NOT_FOUND = (404, {"error": "space_not_found"})
# Two parts (the second from character 1,350): Atlas only in the first, Orion only in the second.
TWO_PARTS = ("Atlas is the payments project. " + "Filler text. " * 130 + "Orion is the search project.").encode()
# Three parts: Orion only in the third.
THREE_PARTS = ("Atlas is the payments project. " + "Filler text. " * 250 + "Orion is the search project.").encode()


@pytest.fixture
def idp() -> Iterator[FakeIdp]:
    with running_idp("okta") as running:
        yield running


def entity(category: str, subject: str, content: str = "", **extra: Any) -> dict[str, Any]:
    return {"category": category, "subject": subject, "content": content, **extra}


def relation(label: str, source: str, target: str, confidence: float = 1.0) -> dict[str, Any]:
    return {"relation_type": label, "source_subject": source, "target_subject": target, "confidence": confidence}


def part_text(body: dict) -> str:
    """The text an extraction prompt carries (after ``TEXT:``), not its rules."""
    prompt = body["messages"][-1]["content"]
    return prompt.split("\nTEXT:\n", 1)[1].rsplit("\n\nRespond with ONLY", 1)[0]


def answering(endpoint: FakeEndpoint, script: list[tuple[str, Any]]) -> None:
    """The endpoint's chat answers: for the first ``(marker, answer)`` whose marker the part holds, the answer (a
    string as it is, anything else as JSON); otherwise ``[]``."""
    def answer(body: dict) -> str:
        text = part_text(body)
        for marker, said in script:
            if marker in text:
                return said if isinstance(said, str) else json.dumps(said)
        return "[]"

    endpoint.chat = answer


def chat_calls(endpoint: FakeEndpoint) -> list[str]:
    """The text of each chat call the endpoint had, in order."""
    return [part_text(seen.body) for seen in endpoint.requests if seen.path == "/v1/chat/completions"]


def add_models(admin: TestClient, base_url: str, *, chat: bool = True, **chat_fields: Any) -> str:
    """An endpoint with the company's default embeddings model and (``chat``) default chat model; its id."""
    endpoint = admin.post("/api/admin/endpoints", json={"name": f"models-{uuid.uuid4().hex[:6]}", "api": "openai",
                                                         "base_url": base_url, "api_key": KEY})
    assert endpoint.status_code == 201, endpoint.text
    endpoint_id = endpoint.json()["id"]
    embed = admin.post("/api/admin/models", json={"endpoint_id": endpoint_id, "name": "company-embed",
                                                   "kind": "embeddings", "is_default": True})
    assert embed.status_code == 201, embed.text
    if chat:
        add_chat(admin, endpoint_id, **chat_fields)
    return endpoint_id


def add_chat(admin: TestClient, endpoint_id: str, **fields: Any) -> None:
    model = admin.post("/api/admin/models", json={"endpoint_id": endpoint_id, "name": "company-chat", "kind": "chat",
                                                   "is_default": True, **fields})
    assert model.status_code == 201, model.text


def graph_of(client: TestClient, **params: Any) -> dict:
    response = client.get("/api/brain/graph", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def named(found: dict) -> dict[str, dict]:
    return {node["subject"]: node for node in found["nodes"]}


def edges(found: dict) -> set[tuple[str, str, str]]:
    """The graph's edges as (source subject, label, target subject)."""
    names = {node["id"]: node["subject"] for node in found["nodes"]}
    return {(names[edge["source_id"]], edge["relation_type"], names[edge["target_id"]]) for edge in found["edges"]}


def status_of(client: TestClient, subject: str) -> str | None:
    """An entity's status as the graph shows it with retired ones (None: not there)."""
    node = named(graph_of(client, retired="true")).get(subject)
    return None if node is None else node["status"]


def details(client: TestClient, entity_id: str) -> dict:
    response = client.get(f"/api/brain/entities/{entity_id}")
    assert response.status_code == 200, response.text
    return response.json()


def answered(response) -> tuple[int, Any]:
    return response.status_code, response.json()


def document_graph(client: TestClient, document_id: str) -> tuple[str, str | None]:
    found = client.get(f"/api/brain/documents/{document_id}").json()
    return found["graph_status"], found["graph_reason"]


ATLAS_SCRIPT: list[tuple[str, Any]] = [
    ("Priya leads", [
        entity("person", "Priya", "Leads the Atlas project."),
        entity("project", "Atlas", "The payments project.", aliases="ATL"),
        entity("system", "Postgres", "The ledger's database.", aliases=["PostgreSQL"]),
        entity("fact", "Ledger", "Not a company type: left out."),
        relation("led_by", "Atlas", "Priya"),  # Passive: read as Priya leads Atlas.
        relation("uses", "Atlas", "Postgres"),
        relation("associated_with", "Priya", "Postgres"),  # Vague: related_to.
        relation("works_on", "Priya", "Atlas", 0.5),  # Too unsure.
        relation("owns", "Priya", "Nobody"),  # An end that isn't an entity.
    ]),
    ("ATLAS team", "```json\n" + json.dumps([
        entity("project", "atlas  ", "Payments."), entity("system", "postgres"),
        relation("depends on", "atlas", "postgres")]) + "\n```"),
    ("offsite", "Sure! The entities are Priya and Atlas."),
]


# -- extraction ---------------------------------------------------------------------------------------

def test_documents_are_read_for_the_graph_through_the_chat_model_metered_to_the_uploader_and_deduped(
        org_database, idp) -> None:
    with running_endpoint("openai") as endpoint, org_server(org_database, idp) as server:
        answering(endpoint, ATLAS_SCRIPT)
        admin = admin_of(server, idp)
        add_models(admin, endpoint.base_url, input_price="1")
        priya, sam = person(server, idp, "priya", ["Sales"]), person(server, idp, "sam", ["Sales"])
        priya_id = me(priya)
        sales = new_space(admin, "Sales team", reader_groups=["Sales"], owner_principals=[priya_id])
        first = uploaded(priya, sales, "atlas.md", b"Priya leads the Atlas project. Atlas uses Postgres.")
        notes = uploaded(priya, sales, "atlas-notes.md", b"The ATLAS team depends on Postgres every day.")
        junk = uploaded(priya, sales, "offsite.md", b"Quarterly offsite notes.")
        assert first["graph_status"] == "pending"

        assert process(server) == 3
        assert chat_calls(endpoint) == ["Priya leads the Atlas project. Atlas uses Postgres.",
                                        "The ATLAS team depends on Postgres every day.", "Quarterly offsite notes."]
        prompt = [seen.body for seen in endpoint.requests if seen.path == "/v1/chat/completions"][0]
        assert 'SOURCE: Part 1 of the document "atlas"' in prompt["messages"][0]["content"]
        for document in (first, notes, junk):
            assert document_graph(priya, document["id"]) == ("done", None)

        # One entity per name and type, whatever the case; the vocabulary's labels, passive ones turned round.
        found = graph_of(sam)
        assert [(node["subject"], node["entity_type"], node["aliases"], node["relation_count"])
                for node in found["nodes"]] == [("Atlas", "project", ["ATL"], 3),
                                                 ("Postgres", "system", ["PostgreSQL"], 3),
                                                 ("Priya", "person", [], 2)]
        assert edges(found) == {("Priya", "leads", "Atlas"), ("Atlas", "uses", "Postgres"),
                                ("Priya", "related_to", "Postgres"), ("Atlas", "depends_on", "Postgres")}
        assert (found["total_entities"], found["total_relations"], found["truncated"]) == (3, 4, False)
        assert found["entity_types"] == ["person", "project", "system"]
        assert found["spaces"] == [{"id": sales, "name": "Sales team", "kind": "group"}]

        atlas = details(sam, named(found)["Atlas"]["id"])
        assert {key: atlas["entity"][key] for key in ("subject", "entity_type", "description", "status",
                                                      "edited_by")} == {
            "subject": "Atlas", "entity_type": "project", "description": "The payments project.", "status": "active",
            "edited_by": None}
        assert atlas["mention_count"] == 2
        assert [(m["document"]["file_name"], m["chunk"], m["excerpt"]) for m in atlas["mentions"]] == [
            ("atlas-notes.md", 0, "The ATLAS team depends on Postgres every day."),
            ("atlas.md", 0, "Priya leads the Atlas project. Atlas uses Postgres.")]
        assert atlas["mentions"][0]["space"] == {"id": sales, "name": "Sales team"}
        assert [(r["relation_type"], r["direction"], r["peer"]["subject"]) for r in atlas["relations"]] == [
            ("depends_on", "out", "Postgres"), ("leads", "in", "Priya"), ("uses", "out", "Postgres")]

        # Each part a model call of the uploader's, metered and audited as theirs.
        chat = [row for row in admin.get("/api/admin/usage?by=model").json()["usage"] if row["model"] == "company-chat"]
        assert chat == [{"model": "company-chat", "calls": 3, "input_tokens": 3 * INPUT_TOKENS,
                         "output_tokens": 3 * OUTPUT_TOKENS, "cost": 0.0036}]
        called = [summary(event) for event in stored_events(org_database.owner_url, "model.called")
                  if event["details"]["model"] == "company-chat"]
        assert [(c[1], c[2]) for c in called] == [("person", priya_id)] * 3

        # A pass reads a document once: nothing is asked again.
        assert process(server) == 0
        assert len(chat_calls(endpoint)) == 3


# -- mentions and orphans -----------------------------------------------------------------------------

def test_what_loses_its_last_mention_goes_unless_curated_and_a_deleted_source_leaves_nothing_readable(
        org_database, idp) -> None:
    with running_endpoint("openai") as endpoint, org_server(org_database, idp) as server:
        answering(endpoint, [
            ("Priya leads", [entity("person", "Priya", "Leads the Atlas project."), entity("project", "Atlas"),
                             relation("leads", "Priya", "Atlas")]),
            ("uses Postgres", [entity("project", "Atlas", "The payments project."), entity("system", "Postgres"),
                               relation("uses", "Atlas", "Postgres")]),
            ("Orion", [entity("project", "Orion")]),
        ])
        admin = admin_of(server, idp)
        add_models(admin, endpoint.base_url)
        company = company_space(admin)
        leads = uploaded(admin, company, "leads.md", b"Priya leads the Atlas project.")
        uses = uploaded(admin, company, "uses.md", b"Atlas uses Postgres.")
        process(server)
        assert set(named(graph_of(admin))) == {"Priya", "Atlas", "Postgres"}
        priya = named(graph_of(admin))["Priya"]["id"]

        def replace(text: bytes) -> dict:
            replaced = admin.put(f"/api/brain/documents/{uses['id']}", files={"file": ("uses.md", text)})
            assert replaced.status_code == 200, replaced.text
            return replaced.json()

        # Delete: what only that document mentioned, and nobody curated, is gone; nothing of it stays readable.
        assert admin.delete(f"/api/brain/documents/{leads['id']}").status_code == 204
        assert set(named(graph_of(admin))) == set(named(graph_of(admin, retired="true"))) == {"Atlas", "Postgres"}
        assert edges(graph_of(admin, retired="true")) == {("Atlas", "uses", "Postgres")}
        assert answered(admin.get(f"/api/brain/entities/{priya}")) == ENTITY_NOT_FOUND
        assert superuser_sql(org_database, "SELECT count(*) FROM row_bot_org.brain_entities WHERE name = 'Priya'"
                             " OR strpos(description, 'Atlas project') > 0") == [(0,)]

        # Replace: the old mentions go at once, and the new version is read.
        assert replace(b"Orion is new.")["graph_status"] == "pending"
        assert graph_of(admin, retired="true")["nodes"] == []
        process(server)
        assert set(named(graph_of(admin))) == {"Orion"}
        replace(b"Atlas uses Postgres.")
        process(server)
        assert set(named(graph_of(admin))) == {"Atlas", "Postgres"}

        # Curated entities are retired instead: an owner's rename keeps the entity, but what extraction wrote
        # (its description, its aliases) goes with the source. One an owner retired stays retired, found again.
        atlas, postgres = named(graph_of(admin))["Atlas"]["id"], named(graph_of(admin))["Postgres"]["id"]
        assert admin.patch(f"/api/brain/entities/{atlas}", json={"subject": "Atlas payments"}).status_code == 200
        assert admin.post(f"/api/brain/entities/{postgres}/retire").status_code == 200
        replace(b"Orion is new.")
        kept = named(graph_of(admin, retired="true"))
        assert set(kept) == {"Atlas payments", "Postgres"}
        assert (kept["Atlas payments"]["status"], kept["Atlas payments"]["description"],
                kept["Atlas payments"]["aliases"]) == ("retired", "", [])
        assert graph_of(admin, retired="true")["edges"] == []
        process(server)
        replace(b"Atlas uses Postgres.")
        process(server)
        assert set(named(graph_of(admin))) == {"Atlas"}
        assert (status_of(admin, "Postgres"), details(admin, postgres)["mention_count"]) == ("retired", 1)
        assert status_of(admin, "Atlas payments") == "retired"

        # Facts are read too; retiring one takes what only it mentioned away.
        fact = admin.post(f"/api/brain/spaces/{company}/facts", json={"text": "Priya leads the Atlas project."})
        assert fact.status_code == 201, fact.text
        process(server)
        priya = named(graph_of(admin))["Priya"]["id"]
        assert details(admin, priya)["mentions"] == [
            {"type": "fact", "space": {"id": company, "name": "Company"},
             "fact": {"id": fact.json()["id"], "text": "Priya leads the Atlas project."}, "document": None}]
        assert admin.post(f"/api/brain/facts/{fact.json()['id']}/retire").status_code == 200
        assert answered(admin.get(f"/api/brain/entities/{priya}")) == ENTITY_NOT_FOUND
        assert superuser_sql(org_database, "SELECT count(*) FROM row_bot_org.brain_mentions WHERE fact_id IS NOT NULL"
                             ) == [(0,)]


# -- who sees what ------------------------------------------------------------------------------------

def test_nobody_sees_a_spaces_graph_they_cant_read_by_any_route_or_answer(org_database, idp) -> None:
    with running_endpoint("openai") as endpoint, org_server(org_database, idp) as server:
        answering(endpoint, [
            ("Priya leads", [entity("person", "Priya"), entity("project", "Atlas"),
                             relation("leads", "Priya", "Atlas")]),
            ("Orion", [entity("project", "Orion")]),
        ])
        admin = admin_of(server, idp)
        add_models(admin, endpoint.base_url)
        priya, eve = person(server, idp, "priya", ["Sales"]), person(server, idp, "eve", ["Engineering"])
        eve_id, priya_id = me(eve), me(priya)
        sales = new_space(admin, "Sales team", reader_groups=["Sales"], owner_principals=[priya_id])
        uploaded(priya, sales, "atlas.md", b"Priya leads the Atlas project.")
        uploaded(admin, company_space(admin), "orion.md", b"Orion is the search project.")
        process(server)
        found = graph_of(priya)
        assert set(named(found)) == {"Priya", "Atlas", "Orion"}
        atlas, (leads,) = named(found)["Atlas"]["id"], [edge["id"] for edge in found["edges"]]
        nowhere = str(uuid.uuid4())

        # Eve reads the company space only, and a hidden id answers exactly as one that doesn't exist.
        assert set(named(graph_of(eve))) == {"Orion"}
        assert answered(eve.get(f"/api/brain/entities/{atlas}")) == answered(
            eve.get(f"/api/brain/entities/{nowhere}")) == ENTITY_NOT_FOUND
        assert answered(eve.get("/api/brain/graph", params={"spaces": sales})) == answered(
            eve.get("/api/brain/graph", params={"spaces": nowhere})) == SPACE_NOT_FOUND
        assert answered(eve.post(f"/api/brain/entities/{atlas}/retire")) == answered(
            eve.post(f"/api/brain/entities/{nowhere}/retire")) == ENTITY_NOT_FOUND
        assert answered(eve.post(f"/api/brain/relations/{leads}/retire")) == answered(
            eve.post(f"/api/brain/relations/{nowhere}/retire")) == (404, {"error": "relation_not_found"})
        relation_body = {"source_id": atlas, "target_id": atlas, "relation_type": "uses"}
        assert answered(eve.post(f"/api/brain/spaces/{sales}/relations", json=relation_body)) == answered(
            eve.post(f"/api/brain/spaces/{nowhere}/relations", json=relation_body)) == SPACE_NOT_FOUND
        # The graph is always a member's view: an admin sees what their groups give them.
        assert set(named(graph_of(admin))) == {"Orion"}

        # Behind the API, row-level security alone keeps Eve out, and the worker's flag gives a principal nothing.
        company = company_space(eve)
        with runtime_pool(org_database) as pool:
            for flag in (False, True):
                with principal_transaction(pool, eve_id, ["Engineering"]) as conn:
                    if flag:
                        conn.execute("SELECT set_config('row_bot.brain_worker', 'on', true)")
                    for table in ("brain_entities", "brain_relations", "brain_mentions"):
                        assert conn.execute(f"SELECT count(*) FROM row_bot_org.{table} WHERE space_id = %s",
                                            (sales,)).fetchone() == (0,), table
                    assert conn.execute("UPDATE row_bot_org.brain_entities SET status = 'retired'").rowcount == 0
            # A reader who doesn't own the company space can't add to its graph either (only the worker adds).
            with principal_transaction(pool, eve_id, ["Engineering"]) as conn, \
                    pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("INSERT INTO row_bot_org.brain_entities (space_id, name, name_key, entity_type)"
                             " VALUES (%s, 'Mallory', 'mallory', 'person')", (company,))
            # Nor can a space's owner move its graph into another space: every row stays in its own.
            for table, row in (("brain_entities", atlas), ("brain_relations", leads)):
                with principal_transaction(pool, priya_id, ["Sales"]) as conn, \
                        pytest.raises(psycopg.errors.InsufficientPrivilege):
                    conn.execute(f"UPDATE row_bot_org.{table} SET space_id = %s WHERE id = %s", (company, row))


# -- curation -----------------------------------------------------------------------------------------

def test_owners_merge_rename_retire_restore_and_relate_by_hand_and_each_change_is_audited(org_database,
                                                                                         idp) -> None:
    with running_endpoint("openai") as endpoint, org_server(org_database, idp) as server:
        answering(endpoint, [
            ("Priya leads", [entity("person", "Priya"), entity("project", "Atlas"),
                             entity("project", "Atlas Programme", "The payments programme."),
                             entity("system", "Postgres"), relation("leads", "Priya", "Atlas"),
                             relation("works_on", "Priya", "Atlas Programme"),
                             relation("uses", "Atlas Programme", "Postgres"), relation("uses", "Atlas", "Postgres")]),
            ("Postgres daily", [entity("project", "Atlas"), entity("project", "Atlas Programme"),
                                entity("system", "Postgres"), relation("uses", "Atlas", "Postgres")]),
            ("Orion", [entity("project", "Orion")]),
        ])
        admin = admin_of(server, idp)
        add_models(admin, endpoint.base_url)
        priya, sam = person(server, idp, "priya", ["Sales"]), person(server, idp, "sam", ["Sales"])
        priya_id = me(priya)
        sales = new_space(admin, "Sales team", reader_groups=["Sales"], owner_principals=[priya_id])
        uploaded(priya, sales, "atlas.md", b"Priya leads the Atlas project on Postgres.")
        uploaded(admin, company_space(admin), "orion.md", b"Orion is the search project.")
        process(server)
        ids = {name: node["id"] for name, node in named(graph_of(priya, spaces=sales)).items()}
        atlas, programme = ids["Atlas"], ids["Atlas Programme"]

        # Readers who don't own the space, and a device's bearer, can't curate.
        assert answered(sam.post(f"/api/brain/entities/{atlas}/retire")) == (403, {"error": "space_owner_required"})
        device = bearer(join(server, priya)["access_token"])
        app = row_bot(server)
        assert app.get("/api/brain/graph", headers=device).status_code == 200
        assert answered(app.post(f"/api/brain/entities/{atlas}/retire", headers=device)) == (
            403, {"error": "browser_session_required"})

        # A name and type another entity has is refused, with that entity's id (merge them instead).
        assert answered(priya.patch(f"/api/brain/entities/{atlas}", json={"subject": "Atlas Programme"})) == (
            409, {"error": "entity_exists", "entity_id": programme})
        merged = priya.post(f"/api/brain/entities/{programme}/merge", json={"into": atlas})
        assert merged.status_code == 200, merged.text
        assert (merged.json()["aliases"], merged.json()["description"]) == (["Atlas Programme"],
                                                                            "The payments programme.")
        found = graph_of(priya, spaces=sales)
        assert set(named(found)) == {"Priya", "Atlas", "Postgres"}
        assert edges(found) == {("Priya", "leads", "Atlas"), ("Priya", "works_on", "Atlas"),
                                ("Atlas", "uses", "Postgres")}
        assert details(priya, programme)["entity"]["status"] == "merged_into"
        assert details(priya, programme)["entity"]["merged_into"] == atlas
        assert details(priya, atlas)["mention_count"] == 1
        assert answered(priya.post(f"/api/brain/entities/{programme}/retire")) == (
            409, {"error": "entity_merged", "merged_into": atlas})

        # A rename keeps the old name as an alias, so extraction still finds it (and the merged name leads there).
        renamed = priya.patch(f"/api/brain/entities/{atlas}",
                              json={"subject": "Atlas payments", "description": "The payments programme, 2026."})
        assert renamed.status_code == 200, renamed.text
        assert (renamed.json()["subject"], renamed.json()["aliases"], renamed.json()["edited_by"]) == (
            "Atlas payments", ["Atlas Programme", "Atlas"], priya_id)
        uploaded(priya, sales, "daily.md", b"The Atlas team uses Postgres daily.")
        process(server)
        assert set(named(graph_of(priya, spaces=sales))) == {"Priya", "Atlas payments", "Postgres"}
        assert details(priya, atlas)["mention_count"] == 2

        # Relations: retired and restored (again: unchanged, no event), and one made by hand.
        (works_on,) = [edge["id"] for edge in graph_of(priya, spaces=sales)["edges"]
                       if edge["relation_type"] == "works_on"]
        assert priya.post(f"/api/brain/relations/{works_on}/retire").json()["status"] == "retired"
        assert priya.post(f"/api/brain/relations/{works_on}/retire").json()["status"] == "retired"
        assert ("Priya", "works_on", "Atlas payments") not in edges(graph_of(priya, spaces=sales))
        assert priya.post(f"/api/brain/relations/{works_on}/restore").json()["status"] == "active"
        by_hand = {"source_id": ids["Priya"], "target_id": ids["Postgres"], "relation_type": "owns"}
        made = priya.post(f"/api/brain/spaces/{sales}/relations", json=by_hand)
        assert made.status_code == 201, made.text
        assert (made.json()["origin"], made.json()["edited_by"]) == ("manual", priya_id)
        assert answered(priya.post(f"/api/brain/spaces/{sales}/relations", json=by_hand)) == (
            409, {"error": "relation_exists", "relation_id": made.json()["id"]})
        assert priya.post(f"/api/brain/spaces/{sales}/relations",
                          json={**by_hand, "relation_type": "likes"}).status_code == 422
        orion = named(graph_of(priya))["Orion"]["id"]
        assert answered(priya.post(f"/api/brain/spaces/{sales}/relations", json={**by_hand, "target_id": orion})) == (
            422, {"error": "unknown_entity"})

        # Entities: retired (with their relations out of the graph) and restored.
        assert priya.post(f"/api/brain/entities/{ids['Priya']}/retire").json()["status"] == "retired"
        assert set(named(graph_of(priya, spaces=sales))) == {"Atlas payments", "Postgres"}
        assert priya.post(f"/api/brain/entities/{ids['Priya']}/restore").json()["status"] == "active"
        assert ("Priya", "owns", "Postgres") in edges(graph_of(priya, spaces=sales))

    events = [summary(event)[:5] + (event["layer"], event["target"]["kind"], event["details"])
              for event in stored_events(org_database.owner_url, "graph.changed")]

    def done(target: str, kind: str, change: str, **more: str) -> tuple:
        return ("graph.changed", "person", priya_id, target, "succeeded", "team", kind,
                {"change": change, "space_id": sales, **more})

    assert events == [
        done(programme, "entity", "merged", merged_into=atlas),
        done(atlas, "entity", "updated", fields="subject,description,aliases"),
        done(works_on, "relation", "retired"),
        done(works_on, "relation", "restored"),
        done(made.json()["id"], "relation", "created"),
        done(ids["Priya"], "entity", "retired"),
        done(ids["Priya"], "entity", "restored"),
    ]


# -- the graph API ------------------------------------------------------------------------------------

def test_the_graph_is_bounded_most_related_first_and_filtered_by_name_or_alias(org_database, idp) -> None:
    teams = [entity("team", f"Team {n:02}", **({"aliases": "Blue squad"} if n == 7 else {})) for n in range(1, 26)]
    belongs = [relation("part_of", f"Team {n:02}", "Team 01") for n in range(2, 26)]
    with running_endpoint("openai") as endpoint, org_server(org_database, idp) as server:
        answering(endpoint, [("teams", teams + belongs)])
        admin = admin_of(server, idp)
        add_models(admin, endpoint.base_url)
        company = company_space(admin)
        uploaded(admin, company, "teams.md", b"Our teams and how they fit together.")
        process(server)

        # At most MAX_ENTITIES entities from a part (and only relations between those).
        everything = graph_of(admin)
        assert (everything["shown_entities"], everything["total_entities"], everything["total_relations"]) == (
            20, 20, 19)
        assert everything["truncated"] is False

        few = graph_of(admin, limit=5)
        assert [node["subject"] for node in few["nodes"]] == ["Team 01", "Team 02", "Team 03", "Team 04", "Team 05"]
        assert [node["relation_count"] for node in few["nodes"]] == [19, 1, 1, 1, 1]
        assert edges(few) == {(f"Team {n:02}", "part_of", "Team 01") for n in range(2, 6)}
        assert (few["shown_entities"], few["total_entities"], few["shown_relations"], few["total_relations"],
                few["truncated"]) == (5, 20, 4, 19, True)

        tens = graph_of(admin, q="team 1")
        assert [node["subject"] for node in tens["nodes"]] == [f"Team {n}" for n in range(10, 20)]
        assert (tens["edges"], tens["total_relations"]) == ([], 0)
        assert [node["subject"] for node in graph_of(admin, q="BLUE")["nodes"]] == ["Team 07"]
        assert graph_of(admin, spaces=company)["total_entities"] == 20
        assert graph_of(admin, spaces="")["nodes"] == []
        for refused in ({"limit": 0}, {"limit": 1001}, {"q": "x" * 161}):
            assert admin.get("/api/brain/graph", params=refused).status_code == 422
        assert answered(admin.get("/api/brain/graph", params={"spaces": "not-an-id"})) == SPACE_NOT_FOUND


# -- waiting, without a loop --------------------------------------------------------------------------

def test_without_a_usable_chat_model_documents_wait_with_the_reason_and_nothing_loops(org_database, idp) -> None:
    with running_endpoint("openai") as endpoint, org_server(org_database, idp) as server:
        answering(endpoint, [("Atlas", [entity("project", "Atlas")])])
        admin = admin_of(server, idp)
        add_models(admin, endpoint.base_url, chat=False)
        document = uploaded(admin, company_space(admin), "atlas.md", b"Atlas is the payments project.")

        assert process(server) == 1
        assert document_graph(admin, document["id"]) == ("waiting", "Add a chat model in Models")
        assert process(server) == 0
        assert chat_calls(endpoint) == []

        # A default chat model whose endpoint is off is a reason of its own; turned on, the document is read.
        off = admin.post("/api/admin/endpoints", json={"name": "chat", "api": "openai", "base_url": endpoint.base_url,
                                                        "api_key": KEY, "enabled": False})
        add_chat(admin, off.json()["id"])
        process(server)
        assert document_graph(admin, document["id"]) == ("waiting", "The default chat model's endpoint is turned off.")
        assert chat_calls(endpoint) == []
        assert admin.patch(f"/api/admin/endpoints/{off.json()['id']}", json={"enabled": True}).status_code == 200
        process(server)
        assert document_graph(admin, document["id"]) == ("done", None)
        assert set(named(graph_of(admin))) == {"Atlas"}


def test_a_spent_budget_leaves_the_rest_of_a_document_waiting_until_later_with_no_loop(org_database, idp) -> None:
    with running_endpoint("openai") as endpoint, org_server(org_database, idp) as server:
        answering(endpoint, [("Orion", [entity("project", "Orion")]), ("Atlas is", [entity("project", "Atlas")])])
        admin = admin_of(server, idp)
        # Each chat call costs 0.012; the budget is 0.01, so the first goes through and spends it.
        add_models(admin, endpoint.base_url, input_price="10")
        priya = person(server, idp, "priya")
        priya_id = me(priya)
        company = company_space(admin)
        assert admin.patch(f"/api/admin/brain/spaces/{company}",
                           json={"owner_principals": [priya_id]}).status_code == 200
        budget = admin.put("/api/admin/budgets", json={"scope": "person", "subject": priya_id, "hard_limit": 0.01})
        assert budget.status_code == 200, budget.text
        document = uploaded(priya, company, "projects.md", TWO_PARTS)

        assert process(server) == 1
        assert len(chat_calls(endpoint)) == 1
        assert document_graph(priya, document["id"]) == ("waiting", "Your monthly model budget is used up.")
        assert set(named(graph_of(priya))) == {"Atlas"}
        assert process(server) == 0
        assert len(chat_calls(endpoint)) == 1

        # Later (the wait over) and with budget again, it carries on from the part it stopped at.
        assert admin.delete(f"/api/admin/budgets/{budget.json()['id']}").status_code == 204
        superuser_sql(org_database, "UPDATE row_bot_org.brain_documents"
                                    " SET graph_retry_at = now() - interval '1 second' WHERE id = %s", document["id"])
        process(server)
        assert len(chat_calls(endpoint)) == 2 and "Orion" in chat_calls(endpoint)[1]
        assert document_graph(priya, document["id"]) == ("done", None)
        assert set(named(graph_of(priya))) == {"Atlas", "Orion"}


# -- the worker's bounds and stop ---------------------------------------------------------------------

def test_a_pass_makes_only_so_many_calls_reads_only_a_documents_first_parts_and_stops_when_asked(
        org_database, idp, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(brain_service, "GRAPH_CALLS_PER_PASS", 1)
    with running_endpoint("openai") as endpoint, org_server(org_database, idp) as server:
        answering(endpoint, [("Orion", [entity("project", "Orion")]), ("Atlas is", [entity("project", "Atlas")])])
        admin = admin_of(server, idp)
        add_models(admin, endpoint.base_url)
        company = company_space(admin)
        two = uploaded(admin, company, "two.md", TWO_PARTS)

        process(server)
        assert len(chat_calls(endpoint)) == 1
        assert document_graph(admin, two["id"]) == ("pending", None)
        process(server)
        assert len(chat_calls(endpoint)) == 2
        assert document_graph(admin, two["id"]) == ("done", None)

        monkeypatch.setattr(brain_service, "GRAPH_CALLS_PER_PASS", 12)
        monkeypatch.setattr(brain_service, "GRAPH_MAX_CHUNKS", 2)
        three = uploaded(admin, company, "three.md", THREE_PARTS)
        process(server)
        assert len(chat_calls(endpoint)) == 4
        assert all("Orion" not in text for text in chat_calls(endpoint)[2:])
        assert document_graph(admin, three["id"]) == (
            "done", "Only the start of this document is in the knowledge graph (its first 2 of 3 parts).")

        # Stopped, the worker takes nothing more.
        server.app.state.brain.stop()
        stopped = uploaded(admin, company, "late.md", b"Atlas is late.")
        assert process(server) == 0
        assert len(chat_calls(endpoint)) == 4
        assert admin.get(f"/api/brain/documents/{stopped['id']}").json()["status"] == "queued"


# -- what no answer, outage or backlog can do ---------------------------------------------------------

DEEP = "[" * 60_000
HUGE = json.dumps([entity("person", "Priya"), entity("project", "Atlas"),
                   relation("leads", "Priya", "Atlas", confidence=10 ** 400)])
LONG_KEY = [entity("person", "\u00df" * 160), entity("project", "Atlas")]


def test_answers_crafted_to_break_the_reader_give_nothing_and_nothing_is_asked_again(org_database, idp) -> None:
    with running_endpoint("openai") as endpoint, org_server(org_database, idp) as server:
        answering(endpoint, [
            ("Orion", [*LONG_KEY[:1], entity("project", "Orion")]), ("Atlas is", DEEP), ("Filler", HUGE),
            ("nesting", DEEP), ("huge", HUGE), ("eszett", LONG_KEY), ("Canteen", [entity("place", "Canteen")]),
        ])
        admin = admin_of(server, idp)
        add_models(admin, endpoint.base_url)
        company = company_space(admin)
        for text in ("A fact about nesting.", "A huge fact.", "An eszett fact.", "The Canteen opens at noon."):
            assert admin.post(f"/api/brain/spaces/{company}/facts", json={"text": text}).status_code == 201
        document = uploaded(admin, company, "three.md", THREE_PARTS)

        process(server)
        # Every part and every fact was asked once; the crafted ones gave nothing, the rest what they held.
        assert len(chat_calls(endpoint)) == 7
        assert document_graph(admin, document["id"]) == ("done", None)
        assert set(named(graph_of(admin))) == {"Orion", "Atlas", "Canteen"}
        assert superuser_sql(org_database, "SELECT count(*) FROM row_bot_org.brain_facts WHERE graph_done_at IS NULL"
                             ) == [(0,)]
        process(server)
        assert len(chat_calls(endpoint)) == 7
        # A name whose compared form would be too long is refused by the API too.
        atlas = named(graph_of(admin))["Atlas"]["id"]
        assert admin.patch(f"/api/brain/entities/{atlas}", json={"subject": "\u00df" * 160}).status_code == 422
        assert admin.patch(f"/api/brain/entities/{atlas}", json={"aliases": ["\u00df" * 160]}).status_code == 422


def test_an_outage_leaves_documents_waiting_never_failed_and_a_new_model_reads_a_failed_graph_again(
        org_database, idp, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(brain_service, "GRAPH_BACKOFF_SECONDS", 0.0)
    with running_endpoint("openai") as embeddings, running_endpoint("openai") as chat, \
            org_server(org_database, idp) as server:
        answering(chat, [("Atlas", [entity("project", "Atlas")])])
        admin = admin_of(server, idp)
        add_models(admin, embeddings.base_url, chat=False)
        chat_endpoint = admin.post("/api/admin/endpoints", json={"name": "chat", "api": "openai",
                                                                 "base_url": chat.base_url, "api_key": KEY})
        add_chat(admin, chat_endpoint.json()["id"])
        document = uploaded(admin, company_space(admin), "atlas.md", b"Atlas is the payments project.")

        def later() -> None:
            superuser_sql(org_database, "UPDATE row_bot_org.brain_documents"
                                        " SET graph_retry_at = now() - interval '1 second' WHERE id = %s",
                          document["id"])
            process(server)

        chat.error = 503
        process(server)
        for _ in range(graph_store.MAX_CLAIMS + 2):
            assert document_graph(admin, document["id"]) == ("waiting", "The company's model endpoint couldn't answer.")
            later()
        assert document_graph(admin, document["id"]) == ("waiting", "The company's model endpoint couldn't answer.")
        assert len(chat_calls(chat)) == graph_store.MAX_CLAIMS + 3

        # A graph that failed with one chat model is read again with another (as if it had failed with an earlier one).
        superuser_sql(org_database, "UPDATE row_bot_org.brain_documents SET graph_status = 'failed',"
                                    " graph_attempts = 4, graph_route = 'an earlier model' WHERE id = %s",
                      document["id"])
        chat.error = None
        process(server)
        assert document_graph(admin, document["id"]) == ("done", None)
        assert set(named(graph_of(admin))) == {"Atlas"}


def test_facts_have_their_own_share_of_a_pass_however_many_documents_wait(org_database, idp,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    # A clock that each chat call moves on by 50 s: documents may use 90 of the pass's 120 s, facts the rest.
    clock = [0.0]
    monkeypatch.setattr(brain_service, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    with running_endpoint("openai") as endpoint, org_server(org_database, idp) as server:
        def answer(body: dict) -> str:
            clock[0] += 50
            return json.dumps([entity("place", "Canteen")] if "Canteen" in part_text(body) else [])

        admin = admin_of(server, idp)
        add_models(admin, endpoint.base_url)
        endpoint.chat = answer
        company = company_space(admin)
        document = uploaded(admin, company, "three.md", THREE_PARTS)
        assert admin.post(f"/api/brain/spaces/{company}/facts",
                          json={"text": "The Canteen opens at noon."}).status_code == 201

        process(server)
        assert len(chat_calls(endpoint)) == 3
        assert chat_calls(endpoint)[-1] == "The Canteen opens at noon."
        assert document_graph(admin, document["id"]) == ("pending", None)
        assert set(named(graph_of(admin))) == {"Canteen"}
