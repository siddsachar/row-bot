"""A chat app shows Row-Bot's own links as words: nothing there can open them (self system)."""
from __future__ import annotations

import pytest

from row_bot.channels.agent_output import assemble_agent_answer

pytestmark = pytest.mark.subsystem


def test_settings_rows_and_guides_become_words_a_chat_app_can_show():
    answer = assemble_agent_answer(
        "Open [Dream Cycle](/settings/preferences#dream-cycle), see [Privacy](/docs/settings/system#privacy), "
        "check [Monitor](/?tab=monitor) and [Settings › Models](/app-v2/settings/models).",
        [],
    )

    assert answer == ("Open Dream Cycle (Settings › Preferences), see Privacy "
                      "(https://row-bot.ai/docs/settings/system#privacy), check Monitor and Settings › Models.")


def test_other_links_and_tool_reports_stay_as_they_were():
    assert assemble_agent_answer("[Example](https://example.test)", []) == "[Example](https://example.test)"
    assert assemble_agent_answer("", ["[Dream Cycle](/settings/preferences)"]) == "[Dream Cycle](/settings/preferences)"
