from __future__ import annotations

from dataclasses import asdict
from uuid import uuid4
import io
import json
import os

import pytest

from row_bot.designer import client_design_controls as client, storage, history, fonts, brand
from row_bot.designer.client_service import ArtifactError
from row_bot.designer.state import DESIGNER_MODES, DesignerInteraction
from tests.subsystem.designer.test_client_exports import project as _project, isolated as _isolated

project, isolated = _project, _isolated
pytestmark = pytest.mark.subsystem


@pytest.fixture(autouse=True)
def catalogs(project, monkeypatch, tmp_path):
    monkeypatch.setattr(fonts, '_CACHE_DIR', tmp_path / 'fonts')
    monkeypatch.setattr(brand, '_BRAND_DIR', tmp_path / 'brands')
    monkeypatch.setattr(fonts, 'ensure_font', lambda *_args, **_kwargs: pytest.fail('no font network'))
    monkeypatch.setattr(brand, 'extract_brand_from_url', lambda *_args: pytest.fail('no brand network'))


def apply(project, operation, payload, **options):
    return client.apply_control(project.id, expected_revision=project.updated_at, operation=operation,
                                payload=payload, validate=lambda: None, **options)


def test_passive_reads_do_not_save_or_snapshot_and_targets_match_inline_ids(project, monkeypatch):
    from row_bot.designer.client_editing import read_editing
    monkeypatch.setattr(storage, 'save_project', lambda *_: pytest.fail('passive save'))
    monkeypatch.setattr(history, 'snapshot', lambda *_a, **_k: pytest.fail('passive snapshot'))
    state = client.read_controls(project.id)
    text = read_editing(project.id)
    assert state.items[0].id == text.elements[0].id
    assert state.resource_revision == project.updated_at
    assert not {'logo_b64', 'stored_name', 'filename'} & set(asdict(state.brand))
    client.read_controls(project.id, section='fonts')
    client.read_controls(project.id, section='presets')
    client.read_review(project.id)


@pytest.mark.parametrize('mode', DESIGNER_MODES)
def test_brand_applies_to_all_existing_pages_and_captures_history(project, mode):
    project.mode = mode
    storage.save_project(project)
    before = project.to_dict()
    result = apply(project, 'brand', {'primary_color': '#123456', 'heading_font': 'Inter'})
    assert result.brand.primary_color == '#123456'
    assert result.updated_at != before['updated_at']
    assert all('#123456' in page.html for page in result.pages)
    assert list((history.HISTORY_DIR / project.id).glob('*.json'))
    # Existing Designer manual_edits are session annotations, not JSON fields;
    # durable recovery belongs to the canonical before-edit history snapshot.
    assert any('brand' in message for message in result.manual_edits)


@pytest.mark.parametrize('updates', [
    {'primary_color': '</style>'}, {'logo_b64': 'bad'}, {'logo_max_height': True},
    {'logo_padding': 161}, {'heading_font': '../foreign'}, {'logo_mode': {}},
    {'logo_asset_id': 'foreign-asset'},
])
def test_invalid_brand_controls_never_change_saved_source(project, updates):
    before = storage.load_project(project.id).to_dict()
    with pytest.raises(ArtifactError):
        apply(project, 'brand', updates)
    assert storage.load_project(project.id).to_dict() == before


def test_style_mutates_exact_target_with_shared_owner_and_refuses_old_revision(project):
    state = client.read_controls(project.id)
    result = apply(project, 'style', {'font-size': '36px', 'color': '#112233'},
                   page_id=state.page_id, element_id=state.items[0].id)
    assert 'font-size: 36px' in result.pages[0].html
    assert result.pages[1].html == project.pages[1].html
    with pytest.raises(ArtifactError, match='resource_revision_conflict'):
        apply(project, 'style', {'font-size': '42px'}, page_id=state.page_id, element_id=state.items[0].id)


@pytest.mark.parametrize('updates', [
    {'color': 'url(https://bad.invalid)'}, {'width': 'expression(bad())'}, {'background': 'url(file:///bad)'},
    {'padding': '1px; color:red'}, {'font-size': '999999px'}, {'opacity': 'NaN'}, {'font-family': '../bad'},
    {'text-align': 'left; display:none'}, {'--secret': 'anything'},
])
def test_style_rejects_non_scalar_and_network_values(project, updates):
    state = client.read_controls(project.id)
    with pytest.raises(ArtifactError):
        apply(project, 'style', updates, page_id=state.page_id, element_id=state.items[0].id)
    assert storage.load_project(project.id).updated_at == project.updated_at


def test_read_property_targets_include_nontext_without_persisting_identifiers(project):
    project.pages[0].html = '<html><head><title>Hidden</title></head><body><div><img src="row-bot-asset:missing"><p>Copy</p></div></body></html>'
    storage.save_project(project)
    state = client.read_controls(project.id)
    assert {'body', 'div', 'img', 'p'} == {item.kind for item in state.items}
    rendered = client.authoring_page_html(project, project.pages[0].route_id)
    assert 'data-row-bot-element-id' in rendered
    assert 'data-row-bot-element-id' not in storage.load_project(project.id).pages[0].html


def test_hotspot_replace_and_clear_retains_unrelated_graph_edges(project):
    project.mode = 'app_mockup'
    unrelated = DesignerInteraction(source_route='second', selector='#other', target='first')
    project.interactions = [unrelated]
    storage.save_project(project)
    state = client.read_controls(project.id)
    options = dict(page_id=state.page_id, element_id=state.items[0].id)
    linked = apply(project, 'hotspot', {'action': 'navigate', 'target': 'second'}, **options)
    assert len(linked.interactions) == 2
    toggled = apply(linked, 'hotspot', {'action': 'toggle_state', 'target': 'menu-open'}, **options)
    assert len(toggled.interactions) == 2
    assert toggled.interactions[-1].action == 'toggle_state'
    cleared = apply(toggled, 'hotspot', {'action': 'clear'}, **options)
    assert len(cleared.interactions) == 1 and cleared.interactions[0].to_dict() == unrelated.to_dict()
    assert 'data-row-bot-action' not in cleared.pages[0].html


def test_collection_cursor_has_real_continuation_and_revision_scope(project):
    project.pages[0].html = ''.join(f'<p>Item {i}</p>' for i in range(80))
    storage.save_project(project)
    first = client.read_controls(project.id, limit=50)
    second = client.read_controls(project.id, limit=50, cursor=first.next_cursor)
    assert first.item_count == second.item_count == 80
    assert len(first.items) == 50 and len(second.items) == 30 and second.next_cursor is None
    assert not {item.id for item in first.items} & {item.id for item in second.items}
    with pytest.raises(ArtifactError, match='invalid_cursor'):
        client.read_controls(project.id, section='fonts', cursor=first.next_cursor)


def test_review_ids_are_stable_and_fix_must_be_current_enumerated_finding(project):
    project.pages[0].html = '<h2 style="font-size:12px">Small heading</h2><img src="row-bot-asset:missing">'
    storage.save_project(project)
    first = client.read_review(project.id)
    second = client.read_review(project.id)
    assert first == second and first.heuristic
    finding = next(item for item in first.findings if item.source == 'brand_lint' and item.category == 'missing_alt')
    fixed = apply(project, 'review_fix', {'finding_id': finding.id}, page_id=first.page_id)
    assert 'alt=""' in fixed.pages[0].html
    with pytest.raises(ArtifactError, match='design_finding_unavailable'):
        apply(fixed, 'review_fix', {'finding_id': 'forged'}, page_id=first.page_id)


def test_review_failure_never_becomes_clean_report(project, monkeypatch):
    monkeypatch.setattr(client.review, 'critique_page_html', lambda *_a: (_ for _ in ()).throw(ValueError('synthetic')))
    with pytest.raises(ArtifactError, match='design_review_unavailable'):
        client.read_review(project.id)
    assert client.review.build_review_report(project)['findings'] == []


def test_live_authority_revalidated_before_history_and_save(project):
    calls = []
    def validate():
        calls.append(1)
        if len(calls) == 3:
            raise ArtifactError('capability_revoked')
    with pytest.raises(ArtifactError, match='capability_revoked'):
        client.apply_control(project.id, expected_revision=project.updated_at, operation='brand',
                             payload={'primary_color': '#123456'}, validate=validate)
    assert storage.load_project(project.id).updated_at == project.updated_at


def test_catalog_bounds_fail_explicitly_without_reading_or_removing_unknown_files(project):
    fonts._CACHE_DIR.mkdir(parents=True)
    for index in range(1025):
        (fonts._CACHE_DIR / str(index)).touch()
    with pytest.raises(ArtifactError, match='design_catalog_too_large'):
        client.read_controls(project.id, section='fonts')
    assert len(list(fonts._CACHE_DIR.iterdir())) == 1025


def image_bytes():
    from PIL import Image
    buffer = io.BytesIO()
    Image.new('RGB', (3, 3), 'blue').save(buffer, format='PNG')
    return buffer.getvalue()


def upload(project, *, data=None, filename='synthetic.png', validate=lambda: None, checkpoint=lambda _: None):
    return client.upload_asset(project.id, expected_revision=project.updated_at, command_id=str(uuid4()),
                               filename=filename, data=image_bytes() if data is None else data,
                               validate=validate, checkpoint=checkpoint)


def test_asset_upload_insert_logo_remove_preserves_bytes_and_history(project):
    updated = upload(project)
    asset = updated.assets[0]
    path = storage.ASSETS_DIR / project.id / asset.stored_name
    assert path.read_bytes() == image_bytes()
    inserted = apply(updated, 'asset_insert', {'asset_id': asset.id}, page_id='first')
    assert f'asset://{asset.id}' in inserted.pages[0].html
    with pytest.raises(ArtifactError, match='asset_still_referenced'):
        apply(inserted, 'asset_forget', {'asset_id': asset.id})
    removed = apply(inserted, 'asset_remove', {'asset_id': asset.id}, page_id='first')
    branded = apply(removed, 'brand', {'logo_asset_id': asset.id})
    assert branded.brand.logo_asset_id == asset.id and branded.brand.logo_mime_type == 'image/png'
    unbranded = apply(branded, 'brand', {'logo_asset_id': ''})
    forgotten = apply(unbranded, 'asset_forget', {'asset_id': asset.id})
    assert not forgotten.assets and path.read_bytes() == image_bytes()


@pytest.mark.parametrize('data,filename', [
    (b'<script>bad()</script>', 'bad.png'), (b'anything', '../bad.png'),
    (b'<svg xmlns="http://www.w3.org/2000/svg"><script>bad()</script></svg>', 'bad.svg'),
    (b'<svg xmlns="http://www.w3.org/2000/svg" onload="bad()"></svg>', 'bad.svg'),
    (b'<svg xmlns="http://www.w3.org/2000/svg"><image href="https://bad.invalid/a"/></svg>', 'bad.svg'),
    (b'<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///private">]><svg>&x;</svg>', 'bad.svg'),
    (b'<svg xmlns="http://www.w3.org/2000/svg"><foreignObject><p>nested</p></foreignObject></svg>', 'bad.svg'),
    (b'not a movie', 'bad.mp4'),
])
def test_unsafe_asset_content_rejected_before_any_file_publication(project, data, filename):
    with pytest.raises(ArtifactError):
        upload(project, data=data, filename=filename)
    assert not storage.ASSETS_DIR.exists() or not list(storage.ASSETS_DIR.rglob('*.*'))
    assert storage.load_project(project.id).assets == []


def test_safe_svg_preserves_original_bytes_without_fetching(project):
    data = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10" fill="#123456"/></svg>'
    updated = upload(project, data=data, filename='logo.svg')
    assert client._asset_bytes(updated, updated.assets[0]) == data


def test_revoke_after_asset_write_retains_bytes_and_original_project(project):
    revoked = False
    stages = []
    def checkpoint(value):
        nonlocal revoked
        stages.append(value['stage'])
        if value['stage'] == 'asset_written':
            revoked = True
    def validate():
        if revoked:
            raise ArtifactError('capability_revoked')
    with pytest.raises(ArtifactError, match='capability_revoked'):
        upload(project, checkpoint=checkpoint, validate=validate)
    assert stages == ['asset_prepared', 'asset_written']
    assert storage.load_project(project.id).updated_at == project.updated_at
    assert storage.load_project(project.id).assets == []
    retained = list((storage.ASSETS_DIR / project.id).glob('*.png'))
    assert len(retained) == 1 and retained[0].read_bytes() == image_bytes()


def test_asset_owner_never_overwrites_existing_target_and_legacy_default_keeps_contract(project):
    first = storage.save_asset_bytes(project.id, 'same-id', 'data.png', b'first')
    path = storage.ASSETS_DIR / project.id / first
    with pytest.raises(FileExistsError):
        storage.save_asset_bytes(project.id, 'same-id', 'data.png', b'new', require_absent=True, validate=lambda: None)
    assert path.read_bytes() == b'first'
    assert storage.save_asset_bytes(project.id, 'same-id', 'data.png', b'legacy') == first
    assert path.read_bytes() == b'legacy'


def test_asset_owner_final_validation_prevents_admitted_write(project):
    calls = []
    def validate():
        calls.append(1)
        if len(calls) == 2:
            raise ArtifactError('capability_revoked')
    with pytest.raises(ArtifactError):
        storage.save_asset_bytes(project.id, 'new-id', 'data.png', b'private candidate', require_absent=True, validate=validate)
    folder = storage.ASSETS_DIR / project.id
    assert not (folder / 'new-id-data.png').exists()
    assert [path.read_bytes() for path in folder.glob('*.tmp')] == [b'private candidate']


def test_asset_modified_bytes_are_not_reused_by_logo_or_page(project):
    updated = upload(project)
    asset = updated.assets[0]
    (storage.ASSETS_DIR / project.id / asset.stored_name).write_bytes(b'External replacement')
    with pytest.raises(ArtifactError, match='asset_unavailable'):
        apply(updated, 'asset_insert', {'asset_id': asset.id}, page_id='first')
    with pytest.raises(ArtifactError, match='asset_unavailable'):
        apply(updated, 'brand', {'logo_asset_id': asset.id})
    assert storage.load_project(project.id).updated_at == updated.updated_at


def test_strict_preset_reads_fail_closed_but_legacy_retains_skip_behavior(project):
    brand._BRAND_DIR.mkdir(parents=True)
    path = brand._BRAND_DIR / 'broken.json'
    path.write_text('invalid json')
    with pytest.raises(ArtifactError, match='design_catalog_unavailable'):
        client.read_controls(project.id, section='presets')
    assert brand.load_brand_presets() == {} and path.read_text() == 'invalid json'
    path.write_text(json.dumps({'name': 'Synthetic', 'primary_color': '#123456'}))
    assert brand.get_all_presets(strict=True)['Synthetic'].primary_color == '#123456'


def test_strict_preset_rejects_opened_leaf_swap_without_adopting_foreign_values(project, monkeypatch):
    from pathlib import Path
    brand._BRAND_DIR.mkdir(parents=True)
    path = brand._BRAND_DIR / 'reviewed.json'
    replacement = brand._BRAND_DIR / 'replacement.tmp'
    path.write_text(json.dumps({'name': 'Original'}))
    replacement.write_text(json.dumps({'name': 'Foreign'}))
    opened = Path.open
    def swapped(self, *args, **kwargs):
        if self == path and args and args[0] == 'rb':
            replacement.replace(path)
        return opened(self, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', swapped)
    with pytest.raises(ValueError, match='brand_catalog_unavailable'):
        brand.load_brand_presets(strict=True)
    assert json.loads(path.read_text())['name'] == 'Foreign'


@pytest.mark.skipif(os.name == 'nt', reason='Native POSIX descriptor-relative parent swap proof')
def test_posix_asset_parent_swap_never_redirects_publication(project, monkeypatch, tmp_path):
    from row_bot.developer import edits
    outside = tmp_path / 'foreign'
    outside.mkdir()
    folder = storage.ASSETS_DIR / project.id
    retained = storage.ASSETS_DIR / 'retained-original'
    rename = edits._rename_edit_no_replace
    def swap(source, destination, **kwargs):
        folder.rename(retained)
        folder.symlink_to(outside, target_is_directory=True)
        return rename(source, destination, **kwargs)
    monkeypatch.setattr(edits, '_rename_edit_no_replace', swap)
    with pytest.raises(ValueError, match='asset_publication_changed'):
        storage.save_asset_bytes(project.id, 'asset-owned', 'data.png', b'retained source',
                                 require_absent=True, validate=lambda: None)
    assert list(outside.iterdir()) == []
    assert [path.read_bytes() for path in retained.iterdir()] == [b'retained source']


def test_property_read_omits_raw_path_or_script_values(project):
    project.pages[0].html = '<h1 style="color:url(file:///private);font-size:32px">Copy</h1>'
    storage.save_project(project)
    state = client.read_controls(project.id)
    selected = client.read_controls(project.id, element_id=state.items[0].id)
    assert selected.element.styles == {'font-size': '32px'}


def logo_preset():
    import base64
    from row_bot.designer.state import BrandConfig
    brand.save_brand_preset('With logo', BrandConfig(logo_b64=base64.b64encode(image_bytes()).decode('ascii')))
    return next(key for key, (name, _value) in client._presets().items() if name == 'With logo')


def test_preset_logo_imports_validated_bytes_and_one_project_revision(project):
    stages = []
    preset_id = logo_preset()
    saved = apply(project, 'preset', {'preset_id': preset_id}, command_id=str(uuid4()), checkpoint=stages.append)
    assert saved.brand.logo_asset_id == saved.assets[0].id
    assert saved.brand.logo_b64 is None
    assert client._asset_bytes(saved, saved.assets[0]) == image_bytes()
    assert [stage['stage'] for stage in stages] == ['asset_prepared', 'asset_written', 'asset_attached']
    assert storage.load_project(project.id).brand.logo_asset_id == saved.assets[0].id


def test_preset_logo_reuses_owned_matching_legacy_kind_without_new_file(project, monkeypatch):
    saved = upload(project)
    saved.assets[0].kind = 'brand-logo'
    storage.save_project(saved)
    monkeypatch.setattr(storage, 'save_asset_bytes', lambda *_a, **_k: pytest.fail('duplicate asset write'))
    result = apply(saved, 'preset', {'preset_id': logo_preset()})
    assert result.brand.logo_asset_id == saved.assets[0].id
    assert len(result.assets) == 1


def test_preset_logo_requires_durable_admission_before_bytes(project):
    with pytest.raises(ArtifactError, match='asset_admission_required'):
        apply(project, 'preset', {'preset_id': logo_preset()})
    assert storage.load_project(project.id).updated_at == project.updated_at
    assert not (storage.ASSETS_DIR / project.id).exists()


def test_preset_logo_revocation_retains_bytes_without_changing_saved_brand(project):
    revoked = False
    stages = []
    def checkpoint(stage):
        nonlocal revoked
        stages.append(stage['stage'])
        revoked = stage['stage'] == 'asset_written'
    def validate():
        if revoked:
            raise ArtifactError('capability_revoked')
    with pytest.raises(ArtifactError, match='capability_revoked'):
        client.apply_control(project.id, expected_revision=project.updated_at, operation='preset',
            payload={'preset_id': logo_preset()}, validate=validate, command_id=str(uuid4()), checkpoint=checkpoint)
    assert stages == ['asset_prepared', 'asset_written']
    assert storage.load_project(project.id).updated_at == project.updated_at
    assert [path.read_bytes() for path in (storage.ASSETS_DIR / project.id).glob('*.png')] == [image_bytes()]


def test_preset_foreign_logo_reference_is_explicitly_unavailable(project):
    from row_bot.designer.state import BrandConfig
    brand.save_brand_preset('Foreign', BrandConfig(logo_asset_id='other-project-logo'))
    preset_id = next(key for key, (name, _value) in client._presets().items() if name == 'Foreign')
    with pytest.raises(ArtifactError, match='design_preset_logo_unavailable'):
        apply(project, 'preset', {'preset_id': preset_id})
    assert storage.load_project(project.id).updated_at == project.updated_at


def test_preset_logo_changed_after_review_does_not_adopt_new_bytes(project):
    preset_id = logo_preset()
    path = brand._BRAND_DIR / 'With logo.json'
    value = json.loads(path.read_text())
    value['logo_b64'] = 'SGVsbG8='
    path.write_text(json.dumps(value))
    with pytest.raises(ArtifactError, match='design_preset_unavailable'):
        apply(project, 'preset', {'preset_id': preset_id}, command_id=str(uuid4()), checkpoint=lambda _: None)
    assert storage.load_project(project.id).updated_at == project.updated_at


def mutate_global(project, action='save', **kwargs):
    return client.mutate_preset(project.id, expected_revision=project.updated_at, action=action,
        command_id=str(uuid4()), validate=lambda: None, checkpoint=lambda _: None, **kwargs)


def test_global_preset_save_replace_delete_retain_original_single_link_and_saved_project(project):
    first = mutate_global(project, name='Shared')
    path = brand._BRAND_DIR / 'Shared.json'
    original = path.read_bytes()
    assert path.stat().st_nlink == 1
    assert brand.get_all_presets(strict=True)['Shared'].primary_color == project.brand.primary_color
    project.brand.primary_color = '#123456'
    storage.save_project(project)
    second = mutate_global(project, name='Shared', preset_id=first['preset_id'])
    assert second['preset_id'] != first['preset_id']
    assert original in [file.read_bytes() for file in (brand._BRAND_DIR / '.row-bot-edit-recovery').glob('*/previous')]
    assert path.stat().st_nlink == 1
    mutate_global(project, 'delete', preset_id=second['preset_id'])
    assert not path.exists()
    assert 'Shared' not in brand.get_all_presets(strict=True)
    assert storage.load_project(project.id).updated_at == project.updated_at


def test_global_preset_save_includes_project_logo_and_original_filename_stays_private(project):
    saved = upload(project)
    saved.brand.logo_asset_id = saved.assets[0].id
    storage.save_project(saved)
    result = mutate_global(saved, name='Reusable')
    value = brand.get_all_presets(strict=True)['Reusable']
    import base64
    assert base64.b64decode(value.logo_b64) == image_bytes() and value.logo_asset_id == ''
    assert set(result) == {'action', 'preset_id', 'name', 'resource_id', 'resource_revision'}


def test_global_preset_default_save_does_not_overwrite_existing_or_stale_review(project):
    first = mutate_global(project, name='Shared')
    with pytest.raises(ArtifactError, match='design_preset_exists'):
        mutate_global(project, name='Shared')
    path = brand._BRAND_DIR / 'Shared.json'
    value = json.loads(path.read_text())
    value['primary_color'] = '#abcdef'
    path.write_text(json.dumps(value))
    with pytest.raises(ArtifactError, match='design_preset_unavailable'):
        mutate_global(project, 'delete', preset_id=first['preset_id'])
    assert json.loads(path.read_text())['primary_color'] == '#abcdef'


def test_global_preset_revocation_after_retirement_restores_original(project):
    first = mutate_global(project, name='Shared')
    path = brand._BRAND_DIR / 'Shared.json'
    original = path.read_bytes()
    def validate():
        if not path.exists():
            raise ArtifactError('capability_revoked')
    with pytest.raises(ArtifactError, match='capability_revoked'):
        client.mutate_preset(project.id, expected_revision=project.updated_at, action='delete',
            preset_id=first['preset_id'], command_id=str(uuid4()), validate=validate, checkpoint=lambda _: None)
    assert path.read_bytes() == original


def test_global_preset_metadata_mismatch_fails_before_old_bytes_move(project, monkeypatch):
    from row_bot.developer import edits
    first = mutate_global(project, name='Shared')
    path = brand._BRAND_DIR / 'Shared.json'
    original = path.read_bytes()
    project.brand.primary_color = '#111222'
    storage.save_project(project)
    real = edits.file_edit_metadata_digest
    def metadata(value):
        if not isinstance(value, int) and value.name == 'candidate':
            return 'different metadata'
        return real(value)
    monkeypatch.setattr(edits, 'file_edit_metadata_digest', metadata)
    if os.name != 'nt':
        pytest.skip('Path callback is Windows-specific; descriptor metadata is independently covered')
    with pytest.raises(edits.FileEditError, match='file_metadata_unavailable'):
        mutate_global(project, name='Shared', preset_id=first['preset_id'])
    assert path.read_bytes() == original


def test_presentation_passive_read_has_bound_cursor_and_plain_notes(project, monkeypatch):
    monkeypatch.setattr(storage, 'save_project', lambda *_: pytest.fail('passive save'))
    monkeypatch.setattr(history, 'snapshot', lambda *_a, **_k: pytest.fail('passive history'))
    first = client.read_presentation(project.id, page_index=0, limit=1)
    second = client.read_presentation(project.id, page_index=0, cursor=first.next_cursor, limit=1)
    assert first.page_count == 2 and second.pages[0].index == 1 and second.next_cursor is None
    with pytest.raises(ArtifactError):
        client.read_presentation(project.id, page_index=1, cursor=first.next_cursor)
    assert 'html' not in json.dumps(asdict(first))


def test_review_draft_is_bound_to_current_finding_and_never_dispatches_or_saves(project, monkeypatch):
    project.pages[0].html = '<h2 style="font-size:12px">Small heading</h2><img src="row-bot-asset:missing">'
    storage.save_project(project)
    monkeypatch.setattr(client.review, 'request_ai_fix', lambda *_a, **_k: pytest.fail('implicit dispatch'))
    monkeypatch.setattr(storage, 'save_project', lambda *_: pytest.fail('draft mutation'))
    finding = client.read_review(project.id).findings[0]
    text = client.draft_review_fix(project.id, expected_revision=project.updated_at, page_id=finding.page_id, finding_id=finding.id)
    assert text.startswith('Fix this ') and 'designer_' in text
    with pytest.raises(ArtifactError, match='resource_revision_conflict'):
        client.draft_review_fix(project.id, expected_revision='old', page_id=finding.page_id, finding_id=finding.id)
    with pytest.raises(ArtifactError, match='design_finding_unavailable'):
        client.draft_review_fix(project.id, expected_revision=project.updated_at, page_id=finding.page_id, finding_id='unknown')


def test_retained_presentation_escapes_script_metadata_and_offline_mode_avoids_cdn(project, monkeypatch):
    from row_bot.designer import presentation
    project.pages[0].notes = '</script><script>unexpected()</script>'
    project.brand.heading_font = 'Missing Font'
    project.brand.body_font = 'Missing Font'
    monkeypatch.setattr(fonts, 'get_font_css_embedded', lambda family, **_kw: fonts._strict_font_css(family))
    with pytest.raises(fonts.FontReadError, match='font_unavailable'):
        presentation._build_reveal_html(project, offline_fonts=True)
    project.brand.heading_font = project.brand.body_font = 'Arial'
    html = presentation._build_reveal_html(project, offline_fonts=True)
    assert '\\u003c/script\\u003e' in html
    assert '</script><script>unexpected()' not in html
    assert 'fonts.googleapis.com' not in html


def test_global_preset_leaf_replacement_is_retained_without_overwrite(project, monkeypatch):
    from row_bot.developer import edits
    first = mutate_global(project, name='Shared')
    path = brand._BRAND_DIR / 'Shared.json'
    foreign = json.dumps({'name': 'Shared', 'primary_color': '#abcdef'}).encode()
    rename = edits._rename_edit_no_replace
    def replaced(source, destination, **kwargs):
        if str(source).endswith('Shared.json'):
            path.write_bytes(foreign)
        return rename(source, destination, **kwargs)
    monkeypatch.setattr(edits, '_rename_edit_no_replace', replaced)
    with pytest.raises(edits.FileEditError, match='file_revision_conflict'):
        mutate_global(project, 'delete', preset_id=first['preset_id'])
    assert path.read_bytes() == foreign


@pytest.mark.skipif(os.name == 'nt', reason='Native POSIX descriptor-relative brand parent swap proof')
def test_posix_global_preset_parent_swap_never_redirects_effect(project, monkeypatch, tmp_path):
    from row_bot.developer import edits
    first = mutate_global(project, name='Shared')
    outside, retained = tmp_path / 'foreign-brands', tmp_path / 'retained-brands'
    outside.mkdir()
    original = (brand._BRAND_DIR / 'Shared.json').read_bytes()
    rename = edits._rename_edit_no_replace
    def swapped(source, destination, **kwargs):
        if str(source) == 'Shared.json':
            brand._BRAND_DIR.rename(retained)
            brand._BRAND_DIR.symlink_to(outside, target_is_directory=True)
        return rename(source, destination, **kwargs)
    monkeypatch.setattr(edits, '_rename_edit_no_replace', swapped)
    with pytest.raises(edits.FileEditError, match='file_revision_conflict'):
        mutate_global(project, 'delete', preset_id=first['preset_id'])
    assert list(outside.iterdir()) == []
    assert (retained / 'Shared.json').read_bytes() == original
