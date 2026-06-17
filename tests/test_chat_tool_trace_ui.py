from __future__ import annotations

import json
from pathlib import Path

from row_bot.ui.tool_trace import (
    agent_runs_from_payload,
    canonical_tool_name,
    display_tool_content,
    group_tool_results,
    is_agent_tool_result,
    is_browser_tool_name,
    parse_agent_tool_payload,
)


def test_tool_results_group_by_name_without_losing_entries():
    results = [
        {"name": "web_search", "content": "first"},
        {"name": "browser_click", "content": "clicked"},
        {"name": "web_search", "content": "second"},
        {"name": "browser_click", "content": "clicked again"},
        {"name": "workspace_read_file", "content": "file"},
    ]

    groups = group_tool_results(results)

    assert [g.name for g in groups] == [
        "web_search",
        "Browser activity",
        "workspace_read_file",
    ]
    assert [g.count for g in groups] == [2, 2, 1]
    assert [r["content"] for r in groups[0].results] == ["first", "second"]
    assert [r["content"] for r in groups[1].results] == ["clicked", "clicked again"]


def test_browser_group_labels_are_activity_summaries():
    group = group_tool_results(
        [
            {"name": "browser_navigate", "content": "url"},
            {"name": "browser_click", "content": "clicked"},
            {"name": "browser_scroll", "content": "scrolled"},
        ]
    )[0]

    assert canonical_tool_name("browser_click") == "Browser Click"
    assert is_browser_tool_name("browser_click")
    assert is_browser_tool_name("Browser Click")
    assert group.name == "Browser activity"
    assert group.label == "Browser activity · 3 steps"


def test_tool_content_truncates_only_for_display():
    raw = "x" * 20

    assert display_tool_content(raw, limit=10) == "x" * 10 + "\n\n… (truncated)"
    assert raw == "x" * 20


def test_agent_tool_payload_is_detected_from_agent_tool_json():
    result = {
        "name": "Agents",
        "content": json.dumps(
            {
                "ok": True,
                "message": "Agent completed.",
                "run": {
                    "id": "run-1",
                    "display_name": "PDF essay writer",
                    "status": "completed",
                    "summary": "Created the PDF.",
                },
            }
        ),
    }

    payload = parse_agent_tool_payload(result)

    assert payload is not None
    assert is_agent_tool_result(result)
    assert [run["id"] for run in agent_runs_from_payload(payload)] == ["run-1"]


def test_agent_tool_payload_detection_is_conservative_for_other_tools():
    assert not is_agent_tool_result({"name": "workspace_read_file", "content": "{}"})
    assert parse_agent_tool_payload({"name": "Agents", "content": "not json"}) is None


def test_agent_tool_cards_dedupe_runs_by_id_without_dropping_raw_payloads():
    from row_bot.ui import render

    results = [
        {
            "name": "delegate_work",
            "content": json.dumps(
                {
                    "message": "Child Agent started.",
                    "run": {
                        "id": "run-1",
                        "display_name": "Alpha",
                        "status": "queued",
                    },
                }
            ),
        },
        {
            "name": "agent_wait",
            "content": json.dumps(
                {
                    "message": "Agent completed.",
                    "run": {
                        "id": "run-1",
                        "display_name": "Alpha",
                        "status": "completed",
                        "summary": "alpha ok",
                    },
                }
            ),
        },
    ]

    card_runs, raw_results = render._agent_card_runs_from_tool_results(results)

    assert len(card_runs) == 1
    assert card_runs[0][0]["id"] == "run-1"
    assert card_runs[0][0]["status"] == "completed"
    assert card_runs[0][1] == "Agent completed."
    assert raw_results == results


def test_chat_tool_trace_source_contracts():
    render_src = Path("src/row_bot/ui/render.py").read_text(encoding="utf-8")
    streaming_src = Path("src/row_bot/ui/streaming.py").read_text(encoding="utf-8")
    chat_src = Path("src/row_bot/ui/chat.py").read_text(encoding="utf-8")
    components_src = Path("src/row_bot/ui/chat_components.py").read_text(encoding="utf-8")
    installer_src = Path("installer/row_bot_setup.iss").read_text(encoding="utf-8")

    assert "group_tool_results" in render_src
    assert "render_agent_tool_result" in render_src
    assert "render_agent_tool_results" in render_src
    assert "_agent_card_runs_from_tool_results" in render_src
    assert "agent_tool_results: list[dict]" in render_src
    assert "render_agent_tool_results(agent_tool_results" in render_src
    assert "agent_run_ids" in render_src
    assert "_render_agent_run_card(run)" in render_src
    assert "Raw Agent tool output" in render_src
    assert "group_tool_results" in streaming_src or "_finish_live_tool_result" in streaming_src
    assert "refresh_parent_agent_strip" in streaming_src
    assert "parse_agent_spawn_text" in streaming_src
    assert "_handle_direct_agent_spawn" in streaming_src
    assert "turn_boundary" in streaming_src
    assert "agent_run_refresh_key" in streaming_src
    assert "is_agent_tool_result" in streaming_src
    assert "has_agent_tool_results = any(" in streaming_src
    assert "or has_agent_tool_results" in streaming_src
    assert "live_row" in Path("src/row_bot/ui/state.py").read_text(encoding="utf-8")
    assert "def _delete_live_generation_row" in streaming_src
    assert "_delete_live_generation_row(gen)" in streaming_src
    assert "_add_live_tool_pending" in streaming_src
    assert "_finish_live_tool_result" in streaming_src
    assert "render_agent_tool_results(_agent_tool_results" in chat_src
    assert "is_agent_tool_result(_tr)" in chat_src
    assert "browser_step_count += 1" in streaming_src
    assert "_capture_balanced_browser_screenshot" in streaming_src
    assert "render_image_with_save(\n                                _b64_ss" not in streaming_src
    assert "Model selection now lives in the composer" in chat_src
    assert "build_composer_policy_cluster" in chat_src
    assert 'list_model_choice_options("chat"' not in chat_src
    assert "on_model_change" not in chat_src
    assert "model_banner_container" in chat_src
    assert "on_model_switch=_refresh_model_surface" in chat_src
    assert "p.chat_scroll.style(replace=" in chat_src
    assert "tooltip(\"Select model for this thread\")" not in chat_src
    assert "on_model_change" not in components_src
    assert "on_model_switch" in components_src
    assert "_MORE_MODELS_SENTINEL" in components_src
    assert "async def _on_model_pick" in components_src
    picker_section = components_src.split("def _build_inline_model_picker", 1)[1]
    assert "use-input" not in picker_section
    assert "options-dense" in picker_section
    assert "if val == _picker_val" in components_src
    assert "get_model_max_context" in components_src
    assert Path("src/row_bot/ui/tool_trace.py").exists()
    assert 'Source: "..\\src\\row_bot\\*"' in installer_src
