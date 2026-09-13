"""Saved Designer controls over canonical project, brand and review owners."""
from __future__ import annotations

import hashlib
import base64
import binascii
import json
import re
import io
import os
import stat
from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict, dataclass
from itertools import islice
from pathlib import Path
from uuid import UUID

from row_bot.designer import brand, fonts, history, hotspot_recorder, review, storage
from row_bot.designer.client_editing import _paged, _selected, _text_targets
from row_bot.designer.client_service import ArtifactError, _identifier, read_artifact
from row_bot.designer.state import BrandConfig, DesignerAsset, DesignerProject

_COLORS = {'primary_color', 'secondary_color', 'accent_color', 'bg_color', 'text_color'}
_LOGO_OPTIONS = {'logo_mode': {'auto', 'manual'}, 'logo_scope': {'all', 'first'},
                 'logo_position': {'top_left', 'top_right', 'bottom_left', 'bottom_right'}}
_EXCLUDED = {'html', 'head', 'script', 'style', 'meta', 'link', 'base', 'title', 'iframe', 'object', 'embed', 'template'}
_STYLE_CHOICES = {'text-align': {'left', 'center', 'right', 'justify', 'start', 'end'},
                  'display': {'block', 'inline', 'inline-block', 'flex', 'grid', 'none'},
                  'flex-direction': {'row', 'column', 'row-reverse', 'column-reverse'}}
_LENGTHS = {'font-size', 'width', 'height', 'max-width', 'max-height', 'min-width', 'min-height',
            'padding', 'margin', 'border-radius', 'gap', 'letter-spacing'}
_STYLE_KEYS = _LENGTHS | set(_STYLE_CHOICES) | {'color', 'background-color', 'border-color',
                                            'font-family', 'font-weight', 'line-height', 'opacity'}


@dataclass(frozen=True)
class DesignBrand:
    primary_color: str
    secondary_color: str
    accent_color: str
    bg_color: str
    text_color: str
    heading_font: str
    body_font: str
    logo_asset_id: str
    logo_mode: str
    logo_scope: str
    logo_position: str
    logo_max_height: int
    logo_padding: int


@dataclass(frozen=True)
class DesignElement:
    id: str
    tag: str
    styles: dict[str, str]
    action: str


@dataclass(frozen=True)
class DesignControlItem:
    id: str
    label: str
    kind: str
    detail: str
    available: bool


@dataclass(frozen=True)
class DesignControlsState:
    resource_id: str
    resource_revision: str
    mode: str
    page_id: str
    brand: DesignBrand
    element: DesignElement | None
    section: str
    items: tuple[DesignControlItem, ...]
    item_count: int
    next_cursor: str | None


@dataclass(frozen=True)
class DesignReviewFinding:
    id: str
    source: str
    category: str
    severity: str
    message: str
    suggested_fix: str
    page_id: str
    auto_fixable: bool


@dataclass(frozen=True)
class DesignReviewState:
    resource_id: str
    resource_revision: str
    page_id: str
    scope: str
    heuristic: bool
    score: int
    findings: tuple[DesignReviewFinding, ...]
    finding_count: int
    next_cursor: str | None


@dataclass(frozen=True)
class DesignPresentationPage:
    id: str
    title: str
    index: int


@dataclass(frozen=True)
class DesignPresentationState:
    resource_id: str
    resource_revision: str
    page_id: str
    title: str
    notes: str
    page_index: int
    page_count: int
    pages: tuple[DesignPresentationPage, ...]
    next_cursor: str | None


def read_presentation(project_id: str, *, page_index: int | None = None,
                      cursor: str | None = None, limit: int = 25) -> DesignPresentationState:
    """Passive paginated navigation; caller renders through the isolated preview."""
    project = read_artifact(project_id)
    index = project.active_page if page_index is None else page_index
    if type(index) is not int or not 0 <= index < len(project.pages):
        raise ArtifactError('page_unavailable')
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ArtifactError('invalid_limit')
    page = project.pages[index]
    items = [DesignPresentationPage(_plain(item.route_id, 128, empty=False),
                                    _plain(item.title), position)
             for position, item in enumerate(project.pages)]
    selected, next_cursor = _paged(items, cursor, limit, project.updated_at,
                                  f'{project.id}:presentation:{index}')
    return DesignPresentationState(project.id, project.updated_at, page.route_id,
        _plain(page.title), _plain(page.notes, 32768), index, len(items), selected, next_cursor)


def _plain(value, maximum: int = 256, *, empty: bool = True) -> str:
    if (not isinstance(value, str) or len(value) > maximum or not empty and not value.strip()
            or any(ord(char) < 32 and char not in '\n\t' for char in value)):
        raise ArtifactError('invalid_design_control')
    return value


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


def _entries(path, limit: int = 1024):
    if not path.exists():
        return []
    if path.is_symlink() or path.is_junction() or not path.is_dir():
        raise ArtifactError('design_catalog_unavailable')
    entries = list(islice(path.iterdir(), limit + 1))
    if len(entries) > limit:
        raise ArtifactError('design_catalog_too_large')
    return entries


def _font_items():
    result = []
    bundled = fonts.get_bundled_font_names()
    if len(bundled) > 1024:
        raise ArtifactError('design_catalog_too_large')
    for family in bundled:
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9 -]{0,127}', family):
            raise ArtifactError('design_catalog_unavailable')
        result.append(DesignControlItem(family, family, 'font', 'Bundled · offline', True))
    for directory in _entries(fonts._CACHE_DIR):
        if directory.is_symlink() or directory.is_junction() or not directory.is_dir():
            continue
        if not re.fullmatch(r'[a-z0-9-]{1,128}', directory.name):
            continue
        if any(fonts._safe_dirname(family) == directory.name for family in bundled):
            continue
        files = _entries(directory, 64)
        available = any(path.suffix == '.woff2' and path.is_file() and not path.is_symlink()
                        and not path.is_junction() for path in files)
        if available:
            name = directory.name.replace('-', ' ').title()
            result.append(DesignControlItem(name, name, 'font', 'Cached · offline', True))
    for name in ('Arial', 'Georgia', 'Times New Roman', 'system-ui', 'serif', 'sans-serif', 'monospace'):
        if name not in {item.id for item in result}:
            result.append(DesignControlItem(name, name, 'font', 'System fallback', True))
    return sorted(result, key=lambda item: item.label)


def _presets():
    try:
        values = brand.get_all_presets(strict=True)
    except (OSError, ValueError, TypeError):
        raise ArtifactError('design_catalog_unavailable') from None
    # BrandConfig.to_dict intentionally drops embedded bytes when an asset ID
    # exists; preset review must still bind every byte we might import.
    return { _hash([name, asdict(value)]): (name, value) for name, value in values.items() }


def _brand_view(value: BrandConfig | None) -> DesignBrand:
    current = value or BrandConfig()
    selected = {name: getattr(current, name) for name in DesignBrand.__dataclass_fields__}
    for name, item in selected.items():
        if name in {'logo_max_height', 'logo_padding'}:
            if type(item) is not int or not 0 <= item <= 16384:
                raise ArtifactError('resource_state_invalid')
        else:
            _plain(item)
    return DesignBrand(**selected)


def _targets(page):
    soup, text_targets = _text_targets(page)
    text_ids = {id(tag): key for key, tag, _text in text_targets}
    result = []
    for ordinal, tag in enumerate(soup.find_all(True)):
        if ordinal >= 10000:
            raise ArtifactError('design_page_too_complex')
        if tag.name in _EXCLUDED or tag.find_parent(['head', 'script', 'style', 'template', 'svg', 'iframe', 'object', 'embed']):
            continue
        key = text_ids.get(id(tag)) or _hash([page.route_id, ordinal, tag.name])
        result.append((key, tag))
    return soup, result


def authoring_page_html(project: DesignerProject, page_id: str) -> str:
    """Decorate only a preview copy; text IDs match the existing inline editor."""
    soup, targets = _targets(_selected(project, page_id))
    for key, tag in targets:
        tag['data-row-bot-element-id'] = key
    return str(soup)


def _element(page, element_id):
    soup, targets = _targets(page)
    found = next((tag for key, tag in targets if key == element_id), None)
    if found is None:
        raise ArtifactError('element_unavailable')
    return soup, found


def _element_view(page, element_id):
    if element_id is None:
        return None
    from row_bot.designer.critique import _parse_style
    _soup, tag = _element(page, element_id)
    styles = {}
    for key, value in _parse_style(tag.get('style', '')).items():
        if key not in _STYLE_KEYS:
            continue
        try:
            _style_updates({key: value})
        except ArtifactError:
            continue  # Never project raw URLs, paths or executable CSS to controls.
        styles[key] = value
    return DesignElement(element_id, tag.name, styles, _plain(tag.get('data-row-bot-action', ''), 256))


def read_controls(project_id: str, *, page_id: str | None = None, element_id: str | None = None,
                  section: str = 'elements', cursor: str | None = None, limit: int = 25) -> DesignControlsState:
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ArtifactError('invalid_limit')
    project = read_artifact(project_id)
    page = _selected(project, page_id)
    if section == 'fonts':
        items = _font_items()
    elif section == 'presets':
        items = [DesignControlItem(key, _plain(name), 'preset', 'Saved brand preset', True)
                 for key, (name, _value) in _presets().items()]
    elif section == 'assets':
        items = [DesignControlItem(_plain(asset.id), _plain(asset.label or asset.filename or asset.id),
                                   _plain(asset.kind), f'{_plain(asset.mime_type)} · {asset.size_bytes} bytes', True)
                 for asset in project.assets]
    elif section == 'interactions':
        items = [DesignControlItem(_plain(item.id), _plain(item.action), 'interaction',
                                   _plain(f'{item.source_route}: {item.target}', 512), True)
                 for item in project.interactions]
    elif section == 'elements':
        _soup, targets = _targets(page)
        items = [DesignControlItem(key, _plain(tag.get_text(' ', strip=True)[:120] or tag.name),
                                   tag.name, '', True) for key, tag in targets]
    else:
        raise ArtifactError('invalid_design_control')
    # Catalog revisions belong in the cursor too: a global preset/font change
    # cannot silently page through a different result set at the same project revision.
    section_key = f'{project.id}:{page.route_id}:{section}:{_hash([asdict(item) for item in items])}'
    selected, next_cursor = _paged(items, cursor, limit, project.updated_at, section_key)
    return DesignControlsState(project.id, project.updated_at, project.mode, page.route_id,
                               _brand_view(project.brand), _element_view(page, element_id),
                               section, selected, len(items), next_cursor)


def _review(project, page_id, scope):
    if scope not in {'page', 'project'}:
        raise ArtifactError('invalid_design_control')
    selected = _selected(project, page_id)
    current = deepcopy(project)
    source_bytes = 0
    for page in current.pages if scope == 'project' else [current.pages[project.pages.index(selected)]]:
        source_bytes += len(page.html.encode('utf-8'))
        if source_bytes > 16 * 1024 * 1024:
            raise ArtifactError('design_review_too_large')
        page.html = authoring_page_html(current, page.route_id)
    try:
        report = review.build_review_report(current, scope=scope,
                                             page_index=project.pages.index(selected), strict=True)
    except Exception:
        raise ArtifactError('design_review_unavailable') from None
    return report


def read_review(project_id: str, *, page_id: str | None = None, scope: str = 'page',
                cursor: str | None = None, limit: int = 25) -> DesignReviewState:
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ArtifactError('invalid_limit')
    project = read_artifact(project_id)
    page = _selected(project, page_id)
    report = _review(project, page.route_id, scope)
    items = [DesignReviewFinding(item['id'], item['source'], item['category'], item['severity'],
                                 _plain(item['message'], 2048), _plain(item['suggested_fix'], 2048),
                                 project.pages[item['page_index']].route_id, item['auto_fixable'])
             for item in report['findings']]
    selected, next_cursor = _paged(items, cursor, limit, project.updated_at,
                                   f'{project.id}:{page.route_id}:review:{scope}')
    return DesignReviewState(project.id, project.updated_at, page.route_id, scope, True,
                             report['score'], selected, len(items), next_cursor)


def draft_review_fix(project_id: str, *, expected_revision: str, page_id: str,
                      finding_id: str) -> str:
    """Prepare an existing review instruction for the one shared composer.

    This never dispatches a provider request or persists a project mutation.
    The host must let the user review and explicitly submit the resulting draft.
    """
    project = read_artifact(project_id)
    if project.updated_at != expected_revision:
        raise ArtifactError('resource_revision_conflict', project.updated_at)
    report = _review(project, page_id, 'page')
    finding = next((item for item in report['findings'] if item['id'] == finding_id), None)
    if finding is None:
        raise ArtifactError('design_finding_unavailable')
    public = {key: finding[key] for key in ('page_index', 'category', 'message', 'suggested_fix')}
    public['message'] = _plain(public['message'], 2048)
    public['suggested_fix'] = _plain(public['suggested_fix'], 2048)
    return _plain(review.build_ai_fix_request(public), 8192)


def _brand_update(current, payload, project):
    if not isinstance(payload, dict) or not payload or set(payload) - set(DesignBrand.__dataclass_fields__):
        raise ArtifactError('invalid_design_control')
    value = deepcopy(current or BrandConfig())
    available = {item.id for item in _font_items()}
    for key, item in payload.items():
        if key in _COLORS:
            if not isinstance(item, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', item):
                raise ArtifactError('invalid_design_control')
        elif key in {'heading_font', 'body_font'}:
            if not isinstance(item, str) or item not in available:
                raise ArtifactError('font_unavailable')
        elif key in _LOGO_OPTIONS:
            if not isinstance(item, str) or item not in _LOGO_OPTIONS[key]:
                raise ArtifactError('invalid_design_control')
        elif key in {'logo_max_height', 'logo_padding'}:
            lower, upper = (24, 240) if key == 'logo_max_height' else (0, 160)
            if type(item) is not int or not lower <= item <= upper:
                raise ArtifactError('invalid_design_control')
        elif key == 'logo_asset_id':
            _plain(item)
            if item and not any(asset.id == item and asset.mime_type in {'image/png', 'image/jpeg', 'image/webp', 'image/gif', 'image/svg+xml'} for asset in project.assets):
                raise ArtifactError('asset_unavailable')
            value.logo_b64 = None
            if item:
                asset = next(asset for asset in project.assets if asset.id == item)
                _asset_bytes(project, asset)
                value.logo_mime_type, value.logo_filename = asset.mime_type, asset.filename
        setattr(value, key, item)
    return value


def _style_updates(payload):
    if not isinstance(payload, dict) or not payload or set(payload) - _STYLE_KEYS:
        raise ArtifactError('invalid_design_control')
    for key, value in payload.items():
        _plain(value)
        if not value:
            continue
        if key in _STYLE_CHOICES:
            valid = value in _STYLE_CHOICES[key]
        elif key in {'color', 'background-color', 'border-color'}:
            valid = bool(re.fullmatch(r'#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?|transparent|currentColor|var\(--(?:primary|secondary|accent|bg|text)\)', value))
        elif key == 'font-family':
            valid = value in {item.id for item in _font_items()}
        elif key == 'font-weight':
            valid = value in {'normal', 'bold', *map(str, range(100, 1000, 100))}
        elif key in {'opacity', 'line-height'}:
            valid = bool(re.fullmatch(r'[0-9]+(?:\.[0-9]{1,3})?', value)) and 0 <= float(value) <= (1 if key == 'opacity' else 5)
        else:
            parts = value.split()
            valid = 1 <= len(parts) <= (4 if key in {'padding', 'margin', 'border-radius'} else 1)
            for part in parts:
                match = re.fullmatch(r'(-?[0-9]+(?:\.[0-9]{1,3})?)(px|rem|em|%|vh|vw)?', part)
                valid = valid and bool(match) and -16384 <= float(match[1]) <= 16384
        if not valid:
            raise ArtifactError('invalid_design_control')
    return payload


def apply_control(project_id: str, *, expected_revision: str, operation: str,
                  payload: dict, page_id: str | None = None, element_id: str | None = None,
                  validate: Callable[[], None], command_id: str | None = None,
                  checkpoint: Callable[[dict], None] | None = None) -> DesignerProject:
    with storage._project_save_lock(_identifier(project_id)):
        validate()
        project = read_artifact(project_id)
        if project.updated_at != expected_revision:
            raise ArtifactError('resource_revision_conflict', project.updated_at)
        updated = deepcopy(project)
        updated._row_bot_persisted_updated_at = project.updated_at
        if operation in {'brand', 'preset'}:
            if operation == 'preset':
                presets = _presets()
                if (not isinstance(payload, dict) or set(payload) != {'preset_id'}
                        or not isinstance(payload['preset_id'], str) or payload['preset_id'] not in presets):
                    raise ArtifactError('design_preset_unavailable')
                _name, preset = presets[payload['preset_id']]
                payload = asdict(_brand_view(preset))
                # A saved foreign project ID is never an asset authority. Import
                # validated embedded bytes explicitly, or reuse an owned asset.
                payload['logo_asset_id'] = ''
                _brand_update(updated.brand, payload, updated)  # Fail before asset effects.
                payload['logo_asset_id'] = _preset_logo(
                    project, updated, preset, validate, command_id, checkpoint)
            updated.brand = _brand_update(updated.brand, payload, updated)
            from row_bot.designer.preview import update_brand_in_html
            for page in updated.pages:
                _targets(page)
                if page.html.strip():
                    page.html = update_brand_in_html(page.html, updated.brand)
                    page.thumbnail_b64 = None
        elif operation in {'style', 'hotspot'}:
            page = _selected(updated, page_id)
            soup, tag = _element(page, element_id)
            tag['data-row-bot-element-id'] = element_id
            if operation == 'style':
                from row_bot.designer.html_ops import restyle_element_in_html
                page.html, _key, _hint = restyle_element_in_html(str(soup),
                    element_ref=element_id, style_updates=json.dumps(_style_updates(payload)))
            else:
                if (not isinstance(payload, dict) or set(payload) - {'action', 'target', 'event', 'transition'}
                        or payload.get('event', 'click') not in {'click', 'hover', 'enter'}
                        or payload.get('transition', 'fade') not in {'fade', 'slide_left', 'slide_up', 'none'}):
                    raise ArtifactError('invalid_design_control')
                target = _plain(payload.get('target', ''), 128)
                if payload.get('action') == 'play_media' and not any(asset.id == target and asset.kind in {'video', 'audio'} for asset in updated.assets):
                    raise ArtifactError('asset_unavailable')
                if payload.get('action') == 'toggle_state' and not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', target):
                    raise ArtifactError('invalid_design_control')
                page.html = str(soup)
                ok, _message = hotspot_recorder.record_hotspot(updated, source_route=page.route_id,
                    selector=f'[data-row-bot-element-id="{element_id}"]', **payload)
                if not ok:
                    raise ArtifactError('invalid_design_control')
            page.thumbnail_b64 = None
        elif operation == 'review_fix':
            if not isinstance(payload, dict) or set(payload) != {'finding_id'}:
                raise ArtifactError('invalid_design_control')
            report = _review(project, page_id, 'page')
            finding = next((item for item in report['findings'] if item['id'] == payload['finding_id']), None)
            if finding is None or not finding['auto_fixable']:
                raise ArtifactError('design_finding_unavailable')
            review._apply_to_page(updated, finding['page_index'], finding['source'], [finding['category']])
        elif operation in {'asset_insert', 'asset_remove', 'asset_forget'}:
            _apply_asset(updated, operation, payload, page_id)
        else:
            raise ArtifactError('invalid_design_control')
        result = _save_control(project, updated, operation, validate)
        if operation == 'preset' and len(updated.assets) > len(project.assets):
            asset = updated.assets[-1]
            checkpoint({'stage': 'asset_attached', 'asset_id': asset.id,
                        'sha256': asset.sha256, 'size_bytes': asset.size_bytes})
        return result


def _save_control(project, updated, operation, validate):
    if updated.to_dict() == project.to_dict():
        return project
    if len(json.dumps(updated.to_dict(), ensure_ascii=False).encode('utf-8')) > 32 * 1024 * 1024:
        raise ArtifactError('resource_too_large')
    validate()
    if not history.snapshot(project, label=f'Before panel {operation}', author='user'):
        raise ArtifactError('history_unavailable')
    updated.manual_edits.append(f'User applied {operation} in the design controls panel.')
    validate()
    try:
        storage.save_project(updated)
    except storage.StaleDesignerProjectError:
        raise ArtifactError('resource_revision_conflict', read_artifact(project.id).updated_at) from None
    return updated


def _asset_bytes(project, asset):
    from row_bot.thread_cleanup import resolve_managed_path
    try:
        root = resolve_managed_path(storage.ASSETS_DIR, project.id)
        path = resolve_managed_path(root, asset.stored_name)
        before, parent = path.lstat(), root.stat()
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or not 0 < before.st_size <= 25 * 1024 * 1024):
            raise ValueError
        with path.open('rb') as handle:
            opened = os.fstat(handle.fileno())
            if not os.path.samestat(before, opened):
                raise ValueError
            data = handle.read(25 * 1024 * 1024 + 1)
            finished = os.fstat(handle.fileno())
        named = path.lstat()
        if (not os.path.samestat(opened, named) or not os.path.samestat(parent, root.stat())
                or len(data) != asset.size_bytes or hashlib.sha256(data).hexdigest() != asset.sha256
                or (opened.st_size, opened.st_mtime_ns) != (finished.st_size, finished.st_mtime_ns)
                or (named.st_size, named.st_mtime_ns) != (finished.st_size, finished.st_mtime_ns)):
            raise ValueError
        return data
    except (OSError, ValueError, TypeError):
        raise ArtifactError('asset_unavailable') from None


def _asset_metadata(data: bytes, filename: str):
    """Validate bounded raster/SVG content or an inert supported media container.

    Playback remains the browser's isolated media decode; this does not claim
    a video codec/playability probe and never launches ffmpeg or a provider.
    """
    from PIL import Image
    if not isinstance(data, bytes) or not 0 < len(data) <= 25 * 1024 * 1024:
        raise ArtifactError('asset_too_large')
    _plain(filename, 200, empty=False)
    if any(char in filename for char in '/\\:\r\n') or filename in {'.', '..'}:
        raise ArtifactError('invalid_design_control')
    suffix = Path(filename).suffix.lower()
    if suffix == '.svg':
        from xml.etree import ElementTree
        from row_bot.designer.html_ops import sanitize_agent_html
        from bs4 import BeautifulSoup
        try:
            if len(data) > 2 * 1024 * 1024:
                raise ValueError
            text = data.decode('utf-8')
            if re.search(r'<!DOCTYPE|<!ENTITY|<\?(?!xml\s)', text, re.IGNORECASE):
                raise ValueError
            root = ElementTree.fromstring(text)
            if root.tag.split('}')[-1].lower() != 'svg':
                raise ValueError
            nodes = list(root.iter())
            if len(nodes) > 10000:
                raise ValueError
            for node in nodes:
                name = node.tag.split('}')[-1].lower()
                if name in {'script', 'foreignobject', 'iframe', 'object', 'embed', 'animate', 'animatetransform', 'set', 'style', 'image'}:
                    raise ValueError
                for attr, value in node.attrib.items():
                    attr = attr.split('}')[-1].lower()
                    if (attr.startswith('on') or attr in {'src', 'srcdoc'}
                            or attr == 'href' and not re.fullmatch(r'#[A-Za-z0-9_-]{1,128}', value)
                            or re.search(r'url\s*\(', value, re.IGNORECASE) and not re.fullmatch(r'url\(#[A-Za-z0-9_-]{1,128}\)', value)
                            or re.search(r'expression|@import|javascript:|vbscript:', value, re.IGNORECASE)):
                        raise ValueError
            parsed = str(BeautifulSoup(text, 'html.parser'))
            if sanitize_agent_html(text) != parsed:
                raise ValueError
            return 'image', 'image/svg+xml', None, None
        except (ValueError, UnicodeError, ElementTree.ParseError):
            raise ArtifactError('asset_content_unsafe') from None
    image_formats = {'.png': ('PNG', 'image/png'), '.jpg': ('JPEG', 'image/jpeg'),
                     '.jpeg': ('JPEG', 'image/jpeg'), '.webp': ('WEBP', 'image/webp'), '.gif': ('GIF', 'image/gif')}
    if suffix in image_formats:
        try:
            with Image.open(io.BytesIO(data)) as image:
                expected, mime = image_formats[suffix]
                frames = getattr(image, 'n_frames', 1)
                if (image.format != expected or image.width * image.height > 32 * 1024 * 1024
                        or frames > 200 or image.width * image.height * frames > 64 * 1024 * 1024):
                    raise ValueError
                width, height = image.size
                image.verify()
            return 'image', mime, width, height
        except (OSError, ValueError, Image.DecompressionBombError):
            raise ArtifactError('asset_content_unsafe') from None
    if suffix in {'.mp4', '.m4v'} and len(data) >= 16 and data[4:8] == b'ftyp' and 12 <= int.from_bytes(data[:4], 'big') <= len(data):
        return 'video', 'video/mp4', None, None
    if suffix == '.webm' and data.startswith(b'\x1aE\xdf\xa3'):
        return 'video', 'video/webm', None, None
    if suffix == '.wav' and len(data) >= 12 and data[:4] == b'RIFF' and data[8:12] == b'WAVE':
        return 'audio', 'audio/wav', None, None
    if suffix == '.ogg' and data.startswith(b'OggS'):
        return 'audio', 'audio/ogg', None, None
    if suffix == '.mp3' and (data.startswith(b'ID3') or len(data) >= 2 and data[0] == 255 and data[1] & 0xE0 == 0xE0):
        return 'audio', 'audio/mpeg', None, None
    raise ArtifactError('asset_type_unavailable')


def _new_asset(project, updated, data, filename, command_id, validate, checkpoint):
    """Publish through the existing asset owner; caller commits one project CAS."""
    if not callable(checkpoint):
        raise ArtifactError('asset_admission_required')
    try:
        identifier = UUID(command_id)
        if str(identifier) != command_id:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise ArtifactError('invalid_design_control') from None
    kind, mime, width, height = _asset_metadata(data, filename)
    asset_id = f'asset-{identifier.hex}'
    digest = hashlib.sha256(data).hexdigest()
    if any(asset.id == asset_id for asset in project.assets):
        raise ArtifactError('asset_identity_conflict')
    checkpoint({'stage': 'asset_prepared', 'asset_id': asset_id, 'sha256': digest, 'size_bytes': len(data)})
    def admitted():
        validate()
        if read_artifact(project.id).updated_at != project.updated_at:
            raise ArtifactError('resource_revision_conflict')
    stored_name = storage.save_asset_bytes(project.id, asset_id, filename, data, require_absent=True, validate=admitted)
    checkpoint({'stage': 'asset_written', 'asset_id': asset_id, 'sha256': digest, 'size_bytes': len(data)})
    asset = DesignerAsset(id=asset_id, kind=kind, label=filename, filename=filename, mime_type=mime,
                          stored_name=stored_name, size_bytes=len(data), sha256=digest, width=width, height=height)
    _asset_bytes(project, asset)
    updated.assets.append(asset)
    return asset


def _preset_logo(project, updated, preset, validate, command_id, checkpoint):
    if not preset.logo_b64:
        if not preset.logo_asset_id:
            return ''
        asset = next((item for item in project.assets if item.id == preset.logo_asset_id), None)
        if asset is None or not asset.mime_type.startswith('image/'):
            raise ArtifactError('design_preset_logo_unavailable')
        _asset_bytes(project, asset)
        return asset.id
    extensions = {'image/png': '.png', 'image/jpeg': '.jpg', 'image/webp': '.webp',
                  'image/gif': '.gif', 'image/svg+xml': '.svg'}
    if (not isinstance(preset.logo_b64, str) or len(preset.logo_b64) > 32 * 1024 * 1024
            or preset.logo_mime_type not in extensions):
        raise ArtifactError('design_preset_logo_unavailable')
    try:
        data = base64.b64decode(preset.logo_b64, validate=True)
    except (ValueError, binascii.Error):
        raise ArtifactError('design_preset_logo_unavailable') from None
    filename = 'brand-logo' + extensions[preset.logo_mime_type]
    _kind, mime, _width, _height = _asset_metadata(data, filename)
    if mime != preset.logo_mime_type:
        raise ArtifactError('design_preset_logo_unavailable')
    digest = hashlib.sha256(data).hexdigest()
    for asset in project.assets:
        if asset.sha256 == digest and asset.mime_type == mime:
            if _asset_bytes(project, asset) == data:
                return asset.id
    return _new_asset(project, updated, data, filename, command_id, validate, checkpoint).id


def mutate_preset(project_id: str, *, expected_revision: str, action: str,
                   command_id: str, validate: Callable[[], None], checkpoint: Callable,
                   name: str | None = None, preset_id: str | None = None) -> dict:
    """Explicit global effect. The caller privately persists FileEditRecovery.

    Saving with no preset_id requires a new name; replacement/deletion require
    the exact currently enumerated preset ID. Global effects never save projects.
    """
    with storage._project_save_lock(_identifier(project_id)):
        validate()
        project = read_artifact(project_id)
        if project.updated_at != expected_revision:
            raise ArtifactError('resource_revision_conflict', project.updated_at)
        presets = _presets()
        if action not in {'save', 'delete'}:
            raise ArtifactError('invalid_design_control')
        if preset_id is not None:
            if preset_id not in presets:
                raise ArtifactError('design_preset_unavailable')
            selected_name, _value = presets[preset_id]
            if name is not None and name != selected_name:
                raise ArtifactError('design_preset_unavailable')
            name = selected_name
        elif action == 'delete':
            raise ArtifactError('design_preset_unavailable')
        _plain(name, 256, empty=False)
        digest = brand.read_preset_revision(name)
        if preset_id is None and digest != 'missing':
            raise ArtifactError('design_preset_exists')
        if action == 'delete' and digest == 'missing':
            raise ArtifactError('design_preset_builtin')
        def admitted():
            validate()
            if read_artifact(project.id).updated_at != expected_revision:
                raise ArtifactError('resource_revision_conflict')
        if action == 'save':
            value = deepcopy(project.brand or BrandConfig())
            # Normalize and validate saved controls without silently changing them.
            _brand_update(value, asdict(_brand_view(value)), project)
            if value.logo_asset_id:
                asset = next((item for item in project.assets if item.id == value.logo_asset_id), None)
                if asset is None:
                    raise ArtifactError('asset_unavailable')
                data = _asset_bytes(project, asset)
                _asset_metadata(data, 'brand-logo' + {'image/png': '.png', 'image/jpeg': '.jpg',
                    'image/webp': '.webp', 'image/gif': '.gif', 'image/svg+xml': '.svg'}.get(asset.mime_type, '.invalid'))
                value.logo_b64 = base64.b64encode(data).decode('ascii')
                value.logo_asset_id = ''
            brand.save_brand_preset(name, value, strict=True, expected_digest=digest,
                command_id=command_id, validate=admitted, checkpoint=checkpoint)
            result_id = _hash([name, asdict(value)])
        else:
            brand.delete_brand_preset(name, strict=True, expected_digest=digest,
                command_id=command_id, validate=admitted, checkpoint=checkpoint)
            result_id = preset_id
        return {'action': action, 'preset_id': result_id, 'name': name,
                'resource_id': project.id, 'resource_revision': project.updated_at}


def upload_asset(project_id: str, *, expected_revision: str, command_id: str, filename: str,
                 data: bytes, validate: Callable[[], None], checkpoint: Callable[[dict], None]) -> DesignerProject:
    """Attach server-resolved upload bytes; caller owns durable command admission."""
    _asset_metadata(data, filename)
    with storage._project_save_lock(_identifier(project_id)):
        validate()
        project = read_artifact(project_id)
        if project.updated_at != expected_revision:
            raise ArtifactError('resource_revision_conflict', project.updated_at)
        updated = deepcopy(project)
        updated._row_bot_persisted_updated_at = project.updated_at
        asset = _new_asset(project, updated, data, filename, command_id, validate, checkpoint)
        result = _save_control(project, updated, 'asset_upload', validate)
        checkpoint({'stage': 'asset_attached', 'asset_id': asset.id, 'sha256': asset.sha256, 'size_bytes': asset.size_bytes})
        return result


def _apply_asset(project, operation, payload, page_id):
    from bs4 import BeautifulSoup
    from row_bot.designer.html_ops import build_media_fragment, wrap_asset_fragment, remove_asset_from_html
    if not isinstance(payload, dict) or set(payload) != {'asset_id'}:
        raise ArtifactError('invalid_design_control')
    asset_id = _plain(payload['asset_id'], 128, empty=False)
    asset = next((item for item in project.assets if item.id == asset_id), None)
    if asset is None:
        raise ArtifactError('asset_unavailable')
    if operation == 'asset_forget':
        if ((project.brand and project.brand.logo_asset_id == asset_id)
                or any(item.target == asset_id for item in project.interactions)
                or any(asset_id in page.html for page in project.pages)
                or any(item.poster_asset_id == asset_id for item in project.assets)):
            raise ArtifactError('asset_still_referenced')
        project.assets[:] = [item for item in project.assets if item.id != asset_id]
        # Canonical bytes remain for history restore and incomplete/retry review.
        return
    page = _selected(project, page_id)
    _targets(page)
    if operation == 'asset_remove':
        try:
            page.html, _removed = remove_asset_from_html(page.html, asset_id)
        except ValueError:
            raise ArtifactError('asset_unavailable') from None
    else:
        _asset_bytes(project, asset)
        if asset_id in page.html:
            raise ArtifactError('asset_already_on_page')
        if asset.kind == 'audio':
            # Build media markup using the same escaped Tag owner; no client HTML.
            soup = BeautifulSoup('', 'html.parser')
            tag = soup.new_tag('audio', src=f'asset://{asset_id}')
            tag['controls'] = ''
            tag['data-asset-id'] = asset_id
            tag['data-row-bot-kind'] = 'audio'
            fragment = str(tag)
        else:
            fragment = build_media_fragment(asset_kind=asset.kind, asset_id=asset_id, src=f'asset://{asset_id}',
                                             mime_type=asset.mime_type, label=asset.label, width=asset.width, height=asset.height)
        wrapped, _key = wrap_asset_fragment(fragment, asset.kind, label=asset.label, asset_id=asset_id)
        soup = BeautifulSoup(page.html, 'html.parser')
        (soup.body or soup).append(BeautifulSoup(wrapped, 'html.parser'))
        page.html = str(soup)
    page.thumbnail_b64 = None
