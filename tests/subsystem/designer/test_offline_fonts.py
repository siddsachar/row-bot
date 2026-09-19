from __future__ import annotations

import os

import pytest

from row_bot.designer import fonts
from tests.subsystem.designer.test_client_exports import project as _project, isolated as _isolated

project, isolated = _project, _isolated

pytestmark = pytest.mark.subsystem
_real_embedded = fonts.get_font_css_embedded


def woff(payload=b'font'):
    data = bytearray(48)
    data[:4] = b'wOF2'
    data[8:12] = (48 + len(payload)).to_bytes(4, 'big')
    data[12:14] = (1).to_bytes(2, 'big')
    data[16:20] = (128).to_bytes(4, 'big')
    data[20:24] = len(payload).to_bytes(4, 'big')
    return bytes(data) + payload


@pytest.fixture
def font_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(fonts, '_FONTS_DIR', tmp_path / 'bundled')
    monkeypatch.setattr(fonts, '_CACHE_DIR', tmp_path / 'cached')
    monkeypatch.setattr(fonts, '_manifest', {'Synthetic Font': {'400': 'synthetic-400.woff2'}})
    monkeypatch.setattr(fonts, 'get_font_css_embedded', _real_embedded)
    monkeypatch.setattr(fonts, '_cdn_font_css', lambda *_: pytest.fail('strict font attempted CDN'))
    monkeypatch.setattr(fonts, 'ensure_font', lambda *_: pytest.fail('strict font attempted download'))
    path = fonts._FONTS_DIR / 'synthetic-font'
    path.mkdir(parents=True)
    (path / 'synthetic-400.woff2').write_bytes(woff())
    return path


def test_strict_embedded_font_reads_saved_single_link_container_without_network(font_dir):
    value = fonts.get_font_css_embedded('Synthetic Font', strict=True)
    assert 'data:font/woff2;base64,' in value and '@import' not in value
    assert fonts.get_font_css_embedded('Arial', strict=True) == ''
    assert fonts.offline_font_fingerprint(['Synthetic Font'])


@pytest.mark.parametrize('family', ['../outside', 'Missing Font', "Font';url(file:///private)"])
def test_strict_missing_or_invalid_family_fails_explicitly(font_dir, family):
    with pytest.raises(fonts.FontReadError):
        fonts.get_font_css_embedded(family, strict=True)


def test_strict_cached_font_is_bounded_and_does_not_read_unknown_directory(font_dir):
    cached = fonts._CACHE_DIR / 'cached-font'
    cached.mkdir(parents=True)
    (cached / 'font-400.woff2').write_bytes(woff())
    assert 'data:font/woff2;base64,' in fonts.get_font_css_embedded('Cached Font', strict=True)
    for index in range(64):
        (cached / f'unknown-{index}').touch()
    with pytest.raises(fonts.FontReadError, match='font_budget_exceeded'):
        fonts.get_font_css_embedded('Cached Font', strict=True)
    assert len(list(cached.iterdir())) == 65


def test_strict_font_file_and_total_budgets_checked_before_open(font_dir, monkeypatch):
    path = font_dir / 'synthetic-400.woff2'
    monkeypatch.setattr(fonts, '_FONT_FILE_BYTES', len(woff()) - 1)
    with pytest.raises(fonts.FontReadError, match='font_file_invalid'):
        fonts.get_font_css_embedded('Synthetic Font', strict=True)
    monkeypatch.setattr(fonts, '_FONT_FILE_BYTES', 4096)
    monkeypatch.setattr(fonts, '_FONT_FAMILY_BYTES', len(woff()) - 1)
    with pytest.raises(fonts.FontReadError, match='font_budget_exceeded'):
        fonts.get_font_css_embedded('Synthetic Font', strict=True)
    assert path.read_bytes() == woff()


@pytest.mark.parametrize('data', [b'private bytes'.ljust(52, b'x'), woff()[:-1], b'wOF2' + bytes(48)])
def test_strict_font_invalid_container_never_embedded(font_dir, data):
    (font_dir / 'synthetic-400.woff2').write_bytes(data)
    with pytest.raises(fonts.FontReadError, match='font_file_invalid'):
        fonts.get_font_css_embedded('Synthetic Font', strict=True)


def test_strict_opened_leaf_replacement_rejected_before_foreign_bytes_return(font_dir, monkeypatch):
    path = font_dir / 'synthetic-400.woff2'
    replacement = font_dir / 'foreign.tmp'
    replacement.write_bytes(woff(b'foreign bytes'))
    original_open = os.open
    def swapped(value, *args, **kwargs):
        if str(value).endswith('synthetic-400.woff2'):
            replacement.replace(path)
        return original_open(value, *args, **kwargs)
    monkeypatch.setattr(os, 'open', swapped)
    with pytest.raises(fonts.FontReadError, match='font_revision_conflict'):
        fonts.get_font_css_embedded('Synthetic Font', strict=True)
    assert path.read_bytes() == woff(b'foreign bytes')


def test_strict_hardlinked_font_is_not_a_managed_read_authority(font_dir, tmp_path):
    path = font_dir / 'synthetic-400.woff2'
    os.link(path, tmp_path / 'other-copy')
    with pytest.raises(fonts.FontReadError, match='font_file_invalid'):
        fonts.get_font_css_embedded('Synthetic Font', strict=True)


def test_strict_manifest_cannot_redirect_file_reads(font_dir, monkeypatch):
    monkeypatch.setattr(fonts, '_manifest', {'Synthetic Font': {'400': '../foreign.woff2'}})
    with pytest.raises(fonts.FontReadError, match='font_catalog_invalid'):
        fonts.get_font_css_embedded('Synthetic Font', strict=True)


def test_saved_font_changes_invalidate_fingerprint_without_a_cache(font_dir):
    first = fonts.offline_font_fingerprint(['Synthetic Font'])
    path = font_dir / 'synthetic-400.woff2'
    path.write_bytes(woff(b'changed'))
    assert fonts.offline_font_fingerprint(['Synthetic Font']) != first


def test_legacy_unknown_font_keeps_existing_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(fonts, '_CACHE_DIR', tmp_path / 'empty')
    monkeypatch.setattr(fonts, '_manifest', {})
    assert 'fonts.googleapis.com' in _real_embedded('Legacy Font')


def test_new_client_preview_rebuilds_after_saved_font_change(project, font_dir, monkeypatch):
    from row_bot.designer import storage, client_service
    monkeypatch.setattr(fonts, 'get_font_css_embedded', _real_embedded)
    project.brand.heading_font = 'Synthetic Font'
    project.brand.body_font = 'Arial'
    storage.save_project(project)
    first = client_service.read_preview(project.id)
    same = client_service.read_preview(project.id, known_revision=first.preview_revision)
    assert same.unchanged and same.html is None
    (font_dir / 'synthetic-400.woff2').write_bytes(woff(b'changed'))
    second = client_service.read_preview(project.id, known_revision=first.preview_revision)
    assert second.resource_revision == first.resource_revision
    assert second.preview_revision != first.preview_revision and second.html is not None


@pytest.mark.parametrize('mode', ['deck', 'document', 'landing', 'app_mockup', 'storyboard'])
def test_static_preview_is_exact_inert_page_and_has_separate_revision(project, font_dir, monkeypatch, mode):
    from row_bot.designer import storage, client_service
    monkeypatch.setattr(fonts, 'get_font_css_embedded', _real_embedded)
    project.brand.heading_font = 'Synthetic Font'
    project.brand.body_font = 'Arial'
    project.mode = mode
    storage.save_project(project)
    interactive = client_service.read_preview(project.id, page_id='second')
    static = client_service.read_preview(project.id, page_id='second', static_page=True,
                                         known_revision=interactive.preview_revision)
    assert static.page_id == 'second' and static.page_index == 1
    assert static.html is not None and not static.scripts_allowed and not static.unchanged
    assert '<p>Two</p>' in static.html and '<h1>One</h1>' not in static.html
    assert "script-src 'none'" in static.html
    assert 'data-row-bot-route-host' not in static.html
    unchanged = client_service.read_preview(project.id, page_id='second', static_page=True,
                                           known_revision=static.preview_revision)
    assert unchanged.unchanged and unchanged.html is None


def test_static_preview_cannot_become_an_authoring_channel(project):
    from row_bot.designer import client_service
    with pytest.raises(client_service.ArtifactError, match='invalid_preview_identity'):
        client_service.read_preview(project.id, authoring=True, static_page=True)


@pytest.mark.skipif(os.name == 'nt', reason='Native POSIX descriptor-relative font parent-swap proof')
def test_posix_font_parent_swap_cannot_read_foreign_root(font_dir, tmp_path, monkeypatch):
    foreign, retained = tmp_path / 'foreign', tmp_path / 'retained'
    foreign.mkdir()
    (foreign / 'synthetic-400.woff2').write_bytes(woff(b'private'))
    original_open = os.open
    def swapped(value, flags, *args, **kwargs):
        if value == 'synthetic-400.woff2':
            font_dir.rename(retained)
            font_dir.symlink_to(foreign, target_is_directory=True)
        return original_open(value, flags, *args, **kwargs)
    monkeypatch.setattr(os, 'open', swapped)
    with pytest.raises(fonts.FontReadError, match='font_revision_conflict'):
        fonts.get_font_css_embedded('Synthetic Font', strict=True)
    assert (foreign / 'synthetic-400.woff2').read_bytes() == woff(b'private')
