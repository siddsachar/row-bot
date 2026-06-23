from __future__ import annotations

from row_bot.designer.state import BrandConfig, DesignerPage, DesignerProject


def sample_designer_project() -> DesignerProject:
    return DesignerProject(
        id="designer-fixture",
        name="Subsystem Snapshot",
        aspect_ratio="16:9",
        brand=BrandConfig(primary_color="#111827", secondary_color="#2563EB", accent_color="#F59E0B"),
        pages=[
            DesignerPage(
                title="Overview",
                html="<main><h1>Overview</h1><p>Deterministic export fixture.</p></main>",
            ),
            DesignerPage(
                title="Details",
                html="<section><h2>Details</h2><p>Second page smoke check.</p></section>",
            ),
        ],
    )
