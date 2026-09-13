"""Passive Designer lifecycle readiness over the existing effect owners.

Export, publication, sharing, presentation, and thumbnail effects retain their
canonical owners.  This module gives clients one bounded, path-free view of
which of those owners can currently be offered without rendering, starting a
tunnel, contacting a provider, or sending to a channel.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib.util import find_spec

from row_bot.designer.client_service import ArtifactError, read_artifact
from row_bot.designer.state import DESIGNER_MODES


@dataclass(frozen=True)
class ArtifactLifecycleCapability:
    id: str
    label: str
    state: str
    detail: str
    review_required: bool


@dataclass(frozen=True)
class ArtifactLifecycleState:
    resource_id: str
    resource_revision: str
    mode: str
    page_count: int
    capabilities: tuple[ArtifactLifecycleCapability, ...]


def _dependency(module: str) -> bool:
    try:
        return find_spec(module) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _remote_access_available() -> bool:
    try:
        from row_bot.tunnel import tunnel_manager

        return bool(tunnel_manager.is_available())
    except Exception:
        return False


def _running_channel_available() -> bool:
    try:
        from row_bot.application.artifact_sharing import sharing_channels

        return any(item["available"] for item in sharing_channels()["items"])
    except Exception:
        return False


def _x_post_available() -> bool:
    try:
        from row_bot.tools.x_tool import XTool

        operations = XTool().get_config("post_operations", [])
        return isinstance(operations, list) and "post" in operations
    except Exception:
        return False


def _capability(
    identity: str,
    label: str,
    state: str,
    detail: str,
    *,
    review: bool = False,
) -> ArtifactLifecycleCapability:
    if state not in {"ready", "check_on_use", "unavailable"}:
        raise ValueError("invalid lifecycle state")
    return ArtifactLifecycleCapability(identity, label, state, detail, review)


def read_artifact_lifecycle(
    project_id: str,
    *,
    expected_revision: str | None = None,
) -> ArtifactLifecycleState:
    """Return current lifecycle readiness without performing any lifecycle effect.

    A bound-resource caller can provide its admitted revision.  The second read
    closes the window where a save could otherwise mix an older page count with
    readiness observed after that save.
    """
    project = read_artifact(project_id)
    if expected_revision is not None and project.updated_at != expected_revision:
        raise ArtifactError("resource_revision_conflict", project.updated_at)
    if project.mode not in DESIGNER_MODES:
        raise ArtifactError("artifact_type_unavailable")
    if not 1 <= len(project.pages) <= 200:
        raise ArtifactError("resource_state_invalid")

    playwright = _dependency("playwright.sync_api")
    powerpoint = _dependency("pptx")
    remote = _remote_access_available()
    channel = _running_channel_available()
    x_post = _x_post_available()
    renderer_detail = (
        "The local renderer is checked again when the export starts."
        if playwright
        else "Install the Designer browser runtime to use this export."
    )
    capabilities = (
        _capability(
            "presentation",
            "Presentation",
            "ready",
            "Uses the saved revision and an isolated slide preview.",
        ),
        _capability(
            "thumbnails",
            "Slide thumbnails",
            "ready",
            "Thumbnails load only while their presentation view is visible.",
        ),
        _capability(
            "export.html",
            "HTML export",
            "ready",
            "Creates a retained local copy before download.",
        ),
        _capability(
            "export.pdf",
            "PDF export",
            "check_on_use" if playwright else "unavailable",
            renderer_detail,
        ),
        _capability(
            "export.png",
            "PNG export",
            "check_on_use" if playwright else "unavailable",
            renderer_detail,
        ),
        _capability(
            "export.pptx",
            "PowerPoint export",
            "check_on_use" if powerpoint else "unavailable",
            (
                "The selected PowerPoint mode and renderer are checked when export starts."
                if powerpoint
                else "Install the Designer PowerPoint runtime to use this export."
            ),
        ),
        _capability(
            "publish.local",
            "Local published link",
            "ready",
            "Replaces the reviewed local published copy.",
            review=True,
        ),
        _capability(
            "publish.remote",
            "Remote access link",
            "check_on_use" if remote else "unavailable",
            (
                "Tunnel reachability is checked when publication starts. Sign-in or pairing remains required."
                if remote
                else "Configure a supported tunnel before publishing remote access."
            ),
            review=True,
        ),
        _capability(
            "share.channel",
            "Channel delivery",
            "ready" if channel else "unavailable",
            (
                "A configured, running outbound channel is available."
                if channel
                else "Start a configured outbound channel before sharing."
            ),
            review=True,
        ),
        _capability(
            "share.x",
            "Post to X",
            "ready" if x_post else "unavailable",
            (
                "Posting is enabled in the current X tool configuration."
                if x_post
                else "Enable X post operations before sharing."
            ),
            review=True,
        ),
    )
    current = read_artifact(project_id)
    if current.updated_at != project.updated_at:
        raise ArtifactError("resource_revision_conflict", current.updated_at)
    return ArtifactLifecycleState(
        project.id,
        project.updated_at,
        project.mode,
        len(project.pages),
        capabilities,
    )
