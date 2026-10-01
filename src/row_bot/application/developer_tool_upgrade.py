"""Developer tools on for people who upgrade (B259).

The Developer tool was off by default until the conversation-first workspace
turned it on. Saved settings outrank defaults, and until then saving any tool
setting also wrote every tool's current value, so an upgraded profile holds
``developer: false`` whether or not the person ever chose it, and nothing
saved says which. So this step runs once, on the first start of a version that
has it: an off it finds then was written while off was the default, is treated
as that default and turned on, and one notice says so. The step leaves a mark
in the same file, so an off saved after it (the person's choice under the new
default) is kept. New profiles get the default.

The Developer skills are tool guides, active exactly while the tool is on, so
they follow it. Its actions keep their approval gates.
"""
from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)

UPGRADE_MARK = "developer_default_on"
NOTICE = "Developer tools are now on · Settings › Tools"


def turn_on_developer_tools_once() -> bool:
    """Apply the one-time step; True when it turned the Developer tool on."""
    from row_bot import tool_configuration
    from row_bot.docs_capture import is_docs_real_data_capture

    if is_docs_real_data_capture():
        return False
    path = tool_configuration.configuration_path()
    with tool_configuration.LOCK:
        saved = tool_configuration.read_saved(path)
        marks = saved.document.get("upgrades")
        marks = marks if isinstance(marks, dict) else {}
        if marks.get(UPGRADE_MARK):
            return False
        document = tool_configuration.editable_document(saved)
        tools = tool_configuration.tools_map(document)
        turned_on = tools.get("developer") is False
        if turned_on:
            tools["developer"] = True
        document["upgrades"] = {**marks, UPGRADE_MARK: True}
        tool_configuration.legacy_publish(path, document, expected_digest=saved.digest)
    registry = sys.modules.get("row_bot.tools.registry")
    if registry is not None:
        registry.reload_saved_config()
    if turned_on:
        from row_bot.application.app_notices import app_notices

        logger.info("Turned the Developer tool on: its saved off was the old default")
        # Information shows only for what the person asked for; a change
        # made to their settings on their behalf is shown as if they had.
        app_notices.post(title=NOTICE, message="", source="tools", requested=True)
    return turned_on
