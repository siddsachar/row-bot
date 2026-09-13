"""Designer — share decks to channels, X, or published links."""

from __future__ import annotations

import logging
import pathlib
import tempfile
from contextlib import nullcontext
from collections.abc import Callable

from row_bot.channels import registry as channel_registry
from row_bot.designer.export import (
    export_html,
    export_pdf,
    export_png,
    export_png_files,
    export_pptx_screenshot,
    export_pptx_structured,
)
from row_bot.designer.publish import publish_project
from row_bot.designer.state import DesignerProject

logger = logging.getLogger(__name__)


def list_share_channels() -> list[dict]:
    """Return lightweight metadata for all registered channels."""
    channels = []
    for channel in channel_registry.all_channels():
        channels.append({
            "name": channel.name,
            "display_name": channel.display_name,
            "configured": channel.is_configured(),
            "running": channel.is_running(),
            "photo_out": channel.capabilities.photo_out,
            "document_out": channel.capabilities.document_out,
        })
    return channels


def _resolve_target(channel, target: str | int | None):
    if target is not None and str(target).strip():
        return target
    try:
        return channel.get_default_target()
    except Exception as exc:
        raise ValueError(
            f"{channel.display_name} needs a delivery target before sharing. {exc}"
        ) from exc


def share_project_to_channel(
    project: DesignerProject,
    channel_name: str,
    *,
    delivery: str = "link",
    target: str | int | None = None,
    text: str = "",
    pages: str | None = None,
    pptx_mode: str = "screenshot",
    validate: Callable[[], None] | None = None,
    checkpoint: Callable[[str, int, int], None] | None = None,
    directory: pathlib.Path | None = None,
    strict: bool = False,
    publisher: Callable[..., dict] | None = None,
) -> dict:
    """Share a project to an active outbound channel."""
    channel = channel_registry.get(channel_name)
    if channel is None:
        raise ValueError(f"Unknown channel: {channel_name}")
    if not channel.is_running():
        raise ValueError(f"{channel.display_name} is not running.")

    resolved_target = _resolve_target(channel, target)
    delivery_mode = (delivery or "link").lower().strip()
    caption = text.strip() or project.name

    def send(call, index: int, total: int):
        if validate:
            validate()
        if checkpoint:
            checkpoint('send_started', index, total)
        try:
            if validate:
                validate()
        except Exception:
            if checkpoint:
                checkpoint('send_not_started', index, total)
            raise
        call()
        if checkpoint:
            checkpoint('send_submitted', index, total)

    if strict and (validate is None or checkpoint is None):
        raise ValueError('sharing_admission_required')
    if strict and ((delivery_mode == 'link' and publisher is None)
                   or (delivery_mode != 'link' and directory is None)):
        raise ValueError('sharing_retained_owner_required')

    if delivery_mode == "link":
        published = (publisher or publish_project)(project, pages, ensure_public=True)
        message = text.strip() or f"{project.name}\n{published['url']}"
        send(lambda: channel.send_message(resolved_target, message), 0, 1)
        return {
            "success": True,
            "detail": f"Shared published link via {channel.display_name}.",
            "url": published["url"],
        }

    with (nullcontext(str(directory)) if directory is not None else tempfile.TemporaryDirectory()) as tmp_dir:
        if delivery_mode == "slides":
            if channel.capabilities.photo_out:
                image_paths = export_png_files(project, pages, directory=tmp_dir)
                for idx, path in enumerate(image_paths):
                    send(lambda: channel.send_photo(resolved_target, str(path), caption=caption if idx == 0 else None), idx, len(image_paths))
                return {
                    "success": True,
                    "detail": f"Shared {len(image_paths)} slide image(s) via {channel.display_name}.",
                    "count": len(image_paths),
                }
            if not channel.capabilities.document_out:
                raise ValueError(f"{channel.display_name} cannot send photos or documents.")
            png_zip = export_png(project, pages, directory=tmp_dir)
            zip_path = pathlib.Path(getattr(png_zip, 'saved_path', '')) if strict else next(iter(pathlib.Path(tmp_dir).glob("*.zip")), None)
            if zip_path is None:
                raise RuntimeError(f"Expected slide ZIP export, got {len(png_zip)} bytes with no file path.")
            send(lambda: channel.send_document(resolved_target, str(zip_path), caption=caption), 0, 1)
            return {
                "success": True,
                "detail": f"Shared slide ZIP via {channel.display_name}.",
                "path": str(zip_path),
            }

        if not channel.capabilities.document_out:
            raise ValueError(f"{channel.display_name} cannot send documents.")

        if delivery_mode == "pdf":
            export_pdf(project, pages, directory=tmp_dir)
            suffix = ".pdf"
        elif delivery_mode == "pptx":
            if pptx_mode == "structured":
                export_pptx_structured(project, pages, directory=tmp_dir)
            else:
                export_pptx_screenshot(project, pages, directory=tmp_dir)
            suffix = ".pptx"
        elif delivery_mode == "html":
            export_html(project, pages, directory=tmp_dir)
            suffix = ".html"
        else:
            raise ValueError(f"Unsupported channel delivery mode: {delivery}")

        file_path = next(iter(pathlib.Path(tmp_dir).glob(f"*{suffix}")), None)
        if file_path is None:
            raise RuntimeError(f"Expected a {suffix} export for channel sharing.")
        send(lambda: channel.send_document(resolved_target, str(file_path), caption=caption), 0, 1)
        return {
            "success": True,
            "detail": f"Shared {file_path.name} via {channel.display_name}.",
            "path": str(file_path),
        }


def share_project_to_x(
    project: DesignerProject,
    *,
    text: str = "",
    pages: str | None = None,
    validate: Callable[[], None] | None = None,
    checkpoint: Callable[[str, int, int], None] | None = None,
    directory: pathlib.Path | None = None,
    strict: bool = False,
) -> dict:
    """Post up to four slide images to X using the existing X tool integration."""
    from row_bot.tools.x_tool import XTool

    tweet_text = text.strip() or project.name

    if strict and (validate is None or checkpoint is None or directory is None):
        raise ValueError('sharing_admission_required')
    with (nullcontext(str(directory)) if directory is not None else tempfile.TemporaryDirectory()) as tmp_dir:
        selected = export_png_files(project, pages, directory=tmp_dir)
        if strict and len(selected) > 4:
            raise ValueError('sharing_media_limit')
        media_paths = [str(path) for path in selected[:4]]
        if not media_paths:
            raise ValueError("No slides were selected for X sharing.")
        if validate:
            validate()
        if checkpoint:
            checkpoint('send_started', 0, 1)
        try:
            if validate:
                validate()
        except Exception:
            if checkpoint:
                checkpoint('send_not_started', 0, 1)
            raise
        result = XTool()._x_post("post", text=tweet_text, media_paths=media_paths,
                               **({'require_all_media': True} if strict else {}))
        if checkpoint:
            checkpoint('send_submitted' if result.startswith('Tweet posted successfully!') else 'send_uncertain', 0, 1)

    return {
        "success": result.startswith("Tweet posted successfully!"),
        "detail": result,
        "media_count": len(media_paths),
    }
