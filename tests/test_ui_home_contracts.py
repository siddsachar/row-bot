from __future__ import annotations

import re
from pathlib import Path


ROOT = Path("src/row_bot")


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_monochrome_icon_helper_maps_legacy_workflow_icons():
    from row_bot.ui.iconography import icon_select_options, material_icon_for

    assert material_icon_for("⚡") == "bolt"
    assert material_icon_for("🧠") == "psychology"
    assert material_icon_for("📡") == "sensors"
    assert material_icon_for("smart_toy") == "smart_toy"
    assert material_icon_for("") == "bolt"
    assert material_icon_for(None, fallback="lightbulb") == "lightbulb"
    assert material_icon_for("not an icon", fallback="lightbulb") == "lightbulb"
    assert material_icon_for("??", fallback="not valid") == "bolt"

    options = icon_select_options("⚡")
    assert options["bolt"] == "Workflow"
    assert all(re.fullmatch(r"[a-z][a-z0-9_]*", key) for key in options)


def test_icon_options_no_longer_introduce_emoji_values():
    from row_bot.ui.constants import ICON_OPTIONS

    emoji_re = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]")

    assert ICON_OPTIONS
    assert "bolt" in ICON_OPTIONS
    assert all(not emoji_re.search(value) for value in ICON_OPTIONS)
    assert all(re.fullmatch(r"[a-z][a-z0-9_]*", value) for value in ICON_OPTIONS)


def test_home_workflow_and_monitor_icons_are_monochrome_source_contracts():
    home_src = _read("ui/home.py")

    assert "material_icon_for(tk.get(\"icon\"))" in home_src
    assert "innerHTML=\"{tk" not in home_src
    assert 'ui.label("⚡ Workflows")' not in home_src
    assert "info += \" · 📅 Daily\"" not in home_src
    assert "info = \"🔔 Reminder\"" not in home_src
    assert "row_bot.channels.telegram" not in home_src
    assert "Telegram —" not in home_src
    assert "No channels configured." not in home_src
    assert "Knowledge Extraction" in home_src
    assert "Dream Cycle" in home_src
    assert "Recent Logs" in home_src
    assert "View Full Log" in home_src


def test_command_center_workflow_icons_are_mapped_source_contracts():
    src = _read("ui/command_center.py")

    assert "from row_bot.ui.iconography import material_icon_for" in src
    assert "icon = material_icon_for(info.get(\"icon\"))" in src
    assert "material_icon_for(item.get(\"task_icon\"))" in src
    assert "ui.label(icon)" not in src
    assert "CATEGORY_ICONS.get(cat" in src
    assert "f\"💬 {suggestion}\"" not in src
    assert "t[\"id\"]: t[\"name\"]" in src
    assert "f\"{t.get('icon', '*')} {t['name']}\"" not in src


def test_workflow_and_skill_icon_pickers_use_material_options():
    task_dialog = _read("ui/task_dialog.py")
    settings = _read("ui/settings.py")

    assert "from row_bot.ui.iconography import icon_select_options, material_icon_for" in task_dialog
    assert "icon_select_options(_icon)" in task_dialog
    assert '"icon": icon_sel.value or "bolt"' in task_dialog
    assert 'cur_icon = icon_sel.value or "bolt"' in task_dialog
    assert "ICON_OPTIONS" not in task_dialog
    assert "icon_select_options(_icon, fallback=\"auto_awesome\")" in settings
    assert "_icon_val = icon_sel.value or \"auto_awesome\"" in settings
