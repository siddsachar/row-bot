from types import SimpleNamespace

import pytest


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    path = tmp_path / "data"
    path.mkdir()
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(path))
    yield path


def test_provider_qualified_cloud_defaults_validate_after_refresh(monkeypatch):
    import row_bot.models as models
    import row_bot.providers.codex as codex

    with models._cloud_cache_lock:
        models._cloud_model_cache.clear()
        models._cloud_model_cache["gpt-4.1"] = {"provider": "openai"}

    assert models._cloud_model_available_after_refresh("model:openai:gpt-4.1")

    monkeypatch.setattr(
        codex,
        "list_codex_model_infos",
        lambda: [SimpleNamespace(model_id="gpt-5.5")],
    )

    assert models._cloud_model_available_after_refresh("model:codex:gpt-5.5")
    assert not models._cloud_model_available_after_refresh("model:codex:not-present")


def test_stability_suppresses_benign_windows_proactor_reset():
    import row_bot.stability as stability

    context = {
        "handle": "<Handle _ProactorBasePipeTransport._call_connection_lost()>",
    }
    benign = ConnectionResetError(
        10054,
        "An existing connection was forcibly closed by the remote host",
    )

    assert stability._is_benign_asyncio_connection_reset(
        "Exception in callback _ProactorBasePipeTransport._call_connection_lost()",
        benign,
        context,
    )
    assert not stability._is_benign_asyncio_connection_reset(
        "Exception in callback something_else()",
        ValueError("boom"),
        {},
    )


def test_checkpoint_cleanup_keeps_latest_and_prunes_old(data_dir, monkeypatch, reload_for_data_dir):
    import sqlite3

    (threads,) = reload_for_data_dir(data_dir, "row_bot.threads")
    monkeypatch.setattr(threads, "DB_PATH", str(data_dir / "threads.db"))
    threads._init_thread_db(raise_on_error=True)

    with sqlite3.connect(threads.DB_PATH) as conn:
        conn.execute(
            "CREATE TABLE checkpoints (thread_id TEXT, checkpoint_ns TEXT, checkpoint_id TEXT)"
        )
        conn.execute(
            "CREATE TABLE writes (thread_id TEXT, checkpoint_ns TEXT, checkpoint_id TEXT, value TEXT)"
        )
        conn.execute(
            "INSERT INTO thread_meta (thread_id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("old-thread", "Old", "2000-01-01T00:00:00", "2000-01-01T00:00:00"),
        )
        for idx in range(125):  # several of the pruning's batches
            cid = f"cp-{idx}"
            conn.execute("INSERT INTO checkpoints VALUES (?, ?, ?)", ("old-thread", "", cid))
            conn.execute("INSERT INTO writes VALUES (?, ?, ?, ?)", ("old-thread", "", cid, "x"))
        conn.commit()

    stats = threads.cleanup_old_checkpoints(keep_per_thread=2, min_age_minutes=0)

    with sqlite3.connect(threads.DB_PATH) as conn:
        checkpoint_ids = [
            row[0]
            for row in conn.execute(
                "SELECT checkpoint_id FROM checkpoints ORDER BY rowid"
            ).fetchall()
        ]
        write_ids = [
            row[0]
            for row in conn.execute(
                "SELECT checkpoint_id FROM writes ORDER BY rowid"
            ).fetchall()
        ]

    assert stats["checkpoints"] == 123
    assert stats["writes"] == 123
    assert checkpoint_ids == ["cp-123", "cp-124"]
    assert write_ids == ["cp-123", "cp-124"]


def test_a_long_run_keeps_only_its_newest_checkpoints_and_still_resumes(data_dir, reload_for_data_dir):
    """I32: a workflow chat saves its whole history at every step; old copies go as new ones are saved."""
    from langgraph.graph import END, START, MessagesState, StateGraph
    from langgraph.types import Command, interrupt

    from langchain_core.messages import AIMessage, HumanMessage

    (threads,) = reload_for_data_dir(data_dir, "row_bot.threads")

    def step(state):
        count = len(state["messages"])
        if count == 6:
            return {"messages": [AIMessage(content=f"approved {interrupt('approve?')}", id=f"step-{count}")]}
        return {"messages": [AIMessage(content="tool output " * 500, id=f"step-{count}")]}

    graph = StateGraph(MessagesState)
    graph.add_node("step", step)
    graph.add_edge(START, "step")
    graph.add_conditional_edges("step", lambda state: END if len(state["messages"]) >= 20 else "step")
    app = graph.compile(checkpointer=threads.checkpointer)

    def counts(thread_id):
        with threads.checkpointer.cursor(transaction=False) as conn:
            kept = conn.execute("SELECT COUNT(*) FROM checkpoints WHERE thread_id = ?", (thread_id,)).fetchone()[0]
            orphan_writes = conn.execute(
                "SELECT COUNT(*) FROM writes w WHERE thread_id = ? AND NOT EXISTS (SELECT 1 FROM checkpoints c "
                "WHERE c.thread_id = w.thread_id AND c.checkpoint_ns = w.checkpoint_ns "
                "AND c.checkpoint_id = w.checkpoint_id)", (thread_id,)).fetchone()[0]
        return kept, orphan_writes

    other = {"configurable": {"thread_id": "other-chat"}}
    app.invoke({"messages": [HumanMessage(content="hi", id="other-ask")]}, {**other, "recursion_limit": 100})
    other_before = counts("other-chat")

    config = {"configurable": {"thread_id": "workflow-chat"}, "recursion_limit": 100}
    app.invoke({"messages": [HumanMessage(content="go", id="ask")]}, config)
    assert app.get_state(config).next == ("step",)  # paused for the approval
    assert counts("workflow-chat") == (threads.CHECKPOINTS_KEPT, 0)

    app.invoke(Command(resume="yes"), config)
    final = app.get_state(config).values["messages"]
    assert [message.id for message in final] == ["ask", *[f"step-{index}" for index in range(1, 20)]]
    assert final[6].content == "approved yes"
    assert counts("workflow-chat") == (threads.CHECKPOINTS_KEPT, 0)
    assert counts("other-chat") == other_before
