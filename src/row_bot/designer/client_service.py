"""Typed Deck pilot commands over Designer's existing persistence and renderer.

Conversation admission, binding, authorization and operation receipts belong to
the application service. None of these helpers invokes a provider or changes
the visible Designer session.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from row_bot.designer import storage
from row_bot.designer.setup_flow import canvas_choices_for_mode, create_project_from_setup
from row_bot.designer.state import DesignerProject, ProjectBrief
from row_bot.designer.templates import get_templates


class ArtifactError(ValueError):
    """Safe domain failure translated by the application/API owner."""

    def __init__(self, code: str, current_revision: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.current_revision = current_revision


@dataclass(frozen=True)
class DeckSetup:
    template_id: str = "blank_deck"
    aspect_ratio: str = "16:9"
    name: str = ""
    brief: str = ""


@dataclass(frozen=True)
class SetupChoice:
    id: str
    label: str


@dataclass(frozen=True)
class DeckSetupOptions:
    mode: str
    templates: tuple[SetupChoice, ...]
    canvases: tuple[SetupChoice, ...]
    default_template: str
    default_canvas: str
    default_name: str
    default_brand: str


@dataclass(frozen=True)
class ArtifactPage:
    id: str
    title: str
    index: int


@dataclass(frozen=True)
class ArtifactPreview:
    resource_id: str
    resource_revision: str
    preview_revision: str
    mode: str
    page_id: str
    page_index: int
    page_count: int
    page_title: str
    canvas_width: int
    canvas_height: int
    pages: tuple[ArtifactPage, ...]
    html: str | None
    unchanged: bool


@dataclass(frozen=True)
class ArtifactSummary:
    id: str
    name: str
    mode: str
    resource_revision: str
    origin_conversation_id: str | None
    origin_missing: bool
    available: bool


@dataclass(frozen=True)
class ArtifactLibrary:
    items: tuple[ArtifactSummary, ...]
    next_cursor: str | None
    has_more: bool


def _identifier(value: str) -> str:
    if (not isinstance(value, str) or not value or len(value.encode()) > 128
            or value in {".", ".."} or any(c in "/\\:" or ord(c) < 32 for c in value)):
        raise ArtifactError("invalid_resource")
    return value


def deck_setup_options() -> DeckSetupOptions:
    """Return local Deck defaults without provider or font downloads."""
    return DeckSetupOptions(
        "deck", tuple(SetupChoice(t.id, t.name) for t in get_templates()
                      if t.mode == "deck" and not t.hidden_from_gallery),
        tuple(SetupChoice(key, label) for key, label in canvas_choices_for_mode("deck")),
        "blank_deck", "16:9", "Untitled Design", "Default brand",
    )


def read_artifact(project_id: str) -> DesignerProject:
    """Read without directory creation, asset migration or session selection."""
    from row_bot.thread_cleanup import resolve_managed_path

    _identifier(project_id)
    try:
        path = resolve_managed_path(storage.PROJECTS_DIR, f"{project_id}.json")
        if not path.is_file():
            raise ArtifactError("not_found")
        if path.stat().st_size > 32 * 1024 * 1024:
            raise ArtifactError("resource_too_large")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("id") != project_id:
            raise ArtifactError("resource_state_invalid")
        project = DesignerProject.from_dict(data)
        project._row_bot_persisted_updated_at = str(data.get("updated_at") or "")
        return project
    except ArtifactError:
        raise
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        raise ArtifactError("resource_state_invalid") from None


def list_artifacts(cursor: str | None = None, limit: int = 50) -> ArtifactLibrary:
    """Page the complete saved library by stable ID without normalizing data.

    Other saved Designer types remain visible with unavailable pilot status.
    A removed cursor item is a valid keyset boundary, never a missing index.
    """
    if not 1 <= limit <= 100:
        raise ArtifactError("invalid_limit")
    if cursor is not None:
        _identifier(cursor)
    paths = sorted(p for p in storage.PROJECTS_DIR.glob("*.json")
                   if cursor is None or p.stem > cursor)
    items = []
    for path in paths[:limit]:
        try:
            project = read_artifact(path.stem)
            items.append(ArtifactSummary(project.id, project.name, project.mode, project.updated_at,
                         project.thread_id or project.missing_origin_thread_id,
                         bool(project.missing_origin_thread_id), project.mode == "deck"))
        except ArtifactError:
            items.append(ArtifactSummary(path.stem, "Artifact unavailable", "unknown", "", None, False, False))
    more = len(paths) > limit
    return ArtifactLibrary(tuple(items), paths[limit - 1].stem if more else None, more)


def create_deck(project_id: str, setup: DeckSetup) -> DesignerProject:
    """Save exactly one preallocated resource; command receipts own input dedupe."""
    _identifier(project_id)
    options = deck_setup_options()
    if setup.template_id not in {choice.id for choice in options.templates}:
        raise ArtifactError("invalid_template")
    if setup.aspect_ratio not in {choice.id for choice in options.canvases}:
        raise ArtifactError("invalid_canvas")
    if len(setup.name) > 200 or len(setup.brief) > 20000:
        raise ArtifactError("invalid_setup")
    with storage._project_save_lock(project_id):
        try:
            return read_artifact(project_id)
        except ArtifactError as exc:
            if exc.code != "not_found":
                raise
        project = create_project_from_setup(
            setup.template_id, aspect_ratio=setup.aspect_ratio, project_name=setup.name,
            brief=ProjectBrief(build_description=setup.brief) if setup.brief.strip() else None,
            mode="deck",
        )
        project.id = project_id
        project.thread_ownership = "resume"
        storage.save_project(project)
        return project


def associate_origin(project_id: str, conversation_id: str, *, expected_revision: str,
                     expected_origin: str | None, repair: bool = False) -> DesignerProject:
    """CAS a resume-only pointer after application validation of the destination."""
    _identifier(conversation_id)
    with storage._project_save_lock(_identifier(project_id)):
        project = read_artifact(project_id)
        current = project.thread_id or project.missing_origin_thread_id
        if project.thread_id == conversation_id and project.thread_ownership == "resume":
            return project
        if project.updated_at != expected_revision:
            raise ArtifactError("resource_revision_conflict", project.updated_at)
        if current != expected_origin or (current and not repair):
            raise ArtifactError("origin_repair_required", project.updated_at)
        project.thread_id = conversation_id
        project.thread_ownership = "resume"
        project.missing_origin_thread_id = None
        storage.save_project(project)
        return project


def read_preview(project_id: str, *, page_id: str | None = None,
                 known_revision: str | None = None) -> ArtifactPreview:
    """Return an isolated Deck page; unchanged reads skip HTML construction."""
    from row_bot.designer.preview import preview_fingerprint, render_page_html, isolate_preview_html

    project = read_artifact(project_id)
    if project.mode != "deck":
        raise ArtifactError("artifact_type_unavailable")
    if len(project.pages) > 200:
        raise ArtifactError("preview_page_limit")
    pages = tuple(ArtifactPage(p.route_id, p.title, i) for i, p in enumerate(project.pages))
    index = project.active_page if page_id is None else next(
        (p.index for p in pages if p.id == page_id), -1,
    )
    if not 0 <= index < len(pages):
        raise ArtifactError("page_unavailable")
    inputs = preview_fingerprint(project, page_index=index)
    revision = hashlib.sha256(repr(inputs).encode()).hexdigest()
    unchanged = revision == known_revision
    markup = None
    if not unchanged:
        markup = isolate_preview_html(render_page_html(project, project.pages[index].html, page_index=index),
                                      brand=project.brand)
        if len(markup.encode()) > 2 * 1024 * 1024:
            raise ArtifactError("preview_too_large")
    metadata = storage.get_project_metadata(project_id)
    if (metadata is None or metadata["updated_at"] != project.updated_at
            or preview_fingerprint(project, page_index=index) != inputs):
        raise ArtifactError("resource_revision_conflict", metadata["updated_at"] if metadata else None)
    return ArtifactPreview(project.id, project.updated_at, revision, "deck", pages[index].id,
                           index, len(pages), pages[index].title, project.canvas_width,
                           project.canvas_height, pages, markup, unchanged)
