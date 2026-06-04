from __future__ import annotations

from row_bot.approval_policy import (
    approval_label,
    decision_for_action,
    legacy_developer_mode_to_approval_mode,
    legacy_safety_mode_to_approval_mode,
    normalize_approval_mode,
)


def test_shared_approval_mode_normalization_and_labels():
    assert normalize_approval_mode("block") == "block"
    assert normalize_approval_mode("ask") == "approve"
    assert normalize_approval_mode("auto") == "allow_all"
    assert normalize_approval_mode("allow-all") == "allow_all"
    assert approval_label("approve") == "Ask"
    assert approval_label("allow_all") == "Auto"


def test_legacy_developer_modes_map_to_shared_modes():
    assert legacy_developer_mode_to_approval_mode("read_only") == "block"
    assert legacy_developer_mode_to_approval_mode("ask") == "approve"
    assert legacy_developer_mode_to_approval_mode("auto_edit") == "allow_all"
    assert legacy_developer_mode_to_approval_mode("agent_run") == "allow_all"


def test_legacy_safety_modes_map_to_shared_modes():
    assert legacy_safety_mode_to_approval_mode("block") == "block"
    assert legacy_safety_mode_to_approval_mode("approve") == "approve"
    assert legacy_safety_mode_to_approval_mode("allow_all") == "allow_all"
    assert legacy_safety_mode_to_approval_mode("read_only") == "block"


def test_decision_for_action_uses_block_ask_auto():
    assert decision_for_action("block") == "block"
    assert decision_for_action("approve") == "ask"
    assert decision_for_action("allow_all") == "allow"
    assert decision_for_action("block", read_only=True) == "allow"
