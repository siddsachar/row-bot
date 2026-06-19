"""Seed deterministic safe data for real Row-Bot docs screenshots."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed real app demo data for docs capture")
    parser.add_argument("--data-dir", required=True, help="Temporary ROW_BOT_DATA_DIR to seed")
    parser.add_argument("--scenario", default="full", help="Demo scenario to seed")
    return parser.parse_args()


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _seed_app_config(data_dir: Path, *, first_run: bool) -> None:
    if first_run:
        config = {
            "onboarding_seen": False,
            "setup_complete": False,
            "window_mode": "browser",
        }
    else:
        config = {
            "onboarding_seen": True,
            "setup_complete": True,
            "onboarding_version": 3,
            "onboarding_profile": ["chat", "research", "workflows", "designer", "developer"],
            "onboarding_completed_steps": ["models", "knowledge", "workflows"],
            "onboarding_skipped_steps": [],
            "onboarding_dismissed_home_card": False,
            "window_mode": "browser",
        }
    _write_json(data_dir / "app_config.json", config)


def _seed_threads_sqlite(data_dir: Path, state: dict) -> None:
    db = data_dir / "threads.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    now = datetime(2026, 6, 18, 9, 0, tzinfo=timezone.utc).isoformat()
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS thread_meta "
            "(thread_id TEXT PRIMARY KEY, name TEXT, created_at TEXT, updated_at TEXT, "
            "model_override TEXT DEFAULT '', skills_override TEXT DEFAULT '', summary TEXT DEFAULT '', "
            "summary_msg_count INTEGER DEFAULT 0, project_id TEXT DEFAULT '', thread_type TEXT DEFAULT '', "
            "developer_workspace_id TEXT DEFAULT '', approval_mode TEXT DEFAULT '', name_source TEXT DEFAULT '', "
            "agent_profile_id TEXT DEFAULT '', agent_profile_slug TEXT DEFAULT '')"
        )
        for row in state.get("threads", []):
            thread_id = str(row.get("id") or "")
            name = str(row.get("name") or thread_id)
            kind = str(row.get("kind") or "chat")
            if not thread_id:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO thread_meta "
                "(thread_id, name, created_at, updated_at, model_override, thread_type, approval_mode, name_source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    thread_id,
                    name,
                    now,
                    now,
                    str(state.get("model") or ""),
                    "workflow" if kind == "workflow" else "",
                    "approve",
                    "manual",
                ),
            )
        conn.commit()


def _seed_checkpoint_messages(state: dict) -> None:
    try:
        from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
        from row_bot.threads import append_checkpoint_messages
    except Exception:
        return
    messages = []
    for item in state.get("messages", []):
        role = item.get("role")
        content = str(item.get("content") or "")
        if role == "user":
            messages.append(HumanMessage(content=content))
            continue
        for tool in item.get("tool_results", []) or []:
            messages.append(
                ToolMessage(
                    content=str(tool.get("content") or ""),
                    name=str(tool.get("name") or "tool"),
                    tool_call_id=str(tool.get("name") or "tool").replace(".", "_"),
                )
            )
        messages.append(AIMessage(content=content))
    append_checkpoint_messages(str(state.get("thread_id") or "docs-demo-chat"), messages)


def _seed_demo_files(data_dir: Path, state: dict) -> None:
    workspace = data_dir / "docs-demo-workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "launch-checklist.md").write_text(
        "# Demo Launch Checklist\n\n"
        "- Confirm local model path.\n"
        "- Review approvals before file writes.\n"
        "- Keep channels disabled until credentials are added.\n",
        encoding="utf-8",
    )
    (workspace / "support-faq.md").write_text(
        "# Support FAQ\n\nAll demo records use example.com addresses and fake provider states.\n",
        encoding="utf-8",
    )
    docs_dir = data_dir / "documents"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "Launch brief.txt").write_text(
        "Demo launch brief for Row-Bot public documentation screenshots.\n",
        encoding="utf-8",
    )
    _write_json(data_dir / "provider_catalog_cache.json", {"providers": state.get("providers", [])})
    _write_json(data_dir / "docs_demo_review_state.json", state)


def main() -> int:
    args = _parse_args()
    data_dir = Path(args.data_dir).resolve()
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    os.environ["ROW_BOT_DATA_DIR"] = str(data_dir)
    os.environ.setdefault("ROW_BOT_DOCS_CAPTURE", "1")
    os.environ.setdefault("ROW_BOT_DOCS_FIXED_NOW", "2026-06-18T09:00:00Z")

    from row_bot.docs_capture import (
        SCENARIOS,
        default_docs_capture_demo_state,
        scan_demo_data_safety,
        write_docs_capture_demo_state,
    )

    scenario = str(args.scenario or "full")
    if scenario not in SCENARIOS:
        raise SystemExit(f"Unknown scenario {scenario!r}. Expected one of: {', '.join(sorted(SCENARIOS))}")
    first_run = scenario == "first-run"
    data_dir.mkdir(parents=True, exist_ok=True)
    state_path = write_docs_capture_demo_state(data_dir, scenario=scenario)
    state = default_docs_capture_demo_state()
    state["scenario"] = scenario
    _seed_app_config(data_dir, first_run=first_run)
    if not first_run:
        _seed_threads_sqlite(data_dir, state)
        _seed_checkpoint_messages(state)
        _seed_demo_files(data_dir, state)
    errors = scan_demo_data_safety(data_dir)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"Seeded real docs demo data in {data_dir}")
    print(f"Wrote demo state to {state_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
