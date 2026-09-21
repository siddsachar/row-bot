from row_bot.ui.legacy_adapter.live_projection import build_live_projection_view


def test_live_projection_view_reduces_public_text_thinking_and_tool_updates():
    snapshot = {
        "generation": {"status": "running"},
        "rows": [
            {"id": "user:submission:one", "blocks": [{"type": "text", "text": "Ignored"}]},
            {"id": "assistant:live:pass:segment", "blocks": [{"type": "text", "text": "Visible"}]},
        ],
    }
    events = [
        {"type": "generation.activity", "payload": {"state": "thinking"}},
        {"type": "tool.activity", "payload": {
            "tool_call_id": "call", "group_name": "fixture image", "status": "pending",
        }},
        {"type": "tool.activity", "payload": {
            "tool_call_id": "call", "group_name": "fixture image", "status": "completed",
        }},
    ]

    view = build_live_projection_view(snapshot, events)

    assert view.active is True
    assert view.text == "Visible"
    assert view.thinking is True
    assert [(trace.label, trace.status) for trace in view.traces] == [
        ("Done fixture image", "completed"),
    ]


def test_terminal_projection_clears_live_view():
    assert build_live_projection_view(
        {"generation": {"status": "completed"}, "rows": []}, []
    ).active is False
