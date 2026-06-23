from __future__ import annotations

import json
import sys
from types import ModuleType

import pytest

from tests.fixtures.memory_stack import fresh_memory_stack


pytestmark = pytest.mark.subsystem


def test_memory_tool_exposes_expected_subtools_and_delete_is_destructive(tmp_path, monkeypatch) -> None:
    stack = fresh_memory_stack(tmp_path, monkeypatch)
    tool = stack["memory_tool"].MemoryTool()

    subtools = {subtool.name for subtool in tool.as_langchain_tools()}

    assert subtools == {
        "save_memory",
        "search_memory",
        "list_memories",
        "update_memory",
        "delete_memory",
        "link_memories",
        "explore_connections",
    }
    assert tool.enabled_by_default is True
    assert tool.required_api_keys == {}
    assert tool.destructive_tool_names == {"delete_memory"}
    assert "save_memory" in tool.execute("anything")


def test_optional_tool_import_failures_are_isolated(monkeypatch) -> None:
    import row_bot.tools as tools_pkg

    def fail_import(_module_name: str) -> None:
        sys.modules["row_bot.tools.partial_optional_dependency"] = ModuleType(
            "row_bot.tools.partial_optional_dependency"
        )
        raise RuntimeError("optional dependency exploded")

    monkeypatch.setattr(tools_pkg.importlib, "import_module", fail_import)

    tools_pkg._import_tool_module("row_bot.tools.optional_broken", optional=True)
    assert "row_bot.tools.partial_optional_dependency" not in sys.modules
    with pytest.raises(RuntimeError, match="optional dependency exploded"):
        tools_pkg._import_tool_module("row_bot.tools.required_broken", optional=False)


def test_memory_tool_save_list_update_delete_uses_isolated_memory_store(tmp_path, monkeypatch) -> None:
    stack = fresh_memory_stack(tmp_path, monkeypatch)
    memory_tool = stack["memory_tool"]
    monkeypatch.setattr(memory_tool, "_check_contradiction", lambda *_args, **_kwargs: None)

    saved = memory_tool._save_memory("project", "Atlas Tool", "Atlas has a timeline.", "atlas,work")
    updated = memory_tool._save_memory("project", "Atlas Tool", "Atlas has a timeline and launch plan.", "atlas")
    listed = json.loads(memory_tool._list_memories("project"))
    memory_id = listed[0]["id"]
    update_result = memory_tool._update_memory(
        memory_id,
        "Atlas launch plan moved to Friday.",
        subject="Atlas Launch Tool",
        entity_type="project",
        aliases="Launch Atlas",
        tags="launch,atlas",
    )

    assert "Memory saved successfully" in saved
    assert "Memory updated (merged with existing)" in updated
    assert listed[0]["subject"] == "Atlas Tool"
    assert listed[0]["category"] == "project"
    assert "Memory updated successfully" in update_result
    assert "Atlas Launch Tool" in update_result
    assert stack["memory"].find_by_subject(None, "Launch Atlas")["id"] == memory_id
    assert memory_tool._delete_memory(memory_id) == f"Memory '{memory_id}' deleted."
    assert memory_tool._delete_memory(memory_id) == f"Memory '{memory_id}' not found."
    assert memory_tool._list_memories("project") == "No memories in category 'project'."


def test_memory_tool_search_formats_properties_and_graph_context(tmp_path, monkeypatch) -> None:
    stack = fresh_memory_stack(tmp_path, monkeypatch)
    memory_tool = stack["memory_tool"]
    touched: list[list[str]] = []

    monkeypatch.setattr(
        memory_tool.kg,
        "retrieve_memory_candidates",
        lambda _query, **_kwargs: [
            {
                "id": "person-1",
                "category": "person",
                "subject": "Ada Tool",
                "content": "Ada enjoys deterministic tests.",
                "tags": "testing",
                "score": 0.92,
                "source": "live",
                "properties": json.dumps(
                    {
                        "status": "active",
                        "confidence": 0.88,
                        "memory_tier": "core",
                        "review_reason": "",
                    }
                ),
                "updated_at": "2026-06-23T10:11:12",
                "via": "graph",
                "relations": [{"from": "Ada Tool", "type": "works_on", "to": "Row-Bot"}],
            },
            {
                "id": "fact-1",
                "category": "fact",
                "subject": "Skipped Tool",
                "content": "Wrong category.",
                "properties": "{not-json",
            },
        ],
    )
    monkeypatch.setattr(memory_tool.kg, "touch_recalled", lambda ids: touched.append(ids))

    payload = json.loads(memory_tool._search_memory("Ada", category="person"))

    assert touched == [["person-1"]]
    assert payload == [
        {
            "id": "person-1",
            "category": "person",
            "subject": "Ada Tool",
            "content": "Ada enjoys deterministic tests.",
            "tags": "testing",
            "relevance": 0.92,
            "source": "live",
            "status": "active",
            "confidence": 0.88,
            "tier": "core",
            "review_reason": "",
            "superseded_by": "",
            "supersedes": [],
            "updated": "2026-06-23T10:11",
            "connected_via": "Ada Tool \u2192 works_on \u2192 Row-Bot",
        }
    ]


def test_memory_tool_links_and_explores_connections_by_subject(tmp_path, monkeypatch) -> None:
    stack = fresh_memory_stack(tmp_path, monkeypatch)
    memory_tool = stack["memory_tool"]
    monkeypatch.setattr(memory_tool, "_check_contradiction", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        memory_tool.kg,
        "to_mermaid",
        lambda *_args, **_kwargs: "graph TD\n  A[User] --> B[Project]\n  B --> C[Coverage]",
    )

    memory_tool._save_memory("person", "Ada Link Tool", "Ada works on Row-Bot.")
    memory_tool._save_memory("project", "Row-Bot Link Tool", "Row-Bot has tools.")

    link_result = memory_tool._link_memories("Ada Link Tool", "Row-Bot Link Tool", "works_on")
    explore_result = memory_tool._explore_connections("Ada Link Tool", hops=99)

    assert "Relationship created successfully" in link_result
    assert "works_on" in link_result
    assert "**Ada Link Tool**" in explore_result
    assert "Relationships (1):" in explore_result
    assert "Row-Bot Link Tool" in explore_result
    assert "```mermaid" in explore_result


def test_memory_tool_missing_link_endpoint_is_reported_without_raising(tmp_path, monkeypatch) -> None:
    stack = fresh_memory_stack(tmp_path, monkeypatch)
    memory_tool = stack["memory_tool"]
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    assert memory_tool._link_memories("missing", "also missing", "related_to").startswith("Error: source entity")
    assert memory_tool._explore_connections("missing").startswith("Entity 'missing' not found")
