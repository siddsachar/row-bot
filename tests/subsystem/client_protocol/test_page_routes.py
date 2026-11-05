"""Pages (N5) over the client API: every signed-in device reads them; a page's frame is served once, sandboxed
by its own policy header too; its state and decisions are checked again here; and only the person's Send posts
its decisions to the chat, as one message labelled as from the page that never counts as their request when an
approval is reviewed.
"""

# ruff: noqa: F401, F811 -- imported pytest fixtures are requested by name.

from __future__ import annotations

import re
from uuid import uuid4

import pytest

from tests.helpers.client_platform_fakes import CheckpointCommit, ScriptedAgentStream
from tests.subsystem.client_protocol.test_protocol_application import _client, _command, _remote_client, service
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = pytest.mark.subsystem

PLAN = "<!doctype html><html><head><title>Plan</title></head><body><h1>Launch plan</h1></body></html>"
THEME = {"mode": "dark",
         "light": {"surface": "#FFFFFF", "text-primary": "#141922", "made-up": "#000000"},
         "dark": {"surface": "#11151C", "text-primary": "#E8EBF0"}}
MODEL = {"provider_id": "fixture", "model_ref": "fixture::model"}


@pytest.fixture
def plan(service, tmp_path, monkeypatch):
    """A chat with a launch plan page; the person approved one step and asked to change another."""
    from row_bot import pages, threads
    from row_bot.application import conversation_creation

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(pages, "_RECORDS", {})
    monkeypatch.setattr(pages, "_LOADED_ROOT", None)
    monkeypatch.setattr(pages, "_RATES", {})
    monkeypatch.setattr(pages, "_FRAMES", {})
    exports = tmp_path / "Documents" / "Row-Bot"
    exports.mkdir(parents=True)
    monkeypatch.setattr(conversation_creation, "configured_workspace_root", lambda: exports.resolve())
    conversation = threads.create_thread("Beta launch", seed_default_skills=False)
    page = pages.create(conversation, "Launch plan", PLAN, {"steps": []}, ["rowbot-style", "charts"])
    return {"conversation": conversation, "page": page.page_id, "pages": pages, "exports": exports}


def _frame_client():
    from fastapi.testclient import TestClient
    from row_bot.app import _page_frame_handler
    from starlette.applications import Starlette

    client = TestClient(Starlette(routes=[]))
    client.app.add_route("/page-frames/{token}", _page_frame_handler, methods=["GET"])
    return client


def test_every_signed_in_device_reads_the_chat_s_pages(service, plan):
    with _client(service) as client:
        _, headers = bootstrap(client)
        listed = client.get("/api/v1/pages", params={"conversation_id": plan["conversation"]}, headers=headers)
        assert listed.status_code == 200, listed.text
        [item] = listed.json()["items"]
        assert (item["page_id"], item["title"], item["conversation_title"], item["version"]) == (
            plan["page"], "Launch plan", "Beta launch", 1)
        assert client.get(f"/api/v1/pages/{plan['page']}", headers=headers).json()["pending"] == 0
        for missing in ("/api/v1/pages/page-00000000", "/api/v1/pages/not-a-page"):
            refused = client.get(missing, headers=headers)
            assert refused.status_code == 404 and refused.json()["code"] == "chat_page_unavailable"
        for partial in ({}, {"Origin": headers["Origin"]}, {**headers, "X-CSRF-Token": "wrong"}):
            assert client.get(f"/api/v1/pages/{plan['page']}", headers=partial).status_code in {401, 403}
    remote, active = _remote_client(service)
    with remote as client:
        _, headers = bootstrap(client)
        assert client.get(f"/api/v1/pages/{plan['page']}", headers=headers).status_code == 200
        active["value"] = False
        assert client.get(f"/api/v1/pages/{plan['page']}", headers=headers).status_code == 401


def test_a_page_frame_is_served_once_sandboxed_by_its_own_header(service, plan):
    with _client(service) as client:
        _, headers = bootstrap(client)
        made = client.post(f"/api/v1/pages/{plan['page']}/frame", headers=headers, json={"theme": THEME})
        assert made.status_code == 200, made.text
        frame_url = made.json()["frame_url"]
        assert frame_url.startswith("/page-frames/")
        bad = {**THEME, "light": {"surface": "url(https://example.invalid/x)"}}
        assert client.post(f"/api/v1/pages/{plan['page']}/frame", headers=headers,
                           json={"theme": bad}).status_code == 422
    frames = _frame_client()
    first = frames.get(frame_url)
    assert first.status_code == 200 and first.headers["content-type"].startswith("text/html")
    policy = [part.strip() for part in first.headers["content-security-policy"].split(";")]
    for directive in ("default-src 'none'", "script-src 'unsafe-inline'", "style-src 'unsafe-inline'",
                      "img-src data: blob:", "connect-src 'none'", "form-action 'none'", "frame-ancestors 'self'",
                      "sandbox allow-scripts"):
        assert directive in policy, directive
    assert first.headers["x-content-type-options"] == "nosniff"
    assert first.headers["cache-control"] == "no-store"
    assert first.headers["referrer-policy"] == "no-referrer"
    document = first.text
    assert "<h1>Launch plan</h1>" in document
    assert "window, 'rowbot'" in document and "Chart.js v4.5.1" in document
    assert "--rb-surface:#11151C" in document and "made-up" not in document
    assert frames.get(frame_url).status_code == 404  # Once: a reload or a direct load later gets nothing.
    assert frames.get("/page-frames/" + "0" * 32).status_code == 404


def test_a_tampered_chart_library_is_never_inlined(service, plan, monkeypatch):
    pages = plan["pages"]
    original = pages._static
    monkeypatch.setattr(pages, "_static", lambda name: b"window.Chart = 'not the pinned release';"
                        if name == "chart.umd.min.js" else original(name))
    with _client(service) as client:
        _, headers = bootstrap(client)
        refused = client.post(f"/api/v1/pages/{plan['page']}/frame", headers=headers, json={"theme": THEME})
        assert refused.status_code == 404 and refused.json()["code"] == "chat_page_unavailable"


def test_the_bridge_writes_are_checked_again(service, plan):
    page = plan["page"]
    with _client(service) as client:
        _, headers = bootstrap(client)
        saved = client.put(f"/api/v1/pages/{page}/state", headers=headers, json={"state": {"ticked": ["a"]}})
        assert saved.status_code == 200 and saved.json()["state_revision"] == 1
        large = client.put(f"/api/v1/pages/{page}/state", headers=headers,
                           json={"state": {"notes": "x" * (300 * 1024)}})
        assert large.status_code == 413
        decided = client.post(f"/api/v1/pages/{page}/decisions", headers=headers,
                              json={"id": "venue", "choice": "change", "note": "Smaller rooms", "label": "Book the venue"})
        assert decided.status_code == 200 and decided.json()["pending"] == 1
        assert decided.json()["decisions"] == [{"id": "venue", "choice": "change", "note": "Smaller rooms",
                                                "label": "Book the venue", "sent": False}]
        for bad in ({"id": "../x", "choice": "approve"}, {"id": "a", "choice": "x" * 41},
                    {"id": "a", "choice": "approve", "note": "x" * 2001}):
            assert client.post(f"/api/v1/pages/{page}/decisions", headers=headers, json=bad).status_code == 422
        cleared = client.post(f"/api/v1/pages/{page}/decisions", headers=headers, json={"id": "venue", "choice": None})
        assert cleared.json()["pending"] == 0


def test_download_as_html_is_one_file_that_works_on_its_own_saved_the_one_way(service, plan):
    page = plan["page"]
    with _client(service) as client:
        _, headers = bootstrap(client)
        offer = client.post("/api/v1/saves", headers=headers,
                            json={"source": {"kind": "page", "page_id": page, "theme": THEME}})
        assert offer.status_code == 200, offer.text
        assert offer.json()["file_name"] == "Launch-plan.html"
        download = client.get(f"/api/v1/attachments/{offer.json()['reference']}", headers=headers)
        assert download.status_code == 200, download.text
        html = download.content.decode("utf-8")
        assert '"standalone":true' in html
        # Opened from disk there are no headers: the frame's policy comes first, before any of the page.
        opening = re.match(r'<!doctype html>\n<html><head>\n<meta charset="utf-8">\n'
                           r'<meta http-equiv="Content-Security-Policy" content="([^"]+)">\n', html)
        assert opening is not None, html[:300]
        policy = [part.strip() for part in opening.group(1).split(";")]
        for directive in ("default-src 'none'", "script-src 'unsafe-inline'", "img-src data: blob:",
                          "connect-src 'none'", "form-action 'none'", "base-uri 'none'"):
            assert directive in policy, directive
        assert not any(part.startswith(("sandbox", "frame-ancestors")) for part in policy)  # A meta can't carry them.
        assert html.count("<!doctype") == 1 and "<h1>Launch plan</h1>" in html[opening.end():]


def test_send_posts_the_decisions_as_one_message_from_the_page(service, plan):
    from langchain_core.messages import AIMessage
    from row_bot import approval_review, threads

    pages, page, conversation = plan["pages"], plan["page"], plan["conversation"]
    pages.decide(page, "announce", "approve", "", "Draft the announcement")
    pages.decide(page, "venue", "change", "Smaller rooms near the station", "Book the venue")
    fake = ScriptedAgentStream((CheckpointCommit((AIMessage(id="reply", content="I'll look for smaller rooms."),),
                                                 "reply"), ("done", None)))
    service.stream_factory = fake.stream
    with _client(service) as client:
        _, headers = bootstrap(client)
        revision = service.get_conversation(conversation)["revision"]
        sent = _command(client, headers, "conversation.submit", {
            "submission_id": str(uuid4()), "text": "2 decisions from Launch plan", "attachment_refs": [],
            "model_selection": MODEL, "page_id": page}, target=conversation, revision=revision)
        assert sent.status_code == 202, sent.text
        assert service.registry.get(sent.json()["execution_id"]).producer_done.wait(5)

        human = next(message for message in threads.get_latest_checkpoint_messages(conversation)
                     if message.type == "human")
        assert "Approved: Draft the announcement" in human.content
        assert '"choice": "change"' in human.content and "not approval of any tool call" in human.content
        assert human.additional_kwargs["platform_note"] == "page"
        assert human.additional_kwargs["platform_public_content"] == (
            "Launch plan · 2 decisions\nApproved: Draft the announcement\n"
            "Change: Book the venue (“Smaller rooms near the station”)")
        # The Auto reviewer never reads a page's decisions as the person's request.
        assert approval_review.person_messages([human]) == ()

        transcript = client.get(f"/api/v1/conversations/{conversation}/transcript", headers=headers).json()
        row = next(item for item in transcript["rows"] if item["role"] == "user")
        assert row["note"] == "page"
        view = client.get(f"/api/v1/pages/{page}", headers=headers).json()
        assert view["pending"] == 0 and all(item["sent"] for item in view["decisions"])

        again = _command(client, headers, "conversation.submit", {
            "submission_id": str(uuid4()), "text": "Again", "attachment_refs": [], "model_selection": MODEL,
            "page_id": page}, target=conversation, revision=service.get_conversation(conversation)["revision"])
        assert again.status_code == 409 and again.json()["code"] == "chat_page_nothing_to_send"
        pages.decide(page, "stickers", "skip", "", "Order stickers")
        view = client.get(f"/api/v1/pages/{page}", headers=headers).json()
        assert view["decisions"][0] == {"id": "stickers", "choice": "skip", "note": "", "label": "Order stickers",
                                        "sent": False}  # What isn't sent yet comes first.


def test_retry_runs_the_message_from_the_page_again_as_it_was(service, plan):
    from langchain_core.messages import AIMessage
    from row_bot import approval_review, threads

    pages, page, conversation = plan["pages"], plan["page"], plan["conversation"]
    pages.decide(page, "venue", "change", "Smaller rooms near the station", "Book the venue")
    fake = ScriptedAgentStream(
        (CheckpointCommit((AIMessage(id="first", content="The provider cut out."),), "first"), ("done", None)),
        (CheckpointCommit((AIMessage(id="again", content="Looking for smaller rooms."),), "again"), ("done", None)))
    service.stream_factory = fake.stream
    with _client(service) as client:
        _, headers = bootstrap(client)
        sent = _command(client, headers, "conversation.submit", {
            "submission_id": str(uuid4()), "text": "1 decision from Launch plan", "attachment_refs": [],
            "model_selection": MODEL, "page_id": page}, target=conversation,
            revision=service.get_conversation(conversation)["revision"])
        assert service.registry.get(sent.json()["execution_id"]).producer_done.wait(5)
        first = next(message for message in threads.get_latest_checkpoint_messages(conversation)
                     if message.type == "human")
        shown = first.additional_kwargs["platform_public_content"]

        # Retry sends what the chat shows; Row-Bot runs the page's own message again in its place.
        again = _command(client, headers, "conversation.submit", {
            "submission_id": str(uuid4()), "text": shown, "attachment_refs": [], "model_selection": MODEL,
            "retry": True}, target=conversation, revision=service.get_conversation(conversation)["revision"])
        assert again.status_code == 202, again.text
        assert service.registry.get(again.json()["execution_id"]).producer_done.wait(5)

    messages = threads.get_latest_checkpoint_messages(conversation)
    [human] = [message for message in messages if message.type == "human"]
    assert human.content == first.content and "Change: Book the venue" in human.content
    assert human.additional_kwargs["platform_note"] == "page"
    assert human.additional_kwargs["platform_public_content"] == shown
    assert approval_review.person_messages([human]) == ()  # Still never the person's request.
    assert [message.content for message in messages if message.type == "ai"] == ["Looking for smaller rooms."]
    assert len(fake.calls) == 2 and pages.get(page).decisions == {}  # Nothing sent twice from the page.


def test_a_page_is_sent_only_to_its_own_chat(service, plan):
    from row_bot import threads

    pages, page = plan["pages"], plan["page"]
    pages.decide(page, "announce", "approve", "", "Draft the announcement")
    other = threads.create_thread("Something else", seed_default_skills=False)
    with _client(service) as client:
        _, headers = bootstrap(client)
        refused = _command(client, headers, "conversation.submit", {
            "submission_id": str(uuid4()), "text": "Sneaky", "attachment_refs": [], "model_selection": MODEL,
            "page_id": page}, target=other, revision=service.get_conversation(other)["revision"])
        assert refused.status_code == 404 and refused.json()["code"] == "chat_page_unavailable"
    assert pages.get(page).decisions  # Still waiting for the person's Send in its own chat.
