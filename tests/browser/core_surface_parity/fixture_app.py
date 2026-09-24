"""Deterministic paired NiceGUI/React fixture for the core-surface audit.

The real application process serves NiceGUI at ``/`` and the built React
client at ``/app-v2/``.  Only provider/native boundaries are replaced by the
existing client-workspace fixture; all saved records live in its disposable
``ROW_BOT_DATA_DIR``.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from fastapi import Header
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from nicegui import app

from row_bot.brand import STRUCTURED_LOG_FILENAME
from tests.browser.client_workspace import fixture_app as workspace
from tests.helpers.client_platform_fakes import fixture_id


FIXTURE_REVISION = "react-default-rich-parity-v3"


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _seed_core_surface_state() -> None:
    """Add bounded, content-safe state without starting any worker or run."""
    token = workspace.predecessor.TOKEN
    workspace.p4_tasks("populated", token)
    workspace.p4_knowledge("populated", token)
    workspace.p4_setup_capability_settings(token)
    workspace.p4_buddy(token)

    from row_bot import tasks
    from row_bot.application.attachments import register_attachment
    from row_bot.conversation_resources import bind, list_bindings
    from row_bot.developer import storage as developer_storage
    from row_bot.threads import append_checkpoint_messages

    developer_workspace = next(
        (
            item
            for item in developer_storage.list_workspaces(include_hidden=True)
            if item.name == "Phase 1 workspace"
        ),
        None,
    )
    if developer_workspace is None:
        raise RuntimeError("Synthetic Developer workspace was not seeded")
    developer_workspace.origin_conversation_id = "p1-browser-a"
    developer_storage.save_workspace(developer_workspace)
    resource_snapshot = list_bindings("p1-browser-a")
    resource_snapshot = bind(
        "p1-browser-a",
        "workspace",
        developer_workspace.id,
        expected_revision=resource_snapshot.revision,
        role="primary",
    )
    bind(
        "p1-browser-a",
        "artifact",
        "docs-designer-project",
        expected_revision=resource_snapshot.revision,
        role="primary",
    )

    connection = tasks._get_conn()
    try:
        connection.execute(
            """UPDATE tasks
               SET enabled = CASE WHEN id IN ('p4-task-000','p4-task-001') THEN 1 ELSE 0 END,
                   description = CASE
                     WHEN id = 'p4-task-000' THEN 'Prepare a compact synthetic morning brief.'
                     WHEN id = 'p4-task-001' THEN 'Review the isolated knowledge fixture.'
                     ELSE description END,
                   prompts = CASE
                     WHEN id = 'p4-task-000' THEN '[\"Summarize the synthetic fixture only.\"]'
                     WHEN id = 'p4-task-001' THEN '[\"Review the synthetic fixture only.\"]'
                     ELSE prompts END
               WHERE id LIKE 'p4-task-%'"""
        )
        connection.commit()
    finally:
        connection.close()

    image_bytes = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a1ZsAAAAASUVORK5CYII="
    )
    chat_media = [
        register_attachment("p1-browser-a", "synthetic.png", image_bytes),
        register_attachment(
            "p1-browser-a", "synthetic.wav", b"RIFF\x10\x00\x00\x00WAVEfmt synthetic"
        ),
        register_attachment(
            "p1-browser-a", "synthetic.mp4", b"\x00\x00\x00\x18ftypisomsynthetic"
        ),
        register_attachment("p1-browser-a", "synthetic.txt", b"Synthetic attachment"),
    ]
    expired = {
        "attachment_ref": "p1-browser-a:00000000-0000-0000-0000-000000000000",
        "name": "expired.png",
        "mime_type": "image/png",
        "size_bytes": 1,
        "revision": "1",
    }
    append_checkpoint_messages(
        "p1-browser-a",
        [
            HumanMessage(
                id=fixture_id("core-parity:user"),
                content="Show the deterministic fixture summary.",
            ),
            AIMessage(
                id=fixture_id("core-parity:assistant"),
                content=(
                    "## Fixture summary\n\n"
                    "The paired clients share **synthetic state**.\n\n"
                    "```text\nNo provider or channel was contacted.\n```"
                ),
                tool_calls=[
                    {
                        "id": fixture_id("core-parity:tool-call"),
                        "name": "fixture_status",
                        "args": {},
                    }
                ],
            ),
            ToolMessage(
                id=fixture_id("core-parity:tool-result"),
                tool_call_id=fixture_id("core-parity:tool-call"),
                content="Synthetic fixture ready.",
            ),
            HumanMessage(
                id=fixture_id("core-rich:user-attachments"),
                content=(
                    "Review the durable fixture attachments.\n\n"
                    "<row_bot_attachment_context>private model-only fixture context"
                    "</row_bot_attachment_context>"
                ),
                additional_kwargs={
                    "platform_public_content": "Review the durable fixture attachments.",
                    "platform_attachments": [*chat_media, expired],
                },
            ),
            AIMessage(
                id=fixture_id("core-rich:assistant"),
                content=(
                    "## Rich fixture\n\n"
                    "| Item | State |\n| --- | --- |\n| Durable blocks | Ready |\n\n"
                    "```python\nanswer = 42\n```\n\n"
                    "```mermaid\ngraph TD\n  A[Admission] --> B[Durable row]\n```\n\n"
                    "[Synthetic YouTube fixture](https://youtu.be/dQw4w9WgXcQ)"
                ),
            ),
            AIMessage(
                id=fixture_id("core-rich:chart"),
                content=(
                    '__CHART__:{"data":[{"type":"bar","x":["A","B"],"y":[1,2]}],'
                    '"layout":{"title":{"text":"Synthetic chart"}}}\n\n'
                    "Synthetic chart"
                ),
            ),
            AIMessage(
                id=fixture_id("core-parity:assistant-final"),
                content="The isolated comparison state is ready.",
            ),
        ],
    )
    trace_media = register_attachment("p1-browser-b", "generated.png", image_bytes)
    trace_calls = [
        ("trace-repeat-a", "fixture_repeat", "Synthetic first result."),
        ("trace-repeat-b", "fixture_repeat", "Synthetic second result."),
        ("trace-failed", "fixture_failure", "Error: synthetic failure."),
        ("trace-blocked", "fixture_policy", "Blocked: synthetic fixture policy."),
        ("trace-cancelled", "fixture_cancel", "Cancelled: synthetic fixture stop."),
        ("trace-uncertain", "fixture_receipt", "Outcome uncertain: synthetic lost reply."),
        ("trace-browser", "browser_navigate", "Synthetic browser step."),
        (
            "trace-skill",
            "skill_load",
            json.dumps(
                {
                    "ok": True,
                    "kind": "skill_loaded",
                    "skill_id": "p4_browser_skill",
                    "display_name": "Synthetic browser skill",
                    "source": "manual",
                    "newly_active": True,
                }
            ),
        ),
        (
            "trace-agent",
            "agents",
            json.dumps(
                {
                    "runs": [
                        {
                            "id": "synthetic-child-run",
                            "display_name": "Synthetic delegated review",
                            "status": "completed",
                        }
                    ]
                }
            ),
        ),
        ("trace-media", "fixture_image", "Synthetic generated image."),
        ("trace-media-error", "fixture_image", "Synthetic unavailable image."),
        ("trace-large", "fixture_large", "L" * 70000),
        ("trace-pending", "computer_use", None),
    ]
    append_checkpoint_messages(
        "p1-browser-b",
        [
            HumanMessage(
                id=fixture_id("core-traces:user"),
                content="Show the deterministic trace matrix.",
            ),
            AIMessage(
                id=fixture_id("core-traces:assistant"),
                content="Synthetic trace states follow.",
                tool_calls=[
                    {
                        "id": fixture_id(identity),
                        "name": name,
                        "args": {},
                    }
                    for identity, name, _content in trace_calls
                ],
            ),
            *[
                ToolMessage(
                    id=fixture_id(identity + ":result"),
                    tool_call_id=fixture_id(identity),
                    content=content,
                    additional_kwargs=(
                        {
                            "platform_media": [
                                {
                                    "type": "media.available",
                                    "payload": {
                                        "media_ref": trace_media["attachment_ref"],
                                        "mime_type": trace_media["mime_type"],
                                    },
                                }
                            ]
                        }
                        if identity == "trace-media"
                        else {
                            "platform_media": [
                                {
                                    "type": "media.error",
                                    "payload": {"code": "media_unavailable"},
                                }
                            ]
                        }
                        if identity == "trace-media-error"
                        else {}
                    ),
                )
                for identity, _name, content in trace_calls
                if content is not None
            ],
            AIMessage(
                id=fixture_id("core-traces:final"),
                content="The deterministic trace matrix is ready.",
            ),
        ],
    )

    data = workspace.predecessor.DATA
    _write_json(
        data / "memory_extraction_state.json",
        {
            "last_extraction": "2026-01-02T09:30:00+00:00",
            "threads_scanned": 4,
            "entities_saved": 12,
            "islands_repaired": 1,
        },
    )
    _write_json(
        data / "extraction_journal.json",
        [
            {
                "timestamp": "2026-01-02T09:30:00+00:00",
                "summary": "12 synthetic memories saved",
                "contradictions_blocked": 1,
                "low_confidence_skipped": 2,
                "islands_repaired": 1,
                "thread_details": [
                    {"thread": "Fixture planning", "extracted": 8, "saved": 7},
                    {"thread": "Fixture review", "extracted": 6, "saved": 5},
                ],
                "errors": [],
            }
        ],
    )
    _write_json(
        data / "dream_config.json",
        {"enabled": True, "window_start": 1, "window_end": 5},
    )
    _write_json(
        data / "dream_journal.json",
        [
            {
                "timestamp": "2026-01-02T04:15:00+00:00",
                "summary": "Synthetic knowledge connected",
                "merges": [
                    {
                        "duplicate_subject": "Fixture alias",
                        "survivor_subject": "Fixture memory",
                        "score": 0.94,
                    }
                ],
                "enrichments": [
                    {
                        "subject": "Fixture memory",
                        "old_length": 42,
                        "new_length": 78,
                        "new_description": "Content-safe synthetic enrichment.",
                    }
                ],
                "inferred_relations": [
                    {
                        "source_subject": "Fixture memory",
                        "target_subject": "Fixture review",
                        "relation_type": "supports",
                        "confidence": 0.91,
                        "evidence": "Synthetic browser evidence.",
                    }
                ],
                "errors": [],
            }
        ],
    )
    log_path = data / "logs" / STRUCTURED_LOG_FILENAME
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_rows = [
        {
            "ts": "2026-01-02T09:31:00+00:00",
            "level": "INFO",
            "logger": "row_bot.fixture",
            "msg": "Core surface fixture ready",
        },
        {
            "ts": "2026-01-02T09:30:00+00:00",
            "level": "INFO",
            "logger": "row_bot.fixture",
            "msg": "Knowledge extraction fixture loaded",
        },
    ]
    log_path.write_text(
        "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in log_rows),
        encoding="utf-8",
    )
    _write_json(
        data / "core_surface_fixture.json",
        {
            "revision": FIXTURE_REVISION,
            "clock": "2026-01-02T10:00:00+00:00",
            "external_calls": 0,
            "clients": ["nicegui", "react"],
        },
    )


@app.get("/__core_parity_fixture/state")
def core_surface_fixture_state(x_fixture_token: str = Header(default="")) -> dict:
    workspace.predecessor._authorize(x_fixture_token)
    state = json.loads(
        (workspace.predecessor.DATA / "core_surface_fixture.json").read_text(
            encoding="utf-8"
        )
    )
    state["data_scope"] = "disposable"
    return state


def main() -> None:
    if os.environ.get("ROW_BOT_TEST_MODE") != "1":
        raise RuntimeError("Core-surface parity fixture requires isolated test mode")
    original_seed = workspace.predecessor.seed

    def seed() -> None:
        original_seed()
        _seed_core_surface_state()

    workspace.predecessor.seed = seed
    workspace.main()


if __name__ == "__main__":
    main()
