"""Share Developer's durable checkout writer lock with ordinary chat runs."""

from __future__ import annotations

from contextlib import contextmanager
from threading import Event
from typing import Callable, Iterator


@contextmanager
def writer_run(conversation_id: str, workspace_id: str, execution_id: str,
               stop_event: Event, on_status: Callable[[str, str], None] | None = None,
               final_status: Callable[[], str] | None = None) -> Iterator[str]:
    """Queue a write-capable turn; release only its own lock after quiescence."""
    from row_bot import agent_runs

    run_id = f"chat-{execution_id}"
    key = f"developer:{workspace_id}"
    agent_runs.create_agent_run(
        run_id=run_id, kind="workflow", status="queued", thread_id=conversation_id,
        workspace_id=workspace_id, workspace_mode="single_writer",
        write_lock_key=key, display_name="Conversation coding run",
    )
    acquired = False
    reported_wait = False
    try:
        while not stop_event.is_set():
            if agent_runs.acquire_agent_write_lock(
                key, run_id, thread_id=conversation_id, workspace_id=workspace_id,
                metadata_json={"runtime_surface": "normal_chat"},
            ):
                acquired = True
                break
            if not reported_wait:
                agent_runs.update_agent_status(run_id, "queued", "Waiting for checkout writer")
                if on_status:
                    on_status("queued", run_id)
                reported_wait = True
            stop_event.wait(0.1)
        if not acquired:
            agent_runs.finish_agent_run(run_id, "stopped", status_message="Stopped while waiting for checkout")
            if on_status:
                on_status("stopped", run_id)
            raise RuntimeError("checkout_writer_cancelled")
        agent_runs.start_agent_run(run_id)
        if on_status:
            on_status("running", run_id)
        yield run_id
        outcome = final_status() if final_status else "completed"
        if outcome == "interrupted":
            outcome = "failed"
        if outcome not in {"completed", "failed", "stopped", "waiting_approval"}:
            outcome = "failed"
        agent_runs.finish_agent_run(run_id, outcome)
        if on_status:
            on_status(outcome, run_id)
    except BaseException:
        if acquired:
            final_status = "stopped" if stop_event.is_set() else "failed"
            agent_runs.finish_agent_run(run_id, final_status)
            if on_status:
                on_status(final_status, run_id)
        raise
    finally:
        if acquired:
            agent_runs.release_agent_write_lock(run_id=run_id)


def require_execution_writer(workspace_id: str, *, write: bool = True) -> None:
    """Validate captured targets and require an owned lease for run mutations."""
    from row_bot.conversation_resources import current_execution_context

    context = current_execution_context()
    if context is not None:
        binding = context.resolve("workspace")
        if binding is None or binding.resource_id != workspace_id:
            raise ValueError("resource_binding_revoked")
    if not write:
        return
    from row_bot.agent import get_active_runtime_context
    from row_bot.agent_runs import get_agent_write_lock

    runtime = get_active_runtime_context()
    run_id = str(runtime.get("agent_run_id") or "")
    if not run_id and not runtime.get("platform_command_id"):
        return  # Existing direct Developer Studio and legacy sessions keep their owner.
    lease = get_agent_write_lock(f"developer:{workspace_id}")
    if not run_id or not lease or lease.get("run_id") != run_id:
        raise ValueError("checkout_writer_unavailable")


def writer_status(conversation_id: str) -> str:
    """Return the latest chat writer state for snapshot recovery."""
    from row_bot import agent_runs

    agent_runs.ensure_agent_run_schema()
    connection = agent_runs._get_conn()
    try:
        row = connection.execute(
            "SELECT status FROM agent_runs WHERE thread_id=? AND id LIKE 'chat-%' "
            "ORDER BY created_at DESC LIMIT 1", (conversation_id,),
        ).fetchone()
        return str(row[0]) if row else ""
    finally:
        connection.close()
