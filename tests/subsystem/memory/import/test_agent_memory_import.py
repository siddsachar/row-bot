"""Importing what Hermes Agent, OpenClaw and Grok Bot remember, read the way each keeps it (Nov 2026)."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing

import pytest

from row_bot.memory_import import SOURCES, scan_files, scan_text
from tests.fixtures.memory_exports import write_zip


pytestmark = pytest.mark.subsystem

HERMES_USER = "Name is Alex; prefers to be called Al.\n§\nTime zone Europe/Berlin.\n§\nPrefers short answers with code first,\nthen a one-line explanation."
HERMES_NOTES = "The garden-planner repo lives in ~/code/garden.\n§\nAlex's laptop runs Fedora 41."


def _facts(scan, source=None):
    return [fact for fact in scan.facts if source is None or fact.source == source]


def _texts(scan, source=None):
    return [fact.text for fact in _facts(scan, source)]


def test_hermes_memory_files_split_on_its_section_sign_into_facts_about_you_and_its_notes(tmp_path) -> None:
    (tmp_path / "USER.md").write_text(HERMES_USER, encoding="utf-8-sig")  # a Notepad BOM is fine
    (tmp_path / "MEMORY.md").write_text(HERMES_NOTES, encoding="utf-8")

    scan = scan_files([tmp_path / "USER.md", tmp_path / "MEMORY.md"])

    about = [fact for fact in _facts(scan, "hermes") if fact.subject_hint == "User"]
    assert [fact.text for fact in about][:2] == ["Name is Alex; prefers to be called Al.", "Time zone Europe/Berlin."]
    assert any(fact.category == "preference" and fact.text.startswith("Prefers short answers") for fact in about)
    notes = [fact for fact in _facts(scan, "hermes") if "agent_note" in fact.tags]
    assert {fact.text for fact in notes} == {"The garden-planner repo lives in ~/code/garden.",
                                             "Alex's laptop runs Fedora 41."}
    assert scan.counts["hermes"].label == "Hermes Agent"


def test_a_hermes_backup_zip_gives_its_memories_and_never_its_keys_or_persona(tmp_path) -> None:
    backup = write_zip(tmp_path / "hermes-backup-20261112.zip", {
        "memories/USER.md": HERMES_USER,
        "memories/MEMORY.md": HERMES_NOTES,
        ".env": "OPENROUTER_API_KEY=sk-or-v1-0123456789abcdef0123456789abcdef\n",
        "auth.json": {"token": "secret-token-value"},
        "SOUL.md": "You are a calm, careful assistant.",
        "config.yaml": "model: hermes-4\n",
    })

    scan = scan_files([backup])

    assert "Time zone Europe/Berlin." in _texts(scan, "hermes")
    everything = json.dumps([fact.text for fact in scan.facts])
    assert "sk-or-v1" not in everything and "secret-token" not in everything and "calm, careful" not in everything
    skipped = {item.name.split(" › ")[-1] for item in scan.skipped}
    assert {".env", "auth.json", "SOUL.md", "config.yaml"} <= skipped


def _hermes_state(path):
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript("""
            CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, title TEXT, started_at REAL, ended_at REAL);
            CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT,
                                   timestamp REAL, active INTEGER, compacted INTEGER);
            INSERT INTO sessions VALUES ('s1', 'telegram', 'Garden plans', 1762900000, 1762903600);
            INSERT INTO messages VALUES (1, 's1', 'user', 'I am building raised beds this spring.', 1762900000, 1, 0);
            INSERT INTO messages VALUES (2, 's1', 'tool', '{"ok": true}', 1762900001, 1, 0);
            INSERT INTO messages VALUES (3, 's1', 'assistant', 'Good plan.', 1762900002, 1, 0);
            INSERT INTO messages VALUES (4, 's1', 'user', 'An older, compacted turn.', 1762899000, 1, 1);
        """)
        conn.commit()


def test_hermes_chats_are_read_from_its_database_without_changing_it(tmp_path) -> None:
    database = tmp_path / "state.db"
    _hermes_state(database)
    before = database.read_bytes()

    scan = scan_files([database])

    [chat] = list(scan.iter_conversations())
    assert chat.provider == "hermes" and chat.title == "Garden plans"
    assert [(m.role, m.text) for m in chat.messages] == [
        ("user", "I am building raised beds this spring."), ("assistant", "Good plan.")]
    assert database.read_bytes() == before
    assert sorted(path.name for path in tmp_path.iterdir()) == ["state.db"]  # no journal files beside it


def test_hermes_sessions_export_jsonl_is_read_one_session_per_line(tmp_path) -> None:
    export = tmp_path / "hermes-sessions.jsonl"
    export.write_text("\n".join(json.dumps(line) for line in [
        {"id": "a", "source": "cli", "title": "Release", "started_at": 1762900000, "messages": [
            {"role": "user", "content": "Ship on Friday.", "timestamp": 1762900000},
            {"role": "assistant", "content": "Noted.", "timestamp": 1762900001}]},
        {"id": "b", "source": "cli", "started_at": 1762990000, "messages": [
            {"role": "user", "content": "Second chat.", "timestamp": 1762990000}]},
    ]), encoding="utf-8")

    chats = list(scan_files([export]).iter_conversations())

    assert [(chat.id, len(chat.messages)) for chat in chats] == [("a", 2), ("b", 1)]


def test_openclaw_markdown_memory_becomes_dated_facts_without_code_tables_or_titles(tmp_path) -> None:
    (tmp_path / "USER.md").write_text(
        "# USER.md - User Model\n## Directives\n- Prefer concise progress updates.\n- Always use metric units.\n"
        "- **Timezone:** Europe/Berlin\n", encoding="utf-8")
    (tmp_path / "2026-11-12-release.md").write_text(
        "# 2026-11-12\n- Alex started the garden-planner project.\n```bash\nrm -rf build\n```\n"
        "| col | col |\n|---|---|\n## Session summary\nTalked through the release checklist.\n", encoding="utf-8")
    (tmp_path / "IDENTITY.md").write_text("Name: Claw. Emoji: lobster.", encoding="utf-8")

    scan = scan_files([tmp_path / "USER.md", tmp_path / "2026-11-12-release.md", tmp_path / "IDENTITY.md"])

    texts = _texts(scan, "openclaw")
    assert "Prefer concise progress updates." in texts and "Always use metric units." in texts
    assert "Timezone: Europe/Berlin" in texts
    assert not any("USER.md" in text or "rm -rf" in text or "col" in text or "lobster" in text for text in texts)
    daily = [fact for fact in _facts(scan, "openclaw") if "daily_note" in fact.tags]
    assert {fact.date for fact in daily} == {"2026-11-12"}
    assert "Alex started the garden-planner project." in [fact.text for fact in daily]
    assert any(item.name == "IDENTITY.md" for item in scan.skipped)


def _openclaw_events():
    return [
        {"type": "session", "id": "sess-1", "timestamp": "2026-11-12T08:00:00Z"},
        {"type": "message", "id": "u1", "parentId": None, "message": {"role": "user", "content": "Plan my week."}},
        {"type": "message", "id": "a1", "parentId": "u1", "message": {"role": "assistant", "content": "Draft A."}},
        {"type": "message", "id": "a2", "parentId": "u1",  # a retry: the newest branch
         "message": {"role": "assistant", "content": [{"type": "text", "text": "Draft B."}]}},
        {"type": "message", "id": "t1", "parentId": "a2", "message": {"role": "toolResult", "content": "ok"}},
    ]


def test_an_openclaw_transcript_keeps_the_newest_branch(tmp_path) -> None:
    transcript = tmp_path / "3f9c2a1e.jsonl"
    transcript.write_text("\n".join(json.dumps(event) for event in _openclaw_events()), encoding="utf-8")

    [chat] = list(scan_files([transcript]).iter_conversations())

    assert chat.provider == "openclaw" and chat.id == "sess-1"
    assert [(m.role, m.text) for m in chat.messages] == [("user", "Plan my week."), ("assistant", "Draft B.")]


def test_openclaw_chats_are_read_from_its_agent_database(tmp_path) -> None:
    database = tmp_path / "openclaw-agent.sqlite"
    with closing(sqlite3.connect(database)) as conn:
        conn.execute("CREATE TABLE transcript_events (session_id TEXT, seq INTEGER, event_json TEXT, event_zstd BLOB)")
        conn.executemany("INSERT INTO transcript_events VALUES ('sess-1', ?, ?, NULL)",
                         [(index, json.dumps(event)) for index, event in enumerate(_openclaw_events())])
        conn.commit()

    [chat] = list(scan_files([database]).iter_conversations())

    assert [(m.role, m.text) for m in chat.messages] == [("user", "Plan my week."), ("assistant", "Draft B.")]


GROK_EXPORT = {
    "format": "grok-bot-export", "version": 1,
    "bots": [{"name": "Scout", "instructions": "Always cite sources.",
              "memories": ["Weekly digest goes out on Mondays."],
              "user_facts": ["Name: Alex", "Time zone: Europe/Berlin"],
              "skills": [{"name": "digest", "instructions": "…"}], "routines": [], "apps": []}],
}


def test_a_grok_bot_export_file_gives_facts_about_you_its_memories_and_rules(tmp_path) -> None:
    export = tmp_path / "grok-bot-export-scout.json"
    export.write_text(json.dumps(GROK_EXPORT), encoding="utf-8")

    scan = scan_files([export])

    facts = {fact.text: fact for fact in _facts(scan, "grok_bot")}
    assert facts["Name: Alex"].subject_hint == "User"
    assert "agent_note" in facts["Weekly digest goes out on Mondays."].tags
    assert facts["Always cite sources."].tags == ("instruction",)
    assert any("check each memory" in warning for warning in scan.warnings)
    assert any("skills, routines and apps" in item.reason for item in scan.skipped)


def test_a_pasted_grok_bot_answer_with_a_fenced_export_and_a_trailing_comma_is_read() -> None:
    answer = "Here is my export:\n```json\n" + json.dumps(GROK_EXPORT)[:-2] + "],}\n```\nThat's everything."

    scan = scan_text(answer)

    assert "Time zone: Europe/Berlin" in _texts(scan, "grok_bot")


def test_the_three_agents_are_offered_the_way_each_really_works() -> None:
    sources = {source.id: source for source in SOURCES}

    assert sources["hermes"].export == "memories" and not sources["hermes"].prompt_route
    assert sources["openclaw"].export == "memories" and not sources["openclaw"].prompt_route
    assert sources["grok_bot"].export == "none" and sources["grok_bot"].prompt_route
    assert "USER.md" in sources["hermes"].how_to_get and "workspace" in sources["openclaw"].how_to_get
