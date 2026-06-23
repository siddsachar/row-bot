from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.designer import sample_designer_project


pytestmark = [pytest.mark.subsystem, pytest.mark.snapshot]


def test_html_export_matches_stable_snapshot_summary(monkeypatch) -> None:
    from row_bot.designer import export
    import row_bot.designer.fonts as fonts
    from tests.helpers.snapshots import assert_or_write_snapshot

    monkeypatch.setattr(fonts, "get_font_css_embedded", lambda _family: "")
    project = sample_designer_project()
    html = export.build_html_export(project).decode("utf-8")
    summary = "\n".join(
        [
            f"name={project.name}",
            f"page-count={html.count('<section id=\"page-')}",
            f"nav={'|'.join(page.title for page in project.pages)}",
            f"canvas={project.canvas_width}x{project.canvas_height}",
            f"sandbox={'sandbox=\"allow-same-origin\"' in html}",
        ]
    ) + "\n"

    assert "Page 1: Overview" in html
    assert "Page 2: Details" in html
    assert_or_write_snapshot(Path("tests/snapshots/designer/html_export_summary.snapshot"), summary)


def test_export_html_writes_selected_pages_to_requested_directory(monkeypatch, tmp_path) -> None:
    from row_bot.designer import export
    import row_bot.designer.fonts as fonts

    monkeypatch.setattr(fonts, "get_font_css_embedded", lambda _family: "")
    project = sample_designer_project()

    data = export.export_html(project, pages="1", directory=tmp_path)

    assert data.saved_path == tmp_path / "Subsystem Snapshot.html"
    assert data.saved_path.exists()
    assert b"Page 1: Overview" in data
    assert b"Page 2: Details" not in data


def test_describe_export_destination_sanitizes_names(tmp_path) -> None:
    from row_bot.designer.export import describe_export_destination
    from row_bot.designer.state import DesignerProject

    project = DesignerProject(name="Bad / Name: Demo?")

    assert describe_export_destination(project, "html", directory=tmp_path).name == "Bad _ Name_ Demo_.html"
    assert describe_export_destination(project, "png", pages="1-2", directory=tmp_path).name == "Bad _ Name_ Demo_.png"
