"""Row-Bot changes its own settings on one path (self system, WP2): checked before anyone is asked,
approved by the chat's approval level, saved by the owner Settings uses, and undoable.

Security behaviour: these stay in the pull-request lane.
"""
from __future__ import annotations

import json

import pytest

from row_bot import approval_review, self_settings
from row_bot.approval_review import ReviewVerdict
from row_bot.tools import approval_gate

pytestmark = pytest.mark.subsystem


@pytest.fixture
def profile(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("row_bot.agent.clear_agent_cache", lambda: None)
    return tmp_path


@pytest.fixture
def level():
    """The chat's approval level for the test (Ask until it says otherwise)."""
    from row_bot import agent

    tokens = []

    def choose(mode: str) -> None:
        tokens.append(agent._approval_mode_var.set(mode))

    choose("approve")
    yield choose
    for token in reversed(tokens):
        agent._approval_mode_var.reset(token)


@pytest.fixture
def person(monkeypatch):
    """The person's answers on cards, and every card they saw."""
    import langgraph.types

    state = {"answer": True, "cards": []}

    def interrupt(payload):
        state["cards"].append(payload)
        return state["answer"]

    monkeypatch.setattr(langgraph.types, "interrupt", interrupt)
    return state


@pytest.fixture
def reviewer(monkeypatch):
    state = {"verdict": ReviewVerdict("approve", "You asked for this."), "seen": []}

    def review(payload, *, risks=(), **_kwargs):
        state["seen"].append((dict(payload), tuple(risks)))
        return state["verdict"]

    monkeypatch.setattr(approval_review, "review", review)
    return state


def _dream(profile) -> dict:
    return json.loads((profile / "dream_config.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize(("setting", "value", "why"), [
    ("dream_window", "5-5", "two different hours"),
    ("dream_window", "25-3", "two different hours"),
    ("context_size", "5", "between"),
    ("cloud_context_size", "lots", "whole number"),
    ("name", "   ", "1 to 128 characters"),
    ("log_detail", "loud", "debug, info, warning or error"),
    ("dream_cycle", "maybe", "'on' or 'off'"),
    ("skill_toggle", "no-colon", "skill_name:on"),
    ("tool_toggle", "no-such-tool-anywhere:on", "Unknown tool"),
])
def test_an_invalid_value_is_refused_before_anyone_is_asked(profile, level, person, setting, value, why):
    """G13: the person approved "Set ... to 5 tokens" and only then heard it couldn't be done."""
    answer = self_settings.change(setting, value)

    assert why in answer
    assert person["cards"] == []


def test_an_image_model_must_be_one_row_bot_can_use(profile, level, person, monkeypatch):
    """G14: any text used to be saved as the image model."""
    import row_bot.tools.image_gen_tool as image_gen_tool

    monkeypatch.setattr(image_gen_tool, "get_available_image_models", lambda: {"openai/gpt-image-2": "GPT Image 2"})

    answer = self_settings.change("image_gen_model", "dall-e-9000")

    assert "isn't one of the image models" in answer and "GPT Image 2" in answer
    assert person["cards"] == []


def test_ask_shows_the_change_in_words_with_its_row_then_saves_it_with_its_undo(profile, level, person):
    answer = self_settings.change("dream_window", "2-6")

    [card] = person["cards"]
    assert card["label"] == "Change the Dream Cycle hours"
    assert card["change"] == {"label": "Dream Cycle", "where": "Settings › Preferences › Dream Cycle",
                              "href": "/settings/preferences#dream-cycle", "before": "1:00–5:00",
                              "after": "2:00–6:00", "tier": "preference"}
    assert card["args"] == {"setting": "dream_window", "value": "2-6"}
    assert (_dream(profile)["window_start"], _dream(profile)["window_end"]) == (2, 6)
    assert answer.splitlines()[0] == "Dream cycle window set to 2:00–6:00."
    assert self_settings.parse_undo(answer) == {"setting": "dream_window", "value": "1-5", "now": "2-6"}


def test_a_change_the_person_declines_is_not_saved(profile, level, person):
    person["answer"] = False

    answer = self_settings.change("dream_window", "2-6")

    assert answer == "Dream Cycle wasn't changed."
    assert not (profile / "dream_config.json").exists()


def test_a_value_it_already_has_asks_nothing(profile, level, person):
    assert self_settings.change("dream_window", "1-5") == "Dream Cycle is already 1:00–5:00."
    assert person["cards"] == []


def test_auto_saves_what_the_reviewer_approves_and_records_why(profile, level, person, reviewer):
    level("auto")

    with approval_gate.collect_decisions() as decisions:
        answer = self_settings.change("dream_cycle", "off")

    assert answer.startswith("Dream cycle disabled.")
    assert person["cards"] == [] and _dream(profile)["enabled"] is False
    assert decisions == ["Approval: approved by the reviewer: You asked for this"]
    [(_payload, risks)] = reviewer["seen"]
    assert risks == ("changes one of the person's Row-Bot settings, which they can undo",)


def test_auto_always_asks_the_person_to_turn_on_a_tool_that_acts_on_this_computer(profile, level, person,
                                                                                    reviewer, monkeypatch):
    from row_bot.tools import registry

    level("auto")
    turned: list[tuple[str, bool]] = []
    monkeypatch.setattr(registry, "is_enabled", lambda name: False)
    monkeypatch.setattr(registry, "set_enabled", lambda name, on: turned.append((name, on)))
    person["answer"] = False

    answer = self_settings.change("tool_toggle", "shell:on")

    [card] = person["cards"]
    assert card["setup"]["kind"] == "tool" and card["change"]["tier"] == "security"
    assert card["review"]["reason"] == "Only you can turn on a tool that acts on this computer."
    assert reviewer["seen"] == [] and turned == []
    assert "chose Not now" in answer


def test_hands_off_saves_without_asking_and_records_it(profile, level, person):
    level("full")

    with approval_gate.collect_decisions() as decisions:
        answer = self_settings.change("log_detail", "warning")

    assert answer.startswith("Log detail set to Warning.")
    assert person["cards"] == [] and decisions == ["Approval: allowed by Hands-off"]
    assert json.loads((profile / "user_config.json").read_text(encoding="utf-8"))["file_log_level"] == "WARNING"


def test_block_refuses_on_both_turn_on_paths(profile, level, person, monkeypatch):
    """G22: the setup tool's "Turn on" used to ask the person under Block while the setting tool refused."""
    from row_bot.tools import registry

    level("block")
    monkeypatch.setattr(registry, "is_enabled", lambda name: False)
    monkeypatch.setattr(registry, "set_enabled", lambda name, on: pytest.fail("Block turned a tool on"))

    assert self_settings.change("tool_toggle", "web_search:on").startswith("BLOCKED")
    assert self_settings.turn_on("developer", "Developer tools", "Row-Bot needs Developer tools.") is False
    assert person["cards"] == []


def test_row_bot_never_turns_off_its_own_settings_tool(profile, level, person):
    """G16: with row_bot_status off, Row-Bot could turn nothing back on."""
    answer = self_settings.change("tool_toggle", "row_bot_status:off")

    assert "can't turn off" in answer and "/settings/tools#built-in-tools" in answer
    assert person["cards"] == []


def test_computer_use_is_turned_on_only_once_the_person_has_set_it_up(profile, level, person, monkeypatch):
    import row_bot.tools.computer_use_tool  # noqa: F401  (registers the Computer use tool)
    from row_bot.computer_use import readiness
    from row_bot.tools import registry

    monkeypatch.setattr(registry, "is_enabled", lambda name: False)
    monkeypatch.setattr(readiness, "disclosure_acknowledged", lambda: False)

    answer = self_settings.change("tool_toggle", "computer_use:on")

    assert "needs its setup first" in answer and "/settings/system#browser-computer-use" in answer
    assert "can't accept the notice or install it" in answer
    assert person["cards"] == []


def test_the_reading_limit_can_go_back_to_auto(profile, level, person, monkeypatch):
    """G15: only a number was accepted, so Auto was out of reach."""
    from row_bot import models

    calls: list[str] = []
    monkeypatch.setattr(models, "get_local_context_mode", lambda: "fixed")
    monkeypatch.setattr(models, "get_user_context_size", lambda: 65_536)
    monkeypatch.setattr(models, "set_context_size_auto", lambda: calls.append("auto"))

    answer = self_settings.change("context_size", "auto")

    assert person["cards"][0]["change"]["before"] == "65,536 tokens"
    assert person["cards"][0]["change"]["after"] == "Auto"
    assert calls == ["auto"] and answer.startswith("Local context size set to Auto.")


def test_renaming_row_bot_keeps_learn_new_skills_as_the_person_set_it(profile, level, person, reload_for_data_dir):
    """Saving the name used to rewrite the identity without "Learn new skills", turning it back on."""
    [identity] = reload_for_data_dir(profile, "row_bot.identity")
    (profile / "user_config.json").write_text(json.dumps({"identity": {"name": "Row-Bot", "self_improvement": False}}),
                                              encoding="utf-8")

    answer = self_settings.change("name", "Rowan")

    assert answer.splitlines()[0] == "Assistant name changed to: Rowan"
    assert identity.get_assistant_name() == "Rowan"
    assert identity.is_self_improvement_enabled() is False


def test_the_persons_undo_restores_the_old_value(profile, level, person):
    answer = self_settings.change("dream_window", "2-6")
    undo = self_settings.parse_undo(answer)
    person["cards"].clear()

    result = self_settings.undo(undo["setting"], undo["value"], undo["now"])

    assert result.splitlines()[0] == "Dream cycle window set to 1:00–5:00."
    assert (_dream(profile)["window_start"], _dream(profile)["window_end"]) == (1, 5)
    assert person["cards"] == []  # The person pressed Undo: nothing to approve.


def test_undo_leaves_a_setting_changed_since_alone(profile, level, person):
    undo = self_settings.parse_undo(self_settings.change("dream_window", "2-6"))
    self_settings.change("dream_window", "3-7")

    result = self_settings.undo(undo["setting"], undo["value"], undo["now"])

    assert result == "Dream Cycle has changed since, so it wasn't undone. Change it in Settings."
    assert (_dream(profile)["window_start"], _dream(profile)["window_end"]) == (3, 7)


def test_running_the_dream_cycle_needs_it_on(profile, level, person):
    self_settings.change("dream_cycle", "off")
    person["cards"].clear()

    assert "The Dream Cycle is off" in self_settings.change("run_dream_cycle", "now")
    assert person["cards"] == []


def test_settings_lists_what_row_bot_can_change_and_what_it_never_does(profile):
    text = self_settings.describe()

    assert "- dream_window: Dream Cycle [Preferences › Dream Cycle](/settings/preferences#dream-cycle). Now: 1:00–5:00." in text
    assert "- log_detail: Log detail [System › Log detail](/settings/system#logging.level)." in text
    assert "never changes these" in text and "approval levels" in text and "Learning" in text
