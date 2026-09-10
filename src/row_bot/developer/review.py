from __future__ import annotations

import pathlib
import subprocess
import os
import stat
import hashlib
import json
import base64
import bisect
import threading
import shutil
from typing import Literal
from dataclasses import dataclass


@dataclass(frozen=True)
class ChangedFile:
    path: str
    status: str
    additions: int = 0
    deletions: int = 0


@dataclass(frozen=True)
class DiffStats:
    files: int = 0
    additions: int = 0
    deletions: int = 0


@dataclass(frozen=True)
class DirectoryEntry:
    name: str
    relative_path: str
    kind: Literal["file", "directory"]
    previewable: bool


@dataclass(frozen=True)
class DirectoryPage:
    items: tuple[DirectoryEntry, ...]
    next_cursor: str | None
    directory_revision: str
    excluded: tuple[str, ...] = (".git", "symbolic links and reparse points")


@dataclass(frozen=True)
class FilePreview:
    status: Literal["text", "binary", "missing", "denied", "stale"]
    relative_path: str
    text: str = ""
    revision: str = ""
    next_offset: int | None = None
    size_bytes: int = 0


@dataclass(frozen=True)
class DiffPreview:
    status: Literal["text", "missing", "denied", "stale", "unavailable"]
    text: str
    revision: str
    next_offset: int | None = None
    truncated: bool = False


def _git_read_metadata(workspace_path: str, arguments: list[str]) -> tuple[int, bytes]:
    """Read bounded Git metadata without interpreting it as a command or path grant."""
    with subprocess.Popen(["git", "-C", workspace_path, *arguments], stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"}) as process:
        timer = threading.Timer(10, process.kill)
        timer.daemon = True
        timer.start()
        try:
            assert process.stdout is not None
            output = process.stdout.read(8193)
            if len(output) > 8192:
                process.kill()
                raise ValueError("git_metadata_unavailable")
            process.wait(timeout=2)
            return process.returncode, output
        finally:
            timer.cancel()
            if process.poll() is None:
                process.kill()
                process.wait(timeout=2)


def _diff_base_revision(root: pathlib.Path) -> str:
    code, index_path = _git_read_metadata(str(root), ["rev-parse", "--git-path", "index"])
    if code:
        raise ValueError("git_metadata_unavailable")
    # Git worktrees legitimately keep their index in the project's Git metadata
    # outside the execution folder. Only stat metadata is observed here; this
    # internal path is never returned or used as authority for reading a file.
    path = pathlib.Path(index_path.decode("utf-8").rstrip("\r\n"))
    path = path if path.is_absolute() else root / path
    index = _file_revision(path.stat()) if path.exists() else "no-index"
    code, head = _git_read_metadata(str(root), ["rev-parse", "--verify", "HEAD"])
    if code not in {0, 128}:
        raise ValueError("git_metadata_unavailable")
    return hashlib.sha256(index.encode() + b"\0" + (head if code == 0 else b"unborn")).hexdigest()


def workspace_has_custom_read_hooks(workspace_path: str) -> bool:
    """Detect Git filters/fsmonitor without invoking configured executable hooks."""
    if shutil.which("git") is None:
        return False
    # Repository discovery observes metadata only. A plain folder never reaches
    # status/diff, so inherited global filters do not restrict its file reads.
    code, inside = _git_read_metadata(workspace_path, ["rev-parse", "--is-inside-work-tree"])
    if code == 128 or (code == 0 and inside.strip() == b"false"):
        return False
    if code != 0 or inside.strip() != b"true":
        raise ValueError("workspace_read_hooks_unavailable")
    environment = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    with subprocess.Popen(["git", "-C", workspace_path, "config", "--name-only", "--get-regexp",
                           r"^(filter\..*\.(clean|process)|core\.fsmonitor)$"], stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, env=environment) as config:
        timer = threading.Timer(10, config.kill)
        timer.daemon = True
        timer.start()
        try:
            assert config.stdout is not None
            configured = config.stdout.read(65537)
            if len(configured) > 65536:
                config.kill()
            config.wait(timeout=2)
            if config.returncode not in {0, 1}:
                raise ValueError("workspace_read_hooks_unavailable")
            return bool(configured)
        finally:
            timer.cancel()
            if config.poll() is None:
                config.kill()
                config.wait(timeout=2)


def read_bounded_diff(workspace_path: str, relative_path: str, *, offset: int = 0,
                      limit_bytes: int = 32768, expected_revision: str | None = None) -> DiffPreview:
    """Read an explicitly requested Git diff with bounded memory and duration."""
    if offset < 0 or not 4 <= limit_bytes <= 65536:
        raise ValueError("invalid_limit")
    root = pathlib.Path(workspace_path)
    try:
        try:
            target = scoped_workspace_path(root, relative_path)
            version = _file_revision(target.stat())
        except FileNotFoundError:
            # Deleted files have legitimate diffs; validate every surviving parent.
            parts = relative_path.replace("\\", "/").split("/")
            if not parts or parts[-1] in {"", ".", "..", ".git"} or ":" in parts[-1]:
                raise ValueError("workspace_path_denied")
            scoped_workspace_path(root, "/".join(parts[:-1]))
            version = "missing"
        environment = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
        # Even with external diff/textconv disabled, Git can invoke a configured
        # clean filter while comparing the working tree. Do not run those hooks
        # from this read-only pilot. A file preview remains available instead.
        if workspace_has_custom_read_hooks(str(root)):
            return DiffPreview("unavailable", "", "")
        base_revision = _diff_base_revision(root)
        revision = hashlib.sha256(f"{relative_path}:{version}:{base_revision}".encode()).hexdigest()
        if expected_revision is not None and expected_revision != revision:
            return DiffPreview("stale", "", revision)
        data = b""
        for extra in ([], ["--cached"]):
            with subprocess.Popen(["git", "--no-pager", "-C", str(root), "diff", "--no-ext-diff",
                                   "--no-textconv", *extra, "--", relative_path],
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  env=environment) as process:
                timer = threading.Timer(10, process.kill)
                timer.daemon = True
                timer.start()
                try:
                    assert process.stdout is not None
                    skipped = 0
                    while skipped < offset:
                        block = process.stdout.read(min(65536, offset - skipped))
                        if not block:
                            break
                        skipped += len(block)
                    data = process.stdout.read(limit_bytes + 4)
                    if len(data) > limit_bytes:
                        process.kill()
                    process.wait(timeout=2)
                finally:
                    timer.cancel()
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=2)
                if process.returncode and not data:
                    return DiffPreview("unavailable", "", revision)
            if data or skipped:
                break
        if version != "missing":
            target = scoped_workspace_path(root, relative_path)
            if version != _file_revision(target.stat()):
                return DiffPreview("stale", "", revision)
        if base_revision != _diff_base_revision(root):
            return DiffPreview("stale", "", revision)
        truncated = len(data) > limit_bytes
        page = data[:limit_bytes]
        if truncated:
            try:
                page.decode("utf-8")
            except UnicodeDecodeError as exc:
                if exc.reason == "unexpected end of data":
                    page = page[:exc.start]
        return DiffPreview("text", page.decode("utf-8", errors="replace"), revision,
                           offset + len(page) if truncated else None, truncated)
    except (OSError, ValueError, subprocess.SubprocessError):
        return DiffPreview("denied", "", "")


def _is_link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def scoped_workspace_path(root: pathlib.Path, relative_path: str = "") -> pathlib.Path:
    """Resolve a relative read inside the registered root without following links."""
    if (not isinstance(relative_path, str) or len(relative_path) > 4096
            or any(ord(c) < 32 for c in relative_path) or ":" in relative_path
            or relative_path.startswith(("/", "\\"))):
        raise ValueError("workspace_path_denied")
    parts = relative_path.replace("\\", "/").split("/") if relative_path else []
    if any(part in {"", ".", "..", ".git"} for part in parts):
        raise ValueError("workspace_path_denied")
    root = root.absolute()
    # Check ancestors as well: a registered folder must not become a link later.
    for parent in (*reversed(root.parents), root):
        if _is_link(parent.lstat()):
            raise ValueError("workspace_path_denied")
    target = root
    for part in parts:
        target = target / part
        if _is_link(target.lstat()):
            raise ValueError("workspace_path_denied")
    target.resolve().relative_to(root.resolve())
    return target


def _file_revision(info: os.stat_result) -> str:
    # Windows stat and fstat disagree about ctime (creation/change time).
    # Identity, size and modification time are consistent across both APIs.
    return f"{info.st_dev}:{info.st_ino}:{info.st_size}:{info.st_mtime_ns}"


def list_directory_page(workspace_path: str, directory: str = "", *,
                        cursor: str | None = None, limit: int = 50,
                        expected_revision: str | None = None) -> DirectoryPage:
    """Enumerate only the expanded directory, retaining at most limit+1 rows."""
    if not 1 <= limit <= 100:
        raise ValueError("invalid_limit")
    root = pathlib.Path(workspace_path)
    folder = scoped_workspace_path(root, directory)
    scope = hashlib.sha256(f"{root}\0{directory}".encode()).hexdigest()
    after = ""
    cursor_revision = expected_revision
    if cursor:
        try:
            if len(cursor) > 2048:
                raise ValueError
            value = json.loads(base64.urlsafe_b64decode(cursor.encode()))
            if value[0] != scope:
                raise ValueError
            cursor_revision, after = value[1], value[2]
            if not isinstance(after, str):
                raise ValueError
        except (ValueError, TypeError, IndexError, KeyError, UnicodeError):
            raise ValueError("invalid_cursor") from None
    candidates: list[tuple[str, DirectoryEntry]] = []
    digest = 0
    initial = _file_revision(folder.stat())
    with os.scandir(folder) as entries:
        for entry in entries:
            info = entry.stat(follow_symlinks=False)
            digest ^= int.from_bytes(hashlib.sha256(
                (entry.name + "\0" + _file_revision(info)).encode()).digest(), "big")
            if entry.name == ".git" or _is_link(info):
                continue
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                continue
            if entry.name <= after:
                continue
            relative = f"{directory}/{entry.name}" if directory else entry.name
            item = DirectoryEntry(entry.name, relative,
                                  "directory" if stat.S_ISDIR(info.st_mode) else "file",
                                  stat.S_ISREG(info.st_mode))
            bisect.insort(candidates, (entry.name, item))
            if len(candidates) > limit + 1:
                candidates.pop()
    scoped_workspace_path(root, directory)
    if initial != _file_revision(folder.stat()):
        raise ValueError("directory_revision_conflict")
    revision = hashlib.sha256(f"{initial}:{digest}".encode()).hexdigest()
    if cursor_revision is not None and revision != cursor_revision:
        raise ValueError("directory_revision_conflict")
    rows = tuple(item for _, item in candidates[:limit])
    next_cursor = None
    if len(candidates) > limit:
        # Encode a root fingerprint, never a host path, in the public cursor.
        value = [scope, revision, rows[-1].name]
        next_cursor = base64.urlsafe_b64encode(json.dumps(value).encode()).decode()
    return DirectoryPage(rows, next_cursor, revision)


def read_bounded_file(workspace_path: str, relative_path: str, *, offset: int = 0,
                      limit_bytes: int = 32768, expected_revision: str | None = None) -> FilePreview:
    """Read a bounded UTF-8 page; never materialize a whole large file to truncate."""
    if not 0 <= offset <= 2**63 - 1 or not 4 <= limit_bytes <= 65536:
        raise ValueError("invalid_limit")
    root = pathlib.Path(workspace_path)
    try:
        target = scoped_workspace_path(root, relative_path)
        before = target.stat()
        if not stat.S_ISREG(before.st_mode):
            return FilePreview("denied", relative_path)
        revision = _file_revision(before)
        if expected_revision is not None and revision != expected_revision:
            return FilePreview("stale", relative_path, revision=revision)
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(target, flags), "rb") as handle:
            current = os.fstat(handle.fileno())
            scoped_workspace_path(root, relative_path)
            if _file_revision(current) != revision or _file_revision(target.stat()) != revision:
                return FilePreview("stale", relative_path)
            if b"\0" in handle.read(4096):
                return FilePreview("binary", relative_path, revision=revision, size_bytes=before.st_size)
            handle.seek(offset)
            data = handle.read(limit_bytes)
            # Preserve a UTF-8 character spanning the page boundary for next read.
            if offset + len(data) < before.st_size:
                for count in range(1, min(4, len(data)) + 1):
                    try:
                        data.decode("utf-8")
                        break
                    except UnicodeDecodeError as exc:
                        if exc.reason != "unexpected end of data":
                            break
                        data = data[:exc.start]
            scoped_workspace_path(root, relative_path)
            if (_file_revision(os.fstat(handle.fileno())) != revision
                    or _file_revision(target.stat()) != revision):
                return FilePreview("stale", relative_path)
        next_offset = offset + len(data) if offset + len(data) < before.st_size else None
        return FilePreview("text", relative_path, data.decode("utf-8", errors="replace"),
                           revision, next_offset, before.st_size)
    except FileNotFoundError:
        return FilePreview("missing", relative_path)
    except (OSError, ValueError):
        return FilePreview("denied", relative_path)


def _git(path: pathlib.Path, args: list[str], *, timeout: int = 10) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    return result.stdout


def list_changed_files(workspace_path: str) -> list[ChangedFile]:
    folder = pathlib.Path(workspace_path).expanduser()
    try:
        output = _git(folder, ["status", "--porcelain=v1", "-z", "-uall"])
    except Exception:
        return []
    stats = _workspace_numstat(folder)
    rows: list[ChangedFile] = []
    records = iter(output.split("\0"))
    for line in records:
        if not line:
            continue
        status = line[:2].strip() or "modified"
        file_path = line[3:]
        if "R" in line[:2] or "C" in line[:2]:
            # Porcelain -z emits the destination first, then the source name.
            next(records, None)
        additions, deletions = stats.get(file_path, (0, 0))
        if additions == 0 and deletions == 0 and status == "??":
            additions = _count_text_lines_if_safe(folder, file_path)
        rows.append(ChangedFile(path=file_path, status=status, additions=additions, deletions=deletions))
    return rows


def get_file_diff(workspace_path: str, file_path: str, *, limit: int = 12_000) -> str:
    folder = pathlib.Path(workspace_path).expanduser()
    clean_file = str(file_path or "").strip()
    if not clean_file:
        return ""
    try:
        diff = _git(folder, ["diff", "--", clean_file], timeout=20)
        if not diff:
            diff = _git(folder, ["diff", "--cached", "--", clean_file], timeout=20)
    except Exception:
        return ""
    if not diff and _is_untracked(folder, clean_file):
        target = (folder / clean_file).resolve()
        try:
            target.relative_to(folder.resolve())
        except ValueError:
            return ""
        if target.is_file() and _looks_text(target):
            try:
                text = target.read_text(encoding="utf-8", errors="replace")
            except Exception:
                return ""
            body = "\n".join(f"+{line}" for line in text.splitlines())
            diff = (
                f"diff --git a/{clean_file} b/{clean_file}\n"
                "new file mode 100644\n"
                "--- /dev/null\n"
                f"+++ b/{clean_file}\n"
                f"@@ -0,0 +1,{len(text.splitlines())} @@\n"
                f"{body}\n"
            )
    if len(diff) > limit:
        return diff[:limit] + "\n...[diff truncated]"
    return diff


def get_file_diff_stats(workspace_path: str, file_path: str) -> tuple[int, int]:
    folder = pathlib.Path(workspace_path).expanduser()
    clean_file = str(file_path or "").strip()
    if not clean_file:
        return 0, 0
    try:
        output = _git(folder, ["diff", "--numstat", "--", clean_file], timeout=20)
        if not output:
            output = _git(folder, ["diff", "--cached", "--numstat", "--", clean_file], timeout=20)
    except Exception:
        output = ""
    additions = 0
    deletions = 0
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        add_raw, del_raw = parts[0], parts[1]
        additions += int(add_raw) if add_raw.isdigit() else 0
        deletions += int(del_raw) if del_raw.isdigit() else 0
    if additions == 0 and deletions == 0 and _is_untracked(folder, clean_file):
        additions = _count_text_lines_if_safe(folder, clean_file)
    return additions, deletions


def get_workspace_diff_stats(workspace_path: str) -> DiffStats:
    changed = list_changed_files(workspace_path)
    return DiffStats(
        files=len(changed),
        additions=sum(item.additions for item in changed),
        deletions=sum(item.deletions for item in changed),
    )


def list_workspace_files(workspace_path: str, *, limit: int = 120) -> list[str]:
    folder = pathlib.Path(workspace_path).expanduser().resolve()
    skip = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", "dist", "build"}
    rows: list[str] = []
    if not folder.is_dir():
        return rows
    for path in sorted(folder.rglob("*"), key=lambda item: item.relative_to(folder).as_posix().lower()):
        rel_parts = set(path.relative_to(folder).parts)
        if rel_parts & skip:
            continue
        if path.is_file():
            rows.append(path.relative_to(folder).as_posix())
            if len(rows) >= limit:
                break
    return rows


def read_file_preview(workspace_path: str, file_path: str, *, max_chars: int = 20_000) -> str:
    folder = pathlib.Path(workspace_path).expanduser().resolve()
    clean = str(file_path or "").strip().replace("\\", "/")
    if not clean:
        return ""
    target = (folder / clean).resolve()
    try:
        target.relative_to(folder)
    except ValueError as exc:
        raise ValueError(f"Path escapes workspace: {file_path}") from exc
    if not target.is_file():
        return "File not found."
    if not _looks_text(target):
        return "Binary or unsupported file preview."
    text = target.read_text(encoding="utf-8", errors="replace")
    if len(text) > max_chars:
        return text[:max_chars] + "\n...[file truncated]"
    return text


def _workspace_numstat(folder: pathlib.Path) -> dict[str, tuple[int, int]]:
    stats: dict[str, tuple[int, int]] = {}
    for args in (["diff", "--numstat", "-z"], ["diff", "--cached", "--numstat", "-z"]):
        try:
            output = _git(folder, args, timeout=20)
        except Exception:
            continue
        records = iter(output.split("\0"))
        for line in records:
            parts = line.split("\t", 2)
            if len(parts) < 3:
                continue
            add_raw, del_raw, path = parts[0], parts[1], parts[2]
            if not path:
                next(records, None)
                path = next(records, "")
            additions = int(add_raw) if add_raw.isdigit() else 0
            deletions = int(del_raw) if del_raw.isdigit() else 0
            prev_additions, prev_deletions = stats.get(path, (0, 0))
            stats[path] = (prev_additions + additions, prev_deletions + deletions)
    return stats


def _count_text_lines_if_safe(folder: pathlib.Path, file_path: str) -> int:
    target = (folder / file_path).resolve()
    try:
        target.relative_to(folder.resolve())
    except ValueError:
        return 0
    if not target.is_file() or not _looks_text(target):
        return 0
    try:
        count = 0
        last = ""
        with target.open("r", encoding="utf-8", errors="replace") as handle:
            while chunk := handle.read(65536):
                count += chunk.count("\n")
                last = chunk[-1]
        return count + int(bool(last) and last != "\n")
    except Exception:
        return 0


def _is_untracked(folder: pathlib.Path, file_path: str) -> bool:
    try:
        output = _git(folder, ["status", "--porcelain", "-uall", "--", file_path], timeout=10)
    except Exception:
        return False
    return any(line.startswith("?? ") for line in output.splitlines())


def _looks_text(path: pathlib.Path, *, sniff_bytes: int = 4096) -> bool:
    try:
        with path.open("rb") as handle:
            data = handle.read(sniff_bytes)
    except Exception:
        return False
    return b"\0" not in data
