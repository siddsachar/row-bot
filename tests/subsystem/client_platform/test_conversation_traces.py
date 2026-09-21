"""Framework-neutral conversation trace semantics stay bounded and stable."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path

import pytest

from row_bot.application.conversation_traces import (
    MAX_AGENT_REFERENCES,
    MAX_SUMMARY_CHARS,
    TRACE_STATUSES,
    bounded_safe_summary,
    build_trace_item,
    canonical_group,
    canonical_tool_name,
    classify_tool_result,
    group_trace_items,
    project_assistant_row_traces,
    safe_tool_call_args,
    safe_tool_input,
    specialize_tool_result,
)


pytestmark = pytest.mark.subsystem


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"content": "still running"}, "succeeded"),
        ({"content": {"ok": False}}, "failed"),
        ({"content": '{"status":"blocked"}'}, "blocked"),
        ({"content": '{"status":"cancelled"}'}, "cancelled"),
        ({"content": '{"status":"uncertain"}'}, "uncertain"),
        ({"content": "Error: synthetic failure"}, "failed"),
        ({"content": "ordinary public result"}, "succeeded"),
    ],
)
def test_result_classification_is_closed_and_truthful(value, expected):
    assert classify_tool_result(value) == expected
    assert expected in TRACE_STATUSES


def test_pending_and_external_uncertainty_are_explicit_not_text_inferences():
    assert classify_tool_result(None, pending=True) == "pending"
    assert (
        classify_tool_result(
            {"content": "apparently successful"}, external_outcome="uncertain"
        )
        == "uncertain"
    )


@pytest.mark.parametrize(
    ("name", "canonical", "group"),
    [
        ("browser_click", "Browser Click", ("Browser activity", "browser")),
        ("Browser Snapshot", "Browser Snapshot", ("Browser activity", "browser")),
        ("computer_use", "Computer activity", ("Computer activity", "computer")),
        ("workspace_read_file", "workspace_read_file", ("workspace_read_file", "generic")),
    ],
)
def test_canonical_grouping_is_shared_and_bounded(name, canonical, group):
    assert canonical_tool_name(name) == canonical
    assert canonical_group(name) == group
    assert len(canonical_tool_name("x" * 1000)) == 180


def _item(
    item_id: str,
    group_id: str,
    call_id: str,
    call_order: int,
    group_order: int,
    tool_name: str,
    result: object = "ok",
):
    return build_trace_item(
        item_id=item_id,
        group_id=group_id,
        call_id=call_id,
        result_message_id=f"result-{call_id}",
        call_order=call_order,
        group_order=group_order,
        tool_name=tool_name,
        result={"name": tool_name, "content": result},
        content_ref=f"result-{call_id}",
    )


def test_caller_identities_and_explicit_orders_survive_grouping():
    items = [
        _item("item-b", "group-b", "call-b", 2, 1, "workspace_read_file"),
        _item("item-a2", "group-a", "call-a2", 3, 0, "browser_click"),
        _item("item-a1", "group-a", "call-a1", 0, 0, "browser_navigate"),
    ]

    groups = group_trace_items(items)

    assert [group.group_id for group in groups] == ["group-a", "group-b"]
    assert [item.item_id for item in groups[0].items] == ["item-a1", "item-a2"]
    assert [item.call_order for item in groups[0].items] == [0, 3]
    assert groups[0].name == "Browser activity"
    assert groups[0].kind == "browser"
    assert groups[0].status == "succeeded"
    assert groups[0].counts["succeeded"] == 2


@pytest.mark.parametrize(
    "changed,code",
    [
        (("item_id", "item-a1"), "duplicate_trace_item_id"),
        (("call_id", "call-a1"), "duplicate_trace_call_id"),
        (("call_order", 0), "duplicate_trace_call_order"),
    ],
)
def test_duplicate_caller_identity_or_order_is_rejected(changed, code):
    first = _item("item-a1", "group-a", "call-a1", 0, 0, "browser_click")
    values = {
        "item_id": "item-a2",
        "group_id": "group-a",
        "call_id": "call-a2",
        "call_order": 1,
        "group_order": 0,
        "tool_name": "browser_navigate",
    }
    values[changed[0]] = changed[1]
    second = _item(**values)
    with pytest.raises(ValueError, match=code):
        group_trace_items([first, second])


def test_group_status_never_calls_attention_or_pending_work_done():
    groups = group_trace_items(
        [
            _item("ok", "group", "ok", 0, 0, "fixture", "ok"),
            _item(
                "blocked",
                "group",
                "blocked",
                1,
                0,
                "fixture",
                '{"status":"blocked"}',
            ),
        ]
    )
    assert groups[0].status == "blocked"
    assert groups[0].counts == {
        "pending": 0,
        "succeeded": 1,
        "failed": 0,
        "blocked": 1,
        "cancelled": 0,
        "uncertain": 0,
    }


def test_safe_summary_prefers_bounded_display_summary_and_excludes_private_json():
    private = "private@example.test / hidden/file.txt"
    summary, truncated = bounded_safe_summary(
        json.dumps(
            {
                "display_summary": "S" * (MAX_SUMMARY_CHARS + 50),
                "private": private,
            }
        )
    )
    assert len(summary) == MAX_SUMMARY_CHARS
    assert truncated is True
    assert private not in summary

    summary, truncated = bounded_safe_summary(
        json.dumps({"private": private, "arguments": {"token": "secret"}})
    )
    assert (summary, truncated) == ("", False)


def test_safe_tool_input_is_allowlisted_bounded_and_stable():
    private = "C:/private/secret.txt"
    args = {
        "limit": 3,
        "statuses": ["running", "completed"],
        "path": private,
        "token": "secret",
    }
    assert safe_tool_call_args(args) == {
        "limit": 3,
        "statuses": ["running", "completed"],
    }
    assert safe_tool_input(args) == '{"limit":3,"statuses":["running","completed"]}'
    assert private not in safe_tool_input(args)


def test_oversized_structured_payload_is_not_parsed_or_echoed_as_a_summary():
    private = "secret-value"
    value = '{"display_summary":"' + ("x" * 40000) + private + '"}'
    assert bounded_safe_summary(value) == ("", False)


def test_skill_specialization_exposes_only_reviewed_bounded_metadata():
    private = "C:/private/reference/instructions.md"
    result = {
        "name": "skill_load",
        "content": json.dumps(
            {
                "ok": True,
                "kind": "skill_loaded",
                "skill_id": "plugin:demo:alpha",
                "display_name": "Alpha",
                "source": "plugin:demo",
                "newly_active": True,
                "evicted_skill_id": "older",
                "root": private,
                "instructions": private,
                "arguments": {"secret": private},
            }
        ),
    }

    specialized = specialize_tool_result(result)

    assert specialized is not None
    assert asdict(specialized) == {
        "kind": "skill_load",
        "skill_id": "plugin:demo:alpha",
        "display_name": "Alpha",
        "source": "plugin:demo",
        "newly_active": True,
        "evicted_skill_id": "older",
        "agent_runs": (),
        "media_kind": "",
        "media": (),
        "error_code": "",
    }
    assert private not in repr(specialized)


def test_agent_specialization_is_deduped_bounded_and_private_field_free():
    private = "private child output"
    runs = [
        {
            "id": f"run-{index}",
            "display_name": f"Agent {index}",
            "status": "completed",
            "summary": private,
            "private": private,
        }
        for index in range(MAX_AGENT_REFERENCES + 4)
    ]
    result = {
        "name": "agent_status",
        "content": json.dumps({"runs": [runs[0], *runs]}),
    }

    specialized = specialize_tool_result(result)

    assert specialized is not None and specialized.kind == "delegated_agent"
    assert len(specialized.agent_runs) == MAX_AGENT_REFERENCES
    assert len({run.run_id for run in specialized.agent_runs}) == MAX_AGENT_REFERENCES
    assert private not in repr(specialized)


def test_media_specialization_keeps_references_without_marker_or_inline_data():
    private_image = "a" * 20000
    result = {
        "name": "image_gen",
        "content": f"__IMAGE__:{private_image}\n\nGenerated",
        "media": [
            {
                "type": "media.available",
                "payload": {
                    "media_ref": "conversation:attachment:synthetic",
                    "mime_type": "image/png",
                    "private": private_image,
                },
            }
        ],
    }

    specialized = specialize_tool_result(result)

    assert specialized is not None and specialized.kind == "media"
    assert specialized.media_kind == "image"
    assert [asdict(item) for item in specialized.media] == [
        {
            "media_ref": "conversation:attachment:synthetic",
            "mime_type": "image/png",
        }
    ]
    assert private_image not in repr(specialized)


def test_application_trace_owner_has_no_ui_import_or_unsafe_object_loader():
    source = Path("src/row_bot/application/conversation_traces.py").read_text(
        encoding="utf-8"
    )
    assert "row_bot.ui" not in source
    assert "pickle" not in source
    assert "yaml.load" not in source


def test_ordered_public_rows_project_stable_grouped_assistant_traces():
    records = [
        {
            "row": {
                "id": "assistant:checkpoint:parent",
                "message_id": "parent",
                "role": "assistant",
                "blocks": [],
            },
            "tool_calls": [
                {"id": "call-browser-1", "name": "browser_navigate"},
                {
                    "id": "call-generic",
                    "name": "workspace_read_file",
                    "args": {"limit": 3, "path": "private/file.txt"},
                },
                {"id": "call-browser-2", "name": "browser_click"},
                {"id": "call-pending", "name": "fixture_pending"},
            ],
        },
        {
            "row": {
                "id": "tool:checkpoint:result-browser-2",
                "message_id": "result-browser-2",
                "role": "tool",
                "tool_call_id": "call-browser-2",
                "blocks": [{"type": "text", "text": "Clicked"}],
            }
        },
        {
            "row": {
                "id": "tool:checkpoint:result-generic",
                "message_id": "result-generic",
                "role": "tool",
                "tool_call_id": "call-generic",
                "blocks": [
                    {
                        "type": "text",
                        "text": '{"status":"blocked","display_summary":"Read blocked safely."}',
                    }
                ],
            }
        },
        {
            "row": {
                "id": "tool:checkpoint:result-browser-1",
                "message_id": "result-browser-1",
                "role": "tool",
                "tool_call_id": "call-browser-1",
                "blocks": [{"type": "text", "text": "Navigated"}],
            }
        },
    ]

    rows = project_assistant_row_traces(records)

    traces = rows[0]["traces"]
    assert [group["name"] for group in traces] == [
        "Browser activity",
        "workspace_read_file",
        "fixture_pending",
    ]
    assert traces[0]["group_id"] == "call-browser-1"
    assert [item["item_id"] for item in traces[0]["items"]] == [
        "call-browser-1",
        "call-browser-2",
    ]
    assert [item["call_order"] for item in traces[0]["items"]] == [0, 2]
    assert traces[1]["status"] == "blocked"
    assert traces[1]["items"][0]["safe_summary"] == "Read blocked safely."
    assert traces[1]["items"][0]["safe_input"] == '{"limit":3}'
    assert traces[2]["status"] == "pending"
    assert traces[2]["items"][0]["result_message_id"] == ""
    assert [row.get("trace_parent_id") for row in rows[1:]] == [
        "assistant:checkpoint:parent",
        "assistant:checkpoint:parent",
        "assistant:checkpoint:parent",
    ]


def test_trace_adapter_copies_rows_and_leaves_orphan_results_unattached():
    source_row = {
        "id": "tool:checkpoint:orphan",
        "message_id": "orphan",
        "role": "tool",
        "tool_call_id": "missing-call",
        "blocks": [{"type": "text", "text": "Orphan"}],
    }
    rows = project_assistant_row_traces([{"row": source_row}])
    assert rows == [source_row]
    assert rows[0] is not source_row
    assert "trace_parent_id" not in source_row


def test_trace_adapter_accepts_caller_group_and_item_ids_without_rewriting():
    rows = project_assistant_row_traces(
        [
            {
                "row": {
                    "id": "assistant:checkpoint:parent",
                    "role": "assistant",
                    "blocks": [],
                },
                "tool_calls": [
                    {
                        "id": "call-1",
                        "name": "browser_click",
                        "item_id": "stable-item-1",
                        "group_id": "stable-group",
                    },
                    {
                        "id": "call-2",
                        "name": "browser_scroll",
                        "item_id": "stable-item-2",
                        "group_id": "stable-group",
                    },
                ],
            }
        ]
    )
    group = rows[0]["traces"][0]
    assert group["group_id"] == "stable-group"
    assert [item["item_id"] for item in group["items"]] == [
        "stable-item-1",
        "stable-item-2",
    ]


def test_trace_adapter_preserves_bounded_composite_parent_identity():
    parent_id = "assistant:checkpoint:" + ("a" * 900)
    rows = project_assistant_row_traces(
        [
            {
                "row": {"id": parent_id, "role": "assistant", "blocks": []},
                "tool_calls": [{"id": "call-1", "name": "fixture"}],
            },
            {
                "row": {
                    "id": "tool:checkpoint:result",
                    "role": "tool",
                    "tool_call_id": "call-1",
                    "blocks": [],
                }
            },
        ]
    )
    assert rows[1]["trace_parent_id"] == parent_id


def test_trace_adapter_serializes_specialization_without_raw_private_payload():
    private = "private skill instructions and filesystem path"
    rows = project_assistant_row_traces(
        [
            {
                "row": {
                    "id": "assistant:checkpoint:parent",
                    "role": "assistant",
                    "blocks": [],
                },
                "tool_calls": [{"id": "skill-call", "name": "skill_load"}],
            },
            {
                "row": {
                    "id": "tool:checkpoint:skill-result",
                    "message_id": "skill-result",
                    "role": "tool",
                    "tool_call_id": "skill-call",
                    "blocks": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {
                                    "ok": True,
                                    "kind": "skill_loaded",
                                    "skill_id": "alpha",
                                    "display_name": "Alpha",
                                    "source": "manual",
                                    "newly_active": True,
                                    "instructions": private,
                                    "root": private,
                                }
                            ),
                        }
                    ],
                }
            },
        ]
    )
    encoded = json.dumps(rows[0]["traces"])
    assert '"kind": "skill_load"' in encoded
    assert '"skill_id": "alpha"' in encoded
    assert private not in encoded


def test_trace_adapter_rejects_duplicate_calls_results_and_conflicting_groups():
    parent = {
        "id": "assistant:checkpoint:parent",
        "role": "assistant",
        "blocks": [],
    }
    with pytest.raises(ValueError, match="duplicate_trace_call_id"):
        project_assistant_row_traces(
            [
                {
                    "row": parent,
                    "tool_calls": [
                        {"id": "same", "name": "first"},
                        {"id": "same", "name": "second"},
                    ],
                }
            ]
        )
    with pytest.raises(ValueError, match="trace_group_conflict"):
        project_assistant_row_traces(
            [
                {
                    "row": parent,
                    "tool_calls": [
                        {
                            "id": "first",
                            "name": "browser_click",
                            "group_id": "group-a",
                        },
                        {
                            "id": "second",
                            "name": "browser_scroll",
                            "group_id": "group-b",
                        },
                    ],
                }
            ]
        )
    records = [
        {"row": parent, "tool_calls": [{"id": "same", "name": "fixture"}]},
        {
            "row": {
                "id": "tool:checkpoint:first",
                "role": "tool",
                "tool_call_id": "same",
                "blocks": [],
            }
        },
        {
            "row": {
                "id": "tool:checkpoint:second",
                "role": "tool",
                "tool_call_id": "same",
                "blocks": [],
            }
        },
    ]
    with pytest.raises(ValueError, match="duplicate_trace_result"):
        project_assistant_row_traces(records)
