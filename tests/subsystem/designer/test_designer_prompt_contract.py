from __future__ import annotations

from row_bot.designer.prompt import build_designer_prompt
from row_bot.designer.state import DesignerProject


def test_document_prompt_allows_brief_progress_without_repeating_body() -> None:
    project = DesignerProject(name="Document Prompt Contract", mode="document")

    prompt = build_designer_prompt(project)

    assert "Brief live progress updates" in prompt
    assert "inspecting, editing" in prompt
    assert "never repeat the full document body in" in prompt
    assert "do not paste document/page body content into chat" in prompt
    assert "designer_set_pages" in prompt
    assert "designer_update_page" in prompt
