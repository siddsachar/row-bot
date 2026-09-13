"""Explicit revision-bound downloads over the existing Designer export owner."""
from __future__ import annotations

import hashlib
import json
import os
import math
import re
import stat
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from itertools import islice
from uuid import UUID

from row_bot.designer import export, storage
from row_bot.designer.client_service import ArtifactError, read_artifact
from row_bot.designer.preview import preview_fingerprint
from row_bot.designer.state import DESIGNER_MODES

MAX_EXPORTS = 32
MAX_TOTAL_BYTES = 512 * 1024 * 1024
EXPORT_LIFETIME_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class ArtifactExport:
    export_id: str
    resource_id: str
    resource_revision: str
    format: str
    pptx_mode: str | None
    page_count: int
    filename: str
    media_type: str
    size_bytes: int
    sha256: str
    status: str
    warnings: tuple[str, ...]
    expires_at: float


def _root() -> Path:
    path = storage.DESIGNER_DIR / 'client_exports_v1'
    if path.is_symlink() or path.is_junction():
        raise ArtifactError('export_storage_unavailable')
    return path


def _identity(value: str) -> str:
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise ArtifactError('invalid_export') from None
    return value


def _directory(export_id: str) -> Path:
    path = _root() / _identity(export_id)
    if path.is_symlink() or path.is_junction():
        raise ArtifactError('export_storage_unavailable')
    return path


def _read(path: Path, maximum: int) -> bytes:
    if path.is_symlink() or path.is_junction():
        raise ArtifactError('export_unavailable')
    try:
        parents = (storage.DESIGNER_DIR, _root(), path.parent)
        identities = tuple(parent.lstat() for parent in parents)
        def validate_parents():
            for parent, original in zip(parents, identities):
                current = parent.lstat()
                if (parent.is_symlink() or parent.is_junction() or not stat.S_ISDIR(current.st_mode)
                        or not os.path.samestat(original, current)):
                    raise ArtifactError('export_unavailable')
        validate_parents()
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(descriptor, 'rb') as handle:
            validate_parents()
            opened = os.fstat(handle.fileno())
            named = path.lstat()
            if (not stat.S_ISREG(opened.st_mode) or not stat.S_ISREG(named.st_mode)
                    or not os.path.samestat(opened, named) or opened.st_nlink != 1 or opened.st_size > maximum):
                raise ArtifactError('export_unavailable')
            data = handle.read(maximum + 1)
            validate_parents()
            if (len(data) > maximum or not os.path.samestat(opened, path.lstat())
                    or path.is_symlink() or path.is_junction()):
                raise ArtifactError('export_unavailable')
            return data
    except OSError:
        raise ArtifactError('export_unavailable') from None


def _manifest(directory: Path) -> dict:
    try:
        value = json.loads(_read(directory / 'manifest.json', 16384))
        if not isinstance(value, dict) or value.get('version') != 1 or value.get('export_id') != directory.name:
            raise ValueError
        return value
    except (ValueError, TypeError):
        raise ArtifactError('export_unavailable') from None


def _ready(directory: Path, manifest: dict) -> tuple[ArtifactExport, bytes]:
    if manifest.get('status') != 'ready':
        raise ArtifactError('export_incomplete')
    try:
        data = _read(directory / 'payload', export.MAX_STRICT_EXPORT_BYTES)
        fields = dict(manifest['descriptor'])
        if (not isinstance(fields.get('warnings'), list) or len(fields['warnings']) > 1
                or any(item != 'external_assets_unavailable' for item in fields['warnings'])):
            raise ValueError
        fields['warnings'] = tuple(fields['warnings'])
        descriptor = ArtifactExport(**fields)
        extension = 'zip' if descriptor.format == 'png' and descriptor.page_count > 1 else descriptor.format
        expected_mime = {'html': 'text/html', 'pdf': 'application/pdf', 'png': 'image/png', 'zip': 'application/zip',
                         'pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation'}
        if (descriptor.export_id != directory.name or descriptor.status != 'ready'
                or descriptor.size_bytes != len(data) or hashlib.sha256(data).hexdigest() != descriptor.sha256
                or descriptor.expires_at != manifest['expires_at']
                or type(descriptor.expires_at) not in {int, float} or not math.isfinite(descriptor.expires_at)
                or descriptor.resource_id != manifest['resource_id']
                or descriptor.resource_revision != manifest['request']['resource_revision']
                or descriptor.format not in {'html', 'pdf', 'png', 'pptx'}
                or descriptor.format != manifest['request']['format']
                or descriptor.pptx_mode != manifest['request']['pptx_mode']
                or descriptor.pptx_mode not in ({'screenshot', 'structured'} if descriptor.format == 'pptx' else {None})
                or type(descriptor.page_count) is not int or not 1 <= descriptor.page_count <= 200
                or descriptor.media_type != expected_mime.get(extension)
                or not isinstance(descriptor.filename, str) or not 1 <= len(descriptor.filename) <= 240
                or not descriptor.filename.endswith('.' + extension)
                or Path(descriptor.filename).name != descriptor.filename
                or any(ord(char) < 32 or char in '/\\:' for char in descriptor.filename)):
            raise ValueError
    except (KeyError, ValueError, TypeError, OverflowError):
        raise ArtifactError('export_unavailable') from None
    return descriptor, data


def _options(format: str, pages: str, pptx_mode: str | None, total: int):
    if format not in {'pdf', 'html', 'png', 'pptx'} or (format != 'pptx' and pptx_mode is not None):
        raise ArtifactError('invalid_export')
    if format == 'pptx' and pptx_mode not in {'screenshot', 'structured'}:
        raise ArtifactError('invalid_export')
    if not isinstance(pages, str) or len(pages) > 256 or total < 1:
        raise ArtifactError('invalid_page_range')
    if pages == 'all':
        return list(range(total))
    indices = set()
    for part in pages.split(','):
        match = re.fullmatch(r'\s*([0-9]{1,6})(?:\s*-\s*([0-9]{1,6}))?\s*', part)
        if not match:
            raise ArtifactError('invalid_page_range')
        first, last = int(match[1]), int(match[2] or match[1])
        if not 1 <= first <= last <= total:
            raise ArtifactError('invalid_page_range')
        indices.update(range(first - 1, last))
    return sorted(indices)


def _scope(manifest: dict, resource_id: str, binding_id: str) -> None:
    if manifest.get('resource_id') != resource_id or manifest.get('binding_id') != binding_id:
        raise ArtifactError('export_unavailable')


def _capacity() -> None:
    root = _root()
    if not root.exists():
        return
    entries = list(islice(root.iterdir(), MAX_EXPORTS + 1))
    if len(entries) >= MAX_EXPORTS:
        raise ArtifactError('export_capacity_reached')
    total = 0
    for entry in entries:
        if entry.is_symlink() or entry.is_junction() or not entry.is_dir():
            raise ArtifactError('export_storage_unavailable')
        files = list(islice(entry.iterdir(), 9))
        if len(files) > 8:
            raise ArtifactError('export_capacity_reached')
        for path in files:
            if path.is_symlink() or path.is_junction() or not path.is_file():
                raise ArtifactError('export_storage_unavailable')
            total += path.stat().st_size
    if total + 2 * export.MAX_STRICT_EXPORT_BYTES > MAX_TOTAL_BYTES:
        raise ArtifactError('export_capacity_reached')


def create_export(project_id: str, *, expected_revision: str, export_id: str,
                  binding_id: str, format: str, pages: str = 'all',
                  pptx_mode: str | None = None, validate: Callable[[], None]) -> ArtifactExport:
    """Export an exact saved source once; the caller supplies a server command ID."""
    _identity(export_id)
    if not isinstance(binding_id, str) or not re.fullmatch(r'[A-Za-z0-9:_-]{1,128}', binding_id):
        raise ArtifactError('invalid_export')
    # One existing owner lock also serializes capacity reservation and expiry.
    with storage._project_save_lock('__client_exports_v1__'):
        validate()
        directory = _directory(export_id)
        request = {'resource_id': project_id, 'binding_id': binding_id, 'resource_revision': expected_revision,
                   'format': format, 'pages': pages, 'pptx_mode': pptx_mode}
        if directory.exists():
            manifest = _manifest(directory)
            _scope(manifest, project_id, binding_id)
            if manifest.get('request') != request:
                raise ArtifactError('export_conflict')
            result, _data = _ready(directory, manifest)
            if result.expires_at <= time.time():
                raise ArtifactError('export_expired')
            validate()
            return result
        project = read_artifact(project_id)
        if project.mode not in DESIGNER_MODES:
            raise ArtifactError('artifact_type_unavailable')
        if project.updated_at != expected_revision:
            raise ArtifactError('resource_revision_conflict', project.updated_at)
        indices = _options(format, pages, pptx_mode, len(project.pages))
        if (any(type(size) is not int or not 1 <= size <= 16384 for size in (project.canvas_width, project.canvas_height))
                or any(not isinstance(project.pages[index].html, str) for index in indices)):
            raise ArtifactError('resource_state_invalid')
        fingerprint = preview_fingerprint(project)
        validation_failure: Exception | None = None

        def guard():
            nonlocal validation_failure
            try:
                validate()
            except Exception as exc:
                validation_failure = exc
                raise
            current = read_artifact(project_id)
            if current.updated_at != expected_revision or preview_fingerprint(current) != fingerprint:
                raise ArtifactError('resource_revision_conflict', current.updated_at)

        _capacity()
        guard()
        directory.mkdir(parents=True, exist_ok=False)
        expires = time.time() + EXPORT_LIFETIME_SECONDS
        manifest = {'version': 1, 'export_id': export_id, 'resource_id': project_id, 'binding_id': binding_id,
                    'request': request, 'status': 'incomplete', 'expires_at': expires}
        storage._write_json_atomic(directory / 'manifest.json', manifest)
        functions = {'pdf': export.export_pdf, 'html': export.export_html, 'png': export.export_png,
                     'pptx': export.export_pptx_structured if pptx_mode == 'structured' else export.export_pptx_screenshot}
        try:
            with export.strict_export(guard) as context:
                payload = functions[format](project, pages=','.join(str(index + 1) for index in indices), directory=directory)
                guard()
                if not isinstance(payload, bytes) or not 0 < len(payload) <= export.MAX_STRICT_EXPORT_BYTES:
                    raise ArtifactError('export_size_limit')
                actual = Path(payload.saved_path)
                if actual.parent != directory or actual.is_symlink() or actual.is_junction() or _read(actual, export.MAX_STRICT_EXPORT_BYTES) != payload:
                    raise ArtifactError('export_unavailable')
                extension = 'zip' if format == 'png' and len(indices) > 1 else format
                mime = {'pdf': 'application/pdf', 'html': 'text/html', 'png': 'image/png', 'zip': 'application/zip',
                        'pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation'}[extension]
                filename = f'{export._sanitize_name(project.name)}.{extension}'
                result = ArtifactExport(export_id, project_id, expected_revision, format, pptx_mode, len(indices),
                                        filename, mime, len(payload), hashlib.sha256(payload).hexdigest(), 'ready',
                                        tuple(sorted(context.warnings)), expires)
                guard()
                # New per-command directory: never replace any pre-existing file.
                with (directory / 'payload').open('xb') as output:
                    output.write(payload)
                guard()
                manifest.update(status='ready', descriptor=asdict(result), generated_name=actual.name)
                storage._write_json_atomic(directory / 'manifest.json', manifest)
                return result
        except ArtifactError:
            raise
        except Exception as exc:
            if exc is validation_failure:
                raise
            raise ArtifactError('export_incomplete') from None


def read_export_payload(project_id: str, export_id: str, *, binding_id: str,
                        validate: Callable[[], None]) -> tuple[ArtifactExport, bytes]:
    """Return verified bytes under current binding admission; never return a path."""
    with storage._project_save_lock('__client_exports_v1__'):
        validate()
        directory = _directory(export_id)
        manifest = _manifest(directory)
        _scope(manifest, project_id, binding_id)
        result, payload = _ready(directory, manifest)
        if result.expires_at <= time.time():
            raise ArtifactError('export_expired')
        validate()
        return result, payload


def read_export(project_id: str, export_id: str, *, binding_id: str,
                validate: Callable[[], None]) -> ArtifactExport:
    return read_export_payload(project_id, export_id, binding_id=binding_id, validate=validate)[0]


def expire_exports(*, before: float, limit: int = 16) -> dict[str, int]:
    """Inspect expiry without deleting copies whose cross-process use is unknown."""
    if type(limit) is not int or not 1 <= limit <= 32:
        raise ArtifactError('invalid_export')
    retained = 0
    with storage._project_save_lock('__client_exports_v1__'):
        root = _root()
        if not root.is_dir():
            return {'removed': 0, 'retained': 0}
        for directory in islice(root.iterdir(), limit):
            try:
                _directory(directory.name)
                manifest = _manifest(directory)
                if manifest['expires_at'] <= min(before, time.time()):
                    retained += 1
            except (ArtifactError, OSError, KeyError, ValueError, TypeError):
                retained += 1
    return {'removed': 0, 'retained': retained}
