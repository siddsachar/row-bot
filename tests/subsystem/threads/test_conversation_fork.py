"""Edit and resend, Fork from here (B5): a new conversation from an earlier message; the original is only read."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

pytestmark = pytest.mark.subsystem


@pytest.fixture
def threads(tmp_path, reload_for_data_dir, monkeypatch):
    from row_bot.designer import storage as artifacts
    from row_bot.designer.state import DesignerProject
    from row_bot.developer import storage as workspaces
    from row_bot.developer.state import DeveloperWorkspace

    module = reload_for_data_dir(tmp_path / "data", "row_bot.threads")[0]
    monkeypatch.setattr(workspaces, "DEVELOPER_DIR", tmp_path / "developer")
    monkeypatch.setattr(workspaces, "WORKSPACES_PATH", tmp_path / "developer" / "workspaces.json")
    monkeypatch.setattr(artifacts, "DESIGNER_DIR", tmp_path / "designer")
    monkeypatch.setattr(artifacts, "PROJECTS_DIR", tmp_path / "designer" / "projects")
    monkeypatch.setattr(artifacts, "ASSETS_DIR", tmp_path / "designer" / "assets")
    monkeypatch.setattr(artifacts, "REFERENCES_DIR", tmp_path / "designer" / "references")
    workspaces.save_workspace(DeveloperWorkspace(id="workspace", name="Workspace", path=str(tmp_path)))
    artifacts.save_project(DesignerProject(id="artifact", name="Artifact"))
    return module


def _history(*, handle: str = "", attachment: dict | None = None) -> list:
    """Three turns; the second runs a tool, and the person's second message has its turn context."""
    files = {"platform_public_content": "Here are my notes", "platform_attachments": [attachment]} if attachment else {}
    return [
        HumanMessage(id="h1", content="Plan a trip" + (f" [{attachment['attachment_ref']}]" if attachment else ""),
                     additional_kwargs=files),
        AIMessage(id="a1", content="Where to?"),
        HumanMessage(id="h2", content="Lisbon, in May"),
        HumanMessage(id="h2-context", content="<row_bot_turn_context>…</row_bot_turn_context>",
                     additional_kwargs={"row_bot_turn_context": {"turn_id": "turn-2"}}),
        AIMessage(id="a2-call", content="", tool_calls=[{"id": "call-1", "name": "web_search", "args": {"q": "Lisbon"}}]),
        ToolMessage(id="t2", tool_call_id="call-1", name="web_search",
                    content=f"Lisbon in May: warm. {('Full result: ' + handle) if handle else ''}"),
        AIMessage(id="a2", content="May is a good month for Lisbon."),
        HumanMessage(id="h3", content="Book it"),
        AIMessage(id="a3", content="Booked."),
    ]


def _source(threads, *, messages: list | None = None, **options) -> str:
    source = threads.create_thread("Trip", thread_id="source-chat", approval_mode=options.pop("approval_mode", "approve"),
                                   **options)
    assert threads.append_checkpoint_messages(source, messages or _history())
    return source


def _ids(threads, conversation: str) -> list[str]:
    return [message.id for message in threads.get_latest_checkpoint_messages(conversation)]


def _stored(threads, conversation: str) -> tuple:
    """Everything saved for a conversation: its metadata row and every checkpoint and write row."""
    with closing(sqlite3.connect(threads.DB_PATH)) as conn:
        return tuple(tuple(conn.execute(f"SELECT * FROM {table} WHERE thread_id = ? ORDER BY rowid",
                                        (conversation,)).fetchall())
                     for table in ("thread_meta", "checkpoints", "writes"))


def test_edit_keeps_what_came_before_the_message_and_not_it_or_its_turn_context(threads):
    source = _source(threads)

    fork = threads.fork_conversation(source, at_message_id="h2", include_message=False)

    assert _ids(threads, fork.conversation_id) == ["h1", "a1"]
    assert fork.message_count == 2


@pytest.mark.parametrize("reply", ["a2-call", "a2"])
def test_fork_keeps_the_whole_turn_with_its_tool_results(threads, reply):
    source = _source(threads)

    fork = threads.fork_conversation(source, at_message_id=reply, include_message=True)

    assert _ids(threads, fork.conversation_id) == ["h1", "a1", "h2", "h2-context", "a2-call", "t2", "a2"]


def test_the_source_is_byte_identical_after_both_kinds_of_fork(threads):
    source = _source(threads)
    before = _stored(threads, source)

    threads.fork_conversation(source, at_message_id="h3", include_message=False)
    threads.fork_conversation(source, at_message_id="a1", include_message=True)

    assert _stored(threads, source) == before


@pytest.mark.parametrize(("message", "include", "code"), [
    ("h2-context", False, "invalid_command"),  # a turn context is not the person's message
    ("h2-context", True, "invalid_command"),
    ("t2", True, "invalid_command"),  # a tool result is not a reply
    ("a1", False, "invalid_command"),  # only the person's own message is edited
    ("h1", True, "invalid_command"),  # only a reply is forked after
    ("missing", True, "not_found"),
])
def test_a_cut_only_at_a_person_message_or_a_reply(threads, message, include, code):
    source = _source(threads)

    with pytest.raises(threads.ForkError) as refused:
        threads.fork_conversation(source, at_message_id=message, include_message=include)

    assert refused.value.code == code
    assert len(threads._list_threads()) == 1


def test_never_cut_after_a_step_still_waiting_on_its_result(threads):
    waiting = _history()[:5]  # a2-call asked for a tool that hasn't answered (an approval being asked)
    source = _source(threads, messages=waiting)

    with pytest.raises(threads.ForkError, match="invalid_command"):
        threads.fork_conversation(source, at_message_id="a2-call", include_message=True)


def test_an_edit_of_the_first_message_starts_an_empty_conversation(threads):
    source = _source(threads)

    fork = threads.fork_conversation(source, at_message_id="h1", include_message=False)

    assert _ids(threads, fork.conversation_id) == []
    assert threads._thread_exists(fork.conversation_id)


def test_a_summary_inside_the_kept_part_stays_valid_and_one_past_it_is_dropped(threads):
    source = _source(threads)
    messages = threads.get_latest_checkpoint_messages(source)
    with closing(sqlite3.connect(threads.DB_PATH)) as conn, conn:
        for column, mode, count in (("summary_state_json", "agent", 2), ("chat_only_summary_state_json", "chat_only", 7)):
            conn.execute(f"UPDATE thread_meta SET {column} = ? WHERE thread_id = ?", (json.dumps({
                "schema_version": 1, "mode": mode, "summary": f"{mode} summary", "boundary_message_count": count,
                "boundary_digest": threads.context_boundary_digest(messages, count, mode)}), source))
    assert threads.load_validated_summary_state(source, "chat_only")

    fork = threads.fork_conversation(source, at_message_id="h3", include_message=False).conversation_id

    assert threads.load_validated_summary_state(fork, "agent")["summary"] == "agent summary"
    whole = threads.fork_conversation(source, at_message_id="a2", include_message=True).conversation_id
    assert threads.load_validated_summary_state(whole, "chat_only")["summary"] == "chat_only summary"
    early = threads.fork_conversation(source, at_message_id="h2", include_message=False).conversation_id
    assert threads.load_validated_summary_state(early, "agent")["summary"] == "agent summary"
    assert threads.load_validated_summary_state(early, "chat_only") is None


def test_a_summary_prepared_ahead_goes_with_a_fork_that_keeps_what_it_summarizes(threads):
    source = _source(threads)
    messages = threads.get_latest_checkpoint_messages(source)
    assert threads.save_prepared_summary_cas(source, {
        "schema_version": 1, "mode": "agent", "summary": "prepared summary", "boundary_message_count": 7,
        "boundary_digest": threads.context_boundary_digest(messages, 7, "agent"), "base_summary_digest": "",
    }, messages=messages)

    kept = threads.fork_conversation(source, at_message_id="h3", include_message=False).conversation_id
    cut = threads.fork_conversation(source, at_message_id="h2", include_message=False).conversation_id

    assert threads.load_prepared_summary(kept, "agent")["summary"] == "prepared summary"
    assert threads.load_prepared_summary(cut, "agent") is None
    assert threads.load_prepared_summary(source, "agent")["summary"] == "prepared summary"


def test_hygiene_marks_are_kept_by_their_anchors_and_reset_past_the_cut(threads):
    from row_bot import history_hygiene

    source = _source(threads)
    messages = threads.get_latest_checkpoint_messages(source)
    assert history_hygiene.save_marks(source, messages, history_hygiene.Marks(mask=1, browser=5, jumps=2))

    fork = threads.fork_conversation(source, at_message_id="h3", include_message=False).conversation_id
    early = threads.fork_conversation(source, at_message_id="h2", include_message=False).conversation_id

    assert history_hygiene.load_marks(fork, threads.get_latest_checkpoint_messages(fork)) == history_hygiene.Marks(1, 5, 2)
    assert history_hygiene.load_marks(early, threads.get_latest_checkpoint_messages(early)) == history_hygiene.Marks(1, 0, 2)


def test_memory_extraction_reads_nothing_already_in_the_fork(threads):
    from row_bot import memory_extraction

    source = _source(threads)
    fork = threads.fork_conversation(source, at_message_id="h3", include_message=False).conversation_id
    with closing(sqlite3.connect(threads.DB_PATH)) as conn:
        raw, updated, created = conn.execute(
            "SELECT memory_extraction_json, updated_at, created_at FROM thread_meta WHERE thread_id = ?", (fork,)
        ).fetchone()

    assert memory_extraction._unread(fork, updated, memory_extraction._parse_watermark(raw), "", created) is None
    threads.append_checkpoint_messages(fork, [HumanMessage(id="h-new", content="I prefer trains to planes")])
    unread = memory_extraction._unread(fork, updated, memory_extraction._parse_watermark(raw), "", created)
    assert [message["content"] for message in unread.messages[unread.start:]] == ["I prefer trains to planes"]


def test_the_fork_shares_the_code_folder_and_design(threads):
    from row_bot import conversation_resources as resources

    source = _source(threads, thread_type="code")
    resources.bind(source, "workspace", "workspace", expected_revision=0, role="primary")
    resources.bind(source, "artifact", "artifact", expected_revision=1)

    fork = threads.fork_conversation(source, at_message_id="a1", include_message=True).conversation_id

    assert {(item.kind, item.resource_id) for item in resources.list_bindings(fork).bindings} == {
        ("workspace", "workspace"), ("artifact", "artifact")}
    assert threads._get_thread_developer_workspace(fork) == "workspace"


def test_files_the_kept_messages_name_are_copied_under_the_fork(threads):
    from row_bot.application import attachments
    from row_bot.developer import tool_context
    from row_bot.tool_outputs import read_tool_output, spill

    source = threads.create_thread("Trip", thread_id="source-chat", approval_mode="approve")
    named = attachments.register_attachment(source, "notes.txt", b"Lisbon notes")
    unnamed = attachments.register_attachment(source, "later.txt", b"Named only after the cut")
    handle = spill("The whole search result " * 10, thread_id=source, source="web_search")
    history = _history(handle=handle, attachment=named)
    history[-2] = HumanMessage(id="h3", content=f"Book it [{unnamed['attachment_ref']}]")
    assert threads.append_checkpoint_messages(source, history)

    fork = threads.fork_conversation(source, at_message_id="a2", include_message=True).conversation_id

    first = threads.get_latest_checkpoint_messages(fork)[0]
    copied = first.additional_kwargs["platform_attachments"][0]["attachment_ref"]
    assert copied.startswith(fork + ":") and copied in first.content and source not in first.content
    assert attachments.read_attachment(copied)[1] == b"Lisbon notes"
    # Only what the kept messages name: the fork can't read the source's other files.
    assert [item["name"] for item in attachments.list_conversation_files(fork)] == ["notes.txt"]
    tokens = tool_context.set_context(thread_id=fork)
    try:
        assert "The whole search result" in read_tool_output(handle)
    finally:
        tool_context.reset_context(tokens)
    # The source keeps its own files.
    assert attachments.read_attachment(named["attachment_ref"])[1] == b"Lisbon notes"


def test_edit_returns_the_edited_messages_files_copied_for_the_new_message(threads):
    from row_bot.application import attachments

    source = threads.create_thread("Trip", thread_id="source-chat", approval_mode="approve")
    named = attachments.register_attachment(source, "notes.txt", b"Lisbon notes")
    assert threads.append_checkpoint_messages(source, _history(attachment=named))

    fork = threads.fork_conversation(source, at_message_id="h1", include_message=False)

    [draft] = fork.draft_attachments
    assert draft["attachment_ref"] == f"{fork.conversation_id}:{named['attachment_ref'].split(':', 1)[1]}"
    assert draft["name"] == "notes.txt"
    assert attachments.read_attachment(draft["attachment_ref"])[1] == b"Lisbon notes"


def test_an_incognito_source_makes_an_incognito_fork(threads):
    source = _source(threads, incognito=True)

    fork = threads.fork_conversation(source, at_message_id="a2", include_message=True).conversation_id

    assert threads.incognito_state(fork).incognito is True


def test_what_was_said_incognito_stays_unread_in_the_fork(threads):
    source = _source(threads, messages=_history()[:2], incognito=True)
    threads.set_thread_incognito(source, False)  # turned off after a1: h1 and a1 are never read for memory
    assert threads.append_checkpoint_messages(source, _history()[2:])

    fork = threads.fork_conversation(source, at_message_id="h3", include_message=False).conversation_id

    kept = threads.get_latest_checkpoint_messages(fork)
    assert threads.incognito_state(fork).readable_from(kept) == 2


def test_the_fork_runs_at_the_sources_approval_level_not_the_last_chosen_one(threads):
    from row_bot import full_autonomy

    source = _source(threads, approval_mode="approve")
    full_autonomy.remember_level("auto")

    fork = threads.fork_conversation(source, at_message_id="a1", include_message=True).conversation_id

    assert threads._get_thread_approval_mode(fork) == "approve"


def test_settings_follow_and_the_fork_records_where_it_came_from(threads):
    source = _source(threads, model_override="model:fixture::model")
    threads.set_thread_skills_override(source, ["writer"])
    threads.set_thread_app(source, "builtin:web", False)

    fork = threads.fork_conversation(source, at_message_id="a1", include_message=True).conversation_id

    assert threads._get_thread_model_override(fork) == "model:fixture::model"
    assert threads.get_thread_skills_override(fork) == ["writer"]
    assert threads.get_thread_apps_off(fork) == ["builtin:web"]
    with closing(sqlite3.connect(threads.DB_PATH)) as conn:
        assert conn.execute("SELECT name, forked_from_id, forked_at_message_id FROM thread_meta WHERE thread_id = ?",
                            (fork,)).fetchone() == ("Trip (fork)", source, "a1")


def test_goals_stay_with_the_source(threads):
    from row_bot import goals

    source = _source(threads)
    goals.start_goal(source, "Plan the whole trip")

    fork = threads.fork_conversation(source, at_message_id="a1", include_message=True).conversation_id

    assert goals.get_current_goal(fork) is None
    assert goals.get_current_goal(source)["objective"] == "Plan the whole trip"
