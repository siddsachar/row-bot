"""Reviewed sandbox imports use only disposable source files and local Git apply."""
from __future__ import annotations

import os
import subprocess
import copy
import uuid

import pytest
from tests.subsystem.developer.test_client_workspace_edits import domain as domain

pytestmark = pytest.mark.subsystem


def _canonical_apply(stage, patch):
    # The shared editor fixture forbids subprocess.run to catch host execution.
    # This explicit comparison runs Git only inside the named disposable tree.
    with subprocess.Popen(["git", "apply", "--whitespace=nowarn", "-"], cwd=stage,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
        _, error = process.communicate(patch, timeout=15)
        assert process.returncode == 0, error


@pytest.mark.parametrize("emitter", ["_unified_file_patch", "_client_file_patch"])
@pytest.mark.parametrize("path,before,after", [
    ("plain.txt", "old\n", "new\n"), ("space name.txt", "old\n", "new\n"),
    ("café 日本語.txt", "old\n", "new\n"), ("no-final.txt", "old", "new"),
    ("empty.txt", None, ""), ("empty.txt", "", None),
    ("replacement.txt", "first\nuntouched \ufffd\nlast\n", "changed\nuntouched \ufffd\nlast\n"),
])
def test_canonical_pending_emitter_retains_exact_git_file_semantics(imports, tmp_path, emitter, path, before, after):
    d = imports
    patch = getattr(d.sandbox, emitter)(path, before, after)
    assert patch
    stage = tmp_path / "canonical"
    stage.mkdir()
    target = stage / path
    if before is not None:
        target.write_bytes(before.encode())
    _canonical_apply(stage, (patch.rstrip("\n") + "\n").encode())
    assert target.read_bytes() == after.encode() if after is not None else not target.exists()


def test_non_utf8_untouched_text_bytes_have_retained_canonical_import_semantics(imports, tmp_path):
    d = imports
    before = b"first\n" + b"context\n" * 10 + b"retained \xff\n"
    after = before.replace(b"first", b"changed", 1)
    pending = d.sandbox._record_pending_change(d.workspace, "chat", "synthetic replacement-text edit",
        {"legacy.txt": before.decode("utf-8", errors="replace")},
        {"legacy.txt": after.decode("utf-8", errors="replace")})
    (d.root / "legacy.txt").write_bytes(before)
    legacy = tmp_path / "retained-canonical"
    legacy.mkdir()
    (legacy / "legacy.txt").write_bytes(before)
    _canonical_apply(legacy, pending.patch.encode())
    assert (legacy / "legacy.txt").read_bytes() == after
    review = d.imports.review_workspace_import(d.workspace.id, "chat", pending.id)
    result = d.apply(review)
    assert result.status == "imported", result
    assert (d.root / "legacy.txt").read_bytes() == after
    assert next(d.root.glob(".row-bot-edit-recovery/*/previous")).read_bytes() == before


def test_imported_attribute_file_does_not_reinterpret_other_captured_outputs(imports):
    d = imports
    pending = d.pending({"plain.txt": "old\n"}, {"plain.txt": "new\n", ".gitattributes": "*.txt text eol=crlf\n"})
    review = d.imports.review_workspace_import(d.workspace.id, "chat", pending.id)
    result = d.apply(review)
    assert result.status == "imported", result
    assert (d.root / "plain.txt").read_bytes() == b"new\n"
    assert (d.root / ".gitattributes").read_bytes() == b"*.txt text eol=crlf\n"


def test_header_remapping_never_changes_patch_hunk_content(imports):
    d = imports
    before = "--- a/.gitattributes\n+++ b/.gitattributes\ndiff --git a/.gitattributes b/.gitattributes\n"
    after = before + "retained \u0085 literal\n"
    pending = d.pending({"file.txt": before}, {"file.txt": after, ".gitattributes": "* filter=untrusted\n"})
    review = d.imports.review_workspace_import(d.workspace.id, "chat", pending.id)
    result = d.apply(review)
    assert result.status == "imported", result
    assert (d.root / "file.txt").read_bytes() == after.encode()


@pytest.mark.parametrize("metadata_rejects", [False, True])
def test_attribute_publication_marker_recovery_is_history_only(imports, monkeypatch, metadata_rejects):
    d = imports
    pending = d.pending({"nested/file.txt": "old\n"},
        {"nested/file.txt": "new\n", ".gitattributes": "*.txt text eol=crlf\n"})
    capture = d.edits.capture_patch_git_policy
    def policy(*args):
        value = capture(*args)
        if (d.root / ".gitattributes").exists():
            if metadata_rejects:
                raise ValueError("sandbox_import_format_unavailable")
            value["attributes"]["nested/file.txt"]["eol"] = "crlf"
        return value
    monkeypatch.setattr(d.edits, "capture_patch_git_policy", policy)
    review = d.imports.review_workspace_import(d.workspace.id, "chat", pending.id)
    command = str(uuid.uuid4())
    marker = d.sandbox.mark_pending_change_imported
    monkeypatch.setattr(d.sandbox, "mark_pending_change_imported", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("synthetic lost marker")))
    assert d.apply(review, command_id=command).status == "partial"
    proof = d.import_receipts[-1]
    identities = {path: (d.root / path).stat().st_ino for path in review.files}
    monkeypatch.setattr(d.sandbox, "mark_pending_change_imported", marker)
    def forbidden(*_args, **_kwargs):
        pytest.fail("Completed attribute recovery attempted host candidate preparation/publication")
    for name in ("prepare_reviewed_patch", "publish_text_revision", "publish_file_removal", "publish_import_directory"):
        monkeypatch.setattr(d.edits, name, forbidden)
    result = d.apply(review, command_id=command, recovery=proof)
    assert result.status == "imported", result
    assert {path: (d.root / path).stat().st_ino for path in review.files} == identities
    assert len(d.ledger.list_change_sets(workspace_id=d.workspace.id)) == 1


@pytest.mark.parametrize("change", ["replace_file", "missing_file", "core_config"])
def test_attribute_recovery_does_not_reapply_incomplete_or_changed_publications(imports, monkeypatch, change):
    d = imports
    pending = d.pending({"file.txt": "old\n"}, {"file.txt": "new\n", ".gitattributes": "*.txt text eol=crlf\n"})
    capture = d.edits.capture_patch_git_policy
    def policy(*args):
        value = capture(*args)
        if (d.root / ".gitattributes").exists():
            value["attributes"]["file.txt"]["eol"] = "crlf"
        return value
    monkeypatch.setattr(d.edits, "capture_patch_git_policy", policy)
    review = d.imports.review_workspace_import(d.workspace.id, "chat", pending.id)
    command = str(uuid.uuid4())
    monkeypatch.setattr(d.sandbox, "mark_pending_change_imported", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("synthetic marker failure")))
    assert d.apply(review, command_id=command).status == "partial"
    proof = d.import_receipts[-1]
    if change in {"replace_file", "missing_file"}:
        (d.root / "file.txt").rename(d.root / "retained-later")
        if change == "replace_file":
            (d.root / "file.txt").write_bytes(b"new\n")
    else:
        monkeypatch.setenv("GIT_CONFIG_VALUE_0", "true")
    def forbidden(*_args, **_kwargs):
        pytest.fail("Incomplete recovery attempted host publication")
    monkeypatch.setattr(d.edits, "publish_text_revision", forbidden)
    result = d.apply(review, command_id=command, recovery=proof)
    assert result.status == "partial" and not result.imported
    if change == "missing_file":
        assert not (d.root / "file.txt").exists()


def test_directory_review_preflights_complete_recovery_not_only_public_names(imports):
    d = imports
    prefix = "/".join(["nested"] * 28)
    values = {prefix + f"/{index}.txt": "new\n" for index in range(30)}
    pending = d.sandbox._record_pending_change(d.workspace, "chat", "synthetic depth budget", {}, values,
        command_id=str(uuid.uuid4()))
    with pytest.raises(ValueError, match="sandbox_import_too_large"):
        d.imports.review_workspace_import(d.workspace.id, "chat", pending.id)
    assert not (d.root / "nested").exists()


def test_directory_name_collision_at_publication_preserves_unrelated_directory(imports, monkeypatch):
    d = imports
    pending = _new_parent_change(d)
    review = d.imports.review_workspace_import(d.workspace.id, "chat", pending.id)
    rename = d.edits._rename_edit_no_replace
    def raced(source, destination, **kwargs):
        if str(destination).endswith("new"):
            if kwargs.get("dst_dir_fd") is None:
                os.mkdir(destination)
            else:
                os.mkdir(destination, dir_fd=kwargs["dst_dir_fd"])
        return rename(source, destination, **kwargs)
    monkeypatch.setattr(d.edits, "_rename_edit_no_replace", raced)
    first = d.apply(review)
    assert first.status == "partial" and not first.files_applied
    assert list((d.root / "new").iterdir()) == []


def test_directory_candidate_replacement_after_receipt_is_not_published(imports):
    d = imports
    pending = _new_parent_change(d)
    review = d.imports.review_workspace_import(d.workspace.id, "chat", pending.id)
    def replaced(progress):
        d.import_receipts.append(copy.deepcopy(progress))
        proof = progress["directories"]["new"]
        candidate = d.root / ".row-bot-edit-recovery" / proof["command_id"] / "directory"
        candidate.rename(candidate.with_name("retained-candidate"))
        candidate.mkdir()
    result = d.imports.import_workspace_change(review, command_id=str(uuid.uuid4()), confirmed=True,
        persist_recovery=replaced)
    assert result.status == "partial" and result.code == "edit_recovery_conflict"
    assert not (d.root / "new").exists()


@pytest.mark.parametrize("tamper", ["command_id", "root_identity", "parent_identity"])
def test_directory_recovery_requires_exact_original_command_scope(imports, monkeypatch, tamper):
    d = imports
    pending = _new_parent_change(d)
    review = d.imports.review_workspace_import(d.workspace.id, "chat", pending.id)
    command = str(uuid.uuid4())
    publish = d.edits.publish_text_revision
    monkeypatch.setattr(d.edits, "publish_text_revision", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("synthetic interruption")))
    assert d.apply(review, command_id=command).status == "partial"
    proof = copy.deepcopy(d.import_receipts[-1])
    proof["directories"]["new"][tamper] = str(uuid.uuid4())
    monkeypatch.setattr(d.edits, "publish_text_revision", publish)
    result = d.apply(review, command_id=command, recovery=proof)
    assert result.status == "partial" and not result.imported
    assert not (d.root / "new/nested/a.txt").exists()


def test_nested_non_ascii_receipt_includes_all_directory_proofs_inside_budget(imports):
    import json
    from dataclasses import asdict
    d = imports
    values = {f"café-{index}/日本語/file.txt": "retained text\n" for index in range(20)}
    pending = d.sandbox._record_pending_change(d.workspace, "chat", "synthetic bounded names", {}, values,
        command_id=str(uuid.uuid4()))
    review = d.imports.review_workspace_import(d.workspace.id, "chat", pending.id)
    result = d.apply(review)
    assert result.status == "imported", result
    assert len(d.import_receipts[-1]["directories"]) == 40
    assert len(json.dumps(d.import_receipts[-1], ensure_ascii=True).encode()) <= d.imports.RECOVERY_BYTES
    envelope = {"review": asdict(review), "result": asdict(result), "_import": d.import_receipts[-1],
        "envelope_reserve": "x" * (16 * 1024)}
    assert len(json.dumps(envelope, ensure_ascii=True).encode()) < 256 * 1024


@pytest.mark.parametrize("before,after", [("", None), (None, ""), ("old", "new")])
def test_existing_pending_owner_roundtrip_preserves_empty_and_final_newline(imports, before, after):
    d = imports
    path = "café space.txt"
    if before is not None:
        (d.root / path).write_bytes(before.encode())
    pending = d.sandbox._record_pending_change(d.workspace, "chat", "retained owner edit",
        {path: before} if before is not None else {}, {path: after} if after is not None else {})
    assert pending is not None
    review = d.imports.review_workspace_import(d.workspace.id, "chat", pending.id)
    result = d.apply(review)
    assert result.status == "imported", result
    assert (d.root / path).read_bytes() == after.encode() if after is not None else not (d.root / path).exists()


def test_retained_rename_is_imported_as_canonical_delete_and_create(imports):
    d = imports
    (d.root / "original.txt").write_bytes(b"retained\n")
    pending = d.sandbox._record_pending_change(d.workspace, "chat", "retained rename",
        {"original.txt": "retained\n"}, {"new-parent/renamed.txt": "retained\n"})
    review = d.imports.review_workspace_import(d.workspace.id, "chat", pending.id)
    result = d.apply(review)
    assert result.status == "imported", result
    assert not (d.root / "original.txt").exists()
    assert (d.root / "new-parent/renamed.txt").read_bytes() == b"retained\n"
    assert next(d.root.glob(".row-bot-edit-recovery/*/previous")).read_bytes() == b"retained\n"


@pytest.mark.parametrize("path", ["directory b/empty space.txt", "a/empty.txt", "b/empty.txt"])
def test_empty_file_diff_header_with_space_before_b_directory_is_unambiguous(imports, tmp_path, path):
    d = imports
    pending = d.sandbox._record_pending_change(d.workspace, "chat", "synthetic empty path", {}, {path: ""},
        command_id=str(uuid.uuid4()))
    stage = tmp_path / "canonical-space-header"
    stage.mkdir()
    _canonical_apply(stage, pending.patch.encode())
    assert (stage / path).read_bytes() == b""
    review = d.imports.review_workspace_import(d.workspace.id, "chat", pending.id)
    result = d.apply(review)
    assert result.status == "imported", result
    assert (d.root / path).read_bytes() == b""


def test_removed_hunk_text_is_not_a_new_file_header(imports):
    d = imports
    pending = d.pending({"file.txt": "-- a/unreviewed.txt\n++ b/another.txt\n"}, {"file.txt": "reviewed replacement\n"})
    review = d.imports.review_workspace_import(d.workspace.id, "chat", pending.id)
    assert review.files == ("file.txt",)
    result = d.apply(review)
    assert result.status == "imported", result
    assert (d.root / "file.txt").read_bytes() == b"reviewed replacement\n"
    assert not (d.root / "unreviewed.txt").exists()


def test_retained_legacy_patch_history_uses_real_a_directory(imports, monkeypatch):
    d = imports
    (d.root / "a").mkdir()
    (d.root / "a/file.txt").write_bytes(b"before\n")
    (d.root / "file.txt").write_bytes(b"unrelated\n")
    patch = d.sandbox._client_file_patch("a/file.txt", "before\n", "after\n")
    # This retained-path comparison explicitly authorizes Git only in the
    # disposable fixture workspace; strict import tests keep it forbidden.
    monkeypatch.setattr(d.edits.subprocess, "run", d.original_run)
    change, _ = d.edits.apply_patch_to_workspace(workspace_id=d.workspace.id, thread_id="chat",
        patch=patch, approval_mode="approve", confirmed=True)
    assert change.files[0].path == "a/file.txt"
    assert change.files[0].before_text == "before\n"
    assert (d.root / "file.txt").read_bytes() == b"unrelated\n"
    d.edits.revert_change_set(d.workspace.id, change.id)
    # This is the retained legacy text Undo contract. Exact byte restoration
    # needs the separately reviewed retained-original publication seam.
    assert (d.root / "a/file.txt").read_text(encoding="utf-8") == "before\n"
    assert (d.root / "file.txt").read_bytes() == b"unrelated\n"


def test_retained_revert_does_not_delete_unrelated_same_byte_root_file(imports):
    d = imports
    pending = d.sandbox._record_pending_change(d.workspace, "chat", "synthetic nested creation", {},
        {"b/file.txt": "same bytes\n"}, command_id=str(uuid.uuid4()))
    review = d.imports.review_workspace_import(d.workspace.id, "chat", pending.id)
    result = d.apply(review)
    assert result.status == "imported", result
    (d.root / "file.txt").write_bytes(b"same bytes\n")
    from row_bot.developer import client_undo
    undo_review = client_undo.review_workspace_undo(d.workspace.id, "chat", result.change_set_id)
    undone = client_undo.undo_workspace_change(undo_review, command_id=str(uuid.uuid4()), confirmed=True,
        persist_recovery=lambda value: None)
    assert undone.status == "undone", undone
    assert not (d.root / "b/file.txt").exists()
    assert (d.root / "file.txt").read_bytes() == b"same bytes\n"


def _new_parent_change(d):
    return d.sandbox._record_pending_change(d.workspace, "chat", "synthetic nested edit", {},
        {"new/nested/a.txt": "first\n", "new/nested/b.txt": "second\n"}, command_id=str(uuid.uuid4()))


def test_new_nested_parents_are_explicit_reviewed_and_shared_once(imports):
    d = imports
    pending = _new_parent_change(d)
    review = d.imports.review_workspace_import(d.workspace.id, "chat", pending.id)
    assert review.directories == ("new", "new/nested")
    assert not (d.root / "new").exists()
    result = d.apply(review)
    assert result.status == "imported", result
    assert (d.root / "new/nested/a.txt").read_bytes() == b"first\n"
    assert (d.root / "new/nested/b.txt").read_bytes() == b"second\n"
    assert set(d.import_receipts[-1]["directories"]) == {"new", "new/nested"}
    assert len(d.ledger.list_change_sets(workspace_id=d.workspace.id)) == 1


@pytest.mark.parametrize("kind", ["empty", "populated", "file"])
def test_reviewed_missing_parent_never_adopts_an_unrelated_name(imports, kind):
    d = imports
    pending = _new_parent_change(d)
    review = d.imports.review_workspace_import(d.workspace.id, "chat", pending.id)
    target = d.root / "new"
    if kind == "file":
        target.write_bytes(b"unrelated")
    else:
        target.mkdir()
        if kind == "populated":
            (target / "unrelated").write_bytes(b"keep")
    result = d.apply(review)
    assert result.status in {"conflict", "denied"} and not result.files_applied
    assert not (target / "nested").exists()
    assert not d.import_receipts


def test_directory_receipt_failure_has_no_public_creation_or_implicit_adoption(imports):
    d = imports
    pending = _new_parent_change(d)
    review = d.imports.review_workspace_import(d.workspace.id, "chat", pending.id)
    command = str(uuid.uuid4())
    def fail(_proof):
        raise OSError("synthetic receipt failure")
    result = d.imports.import_workspace_change(review, command_id=command, confirmed=True, persist_recovery=fail)
    assert result.status == "partial" and result.code == "edit_receipt_unconfirmed"
    assert not (d.root / "new").exists()
    retry = d.apply(review, command_id=command)
    assert retry.status != "imported" and not (d.root / "new").exists()
    assert not d.ledger.list_change_sets(workspace_id=d.workspace.id)


@pytest.mark.parametrize("boundary", ["before_rename", "after_rename", "before_file"])
def test_new_parent_original_recovery_proves_identity_without_recreating(imports, monkeypatch, boundary):
    d = imports
    pending = _new_parent_change(d)
    review = d.imports.review_workspace_import(d.workspace.id, "chat", pending.id)
    command = str(uuid.uuid4())
    rename, publish = d.edits._rename_edit_no_replace, d.edits.publish_text_revision
    def interrupted(source, destination, **kwargs):
        if str(destination).endswith("new"):
            if boundary == "after_rename":
                rename(source, destination, **kwargs)
            raise OSError("synthetic rename interruption")
        return rename(source, destination, **kwargs)
    if boundary == "before_file":
        monkeypatch.setattr(d.edits, "publish_text_revision", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("synthetic file interruption")))
    else:
        monkeypatch.setattr(d.edits, "_rename_edit_no_replace", interrupted)
    first = d.apply(review, command_id=command)
    assert first.status == "partial" and not first.files_applied
    inode = (d.root / "new").stat().st_ino if (d.root / "new").exists() else None
    monkeypatch.setattr(d.edits, "_rename_edit_no_replace", rename)
    monkeypatch.setattr(d.edits, "publish_text_revision", publish)
    retry = d.apply(review, command_id=command, recovery=d.import_receipts[-1])
    assert retry.status == "imported", retry
    if inode is not None:
        assert (d.root / "new").stat().st_ino == inode
    assert (d.root / "new/nested/b.txt").read_bytes() == b"second\n"


def test_directory_recovery_rejects_replaced_same_empty_parent(imports, monkeypatch):
    d = imports
    pending = _new_parent_change(d)
    review = d.imports.review_workspace_import(d.workspace.id, "chat", pending.id)
    command = str(uuid.uuid4())
    publish = d.edits.publish_text_revision
    monkeypatch.setattr(d.edits, "publish_text_revision", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("synthetic interruption")))
    assert d.apply(review, command_id=command).status == "partial"
    (d.root / "new").rename(d.root / "retained-original")
    (d.root / "new").mkdir()
    monkeypatch.setattr(d.edits, "publish_text_revision", publish)
    result = d.apply(review, command_id=command, recovery=d.import_receipts[-1])
    assert result.status == "partial" and not result.imported
    assert list((d.root / "new").iterdir()) == []
    assert (d.root / "retained-original/nested").is_dir()


def test_directory_publication_rechecks_authority_after_durable_proof(imports):
    d = imports
    pending = _new_parent_change(d)
    review = d.imports.review_workspace_import(d.workspace.id, "chat", pending.id)
    revoked = False
    def persist(proof):
        nonlocal revoked
        d.import_receipts.append(copy.deepcopy(proof))
        revoked = True
    def validate():
        if revoked:
            raise ValueError("capability_revoked")
    result = d.imports.import_workspace_change(review, command_id=str(uuid.uuid4()), confirmed=True,
        persist_recovery=persist, validate=validate)
    assert result.status == "partial" and result.code == "capability_revoked"
    assert not (d.root / "new").exists()


@pytest.mark.parametrize("attributes", ["text eol=lf", "text eol=crlf"])
def test_non_repository_staging_preserves_captured_git_attributes(tmp_path, attributes):
    stage = tmp_path / "stage"
    stage.mkdir()
    target = stage / "file.txt"
    target.write_bytes(b"before\r\n")
    attr = tmp_path / "captured.attributes"
    attr.write_text(f"file.txt {attributes}\n", encoding="utf-8")
    patch = b"--- a/file.txt\n+++ b/file.txt\n@@ -1 +1 @@\n-before\n+after\n"
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_COUNT="2",
        GIT_CEILING_DIRECTORIES=str(tmp_path),
        GIT_CONFIG_KEY_0="core.autocrlf", GIT_CONFIG_VALUE_0="true",
        GIT_CONFIG_KEY_1="core.attributesFile", GIT_CONFIG_VALUE_1=str(attr))
    result = subprocess.run(["git", "apply", "--whitespace=nowarn", "-"], cwd=stage, input=patch,
        capture_output=True, timeout=15, env=env)
    assert result.returncode == 0, result.stderr
    assert target.read_bytes() == (b"after\n" if attributes.endswith("=lf") else b"after\r\n")
    assert not (stage / ".git").exists()


@pytest.fixture
def imports(domain, monkeypatch, tmp_path):
    from row_bot.developer import client_imports
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.autocrlf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "false")
    domain.imports = client_imports
    domain.import_receipts = []
    def pending(before, after):
        for path, text in before.items():
            target = domain.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            if text is not None:
                target.write_bytes(text.encode())
        return domain.sandbox._record_pending_change(domain.workspace, "chat", "synthetic sandbox edit", before, after,
            command_id=str(uuid.uuid4()))
    domain.pending = pending
    def apply(review, **kwargs):
        return client_imports.import_workspace_change(review, command_id=kwargs.pop("command_id", str(uuid.uuid4())),
            confirmed=kwargs.pop("confirmed", True), persist_recovery=lambda value: domain.import_receipts.append(copy.deepcopy(value)), **kwargs)
    domain.apply = apply
    return domain


def test_passive_pending_list_and_paged_patch_do_not_run_git_or_write_host(imports, monkeypatch):
    d = imports
    pending = d.pending({"file.txt": "before\n"}, {"file.txt": "after\n"})
    monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_kw: pytest.fail("Passive query started a process"))
    before = (d.root / "file.txt").read_bytes()
    page = d.imports.list_workspace_imports(d.workspace.id, "chat")
    assert page.total == 1 and page.items[0].pending_change_id == pending.id
    first = d.imports.read_workspace_import_patch(d.workspace.id, "chat", pending.id, expected_revision=page.items[0].revision, limit=8)
    assert first.next_offset == 8 and first.text == pending.patch[:8]
    assert (d.root / "file.txt").read_bytes() == before


@pytest.mark.parametrize("before,after", [("old\n", "new\n"), (None, "new\n"), ("old\n", None)])
def test_reviewed_create_update_delete_retains_originals_and_canonical_history(imports, before, after):
    d = imports
    pending = d.pending({"file.txt": before}, {"file.txt": after})
    review = d.imports.review_workspace_import(d.workspace.id, "chat", pending.id)
    assert review.policy_decision == "ask" and review.approval_required
    command_id = str(uuid.uuid4())
    result = d.apply(review, command_id=command_id)
    assert result.status == "imported", result
    assert result.imported and result.ledger_saved and result.files_applied == ("file.txt",)
    assert (d.root / "file.txt").read_bytes() == after.encode() if after is not None else not (d.root / "file.txt").exists()
    row = d.sandbox.read_pending_import_rows(d.workspace.id, "chat")[0]
    assert row["imported"] and row["import_command_id"] == command_id
    assert len(d.ledger.list_change_sets(workspace_id=d.workspace.id)) == 1
    assert d.runs.get_agent_write_lock("developer:" + d.workspace.id) is None
    if before is not None:
        retained = list(d.root.glob(".row-bot-edit-recovery/*/previous"))
        assert len(retained) == 1 and retained[0].read_bytes() == before.encode()


def test_unconfirmed_ask_and_stale_preimage_leave_host_unchanged(imports):
    d = imports
    pending = d.pending({"file.txt": "old\n"}, {"file.txt": "new\n"})
    review = d.imports.review_workspace_import(d.workspace.id, "chat", pending.id)
    denied = d.apply(review, confirmed=False)
    assert denied.code == "sandbox_import_approval_required"
    assert (d.root / "file.txt").read_bytes() == b"old\n" and not d.import_receipts
    (d.root / "file.txt").write_bytes(b"external edit\n")
    stale = d.apply(review)
    assert stale.code == "file_revision_conflict" and (d.root / "file.txt").read_bytes() == b"external edit\n"


def test_mark_failure_recovers_same_command_without_reapplying_host_patch(imports, monkeypatch):
    d = imports
    pending = d.pending({"file.txt": "old\n"}, {"file.txt": "new\n"})
    review = d.imports.review_workspace_import(d.workspace.id, "chat", pending.id)
    command_id = str(uuid.uuid4())
    original = d.sandbox.mark_pending_change_imported
    monkeypatch.setattr(d.sandbox, "mark_pending_change_imported", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("marker unavailable")))
    result = d.apply(review, command_id=command_id)
    assert result.status == "partial" and result.ledger_saved and not result.imported
    before = (d.root / "file.txt").stat()
    monkeypatch.setattr(d.sandbox, "mark_pending_change_imported", original)
    recovered = d.apply(review, command_id=command_id, recovery=d.import_receipts[-1])
    assert recovered.status == "imported", recovered
    assert (d.root / "file.txt").stat().st_ino == before.st_ino
    assert (d.root / "file.txt").stat().st_mtime_ns == before.st_mtime_ns
    assert len(d.ledger.list_change_sets(workspace_id=d.workspace.id)) == 1


def test_partial_multi_file_import_recovers_original_preimages_and_never_replays_host_git(imports, monkeypatch):
    d = imports
    pending = d.pending({"a.txt": "old\n", "b.txt": "delete me\n", "c.txt": None},
        {"a.txt": "updated\n", "b.txt": None, "c.txt": "created\n"})
    review = d.imports.review_workspace_import(d.workspace.id, "chat", pending.id)
    command_id = str(uuid.uuid4())
    remove = d.edits.publish_file_removal
    monkeypatch.setattr(d.edits, "publish_file_removal", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("synthetic interruption")))
    partial = d.apply(review, command_id=command_id)
    assert partial.status == "partial" and partial.files_applied == ("a.txt",)
    assert (d.root / "a.txt").read_bytes() == b"updated\n"
    assert (d.root / "b.txt").read_bytes() == b"delete me\n" and not (d.root / "c.txt").exists()
    original_identity = (d.root / "a.txt").stat().st_ino
    monkeypatch.setattr(d.edits, "publish_file_removal", remove)
    recovered = d.apply(review, command_id=command_id, recovery=d.import_receipts[-1])
    assert recovered.status == "imported", recovered
    assert recovered.files_applied == ("a.txt", "b.txt", "c.txt")
    assert (d.root / "a.txt").stat().st_ino == original_identity
    assert not (d.root / "b.txt").exists() and (d.root / "c.txt").read_bytes() == b"created\n"
    assert len(d.ledger.list_change_sets(workspace_id=d.workspace.id)) == 1


def test_import_receipt_failure_precedes_original_retirement(imports):
    d = imports
    pending = d.pending({"file.txt": "old\n"}, {"file.txt": "new\n"})
    review = d.imports.review_workspace_import(d.workspace.id, "chat", pending.id)
    def fail(_progress):
        raise OSError("synthetic private receipt failure")
    result = d.imports.import_workspace_change(review, command_id=str(uuid.uuid4()), confirmed=True, persist_recovery=fail)
    assert result.status == "partial" and not result.files_applied and not result.imported
    assert (d.root / "file.txt").read_bytes() == b"old\n"
    assert not list(d.root.glob(".row-bot-edit-recovery/*/previous"))


def test_original_import_receipt_recovery_refuses_external_new_bytes(imports, monkeypatch):
    d = imports
    pending = d.pending({"file.txt": "old\n"}, {"file.txt": "new\n"})
    review = d.imports.review_workspace_import(d.workspace.id, "chat", pending.id)
    command_id = str(uuid.uuid4())
    monkeypatch.setattr(d.sandbox, "mark_pending_change_imported", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("marker unavailable")))
    assert d.apply(review, command_id=command_id).status == "partial"
    (d.root / "file.txt").write_bytes(b"later external edit\n")
    result = d.apply(review, command_id=command_id, recovery=d.import_receipts[-1])
    assert result.status == "partial" and not result.imported
    assert (d.root / "file.txt").read_bytes() == b"later external edit\n"


@pytest.mark.parametrize("change", ["pending", "policy", "binding", "git"])
def test_review_revalidates_full_pending_binding_policy_and_git_before_any_host_write(imports, monkeypatch, change):
    import sqlite3
    d = imports
    pending = d.pending({"file.txt": "old\n"}, {"file.txt": "new\n"})
    review = d.imports.review_workspace_import(d.workspace.id, "chat", pending.id)
    if change == "pending":
        saved = d.sandbox._load_client_pending()
        saved["changes"][0]["retained_unknown_metadata"] = "changed"
        d.sandbox._save_pending_payload(saved)
    elif change == "policy":
        with sqlite3.connect(d.threads.DB_PATH) as conn:
            conn.execute("UPDATE thread_meta SET approval_mode='block' WHERE thread_id='chat'")
    elif change == "binding":
        d.resources.unbind("chat", review.binding_id, expected_revision=review.binding_revision)
    else:
        monkeypatch.setenv("GIT_CONFIG_VALUE_0", "true")
    result = d.apply(review)
    assert result.status in {"denied", "conflict"} and not result.files_applied
    assert (d.root / "file.txt").read_bytes() == b"old\n" and not d.import_receipts


def test_pending_query_complete_pages_and_changed_cursor(imports):
    d = imports
    for index in range(103):
        d.pending({f"{index}.txt": "old\n"}, {f"{index}.txt": "new\n"})
    first = d.imports.list_workspace_imports(d.workspace.id, "chat", limit=50)
    second = d.imports.list_workspace_imports(d.workspace.id, "chat", cursor=first.next_cursor, limit=50)
    third = d.imports.list_workspace_imports(d.workspace.id, "chat", cursor=second.next_cursor, limit=50)
    assert len(first.items) == len(second.items) == 50 and len(third.items) == 3 and third.next_cursor is None
    assert len({row.pending_change_id for row in (*first.items, *second.items, *third.items)}) == 103
    d.pending({"later.txt": "old\n"}, {"later.txt": "new\n"})
    with pytest.raises(ValueError, match="snapshot_revision_conflict"):
        d.imports.list_workspace_imports(d.workspace.id, "chat", cursor=first.next_cursor)


@pytest.mark.parametrize("eol", ["true", "false", "input"])
def test_strict_import_output_matches_retained_git_apply_eol(imports, monkeypatch, tmp_path, eol):
    d = imports
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", eol)
    pending = d.pending({"file.txt": "old\n"}, {"file.txt": "new\n"})
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    (canonical / "file.txt").write_bytes(b"old\n")
    original = d.original_run(["git", "apply", "--whitespace=nowarn", "-"], cwd=canonical,
        input=pending.patch.encode(), capture_output=True, timeout=15)
    assert original.returncode == 0, original.stderr
    review = d.imports.review_workspace_import(d.workspace.id, "chat", pending.id)
    result = d.apply(review)
    assert result.status == "imported", result
    assert (d.root / "file.txt").read_bytes() == (canonical / "file.txt").read_bytes()


def test_marked_import_with_missing_ledger_does_not_claim_complete_history(imports):
    d = imports
    pending = d.pending({"file.txt": "old\n"}, {"file.txt": "new\n"})
    review = d.imports.review_workspace_import(d.workspace.id, "chat", pending.id)
    command_id = str(uuid.uuid4())
    assert d.apply(review, command_id=command_id).status == "imported"
    d.ledger.LEDGER_PATH.write_text('{"change_sets": []}', encoding="utf-8")
    result = d.apply(review, command_id=command_id, recovery=d.import_receipts[-1])
    assert result.status == "partial" and result.imported and not result.ledger_saved
    assert result.code == "change_ledger_unavailable"


@pytest.mark.parametrize("bound", [0, -1, True, 256 * 1024 + 1, "8192", None])
def test_git_metadata_invalid_bounds_never_spawn(monkeypatch, tmp_path, bound):
    from row_bot.developer.review import _git_read_metadata
    monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_kw: pytest.fail("Invalid bound spawned Git"))
    with pytest.raises(ValueError, match="git_metadata_unavailable"):
        _git_read_metadata(str(tmp_path), ["config", "--list"], max_bytes=bound)


@pytest.mark.parametrize("bound,length,allowed", [(None, 8192, True), (None, 8193, False),
    (256 * 1024, 256 * 1024, True), (256 * 1024, 256 * 1024 + 1, False)])
def test_git_metadata_default_and_explicit_hard_ceiling(monkeypatch, tmp_path, bound, length, allowed):
    import io
    from row_bot.developer.review import _git_read_metadata
    class Process:
        returncode = 0
        killed = False
        stdout = io.BytesIO(b"x" * length)
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def kill(self):
            self.killed = True
        def poll(self):
            return 0
        def wait(self, **_kwargs):
            return 0
    process = Process()
    monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_kw: process)
    kwargs = {} if bound is None else {"max_bytes": bound}
    if allowed:
        assert _git_read_metadata(str(tmp_path), ["config", "--list"], **kwargs) == (0, b"x" * length)
        assert not process.killed
    else:
        with pytest.raises(ValueError, match="git_metadata_unavailable"):
            _git_read_metadata(str(tmp_path), ["config", "--list"], **kwargs)
        assert process.killed


def test_review_batches_all_attributes_without_partial_or_duplicate_results(monkeypatch, tmp_path):
    from row_bot.developer import edits, review
    names = ("text", "eol", "working-tree-encoding", "filter", "ident")
    paths = [f"file-{index}.txt" for index in range(100)]
    calls = []
    duplicate = False
    def metadata(_root, arguments, **kwargs):
        calls.append((arguments, kwargs))
        if arguments[0] == "config":
            return 1, b""
        if arguments[0] == "rev-parse":
            return 0, b"true\n"
        selected = paths[:-1] + [paths[0]] if duplicate else paths
        return 0, b"".join((f"{path}\0{name}\0unspecified\0").encode() for path in selected for name in names)
    monkeypatch.setattr(review, "_git_read_metadata", metadata)
    result = edits.capture_patch_git_policy(tmp_path, paths)
    assert list(result["attributes"]) == paths and len(calls) == 5
    assert calls[-1][1] == {"max_bytes": 256 * 1024}
    duplicate = True
    with pytest.raises(ValueError, match="sandbox_git_policy_unavailable"):
        edits.capture_patch_git_policy(tmp_path, paths)


def test_authority_revoked_during_writer_admission_prevents_all_file_effects(imports, monkeypatch):
    d = imports
    pending = d.pending({"file.txt": "old\n"}, {"file.txt": "new\n"})
    review = d.imports.review_workspace_import(d.workspace.id, "chat", pending.id)
    revoked = False
    original = d.runs.acquire_agent_write_lock
    def acquire(*args, **kwargs):
        nonlocal revoked
        result = original(*args, **kwargs)
        revoked = True
        return result
    monkeypatch.setattr(d.runs, "acquire_agent_write_lock", acquire)
    def validate():
        if revoked:
            raise ValueError("capability_revoked")
    result = d.apply(review, validate=validate)
    assert result.code == "capability_revoked" and not result.files_applied
    assert (d.root / "file.txt").read_bytes() == b"old\n" and not d.import_receipts
    assert d.runs.get_agent_write_lock("developer:" + d.workspace.id) is None


def test_run_registration_event_failure_does_not_strand_running_owner(imports, monkeypatch):
    d = imports
    pending = d.pending({"file.txt": "old\n"}, {"file.txt": "new\n"})
    review = d.imports.review_workspace_import(d.workspace.id, "chat", pending.id)
    original = d.runs.create_agent_run
    def create(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("synthetic after-commit event failure")
    monkeypatch.setattr(d.runs, "create_agent_run", create)
    command_id = str(uuid.uuid4())
    result = d.apply(review, command_id=command_id)
    run_id = uuid.uuid5(uuid.NAMESPACE_URL, "row-bot:workspace-import:" + command_id).hex
    assert result.status == "denied" and d.runs.get_agent_run(run_id)["status"] == "failed"
    assert (d.root / "file.txt").read_bytes() == b"old\n" and not d.import_receipts


@pytest.mark.parametrize("header", ["old mode 100644\nnew mode 100755\n", "GIT binary patch\n",
    "rename from file.txt\nrename to renamed.txt\n", "new file mode 120000\n"])
def test_unsupported_import_formats_preserve_pending_and_original(imports, header):
    d = imports
    pending = d.pending({"file.txt": "old\n"}, {"file.txt": "new\n"})
    saved = d.sandbox._load_client_pending()
    saved["changes"][0]["patch"] = header + pending.patch
    d.sandbox._save_pending_payload(saved)
    with pytest.raises(ValueError, match="sandbox_import_format_unavailable"):
        d.imports.review_workspace_import(d.workspace.id, "chat", pending.id)
    assert (d.root / "file.txt").read_bytes() == b"old\n" and not d.import_receipts


def test_maximum_file_count_receipt_fits_existing_admission_envelope(imports):
    import json
    from dataclasses import asdict
    d = imports
    paths = [f"file-{index:03}-" + "x" * 60 + ".txt" for index in range(100)]
    pending = d.pending(dict.fromkeys(paths, "old\n"), dict.fromkeys(paths, "new\n"))
    review = d.imports.review_workspace_import(d.workspace.id, "chat", pending.id)
    result = d.apply(review)
    assert result.status == "imported", result
    # Include separate original public review/result, a 16 KiB admission envelope,
    # and private recovery: the real read owner rejects receipts above 256 KiB.
    envelope = {"review": asdict(review), "result": asdict(result), "_import": d.import_receipts[-1],
        "envelope_reserve": "x" * (16 * 1024)}
    encoded = json.dumps(envelope, ensure_ascii=True).encode()
    assert len(encoded) < 256 * 1024
    assert len(json.dumps(d.import_receipts[-1], ensure_ascii=True).encode()) <= d.imports.RECOVERY_BYTES
    assert len(d.import_receipts[-1]["patch"]["files"]) == 100


def test_long_reviewed_names_fail_explicitly_before_publication(imports):
    d = imports
    paths = [f"file-{index:03}-" + "x" * 180 + ".txt" for index in range(50)]
    pending = d.pending(dict.fromkeys(paths, "old\n"), dict.fromkeys(paths, "new\n"))
    with pytest.raises(ValueError, match="sandbox_import_too_large"):
        d.imports.review_workspace_import(d.workspace.id, "chat", pending.id)
    assert not d.import_receipts
    assert all((d.root / path).read_bytes() == b"old\n" for path in paths)


@pytest.mark.parametrize("action", ["update", "delete"])
@pytest.mark.parametrize("race", ["identity", "metadata"])
def test_same_byte_replacement_after_capture_does_not_adopt_new_identity_or_metadata(imports, monkeypatch, action, race):
    import stat
    d = imports
    pending = d.pending({"file.txt": "old\n"}, {"file.txt": "new\n" if action == "update" else None})
    review = d.imports.review_workspace_import(d.workspace.id, "chat", pending.id)
    name = "publish_text_revision" if action == "update" else "publish_file_removal"
    original = getattr(d.edits, name)
    target = d.root / "file.txt"
    def publish(*args, **kwargs):
        if race == "identity":
            target.rename(d.root / "external-retained.txt")
            target.write_bytes(b"old\n")
        else:
            target.chmod(stat.S_IREAD)
        return original(*args, **kwargs)
    monkeypatch.setattr(d.edits, name, publish)
    try:
        result = d.apply(review)
        assert result.code == "file_revision_conflict", result
        assert target.read_bytes() == b"old\n" and not result.files_applied
        assert not d.import_receipts and not (d.root / ".row-bot-edit-recovery").exists()
    finally:
        if race == "metadata":
            target.chmod(stat.S_IWRITE | stat.S_IREAD)


@pytest.mark.parametrize("action", ["create", "update", "delete"])
def test_parent_replacement_after_capture_cannot_redirect_import(imports, monkeypatch, action):
    d = imports
    pending = d.pending({"parent/file.txt": None if action == "create" else "old\n"},
        {"parent/file.txt": None if action == "delete" else "new\n"})
    review = d.imports.review_workspace_import(d.workspace.id, "chat", pending.id)
    name = "publish_file_removal" if action == "delete" else "publish_text_revision"
    original = getattr(d.edits, name)
    parent = d.root / "parent"
    def publish(*args, **kwargs):
        parent.rename(d.root / "original-parent")
        parent.mkdir()
        if action != "create":
            (parent / "file.txt").write_bytes(b"old\n")
        return original(*args, **kwargs)
    monkeypatch.setattr(d.edits, name, publish)
    result = d.apply(review)
    assert result.status == "denied" and not result.files_applied
    assert not d.import_receipts and not (parent / ".row-bot-edit-recovery").exists()
    if action == "create":
        assert not list(parent.iterdir())
    else:
        assert (parent / "file.txt").read_bytes() == b"old\n"
        assert (d.root / "original-parent/file.txt").read_bytes() == b"old\n"


def test_traversal_patch_rejection_does_not_expose_renderer_path_or_host_path(imports):
    d = imports
    pending = d.pending({"file.txt": "old\n"}, {"file.txt": "new\n"})
    saved = d.sandbox._load_client_pending()
    saved["changes"][0]["patch"] = pending.patch.replace("file.txt", "../private-synthetic-name.txt")
    d.sandbox._save_pending_payload(saved)
    with pytest.raises(ValueError, match="^workspace_path_denied$"):
        d.imports.review_workspace_import(d.workspace.id, "chat", pending.id)


def test_git_metadata_failure_does_not_expose_subprocess_or_local_diagnostics(imports, monkeypatch):
    d = imports
    pending = d.pending({"file.txt": "old\n"}, {"file.txt": "new\n"})
    def fail(*_args):
        raise OSError("synthetic private-root and subprocess diagnostic")
    monkeypatch.setattr(d.edits, "capture_patch_git_policy", fail)
    with pytest.raises(ValueError, match="^sandbox_git_policy_unavailable$"):
        d.imports.review_workspace_import(d.workspace.id, "chat", pending.id)


def test_partial_import_recovers_original_owned_lease_without_reacquiring_or_republishing(imports, monkeypatch):
    d = imports
    pending = d.pending({"file.txt": "old\n"}, {"file.txt": "new\n"})
    review = d.imports.review_workspace_import(d.workspace.id, "chat", pending.id)
    command_id = str(uuid.uuid4())
    marker, release = d.sandbox.mark_pending_change_imported, d.runs.release_agent_write_lock
    def fail(*_args, **_kwargs):
        raise OSError("synthetic publication/cleanup failure")
    monkeypatch.setattr(d.sandbox, "mark_pending_change_imported", fail)
    monkeypatch.setattr(d.runs, "release_agent_write_lock", fail)
    first = d.apply(review, command_id=command_id)
    assert first.status == "partial" and first.ledger_saved and not first.imported
    assert d.runs.get_agent_write_lock("developer:" + d.workspace.id)
    identity = (d.root / "file.txt").stat().st_ino
    monkeypatch.setattr(d.sandbox, "mark_pending_change_imported", marker)
    monkeypatch.setattr(d.runs, "release_agent_write_lock", release)
    recovered = d.apply(review, command_id=command_id, recovery=d.import_receipts[-1])
    assert recovered.status == "imported", recovered
    assert (d.root / "file.txt").stat().st_ino == identity
    assert not d.runs.get_agent_write_lock("developer:" + d.workspace.id)
    assert len(d.ledger.list_change_sets(workspace_id=d.workspace.id)) == 1
