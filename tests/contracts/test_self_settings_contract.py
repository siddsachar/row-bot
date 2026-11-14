"""The settings Row-Bot may change name the rows the Settings page has (self system).

Read with the docs inventory's parser of the client's settings registry, so a renamed or moved row fails
here instead of sending the person to a row that isn't there.
"""
from __future__ import annotations

import pytest

from row_bot import self_settings
from scripts.docs.collect_inventory import collect_settings_controls

pytestmark = pytest.mark.contract

# Settings named by their page, not by one row (a skill's own page, the list of tools).
_PAGE_LABELS = {"skill_toggle", "skill_pin", "tool_toggle"}


def _rows() -> dict[tuple[str, str], dict]:
    return {(row["page_id"], row["anchor"]): row for row in collect_settings_controls()}


def test_every_setting_row_bot_changes_is_a_row_with_the_same_name():
    rows = _rows()
    for setting in self_settings.SETTINGS:
        if not setting.page:
            continue  # This chat's model lives on the model pill, not in Settings.
        if not setting.anchor:
            assert any(page == setting.page for page, _anchor in rows), setting.id
            continue
        row = rows.get((setting.page, setting.anchor))
        assert row is not None, f"{setting.id}: no row {setting.page}#{setting.anchor}"
        assert row["page"] == setting.page_title, setting.id
        if setting.id not in _PAGE_LABELS:
            assert row["label"] == setting.label, setting.id


def test_every_tool_row_a_turn_on_card_shows_exists():
    rows = _rows()
    for name, (page, _title, anchor) in self_settings._TOOL_ROWS.items():
        if anchor:
            assert (page, anchor) in rows, name
        else:
            assert any(row_page == page for row_page, _anchor in rows), name
