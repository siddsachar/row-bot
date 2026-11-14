from __future__ import annotations

from pathlib import Path


def _reload_skill_modules(tmp_path: Path, reload_for_data_dir):
    return tuple(reload_for_data_dir(tmp_path, "row_bot.skills", "row_bot.skills_activation", "row_bot.channels.commands"))


def _write_skill(root: Path, name: str, *, description: str, tools: list[str] | None = None) -> None:
    skill_dir = root / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        f"name: {name}",
        f"display_name: {name.replace('_', ' ').title()}",
        "icon: '*'",
        f"description: {description}",
        "enabled_by_default: true",
        "version: 1.0",
    ]
    if tools:
        lines.append("tools:")
        lines.extend(f"  - {tool}" for tool in tools)
    lines.extend(["---", "", f"Instructions for {name}."])
    (skill_dir / "SKILL.md").write_text("\n".join(lines), encoding="utf-8")


def test_thread_scoped_command_tokens_include_reset_aliases(tmp_path, reload_for_data_dir):
    _skills, _activation, commands = _reload_skill_modules(tmp_path, reload_for_data_dir)

    for text in ("/skill foo", "/skills", "/skill-reset", "/skillreset", "/skill_reset", "/noskill", "/reasoning high"):
        assert commands.is_thread_scoped_command(text)
    for text in ("/help", "/status", "/tools", "/new", "/stop", "/model"):
        assert not commands.is_thread_scoped_command(text)


def test_channel_reasoning_dispatch_uses_conversation_thread_id(tmp_path, monkeypatch, reload_for_data_dir):
    _skills, _activation, commands = _reload_skill_modules(tmp_path, reload_for_data_dir)
    import row_bot.agent as agent
    import row_bot.models as models
    import row_bot.providers.reasoning as reasoning
    import row_bot.providers.resolution as resolution
    import row_bot.threads as threads

    calls = []
    monkeypatch.setattr(threads, "_get_thread_model_override", lambda thread_id: "model:openai:gpt-5")
    monkeypatch.setattr(
        resolution,
        "resolve_provider_config",
        lambda selected, allow_legacy_local=True: type("Resolved", (), {"selection_ref": selected})(),
    )
    monkeypatch.setattr(
        reasoning,
        "apply_reasoning_command",
        lambda thread_id, model_ref, arg: calls.append((thread_id, model_ref, arg)) or "Reasoning: High.",
    )
    monkeypatch.setattr(agent, "clear_agent_cache", lambda: None)
    monkeypatch.setattr(models, "clear_llm_cache", lambda: None)

    response = commands.dispatch("sms", "/reasoning high", thread_id="sms-thread")

    assert response == "Reasoning: High."
    assert calls == [("sms-thread", "model:openai:gpt-5", "high")]


def test_channel_dispatch_skill_reset_aliases_use_thread_id(tmp_path, reload_for_data_dir):
    _write_skill(tmp_path, "meeting_notes", description="Summarize meetings")
    skills, activation, commands = _reload_skill_modules(tmp_path, reload_for_data_dir)
    skills.load_skills()

    commands.dispatch("sms", "/skill meeting_notes", thread_id="sms_1")
    assert activation.resolve_active_skill_names("sms_1") == ["meeting_notes"]

    for reset_text in ("/skill-reset", "/skillreset", "/skill_reset"):
        commands.dispatch("sms", "/skill meeting_notes", thread_id="sms_1")
        response = commands.dispatch("sms", reset_text, thread_id="sms_1")
        assert response and "reset" in response.lower()
        assert activation.resolve_active_skill_names("sms_1") == skills.get_default_active_skill_names("chat")


def test_channel_skills_text_fallback_lists_available_runtime_skills(tmp_path, reload_for_data_dir):
    _write_skill(tmp_path, "meeting_notes", description="Summarize meetings")
    _write_skill(tmp_path, "browser_guide", description="Browser tool guide", tools=["browser"])
    skills, _activation, commands = _reload_skill_modules(tmp_path, reload_for_data_dir)
    skills.load_skills()

    response = commands.dispatch("slack", "/skills", thread_id="slack_1")
    assert response
    assert "Available skills" in response
    assert "Brain Dump" in response  # The first page lists ten; the query below finds the rest.
    assert "browser_guide" not in response

    response = commands.dispatch("slack", "/skills meeting", thread_id="slack_1")
    assert response
    assert "Meeting Notes" in response


def test_channel_help_mentions_discoverable_skill_commands(tmp_path, reload_for_data_dir):
    _skills, _activation, commands = _reload_skill_modules(tmp_path, reload_for_data_dir)

    help_text = commands.cmd_help("discord")
    assert "/skills <query>" in help_text
    assert "/skill <query>" in help_text
    assert "/noskill [query]" in help_text
    assert "/skillreset" in help_text
