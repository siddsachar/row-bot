"""Reviewed explicit publication/delivery over existing Designer owners."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import stat
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import UUID

from row_bot.designer import export, publish, share, storage
from row_bot.designer.client_exports import _options
from row_bot.designer.client_service import ArtifactError, read_artifact
from row_bot.designer.preview import preview_fingerprint
from row_bot.designer.state import DESIGNER_MODES

_REVIEW_KEY = secrets.token_bytes(32)


def _read_published(path: Path) -> bytes:
    if path.parent.is_symlink() or path.parent.is_junction() or path.is_symlink() or path.is_junction():
        raise ArtifactError('publish_path_denied')
    try:
        with path.open('rb') as handle:
            opened = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or opened.st_size > export.MAX_STRICT_EXPORT_BYTES:
                raise ArtifactError('publish_path_denied')
            value = handle.read(export.MAX_STRICT_EXPORT_BYTES + 1)
            finished = os.fstat(handle.fileno())
        current = path.lstat()
        if (len(value) > export.MAX_STRICT_EXPORT_BYTES or not os.path.samestat(current, opened)
                or (opened.st_size, opened.st_mtime_ns) != (finished.st_size, finished.st_mtime_ns)
                or (current.st_size, current.st_mtime_ns) != (finished.st_size, finished.st_mtime_ns)):
            raise ArtifactError('publish_revision_conflict')
        return value
    except OSError:
        raise ArtifactError('publish_path_denied') from None


def _published_version(project_id: str) -> str:
    path = publish.PUBLISHED_DIR / f'{project_id}.html'
    return hashlib.sha256(_read_published(path)).hexdigest() if os.path.lexists(path) else 'missing'


@dataclass(frozen=True)
class SharingReview:
    review_id: str
    resource_id: str
    resource_revision: str
    action: str
    channel_name: str | None
    recipient: str | None
    delivery: str
    pages: str
    page_count: int
    remote: bool
    requires_pairing: bool = True


@dataclass(frozen=True)
class SharingOutcome:
    status: str
    code: str | None
    resource_id: str
    resource_revision: str
    url: str | None
    link_kind: str | None
    submitted_count: int
    total_count: int


def _digest(value) -> str:
    return hmac.new(_REVIEW_KEY, json.dumps(value, sort_keys=True, separators=(',', ':'),
                                          default=str).encode('utf-8'), hashlib.sha256).hexdigest()


def _prepare(project_id: str, *, action: str, channel_name: str | None = None,
             target: str | None = None, delivery: str = 'link', pages: str = 'all',
             text: str = '', pptx_mode: str = 'screenshot', remote: bool = False,
             _revision_override: str | None = None, _published_override: str | None = None):
    from row_bot.channels import config, registry

    if (action not in {'publish', 'channel', 'x'} or type(remote) is not bool
            or not isinstance(text, str) or len(text) > 10000
            or target is not None and (not isinstance(target, str) or len(target) > 1024)
            or pptx_mode not in {'screenshot', 'structured'}):
        raise ArtifactError('invalid_share')
    project = read_artifact(project_id)
    if project.mode not in DESIGNER_MODES:
        raise ArtifactError('artifact_type_unavailable')
    indices = _options('html', pages, None, len(project.pages))
    if len(indices) > 200 or action == 'x' and len(indices) > 4:
        raise ArtifactError('sharing_media_limit')
    if action != 'channel' and (channel_name is not None or target is not None):
        raise ArtifactError('invalid_share')
    if action == 'channel' and delivery not in {'link', 'slides', 'pdf', 'pptx', 'html'}:
        raise ArtifactError('invalid_share')
    if (project.mode in {'landing', 'app_mockup', 'storyboard'} and (action == 'publish' or action == 'channel' and delivery == 'link')
            and len(indices) != len(project.pages)):
        raise ArtifactError('interactive_publish_requires_all_pages')
    channel, recipient, channel_scope = None, None, None
    if action == 'channel':
        channel = registry.get(channel_name)
        if channel is None or not channel.is_configured() or not channel.is_running():
            raise ArtifactError('channel_unavailable')
        resolved = share._resolve_target(channel, target)
        if isinstance(resolved, bool) or not isinstance(resolved, (str, int)) or not str(resolved).strip() or len(str(resolved)) > 1024:
            raise ArtifactError('recipient_unavailable')
        recipient = str(resolved)
        caps = channel.capabilities
        if delivery == 'slides' and not (caps.photo_out or caps.document_out):
            raise ArtifactError('delivery_unavailable')
        if delivery in {'pdf', 'pptx', 'html'} and not caps.document_out:
            raise ArtifactError('delivery_unavailable')
        channel_scope = [id(channel), asdict(registry.get_source(channel_name)), config.get_all(channel_name),
                         caps.photo_out, caps.document_out]
    if action == 'x':
        from row_bot.tools.x_tool import XTool
        channel_scope = XTool().get_config('post_operations', [])
    source = project.to_dict()
    source.pop('publish_url', None)
    source.pop('published_at', None)
    revision = _revision_override or project.updated_at
    source['updated_at'] = revision
    fingerprint = list(preview_fingerprint(project))
    fingerprint[1] = revision
    publication = _published_version(project_id) if action == 'publish' or action == 'channel' and delivery == 'link' else 'unused'
    binding = [project_id, revision, source, fingerprint, action, channel_name,
               recipient, delivery, pages, text, pptx_mode, remote, channel_scope]
    binding.append(_published_override if _published_override is not None else publication)
    review = SharingReview(_digest(binding), project_id, project.updated_at, action, channel_name,
                           recipient, delivery, pages, len(indices), remote)
    return review, project, channel


def prepare_share(project_id: str, *, action: str, channel_name: str | None = None,
                  target: str | None = None, delivery: str = 'link', pages: str = 'all',
                  text: str = '', pptx_mode: str = 'screenshot', remote: bool = False) -> SharingReview:
    """Read current source/recipient without rendering, starting tunnels or sending."""
    return _prepare(project_id, action=action, channel_name=channel_name, target=target,
                    delivery=delivery, pages=pages, text=text, pptx_mode=pptx_mode, remote=remote)[0]


def _publication_bytes(path: Path, data: bytes, *, command_id: str,
                       expected_digest: str, validate: Callable[[], None], checkpoint: Callable[[str, int, int], None]) -> None:
    from row_bot.developer.client_workspace import _empty_parent_guard, _directory_identity
    if (not 0 < len(data) <= export.MAX_STRICT_EXPORT_BYTES or path.parent != publish.PUBLISHED_DIR
            or path.parent.parent != storage.DESIGNER_DIR):
        raise ArtifactError('publish_path_denied')
    if not storage.DESIGNER_DIR.exists():
        with _empty_parent_guard(storage.DESIGNER_DIR.parent, _directory_identity(storage.DESIGNER_DIR.parent, parent=True)) as parent_fd:
            if os.name == 'nt':
                storage.DESIGNER_DIR.mkdir(exist_ok=True)
            else:
                try:
                    os.mkdir(storage.DESIGNER_DIR.name, dir_fd=parent_fd)
                except FileExistsError:
                    pass
    if os.name != 'nt':
        _publication_bytes_posix(path, data, command_id=command_id, expected_digest=expected_digest,
                                  validate=validate, checkpoint=checkpoint)
        return
    with _empty_parent_guard(storage.DESIGNER_DIR, _directory_identity(storage.DESIGNER_DIR, parent=True)):
        path.parent.mkdir(exist_ok=True)
        recovery = storage.DESIGNER_DIR / 'publish_recovery'
        recovery.mkdir(exist_ok=True)
        with _empty_parent_guard(recovery, _directory_identity(recovery, parent=True)):
            _publication_bytes_windows(path, data, command_id=command_id, expected_digest=expected_digest,
                                        validate=validate, checkpoint=checkpoint)


def _publication_bytes_posix(path: Path, data: bytes, *, command_id: str,
                             expected_digest: str, validate, checkpoint) -> None:
    from row_bot.developer.client_workspace import _empty_parent_guard, _directory_identity
    from row_bot.developer.edits import _rename_edit_no_replace, file_edit_metadata_digest
    from contextlib import ExitStack
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW

    def open_directory(parent_fd, name, *, exclusive=False):
        try:
            os.mkdir(name, dir_fd=parent_fd)
        except FileExistsError:
            if exclusive:
                raise
        descriptor = os.open(name, flags, dir_fd=parent_fd)
        stack.callback(os.close, descriptor)
        return descriptor

    def read(directory_fd, name):
        try:
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        except FileNotFoundError:
            return None
        with os.fdopen(descriptor, 'rb') as handle:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or opened.st_size > export.MAX_STRICT_EXPORT_BYTES:
                raise ArtifactError('publish_path_denied')
            captured = handle.read(export.MAX_STRICT_EXPORT_BYTES + 1)
            metadata = file_edit_metadata_digest(descriptor)
            finished = os.fstat(descriptor)
        named = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        def revision(info):
            return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns
        if (len(captured) > export.MAX_STRICT_EXPORT_BYTES or revision(opened) != revision(finished)
                or revision(named) != revision(finished)):
            raise ArtifactError('publish_revision_conflict')
        return captured, metadata, opened

    with ExitStack() as stack:
        designer_fd = stack.enter_context(_empty_parent_guard(storage.DESIGNER_DIR, _directory_identity(storage.DESIGNER_DIR, parent=True)))
        published_fd = open_directory(designer_fd, path.parent.name)
        published_info = os.fstat(published_fd)
        recovery_fd = open_directory(designer_fd, 'publish_recovery')
        command_fd = open_directory(recovery_fd, command_id, exclusive=True)
        candidate_fd = os.open('candidate', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=command_fd)
        with os.fdopen(candidate_fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        before = read(published_fd, path.name)
        if before is not None:
            if hashlib.sha256(before[0]).hexdigest() != expected_digest:
                raise ArtifactError('publish_revision_conflict')
            descriptor = os.open('candidate', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=command_fd)
            try:
                os.fchmod(descriptor, stat.S_IMODE(before[2].st_mode))
            finally:
                os.close(descriptor)
        elif expected_digest != 'missing':
            raise ArtifactError('publish_revision_conflict')
        candidate = read(command_fd, 'candidate')
        if before is not None and candidate[1] != before[1]:
            raise ArtifactError('publish_metadata_unavailable')
        checkpoint('publication_prepared', 0, 1)
        validate()
        if not os.path.samestat(published_info, path.parent.lstat()):
            raise ArtifactError('publish_revision_conflict')
        retired = False
        try:
            if before is not None:
                current = read(published_fd, path.name)
                if current is None or current[:2] != before[:2]:
                    raise ArtifactError('publish_revision_conflict')
                _rename_edit_no_replace(Path(path.name), Path('previous'), src_dir_fd=published_fd, dst_dir_fd=command_fd)
                retired = True
                retained = read(command_fd, 'previous')
                if retained is None or retained[:2] != before[:2]:
                    raise ArtifactError('publish_revision_conflict')
            validate()
            current_candidate = read(command_fd, 'candidate')
            if (current_candidate is None or current_candidate[:2] != candidate[:2]
                    or not os.path.samestat(current_candidate[2], candidate[2])):
                raise ArtifactError('publish_revision_conflict')
            _rename_edit_no_replace(Path('candidate'), Path(path.name), src_dir_fd=command_fd, dst_dir_fd=published_fd)
            published = read(published_fd, path.name)
            if (published is None or published[:2] != candidate[:2]
                    or not os.path.samestat(published[2], candidate[2])
                    or not os.path.samestat(published_info, path.parent.lstat())):
                raise ArtifactError('publish_revision_conflict')
        except Exception:
            # Restore only into an absent leaf of the originally admitted
            # directory. A replacement directory/name never receives writes.
            if retired:
                try:
                    os.stat(path.name, dir_fd=published_fd, follow_symlinks=False)
                except FileNotFoundError:
                    _rename_edit_no_replace(Path('previous'), Path(path.name), src_dir_fd=command_fd, dst_dir_fd=published_fd)
            raise


def _publication_bytes_windows(path: Path, data: bytes, *, command_id: str,
                                expected_digest: str, validate: Callable[[], None], checkpoint: Callable[[str, int, int], None]) -> None:
    from row_bot.developer.edits import file_edit_metadata_digest, _rename_edit_no_replace
    from row_bot.developer.client_workspace import _empty_parent_guard, _directory_identity

    if not 0 < len(data) <= export.MAX_STRICT_EXPORT_BYTES:
        raise ArtifactError('publish_size_limit')
    if path.parent != publish.PUBLISHED_DIR or path.is_symlink() or path.is_junction():
        raise ArtifactError('publish_path_denied')
    directory = storage.DESIGNER_DIR / 'publish_recovery' / command_id
    if directory.parent.is_symlink() or directory.parent.is_junction():
        raise ArtifactError('publish_path_denied')
    directory.mkdir(parents=True, exist_ok=False)
    candidate, previous = directory / 'candidate', directory / 'previous'
    with candidate.open('xb') as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    with _empty_parent_guard(path.parent, _directory_identity(path.parent, parent=True)):
        before, metadata = None, None
        if path.exists():
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > export.MAX_STRICT_EXPORT_BYTES:
                raise ArtifactError('publish_path_denied')
            before = _read_published(path)
            if hashlib.sha256(before).hexdigest() != expected_digest:
                raise ArtifactError('publish_revision_conflict')
            metadata = file_edit_metadata_digest(path)
            os.chmod(candidate, stat.S_IMODE(info.st_mode))
            if file_edit_metadata_digest(candidate) != metadata:
                raise ArtifactError('publish_metadata_unavailable')
        elif expected_digest != 'missing':
            raise ArtifactError('publish_revision_conflict')
        candidate_identity = candidate.stat()
        checkpoint('publication_prepared', 0, 1)
        validate()
        try:
            if before is not None:
                if _read_published(path) != before or file_edit_metadata_digest(path) != metadata:
                    raise ArtifactError('publish_revision_conflict')
                _rename_edit_no_replace(path, previous)
                if _read_published(previous) != before or file_edit_metadata_digest(previous) != metadata:
                    raise ArtifactError('publish_revision_conflict')
            validate()
            if metadata is not None and file_edit_metadata_digest(candidate) != metadata:
                raise ArtifactError('publish_metadata_unavailable')
            _rename_edit_no_replace(candidate, path)
            if not os.path.samestat(candidate_identity, path.stat()) or _read_published(path) != data:
                raise ArtifactError('publish_revision_conflict')
        except Exception:
            if previous.exists() and not path.exists():
                _rename_edit_no_replace(previous, path)
            raise


def execute_share(project_id: str, *, review_id: str, command_id: str,
                  action: str, channel_name: str | None = None, target: str | None = None,
                  delivery: str = 'link', pages: str = 'all', text: str = '',
                  pptx_mode: str = 'screenshot', remote: bool = False,
                  validate: Callable[[], None], checkpoint: Callable[[dict], None]) -> SharingOutcome:
    """Execute an explicitly approved review; caller owns durable effect admission."""
    try:
        if str(UUID(command_id)) != command_id:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise ArtifactError('invalid_share') from None
    validate()
    options = dict(action=action, channel_name=channel_name, target=target, delivery=delivery,
                   pages=pages, text=text, pptx_mode=pptx_mode, remote=remote)
    review, project, channel = _prepare(project_id, **options)
    if not isinstance(review_id, str) or not hmac.compare_digest(review_id, review.review_id):
        raise ArtifactError('share_review_changed')
    expected_revision = project.updated_at
    original_revision = project.updated_at
    original_publication = _published_version(project_id) if action == 'publish' or action == 'channel' and delivery == 'link' else 'unused'
    expected_publication = original_publication
    submitted, total, uncertain, published, last_stage = 0, 0, False, False, ''
    result_url, link_kind = None, None

    def guard(*, publication: bool = True):
        validate()
        current_review, current, current_channel = _prepare(project_id, **options,
            _revision_override=original_revision, _published_override=original_publication)
        if current.updated_at != expected_revision or current_channel is not channel:
            raise ArtifactError('share_review_changed')
        # Only our saved publication timestamp may advance. Source content and
        # destination authority remain bound to the original reviewed payload.
        if not hmac.compare_digest(current_review.review_id, review.review_id):
            raise ArtifactError('share_review_changed')
        if publication and expected_publication != 'unused' and _published_version(project_id) != expected_publication:
            raise ArtifactError('share_review_changed')

    def progress(stage: str, index: int, count: int):
        nonlocal submitted, total, uncertain, published, last_stage
        last_stage, total = stage, max(total, count)
        if stage == 'send_submitted':
            submitted, uncertain = index + 1, False
        if stage == 'send_not_started':
            uncertain = False
        if stage == 'send_started' or stage == 'send_uncertain':
            uncertain = True
        if stage == 'publish_file_completed':
            published = True
        checkpoint({'stage': stage, 'effect_id': hashlib.sha256(f'{command_id}:{stage}:{index}'.encode()).hexdigest(),
                    'index': index, 'count': count, 'outcome': stage.rsplit('_', 1)[-1]})

    def publisher(source, selected_pages, **_kwargs):
        nonlocal expected_revision, result_url, link_kind, expected_publication
        def writer(path, data):
            nonlocal expected_publication
            _publication_bytes(path, data, command_id=command_id, expected_digest=expected_publication,
                               validate=lambda: guard(publication=False), checkpoint=progress)
            expected_publication = hashlib.sha256(data).hexdigest()
        with storage._project_save_lock(project_id):
            guard()
            result = publish.publish_project(source, selected_pages, ensure_public=remote,
                validate=guard, checkpoint=progress,
                isolated=True, local_only=not remote,
                publish_bytes=writer)
            result_url = result['url']
            link_kind = 'remote_access' if result['public'] else 'local'
            expected_revision = source.updated_at
            return result

    try:
        with export.strict_export(guard):
            if action == 'publish':
                publisher(project, pages)
                return SharingOutcome('published', 'remote_access_unavailable' if remote and link_kind == 'local' else None,
                                      project_id, expected_revision, result_url, link_kind, 0, 0)
            directory = storage.DESIGNER_DIR / 'share_recovery' / command_id
            if directory.parent.is_symlink() or directory.parent.is_junction():
                raise ArtifactError('sharing_path_denied')
            directory.mkdir(parents=True, exist_ok=False)
            guard()
            if action == 'channel':
                result = share.share_project_to_channel(project, channel_name, delivery=delivery,
                    target=review.recipient, text=text, pages=pages, pptx_mode=pptx_mode,
                    validate=guard, checkpoint=progress, directory=directory, strict=True, publisher=publisher)
            else:
                result = share.share_project_to_x(project, text=text, pages=pages,
                    validate=guard, checkpoint=progress, directory=directory, strict=True)
            if result.get('success') is False:
                return SharingOutcome('uncertain', 'delivery_unconfirmed', project_id, expected_revision,
                                      result_url, link_kind, submitted, total)
            return SharingOutcome('submitted', None, project_id, expected_revision,
                                  result_url, link_kind, submitted, total)
    except Exception as exc:
        code = getattr(exc, 'code', '')
        allowed = {'capability_revoked', 'action_denied', 'share_review_changed', 'publish_revision_conflict',
                   'publish_metadata_unavailable', 'file_metadata_unavailable', 'publish_path_denied', 'sharing_path_denied'}
        code = code if code in allowed else 'delivery_unconfirmed' if uncertain else 'sharing_incomplete'
        status = 'uncertain' if uncertain or last_stage == 'tunnel_started' else 'partial' if published or submitted or last_stage in {'publication_prepared', 'publish_file_started'} else 'denied'
        return SharingOutcome(status, code, project_id, expected_revision, result_url, link_kind, submitted, total)
