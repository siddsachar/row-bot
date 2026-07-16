from __future__ import annotations

from row_bot.prompts import _AGENT_GUIDELINES, AGENT_BG_OVERRIDE


def test_prompt_prefers_structured_then_browser_then_computer() -> None:
    assert "structured Row-Bot tool or plugin" in _AGENT_GUIDELINES
    assert "use Browser for ordinary websites" in _AGENT_GUIDELINES
    assert "use computer_use only for native desktop apps" in _AGENT_GUIDELINES
    assert "call stop immediately and never add another capture" in _AGENT_GUIDELINES
    assert "before the first coordinate-only action" in _AGENT_GUIDELINES
    assert "capture once with visual_question" in _AGENT_GUIDELINES
    assert "Computer Use is unavailable in background tasks" in AGENT_BG_OVERRIDE
