"""The client's view of Prepare sandbox and the sandbox image (F18), and the person's own starts.

The person starts Prepare (or the image's download) with a button in the app and confirms it
there first; that confirmation is their approval, as for the sandbox's other controls. Block
refuses it. The agent's Prepare goes through the approval gate instead
(``developer_prepare_sandbox``). Stopping needs no approval.
"""

from __future__ import annotations

from typing import Any

from row_bot.developer import sandbox_prepare


class SandboxViewError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _workspace(conversation_id: str) -> Any:
    workspace = sandbox_prepare.conversation_workspace(conversation_id)
    if workspace is None:
        raise SandboxViewError("sandbox_unavailable")
    return workspace


def read_status(conversation_id: str) -> dict[str, Any]:
    """Docker, the image, what Prepare would install, and the latest job, for a conversation's code folder."""
    return sandbox_prepare.image_status(_workspace(conversation_id))


def start_job(conversation_id: str, kind: str) -> dict[str, Any]:
    """The person's own Prepare sandbox (or image download), confirmed in the app; Block refuses it."""
    from row_bot.developer.sandbox import decide_action
    from row_bot.threads import _get_thread_approval_mode

    workspace = _workspace(conversation_id)
    action = "run_install" if kind == "prepare" else "run_network"
    if decide_action(_get_thread_approval_mode(conversation_id), action, by_person=True).decision == "block":
        raise SandboxViewError("sandbox_prepare_blocked")
    try:
        job = sandbox_prepare.start(workspace, conversation_id, kind="pull" if kind == "pull" else "prepare",
                                    by="you")
    except sandbox_prepare.PrepareError as exc:
        raise SandboxViewError(exc.code) from None
    return sandbox_prepare.view(job)


def read_jobs(conversation_id: str | None) -> dict[str, Any]:
    jobs = sandbox_prepare.list_jobs(conversation_id)[:100]
    return {"items": [sandbox_prepare.view(job) for job in jobs], "running": sum(job.active for job in jobs)}


def read_job(job_id: str) -> dict[str, Any] | None:
    job = sandbox_prepare.get(job_id)
    return None if job is None else sandbox_prepare.view(job)


def stop_job(job_id: str) -> dict[str, Any] | None:
    job = sandbox_prepare.stop(job_id, by="you")
    return None if job is None else sandbox_prepare.view(job)
