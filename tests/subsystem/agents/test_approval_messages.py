from __future__ import annotations


def test_approval_message_prefers_model_reason_and_preserves_raw_action():
    from row_bot.approval_messages import compact_message, normalize_interrupts

    payload = normalize_interrupts(
        [
            {
                "tool": "run_command",
                "label": "Run shell command",
                "approval_reason": "Check the fast test lane.",
                "args": {"command": "uv run python scripts/run_test_matrix.py fast"},
            }
        ],
        source_label="Child Builder",
        agent_run_id="run-1",
        parent_thread_id="parent",
    )

    assert payload["title"] == "Child Builder needs approval to run a command."
    assert payload["reason"] == "Check the fast test lane."
    assert payload["raw_action"] == "uv run python scripts/run_test_matrix.py fast"
    message = compact_message(payload)
    assert "Check the fast test lane." in message
    assert "uv run python scripts/run_test_matrix.py fast" in message


def test_approval_message_redacts_and_truncates_reason():
    from row_bot.approval_messages import sanitize_approval_reason

    reason = sanitize_approval_reason(
        "token=abc123 " + ("explain " * 80),
        max_chars=60,
    )

    assert "abc123" not in reason
    assert "token=<redacted>" in reason
    assert len(reason) <= 60


def test_approval_message_falls_back_without_model_reason():
    from row_bot.approval_messages import compact_message, normalize_interrupts

    payload = normalize_interrupts(
        [{"tool": "developer_write_file", "args": {"path": "README.md"}}],
        source_label="Child Writer",
    )

    assert payload["title"] == "Child Writer needs approval to write a file."
    assert payload["reason"] == ""
    assert "README.md" in compact_message(payload)
