from __future__ import annotations

import pathlib
import re
import subprocess
from dataclasses import dataclass
from collections.abc import Callable
import hashlib
import os
import stat
import sys
import uuid

from row_bot.developer import change_ledger
from row_bot.developer.change_ledger import ChangeSet, FileChange
from row_bot.developer.sandbox import ApprovalDecision, decide_action
from row_bot.developer.state import ApprovalMode
from row_bot.developer.storage import get_workspace


_DIFF_PATH_RE = re.compile(r"^(?:diff --git a/(.+?) b/(.+)|--- (?:a/)?(.+)|\+\+\+ (?:b/)?(.+))$")


def _patch_lines(patch: str):
    """Yield exact lines with counted header/body classification."""
    before = after = 0
    lines = patch.split("\n")
    for index, body in enumerate(lines):
        line = body + ("\n" if index < len(lines) - 1 else "")
        if line.startswith("\\ No newline at end of file"):
            yield line, False
            continue
        if before or after:
            if line.startswith(" "):
                before, after = before - 1, after - 1
            elif line.startswith("-"):
                before -= 1
            elif line.startswith("+"):
                after -= 1
            else:
                raise ValueError("sandbox_patch_conflict")
            if before < 0 or after < 0:
                raise ValueError("sandbox_patch_conflict")
            yield line, False
            continue
        hunk = re.match(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@", line)
        if hunk:
            before, after = (int(value) if value is not None else 1 for value in hunk.groups())
        yield line, hunk is None
    if before or after:
        raise ValueError("sandbox_patch_conflict")


def ordinary_edit_decision(approval_mode: ApprovalMode) -> ApprovalDecision:
    """Apply the local policy for a contained ordinary workspace edit."""

    decision = decide_action(approval_mode, "edit")
    if decision.requires_approval:
        return ApprovalDecision(
            "allow",
            "Ask allows ordinary contained workspace edits without an approval card.",
        )
    return decision


def _workspace_root(workspace_id: str) -> pathlib.Path:
    workspace = get_workspace(workspace_id)
    if workspace is None:
        raise ValueError("No active Developer workspace.")
    root = pathlib.Path(workspace.path).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Workspace folder does not exist: {root}")
    return root


def _validate_relative_path(root: pathlib.Path, rel_path: str, *, strip_git_prefix: bool = True) -> pathlib.Path:
    clean = (rel_path or "").strip().replace("\\", "/")
    if not clean or clean == "/dev/null":
        raise ValueError("Empty patch path.")
    if strip_git_prefix and clean.startswith(("a/", "b/")):
        clean = clean[2:]
    if clean.startswith("/") or clean.startswith("../") or "/../" in clean:
        raise ValueError(f"Patch path escapes workspace: {rel_path}")
    target = (root / clean).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Patch path escapes workspace: {rel_path}") from exc
    return target


def paths_from_patch(root: pathlib.Path, patch: str) -> list[str]:
    paths: list[str] = []
    for line, is_header in _patch_lines(patch or ""):
        if not is_header:
            continue
        line = line.removesuffix("\n")
        match = _DIFF_PATH_RE.match(line)
        if not match:
            continue
        groups = match.groups()
        if line.startswith("diff --git a/"):
            repeated = line[len("diff --git a/"):]
            width = (len(repeated) - 3) // 2
            # Canonical create/update/delete headers repeat the same path.
            # Prefer that exact split over a ' b/' inside a directory name.
            if width > 0 and repeated[width:width + 3] == " b/" and repeated[:width] == repeated[width + 3:]:
                groups = (repeated[:width], repeated[:width])
        for group in groups:
            if not group or group == "/dev/null":
                continue
            clean = group.strip()
            if "\t" in clean:
                clean = clean.split("\t", 1)[0]
            if clean not in paths:
                # The header parser has consumed the transport prefix already;
                # a real first directory named a or b belongs to the file.
                _validate_relative_path(root, clean, strip_git_prefix=False)
                paths.append(clean)
    if not paths:
        raise ValueError("Patch did not include any workspace file paths.")
    return paths


def preview_patch(workspace_id: str, patch: str) -> str:
    root = _workspace_root(workspace_id)
    paths = paths_from_patch(root, patch)
    return "Patch looks structurally valid for:\n" + "\n".join(f"- {path}" for path in paths)


def apply_patch_to_workspace(
    *,
    workspace_id: str,
    thread_id: str,
    patch: str,
    approval_mode: ApprovalMode,
    summary: str = "",
    confirmed: bool = False,
    prepared_import: dict | None = None,
    command_id: str | None = None,
    persist_recovery: Callable | None = None,
    recovery: dict | None = None,
    validate: Callable[[], None] | None = None,
) -> tuple[ChangeSet | None, ApprovalDecision]:
    if prepared_import is not None:
        return _apply_reviewed_patch(workspace_id=workspace_id, thread_id=thread_id, patch=patch,
            approval_mode=approval_mode, summary=summary, confirmed=confirmed, prepared=prepared_import,
            command_id=command_id, persist_recovery=persist_recovery, recovery=recovery, validate=validate)
    decision = ordinary_edit_decision(approval_mode)
    if decision.decision == "block":
        return None, decision
    if decision.requires_approval and not confirmed:
        return None, decision

    root = _workspace_root(workspace_id)
    paths = paths_from_patch(root, patch)
    before: dict[str, str | None] = {}
    for path in paths:
        target = _validate_relative_path(root, path, strip_git_prefix=False)
        before[path] = target.read_text(encoding="utf-8", errors="replace") if target.exists() else None

    check = subprocess.run(
        ["git", "apply", "--check", "--whitespace=nowarn", "-"],
        cwd=str(root),
        input=patch.encode("utf-8"),
        capture_output=True,
        timeout=30,
    )
    if check.returncode != 0:
        diagnostic = check.stderr or check.stdout or "Patch did not apply."
        raise ValueError((diagnostic.decode("utf-8", errors="replace") if isinstance(diagnostic, bytes) else diagnostic).strip())

    applied = subprocess.run(
        ["git", "apply", "--whitespace=nowarn", "-"],
        cwd=str(root),
        input=patch.encode("utf-8"),
        capture_output=True,
        timeout=30,
    )
    if applied.returncode != 0:
        diagnostic = applied.stderr or applied.stdout or "Patch failed while applying."
        raise ValueError((diagnostic.decode("utf-8", errors="replace") if isinstance(diagnostic, bytes) else diagnostic).strip())

    files: list[FileChange] = []
    for path in paths:
        target = _validate_relative_path(root, path, strip_git_prefix=False)
        after = target.read_text(encoding="utf-8", errors="replace") if target.exists() else None
        before_text = before[path]
        if before_text is None and after is not None:
            action = "create"
        elif before_text is not None and after is None:
            action = "delete"
        else:
            action = "update"
        files.append(
            FileChange(
                path=path,
                action=action,
                before_hash=change_ledger.text_hash(before_text),
                after_hash=change_ledger.text_hash(after),
                before_text=before_text,
                patch=patch,
            )
        )
    change_set = change_ledger.record_change_set(
        workspace_id=workspace_id,
        thread_id=thread_id,
        summary=summary or "Developer patch",
        files=files,
    )
    return change_set, decision


def capture_patch_git_configuration(root: pathlib.Path) -> dict[str, str]:
    """Read the closed conversion configuration without evaluating attributes."""
    from row_bot.developer.review import _git_read_metadata
    values = {}
    allowed = {"core.autocrlf": {"true", "false", "input"}, "core.eol": {"lf", "crlf", "native"},
               "core.safecrlf": {"true", "false", "warn"}}
    for name, choices in allowed.items():
        code, output = _git_read_metadata(str(root), ["config", "--get", name])
        if code not in {0, 1}:
            raise ValueError("sandbox_git_policy_unavailable")
        value = output.decode("utf-8").strip().lower() if code == 0 else ""
        if value and value not in choices:
            raise ValueError("sandbox_git_policy_unavailable")
        values[name] = value
    return values


def capture_patch_git_policy(root: pathlib.Path, paths: list[str]) -> dict:
    """Capture only Git inputs that a bounded ordinary import can preserve."""
    from row_bot.developer.review import _git_read_metadata
    values = capture_patch_git_configuration(root)
    code, inside = _git_read_metadata(str(root), ["rev-parse", "--is-inside-work-tree"])
    if code not in {0, 128}:
        raise ValueError("sandbox_git_policy_unavailable")
    names = ("text", "eol", "working-tree-encoding", "filter", "ident")
    if not paths or len(paths) > 100 or len(set(paths)) != len(paths):
        raise ValueError("sandbox_import_format_unavailable")
    attributes = {path: dict.fromkeys(names, "unspecified") for path in paths}
    for path in paths:
        if any(part.casefold() == ".git" for part in path.split("/")):
            raise ValueError("sandbox_import_format_unavailable")
    if code == 0 and inside.strip() == b"true":
        arguments = ["check-attr", "-z", *names, "--", *paths]
        # Windows command lines are bounded in UTF-16 code units. Fail clearly
        # before spawning, including quoting and the executable/root overhead.
        command = subprocess.list2cmdline(["git", "-C", str(root), *arguments])
        if len(command.encode("utf-16-le")) // 2 > 30000:
            raise ValueError("sandbox_import_too_large")
        status, raw = _git_read_metadata(str(root), arguments, max_bytes=256 * 1024)
        if status or not raw.endswith(b"\0"):
            raise ValueError("sandbox_git_policy_unavailable")
        parts = raw.decode("utf-8").split("\0")[:-1]
        if len(parts) != len(paths) * len(names) * 3:
            raise ValueError("sandbox_git_policy_unavailable")
        seen = set()
        for offset in range(0, len(parts), 3):
            found_path, name, value = parts[offset:offset + 3]
            if found_path not in attributes or name not in names or (found_path, name) in seen:
                raise ValueError("sandbox_git_policy_unavailable")
            seen.add((found_path, name))
            attributes[found_path][name] = value
    for row in attributes.values():
        if (row["filter"] not in {"unset", "unspecified"} or row["working-tree-encoding"] not in {"unset", "unspecified"}
                or row["ident"] not in {"set", "unset", "unspecified"}
                or row["text"] not in {"set", "unset", "unspecified", "auto"}
                or row["eol"] not in {"lf", "crlf", "unset", "unspecified"}):
            raise ValueError("sandbox_import_format_unavailable")
    return {"config": values, "attributes": attributes}


def _attribute_path(path: str) -> str:
    encoded = path.encode("utf-8")
    return '"' + "".join(chr(byte) if 32 <= byte < 127 and byte not in {34, 92} else f"\\{byte:03o}" for byte in encoded) + '"'


def _staged_patch_headers(patch: str, names: dict[str, str]) -> str:
    """Remap only parsed headers; hunk bodies are byte-for-byte unchanged."""
    replacements = {}
    for source, target in names.items():
        replacements[f"diff --git a/{source} b/{source}"] = f"diff --git a/{target} b/{target}"
        for prefix, side in (("---", "a"), ("+++", "b")):
            replacements[f"{prefix} {side}/{source}"] = f"{prefix} {side}/{target}"
            replacements[f"{prefix} {side}/{source}\t"] = f"{prefix} {side}/{target}\t"
    output = []
    for line, is_header in _patch_lines(patch):
        if not is_header:
            output.append(line)
            continue
        ending = "\n" if line.endswith("\n") else ""
        header = line[:-1] if ending else line
        output.append(replacements.get(header, header) + ending)
    return "".join(output)


def prepare_reviewed_patch(patch: str, captured: dict, git_policy: dict,
                           *, validate: Callable[[], None]) -> dict[str, bytes | None]:
    """Use Git's parser/EOL conversion in a disposable non-repository tree."""
    import tempfile
    import threading
    if not captured or len(captured) > 100 or len(patch.encode("utf-8")) > 1024 * 1024:
        raise ValueError("sandbox_import_too_large")
    from row_bot.developer.client_workspace import _empty_folder_name
    total = 0
    for path, record in captured.items():
        if type(path) is not str or len(path) > 4096 or "\\" in path:
            raise ValueError("sandbox_import_format_unavailable")
        for part in path.split("/"):
            _empty_folder_name(part)
            if part.casefold() in {".git", ".row-bot-edit-recovery"}:
                raise ValueError("sandbox_import_format_unavailable")
        data = record["data"]
        if data is not None:
            if type(data) is not bytes or len(data) > 1024 * 1024:
                raise ValueError("sandbox_import_too_large")
            total += len(data)
    if total > 8 * 1024 * 1024:
        raise ValueError("sandbox_import_too_large")
    with tempfile.TemporaryDirectory(prefix="row-bot-reviewed-patch-") as temporary:
        private = pathlib.Path(temporary)
        stage = private / "files"
        stage.mkdir()
        # Never load authored .gitattributes during candidate conversion. Each
        # logical file gets an inert flat name and its exact captured policy.
        names = {path: hashlib.sha256(path.encode()).hexdigest() + ".candidate" for path in captured}
        staged_patch = _staged_patch_headers(patch, names)
        for path, record in captured.items():
            # All names/preimages were admitted by the same guarded file owner.
            target = stage / names[path]
            target.parent.mkdir(parents=True, exist_ok=True)
            if record["data"] is not None:
                target.write_bytes(record["data"])
        attributes = []
        for path, row in git_policy["attributes"].items():
            terms = []
            for name, value in row.items():
                terms.append(name if value == "set" else "-" + name if value == "unset" else "!" + name if value == "unspecified" else name + "=" + value)
            attributes.append(_attribute_path(names[path]) + " " + " ".join(terms))
        attribute_file = private / "attributes"
        attribute_file.write_text("\n".join(attributes) + "\n", encoding="utf-8")
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", GIT_CEILING_DIRECTORIES=str(private))
        settings = [(name, value) for name, value in git_policy["config"].items() if value]
        settings.extend([("core.attributesFile", str(attribute_file)), ("core.hooksPath", str(private / "no-hooks"))])
        env["GIT_CONFIG_COUNT"] = str(len(settings))
        for index, (name, value) in enumerate(settings):
            env[f"GIT_CONFIG_KEY_{index}"], env[f"GIT_CONFIG_VALUE_{index}"] = name, value
        validate()
        with subprocess.Popen(["git", "apply", "--whitespace=nowarn", "-"], cwd=stage, env=env,
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as process:
            timer = threading.Timer(15, process.kill)
            timer.daemon = True
            timer.start()
            try:
                process.communicate(staged_patch.encode("utf-8"), timeout=16)
                if process.returncode:
                    raise ValueError("sandbox_patch_conflict")
            finally:
                timer.cancel()
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=2)
        validate()
        outputs, size = {}, 0
        discovered = set()
        for target in stage.rglob("*"):
            if target.is_symlink():
                raise ValueError("sandbox_import_format_unavailable")
            if target.is_file():
                discovered.add(target.relative_to(stage).as_posix())
        if discovered - set(names.values()):
            raise ValueError("sandbox_import_format_unavailable")
        for path in captured:
            target = stage / names[path]
            if not target.exists():
                outputs[path] = None
                continue
            if not target.is_file() or target.stat().st_size > 1024 * 1024:
                raise ValueError("sandbox_import_too_large")
            data = target.read_bytes()
            size += len(data)
            if size > 8 * 1024 * 1024:
                raise ValueError("sandbox_import_too_large")
            outputs[path] = data
        validate()
        return outputs


def read_retained_edit_before(root: pathlib.Path, relative_path: str, recovery: FileEditRecovery) -> bytes | None:
    """Read a proven original from the existing private edit recovery generation."""
    from row_bot.developer.client_workspace import _empty_parent_guard, _directory_identity
    from row_bot.developer.review import scoped_workspace_path
    target = _edit_path(root, relative_path)
    if (str(uuid.UUID(recovery.command_id)) != recovery.command_id or recovery.relative_path != relative_path
            or recovery.root_identity != _edit_identity(root.stat())):
        raise FileEditError("edit_recovery_conflict")
    if recovery.before_digest == "missing":
        return None
    with _empty_parent_guard(target.parent, _directory_identity(target.parent, parent=True)) as parent:
        if parent is None:
            directory = target.parent / ".row-bot-edit-recovery" / recovery.command_id
            scoped_workspace_path(root, directory.relative_to(root).as_posix())
            if os.path.lexists(directory / "previous"):
                data, digest, identity, _ = read_edit_bytes(directory, "previous")
                metadata = file_edit_metadata_digest(directory / "previous")
            else:
                data, digest, identity, _ = read_edit_bytes(root, relative_path)
                metadata = file_edit_metadata_digest(target)
        else:
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            retained = os.open(".row-bot-edit-recovery", flags, dir_fd=parent)
            try:
                directory = os.open(recovery.command_id, flags, dir_fd=retained)
                try:
                    data, digest, identity, _ = _read_edit_at(directory, "previous")
                    if data is None:
                        data, digest, identity, _ = _read_edit_at(parent, target.name)
                        metadata = _edit_metadata_at(parent, target.name)
                    else:
                        metadata = _edit_metadata_at(directory, "previous")
                finally:
                    os.close(directory)
            finally:
                os.close(retained)
        if digest != recovery.before_digest or identity != recovery.original_identity or metadata != recovery.metadata_digest:
            raise FileEditError("edit_recovery_conflict")
        return data


def publish_file_removal(root: pathlib.Path, relative_path: str, *, expected_digest: str, command_id: str,
                         persist_recovery: Callable[[FileEditRecovery], None], recovery: FileEditRecovery | None = None,
                         validate: Callable[[], None], expected_identity: str | None = None,
                         expected_metadata: str | None = None, expected_parent_identity: str | None = None,
                         _import_bytes: bool = False) -> FileEditPublication:
    """Retire an exact reviewed file without deleting its original bytes."""
    from row_bot.developer.client_workspace import _empty_parent_guard, _directory_identity
    from row_bot.developer.review import scoped_workspace_path
    if str(uuid.UUID(command_id)) != command_id:
        raise FileEditError("invalid_edit")
    target = _edit_path(root, relative_path)
    root_identity = _edit_identity(root.stat())
    validate()
    def validate_original(before, digest, identity, metadata):
        if recovery is not None:
            return
        if (digest != expected_digest or before is None
                or expected_identity is not None and identity != expected_identity
                or expected_metadata is not None and metadata != expected_metadata):
            raise FileEditError("file_revision_conflict")
        try:
            if _import_bytes:
                return
            before.decode("utf-8")
            if b"\0" in before:
                raise UnicodeError
        except UnicodeError:
            raise FileEditError("file_not_text") from None
    with _empty_parent_guard(target.parent, expected_parent_identity or _directory_identity(target.parent, parent=True)) as parent:
        descriptors = []
        directory = None
        try:
            if parent is None:
                before, digest, identity, _ = read_edit_bytes(root, relative_path)
                metadata = file_edit_metadata_digest(target) if before is not None else ""
                validate_original(before, digest, identity, metadata)
                directory = target.parent / ".row-bot-edit-recovery" / command_id
                if recovery is None:
                    if digest != expected_digest or before is None:
                        raise FileEditError("file_revision_conflict")
                    directory.parent.mkdir(exist_ok=True)
                    scoped_workspace_path(root, directory.parent.relative_to(root).as_posix())
                    directory.mkdir(exist_ok=False)
                scoped_workspace_path(root, directory.relative_to(root).as_posix())
                def exists():
                    return os.path.lexists(directory / "previous")
                def read_previous():
                    return read_edit_bytes(directory, "previous")
                def previous_metadata():
                    return file_edit_metadata_digest(directory / "previous")
                def retire():
                    return _rename_edit_no_replace(target, directory / "previous")
                def restore():
                    return _rename_edit_no_replace(directory / "previous", target)
                def current():
                    return read_edit_bytes(root, relative_path)
            else:
                before, digest, identity, _ = _read_edit_at(parent, target.name)
                metadata = _edit_metadata_at(parent, target.name) if before is not None else ""
                validate_original(before, digest, identity, metadata)
                if recovery is None:
                    if digest != expected_digest or before is None:
                        raise FileEditError("file_revision_conflict")
                    try:
                        os.mkdir(".row-bot-edit-recovery", mode=0o700, dir_fd=parent)
                    except FileExistsError:
                        pass
                flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                retained = os.open(".row-bot-edit-recovery", flags, dir_fd=parent)
                descriptors.append(retained)
                if recovery is None:
                    os.mkdir(command_id, mode=0o700, dir_fd=retained)
                directory = os.open(command_id, flags, dir_fd=retained)
                descriptors.append(directory)
                def exists():
                    return _read_edit_at(directory, "previous")[0] is not None
                def read_previous():
                    return _read_edit_at(directory, "previous")
                def previous_metadata():
                    return _edit_metadata_at(directory, "previous")
                def retire():
                    return _rename_edit_no_replace(target.name, "previous", src_dir_fd=parent, dst_dir_fd=directory)
                def restore():
                    return _rename_edit_no_replace("previous", target.name, src_dir_fd=directory, dst_dir_fd=parent)
                def current():
                    return _read_edit_at(parent, target.name)
            if recovery is None:
                recovery = FileEditRecovery(command_id, relative_path, root_identity, digest, "missing", identity, "", metadata)
                persist_recovery(recovery)
            elif (recovery.command_id != command_id or recovery.relative_path != relative_path or
                    recovery.root_identity != root_identity or recovery.before_digest != expected_digest or recovery.after_digest != "missing"):
                raise FileEditError("edit_recovery_conflict")
            if not exists():
                if digest != recovery.before_digest or identity != recovery.original_identity or metadata != recovery.metadata_digest:
                    raise FileEditError("file_revision_conflict")
                validate()
                retire()
            before, digest, identity, _ = read_previous()
            if digest != recovery.before_digest or identity != recovery.original_identity or previous_metadata() != recovery.metadata_digest:
                if current()[0] is None:
                    restore()
                raise FileEditError("file_revision_conflict")
            if current()[0] is not None:
                raise FileEditError("file_revision_conflict", recovery, file_saved=True)
            return FileEditPublication("missing", before.decode("utf-8", errors="replace" if _import_bytes else "strict"), recovery, True)
        except FileEditError:
            raise
        except Exception:
            raise FileEditError("file_publication_incomplete", recovery) from None
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)


def confirm_import_publications(root: pathlib.Path, captured: dict, proofs: dict, *, command_id: str,
                                validate: Callable[[], None]) -> dict[str, bytes | None]:
    """Read-only exact publication checks for history/marker-only recovery."""
    from row_bot.developer.client_workspace import _empty_parent_guard
    if set(proofs) != set(captured):
        raise FileEditError("edit_recovery_conflict")
    outputs = {}
    for path, host in captured.items():
        validate()
        proof = FileEditRecovery(**proofs[path])
        if (proof.command_id != str(uuid.uuid5(uuid.UUID(command_id), "sandbox-import:" + path))
                or proof.relative_path != path or proof.root_identity != _edit_identity(root.stat())
                or proof.before_digest != host["digest"] or proof.original_identity != host["identity"]):
            raise FileEditError("edit_recovery_conflict")
        target = _edit_path(root, path)
        with _empty_parent_guard(target.parent, host["parent_identity"]) as parent:
            if parent is None:
                data, digest, identity, _ = read_edit_bytes(root, path)
                metadata = file_edit_metadata_digest(target) if data is not None else ""
            else:
                data, digest, identity, _ = _read_edit_at(parent, target.name)
                metadata = _edit_metadata_at(parent, target.name) if data is not None else ""
            if digest != proof.after_digest or identity != proof.candidate_identity:
                raise FileEditError("file_revision_conflict")
            if data is not None and metadata != proof.metadata_digest:
                raise FileEditError("file_revision_conflict")
        if read_retained_edit_before(root, path, proof) != host["data"]:
            raise FileEditError("file_revision_conflict")
        outputs[path] = data
    validate()
    return outputs


def _apply_reviewed_patch(*, workspace_id, thread_id, patch, approval_mode, summary, confirmed,
                          prepared, command_id, persist_recovery, recovery, validate):
    """Canonical strict import path; default legacy Git behavior stays above."""
    from dataclasses import asdict
    decision = decide_action(approval_mode, "edit")
    if decision.decision == "block" or decision.requires_approval and not confirmed:
        return None, decision
    if not callable(validate) or not callable(persist_recovery) or str(uuid.UUID(command_id)) != command_id:
        raise FileEditError("invalid_edit")
    root = _workspace_root(workspace_id)
    captured, outputs = prepared["captured"], prepared["outputs"]
    digest = hashlib.sha256(patch.encode("utf-8")).hexdigest()
    progress = recovery or {"command_id": command_id, "patch_digest": digest, "files": {}, "completed": []}
    if (progress.get("command_id") != command_id or progress.get("patch_digest") != digest or
            set(progress.get("files", {})) - set(captured) or set(outputs) != set(captured)):
        raise FileEditError("edit_recovery_conflict")
    change_ledger.validate_client_ledger()
    completion_only = prepared.get("completion_only", False)
    if completion_only:
        outputs = confirm_import_publications(root, captured, progress["files"], command_id=command_id, validate=validate)
    files = {}
    # Attribute publications are last. A lost marker can subsequently finish
    # history only after every host effect is independently proved complete.
    for path in sorted(outputs, key=lambda path: pathlib.PurePosixPath(path).name.casefold() == ".gitattributes"):
        content = outputs[path]
        validate()
        old = progress["files"].get(path)
        file_recovery = FileEditRecovery(**old) if old else None
        file_command = str(uuid.uuid5(uuid.UUID(command_id), "sandbox-import:" + path))
        def persist(value, path=path):
            progress["files"][path] = asdict(value)
            persist_recovery(progress)
        arguments = dict(expected_digest=captured[path]["digest"], command_id=file_command,
            expected_identity=captured[path]["identity"], expected_metadata=captured[path]["metadata"],
            expected_parent_identity=captured[path]["parent_identity"],
            persist_recovery=persist, recovery=file_recovery, validate=validate)
        if completion_only:
            before = captured[path]["data"]
            publication = FileEditPublication(file_recovery.after_digest,
                before.decode("utf-8", errors="replace") if before is not None else None, file_recovery, False)
        elif content is None:
            publication = publish_file_removal(root, path, _import_bytes=True, **arguments)
        else:
            publication = publish_text_revision(root, path, "", _import_bytes=content, **arguments)
        if path not in progress["completed"]:
            progress["completed"].append(path)
        progress["completed"] = [item for item in captured if item in progress["completed"]]
        persist_recovery(progress)
        files[path] = FileChange(path=path, action="delete" if content is None else "create" if publication.before_text is None else "update",
            before_hash=change_ledger.text_hash(publication.before_text),
            after_hash=change_ledger.text_hash(content.decode("utf-8", errors="replace") if content is not None else None),
            before_text=publication.before_text)
    validate()
    change = change_ledger.record_change_set(workspace_id=workspace_id, thread_id=thread_id,
        summary=summary or "Import sandbox changes", files=[files[path] for path in captured], command_id=command_id,
        guarded_import={"kind": "workspace.import.v1", "command_id": command_id,
            "files": progress["files"],
            "parents": {path: captured[path]["parent_identity"] for path in captured},
            "directories": sorted({directory for item in captured.values() for directory in item.get("missing_parents", ())})})
    return change, decision


def write_file_to_workspace(
    *,
    workspace_id: str,
    thread_id: str,
    path: str,
    content: str,
    approval_mode: ApprovalMode,
    summary: str = "",
    confirmed: bool = False,
) -> tuple[ChangeSet | None, ApprovalDecision]:
    decision = ordinary_edit_decision(approval_mode)
    if decision.decision == "block":
        return None, decision
    if decision.requires_approval and not confirmed:
        return None, decision

    root = _workspace_root(workspace_id)
    target = _validate_relative_path(root, path)
    before_text = target.read_text(encoding="utf-8", errors="replace") if target.exists() else None
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    after_text = target.read_text(encoding="utf-8", errors="replace")
    action = "create" if before_text is None else "update"
    change_set = change_ledger.record_change_set(
        workspace_id=workspace_id,
        thread_id=thread_id,
        summary=summary or f"Write {path}",
        files=[
            FileChange(
                path=path.strip().replace("\\", "/"),
                action=action,
                before_hash=change_ledger.text_hash(before_text),
                after_hash=change_ledger.text_hash(after_text),
                before_text=before_text,
            )
        ],
    )
    return change_set, decision


def revert_change_set(workspace_id: str, change_set_id: str) -> str:
    root = _workspace_root(workspace_id)
    matches = [
        item for item in change_ledger.list_change_sets(workspace_id=workspace_id, include_reverted=False)
        if item.id == change_set_id
    ]
    if not matches:
        raise ValueError(f"Agent change set not found or already reverted: {change_set_id}")
    change_set = matches[0]
    if change_ledger.requires_guarded_undo(change_set):
        raise ValueError("workspace_undo_review_required")
    for file_change in change_set.files:
        target = _validate_relative_path(root, file_change.path, strip_git_prefix=False)
        current = target.read_text(encoding="utf-8", errors="replace") if target.exists() else None
        if change_ledger.text_hash(current) != file_change.after_hash:
            raise ValueError(
                f"Refusing to revert {file_change.path}: file changed after the agent edit."
            )
    for file_change in change_set.files:
        target = _validate_relative_path(root, file_change.path, strip_git_prefix=False)
        if file_change.before_text is None:
            target.unlink(missing_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(file_change.before_text, encoding="utf-8")
    change_ledger.mark_reverted(change_set.id)
    return f"Reverted {len(change_set.files)} file(s) from change set {change_set.id}."

# Explicit client edits share the file owner but require a complete revision
# and a durable command-owned recovery receipt. Legacy tool signatures stay intact.


CLIENT_EDIT_BYTE_LIMIT = 1024 * 1024


@dataclass(frozen=True)
class FileEditRecovery:
    command_id: str
    relative_path: str
    root_identity: str
    before_digest: str
    after_digest: str
    original_identity: str
    candidate_identity: str
    metadata_digest: str


@dataclass(frozen=True)
class DirectoryEditRecovery:
    command_id: str
    relative_path: str
    root_identity: str
    parent_identity: str
    directory_identity: str


def publish_import_directory(root: pathlib.Path, relative_path: str, *, command_id: str,
                             expected_parent_identity: str,
                             persist_recovery: Callable[[DirectoryEditRecovery], None],
                             recovery: DirectoryEditRecovery | None = None,
                             validate: Callable[[], None]) -> DirectoryEditRecovery:
    """Publish a proven empty candidate by no-replace rename, never adopt a name.

    The command's private candidate identity is durable before the public name
    changes. An interrupted pre-receipt staging operation remains unconfirmed;
    the retained candidate is never inferred to belong to a later attempt.
    """
    from contextlib import ExitStack
    from row_bot.developer.client_workspace import _empty_parent_guard, _directory_identity
    if str(uuid.UUID(command_id)) != command_id:
        raise FileEditError("invalid_edit")
    target = _edit_path(root, relative_path)
    root_identity = _edit_identity(root.stat())
    with ExitStack() as guards:
        parent = guards.enter_context(_empty_parent_guard(target.parent, expected_parent_identity))
        descriptors = []
        try:
            validate()
            def info(name, descriptor):
                try:
                    return os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                except FileNotFoundError:
                    return None
            def identity(name, descriptor):
                found = info(name, descriptor)
                if found is None:
                    return None
                if not stat.S_ISDIR(found.st_mode) or getattr(found, "st_file_attributes", 0) & 0x400:
                    raise FileEditError("workspace_path_denied")
                return _edit_identity(found) + ":0"
            leaf = target if parent is None else target.name
            existing = identity(leaf, parent)
            if recovery is not None:
                if (recovery.command_id != command_id or recovery.relative_path != relative_path
                        or recovery.root_identity != root_identity or recovery.parent_identity != expected_parent_identity):
                    raise FileEditError("edit_recovery_conflict")
                if existing is not None:
                    if existing != recovery.directory_identity:
                        raise FileEditError("file_revision_conflict")
                    validate()
                    return recovery
            elif existing is not None:
                raise FileEditError("file_revision_conflict")
            retained_path = target.parent / ".row-bot-edit-recovery"
            generation_path = retained_path / command_id
            if parent is None:
                if recovery is None:
                    retained_path.mkdir(exist_ok=True)
                guards.enter_context(_empty_parent_guard(retained_path, _directory_identity(retained_path, parent=True)))
                if recovery is None:
                    generation_path.mkdir()
                guards.enter_context(_empty_parent_guard(generation_path, _directory_identity(generation_path, parent=True)))
                directory, candidate = None, generation_path / "directory"
            else:
                flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                if recovery is None:
                    try:
                        os.mkdir(".row-bot-edit-recovery", mode=0o700, dir_fd=parent)
                    except FileExistsError:
                        pass
                retained = os.open(".row-bot-edit-recovery", flags, dir_fd=parent)
                descriptors.append(retained)
                if recovery is None:
                    os.mkdir(command_id, mode=0o700, dir_fd=retained)
                directory = os.open(command_id, flags, dir_fd=retained)
                descriptors.append(directory)
                candidate = "directory"
            if recovery is None:
                # The retained generation protects the unpublished candidate;
                # its eventual public directory uses ordinary mkdir defaults.
                os.mkdir(candidate, mode=0o777, dir_fd=directory)
                recovery = DirectoryEditRecovery(command_id, relative_path, root_identity,
                    expected_parent_identity, identity(candidate, directory))
                try:
                    persist_recovery(recovery)
                except Exception:
                    raise FileEditError("edit_receipt_unconfirmed") from None
            if identity(candidate, directory) != recovery.directory_identity:
                raise FileEditError("edit_recovery_conflict")
            # The staged object must still be empty before its first publication.
            if directory is None:
                empty = not any(candidate.iterdir())
            else:
                descriptor = os.open(candidate, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                try:
                    empty = not os.listdir(descriptor)
                finally:
                    os.close(descriptor)
            if not empty:
                raise FileEditError("edit_recovery_conflict")
            validate()
            if identity(candidate, directory) != recovery.directory_identity:
                raise FileEditError("edit_recovery_conflict")
            _rename_edit_no_replace(candidate, leaf, src_dir_fd=directory, dst_dir_fd=parent)
            if identity(leaf, parent) != recovery.directory_identity:
                raise FileEditError("file_revision_conflict")
            validate()
            return recovery
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)


@dataclass(frozen=True)
class FileEditPublication:
    digest: str
    before_text: str | None
    recovery: FileEditRecovery | None
    changed: bool


class FileEditError(ValueError):
    def __init__(self, code: str, recovery: FileEditRecovery | None = None, *, file_saved: bool = False):
        super().__init__(code)
        self.code, self.recovery, self.file_saved = code, recovery, file_saved


def _edit_identity(info: os.stat_result) -> str:
    return f"{info.st_dev}:{info.st_ino}"


def _windows_edit_metadata(path: pathlib.Path) -> bytes:
    """Bounded permission/stream inspection; never modifies ACLs or streams."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    attributes = kernel.GetFileAttributesW
    attributes.argtypes, attributes.restype = [wintypes.LPCWSTR], wintypes.DWORD
    flags = attributes(str(path))
    # Preserve unsupported extended storage metadata by refusing replacement.
    if flags == 0xFFFFFFFF or flags & (0x200 | 0x400 | 0x800 | 0x1000 | 0x4000 | 0x8000 | 0x40000 | 0x400000):
        raise FileEditError("file_metadata_unavailable")
    class StreamData(ctypes.Structure):
        _fields_ = [("size", ctypes.c_longlong), ("name", wintypes.WCHAR * 296)]
    first, following, close = kernel.FindFirstStreamW, kernel.FindNextStreamW, kernel.FindClose
    first.argtypes, first.restype = [wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(StreamData), wintypes.DWORD], wintypes.HANDLE
    following.argtypes, following.restype = [wintypes.HANDLE, ctypes.POINTER(StreamData)], wintypes.BOOL
    close.argtypes, close.restype = [wintypes.HANDLE], wintypes.BOOL
    stream = StreamData()
    handle = first(str(path), 0, ctypes.byref(stream), 0)
    if handle == ctypes.c_void_p(-1).value:
        raise FileEditError("file_metadata_unavailable")
    try:
        while True:
            if stream.name != "::$DATA":
                raise FileEditError("file_metadata_unavailable")
            if not following(handle, ctypes.byref(stream)):
                if ctypes.get_last_error() != 38:  # ERROR_HANDLE_EOF
                    raise FileEditError("file_metadata_unavailable")
                break
    finally:
        close(handle)
    read_security = security.GetFileSecurityW
    read_security.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    read_security.restype = wintypes.BOOL
    size = wintypes.DWORD()
    read_security(str(path), 7, None, 0, ctypes.byref(size))  # owner, group, DACL
    if not 0 < size.value <= 1024 * 1024:
        raise FileEditError("file_metadata_unavailable")
    descriptor = ctypes.create_string_buffer(size.value)
    if not read_security(str(path), 7, descriptor, size.value, ctypes.byref(size)):
        raise FileEditError("file_metadata_unavailable")
    return (flags & ~(0x20 | 0x80)).to_bytes(4, "little") + descriptor.raw[:size.value]


def file_edit_metadata_digest(path: pathlib.Path | int) -> str:
    """Compare supported replacement metadata; fail closed for ADS/xattrs.

    A caller must compare the original and actual staged candidate, then recheck
    at publication. This helper neither grants replacement authority nor copies
    security metadata; a mismatch requires retaining the original file.
    """
    try:
        before = os.fstat(path) if isinstance(path, int) else path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise FileEditError("file_metadata_unavailable")
        if os.name == "nt":
            value = _windows_edit_metadata(path)
        else:
            if not hasattr(os, "listxattr") or (os.listxattr(path) if isinstance(path, int)
                                               else os.listxattr(path, follow_symlinks=False)):
                raise FileEditError("file_metadata_unavailable")
            value = f"{before.st_uid}:{before.st_gid}:{stat.S_IMODE(before.st_mode)}".encode("ascii")
        after = os.fstat(path) if isinstance(path, int) else path.lstat()
        if _edit_identity(before) != _edit_identity(after):
            raise FileEditError("file_revision_conflict")
        return hashlib.sha256(value).hexdigest()
    except FileEditError:
        raise
    except (OSError, ValueError, AttributeError):
        raise FileEditError("file_metadata_unavailable") from None


def _edit_path(root: pathlib.Path, relative_path: str) -> pathlib.Path:
    from row_bot.developer.client_workspace import _empty_folder_name
    from row_bot.developer.review import scoped_workspace_path
    parts = relative_path.replace("\\", "/").split("/") if isinstance(relative_path, str) else []
    if not parts or len(relative_path) > 4096 or ".row-bot-edit-recovery" in parts:
        raise FileEditError("workspace_path_denied")
    try:
        for part in parts:
            _empty_folder_name(part)
        parent = scoped_workspace_path(root, "/".join(parts[:-1]))
        if not parent.is_dir():
            raise ValueError
        target = parent / parts[-1]
        if os.path.lexists(target):
            scoped_workspace_path(root, "/".join(parts))
        return target
    except (OSError, ValueError):
        raise FileEditError("workspace_path_denied") from None


def _validate_publication_limit(max_bytes: int) -> None:
    if type(max_bytes) is not int or not 0 < max_bytes <= 8 * 1024 * 1024:
        raise FileEditError("invalid_edit")


def read_edit_bytes(root: pathlib.Path, relative_path: str, *, max_bytes: int = CLIENT_EDIT_BYTE_LIMIT) -> tuple[bytes | None, str, str, int]:
    """Read a complete bounded regular file; never decode replacement text."""
    _validate_publication_limit(max_bytes)
    target = _edit_path(root, relative_path)
    if os.name != "nt":
        from row_bot.developer.client_workspace import _empty_parent_guard, _directory_identity
        with _empty_parent_guard(target.parent, _directory_identity(target.parent, parent=True)) as descriptor:
            return _read_edit_at(descriptor, target.name, max_bytes=max_bytes)
    if not os.path.lexists(target):
        return None, "missing", "", 0
    try:
        info = target.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes:
            raise FileEditError("file_too_large" if info.st_size > max_bytes else "workspace_path_denied")
        with target.open("rb") as handle:
            opened = os.fstat(handle.fileno())
            if _edit_identity(info) != _edit_identity(opened):
                raise FileEditError("file_revision_conflict")
            content = handle.read(max_bytes + 1)
            finished = os.fstat(handle.fileno())
        if len(content) > max_bytes:
            raise FileEditError("file_too_large")
        current = _edit_path(root, relative_path).lstat()
        if (_edit_identity(current) != _edit_identity(opened) or
                (opened.st_size, opened.st_mtime_ns) != (finished.st_size, finished.st_mtime_ns) or
                (current.st_size, current.st_mtime_ns) != (finished.st_size, finished.st_mtime_ns)):
            raise FileEditError("file_revision_conflict")
        return content, hashlib.sha256(content).hexdigest(), _edit_identity(opened), stat.S_IMODE(opened.st_mode)
    except FileEditError:
        raise
    except OSError:
        raise FileEditError("workspace_path_denied") from None


def _rename_edit_no_replace(source: pathlib.Path | str, destination: pathlib.Path | str, *,
                            src_dir_fd: int | None = None, dst_dir_fd: int | None = None) -> None:
    for name, descriptor in ((source, src_dir_fd), (destination, dst_dir_fd)):
        if descriptor is not None and (os.fspath(name) in {"", ".", ".."} or
                "/" in os.fspath(name) or "\\" in os.fspath(name)):
            raise FileEditError("workspace_path_denied")
    if os.name == "nt":
        if src_dir_fd is not None or dst_dir_fd is not None:
            raise FileEditError("file_publication_unavailable")
        os.rename(source, destination)
        return
    import ctypes
    library = ctypes.CDLL(None, use_errno=True)
    try:
        if sys.platform == "darwin":
            rename = library.renameatx_np
            rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            arguments = (src_dir_fd if src_dir_fd is not None else -2, os.fsencode(source),
                         dst_dir_fd if dst_dir_fd is not None else -2, os.fsencode(destination), 4)
        elif sys.platform.startswith("linux"):
            rename = library.renameat2
            rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            arguments = (src_dir_fd if src_dir_fd is not None else -100, os.fsencode(source),
                         dst_dir_fd if dst_dir_fd is not None else -100, os.fsencode(destination), 1)
        else:
            raise AttributeError
    except AttributeError:
        raise FileEditError("file_publication_unavailable") from None
    rename.restype = ctypes.c_int
    if rename(*arguments):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _read_edit_at(directory: int, name: str, *, max_bytes: int = CLIENT_EDIT_BYTE_LIMIT) -> tuple[bytes | None, str, str, int]:
    """Read only a regular no-follow leaf of the already admitted directory."""
    _validate_publication_limit(max_bytes)
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    except FileNotFoundError:
        return None, "missing", "", 0
    except OSError:
        raise FileEditError("workspace_path_denied") from None
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise FileEditError("workspace_path_denied")
        if opened.st_size > max_bytes:
            raise FileEditError("file_too_large")
        data = stream.read(max_bytes + 1)
        finished = os.fstat(descriptor)
        current = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if len(data) > max_bytes:
            raise FileEditError("file_too_large")
        def revision(info):
            return (_edit_identity(info), info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if revision(opened) != revision(finished) or revision(current) != revision(finished):
            raise FileEditError("file_revision_conflict")
        return data, hashlib.sha256(data).hexdigest(), _edit_identity(opened), stat.S_IMODE(opened.st_mode)


def _edit_metadata_at(directory: int, name: str) -> str:
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    try:
        return file_edit_metadata_digest(descriptor)
    finally:
        os.close(descriptor)


def _verify_retained_candidate(directory: int | pathlib.Path, proof: FileEditRecovery,
                               data: bytes, *, name: str = "previous") -> None:
    if isinstance(directory, int):
        content, digest, identity, _ = _read_edit_at(directory, name)
        metadata = _edit_metadata_at(directory, name) if content is not None else ""
    else:
        content, digest, identity, _ = read_edit_bytes(directory, name)
        metadata = file_edit_metadata_digest(directory / name) if content is not None else ""
    if (content != data or digest != proof.before_digest or identity != proof.original_identity
            or metadata != proof.metadata_digest):
        raise FileEditError("edit_recovery_conflict")


def _publish_text_at(parent: int, target: str, data: bytes, *, relative_path: str,
                     root_identity: str, expected_digest: str, command_id: str,
                     persist_recovery: Callable[[FileEditRecovery], None],
                     recovery: FileEditRecovery | None, validate: Callable[[], None] | None,
                     max_bytes: int = CLIENT_EDIT_BYTE_LIMIT, expected_identity: str | None = None,
                     expected_metadata: str | None = None, import_bytes: bool = False,
                     retained_source: FileEditRecovery | None = None) -> FileEditPublication:
    """POSIX publication stays on admitted directory descriptors after any rename."""
    after_digest = hashlib.sha256(data).hexdigest()
    before, digest, identity, mode = _read_edit_at(parent, target, max_bytes=max_bytes)
    original_metadata = _edit_metadata_at(parent, target) if before is not None else ""
    if before is not None and not import_bytes:
        try:
            before.decode("utf-8")
            if b"\0" in before:
                raise UnicodeError
        except UnicodeError:
            raise FileEditError("file_not_text") from None
    if recovery is None and (digest != expected_digest
            or expected_identity is not None and identity != expected_identity
            or expected_metadata is not None and original_metadata != expected_metadata):
        raise FileEditError("file_revision_conflict")
    if recovery is None and before == data:
        return FileEditPublication(digest, before.decode("utf-8", errors="replace" if import_bytes else "strict"), None, False)
    descriptors = []
    directory = None
    def exists(fd, name):
        try:
            os.stat(name, dir_fd=fd, follow_symlinks=False)
            return True
        except FileNotFoundError:
            return False
    def restore():
        if directory is not None and exists(directory, "previous") and not exists(parent, target):
            _rename_edit_no_replace("previous", target, src_dir_fd=directory, dst_dir_fd=parent)
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        if recovery is None:
            try:
                os.mkdir(".row-bot-edit-recovery", mode=0o700, dir_fd=parent)
            except FileExistsError:
                pass
        retained_root = os.open(".row-bot-edit-recovery", flags, dir_fd=parent)
        descriptors.append(retained_root)
        if recovery is None:
            os.mkdir(command_id, mode=0o700, dir_fd=retained_root)
        directory = os.open(command_id, flags, dir_fd=retained_root)
        descriptors.append(directory)
        if recovery is None:
            if retained_source is not None:
                source = os.open(retained_source.command_id, flags, dir_fd=retained_root)
                descriptors.append(source)
                _verify_retained_candidate(source, retained_source, data)
                os.link("previous", "candidate", src_dir_fd=source, dst_dir_fd=directory, follow_symlinks=False)
                _verify_retained_candidate(directory, retained_source, data, name="candidate")
            else:
                descriptor = os.open("candidate", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                     0o600, dir_fd=directory)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fchmod(descriptor, mode if before is not None else 0o600)
                    os.fsync(descriptor)
            metadata = _edit_metadata_at(directory, "candidate")
            if original_metadata and metadata != original_metadata:
                raise FileEditError("file_metadata_unavailable")
            candidate_identity = _edit_identity(os.stat("candidate", dir_fd=directory, follow_symlinks=False))
            recovery = FileEditRecovery(command_id, relative_path, root_identity, digest, after_digest,
                                        identity, candidate_identity, metadata)
            try:
                persist_recovery(recovery)
            except Exception:
                raise FileEditError("edit_receipt_unconfirmed", recovery) from None
        elif (recovery.command_id != command_id or recovery.relative_path != relative_path or
              recovery.root_identity != root_identity or recovery.before_digest != expected_digest or
              recovery.after_digest != after_digest):
            raise FileEditError("edit_recovery_conflict", recovery)
        _, candidate_digest, candidate_identity, _ = _read_edit_at(directory, "candidate", max_bytes=max_bytes)
        if candidate_digest != after_digest or candidate_identity != recovery.candidate_identity:
            raise FileEditError("edit_recovery_conflict", recovery)
        if _edit_metadata_at(directory, "candidate") != recovery.metadata_digest:
            raise FileEditError("file_metadata_unavailable", recovery)
        if not exists(directory, "previous") and recovery.before_digest != "missing":
            if digest != recovery.before_digest or identity != recovery.original_identity:
                raise FileEditError("file_revision_conflict", recovery)
            if validate:
                validate()
            if retained_source is not None:
                _verify_retained_candidate(directory, retained_source, data, name="candidate")
            _rename_edit_no_replace(target, "previous", src_dir_fd=parent, dst_dir_fd=directory)
        if exists(directory, "previous"):
            before, old_digest, old_identity, _ = _read_edit_at(directory, "previous", max_bytes=max_bytes)
            if old_digest != recovery.before_digest or old_identity != recovery.original_identity:
                raise FileEditError("file_revision_conflict", recovery)
            if _edit_metadata_at(directory, "previous") != recovery.metadata_digest:
                raise FileEditError("file_metadata_unavailable", recovery)
        else:
            before = None
        if validate:
            validate()
        if retained_source is not None:
            _verify_retained_candidate(directory, retained_source, data, name="candidate")
        if _edit_metadata_at(directory, "candidate") != recovery.metadata_digest:
            raise FileEditError("file_metadata_unavailable", recovery)
        if exists(directory, "previous") and _edit_metadata_at(directory, "previous") != recovery.metadata_digest:
            raise FileEditError("file_metadata_unavailable", recovery)
        if exists(parent, target):
            if _edit_identity(os.stat(target, dir_fd=parent, follow_symlinks=False)) != recovery.candidate_identity:
                raise FileEditError("file_revision_conflict", recovery)
        else:
            os.link("candidate", target, src_dir_fd=directory, dst_dir_fd=parent, follow_symlinks=False)
        _, actual_digest, actual_identity, _ = _read_edit_at(parent, target, max_bytes=max_bytes)
        if actual_digest != after_digest or actual_identity != recovery.candidate_identity:
            raise FileEditError("file_revision_conflict", recovery)
        return FileEditPublication(actual_digest, before.decode("utf-8", errors="replace" if import_bytes else "strict") if before is not None else None, recovery, True)
    except Exception as exc:
        try:
            restore()
        except OSError:
            pass
        if isinstance(exc, FileEditError):
            raise
        raise FileEditError("file_publication_incomplete", recovery) from None
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def publish_text_revision(root: pathlib.Path, relative_path: str, content: str, *,
                          expected_digest: str, command_id: str,
                          persist_recovery: Callable[[FileEditRecovery], None],
                          recovery: FileEditRecovery | None = None,
                          validate: Callable[[], None] | None = None,
                          max_bytes: int = CLIENT_EDIT_BYTE_LIMIT, expected_identity: str | None = None,
                          expected_metadata: str | None = None, expected_parent_identity: str | None = None,
                          _import_bytes: bytes | None = None,
                          _retained_source: FileEditRecovery | None = None) -> FileEditPublication:
    """Publish a complete candidate without overwriting a racing external edit.

    Retained originals/candidates stay on the same volume. The application owns
    the receipt before any original is moved. A crash can leave a recoverable
    missing name; this is not a hash-CAS primitive across arbitrary OS writers.
    """
    _validate_publication_limit(max_bytes)
    from row_bot.developer.client_workspace import _empty_parent_guard, _directory_identity
    from row_bot.developer.review import scoped_workspace_path
    try:
        if str(uuid.UUID(command_id)) != command_id:
            raise ValueError
        if _import_bytes is not None and type(_import_bytes) is not bytes:
            raise ValueError
        data = _import_bytes if _import_bytes is not None else content.encode("utf-8")
    except (ValueError, TypeError, UnicodeError, AttributeError):
        raise FileEditError("invalid_edit") from None
    if len(data) > max_bytes or _import_bytes is None and b"\0" in data:
        raise FileEditError("file_too_large" if len(data) > max_bytes else "file_not_text")
    target = _edit_path(root, relative_path)
    root_identity = _edit_identity(root.stat())
    if _retained_source is not None and (_retained_source.relative_path != relative_path
            or _retained_source.root_identity != root_identity
            or str(uuid.UUID(_retained_source.command_id)) != _retained_source.command_id
            or _retained_source.before_digest != hashlib.sha256(data).hexdigest()):
        raise FileEditError("edit_recovery_conflict")
    after_digest = hashlib.sha256(data).hexdigest()
    if validate:
        validate()
    with _empty_parent_guard(target.parent, expected_parent_identity or _directory_identity(target.parent, parent=True)) as parent_descriptor:
        if parent_descriptor is not None:
            return _publish_text_at(parent_descriptor, target.name, data, relative_path=relative_path,
                root_identity=root_identity, expected_digest=expected_digest, command_id=command_id,
                persist_recovery=persist_recovery, recovery=recovery, validate=validate, max_bytes=max_bytes,
                expected_identity=expected_identity, expected_metadata=expected_metadata, import_bytes=_import_bytes is not None,
                retained_source=_retained_source)
        before, digest, identity, mode = read_edit_bytes(root, relative_path, max_bytes=max_bytes)
        original_metadata = file_edit_metadata_digest(target) if before is not None else ""
        if before is not None and _import_bytes is None:
            try:
                before.decode("utf-8")
                if b"\0" in before:
                    raise UnicodeError
            except UnicodeError:
                raise FileEditError("file_not_text") from None
        if recovery is None and (digest != expected_digest
                or expected_identity is not None and identity != expected_identity
                or expected_metadata is not None and original_metadata != expected_metadata):
            raise FileEditError("file_revision_conflict")
        if recovery is None and before == data:
            return FileEditPublication(digest, before.decode("utf-8", errors="replace" if _import_bytes is not None else "strict"), None, False)
        directory = target.parent / ".row-bot-edit-recovery" / command_id
        candidate, retained = directory / "candidate", directory / "previous"
        if recovery is None:
            # Strictly owned new directory; an existing unconfirmed generation
            # is never adopted after response loss without its server receipt.
            directory.parent.mkdir(exist_ok=True)
            scoped_workspace_path(root, directory.parent.relative_to(root).as_posix())
            directory.mkdir(exist_ok=False)
            if _retained_source is not None:
                source = directory.parent / _retained_source.command_id
                scoped_workspace_path(root, source.relative_to(root).as_posix())
                with _empty_parent_guard(source, _directory_identity(source, parent=True)):
                    _verify_retained_candidate(source, _retained_source, data)
                    os.link(source / "previous", candidate, follow_symlinks=False)
                    _verify_retained_candidate(directory, _retained_source, data, name="candidate")
            else:
                with candidate.open("xb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(candidate, mode if before is not None else 0o600)
            candidate_metadata = file_edit_metadata_digest(candidate)
            if original_metadata and candidate_metadata != original_metadata:
                raise FileEditError("file_metadata_unavailable")
            recovery = FileEditRecovery(command_id, relative_path, root_identity, digest, after_digest,
                                        identity, _edit_identity(candidate.stat()), candidate_metadata)
            try:
                persist_recovery(recovery)
            except Exception:
                raise FileEditError("edit_receipt_unconfirmed", recovery) from None
        else:
            if (recovery.command_id != command_id or recovery.relative_path != relative_path
                    or recovery.root_identity != root_identity or recovery.before_digest != expected_digest
                    or recovery.after_digest != after_digest):
                raise FileEditError("edit_recovery_conflict", recovery)
        try:
            scoped_workspace_path(root, directory.relative_to(root).as_posix())
            candidate_data, candidate_digest, candidate_identity, _ = read_edit_bytes(directory, "candidate", max_bytes=max_bytes)
            if candidate_identity != recovery.candidate_identity or candidate_digest != after_digest:
                raise FileEditError("edit_recovery_conflict", recovery)
            if file_edit_metadata_digest(candidate) != recovery.metadata_digest:
                raise FileEditError("file_metadata_unavailable", recovery)
            if os.path.lexists(retained):
                before, old_digest, old_identity, _ = read_edit_bytes(directory, "previous", max_bytes=max_bytes)
                if old_digest != recovery.before_digest or old_identity != recovery.original_identity:
                    # A raced original remains retained and can be restored
                    # only into an absent name, never over a newer editor file.
                    if not os.path.lexists(target):
                        _rename_edit_no_replace(retained, target)
                    raise FileEditError("file_revision_conflict", recovery)
                if file_edit_metadata_digest(retained) != recovery.metadata_digest:
                    raise FileEditError("file_metadata_unavailable", recovery)
            elif recovery.before_digest != "missing":
                if digest != recovery.before_digest or identity != recovery.original_identity:
                    raise FileEditError("file_revision_conflict", recovery)
                if validate:
                    validate()
                if _retained_source is not None:
                    _verify_retained_candidate(directory, _retained_source, data, name="candidate")
                _edit_path(root, relative_path)
                _rename_edit_no_replace(target, retained)
                before, old_digest, old_identity, _ = read_edit_bytes(directory, "previous", max_bytes=max_bytes)
                if old_digest != recovery.before_digest or old_identity != recovery.original_identity:
                    if not os.path.lexists(target):
                        _rename_edit_no_replace(retained, target)
                    raise FileEditError("file_revision_conflict", recovery)
                if file_edit_metadata_digest(retained) != recovery.metadata_digest:
                    raise FileEditError("file_metadata_unavailable", recovery)
            else:
                before = None
            if validate:
                validate()
            if _retained_source is not None:
                _verify_retained_candidate(directory, _retained_source, data, name="candidate")
            _edit_path(root, relative_path)
            if (file_edit_metadata_digest(candidate) != recovery.metadata_digest or
                    (os.path.lexists(retained) and file_edit_metadata_digest(retained) != recovery.metadata_digest)):
                raise FileEditError("file_metadata_unavailable", recovery)
            if os.path.lexists(target):
                if not target.samefile(candidate):
                    raise FileEditError("file_revision_conflict", recovery)
            else:
                os.link(candidate, target, follow_symlinks=False)
            actual, digest, identity, _ = read_edit_bytes(root, relative_path, max_bytes=max_bytes)
            if digest != after_digest or identity != recovery.candidate_identity:
                raise FileEditError("file_revision_conflict", recovery)
            before_text = before.decode("utf-8", errors="replace" if _import_bytes is not None else "strict") if before is not None else None
            return FileEditPublication(digest, before_text, recovery, True)
        except Exception as exc:
            # Restore the captured name without replacing any racing creator.
            # Failure leaves both the candidate and the retained source intact.
            if os.path.lexists(retained) and not os.path.lexists(target):
                try:
                    _rename_edit_no_replace(retained, target)
                except OSError:
                    pass
            if isinstance(exc, FileEditError):
                raise
            raise FileEditError("file_publication_incomplete", recovery) from None
