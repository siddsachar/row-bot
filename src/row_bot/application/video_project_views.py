"""The client's views of video projects (5.3.1 B3): the Video panel reads and changes them.

The person's changes are new versions by "you", made on the version they saw (a newer one is
refused, so two editors never overwrite each other), and each makes the video again. Making a
video downloads nothing: when ffmpeg isn't here yet, the panel says to ask Row-Bot, whose step
asks to download it.
"""

from __future__ import annotations

from typing import Any

from row_bot import video_projects
from row_bot.video_projects import ProjectError


class ProjectRefused(ValueError):
    """The change can't be made: ``video_project_stale`` (a newer version is there), ``video_project_invalid``
    (a scene can't be used) or ``video_project_needs_ffmpeg``."""


def _project(project_id: str) -> video_projects.Project | None:
    return video_projects.get(project_id)


def _stored(project: video_projects.Project) -> video_projects.Project:
    """The project as it is now: making a video records its progress there, not on this copy."""
    return video_projects.get(project.project_id, project.conversation_id) or project


def read_video_project(project_id: str, number: int | None = None) -> dict[str, Any] | None:
    project = _project(project_id)
    if project is None or (number is not None and project.version(number) is None):
        return None
    return video_projects.view(project, number)


def _need_ffmpeg() -> None:
    from row_bot import ffmpeg_runtime

    if ffmpeg_runtime.find() is None:
        raise ProjectRefused("video_project_needs_ffmpeg")


def change_video_project(project_id: str, body: dict[str, Any]) -> dict[str, Any] | None:
    project = _project(project_id)
    if project is None:
        return None
    _need_ffmpeg()
    try:
        version = video_projects.change(project, by="you", base=int(body["base"]), scenes=list(body["scenes"]),
                                        title=body.get("title") or None, music_volume=body.get("music_volume"),
                                        captions=body.get("captions"), brand=body.get("brand"),
                                        voice=body.get("voice"), note=body.get("note") or "Edited in the panel")
    except ProjectError as error:
        raise ProjectRefused("video_project_stale" if error.stale else "video_project_invalid") from None
    video_projects.make(project, version.number)
    return video_projects.view(_stored(project), version.number)


def restore_video_project(project_id: str, number: int) -> dict[str, Any] | None:
    project = _project(project_id)
    if project is None:
        return None
    old = project.version(number)
    if old is not None and not old.edit_id:
        _need_ffmpeg()
    try:
        version = video_projects.restore(project, number, by="you")
    except ProjectError:
        raise ProjectRefused("video_project_invalid") from None
    if not version.edit_id:
        video_projects.make(project, version.number)
    return video_projects.view(_stored(project), version.number)


def export_video_project(project_id: str, shape: str) -> dict[str, Any] | None:
    project = _project(project_id)
    if project is None:
        return None
    _need_ffmpeg()
    try:
        video_projects.export(project, shape)
    except ProjectError:
        raise ProjectRefused("video_project_not_made") from None
    return video_projects.view(_stored(project))


def make_video_project(project_id: str, number: int | None = None) -> dict[str, Any] | None:
    project = _project(project_id)
    if project is None or (number is not None and project.version(number) is None):
        return None
    _need_ffmpeg()
    video_projects.make(project, number or project.latest.number)
    return video_projects.view(_stored(project), number)
