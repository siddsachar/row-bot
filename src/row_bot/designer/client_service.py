"""Typed artifact commands over Designer's existing persistence and renderer.

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
from row_bot.designer.state import DESIGNER_MODES, DesignerProject, ProjectBrief, default_aspect_for_mode
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
class ArtifactSetup:
    mode: str = "deck"
    template_id: str = ""
    aspect_ratio: str = ""
    name: str = ""
    brief: str = ""


@dataclass(frozen=True)
class SetupChoice:
    id: str
    label: str


@dataclass(frozen=True)
class ArtifactSetupOptions:
    mode: str
    templates: tuple[SetupChoice, ...]
    canvases: tuple[SetupChoice, ...]
    default_template: str
    default_canvas: str
    default_name: str
    default_brand: str


# Public Deck callers retain the same options shape during migration.
DeckSetupOptions = ArtifactSetupOptions


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
    scripts_allowed: bool = False


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


def artifact_setup_options(mode: str = "deck") -> ArtifactSetupOptions:
    """Return the existing mode's local defaults without downloads or inference."""
    if not isinstance(mode, str) or mode not in DESIGNER_MODES:
        raise ArtifactError("artifact_type_unavailable")
    return ArtifactSetupOptions(
        mode, tuple(SetupChoice(t.id, t.name) for t in get_templates()
                    if t.mode == mode and not t.hidden_from_gallery),
        tuple(SetupChoice(key, label) for key, label in canvas_choices_for_mode(mode)),
        f"blank_{mode}", default_aspect_for_mode(mode), "Untitled Design", "Default brand",
    )


def deck_setup_options() -> DeckSetupOptions:
    """Compatibility entry point using the shared artifact setup owner."""
    return artifact_setup_options("deck")


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
        # Legacy mode-less projects are decks. Explicit future/unsupported
        # types must not inherit the legacy model's silent Deck fallback.
        if "mode" in data and data["mode"] not in DESIGNER_MODES:
            project.mode = "unknown"
        project._row_bot_persisted_updated_at = str(data.get("updated_at") or "")
        return project
    except ArtifactError:
        raise
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        raise ArtifactError("resource_state_invalid") from None


def list_artifacts(cursor: str | None = None, limit: int = 50) -> ArtifactLibrary:
    """Page the complete saved library by stable ID without normalizing data.

    Unsupported or corrupt saved artifacts remain visibly unavailable.
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
                         bool(project.missing_origin_thread_id), project.mode in DESIGNER_MODES))
        except ArtifactError:
            items.append(ArtifactSummary(path.stem, "Artifact unavailable", "unknown", "", None, False, False))
    more = len(paths) > limit
    return ArtifactLibrary(tuple(items), paths[limit - 1].stem if more else None, more)


def create_deck(project_id: str, setup: DeckSetup) -> DesignerProject:
    """Compatibility entry point using the shared artifact creation owner."""
    return create_artifact(project_id, ArtifactSetup(
        "deck", setup.template_id, setup.aspect_ratio, setup.name, setup.brief,
    ))


def create_artifact(project_id: str, setup: ArtifactSetup) -> DesignerProject:
    """Save exactly one preallocated resource; command receipts own input dedupe."""
    _identifier(project_id)
    options = artifact_setup_options(setup.mode)
    if any(not isinstance(value, str) for value in
           (setup.template_id, setup.aspect_ratio, setup.name, setup.brief)):
        raise ArtifactError("invalid_setup")
    template_id = setup.template_id or options.default_template
    aspect_ratio = setup.aspect_ratio or options.default_canvas
    if template_id not in {choice.id for choice in options.templates}:
        raise ArtifactError("invalid_template")
    if aspect_ratio not in {choice.id for choice in options.canvases}:
        raise ArtifactError("invalid_canvas")
    if len(setup.name) > 200 or len(setup.brief) > 20000:
        raise ArtifactError("invalid_setup")
    with storage._project_save_lock(project_id):
        try:
            existing = read_artifact(project_id)
            if existing.mode != setup.mode:
                raise ArtifactError("resource_state_invalid")
            return existing
        except ArtifactError as exc:
            if exc.code != "not_found":
                raise
        project = create_project_from_setup(
            template_id, aspect_ratio=aspect_ratio, project_name=setup.name,
            brief=ProjectBrief(build_description=setup.brief) if setup.brief.strip() else None,
            mode=setup.mode,
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
        if project.mode not in DESIGNER_MODES:
            raise ArtifactError("artifact_type_unavailable")
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
                 known_revision: str | None = None, authoring: bool = False,
                 preview_id: str = "", capability: str = "", static_page: bool = False) -> ArtifactPreview:
    """Return an isolated page or interactive routes; unchanged reads skip HTML work.

    Scripts are trusted local prototype runtime only. The client must retain an
    opaque frame, allowing scripts only when requested and never same-origin.
    """
    from row_bot.designer.preview import (
        INTERACTIVE_MODES, _ensure_page_route_ids, isolate_preview_html, preview_fingerprint,
        render_multi_route_html, render_page_html,
    )

    project = read_artifact(project_id)
    if type(authoring) is not bool or type(static_page) is not bool or static_page and authoring:
        raise ArtifactError("invalid_preview_identity")
    if authoring:
        import re
        if any(not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", value)
               for value in (preview_id, capability)):
            raise ArtifactError("invalid_preview_identity")
    if project.mode not in DESIGNER_MODES:
        raise ArtifactError("artifact_type_unavailable")
    if len(project.pages) > 200:
        raise ArtifactError("preview_page_limit")
    route_ids = _ensure_page_route_ids(project)
    pages = tuple(ArtifactPage(route_ids[i], p.title, i) for i, p in enumerate(project.pages))
    index = project.active_page if page_id is None else next(
        (p.index for p in pages if p.id == page_id), -1,
    )
    if not 0 <= index < len(pages):
        raise ArtifactError("page_unavailable")
    from row_bot.designer.fonts import FontReadError, offline_font_fingerprint, strict_offline_fonts
    families = [project.brand.heading_font, project.brand.body_font] if project.brand else []
    try:
        inputs = (preview_fingerprint(project, page_index=index), offline_font_fingerprint(families))
    except FontReadError as exc:
        raise ArtifactError(str(exc)) from None
    revision = hashlib.sha256(repr((inputs, static_page, preview_id, capability) if authoring
                                  else (inputs, static_page)).encode()).hexdigest()
    unchanged = revision == known_revision
    markup = None
    scripts_allowed = not static_page and (authoring or project.mode in INTERACTIVE_MODES)
    if not unchanged:
        with strict_offline_fonts():
            if authoring:
                from row_bot.designer.client_editing import authoring_page_html
                from row_bot.designer.interaction import inject_bridge_js

                rendered = render_page_html(project, authoring_page_html(project, pages[index].id), page_index=index)
                rendered = inject_bridge_js(rendered, preview_id=preview_id, revision=revision, capability=capability,
                                           plain_text=True)
            else:
                rendered = (render_multi_route_html(project, active_route_id=pages[index].id)
                            if scripts_allowed else
                            render_page_html(project, project.pages[index].html, page_index=index))
        try:
            markup = isolate_preview_html(rendered, scripts=scripts_allowed, brand=project.brand, strict_fonts=True)
        except FontReadError as exc:
            raise ArtifactError(str(exc)) from None
        if len(markup.encode()) > 2 * 1024 * 1024:
            raise ArtifactError("preview_too_large")
    metadata = storage.get_project_metadata(project_id)
    try:
        current_inputs = (preview_fingerprint(project, page_index=index), offline_font_fingerprint(families))
    except FontReadError as exc:
        raise ArtifactError(str(exc)) from None
    if (metadata is None or metadata["updated_at"] != project.updated_at or current_inputs != inputs):
        raise ArtifactError("resource_revision_conflict", metadata["updated_at"] if metadata else None)
    return ArtifactPreview(project.id, project.updated_at, revision, project.mode, pages[index].id,
                           index, len(pages), pages[index].title, project.canvas_width,
                           project.canvas_height, pages, markup, unchanged, scripts_allowed)
