"""The four approval levels: Block, Ask, Auto (model-reviewed) and Hands-off (A0)."""

from __future__ import annotations

import pytest

from row_bot.approval_policy import (
    approval_label,
    approval_rank,
    decision_for_action,
    known_approval_mode,
    legacy_developer_mode_to_approval_mode,
    legacy_safety_mode_to_approval_mode,
    normalize_approval_mode,
    stricter_approval_mode,
)

pytestmark = pytest.mark.subsystem


def test_levels_and_their_labels():
    assert [approval_label(mode) for mode in ("block", "approve", "auto", "full")] == [
        "Block", "Ask", "Auto", "Hands-off"]
    assert normalize_approval_mode("") == "approve"  # Ask stays the default.
    assert normalize_approval_mode("ask") == "approve"
    assert normalize_approval_mode("full") == "full"
    # Hands-off by its name, as a person types it, is the same stored level.
    for name in ("Hands-off", "hands off", "hands_off"):
        assert normalize_approval_mode(name) == "full"


@pytest.mark.parametrize("legacy", ["allow_all", "allow-all", "Allow All", "auto", "auto_edit", "agent_run"])
def test_every_legacy_auto_value_is_reviewed_auto(legacy):
    assert normalize_approval_mode(legacy) == "auto"
    assert legacy_safety_mode_to_approval_mode(legacy) == "auto"
    assert known_approval_mode(legacy) == "auto"


@pytest.mark.parametrize("value", ["allow_all", "auto", "agent_run", "auto_edit", "approve", "block", "yes", "",
                                   "everything", "unrestricted", None])
def test_nothing_but_full_itself_becomes_full_autonomy(value):
    assert normalize_approval_mode(value) != "full"
    assert legacy_safety_mode_to_approval_mode(value) != "full"
    assert legacy_developer_mode_to_approval_mode(value) != "full"


def test_legacy_developer_modes_map_to_the_levels():
    assert legacy_developer_mode_to_approval_mode("read_only") == "block"
    assert legacy_developer_mode_to_approval_mode("ask") == "approve"
    assert legacy_developer_mode_to_approval_mode("auto_edit") == "auto"
    assert legacy_developer_mode_to_approval_mode("agent_run") == "auto"


def test_an_unknown_value_is_not_a_level():
    assert known_approval_mode("everything") is None
    assert normalize_approval_mode("everything") == "approve"


@pytest.mark.parametrize(("mode", "decision"), [
    ("block", "block"),
    ("approve", "ask"),
    # Auto and Hands-off still need an approval; the reviewer, or the level itself, gives it.
    ("auto", "ask"),
    ("full", "ask"),
    ("allow_all", "ask"),
])
def test_an_action_that_needs_approval_needs_it_at_every_level_but_block(mode, decision):
    assert decision_for_action(mode) == decision


@pytest.mark.parametrize("mode", ["block", "approve", "auto", "full"])
def test_read_only_actions_never_need_an_approval(mode):
    assert decision_for_action(mode, read_only=True) == "allow"


@pytest.mark.parametrize(("mode", "decision"), [
    ("block", "block"), ("approve", "ask"), ("auto", "allow"), ("full", "allow")])
def test_the_persons_own_action_in_the_app_needs_no_more_under_auto_or_full(mode, decision):
    assert decision_for_action(mode, by_person=True) == decision


def test_a_narrowed_run_takes_the_stricter_level():
    assert [approval_rank(mode) for mode in ("block", "approve", "auto", "full")] == [0, 1, 2, 3]
    assert stricter_approval_mode("full", "auto") == "auto"
    assert stricter_approval_mode("approve", "full") == "approve"
    assert stricter_approval_mode("allow_all", "full") == "auto"
