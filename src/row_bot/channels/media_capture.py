"""
Shared media-capture helpers for all channel adapters.

Extracts vision screenshots and generated images from tool services
so every channel can attach them to replies.
"""

from __future__ import annotations


def grab_vision_capture() -> bytes | None:
    """Return the last captured image from the vision service, if any."""
    try:
        from row_bot.tools.vision_tool import _get_vision_service
        svc = _get_vision_service()
        if svc and svc.last_capture:
            img = svc.last_capture
            svc.last_capture = None
            return img
    except Exception:
        pass
    return None


def grab_generated_image() -> bytes | None:
    """Return the last image-gen output as raw bytes, if any."""
    try:
        import base64
        from row_bot.tools.image_gen_tool import get_and_clear_last_image
        b64 = get_and_clear_last_image()
        if b64:
            return base64.b64decode(b64)
    except Exception:
        pass
    return None


def grab_generated_video() -> str | None:
    """Return the saved file path of the last generated video, if any."""
    try:
        from row_bot.tools.video_gen_tool import get_and_clear_last_video
        vid = get_and_clear_last_video()
        if vid and vid.get("path"):
            return vid["path"]
    except Exception:
        pass
    return None
