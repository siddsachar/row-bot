"""A scratch profile for the stale-skills setting: the memory stack, user skills and a settable clock."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from tests.fixtures.memory_stack import fresh_memory_stack


@dataclass
class StaleWorld:
    data: Path
    kg: Any
    memory_tool: Any
    memory_evolution: Any
    skills: Any
    now: datetime

    def set_arm(self, arm: str | None) -> None:
        """``None``: the setting off (no file); otherwise on with this arm."""

        from row_bot import stale_skills

        path = self.data / "stale_skills.json"
        if arm is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(json.dumps({"enabled": True, "arm": arm}), encoding="utf-8")
        stale_skills.invalidate()

    def at(self, moment: datetime) -> None:
        self.now = moment

    def later(self, **delta: int) -> None:
        self.now = self.now + timedelta(**delta)

    def skill(self, name: str, instructions: str, *, display: str = "") -> Any:
        return self.skills.create_skill(name=name, display_name=display or name.replace("_", " ").title(), icon="R",
                                        description=f"How {name} is done.", instructions=instructions)

    def tables(self) -> set[str]:
        import sqlite3

        conn = sqlite3.connect(self.kg.DB_PATH)
        try:
            return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','index')")}
        finally:
            conn.close()


def stale_world(tmp_path: Path, monkeypatch, reload_for_data_dir, *, arm: str | None = None) -> StaleWorld:
    from row_bot import stale_skills

    stack = fresh_memory_stack(tmp_path, monkeypatch, reload_for_data_dir)
    data = tmp_path / "row-bot-data"
    (skills,) = reload_for_data_dir(data, "row_bot.skills")
    skills.load_skills()
    world = StaleWorld(data=data, kg=stack["kg"], memory_tool=stack["memory_tool"],
                       memory_evolution=stack["memory_evolution"], skills=skills, now=datetime(2026, 11, 2, 9, 0))
    monkeypatch.setattr(stale_skills, "_clock", lambda: world.now)
    monkeypatch.setattr(stale_skills, "_cache", None)
    world.set_arm(arm)
    return world
