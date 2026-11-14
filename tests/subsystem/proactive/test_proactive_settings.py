"""Row-Bot changes Proactive's settings through the self system's one path: checked, approved, undoable."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.subsystem


def _change(setting_id: str, value: str) -> str:
    """What ``row_bot_update_setting`` does once the approval level said yes."""
    from row_bot import self_settings

    setting = self_settings.BY_ID[setting_id]
    return self_settings.save(setting.prepare(setting, value))


def test_the_level_changes_and_undoes_through_the_settings_registry(proactive):
    from row_bot import self_settings

    said = _change("proactive_level", "balanced")

    assert proactive.settings.load()["level"] == "balanced"
    undo = self_settings.parse_undo(said)
    assert (undo["value"], undo["now"], undo["before"], undo["after"]) == ("quiet", "balanced", "Quiet", "Balanced")
    assert self_settings.undo(undo["setting"], undo["value"], undo["now"])[0] == "undone"
    assert proactive.settings.load()["level"] == "quiet"


@pytest.mark.parametrize("setting_id,value,check", [
    ("proactive_quiet_hours", "21:00-06:45", lambda saved: (saved["quiet_start"], saved["quiet_end"]) == ("21:00", "06:45")),
    ("proactive_desktop_context", "off", lambda saved: saved["desktop_context"] is False),
    ("proactive_kind", "date:off", lambda saved: saved["kinds"]["date"] is False),
    ("proactive_source", "mail:off", lambda saved: saved["sources"]["mail"] is False),
    ("proactive_urgent", "meeting_prep:on", lambda saved: saved["urgent_kinds"] == ["meeting_prep"]),
])
def test_each_registered_setting_saves_what_it_says(proactive, setting_id, value, check):
    from row_bot import self_settings

    before = proactive.settings.load()
    said = _change(setting_id, value)

    assert check(proactive.settings.load()), said
    undo = self_settings.parse_undo(said)
    assert self_settings.undo(undo["setting"], undo["value"], undo["now"])[0] == "undone"
    assert proactive.settings.load() == before


@pytest.mark.parametrize("setting_id,value", [
    ("proactive_level", "loud"),
    ("proactive_quiet_hours", "22:00-22:00"),
    ("proactive_kind", "everything:on"),
    ("proactive_urgent", "meeting_prep"),
])
def test_a_change_that_says_nothing_valid_is_refused_before_any_card(proactive, setting_id, value):
    from row_bot import self_settings

    before = proactive.settings.load()
    setting = self_settings.BY_ID[setting_id]
    with pytest.raises(self_settings.SettingRefused):
        setting.prepare(setting, value)
    assert proactive.settings.load() == before
