from __future__ import annotations

from row_bot.ui.live_control import (
    browser_live_control_view,
    computer_live_control_view,
    select_live_control_view,
)


def test_computer_control_is_visible_only_to_owning_thread() -> None:
    snapshot = {
        "active": True,
        "paused": False,
        "thread_id": "owner",
        "state": "acting",
        "app": "Calculator",
        "window": "Calculator",
        "has_thumbnail": True,
        "revision": 7,
    }
    owner = computer_live_control_view(snapshot, "owner")
    other = computer_live_control_view(snapshot, "other")
    assert owner.active
    assert owner.state_label == "Acting"
    assert owner.can_take_over
    assert owner.can_preview
    assert not other.active


def test_computer_takeover_exposes_resume_and_shields_preview() -> None:
    view = computer_live_control_view(
        {
            "active": True,
            "paused": True,
            "thread_id": "owner",
            "state": "waiting_user",
            "app": "Calculator",
            "window": "Calculator",
            "has_thumbnail": False,
        },
        "owner",
    )
    assert view.state_label == "Waiting for you"
    assert view.can_resume
    assert not view.can_take_over
    assert view.can_preview
    assert not view.has_preview
    assert view.preview_shielded


def test_resuming_and_needs_attention_states_are_truthful_and_not_takeoverable() -> None:
    resuming = computer_live_control_view(
        {
            "active": True,
            "thread_id": "owner",
            "state": "resuming",
            "app": "Notepad",
        },
        "owner",
    )
    needs_attention = computer_live_control_view(
        {
            "active": True,
            "thread_id": "owner",
            "state": "needs_attention",
            "app": "Paint",
        },
        "owner",
    )

    assert resuming.state_label == "Resuming"
    assert not resuming.can_take_over
    assert needs_attention.state_label == "Needs attention"
    assert needs_attention.can_take_over


def test_shared_shell_prioritizes_exclusive_computer_lease_without_merging_engines() -> None:
    computer = {
        "active": True,
        "thread_id": "owner",
        "state": "observing",
        "app": "Calculator",
    }
    browser = {
        "active": True,
        "thread_id": "owner",
        "state": "observing",
        "target": "Example",
    }
    selected = select_live_control_view(computer, browser, "owner")
    assert selected.engine == "computer"
    assert selected.scope == "This app only"


def test_browser_control_is_task_tab_scoped_and_has_separate_takeover_semantics() -> None:
    view = browser_live_control_view(
        {
            "active": True,
            "thread_id": "owner",
            "state": "observing",
            "target": "Example",
            "site": "example.com",
            "has_thumbnail": True,
            "revision": 4,
        },
        "owner",
    )
    assert view.engine == "browser"
    assert view.scope == "This task tab only"
    assert view.can_take_over
    assert not view.can_resume
    assert view.can_preview
    assert view.has_preview


def test_browser_and_computer_share_the_same_live_picture_contract() -> None:
    snapshots = (
        {
            "active": True,
            "thread_id": "owner",
            "state": "observing",
            "app": "Calculator",
            "has_thumbnail": True,
        },
        {
            "active": True,
            "thread_id": "owner",
            "state": "observing",
            "target": "Example",
            "has_thumbnail": True,
        },
    )
    computer = computer_live_control_view(snapshots[0], "owner")
    browser = browser_live_control_view(snapshots[1], "owner")

    assert computer.can_preview == browser.can_preview is True
    assert computer.has_preview == browser.has_preview is True
    assert computer.preview_shielded == browser.preview_shielded is False
