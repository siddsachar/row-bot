from __future__ import annotations

import pathlib
import re
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class GitStatus:
    path: str
    is_git: bool = False
    branch: str = ""
    remote: str = ""
    dirty: bool = False
    ahead_behind: str = ""
    repo_root: str = ""
    is_repo_root: bool = False
    error: str = ""


def _run_git(path: pathlib.Path, args: list[str], *, timeout: int = 10) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.stdout.strip()


def get_git_status(path: str) -> GitStatus:
    folder = pathlib.Path(path).expanduser()
    if not folder.exists():
        return GitStatus(path=str(folder), error="Workspace folder does not exist.")
    try:
        inside = _run_git(folder, ["rev-parse", "--is-inside-work-tree"])
        if inside.lower() != "true":
            return GitStatus(path=str(folder), is_git=False)
        repo_root = _run_git(folder, ["rev-parse", "--show-toplevel"])
        repo_root_path = pathlib.Path(repo_root).expanduser().resolve()
        folder_resolved = folder.resolve()
        branch = _run_git(folder, ["branch", "--show-current"])
        remote = ""
        try:
            remote = _run_git(folder, ["remote", "get-url", "origin"])
        except Exception:
            remote = ""
        dirty = bool(_run_git(folder, ["status", "--porcelain"]))
        ahead_behind = ""
        try:
            ahead_behind = _run_git(folder, ["status", "-sb"]).splitlines()[0]
        except Exception:
            ahead_behind = ""
        return GitStatus(
            path=str(folder),
            is_git=True,
            branch=branch,
            remote=remote,
            dirty=dirty,
            ahead_behind=ahead_behind,
            repo_root=str(repo_root_path),
            is_repo_root=repo_root_path == folder_resolved,
        )
    except subprocess.CalledProcessError as exc:
        detail = f"{exc.stderr or ''}\n{exc.stdout or ''}".lower()
        if "not a git repository" in detail or "not a git repo" in detail:
            return GitStatus(path=str(folder), is_git=False)
        return GitStatus(path=str(folder), error=str(exc))
    except Exception as exc:
        return GitStatus(path=str(folder), error=str(exc))


def list_branches(path: str, *, limit: int = 50) -> list[str]:
    """Local branch names, most recently committed first, at most ``limit``."""
    folder = pathlib.Path(path).expanduser()
    output = _run_git(folder, [
        "for-each-ref", "--sort=-committerdate", f"--count={max(1, limit)}",
        "--format=%(refname:short)", "refs/heads",
    ])
    return [line.strip() for line in output.splitlines() if line.strip()][:limit]


def sanitize_branch_name(name: str) -> str:
    text = str(name or "").strip().replace("\\", "/")
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"[^A-Za-z0-9._/-]+", "-", text)
    text = re.sub(r"/+", "/", text).strip("/.-")
    if not text:
        return "feature"
    if text.endswith(".lock"):
        text = text[:-5]
    return text[:120]


def create_branch(path: str, branch_name: str) -> GitStatus:
    folder = pathlib.Path(path).expanduser()
    branch = sanitize_branch_name(branch_name)
    if not branch:
        raise ValueError("Branch name is required.")
    _run_git(folder, ["checkout", "-b", branch])
    return get_git_status(str(folder))


def switch_branch(path: str, branch_name: str) -> GitStatus:
    folder = pathlib.Path(path).expanduser()
    branch = sanitize_branch_name(branch_name)
    if not branch:
        raise ValueError("Branch name is required.")
    _run_git(folder, ["switch", branch])
    return get_git_status(str(folder))


def commit_changes(path: str, message: str, paths: list[str] | None = None) -> GitStatus:
    folder = pathlib.Path(path).expanduser()
    msg = str(message or "").strip()
    if not msg:
        raise ValueError("Commit message is required.")
    add_paths = paths or ["."]
    _run_git(folder, ["add", "--", *add_paths])
    _run_git(folder, ["commit", "-m", msg], timeout=60)
    return get_git_status(str(folder))


def fast_forward_merge(path: str, branch_name: str) -> GitStatus:
    folder = pathlib.Path(path).expanduser()
    branch = sanitize_branch_name(branch_name)
    if not branch:
        raise ValueError("Branch name is required.")
    _run_git(folder, ["merge", "--ff-only", branch], timeout=60)
    return get_git_status(str(folder))


def create_worktree(path: str, parent_folder: str, branch_name: str) -> pathlib.Path:
    source = pathlib.Path(path).expanduser().resolve()
    parent = pathlib.Path(parent_folder).expanduser().resolve()
    if not parent.exists() or not parent.is_dir():
        raise ValueError(f"Worktree parent folder does not exist: {parent_folder}")
    branch = sanitize_branch_name(branch_name)
    if not branch:
        raise ValueError("Branch name is required.")
    target = parent / branch.replace("/", "-")
    if target.exists():
        raise FileExistsError(f"Worktree target already exists: {target}")
    _run_git(source, ["worktree", "add", "-b", branch, str(target)])
    return target


@dataclass(frozen=True)
class BranchChanges:
    base: str
    commits: tuple[str, ...]
    files: tuple[str, ...]


def branch_changes(path: str, *, max_commits: int = 20, max_files: int = 100) -> BranchChanges | None:
    """What the current branch adds to its base branch: commit subjects (oldest first) and changed files.

    The base is the remote's default branch, else a local main or master. None when there is no base or the
    current branch is the base. No external diff drivers or text conversions run."""
    folder = pathlib.Path(path).expanduser()
    try:
        branch = _run_git(folder, ["branch", "--show-current"])
        candidates = []
        try:
            candidates.append(_run_git(folder, ["rev-parse", "--abbrev-ref", "origin/HEAD"]))
        except subprocess.CalledProcessError:
            pass
        candidates += ["main", "master", "origin/main", "origin/master"]
        base = ""
        for candidate in candidates:
            try:
                _run_git(folder, ["rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}"])
                base = candidate
                break
            except subprocess.CalledProcessError:
                continue
        if not base or not branch or base.split("/", 1)[-1] == branch:
            return None
        commits = _run_git(folder, ["log", "--reverse", "--no-merges", f"--max-count={max_commits}",
                                    "--format=%s", f"{base}..HEAD"])
        files = _run_git(folder, ["diff", "--name-only", "--no-ext-diff", "--no-textconv", f"{base}...HEAD"])
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        return None
    return BranchChanges(base, tuple(line for line in commits.splitlines() if line.strip()),
                         tuple(line for line in files.splitlines() if line.strip())[:max_files])


def pull_request_text(branch: str, changes: BranchChanges | None) -> dict[str, str] | None:
    """A pull request title and body from what the branch adds to its base (B301); None when it adds nothing."""
    if changes is None or not changes.commits:
        return None
    if len(changes.commits) == 1:
        title = changes.commits[0]
    else:
        words = re.sub(r"^(?:feat|feature|fix|bugfix|hotfix|chore|docs|refactor)/", "", branch, flags=re.I)
        words = re.sub(r"[-_]+", " ", words).strip()
        title = words[:1].upper() + words[1:] if words else changes.commits[-1]
    body = ["## Summary", "", *(f"- {subject}" for subject in changes.commits), "", "## Changed files", "",
            *(f"- `{name}`" for name in changes.files)]
    return {"title": title[:200], "body": "\n".join(body)[:8000]}
