"""Fixture plugin code: importing it leaves a marker beside it, so a test can prove it never ran."""
from pathlib import Path

Path(__file__).with_name("plugin-code-ran.txt").write_text("plugin code ran", encoding="utf-8")


def register(api):
    api.register_tool(None)
