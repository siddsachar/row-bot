"""Isolated Buddy preferences, exact private recovery and bounded media contracts."""
from dataclasses import asdict, replace
import json
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest

from row_bot.buddy import assets, config, client_service as service


@pytest.fixture
def owner(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "_BUDDY_CONFIG_PATH", tmp_path / "buddy_config.json")
    monkeypatch.setattr(assets, "_BUILTIN_PACKS_DIR", tmp_path / "builtins")
    monkeypatch.setattr(assets, "_USER_PACKS_DIR", tmp_path / "packs")
    return tmp_path


def pack(root, pack_id="hatch-one", *, preview=None):
    path = root / "packs" / pack_id
    path.mkdir(parents=True)
    (path / "preview.png").write_bytes(b"\x89PNG\r\n\x1a\nsynthetic")
    (path / "manifest.json").write_text(json.dumps({"id": pack_id, "name": "Synthetic Buddy",
        "runtime": "generated_still", "preview": preview or "preview.png"}), encoding="utf-8")
    return path


def update(changes, revision="missing", *, validate=lambda: None, checkpoint=lambda proof: None):
    return service.update_buddy(changes, expected_revision=revision, command_id=str(uuid4()),
                                 validate=validate, checkpoint=checkpoint)


def test_passive_snapshot_sanitizes_other_conversation_details_without_saving(owner, monkeypatch):
    import row_bot.buddy.brain as brain
    state = SimpleNamespace(mood="focused", energy=70, focus=80, alert=0, event_id=3,
                            details={"event_type": "generation.token", "secret": "private"}, message="private title")
    monkeypatch.setattr(brain, "get_buddy_brain", lambda: SimpleNamespace(tick=lambda: state))
    snapshot = service.read_buddy(validate=lambda: None)
    assert snapshot.status.label == "Writing" and snapshot.placement == "docked"
    assert "private" not in json.dumps(asdict(snapshot))
    assert not config._BUDDY_CONFIG_PATH.exists()


def test_save_preserves_unknown_properties_native_placement_and_single_link(owner):
    config.save_buddy_config({"private_legacy_property": "retained", "placement": "desktop", "visible": True})
    _, revision = config.read_buddy_config_revision()
    proofs = []
    result = update({"bubble_verbosity": "quiet"}, revision, checkpoint=proofs.append)
    saved, actual = config.read_buddy_config_revision()
    assert saved["private_legacy_property"] == "retained" and saved["placement"] == "desktop"
    assert actual == result and saved["bubble_verbosity"] == "quiet"
    assert config._BUDDY_CONFIG_PATH.stat().st_nlink == 1
    assert config.read_buddy_config_recovery(proofs[0]) == "applied"
    assert (owner / ".row-bot-edit-recovery" / proofs[0].command_id / "previous").exists()


def test_cas_conflict_preserves_external_bytes(owner):
    update({"visible": False})
    before = config._BUDDY_CONFIG_PATH.read_bytes()
    with pytest.raises(service.BuddyClientError, match="buddy_revision_conflict"):
        update({"visible": True})
    assert config._BUDDY_CONFIG_PATH.read_bytes() == before


@pytest.mark.parametrize("changes", [{"visible": 1}, {"collapsed": "true"}, {"pack_id": "../escape"},
    {"display_name": "x" * 129}, {"personality": "unknown"}, {"arbitrary_path": "private"}, {}])
def test_invalid_preferences_do_not_publish(owner, changes):
    with pytest.raises(ValueError):
        update(changes)
    assert not config._BUDDY_CONFIG_PATH.exists()


def test_checkpoint_failure_is_before_effect_and_passive_proof_is_not_applied(owner):
    proofs = []

    def checkpoint(proof):
        proofs.append(proof)
        raise RuntimeError("synthetic receipt unavailable")

    with pytest.raises(RuntimeError, match="receipt unavailable"):
        update({"visible": False}, checkpoint=checkpoint)
    assert not config._BUDDY_CONFIG_PATH.exists()
    assert config.read_buddy_config_recovery(proofs[0]) == "not_applied"
    assert not config._BUDDY_CONFIG_PATH.exists()


def test_revocation_after_checkpoint_preserves_original_and_exact_callback_error(owner):
    config.save_buddy_config({"visible": True})
    original = config._BUDDY_CONFIG_PATH.read_bytes()
    _, revision = config.read_buddy_config_revision()
    proofs = []
    sentinel = PermissionError("synthetic revoked")

    def validate():
        if proofs:
            raise sentinel

    with pytest.raises(PermissionError) as caught:
        update({"visible": False}, revision, validate=validate, checkpoint=proofs.append)
    assert caught.value is sentinel
    assert config._BUDDY_CONFIG_PATH.read_bytes() == original
    assert config.read_buddy_config_recovery(proofs[0]) == "not_applied"


def test_replaced_source_during_checkpoint_is_retained_and_not_overwritten(owner):
    config.save_buddy_config({"visible": True})
    _, revision = config.read_buddy_config_revision()
    external = b'{"external":"preserve"}'
    proofs = []

    def checkpoint(proof):
        proofs.append(proof)
        replacement = owner / "replacement.json"
        replacement.write_bytes(external)
        os.replace(replacement, config._BUDDY_CONFIG_PATH)

    with pytest.raises(ValueError, match="file_revision_conflict"):
        update({"visible": False}, revision, checkpoint=checkpoint)
    assert config._BUDDY_CONFIG_PATH.read_bytes() == external
    assert config.read_buddy_config_recovery(proofs[0]) == "conflict"


def test_recovery_cannot_adopt_equal_bytes_with_different_file_identity(owner):
    proofs = []
    update({"visible": False}, checkpoint=proofs.append)
    replacement = owner / "replacement.json"
    replacement.write_bytes(config._BUDDY_CONFIG_PATH.read_bytes())
    os.replace(replacement, config._BUDDY_CONFIG_PATH)
    assert config.read_buddy_config_recovery(proofs[0]) == "conflict"
    assert config.read_buddy_config_recovery(replace(proofs[0], command_id="../bad")) == "conflict"


@pytest.mark.parametrize("value", [b"invalid json", b"[]", b"x" * 65537], ids=["invalid", "shape", "oversize"])
def test_uncertain_config_is_not_replaced(owner, value):
    config._BUDDY_CONFIG_PATH.write_bytes(value)
    with pytest.raises(ValueError):
        update({"visible": False})
    assert config._BUDDY_CONFIG_PATH.read_bytes() == value


def test_hardlinked_config_is_not_read_or_replaced(owner):
    source = owner / "foreign.json"
    source.write_text('{"visible":true}', encoding="utf-8")
    os.link(source, config._BUDDY_CONFIG_PATH)
    with pytest.raises(ValueError):
        config.read_buddy_config_revision()
    assert source.read_text() == '{"visible":true}'


def test_pack_pages_continue_and_revision_invalidates_old_cursor(owner):
    for index in range(53):
        pack(owner, f"hatch-{index:03}")
    first = service.list_buddy_packs(validate=lambda: None)
    second = service.list_buddy_packs(cursor=first.next_cursor, validate=lambda: None)
    assert first.total == 53 and len(first.packs) == 50 and len(second.packs) == 3 and second.next_cursor is None
    pack(owner, "hatch-added")
    with pytest.raises(service.BuddyClientError, match="buddy_cursor_changed"):
        service.list_buddy_packs(cursor=first.next_cursor, validate=lambda: None)


def test_pack_media_and_selection_are_exact_contained_and_private(owner):
    path = pack(owner)
    descriptor = service.list_buddy_packs(validate=lambda: None).packs[0]
    assert descriptor.available and descriptor.assets[0].id == "preview"
    assert str(owner) not in json.dumps(asdict(descriptor))
    data, content_type = service.read_buddy_media(descriptor.id, "preview", expected_revision=descriptor.revision, validate=lambda: None)
    assert data == (path / "preview.png").read_bytes() and content_type == "image/png"
    update({"pack_id": descriptor.id})
    assert config.get_buddy_config()["pack_id"] == descriptor.id
    (path / "preview.png").write_bytes(b"\x89PNG\r\n\x1a\nchanged")
    with pytest.raises(service.BuddyClientError, match="buddy_revision_conflict"):
        service.read_buddy_media(descriptor.id, "preview", expected_revision=descriptor.revision, validate=lambda: None)


@pytest.mark.parametrize("kind", ["absolute", "parent", "hardlink", "oversize"])
def test_unsafe_pack_is_unavailable_and_healthy_sibling_survives(owner, kind):
    foreign = owner / "private.png"
    foreign.write_bytes(b"private bytes")
    path = pack(owner, "hatch-unsafe", preview=str(foreign) if kind == "absolute" else "../../private.png" if kind == "parent" else None)
    if kind == "hardlink":
        (path / "preview.png").unlink()
        os.link(foreign, path / "preview.png")
    if kind == "oversize":
        (path / "manifest.json").write_bytes(b"x" * 65537)
    pack(owner, "hatch-healthy")
    result = service.list_buddy_packs(validate=lambda: None)
    assert {item.id: item.available for item in result.packs} == {"hatch-healthy": True, "hatch-unsafe": False}
    assert foreign.read_bytes() == b"private bytes"


def test_asset_leaf_swap_before_open_does_not_return_replacement(owner, monkeypatch):
    path = pack(owner) / "preview.png"
    real_open = os.open

    def swap(name, flags, *args, **kwargs):
        if str(name).endswith("preview.png"):
            replacement = path.parent / "replacement"
            replacement.write_bytes(b"private replacement")
            os.replace(replacement, path)
        return real_open(name, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swap)
    with pytest.raises(ValueError, match="buddy_asset_unavailable"):
        assets.read_buddy_asset(path, root=path.parent)
    assert path.read_bytes() == b"private replacement"


def test_metadata_mismatch_preserves_existing_config_before_checkpoint(owner, monkeypatch):
    from row_bot.developer import edits
    config.save_buddy_config({"visible": True})
    original = config._BUDDY_CONFIG_PATH.read_bytes()
    _, revision = config.read_buddy_config_revision()
    real_digest = edits.file_edit_metadata_digest
    monkeypatch.setattr(edits, "file_edit_metadata_digest", lambda path:
                        "different" if not isinstance(path, int) and path.name == "candidate" else real_digest(path))
    if os.name != "nt":
        # On POSIX inspection is descriptor based; discriminate the new inode.
        original_inode = config._BUDDY_CONFIG_PATH.stat().st_ino
        monkeypatch.setattr(edits, "file_edit_metadata_digest", lambda path:
                            real_digest(path) if os.fstat(path).st_ino == original_inode else "different")
    proofs = []
    with pytest.raises(ValueError, match="file_metadata_unavailable"):
        update({"visible": False}, revision, checkpoint=proofs.append)
    assert not proofs and config._BUDDY_CONFIG_PATH.read_bytes() == original


@pytest.mark.skipif(os.name != "nt", reason="Windows alternate data stream contract")
def test_alternate_data_stream_config_remains_untouched(owner):
    config.save_buddy_config({"visible": True})
    stream = str(config._BUDDY_CONFIG_PATH) + ":synthetic"
    with open(stream, "wb") as handle:
        handle.write(b"retained metadata")
    original = config._BUDDY_CONFIG_PATH.read_bytes()
    with pytest.raises(ValueError, match="file_metadata_unavailable"):
        update({"visible": False})
    assert config._BUDDY_CONFIG_PATH.read_bytes() == original
    with open(stream, "rb") as handle:
        assert handle.read() == b"retained metadata"


def test_post_retirement_authority_denial_restores_original_without_losing_candidate(owner, monkeypatch):
    from row_bot.developer import edits
    config.save_buddy_config({"visible": True})
    original = config._BUDDY_CONFIG_PATH.read_bytes()
    _, revision = config.read_buddy_config_revision()
    proofs = []
    retired = [False]
    rename = edits._rename_edit_no_replace

    def intercept(source, destination, **kwargs):
        rename(source, destination, **kwargs)
        if os.fspath(destination).endswith("previous"):
            retired[0] = True

    def validate():
        if retired[0]:
            raise PermissionError("revoked after retirement")

    monkeypatch.setattr(edits, "_rename_edit_no_replace", intercept)
    with pytest.raises(PermissionError, match="revoked after retirement"):
        update({"visible": False}, revision, validate=validate, checkpoint=proofs.append)
    assert config._BUDDY_CONFIG_PATH.read_bytes() == original
    assert config.read_buddy_config_recovery(proofs[0]) == "not_applied"


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor-relative publication")
def test_parent_substitution_cannot_redirect_config_publication(owner, monkeypatch):
    from row_bot.developer import edits
    config.save_buddy_config({"visible": True})
    _, revision = config.read_buddy_config_revision()
    original = config._BUDDY_CONFIG_PATH.read_bytes()
    moved = owner.with_name(owner.name + "-retained")
    real_rename = edits._rename_edit_no_replace
    swapped = [False]

    def rename(source, destination, **kwargs):
        if not swapped[0]:
            swapped[0] = True
            owner.rename(moved)
            owner.mkdir()
            (owner / "buddy_config.json").write_bytes(b"foreign replacement")
        real_rename(source, destination, **kwargs)

    monkeypatch.setattr(edits, "_rename_edit_no_replace", rename)
    with pytest.raises(ValueError, match="file_revision_conflict"):
        update({"visible": False}, revision)
    assert (owner / "buddy_config.json").read_bytes() == b"foreign replacement"
    assert any(path.read_bytes() == original for path in moved.rglob("previous"))


def test_pack_catalog_enumeration_is_bounded_before_materializing(owner, monkeypatch):
    calls = []
    assets._USER_PACKS_DIR.mkdir()
    original = type(owner).iterdir

    def entries(path):
        if path == assets._USER_PACKS_DIR:
            for index in range(10000):
                calls.append(index)
                yield path / f"synthetic-{index}"
        else:
            yield from original(path)

    monkeypatch.setattr(type(owner), "iterdir", entries)
    with pytest.raises(service.BuddyClientError, match="buddy_pack_limit"):
        service.list_buddy_packs(validate=lambda: None)
    assert len(calls) == 4097


def test_response_byte_budget_keeps_real_continuation(owner, monkeypatch):
    assets._USER_PACKS_DIR.mkdir()
    for index in range(50):
        (assets._USER_PACKS_DIR / f"pack-{index:02}").mkdir()
    large_map = {f"{index:03}" + "k" * 125: "v" * 128 for index in range(64)}

    def descriptor(pack_id):
        return service.BuddyPack(pack_id, "Synthetic", "revision", "generated_still", True,
                                 False, (), large_map), {}, owner

    monkeypatch.setattr(service, "_pack", descriptor)
    result = service.list_buddy_packs(validate=lambda: None)
    assert len(json.dumps(asdict(result)).encode()) < 256 * 1024
    assert 0 < len(result.packs) < 50 and result.next_cursor
    following = service.list_buddy_packs(cursor=result.next_cursor, validate=lambda: None)
    assert following.packs[0].id != result.packs[-1].id
    assert following.total == 50


def test_creator_between_retirement_and_publication_is_preserved(owner, monkeypatch):
    from row_bot.developer import edits
    config.save_buddy_config({"visible": True})
    original = config._BUDDY_CONFIG_PATH.read_bytes()
    _, revision = config.read_buddy_config_revision()
    proofs = []
    rename = edits._rename_edit_no_replace

    def intercept(source, destination, **kwargs):
        rename(source, destination, **kwargs)
        if os.fspath(destination).endswith("previous"):
            config._BUDDY_CONFIG_PATH.write_bytes(b'{"external":"retained"}')

    monkeypatch.setattr(edits, "_rename_edit_no_replace", intercept)
    with pytest.raises(OSError):
        update({"visible": False}, revision, checkpoint=proofs.append)
    assert config._BUDDY_CONFIG_PATH.read_bytes() == b'{"external":"retained"}'
    assert (owner / ".row-bot-edit-recovery" / proofs[0].command_id / "previous").read_bytes() == original
    assert config.read_buddy_config_recovery(proofs[0]) == "conflict"


@pytest.mark.skipif(os.name == "nt", reason="POSIX symbolic link no-follow contract")
def test_internal_symbolic_link_is_not_resolved_away(owner):
    path = pack(owner)
    target = path / "saved.png"
    (path / "preview.png").rename(target)
    (path / "preview.png").symlink_to(target)
    result = service.list_buddy_packs(validate=lambda: None)
    assert not result.packs[0].available
