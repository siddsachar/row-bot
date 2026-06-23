from __future__ import annotations

import pytest


pytestmark = [pytest.mark.subsystem, pytest.mark.snapshot]


def test_thumbnail_dimensions_preserve_canvas_aspect_ratio() -> None:
    from row_bot.designer.thumbnail import compute_thumbnail_dimensions

    width, scale = compute_thumbnail_dimensions(1920, 1080, 162)

    assert width == 288
    assert round(scale, 3) == 0.15


def test_thumbnail_dimensions_handle_zero_height_canvas() -> None:
    from row_bot.designer.thumbnail import compute_thumbnail_dimensions

    assert compute_thumbnail_dimensions(1920, 0, 162) == (162, 0.08)
