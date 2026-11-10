"""The settings OpenRouter lists for its image and video models (``/images/models``, ``/videos/models``)."""
from __future__ import annotations

from typing import Any

# The setting each field of OpenRouter's video model list names.
_VIDEO_FIELDS = {
    "supported_durations": "duration",
    "supported_resolutions": "resolution",
    "supported_aspect_ratios": "aspect_ratio",
    "supported_sizes": "size",
    "supported_frame_images": "frame_images",
}


def openrouter_video_parameters(row: dict[str, Any]) -> dict[str, Any]:
    """A video model's choices for each setting it takes; a setting it lists nothing for is absent."""
    return {"options": {name: list(row[field]) for field, name in _VIDEO_FIELDS.items()
                        if isinstance(row.get(field), list) and row[field]}}


def openrouter_image_parameters(row: dict[str, Any]) -> dict[str, Any]:
    """An image model's choices for each setting it takes as a list of values; any other setting is absent."""
    described = row.get("supported_parameters")
    return {"options": {
        str(name): [str(value) for value in descriptor["values"]]
        for name, descriptor in (described.items() if isinstance(described, dict) else ())
        if isinstance(descriptor, dict) and descriptor.get("type") == "enum"
        and isinstance(descriptor.get("values"), list) and descriptor["values"]
    }}


def openrouter_media_parameters(video_rows: Any, image_rows: Any) -> dict[str, dict[str, Any]]:
    """Each listed media model's settings, by model ID."""
    parameters: dict[str, dict[str, Any]] = {}
    for rows, parse in ((video_rows, openrouter_video_parameters), (image_rows, openrouter_image_parameters)):
        for row in rows if isinstance(rows, list) else []:
            model_id = str(row.get("id") or "").strip() if isinstance(row, dict) else ""
            if model_id:
                parameters[model_id] = parse(row)
    return parameters
