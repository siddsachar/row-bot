from __future__ import annotations

from row_bot.prompts import _AGENT_GUIDELINES
from row_bot.tools.computer_use_tool import ComputerUseTool


def test_computer_static_guidance_is_tool_bound_deduplicated_and_within_budget() -> None:
    from row_bot import skills

    skills.load_skills()
    guide = skills.get_skill("computer_use_guide")
    assert guide is not None
    assert guide.tools == ["computer_use"]
    assert guide.name not in {skill.name for skill in skills.get_manual_skills()}
    assert len(guide.instructions.split()) <= 300
    assert sum(
        1 for line in guide.instructions.splitlines() if line.lstrip().startswith("-")
    ) <= 10

    description = ComputerUseTool().description
    routing_line = next(
        line
        for line in _AGENT_GUIDELINES.splitlines()
        if "Interaction preference order" in line
    )
    assert len(description.split()) <= 120
    assert len(_AGENT_GUIDELINES.split()) <= 2_100
    assert len((routing_line + " " + description + " " + guide.instructions).split()) <= 550
    assert description not in guide.instructions
    assert routing_line not in guide.instructions
