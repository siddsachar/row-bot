"""Revision-bound panel editing over the existing Designer document/history owners."""
from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path

from bs4 import BeautifulSoup, Tag

from row_bot.designer import history, storage
from row_bot.designer.client_service import ArtifactError, ArtifactPage, _identifier, read_artifact
from row_bot.designer.state import DESIGNER_MODES, DesignerProject
from row_bot.thread_cleanup import resolve_managed_path

_TEXT_TAGS = frozenset("h1 h2 h3 h4 h5 h6 p span a li td th label figcaption blockquote button dt dd strong em b i small code pre caption summary div section".split())
_SNAPSHOT_ID = re.compile(r"[0-9]{1,20}(?:\.[0-9]{1,12})?")
_MAX_TEXT = 20000


@dataclass(frozen=True)
class ArtifactTextElement:
    id: str
    tag: str
    text: str
    editable: bool


@dataclass(frozen=True)
class ArtifactHistoryItem:
    id: str
    label: str
    author: str
    page_count: int
    available: bool


@dataclass(frozen=True)
class ArtifactEditingState:
    resource_id: str
    resource_revision: str
    mode: str
    name: str
    canvas_width: int
    canvas_height: int
    page_id: str
    page_title: str
    page_notes: str
    pages: tuple[ArtifactPage, ...]
    page_count: int
    page_next_cursor: str | None
    elements: tuple[ArtifactTextElement, ...]
    element_count: int
    element_next_cursor: str | None
    history: tuple[ArtifactHistoryItem, ...]
    history_count: int
    history_next_cursor: str | None


def _selected(project: DesignerProject, page_id: str | None):
    index = project.active_page if page_id is None else next(
        (i for i, page in enumerate(project.pages) if page.route_id == page_id), -1,
    )
    if type(index) is not int or not 0 <= index < len(project.pages):
        raise ArtifactError("page_unavailable")
    return project.pages[index]


def _text_targets(page):
    if not isinstance(page.html, str):
        raise ArtifactError("resource_state_invalid")
    try:
        byte_count = len(page.html.encode("utf-8"))
    except UnicodeError:
        raise ArtifactError("resource_state_invalid") from None
    if byte_count > 2 * 1024 * 1024:
        raise ArtifactError("preview_too_large")
    soup = BeautifulSoup(page.html, "html.parser")
    targets = []
    for ordinal, tag in enumerate(soup.find_all(True)):
        if tag.name not in _TEXT_TAGS or tag.find(True) is not None:
            continue
        text = tag.get_text()
        if not text.strip() or tag.find_parent(["head", "script", "style", "svg", "template"]):
            continue
        identity = json.dumps([page.route_id, ordinal, tag.name, text], ensure_ascii=False)
        target_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        targets.append((target_id, tag, text))
    return soup, targets


def authoring_page_html(project: DesignerProject, page_id: str) -> str:
    """Mark a parsed preview copy; reading never persists element identifiers."""
    page = _selected(project, page_id)
    soup, targets = _text_targets(page)
    for target_id, tag, text in targets:
        if len(text) <= _MAX_TEXT:
            tag["data-row-bot-element-id"] = target_id
    return str(soup)


def _cursor_value(cursor: str | None, revision: str, section: str):
    value = None
    if cursor is not None:
        try:
            if not isinstance(cursor, str) or len(cursor) > 512:
                raise ValueError
            saved_revision, saved_section, value = json.loads(base64.urlsafe_b64decode(cursor))
            if saved_revision != revision:
                raise ArtifactError("resource_revision_conflict", revision)
            if saved_section != section:
                raise ValueError
        except ArtifactError:
            raise
        except (ValueError, TypeError, UnicodeError):
            raise ArtifactError("invalid_cursor") from None
    return value


def _encode_cursor(value, revision: str, section: str) -> str:
    return base64.urlsafe_b64encode(json.dumps([revision, section, value]).encode()).decode()


def _paged(items, cursor: str | None, limit: int, revision: str, section: str):
    offset = _cursor_value(cursor, revision, section) if cursor is not None else 0
    if type(offset) is not int or not 0 <= offset <= len(items):
        raise ArtifactError("invalid_cursor")
    selected, byte_count = [], 0
    for item in items[offset:offset + limit]:
        try:
            size = len(json.dumps(asdict(item), ensure_ascii=False).encode("utf-8"))
        except UnicodeError:
            raise ArtifactError("resource_state_invalid") from None
        if size > 64 * 1024:
            raise ArtifactError("editing_record_too_large")
        if byte_count + size > 64 * 1024:
            break
        selected.append(item)
        byte_count += size
    result = tuple(selected)
    next_offset = offset + len(result)
    next_cursor = None if next_offset >= len(items) else _encode_cursor(next_offset, revision, section)
    return result, next_cursor


def _history_paths(project_id: str) -> list[Path]:
    directory = resolve_managed_path(history.HISTORY_DIR, _identifier(project_id))
    return sorted(directory.glob("*.json"), key=lambda path: path.stem, reverse=True) if directory.is_dir() else []


def _read_snapshot(project_id: str, snapshot_id: str) -> dict:
    if not isinstance(snapshot_id, str) or not _SNAPSHOT_ID.fullmatch(snapshot_id):
        raise ArtifactError("history_unavailable")
    directory = resolve_managed_path(history.HISTORY_DIR, _identifier(project_id))
    path = resolve_managed_path(directory, f"{snapshot_id}.json")
    try:
        if path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or str(value.get("id")) != snapshot_id:
            raise ValueError
        return value
    except (OSError, ValueError, TypeError):
        raise ArtifactError("history_unavailable") from None


def _history_page(project: DesignerProject, page_id: str, cursor: str | None, limit: int):
    section = f"{project.id}:history:{page_id}"
    boundary = _cursor_value(cursor, project.updated_at, section)
    if boundary is not None:
        _identifier(boundary)
    paths = _history_paths(project.id)
    remaining = [path for path in paths if boundary is None or path.stem < boundary]
    selected = remaining[:limit]
    items = []
    for path in selected:
        if not _SNAPSHOT_ID.fullmatch(path.stem):
            # Preserve unknown files; never emit a misleading restorable ID or
            # let an invalid saved name escape as a protocol serialization error.
            raise ArtifactError("history_unavailable")
        try:
            value = _read_snapshot(project.id, path.stem)
            _restore_state(project, path.stem, value=value)
            pages = value.get("pages")
            if not isinstance(pages, list):
                raise ArtifactError("history_unavailable")
            label = value.get("label", "")
            if not isinstance(label, str) or len(label) > 256:
                raise ArtifactError("history_unavailable")
            items.append(ArtifactHistoryItem(path.stem, label,
                         "agent" if value.get("author") == "agent" else "user", len(pages), True))
        except ArtifactError:
            items.append(ArtifactHistoryItem(path.stem, "Snapshot unavailable", "unknown", 0, False))
    next_cursor = _encode_cursor(selected[-1].stem, project.updated_at, section) if len(remaining) > limit else None
    return tuple(items), len(paths), next_cursor


def read_editing(project_id: str, *, page_id: str | None = None, page_cursor: str | None = None,
                 element_cursor: str | None = None, history_cursor: str | None = None,
                 limit: int = 25, element_id: str | None = None) -> ArtifactEditingState:
    """Read saved panel state without creating history, selecting a session or saving."""
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ArtifactError("invalid_limit")
    project = read_artifact(project_id)
    if project.mode not in DESIGNER_MODES:
        raise ArtifactError("artifact_type_unavailable")
    page = _selected(project, page_id)
    if (any(not isinstance(item, str) or len(item) > 256 for item in (project.name, *(item.title for item in project.pages)))
            or not isinstance(page.notes, str) or len(page.notes) > 32768):
        raise ArtifactError("editing_record_too_large")
    if (any(type(size) is not int or not 1 <= size <= 16384 for size in (project.canvas_width, project.canvas_height))
            or not isinstance(project.updated_at, str) or not 1 <= len(project.updated_at) <= 128):
        raise ArtifactError("resource_state_invalid")
    if (any(not isinstance(item.route_id, str) or not re.fullmatch(r"[A-Za-z0-9:_-]{1,128}", item.route_id)
            for item in project.pages)
            or len({item.route_id for item in project.pages}) != len(project.pages)):
        raise ArtifactError("resource_state_invalid")
    all_pages = [ArtifactPage(item.route_id, item.title, index) for index, item in enumerate(project.pages)]
    pages, next_page = _paged(all_pages, page_cursor, limit, project.updated_at, f"{project.id}:pages:{page.route_id}")
    _soup, targets = _text_targets(page)
    all_elements = [ArtifactTextElement(key, tag.name, text if len(text) <= _MAX_TEXT else "", len(text) <= _MAX_TEXT)
                    for key, tag, text in targets]
    if element_id is not None and element_cursor is None:
        position = next((index for index, item in enumerate(all_elements) if item.id == element_id), -1)
        if position < 0:
            raise ArtifactError("element_unavailable")
        element_cursor = _encode_cursor(position, project.updated_at, f"{project.id}:elements:{page.route_id}")
    elements, next_element = _paged(all_elements, element_cursor, limit, project.updated_at, f"{project.id}:elements:{page.route_id}")
    snapshots, history_count, next_history = _history_page(project, page.route_id, history_cursor, limit)
    result = ArtifactEditingState(project.id, project.updated_at, project.mode, project.name,
        project.canvas_width, project.canvas_height, page.route_id, page.title, page.notes,
        pages, len(all_pages), next_page, elements, len(all_elements), next_element,
        snapshots, history_count, next_history)
    try:
        byte_count = len(json.dumps(asdict(result), ensure_ascii=False).encode("utf-8"))
    except UnicodeError:
        raise ArtifactError("resource_state_invalid") from None
    if byte_count > 240 * 1024:
        raise ArtifactError("editing_record_too_large")
    return result


def _plain(value, *, maximum: int, empty: bool = True) -> str:
    if (not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip())
            or any(0xD800 <= ord(char) <= 0xDFFF or ord(char) == 0 for char in value)):
        raise ArtifactError("invalid_edit")
    return value


def _restore_state(project: DesignerProject, snapshot_id: str, *, value: dict | None = None):
    value = _read_snapshot(project.id, snapshot_id) if value is None else value
    pages = value.get("pages")
    if (not isinstance(pages, list) or not pages or any(not isinstance(page, dict) for page in pages)
            or not isinstance(value.get("name", project.name), str)
            or value.get("brand") is not None and not isinstance(value["brand"], dict)):
        raise ArtifactError("history_unavailable")
    if not isinstance(value.get("label", ""), str) or len(value.get("label", "")) > 256:
        raise ArtifactError("history_unavailable")
    for page in pages:
        if any(not isinstance(page.get(key, ""), str) for key in ("title", "html", "notes", "route_id")):
            raise ArtifactError("history_unavailable")
    for field, default in (("canvas_width", project.canvas_width), ("canvas_height", project.canvas_height)):
        if type(value.get(field, default)) is not int or not 1 <= value.get(field, default) <= 16384:
            raise ArtifactError("history_unavailable")
    active = value.get("active_page", project.active_page)
    if type(active) is not int or not 0 <= active < len(pages):
        raise ArtifactError("history_unavailable")
    if not isinstance(value.get("aspect_ratio", project.aspect_ratio), str):
        raise ArtifactError("history_unavailable")
    if value.get("brand"):
        for field, item in value["brand"].items():
            if field in {"logo_max_height", "logo_padding"}:
                if type(item) is not int or not 0 <= item <= 10000:
                    raise ArtifactError("history_unavailable")
            elif item is not None and not isinstance(item, str):
                raise ArtifactError("history_unavailable")
    return history.project_state_from_dict(value, project)


def apply_edit(project_id: str, *, expected_revision: str, operation: str,
               page_id: str | None = None, name: str | None = None, title: str | None = None,
               notes: str | None = None, element_id: str | None = None, text: str | None = None,
               snapshot_id: str | None = None,
               validate: Callable[[], None] | None = None) -> DesignerProject:
    """Apply an explicit captured edit under the existing document save owner."""
    supplied = {key for key, value in {"page_id": page_id, "name": name, "title": title, "notes": notes,
                "element_id": element_id, "text": text, "snapshot_id": snapshot_id}.items() if value is not None}
    allowed = {"project_properties": {"name"}, "page_properties": {"page_id", "title", "notes"},
               "text": {"page_id", "element_id", "text"}, "restore": {"snapshot_id"}}
    if operation not in allowed or supplied - allowed[operation] or not supplied:
        raise ArtifactError("invalid_edit")
    with storage._project_save_lock(_identifier(project_id)):
        if validate:
            validate()
        project = read_artifact(project_id)
        if project.mode not in DESIGNER_MODES:
            raise ArtifactError("artifact_type_unavailable")
        if not isinstance(expected_revision, str) or project.updated_at != expected_revision:
            raise ArtifactError("resource_revision_conflict", project.updated_at)
        updated = deepcopy(project)
        updated._row_bot_persisted_updated_at = project.updated_at
        if operation == "project_properties":
            updated.name = _plain(name, maximum=200, empty=False)
        elif operation == "restore":
            history.apply_project_state(updated, _restore_state(project, snapshot_id))
        else:
            if not isinstance(page_id, str):
                raise ArtifactError("invalid_edit")
            page = _selected(updated, page_id)
            if operation == "page_properties":
                if title is None and notes is None:
                    raise ArtifactError("invalid_edit")
                if title is not None:
                    page.title = _plain(title, maximum=200, empty=False)
                if notes is not None:
                    page.notes = _plain(notes, maximum=20000)
            else:
                replacement = _plain(text, maximum=_MAX_TEXT)
                soup, targets = _text_targets(page)
                target: Tag | None = next((tag for key, tag, old in targets if key == element_id and len(old) <= _MAX_TEXT), None)
                if target is None:
                    raise ArtifactError("element_unavailable")
                # BeautifulSoup escapes text nodes. No user HTML, fuzzy content
                # matching or cross-page replacement can enter this seam.
                target.string = replacement
                page.html = str(soup)
                page.thumbnail_b64 = None
        if updated.to_dict() == project.to_dict():
            return project
        if len(json.dumps(updated.to_dict(), ensure_ascii=False).encode("utf-8")) > 32 * 1024 * 1024:
            raise ArtifactError("resource_too_large")
        if validate:
            validate()
        if not history.snapshot(project, label=f"Before panel {operation}", author="user"):
            raise ArtifactError("history_unavailable")
        updated.manual_edits.append(f"User applied {operation} in the artifact panel.")
        try:
            if validate:
                validate()
            storage.save_project(updated)
        except storage.StaleDesignerProjectError:
            raise ArtifactError("resource_revision_conflict", read_artifact(project_id).updated_at) from None
        return updated
