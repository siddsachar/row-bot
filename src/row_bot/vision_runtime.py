"""Shared runtime holder for the app-wide VisionService instance."""

from __future__ import annotations

from typing import Any

_vision_service: Any | None = None


def set_vision_service(svc: Any) -> None:
    """Store the shared VisionService instance created by UI state."""
    global _vision_service
    _vision_service = svc


def get_vision_service() -> Any:
    """Return the shared VisionService, creating a local fallback if needed."""
    global _vision_service
    if _vision_service is None:
        from row_bot.vision import VisionService

        _vision_service = VisionService()
    return _vision_service
