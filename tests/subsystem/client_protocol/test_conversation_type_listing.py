"""The sidebar's type filters read each type from the server, older conversations included (B239)."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing

import pytest

from tests.subsystem.client_protocol.test_protocol_application import service  # noqa: F401

pytestmark = pytest.mark.subsystem

# Each fixture shape and the sidebar types the client shows it under
# (`conversation-groups.ts` `matchesType`): its server category plus its bindings.
SHAPES = {
    "chat": {"chat"},
    "code-thread": {"code"},  # a Code conversation whose folder is gone
    "code-folder": {"code"},
    "design": {"designer"},
    "workflow": {"workflow"},
    "chat-with-folder": {"code"},  # a chat that later bound a folder
    "design-with-folder": {"designer", "code"},
}
# The listing group the sidebar asks for per type.
GROUPS = {"chat": "chat", "code": "workspace", "designer": "artifact", "workflow": "workflow"}


def _library(threads, tasks) -> dict[str, list[str]]:
    """Two conversations of every shape, oldest first."""
    expected: dict[str, list[str]] = {kind: [] for kind in GROUPS}
    for index in range(2 * len(SHAPES)):
        shape = list(SHAPES)[index % len(SHAPES)]
        identity = f"library-{index:02d}-{shape}"
        threads.create_thread(
            f"Library {index:02d}", thread_id=identity, seed_default_skills=False,
            thread_type="code" if shape == "code-thread" else "",
            developer_workspace_id="folder-a" if shape in {"code-folder", "design-with-folder"} else "",
            project_id="deck-a" if shape.startswith("design") else "")
        if shape == "workflow":
            tasks.create_task(f"Workflow {index:02d}", prompts=["Summarise"], persistent_thread_id=identity)
        with closing(sqlite3.connect(threads.DB_PATH)) as conn, conn:
            conn.execute("UPDATE thread_meta SET updated_at=? WHERE thread_id=?",
                         (f"2026-01-{1 + index:02d}T09:00:00", identity))
            if shape == "chat-with-folder":
                conn.execute("UPDATE thread_meta SET resource_bindings_json=? WHERE thread_id=?", (json.dumps([
                    {"binding_id": f"binding-{index}", "kind": "workspace", "resource_id": "folder-b",
                     "role": "context", "revision": "1"}]), identity))
        for kind in SHAPES[shape]:
            expected[kind].append(identity)
    return expected


def _shown_under(row: dict) -> set[str]:
    kinds = {binding["kind"] for binding in row["resource_bindings"]}
    shown = set()
    if row["category"] == "designer" or "artifact" in kinds:
        shown.add("designer")
    if row["category"] == "code" or "workspace" in kinds:
        shown.add("code")
    if row["category"] == "workflow":
        shown.add("workflow")
    return shown or {"chat"}


def test_each_type_pages_through_its_own_older_conversations(service):  # noqa: F811
    from row_bot import tasks, threads

    expected = _library(threads, tasks)
    for kind, group in GROUPS.items():
        cursor, found, pages = None, [], 0
        while True:
            # A page one short of the type's matches: the oldest is on the next page.
            page = service.list_conversations(limit=len(expected[kind]) - 1, cursor=cursor, group=group)
            pages += 1
            assert all(kind in _shown_under(row) for row in page["items"]), (kind, page["items"])
            found.extend(row["id"] for row in page["items"])
            if not page["has_more"]:
                break
            cursor = page["next_cursor"]
        # Newest first through every page, down to the oldest match.
        assert found == expected[kind][::-1], kind
        assert pages > 1, kind
