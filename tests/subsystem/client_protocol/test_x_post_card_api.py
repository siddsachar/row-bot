"""An X post's approval through the client API (N7): the card the person reads, then the post it lets out.

The turn pauses on the real x_post card; the approval view carries the account, the whole text and each
picture's name, size and SHA-256 (never a path or a token); approving resumes the turn, and the tool,
run again from the top, posts exactly that to a fake X API. Stores are isolated and the model is a
scripted stream; nothing reaches X.
"""
from __future__ import annotations

import hashlib
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from tests.helpers.fake_x_api import PERSONAL, ROWBOT, FakeXApi, install
from tests.subsystem.client_protocol.test_protocol_application import service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap
from tests.helpers.access import person_window

pytestmark = pytest.mark.subsystem


def _post(client, headers, target, kind, payload, revision="0"):
    identity = str(uuid4())
    return client.post(f"/api/v1/conversations/{target}/commands", headers={**headers, "Idempotency-Key": identity},
                       json={"command_id": identity, "client_session_id": headers["X-Client-Session"], "type": kind,
                             "expected_revision": revision, "payload": payload})


def test_the_approval_view_shows_the_post_and_approving_sends_exactly_it(service, tmp_path, monkeypatch):  # noqa: F811
    from PIL import Image

    from row_bot.api.v1.routes import create_client_platform_app
    from row_bot.tools import approval_gate, x_posting
    from row_bot.tools.x_tool import XTool

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "profile"))
    fake = FakeXApi()
    install(monkeypatch, fake, ROWBOT, PERSONAL)
    for user in fake.users.values():  # Both accounts post long posts (an X subscription).
        user["subscription_type"] = "Premium"
    monkeypatch.setattr(approval_gate, "current_approval_mode", lambda: "ask")
    tool = XTool()
    monkeypatch.setattr(tool, "get_config", lambda key, default=None: default)
    picture = tmp_path / "launch.png"
    Image.new("RGB", (800, 450), "teal").save(picture)
    text = "Row-Bot 5.3: every post names its account.\n\n" + "Details follow. " * 120
    call = {"action": "post", "text": text, "media_paths": [str(picture)], "account": "@rowbot_ai"}
    results: list[str] = []

    class Paused(Exception):
        pass

    def stream(_text, _enabled, _config, *, stop_event):
        card: dict = {}

        def pause(payload):
            card.update(payload)
            raise Paused
        with monkeypatch.context() as patch:
            patch.setattr(x_posting, "_ask", pause)
            with pytest.raises(Paused):
                tool._x_post(**call)
        yield "interrupt", [{"__interrupt_id": "x-card", **card}]

    def resume(_enabled, _config, approved, *, interrupt_ids=None, stop_event=None):
        # The tool runs again from the top, and the person's answer is this card's.
        with monkeypatch.context() as patch:
            patch.setattr(x_posting, "_ask", lambda _payload: approved)
            results.append(tool._x_post(**call))
        yield "done", "Posted."

    service.stream_factory, service.resume_factory = stream, resume
    app = create_client_platform_app(service, choices=lambda: {"models": [], "capabilities": []}, session_authenticator=person_window)
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 12345)) as client:
        _, headers = bootstrap(client)
        created = client.post("/api/v1/conversations/commands", headers={**headers, "Idempotency-Key": str(uuid4())},
                              json={"command_id": str(uuid4()), "client_session_id": headers["X-Client-Session"],
                                    "type": "conversation.create", "expected_revision": "0",
                                    "payload": {"title": "Launch post"}})
        conversation = created.json()["conversation_id"]
        submitted = _post(client, headers, conversation, "conversation.submit", {
            "submission_id": str(uuid4()), "text": "Post the launch",
            "model_selection": {"provider_id": "fixture", "model_ref": "fixture::model"}})
        assert submitted.status_code == 202, submitted.text
        handle = service.registry.get(submitted.json()["execution_id"])
        assert handle.producer_done.wait(5)
        view = client.get(f"/api/v1/approvals/{handle.approval_id}", headers=headers)
        assert view.status_code == 200, view.text
        assert fake.posts == []  # Nothing goes out while the card waits.
        resolved = client.post(f"/api/v1/approvals/{handle.approval_id}/commands",
                               headers={**headers, "Idempotency-Key": str(uuid4())}, json={
                                   "command_id": str(uuid4()), "client_session_id": headers["X-Client-Session"],
                                   "type": "approval.resolve", "expected_revision": view.json()["revision"],
                                   "payload": {"decision": "approve", "nonce": view.json()["nonce"]}})
        assert resolved.status_code == 202, resolved.text
        assert service.registry.get(resolved.json()["execution_id"]).producer_done.wait(5)

    card = view.json()
    assert card["question"] == "Post to X as @rowbot_ai?"
    post = card["x_post"]
    assert (post["username"], post["name"], post["account_id"], post["action"]) == ("rowbot_ai", "Row-Bot", "111",
                                                                                    "post")
    assert post["text"] == text  # The whole text: far past the reason's 1,024 characters.
    [media] = post["media"]
    assert (media["name"], media["size"], media["kind"]) == ("launch.png", picture.stat().st_size, "image")
    assert media["sha256"] == hashlib.sha256(picture.read_bytes()).hexdigest()[:12]
    assert media["thumbnail"].startswith("data:image/jpeg;base64,")
    assert str(tmp_path) not in json.dumps(card) and "access-" not in json.dumps(card)
    [result] = results
    [sent] = fake.posted_by("111")
    assert f"Posted as @rowbot_ai: https://x.com/rowbot_ai/status/{sent['id']}" in result
    assert sent["text"].startswith("Row-Bot 5.3: every post names its account.") and fake.posted_by("222") == []


def test_an_edit_on_the_card_resumes_the_post_with_the_edit_and_approves_nothing(service, tmp_path, monkeypatch):  # noqa: F811
    """G2: Edit sends the new text and the files kept to the paused post (which asks again), never an approval."""
    from PIL import Image

    from row_bot.api.v1.routes import create_client_platform_app
    from row_bot.tools import approval_gate, x_posting
    from row_bot.tools.x_tool import XTool

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "profile"))
    fake = FakeXApi()
    install(monkeypatch, fake, ROWBOT)
    monkeypatch.setattr(approval_gate, "current_approval_mode", lambda: "ask")
    tool = XTool()
    monkeypatch.setattr(tool, "get_config", lambda key, default=None: default)
    picture = tmp_path / "launch.png"
    Image.new("RGB", (800, 450), "teal").save(picture)
    resumed: list[tuple] = []

    class Paused(Exception):
        pass

    def stream(_text, _enabled, _config, *, stop_event):
        card: dict = {}

        def pause(payload):
            card.update(payload)
            raise Paused
        with monkeypatch.context() as patch:
            patch.setattr(x_posting, "_ask", pause)
            with pytest.raises(Paused):
                tool._x_post("post", text="Draft", media_paths=[str(picture)], account="@rowbot_ai")
        yield "interrupt", [{"__interrupt_id": "x-card", **card}]

    def resume(_enabled, _config, approved, *, interrupt_ids=None, stop_event=None, resume_values=None):
        resumed.append((approved, interrupt_ids, resume_values))
        yield "done", "Asked again."

    service.stream_factory, service.resume_factory = stream, resume
    app = create_client_platform_app(service, choices=lambda: {"models": [], "capabilities": []}, session_authenticator=person_window)
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 12345)) as client:
        _, headers = bootstrap(client)
        created = client.post("/api/v1/conversations/commands", headers={**headers, "Idempotency-Key": str(uuid4())},
                              json={"command_id": str(uuid4()), "client_session_id": headers["X-Client-Session"],
                                    "type": "conversation.create", "expected_revision": "0",
                                    "payload": {"title": "Launch post"}})
        conversation = created.json()["conversation_id"]
        submitted = _post(client, headers, conversation, "conversation.submit", {
            "submission_id": str(uuid4()), "text": "Post the launch",
            "model_selection": {"provider_id": "fixture", "model_ref": "fixture::model"}})
        handle = service.registry.get(submitted.json()["execution_id"])
        assert handle.producer_done.wait(5)
        view = client.get(f"/api/v1/approvals/{handle.approval_id}", headers=headers).json()
        [shown] = view["x_post"]["media"]

        def resolve(payload):
            # Each try reads the card again: a nonce answers once, even when its command is refused.
            current = client.get(f"/api/v1/approvals/{handle.approval_id}", headers=headers).json()
            identity = str(uuid4())
            return client.post(f"/api/v1/approvals/{handle.approval_id}/commands",
                               headers={**headers, "Idempotency-Key": identity}, json={
                                   "command_id": identity, "client_session_id": headers["X-Client-Session"],
                                   "type": "approval.resolve", "expected_revision": current["revision"],
                                   "payload": {"nonce": current["nonce"], **payload}})
        refused = [resolve({"decision": "edit", "edit": {"text": "New", "media": ["0123456789ab"]}}),  # Not its file.
                   resolve({"decision": "approve", "edit": {"text": "New"}}),
                   resolve({"decision": "edit"})]
        edited = resolve({"decision": "edit", "edit": {"text": "Row-Bot 5.3.1 is out.", "media": [shown["sha256"]]}})
        assert edited.status_code == 202, edited.text
        assert service.registry.get(edited.json()["execution_id"]).producer_done.wait(5)

    assert [(item.status_code, item.json()["code"]) for item in refused] == [(422, "approval_edit_invalid")] * 3
    assert view["x_post"]["limit"] == 280
    assert resumed == [(False, ["x-card"], {"x-card": {"x_post_edit": {"text": "Row-Bot 5.3.1 is out.",
                                                                        "media": [shown["sha256"]]}}})]
    assert fake.posts == []



def test_review_lists_what_goes_out_and_the_scheduled_posts_and_cancel_stops_one(service, tmp_path, monkeypatch):  # noqa: F811
    """G3: the approvals feed marks a waiting post as one Review shows in full; scheduled posts list and cancel."""
    from datetime import datetime, timedelta

    from row_bot.api.v1.routes import create_client_platform_app
    from row_bot.tools import approval_gate, x_posting
    from row_bot.tools.x_tool import XTool
    from row_bot import x_accounts

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "profile"))
    fake = FakeXApi()
    install(monkeypatch, fake, ROWBOT)
    monkeypatch.setattr(approval_gate, "current_approval_mode", lambda: "ask")
    tool = XTool()
    monkeypatch.setattr(tool, "get_config", lambda key, default=None: default)
    when = (datetime.now() + timedelta(hours=3)).replace(second=0, microsecond=0)

    class Paused(Exception):
        pass

    def stream(_text, _enabled, _config, *, stop_event):
        card: dict = {}

        def pause(payload):
            card.update(payload)
            raise Paused
        with monkeypatch.context() as patch:
            patch.setattr(x_posting, "_ask", pause)
            with pytest.raises(Paused):
                tool._x_post("post", text="Evening post", account="@rowbot_ai",
                             post_at=when.isoformat(timespec="minutes"))
        yield "interrupt", [{"__interrupt_id": "x-card", **card}]

    service.stream_factory = stream
    account = x_accounts.resolve("@rowbot_ai", required=True)
    kept = x_posting.schedule(account, "post", "Morning post", "", [], when - timedelta(hours=1))
    app = create_client_platform_app(service, choices=lambda: {"models": [], "capabilities": []}, session_authenticator=person_window)
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 12345)) as client:
        _, headers = bootstrap(client)
        created = client.post("/api/v1/conversations/commands", headers={**headers, "Idempotency-Key": str(uuid4())},
                              json={"command_id": str(uuid4()), "client_session_id": headers["X-Client-Session"],
                                    "type": "conversation.create", "expected_revision": "0",
                                    "payload": {"title": "Evening post"}})
        conversation = created.json()["conversation_id"]
        submitted = _post(client, headers, conversation, "conversation.submit", {
            "submission_id": str(uuid4()), "text": "Post this evening",
            "model_selection": {"provider_id": "fixture", "model_ref": "fixture::model"}})
        handle = service.registry.get(submitted.json()["execution_id"])
        assert handle.producer_done.wait(5)
        feed = client.get("/api/v1/monitor/approvals", headers=headers).json()
        view = client.get(f"/api/v1/approvals/{handle.approval_id}", headers=headers).json()
        listed = client.get("/api/v1/monitor/scheduled-posts", headers=headers).json()
        cancelled = client.delete(f"/api/v1/monitor/scheduled-posts/{kept['id']}", headers=headers)
        again = client.delete(f"/api/v1/monitor/scheduled-posts/{kept['id']}", headers=headers)
        after = client.get("/api/v1/monitor/scheduled-posts", headers=headers).json()

    [waiting] = feed["items"]
    assert (waiting["id"], waiting["goes_out"]) == (handle.approval_id, "post")
    assert view["x_post"]["post_at"] == when.isoformat(timespec="minutes")
    assert [(item["id"], item["username"], item["text"]) for item in listed["items"]] == [
        (kept["id"], "rowbot_ai", "Morning post")]
    assert cancelled.json() == {"cancelled": True}
    assert (again.status_code, again.json()["code"]) == (404, "scheduled_post_unavailable")
    assert after["items"] == [] and fake.posts == []


def test_edit_changes_a_waiting_posts_text_and_says_why_it_cant(service, tmp_path, monkeypatch):  # noqa: F811
    from datetime import datetime, timedelta

    from row_bot import x_accounts
    from row_bot.api.v1.routes import create_client_platform_app
    from row_bot.tools import x_posting

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "profile"))
    install(monkeypatch, FakeXApi(), ROWBOT)
    account = x_accounts.resolve("@rowbot_ai", required=True)
    when = (datetime.now() + timedelta(hours=3)).replace(second=0, microsecond=0)
    kept = x_posting.schedule(account, "post", "Morning post", "", [], when)
    app = create_client_platform_app(service, choices=lambda: {"models": [], "capabilities": []},
                                     session_authenticator=person_window)
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 12345)) as client:
        _, headers = bootstrap(client)
        edited = client.post(f"/api/v1/monitor/scheduled-posts/{kept['id']}/edit", headers=headers,
                             json={"text": "Morning post, edited", "media": []})
        empty = client.post(f"/api/v1/monitor/scheduled-posts/{kept['id']}/edit", headers=headers,
                            json={"text": " ", "media": []})
        missing = client.post("/api/v1/monitor/scheduled-posts/xs-000000000000/edit", headers=headers,
                              json={"text": "Nothing waits here", "media": []})
        [listed] = client.get("/api/v1/monitor/scheduled-posts", headers=headers).json()["items"]

    assert edited.json() == {"edited": True}
    assert (empty.status_code, empty.json()["code"]) == (422, "scheduled_post_invalid")
    assert (missing.status_code, missing.json()["code"]) == (404, "scheduled_post_unavailable")
    assert (listed["text"], listed["post_at"], listed["username"]) == (
        "Morning post, edited", when.isoformat(timespec="minutes"), "rowbot_ai")


def test_a_post_that_didnt_go_out_is_listed_with_why_and_try_again_sends_it(service, tmp_path, monkeypatch):  # noqa: F811
    from datetime import datetime, timedelta

    from row_bot import x_accounts
    from row_bot.api.v1.routes import create_client_platform_app
    from row_bot.tools import x_posting

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "profile"))
    install(monkeypatch, FakeXApi(), ROWBOT)
    account = x_accounts.resolve("@rowbot_ai", required=True)
    when = (datetime.now() - timedelta(hours=13)).replace(second=0, microsecond=0)
    kept = x_posting.schedule(account, "post", "Missed while closed", "", [], when)
    x_posting.send_due()  # Row-Bot was closed at its time: missed.
    sent: list[bool] = []
    monkeypatch.setattr(x_posting, "send_due", lambda: sent.append(True))
    app = create_client_platform_app(service, choices=lambda: {"models": [], "capabilities": []},
                                     session_authenticator=person_window)
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 12345)) as client:
        _, headers = bootstrap(client)
        [listed] = client.get("/api/v1/monitor/scheduled-posts", headers=headers).json()["items"]
        retried = client.post(f"/api/v1/monitor/scheduled-posts/{kept['id']}/retry", headers=headers,
                              json={"without_media": False})
        again = client.post(f"/api/v1/monitor/scheduled-posts/{kept['id']}/retry", headers=headers, json={})
        [waiting] = client.get("/api/v1/monitor/scheduled-posts", headers=headers).json()["items"]

    assert (listed["status"], listed["text"]) == ("missed", "Missed while closed")
    assert "wasn't posted. Approve it again to post it." in listed["reason"]
    assert retried.json() == {"retried": True} and sent, "Try again sends it at once"
    assert (again.status_code, again.json()["code"]) == (404, "scheduled_post_unavailable")
    assert (waiting["status"], waiting["reason"]) == ("scheduled", "")


@pytest.mark.parametrize("sound", [False, "silence"])
def test_a_video_with_nothing_to_hear_says_no_sound_on_its_card_and_in_review(service, tmp_path, monkeypatch,  # noqa: F811
                                                                             sound):
    """Twice a demo with no sound went out to X (3 Nov): nothing on its card said so. A video with nothing to
    hear (no sound track, or only silence) now says "no sound" on the card the person approves and in Review's
    scheduled posts; a narrated one says nothing."""
    pytest.importorskip("av")
    from datetime import datetime, timedelta

    from row_bot import x_accounts
    from row_bot.api.v1.routes import create_client_platform_app
    from row_bot.api.v1.schemas import XPostApproval
    from row_bot.tools import x_posting
    from tests.helpers.media_files import video

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "profile"))
    install(monkeypatch, FakeXApi(), ROWBOT)
    account = x_accounts.resolve("@rowbot_ai", required=True)
    silent = x_posting.media_facts([str(video(tmp_path / "demo.mp4", sound=sound, codec="mpeg4"))])
    narrated = x_posting.media_facts([str(video(tmp_path / "narrated.mp4", sound=True, codec="mpeg4"))])

    payload = x_posting.approval_payload(account, "post", "Watch the demo", "", silent)
    assert XPostApproval(**payload["x_post"]).media[0].silent
    assert "demo.mp4 (" in payload["description"] and ", no sound)" in payload["description"]
    plain = x_posting.approval_payload(account, "post", "Watch the demo", "", narrated)
    assert not plain["x_post"]["media"][0]["silent"] and "no sound" not in plain["description"]

    assert payload["x_post"]["media"][0]["ref"] == "", "a file on this computer: its path never reaches the card"
    # A file from Library › Media carries its reference and type, so the card and Review open it to check.
    from row_bot import threads
    from row_bot.application.attachments import register_attachment

    chat = threads.create_thread("Demo video", seed_default_skills=False)
    saved = register_attachment(chat, "demo.mp4", (tmp_path / "demo.mp4").read_bytes())
    library = x_posting.media_facts([saved["attachment_ref"]])
    shown = x_posting.approval_payload(account, "post", "Watch the demo", "", library)["x_post"]["media"][0]
    assert (shown["ref"], shown["mime"], shown["silent"]) == (saved["attachment_ref"], "video/mp4", True)

    when = (datetime.now() + timedelta(hours=2)).replace(second=0, microsecond=0)
    x_posting.schedule(account, "post", "Watch the demo", "", library, when)
    app = create_client_platform_app(service, choices=lambda: {"models": [], "capabilities": []},
                                     session_authenticator=person_window)
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 12345)) as client:
        _, headers = bootstrap(client)
        listed = client.get("/api/v1/monitor/scheduled-posts", headers=headers).json()
    [post] = listed["items"]
    assert [(item["name"], item["silent"], item["ref"], item["mime"]) for item in post["media"]] == [
        ("demo.mp4", True, saved["attachment_ref"], "video/mp4")]
