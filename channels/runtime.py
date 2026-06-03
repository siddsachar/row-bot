from __future__ import annotations

from approval_policy import DEFAULT_APPROVAL_MODE


def approval_mode_for_config(config: dict) -> str:
    configurable = config.get("configurable") or {}
    thread_id = str(configurable.get("thread_id") or "")
    if not thread_id:
        return DEFAULT_APPROVAL_MODE
    try:
        from threads import _get_thread_approval_mode

        return _get_thread_approval_mode(thread_id)
    except Exception:
        return DEFAULT_APPROVAL_MODE
