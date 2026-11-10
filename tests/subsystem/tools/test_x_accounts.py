"""Several X accounts, named on every post (N7).

Each X account keeps its own sign-in; the one from before 5.3 becomes the first account only once its
copy is saved; a write names its account; posting asks the person (only the person, under Auto) on a
card bound to exactly what goes out; the result links the post; an unconfirmed send is checked
against the account's latest posts, never sent again. A fake X API (in-process) and an in-memory
keychain stand in: nothing reaches X.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import pytest

from row_bot import account_tokens, x_accounts
from row_bot.application.approval_projection import project_approval_context
from row_bot.tools import approval_gate, x_posting
from tests.helpers.fake_x_api import PERSONAL, ROWBOT, FakeXApi, install

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

_NOW = datetime(2026, 10, 22, 10, 0, tzinfo=timezone.utc)
_SECRETS = ("access-rowbot_ai", "refresh-rowbot_ai", "access-sidd", "refresh-sidd", "legacy-access", "legacy-refresh",
            "fixture-x-secret")


@pytest.fixture
def x(tmp_path, monkeypatch):
    """A fresh profile, the fake X API, the person's own X app and an X tool with its default operations."""
    from row_bot.tools.x_tool import XTool

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "profile"))
    fake = FakeXApi()
    install(monkeypatch, fake)
    monkeypatch.setattr(x_posting, "_now", lambda: _NOW)  # The fake X API's clock: no real one.
    tool = XTool()
    monkeypatch.setattr(tool, "get_config", lambda key, default=None: default)
    return fake, tool


@pytest.fixture
def person(monkeypatch):
    """Stands in for the person on the approval card: records each card, answers as told."""
    asked: list[dict] = []
    answers: list = []

    def answer(payload):
        asked.append(payload)
        reply = answers.pop(0) if answers else True
        return reply(payload) if callable(reply) else reply
    monkeypatch.setattr(x_posting, "_ask", answer)
    monkeypatch.setattr(approval_gate, "current_approval_mode", lambda: "ask")
    return asked, answers


def _mode(monkeypatch, mode: str) -> None:
    monkeypatch.setattr(approval_gate, "current_approval_mode", lambda: mode)


def _picture(path: Path, colour: str = "orange") -> Path:
    from PIL import Image

    Image.new("RGB", (640, 360), colour).save(path, format="PNG")
    return path


def _creates(fake: FakeXApi) -> int:
    return sum(1 for method, path in fake.requests if (method, path) == ("POST", "/2/tweets"))


# ── Accounts and the migration ───────────────────────────────────────────────


def test_the_sign_in_from_before_becomes_the_first_account_and_its_old_slot_goes_last(x):
    fake, _ = x
    legacy = fake.sign_in(*ROWBOT, access="legacy-access")
    account_tokens.write("x", legacy)  # How 5.2 kept its one X sign-in.
    later = x_accounts.add(fake.sign_in(*PERSONAL))  # Adding another moves the old one first.

    assert [account.handle for account in x_accounts.listed()] == ["@rowbot_ai", "@sidd"]
    assert account_tokens.read("x") is None
    assert account_tokens.read("x:111") == legacy and account_tokens.read(later.slot)["access_token"] == "access-sidd"
    assert x_accounts.migrate() is None  # Once.


def test_a_migration_x_cannot_finish_keeps_the_old_sign_in_readable(x, monkeypatch):
    fake, _ = x
    stale = {"access_token": "legacy-access", "refresh_token": "legacy-refresh", "expires_at": 946684800.0}
    account_tokens.write("x", stale)
    # X renews the sign-in (its refresh tokens work once), then can't say who it is.
    fake.refreshes["legacy-refresh"] = {"access_token": "renewed-access", "refresh_token": "renewed-refresh",
                                        "expires_in": 7200}

    with pytest.raises(x_accounts.XAccountError):
        x_accounts.migrate()

    assert account_tokens.read("x")["refresh_token"] == "renewed-refresh"  # Kept before anything else could fail.
    assert x_accounts.listed() == []
    assert [row["id"] for row in x_accounts.summaries()] == [""]  # Settings still shows it, unnamed.
    fake.users["renewed-access"] = {"id": "111", "username": "rowbot_ai", "name": "Row-Bot"}
    assert x_accounts.migrate().handle == "@rowbot_ai"  # The next check finishes it.
    assert account_tokens.read("x") is None and account_tokens.read("x:111")["access_token"] == "renewed-access"


def test_a_keychain_that_cannot_keep_the_new_slot_leaves_the_old_sign_in(x, monkeypatch):
    from row_bot import secret_store

    fake, _ = x
    legacy = fake.sign_in(*ROWBOT, access="legacy-access")
    account_tokens.write("x", legacy)
    keyring = secret_store._backend_override
    real_set = keyring.set_password

    def refuse(service, name, value):
        if name.startswith("accounts:x:"):
            raise RuntimeError("keyring unavailable")
        real_set(service, name, value)
    monkeypatch.setattr(keyring, "set_password", refuse)

    with pytest.raises(x_accounts.XAccountError):
        x_accounts.migrate()
    assert account_tokens.read("x") == legacy and x_accounts.listed() == []


def test_removing_one_account_leaves_the_other_signed_in(x):
    fake, _ = x
    first, second = (x_accounts.add(fake.sign_in(*user)) for user in (ROWBOT, PERSONAL))
    x_accounts.remove(first.id)
    assert x_accounts.listed() == [second]
    assert account_tokens.read(first.slot) is None and account_tokens.read(second.slot) is not None


def test_x_accounts_lists_each_handle_name_id_and_state(x):
    fake, tool = x
    for user in (ROWBOT, PERSONAL):
        x_accounts.add(fake.sign_in(*user))
    listed = tool._x_accounts()
    assert "@rowbot_ai · Row-Bot (ID 111): signed in" in listed
    assert "@sidd · Sidd (ID 222): signed in" in listed
    assert "must say which with account" in listed


# ── Which account ────────────────────────────────────────────────────────────


def test_with_two_accounts_a_write_without_one_is_refused_with_the_list(x, person):
    fake, tool = x
    asked, _ = person
    for user in (ROWBOT, PERSONAL):
        x_accounts.add(fake.sign_in(*user))

    refused = tool._x_post("post", text="Hello")
    engaged = tool._x_engage("like", "123")

    assert refused.startswith("Which X account? @rowbot_ai or @sidd.") and "Nothing was sent" in refused
    assert engaged.startswith("Which X account? @rowbot_ai or @sidd.")
    assert asked == [] and fake.posts == []
    assert tool._x_post("post", text="Hello", account="@nobody").startswith("No connected X account is @nobody")
    # A read about another user may go out as the first account; mentions are one account's own.
    assert tool._x_read("mentions").startswith("Which X account?")


def test_with_one_account_it_is_used_and_named(x, person):
    fake, tool = x
    x_accounts.add(fake.sign_in(*ROWBOT))
    result = tool._x_post("post", text="Hello from Row-Bot")
    [post] = fake.posted_by("111")
    assert f"Posted as @rowbot_ai: https://x.com/rowbot_ai/status/{post['id']}" in result
    assert tool._x_engage("like", post["id"]) == f"Liked tweet {post['id']} as @rowbot_ai."


def test_the_named_account_is_the_one_that_posts(x, person):
    fake, tool = x
    for user in (ROWBOT, PERSONAL):
        x_accounts.add(fake.sign_in(*user))
    tool._x_post("post", text="Personal note", account="222")
    tool._x_post("post", text="Product news", account="@ROWBOT_AI")
    assert [post["text"] for post in fake.posted_by("222")] == ["Personal note"]
    assert [post["text"] for post in fake.posted_by("111")] == ["Product news"]


# ── The card and what it is bound to ─────────────────────────────────────────


def test_the_card_names_the_account_and_shows_everything_that_goes_out(x, person, tmp_path):
    from row_bot.api.v1 import schemas

    fake, tool = x
    asked, _ = person
    x_accounts.add(fake.sign_in(*ROWBOT, subscription="Premium"))  # Posts up to 25,000 characters.
    picture = _picture(tmp_path / "launch.png")
    text = "Row-Bot 5.3 is out. " + "Every post names its account. " * 60  # Far past the reason's 1,024.

    tool._x_post("post", text=text, media_paths=[str(picture)], account="@rowbot_ai")

    [payload] = asked
    card = project_approval_context(payload)
    assert card["question"] == "Post to X as @rowbot_ai?"
    post = schemas.XPostApproval.model_validate(card["x_post"])  # What the approval view carries.
    assert (post.username, post.account_id, post.name, post.action) == ("rowbot_ai", "111", "Row-Bot", "post")
    assert post.text == text  # Never cut.
    [media] = post.media
    assert (media.name, media.size, media.kind) == ("launch.png", picture.stat().st_size, "image")
    assert media.sha256 == hashlib.sha256(picture.read_bytes()).hexdigest()[:12]
    assert media.thumbnail.startswith("data:image/jpeg;base64,")
    assert str(picture) not in json.dumps(card)  # The card never shows where a file is kept.


def test_a_reply_card_links_the_post_it_answers(x, person):
    fake, tool = x
    asked, _ = person
    x_accounts.add(fake.sign_in(*ROWBOT))
    original = fake.post("999", "@rowbot_ai What does Row-Bot do?")

    result = tool._x_post("reply", text="It keeps your data local.", tweet_id=original["id"])

    card = project_approval_context(asked[0])
    assert card["question"] == "Reply on X as @rowbot_ai?"
    assert card["x_post"]["target_url"] == f"https://x.com/i/status/{original['id']}"
    [reply] = fake.posted_by("111")
    assert reply["referenced_tweets"] == [{"type": "replied_to", "id": original["id"]}]
    assert f"Replied as @rowbot_ai to https://x.com/i/status/{original['id']}: " in result


def test_changing_a_file_after_the_card_never_sends_the_changed_file(x, person, tmp_path):
    fake, tool = x
    _, answers = person
    x_accounts.add(fake.sign_in(*ROWBOT))
    picture = _picture(tmp_path / "chart.png")
    answers.append(lambda _payload: (_picture(picture, "navy"), True)[1])  # Edited while the card waited.

    result = tool._x_post("post", text="This week's chart", media_paths=[str(picture)])

    assert result.endswith("chart.png changed after it was approved, so nothing was posted.")
    assert fake.posts == [] and _creates(fake) == 0


def test_any_change_to_the_account_text_or_media_is_another_question(x, tmp_path):
    fake, _ = x
    first, second = (x_accounts.add(fake.sign_in(*user)) for user in (ROWBOT, PERSONAL))
    picture = _picture(tmp_path / "a.png")
    media = x_posting.media_facts([str(picture)])
    bound = x_posting.payload_hash(first, "post", "Hello", "", media)

    assert x_posting.payload_hash(first, "post", "Hello", "", media) == bound
    assert x_posting.payload_hash(second, "post", "Hello", "", media) != bound
    assert x_posting.payload_hash(first, "post", "Hello!", "", media) != bound
    assert x_posting.payload_hash(first, "post", "Hello", "", x_posting.media_facts(
        [str(_picture(picture, "green"))])) != bound


def test_a_changed_file_asks_again_in_a_paused_conversation(x, monkeypatch, tmp_path):
    """LangGraph runs a paused tool again once the person answers: what changed meanwhile is a new card."""
    from typing import TypedDict

    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command

    fake, tool = x
    _mode(monkeypatch, "ask")
    x_accounts.add(fake.sign_in(*ROWBOT))
    picture = _picture(tmp_path / "chart.png")

    class State(TypedDict, total=False):
        result: str

    def call(_state):
        return {"result": tool._x_post("post", text="Chart", media_paths=[str(picture)])}
    graph = StateGraph(State)
    graph.add_node("x_post", call)
    graph.add_edge(START, "x_post")
    graph.add_edge("x_post", END)
    app = graph.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "n7"}}

    first = app.invoke({}, config)["__interrupt__"][0].value
    _picture(picture, "navy")
    again = app.invoke(Command(resume=True), config)

    assert "__interrupt__" in again and fake.posts == []  # The approval was for the other file.
    second = again["__interrupt__"][0].value
    assert first["x_post"]["media"][0]["sha256"] != second["x_post"]["media"][0]["sha256"]
    done = app.invoke(Command(resume=True), config)
    assert "\nPosted as @rowbot_ai: " in done["result"] and len(fake.posts) == 1


def test_the_person_edits_a_post_on_its_card_and_approves_the_edited_post(x, monkeypatch, tmp_path):
    """G2: an edit is never an approval: the paused tool asks again with the edited text and the files kept."""
    from typing import TypedDict

    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command

    fake, tool = x
    _mode(monkeypatch, "ask")
    x_accounts.add(fake.sign_in(*ROWBOT))
    first, second = _picture(tmp_path / "a.png"), _picture(tmp_path / "b.png", "navy")

    class State(TypedDict, total=False):
        result: str

    def call(_state):
        return {"result": tool._x_post("post", text="Draft launch post", media_paths=[str(first), str(second)])}
    graph = StateGraph(State)
    graph.add_node("x_post", call)
    graph.add_edge(START, "x_post")
    graph.add_edge("x_post", END)
    app = graph.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "g2"}}

    card = app.invoke({}, config)["__interrupt__"][0].value
    kept = card["x_post"]["media"][1]["sha256"]
    again = app.invoke(Command(resume={"x_post_edit": {"text": "Row-Bot 5.3.1 is out.", "media": [kept]}}), config)

    assert fake.posts == [], "an edit sends nothing"
    edited = again["__interrupt__"][0].value
    assert edited["x_post"]["text"] == "Row-Bot 5.3.1 is out." and edited["binding"] != card["binding"]
    assert [item["name"] for item in edited["x_post"]["media"]] == ["b.png"]
    assert edited["x_post"]["limit"] == 280  # The account's limit, read before the card.
    done = app.invoke(Command(resume=True), config)

    [sent] = fake.posts
    assert sent["text"].startswith("Row-Bot 5.3.1 is out.") and len(sent["media_ids"]) == 1
    assert "The person edited the post on its card, then approved it as edited:\nRow-Bot 5.3.1 is out." in done["result"]


def test_an_edit_too_long_for_the_account_sends_nothing(x, person):
    fake, tool = x
    asked, answers = person
    x_accounts.add(fake.sign_in(*ROWBOT))
    answers.append({"x_post_edit": {"text": "Longer. " * 40, "media": []}})

    result = tool._x_post("post", text="Short", account="@rowbot_ai")

    assert result.startswith("The person edited the post, but it can't go out as edited: That post is 320 characters")
    assert result.endswith("Nothing was sent to X.") and len(asked) == 1 and _creates(fake) == 0


# ── Who approves ─────────────────────────────────────────────────────────────


def test_under_auto_only_the_person_approves_a_post(x, person, monkeypatch):
    fake, tool = x
    asked, _ = person
    _mode(monkeypatch, "auto")
    from row_bot import approval_review
    monkeypatch.setattr(approval_review, "review", lambda *_a, **_k: pytest.fail("the reviewer decided a post"))
    x_accounts.add(fake.sign_in(*ROWBOT))

    result = tool._x_post("post", text="Reviewed by the person")

    [payload] = asked
    assert payload["review"] == {"decision": "ask", "reason": "Only you can approve what goes out on X in your name."}
    assert result.startswith("Approval: asked; approved by you. The reviewer asked you: Only you can approve")
    assert len(fake.posts) == 1


def test_a_denied_post_is_not_sent(x, person):
    fake, tool = x
    _, answers = person
    answers.append(False)
    x_accounts.add(fake.sign_in(*ROWBOT))
    result = tool._x_post("post", text="Not this one")
    assert result.startswith("Approval: asked; denied by you") and "Nothing was sent to X" in result
    assert fake.posts == [] and _creates(fake) == 0


def test_hands_off_is_the_persons_choice_and_block_sends_nothing(x, person, monkeypatch):
    fake, tool = x
    asked, _ = person
    x_accounts.add(fake.sign_in(*ROWBOT))
    _mode(monkeypatch, "full")
    allowed = tool._x_post("post", text="Hands-off post")
    _mode(monkeypatch, "block")
    blocked = tool._x_post("post", text="Blocked post")
    assert allowed.startswith("Approval: allowed by Hands-off\nPosted as @rowbot_ai: ")
    assert blocked.startswith("BLOCKED:") and asked == []
    assert [post["text"] for post in fake.posts] == ["Hands-off post"]


@pytest.mark.parametrize("approved", [False, True])
def test_always_ask_me_keeps_the_ask_under_hands_off(x, monkeypatch, approved):
    """A workflow's "Always ask me" rule (N9): the person answers on their own bound card, whatever the level;
    once they approve, the post goes out as approved without a second card."""
    from row_bot import agent

    fake, tool = x
    x_accounts.add(fake.sign_in(*ROWBOT))
    _mode(monkeypatch, "full")
    monkeypatch.setattr(x_posting, "_ask", lambda _payload: pytest.fail("asked twice"))
    asked = []

    def person(payload):
        asked.append(payload)
        return approval_gate.bound_answer(approved, payload["binding"])
    monkeypatch.setattr(approval_gate, "_interrupt", person)
    arguments = {"action": "post", "text": "Hi", "account": "@rowbot_ai"}
    rules = agent._current_person_rules_var.set({"items": ["x"]})
    try:
        refusal, binding = agent._person_rule_refusal("x_post", arguments, source="core", parent="x")
        with approval_gate.approved_by_person(binding):
            result = None if refusal else tool._x_post(**arguments)
    finally:
        agent._current_person_rules_var.reset(rules)

    assert [payload["person_only"] for payload in asked] == [True]
    if approved:
        assert refusal is None and "Posted as @rowbot_ai: " in result and len(fake.posts) == 1
    else:
        assert "didn't approve" in refusal and fake.posts == []


# ── The result and "never twice" ─────────────────────────────────────────────


def test_the_result_has_the_link_the_post_id_the_account_and_the_time(x, person):
    fake, tool = x
    x_accounts.add(fake.sign_in(*ROWBOT))
    result = tool._x_post("post", text="Results name everything")
    [post] = fake.posts
    assert result.splitlines()[1:] == [
        f"Posted as @rowbot_ai: https://x.com/rowbot_ai/status/{post['id']}",
        f"Post ID: {post['id']} · Account: @rowbot_ai (ID 111) · Time: " + result.rsplit("Time: ", 1)[1],
    ]
    assert result.rsplit("Time: ", 1)[1] == "2026-10-22T10:00:00Z"
    [operation] = x_posting.operations()
    assert (operation["status"], operation["post_id"], operation["account_id"]) == ("posted", post["id"], "111")


def test_a_delete_says_what_was_deleted(x, person):
    fake, tool = x
    asked, _ = person
    x_accounts.add(fake.sign_in(*ROWBOT))
    post = fake.post("111", "An old announcement")
    result = tool._x_post("delete", tweet_id=post["id"])
    card = project_approval_context(asked[0])
    assert card["question"] == "Delete a post from @rowbot_ai?"
    assert card["x_post"]["target_text"] == "An old announcement"
    assert f"Deleted as @rowbot_ai: post {post['id']} (https://x.com/rowbot_ai/status/{post['id']})" in result
    assert fake.posts == []


@pytest.mark.parametrize("lost", ["timeout", "disconnect"])
def test_when_x_does_not_confirm_row_bot_finds_the_post_instead_of_sending_again(x, person, lost, tmp_path):
    fake, tool = x
    x_accounts.add(fake.sign_in(*ROWBOT))
    fake.after_send = lost
    picture = _picture(tmp_path / "news.png")

    result = tool._x_post("post", text="Big news & more", media_paths=[str(picture)])

    [post] = fake.posts
    assert "Not sure it went through: X didn't confirm it. Row-Bot read @rowbot_ai's latest posts and found it." in result
    assert f"Posted as @rowbot_ai: https://x.com/rowbot_ai/status/{post['id']}" in result
    assert _creates(fake) == 1
    assert x_posting.operations()[-1]["status"] == "posted"


def test_when_the_post_is_not_there_it_says_it_did_not_post(x, person, monkeypatch):
    import httpx

    fake, tool = x
    x_accounts.add(fake.sign_in(*ROWBOT))
    real = fake.handle

    def unavailable(request):
        if (request.method, request.url.path) == ("POST", "/2/tweets"):
            fake.requests.append(("POST", "/2/tweets"))
            return httpx.Response(503, json={"title": "Service Unavailable"})  # Not created, but X can't say.
        return real(request)
    monkeypatch.setattr(x_accounts, "transport", httpx.MockTransport(unavailable))

    result = tool._x_post("post", text="Did this go out?")

    assert "Row-Bot read @rowbot_ai's latest posts and it isn't there: it didn't post." in result
    assert "trying again needs a new approval" in result
    assert fake.posts == [] and _creates(fake) == 1
    assert x_posting.operations()[-1]["status"] == "not_posted"


def test_an_unsettled_send_is_checked_before_the_same_post_goes_out_again(x, person):
    fake, tool = x
    x_accounts.add(fake.sign_in(*ROWBOT))
    fake.after_send, fake.fail_reads = "timeout", True

    first = tool._x_post("post", text="Once only")
    fake.fail_reads = False
    second = tool._x_post("post", text="Once only")  # The person approved the same post again.

    assert "couldn't read @rowbot_ai's latest posts to check" in first and "won't send it again by itself" in first
    assert second.splitlines()[1] == "An earlier attempt that X didn't confirm went through, so it wasn't sent again."
    assert len(fake.posts) == 1 and _creates(fake) == 1


def test_a_send_that_never_reached_x_is_a_plain_failure(x, person):
    fake, tool = x
    x_accounts.add(fake.sign_in(*ROWBOT))
    fake.before_send = "connect"
    result = tool._x_post("post", text="Offline")
    assert result.endswith("Nothing was posted as @rowbot_ai. Row-Bot couldn't reach X.")
    assert fake.posts == [] and x_posting.operations()[-1]["status"] == "failed"


# ── Tokens ───────────────────────────────────────────────────────────────────


def test_tokens_never_appear_in_results_cards_settings_or_logs(x, person, monkeypatch, caplog, tmp_path):
    from row_bot.application.client_account_oauth import read_account_auth

    fake, tool = x
    asked, _ = person
    caplog.set_level(logging.DEBUG)
    account_tokens.write("x", fake.sign_in(*ROWBOT, access="legacy-access"))
    x_accounts.add(fake.sign_in(*PERSONAL))
    outputs = [tool._x_accounts(), tool._x_post("post", text="One", account="@sidd"),
               tool._x_post("post", text="Two", account="@rowbot_ai", media_paths=[str(_picture(tmp_path / "p.png"))]),
               tool._x_read("mentions", account="@sidd"), tool.check_token_health()[1]]
    fake.after_send, fake.fail_reads = "timeout", True
    outputs.append(tool._x_post("post", text="Three", account="@sidd"))
    fake.users.clear()  # X turns every sign-in down.
    outputs += [tool._x_post("post", text="Four", account="@sidd"), tool.check_token_health()[1]]
    settings = json.dumps([x_accounts.summaries(), read_account_auth(account="x")])

    seen = "\n".join([*outputs, json.dumps(asked), settings, caplog.text, json.dumps(x_posting.operations()),
                      *(path.read_text(encoding="utf-8") for path in Path(x_accounts._path()).parent.glob("*.json"))])
    assert not [secret for secret in _SECRETS if secret in seen]


def test_an_unconfirmed_delete_is_checked_on_x_and_never_sent_twice(x, person):
    fake, tool = x
    x_accounts.add(fake.sign_in(*ROWBOT))
    post = fake.post("111", "An old announcement")
    fake.after_send = "disconnect"

    result = tool._x_post("delete", tweet_id=post["id"])

    assert "Not sure it went through: X didn't confirm it. Row-Bot looked on X and the post is gone." in result
    assert f"Deleted as @rowbot_ai: post {post['id']}" in result
    assert fake.posts == [] and fake.requests.count(("DELETE", f"/2/tweets/{post['id']}")) == 1


def test_an_old_slot_the_keychain_cannot_delete_yet_leaves_the_account_usable(x, monkeypatch):
    from row_bot import secret_store

    fake, tool = x
    account_tokens.write("x", fake.sign_in(*ROWBOT, access="legacy-access"))
    keyring = secret_store._backend_override
    real_delete = keyring.delete_password

    def refuse(service, name):
        if name == "accounts:x":
            raise RuntimeError("keyring unavailable")
        real_delete(service, name)
    monkeypatch.setattr(keyring, "delete_password", refuse)

    assert x_accounts.migrate().handle == "@rowbot_ai"
    assert account_tokens.read("x:111")["access_token"] == "legacy-access" and account_tokens.read("x") is not None
    monkeypatch.setattr(keyring, "delete_password", real_delete)
    assert x_accounts.migrate().handle == "@rowbot_ai"  # The next check removes it.
    assert account_tokens.read("x") is None and [a.handle for a in x_accounts.listed()] == ["@rowbot_ai"]



# ── Post length (5.3.1 G1) ───────────────────────────────────────────────────


def test_x_counts_a_posts_length_by_weight():
    assert x_posting.weighted_length("Row-Bot 5.3 is out") == 18
    assert x_posting.weighted_length("Read it: https://row-bot.ai/docs/a-very-long-path?with=query") == 9 + 23
    assert x_posting.weighted_length("東京") == 4 and x_posting.weighted_length("café — ok") == 9


def test_a_post_too_long_for_the_account_is_refused_before_the_card(x, person):
    fake, tool = x
    asked, _ = person
    x_accounts.add(fake.sign_in(*ROWBOT))  # No X subscription: 280 characters.
    text = "Row-Bot runs on your computer. " * 10  # 310 characters.

    refused = tool._x_post("post", text=text, account="@rowbot_ai")

    assert refused.startswith("That post is 310 characters as X counts them (each link counts 23); @rowbot_ai can "
                              "post up to 280 without an X subscription. Shorten it by 30")
    assert asked == [] and _creates(fake) == 0, "no card for a post X would refuse"
    listed = tool._x_accounts()
    assert "@rowbot_ai · Row-Bot (ID 111): signed in; posts up to 280 characters" in listed
    assert "Media: up to 4 pictures (5 MB each) or one GIF (15 MB) or one video (512 MB) per post." in listed


def test_a_subscribed_account_posts_a_long_post(x, person):
    fake, tool = x
    x_accounts.add(fake.sign_in(*ROWBOT, subscription="Premium"))

    sent = tool._x_post("post", text="A longer post about Row-Bot. " * 20, account="@rowbot_ai")

    assert "https://x.com/rowbot_ai/status/" in sent and _creates(fake) == 1



# ── Time slots and scheduled posts (5.3.1 G3) ────────────────────────────────


@pytest.fixture
def notices(monkeypatch):
    from row_bot import notifications
    said: list[tuple[str, str]] = []
    monkeypatch.setattr(notifications, "notify", lambda title, message, *a, **k: said.append((title, message)))
    return said


def test_a_slot_is_a_local_time_ahead_and_within_a_week():
    from datetime import datetime
    now = datetime(2026, 10, 28, 9, 0)

    assert x_posting.slot("13:00", now) == datetime(2026, 10, 28, 13, 0)
    assert x_posting.slot("08:30", now) == datetime(2026, 10, 29, 8, 30), "a time already past today is tomorrow's"
    assert x_posting.slot("2026-11-01T09:30", now) == datetime(2026, 11, 1, 9, 30)
    assert x_posting.slot("", now) is None
    for refused, words in (("2026-10-27T10:00", "has passed"), ("2026-11-05T09:01", "up to 7 days ahead"),
                           ("tomorrow", "a local time like 13:00")):
        with pytest.raises(x_posting.XPostError, match=words):
            x_posting.slot(refused, now)


def test_an_approved_post_with_a_slot_goes_out_at_its_time_once(x, person, notices, tmp_path):
    from datetime import datetime, timedelta
    fake, tool = x
    asked, _ = person
    x_accounts.add(fake.sign_in(*ROWBOT))
    picture = _picture(tmp_path / "chart.png")
    when = (datetime.now() + timedelta(hours=2)).replace(second=0, microsecond=0)

    result = tool._x_post("post", text="Tonight's chart", media_paths=[str(picture)], account="@rowbot_ai",
                          post_at=when.isoformat(timespec="minutes"))

    [card] = asked
    assert card["x_post"]["post_at"] == when.isoformat(timespec="minutes")
    assert card["binding"].endswith("@" + when.isoformat(timespec="minutes")), "the approval is for that time"
    assert f"Approved for its time: it goes out as @rowbot_ai at {when:%H:%M}" in result
    assert _creates(fake) == 0, "nothing is on X before its time"
    [waiting] = x_posting.scheduled_posts()
    assert (waiting["username"], waiting["text"], waiting["post_at"]) == ("rowbot_ai", "Tonight's chart",
                                                                          when.isoformat(timespec="minutes"))
    assert waiting["media"][0]["name"] == "chart.png" and str(tmp_path) not in str(waiting)

    assert x_posting.send_due(when - timedelta(minutes=1)) == []
    [sent] = x_posting.send_due(when)
    assert sent["status"] == "posted" and sent["url"].startswith("https://x.com/rowbot_ai/status/")
    assert x_posting.send_due(when + timedelta(minutes=1)) == [] and _creates(fake) == 1
    assert x_posting.scheduled_posts() == []
    assert notices == [("Posted on X as @rowbot_ai", sent["url"])]


def test_a_scheduled_post_never_goes_out_cancelled_changed_or_long_missed(x, person, notices, tmp_path):
    from datetime import datetime, timedelta
    fake, tool = x
    x_accounts.add(fake.sign_in(*ROWBOT))
    picture = _picture(tmp_path / "chart.png")
    when = (datetime.now() + timedelta(hours=1)).replace(second=0, microsecond=0)
    for text in ("Cancelled", "Changed file", "Missed"):
        tool._x_post("post", text=text, media_paths=[str(picture)] if text == "Changed file" else None,
                     account="@rowbot_ai", post_at=when.isoformat(timespec="minutes"))
    cancelled, changed, missed = x_posting.scheduled_posts()

    assert x_posting.cancel_scheduled(cancelled["id"]) and not x_posting.cancel_scheduled(cancelled["id"])
    _picture(picture, "navy")
    done = {item["id"]: item for item in x_posting.send_due(when)}
    assert done[changed["id"]]["status"] == "failed"
    assert done[changed["id"]]["result"] == "A file changed after you approved the post, so it wasn't posted."
    assert done[missed["id"]]["status"] == "posted"  # On time.

    tool._x_post("post", text="Late", account="@rowbot_ai", post_at=when.isoformat(timespec="minutes"))
    [late] = x_posting.send_due(when + timedelta(hours=13))
    assert late["status"] == "missed" and "wasn't posted. Approve it again to post it." in late["result"]
    assert cancelled["id"] not in done and _creates(fake) == 1
    assert [title for title, _ in notices] == ["A scheduled post as @rowbot_ai didn't go out", "Posted on X as @rowbot_ai",
                                               "A scheduled post as @rowbot_ai didn't go out"]


def test_the_person_edits_a_scheduled_post_and_it_goes_out_as_edited_at_its_time(x, person, notices, tmp_path):
    """Home › Review's Edit: the text and which files go change; the time and the approval stay."""
    from datetime import datetime, timedelta

    fake, tool = x
    x_accounts.add(fake.sign_in(*ROWBOT))
    chart, photo = _picture(tmp_path / "chart.png"), _picture(tmp_path / "photo.png", "navy")
    when = (datetime.now() + timedelta(hours=2)).replace(second=0, microsecond=0)
    tool._x_post("post", text="Tonight's chart", media_paths=[str(chart), str(photo)], account="@rowbot_ai",
                 post_at=when.isoformat(timespec="minutes"))
    [waiting] = x_posting.scheduled_posts()
    kept = waiting["media"][1]["sha256"]

    assert x_posting.edit_scheduled(waiting["id"], "Tonight's chart, with the photo", [kept])
    with pytest.raises(x_posting.XPostError, match="@rowbot_ai can post up to 280"):
        x_posting.edit_scheduled(waiting["id"], "x" * 281, [kept])
    with pytest.raises(x_posting.XPostError, match="Give the text to post"):
        x_posting.edit_scheduled(waiting["id"], "  ", [kept])
    assert not x_posting.edit_scheduled("xs-000000000000", "Nothing waits here", [])

    [edited] = x_posting.scheduled_posts()
    assert (edited["text"], edited["post_at"]) == ("Tonight's chart, with the photo", waiting["post_at"])
    assert [item["name"] for item in edited["media"]] == ["photo.png"]
    [sent] = x_posting.send_due(when)
    assert sent["status"] == "posted" and fake.posts[0]["text"].startswith("Tonight's chart, with the photo")
    assert len(fake.posts[0]["media_ids"]) == 1
    assert not x_posting.edit_scheduled(waiting["id"], "Too late", []), "a post that went out can't be edited"


def test_a_picture_goes_up_through_xs_v2_upload_and_a_video_waits_for_its_processing(x, person, tmp_path):
    """29 Oct: every picture post failed on v1.1's upload, which refuses an OAuth 2.0 sign-in."""
    fake, tool = x
    x_accounts.add(fake.sign_in(*ROWBOT))
    picture = _picture(tmp_path / "chart.png")
    video = tmp_path / "demo.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"0" * 4096)

    shown = tool._x_post("post", text="Tonight's chart", media_paths=[str(picture)], account="@rowbot_ai")
    clip = tool._x_post("post", text="The demo", media_paths=[str(video)], account="@rowbot_ai")

    assert "Posted as @rowbot_ai" in shown and "Posted as @rowbot_ai" in clip
    steps = [path for method, path in fake.requests if "/media/upload" in path]
    image_id, video_id = list(fake.uploaded)
    assert steps == ["/2/media/upload/initialize", f"/2/media/upload/{image_id}/append",
                     f"/2/media/upload/{image_id}/finalize",
                     "/2/media/upload/initialize", f"/2/media/upload/{video_id}/append",
                     f"/2/media/upload/{video_id}/finalize", "/2/media/upload"]  # The video's STATUS.
    assert fake.categories == {image_id: "tweet_image", video_id: "tweet_video"}
    assert [post["media_ids"] for post in fake.posts] == [[image_id], [video_id]]


@pytest.fixture
def chats(tmp_path, monkeypatch):
    """Two chats with real attachment storage in the test's folder, as Library › Media reads them; the tool runs in
    the second."""
    import sqlite3

    from row_bot import threads, tool_outputs

    monkeypatch.setattr(threads, "DB_PATH", str(tmp_path / "threads.db"))
    monkeypatch.setattr(threads, "_MEDIA_DIR", tmp_path / "media")
    threads._ensure_thread_db()
    with sqlite3.connect(threads.DB_PATH) as connection:
        connection.executemany("INSERT INTO thread_meta(thread_id,name) VALUES(?,?)",
                               [("growth-assets", "Growth | Assets"), ("growth-x", "Growth | X content")])
    monkeypatch.setattr(tool_outputs, "current_thread_id", lambda: "growth-x")


def test_a_post_attaches_a_library_item_by_its_reference_or_a_chat_file_by_its_name(x, person, chats, tmp_path):
    """29 Oct: the growth run's screenshots were in Library › Media, but x_post took only file paths."""
    from row_bot.application.attachments import register_attachment

    fake, tool = x
    asked, _ = person
    x_accounts.add(fake.sign_in(*ROWBOT))
    screenshot = _picture(tmp_path / "a.png").read_bytes()
    card = _picture(tmp_path / "b.png", "navy").read_bytes()
    shot = register_attachment("growth-assets", "screenshot-2026-10-29-115700.png", screenshot)
    register_attachment("growth-x", "launch-card.png", b"an older card")
    # Each file in a chat has its own name: the second card is kept as launch-card-2.png.
    assert register_attachment("growth-x", "launch-card.png", card)["name"] == "launch-card-2.png"

    by_ref = tool._x_post("post", text="Memory that lasts", media_paths=[shot["attachment_ref"]],
                          account="@rowbot_ai")
    by_name = tool._x_post("post", text="Launch", media_paths=["launch-card-2.png"], account="@rowbot_ai")

    assert "Posted as @rowbot_ai" in by_ref and "Posted as @rowbot_ai" in by_name
    shown = [payload["x_post"]["media"] for payload in asked]
    assert [(item["name"], item["sha256"]) for [item] in shown] == [
        ("screenshot-2026-10-29-115700.png", hashlib.sha256(screenshot).hexdigest()[:12]),
        ("launch-card-2.png", hashlib.sha256(card).hexdigest()[:12])]
    assert [post["media_ids"] for post in fake.posts] == [[media_id] for media_id in fake.uploaded]
    assert str(tmp_path) not in json.dumps(asked)

    missing = tool._x_post("post", text="Nothing", media_paths=["chart.png"], account="@rowbot_ai")
    assert missing == ("chart.png isn't a file Row-Bot can read, a file in this chat or a Library › Media item "
                       "(media_find gives its reference), so nothing was posted.")
    assert len(asked) == 2 and _creates(fake) == 2


def test_a_scheduled_post_finds_its_library_item_again_at_its_time(x, person, notices, chats, tmp_path):
    from datetime import datetime, timedelta

    from row_bot.application.attachments import register_attachment

    fake, tool = x
    x_accounts.add(fake.sign_in(*ROWBOT))
    shot = register_attachment("growth-assets", "screenshot-2026-10-29-115700.png",
                               _picture(tmp_path / "a.png").read_bytes())
    when = (datetime.now() + timedelta(hours=1)).replace(second=0, microsecond=0)

    tool._x_post("post", text="At noon", media_paths=[shot["attachment_ref"]], account="@rowbot_ai",
                 post_at=when.isoformat(timespec="minutes"))

    [waiting] = x_posting.scheduled_posts()
    assert [item["name"] for item in waiting["media"]] == ["screenshot-2026-10-29-115700.png"]
    assert str(tmp_path) not in json.dumps(waiting)
    [sent] = x_posting.send_due(when)
    assert sent["status"] == "posted" and fake.posts[0]["media_ids"] == list(fake.uploaded)


def test_an_account_connected_before_pictures_is_asked_to_reconnect_before_any_card(x, person, tmp_path):
    from tests.helpers.fake_x_api import OLD_SCOPE

    fake, tool = x
    asked, _ = person
    x_accounts.add(fake.sign_in(*ROWBOT, scope=OLD_SCOPE))

    refused = tool._x_post("post", text="Tonight's chart", media_paths=[str(_picture(tmp_path / "c.png"))],
                           account="@rowbot_ai")

    assert refused.startswith("@rowbot_ai was connected before Row-Bot could post pictures and videos. Sign in to "
                              "it once more in Settings › Apps › X")
    assert "Nothing was sent to X." in refused and asked == []
    assert not [path for _, path in fake.requests if "media" in path]
    # Text alone still goes; signing in again (with media.write now) lets the picture go too.
    assert "Posted as @rowbot_ai" in tool._x_post("post", text="Words only", account="@rowbot_ai")
    x_accounts.add(fake.sign_in(*ROWBOT, access="access-rowbot-again"))
    assert "Posted as @rowbot_ai" in tool._x_post("post", text="Now with it", account="@rowbot_ai",
                                                   media_paths=[str(_picture(tmp_path / "c.png"))])


def test_a_refused_upload_says_xs_own_reason(x, person, tmp_path, monkeypatch):
    import httpx

    fake, tool = x
    x_accounts.add(fake.sign_in(*ROWBOT))
    real = fake.handle

    def refuse(request):
        if str(request.url).endswith("/2/media/upload/initialize"):
            return httpx.Response(400, json={"title": "Invalid Request", "detail": "media_type is not supported"})
        return real(request)

    monkeypatch.setattr(x_accounts, "transport", httpx.MockTransport(refuse))
    result = tool._x_post("post", text="Chart", media_paths=[str(_picture(tmp_path / "c.png"))])

    assert ("Uploading c.png to X failed (start), so nothing was posted. X refused it (400): media_type is not "
            "supported.") in result
    assert fake.posts == []


def test_a_scheduled_post_that_didnt_go_out_stays_in_review_with_why_and_goes_when_tried_again(
        x, person, notices, tmp_path):
    from datetime import datetime, timedelta
    from tests.helpers.fake_x_api import OLD_SCOPE

    fake, tool = x
    x_accounts.add(fake.sign_in(*ROWBOT))
    when = (datetime.now() + timedelta(hours=1)).replace(second=0, microsecond=0)
    for text in ("With its picture", "Dismissed"):
        tool._x_post("post", text=text, account="@rowbot_ai", post_at=when.isoformat(timespec="minutes"),
                     media_paths=[str(_picture(tmp_path / f"{text[:4]}.png"))])
    # The sign-in loses media.write before the slot (an account from before 5.3.1, say).
    x_accounts.add(fake.sign_in(*ROWBOT, access="access-old", scope=OLD_SCOPE))
    x_posting.send_due(when)

    failed, dismissed = x_posting.scheduled_posts()
    assert (failed["status"], failed["text"]) == ("failed", "With its picture")
    assert failed["reason"].startswith("Nothing was sent: @rowbot_ai was connected before Row-Bot could post")
    assert notices[0][1].endswith("It's in Home › Review, where you can try it again.")
    assert x_posting.cancel_scheduled(dismissed["id"])  # Dismiss: it leaves Review.
    assert [item["id"] for item in x_posting.scheduled_posts()] == [failed["id"]]

    # Post without the picture: the text goes now.
    assert x_posting.retry_scheduled(failed["id"], without_media=True)
    x_posting.send_due(datetime.now() + timedelta(minutes=1))
    [post] = fake.posts
    assert post["text"] == "With its picture" and post["media_ids"] == []
    assert x_posting.scheduled_posts() == [] and not x_posting.retry_scheduled(failed["id"])


def test_a_delete_has_no_time_slot(x, person):
    fake, tool = x
    asked, _ = person
    x_accounts.add(fake.sign_in(*ROWBOT))
    refused = tool._x_post("delete", tweet_id="123", account="@rowbot_ai", post_at="13:00")
    assert refused == "A delete happens at once; leave post_at out." and asked == []


# ── Numbers (5.3.1 G7) ───────────────────────────────────────────────────────


def test_an_accounts_recent_posts_come_with_their_numbers_and_totals(x):
    fake, tool = x
    x_accounts.add(fake.sign_in(*ROWBOT))
    for text, views, likes in (("Pages are out", 1200, 30), ("Workflows run as chats", 340, 1)):
        fake.post("111", text)["public_metrics"] = {"impression_count": views, "like_count": likes,
                                                    "retweet_count": 2, "reply_count": 1, "quote_count": 0,
                                                    "bookmark_count": 5}

    read = tool._x_read("timeline", account="@rowbot_ai")

    assert ("Together, these 2 posts: 1,540 views · 31 likes · 4 reposts · 2 replies · 0 quotes · 10 bookmarks. "
            "Most viewed: ") in read
    assert "1,200 views · 30 likes · 2 reposts · 1 reply · 0 quotes · 5 bookmarks" in read
    assert "340 views · 1 like · 2 reposts · 1 reply" in read


# ── X's reply rule (23 Feb 2026) ─────────────────────────────────────────────
# X's self-serve API takes a reply or quote only where the post's author summoned the account: mentioned it, quoted
# one of its posts, or is the account. Row-Bot checks by the posting account's own user ID, before any card.


def test_a_reply_or_quote_asks_only_where_the_posts_author_summoned_that_account(x, person):
    fake, tool = x
    asked, _ = person
    for user in (ROWBOT, PERSONAL):
        x_accounts.add(fake.sign_in(*user))
    for_sidd = fake.post("999", "@sidd what do you make of local-first apps?")
    launch = fake.post("111", "Row-Bot 5.3 is out")
    quoting = fake.post("999", "Look at this", references=[{"type": "quoted", "id": launch["id"]}])

    allowed = [tool._x_post("reply", text="They keep your data yours.", tweet_id=for_sidd["id"], account="@sidd"),
               tool._x_post("quote", text="Thanks for sharing!", tweet_id=quoting["id"], account="@rowbot_ai"),
               tool._x_post("reply", text="Release notes inside.", tweet_id=launch["id"], account="@rowbot_ai")]
    assert len(asked) == 3 and all("https://x.com/" in result for result in allowed)
    assert [post["text"] for post in fake.posted_by("222")] == ["They keep your data yours."]

    blocked = {"reply as rowbot": tool._x_post("reply", text="Row-Bot does that too.", tweet_id=for_sidd["id"],
                                               account="@rowbot_ai"),
               "quote as sidd": tool._x_post("quote", text="Worth a read.", tweet_id=quoting["id"], account="@sidd")}

    assert len(asked) == 3 and _creates(fake) == 3, "an unsummoned reply or quote never reaches X"
    reply, quote = blocked.values()
    assert reply.startswith("Nothing was sent to X: X allows this reply only from its own app or website. Through "
                            "other apps, X allows a reply or quote only when the post's author mentioned @rowbot_ai "
                            "or quoted one of @rowbot_ai's posts.")
    assert f"Reply to it there: https://x.com/i/status/{for_sidd['id']}" in reply
    assert "The draft, ready to copy:\nRow-Bot does that too." in reply
    assert "mentioned @sidd" in quote and f"Quote it there: https://x.com/i/status/{quoting['id']}" in quote
    assert "The draft, ready to copy:\nWorth a read." in quote


def test_an_unsummoned_reply_is_never_scheduled(x, person):
    from datetime import datetime, timedelta

    fake, tool = x
    asked, _ = person
    x_accounts.add(fake.sign_in(*ROWBOT))
    question = fake.post("999", "Which local AI assistant should I try?")
    when = (datetime.now() + timedelta(hours=2)).replace(second=0, microsecond=0)

    result = tool._x_post("reply", text="Try Row-Bot.", tweet_id=question["id"], account="@rowbot_ai",
                          post_at=when.isoformat(timespec="minutes"))

    assert asked == [] and x_posting.scheduled_posts() == []
    assert "Nothing was sent to X" in result and "The draft, ready to copy:\nTry Row-Bot." in result


def test_a_reply_x_refuses_by_its_rule_gives_the_draft_and_is_not_offered_again(x, person):
    """Row-Bot couldn't read the post first (X down for reads), so it asked; X then refused the reply."""
    fake, tool = x
    asked, _ = person
    x_accounts.add(fake.sign_in(*ROWBOT))
    question = fake.post("999", "Which local AI assistant should I try?")
    fake.fail_reads = True

    result = tool._x_post("reply", text="Try Row-Bot.", tweet_id=question["id"], account="@rowbot_ai")

    assert len(asked) == 1 and _creates(fake) == 1 and fake.posted_by("111") == []
    assert "Nothing was posted as @rowbot_ai. X refused it: X allows this reply only from its own app" in result
    assert f"https://x.com/i/status/{question['id']}" in result and "ready to copy:\nTry Row-Bot." in result
    assert [item["status"] for item in x_posting.operations()] == ["failed"], "nothing left to check or resend"


def test_a_scheduled_reply_x_wont_take_settles_with_its_draft_and_no_try_again(x, notices):
    """Replies scheduled before Row-Bot checked X's rule: checked at their time, or refused by X if it couldn't."""
    from datetime import datetime, timedelta

    fake, _ = x
    rowbot, sidd = (x_accounts.add(fake.sign_in(*user)) for user in (ROWBOT, PERSONAL))
    question = fake.post("999", "@sidd which local AI assistant should I try?")
    when = (datetime.now() + timedelta(hours=1)).replace(second=0, microsecond=0)
    checked = x_posting.schedule(rowbot, "reply", "Try Row-Bot.", question["id"], [], when)
    welcome = x_posting.schedule(sidd, "reply", "Row-Bot, I'd say.", question["id"], [], when)

    done = {item["id"]: item for item in x_posting.send_due(when)}
    assert done[welcome["id"]]["status"] == "posted", "@sidd was mentioned"
    assert done[checked["id"]]["status"] == "by_hand" and _creates(fake) == 1, "@rowbot_ai's never reached X"

    refused = x_posting.schedule(rowbot, "quote", "Good question.", question["id"], [], when)
    fake.fail_reads = True  # Row-Bot can't check first: X refuses it.
    [sent] = x_posting.send_due(when)
    assert sent["status"] == "by_hand" and _creates(fake) == 2 and fake.posted_by("111") == []
    fake.fail_reads = False

    shown = {item["id"]: item for item in x_posting.scheduled_posts()}
    assert set(shown) == {checked["id"], refused["id"]}
    assert shown[checked["id"]]["reason"].startswith("Nothing was sent to X: X allows this reply only from its own")
    assert shown[refused["id"]]["reason"].startswith("Nothing was posted as @rowbot_ai. X refused it: X allows this "
                                                     "quote only from its own app or website.")
    assert (shown[refused["id"]]["text"], shown[refused["id"]]["target_url"]) == (
        "Good question.", f"https://x.com/i/status/{question['id']}")
    assert not x_posting.retry_scheduled(refused["id"]), "trying again would fail again"
    assert notices[-1] == ("A scheduled post as @rowbot_ai didn't go out",
                           f"{shown[refused['id']]['reason']} It's in Home › Review, with the draft to copy.")
    assert x_posting.cancel_scheduled(refused["id"]) and refused["id"] not in {
        item["id"] for item in x_posting.scheduled_posts()}
