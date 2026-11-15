"""row_bot_status category='features': what Row-Bot does, from its own guides (G1), never a hand-kept list."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.subsystem


def test_features_come_from_the_guides_by_area(tmp_path, monkeypatch):
    from row_bot import guides
    from row_bot.tools import row_bot_status_tool as status

    docs = tmp_path / "docs"
    (docs / "chat").mkdir(parents=True)
    (docs / "reference").mkdir()
    (docs / "index.mdx").write_text("---\ntitle: Row-Bot Documentation\n---\nStart.\n", encoding="utf-8")
    (docs / "chat" / "index.mdx").write_text(
        "---\ntitle: Chat\ndescription: Talk to Row-Bot and let it use tools.\n---\nText.\n", encoding="utf-8")
    (docs / "chat" / "pages.mdx").write_text("---\ntitle: Pages And Questions\n---\nText.\n", encoding="utf-8")
    (docs / "reference" / "tools.mdx").write_text("---\ntitle: Tools Reference\n---\nTable.\n", encoding="utf-8")
    monkeypatch.setattr(guides, "guides_dir", lambda: docs)

    text = status._row_bot_status("features")

    assert text.splitlines() == [
        "**Row-Bot features** (by area, from its guides for this version; row_bot_docs reads any of them)",
        "- Chat: Talk to Row-Bot and let it use tools. Guides: Pages And Questions.",
    ]


def test_features_say_so_when_an_install_has_no_guides(tmp_path, monkeypatch):
    from row_bot import guides
    from row_bot.tools import row_bot_status_tool as status

    monkeypatch.setattr(guides, "guides_dir", lambda: tmp_path / "missing")

    assert "This install has no guides" in status._row_bot_status("features")


def test_every_chat_gets_a_short_self_description_that_points_to_the_guides(tmp_path, monkeypatch):
    from row_bot import self_knowledge

    monkeypatch.setattr("row_bot.identity.is_self_improvement_enabled", lambda: False)
    block = self_knowledge.build_static_self_knowledge_block()

    assert "search your guides with\nrow_bot_docs" in block and "use row_bot_status" in block
    # No feature list and no fixed folder path: the configured workspace may be anywhere.
    assert "Core capabilities:" not in block
    assert "~/Documents" not in block
    assert len(block) < 600



def test_the_turns_state_leaves_out_counts_that_change_every_turn_and_status_says_them(tmp_path, monkeypatch):
    """G9: a new memory or a Dream Cycle doesn't send the whole state block again; row_bot_status answers both."""
    from row_bot import dream_cycle, knowledge_graph, self_knowledge
    from row_bot.tools import row_bot_status_tool as status

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    entities = [658]
    monkeypatch.setattr(knowledge_graph, "count_entities", lambda: entities[0])
    monkeypatch.setattr(knowledge_graph, "count_relations", lambda: 1200)
    monkeypatch.setattr(dream_cycle, "get_dream_status", lambda: {"last_summary": "2 insight(s), in 92s"})

    before = self_knowledge.get_dynamic_state()
    entities[0] += 1  # A memory saved during the turn.
    after = self_knowledge.get_dynamic_state()

    assert before == after, "the state is the same after one more memory"
    assert "Knowledge graph" not in before and "dream cycle" not in before.lower()
    memory = status._row_bot_status("memory")
    assert "- Entities: 659" in memory and "- Last Dream Cycle: 2 insight(s), in 92s" in memory


def test_the_turns_state_names_the_chats_own_model(tmp_path, monkeypatch):
    """R12 (12 Nov): an Opus chat's turn context said "Current model: model:codex:gpt-6.1-sol", the default."""
    import contextvars

    from row_bot import models, self_knowledge

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(models, "get_current_model", lambda: "model:codex:gpt-6.1-sol")

    def state_with(override: str) -> str:
        models.set_active_model_override(override)
        return self_knowledge.get_dynamic_state()

    assert "- Current model: model:codex:gpt-6.1-sol" in contextvars.copy_context().run(state_with, "")
    chosen = contextvars.copy_context().run(state_with, "model:claude_subscription:claude-opus-5-5")
    assert "- Current model: model:claude_subscription:claude-opus-5-5" in chosen
