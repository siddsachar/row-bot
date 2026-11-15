"""Personal-model versions the person can see and act on (L2c2; designs/learning-ux.md §3, §5).

- **The versions** on this computer, with their comparison and the kinds of work each answers.
- **Export…** (D14): the version's adapter, its ready-to-run GGUF, its model card and its manifest,
  copied to a folder the person chooses (file names are shown here and in Experiment details, never
  on a main screen, O54).
- **Delete version**: it stops answering everywhere at once (back to the version before, or the main
  model), leaves the runtime Row-Bot registered it in, and its files go.
- **Needs you**: "Personal model v1 is ready to compare", "Keep v1 for document lookups?", and "A
  deleted chat was used to train v1" with **Retrain without it** (O6): deleting a chat removes its
  records and marks the versions trained on it; the next version is planned without that data.
- **The picker** (D11): every ready version is offered as "Personal model v1", tagged Personal.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from row_bot import learning as gate
from row_bot.learning import kinds, use

logger = logging.getLogger(__name__)

NEEDS_FILE = "needs_you.json"
EXPORT_NAME = "row-bot-personal-v{version}"


class VersionError(ValueError):
    """A refused action on a version; ``code`` is stable."""

    def __init__(self, code: str, *, needs: int | None = None, free: int | None = None) -> None:
        self.code = code
        self.needs = needs
        self.free = free
        super().__init__(code)


@dataclass(frozen=True)
class Version:
    version: int
    phase: str
    release_id: str = ""
    model_ref: str | None = None
    runtime: str = "ollama"
    gguf: str | None = None
    gguf_bytes: int = 0
    adapter_bytes: int = 0
    quantization: str = "Q8_0"
    trained_at: float | None = None
    examples: int = 0
    comparison: Mapping[str, Any] | None = None
    progress: Mapping[str, Any] | None = None
    uses: tuple[use.Assignment, ...] = field(default_factory=tuple)

    @property
    def ready(self) -> bool:
        from row_bot.learning.training import releases

        return self.phase == releases.READY and bool(self.model_ref)


def all_versions(root: Path | None = None) -> list[Version]:
    """Every version on this computer, newest first."""

    from row_bot.learning import model_compare
    from row_bot.learning.training import releases

    folder = releases.versions_dir(root)
    if not folder.is_dir():
        return []
    state_of_use = use.read(root)
    found: list[Version] = []
    for entry in folder.iterdir():
        if not (entry.is_dir() and entry.name.startswith("v") and entry.name[1:].isdigit()):
            continue
        state = releases.read_state(entry)
        if state is None:
            continue
        found.append(Version(
            version=state.version, phase=state.phase, release_id=state.release_id, model_ref=state.model_ref,
            runtime=state.runtime, gguf=state.gguf, gguf_bytes=state.gguf_bytes, adapter_bytes=state.adapter_bytes,
            quantization=state.quantization, trained_at=state.trained_at, examples=state.examples,
            comparison=model_compare.read_comparison(state.version, root),
            progress=model_compare.read_progress(state.version, root),
            uses=tuple(item for item in state_of_use.assignments.values() if item.version == state.version),
        ))
    return sorted(found, key=lambda item: item.version, reverse=True)


def get(version: int, root: Path | None = None) -> Version | None:
    return next((item for item in all_versions(root) if item.version == int(version)), None)


def ready_ref(version: int, root: Path | None = None) -> str | None:
    found = get(version, root)
    return found.model_ref if found is not None and found.ready else None


# ── The picker ──────────────────────────────────────────────────────────────


def offered(root: Path | None = None) -> dict[str, str]:
    """``{model reference: "Personal model v1"}`` for every ready version (D11)."""

    return {item.model_ref: f"Personal model v{item.version}" for item in all_versions(root)
            if item.ready and item.model_ref}


# ── Export… ─────────────────────────────────────────────────────────────────


def suggested_folder() -> str:
    """Where Export… offers to save: Documents (or the home folder) › Row-Bot personal models."""

    home = Path.home()
    documents = home / "Documents"
    return str((documents if documents.is_dir() else home) / "Row-Bot personal models")


def export_files(version: int, root: Path | None = None) -> list[tuple[str, Path, int]]:
    """(name in the export, source, bytes) of what Export… copies: adapter, GGUF, card, manifest."""

    from row_bot.learning.training import export as export_module, releases

    found = get(version, root)
    if found is None:
        raise VersionError("version_missing")
    folder = releases.version_dir(version, root)
    files: list[tuple[str, Path, int]] = []
    for name in export_module.ADAPTER_FILES:
        source = folder / "adapter" / name
        if source.is_file():
            files.append((f"adapter/{name}", source, source.stat().st_size))
    if found.gguf and (folder / found.gguf).is_file():
        files.append((found.gguf, folder / found.gguf, (folder / found.gguf).stat().st_size))
    for name in (releases.MODEL_CARD_FILE, releases.MANIFEST_FILE):
        if (folder / name).is_file():
            files.append((name, folder / name, (folder / name).stat().st_size))
    if not any(name.endswith(".gguf") for name, _, _ in files):
        raise VersionError("files_missing")
    return files


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def export_version(version: int, folder: str, *, root: Path | None = None,
                   disk_free: Callable[[Path], int | None] | None = None) -> dict[str, Any]:
    """Copy the version's files into ``folder``/row-bot-personal-v<N> (a new folder each time)."""

    from row_bot.learning.training import export as export_module

    text = str(folder or "").strip()
    if not text or len(text) > 1024 or "\0" in text:
        raise VersionError("invalid_folder")
    target_root = Path(text).expanduser()
    if not target_root.is_absolute():
        raise VersionError("invalid_folder")
    data = root if root is not None else gate.data_dir()
    if _inside(target_root, data):
        raise VersionError("inside_data_folder")
    files = export_files(version, root)
    total = sum(size for _, _, size in files)
    try:
        target_root.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise VersionError("folder_unavailable") from error
    free = (disk_free or export_module.free_bytes)(target_root)
    if free is not None and free < total + 50_000_000:
        raise VersionError("not_enough_disk", needs=total + 50_000_000, free=free)
    base = EXPORT_NAME.format(version=int(version))
    destination = target_root / base
    counter = 2
    while destination.exists():
        destination = target_root / f"{base} ({counter})"
        counter += 1
    partial = target_root / f".{base}.{uuid.uuid4().hex}.partial"
    try:
        from row_bot.learning import atomic

        for name, source, _ in files:
            path = partial / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, path)
        atomic.replace(partial, destination)
    except OSError as error:
        shutil.rmtree(partial, ignore_errors=True)
        raise VersionError("copy_failed") from error
    return {"folder": str(destination), "files": [{"name": name, "bytes": size} for name, _, size in files],
            "bytes": total}


# ── Delete version ──────────────────────────────────────────────────────────


def delete_version(version: int, *, root: Path | None = None, jobs: Any = None,
                   withdraw: Callable[..., None] | None = None, clock: Callable[[], float] = time.time) -> dict[str, Any]:
    """Delete a version: Undo everywhere it answers, out of Ollama, its files gone. Its release record
    stays (retired) for the archive; its job is settled (not used), so a version that was never ready
    isn't made again. The starting model's own copy stays while training in quiet hours is on (the next
    version is compared with it)."""

    from dataclasses import replace

    from row_bot.learning import contracts
    from row_bot.learning.training import releases, serving

    found = get(version, root)
    if found is None:
        raise VersionError("version_missing")
    undone = use.forget_version(int(version), root=root, clock=clock,
                                still_ready=lambda other: ready_ref(other, root) if other != int(version) else None)
    if found.runtime == "ollama" and found.phase in (releases.READY, releases.MAKING_READY, releases.RUNTIME_FAILED):
        try:
            (withdraw or serving.withdraw)("ollama", int(version), root=root, catalog=serving.refresh_ollama_catalog)
        except Exception:  # noqa: BLE001 - the files still go; Ollama's copy is retried by nothing
            logger.warning("Learning couldn't remove personal model v%s from Ollama", version, exc_info=True)
    if gate.store_exists(root):
        from row_bot.learning.store import LearningStore

        store = LearningStore(root)
        release = store.get("release", found.release_id) if found.release_id else None
        if isinstance(release, contracts.Release) and release.status != "retired":
            store.put(replace(release, status="retired"))
        jobs = jobs or _jobs(root)
        for job in jobs.all():
            if job.version == int(version) and job.state in ("evaluating", "ready", "trial", "in_use"):
                try:
                    jobs.move(job.id, "not_used", reason="deleted")
                except Exception:  # noqa: BLE001
                    logger.debug("Learning couldn't settle a deleted version's job", exc_info=True)
    folder = releases.version_dir(int(version), root)
    freed = sum(path.stat().st_size for path in folder.rglob("*") if path.is_file()) if folder.is_dir() else 0
    shutil.rmtree(folder, ignore_errors=True)
    if not all_versions(root) and not gate.read_settings(root).train_in_quiet_hours:
        _remove_base(root)
    return {"undone": undone, "freed": freed}


def _jobs(root: Path | None) -> Any:
    from row_bot.learning.training.jobs import TrainingJobs

    return TrainingJobs(root)


def _remove_base(root: Path | None) -> None:
    from row_bot.learning.training import base_model, weights

    try:
        model = weights.MODELS.get(gate.read_settings(root).starting_model, weights.QWEN35_9B)
        if base_model.read(model.id, root) is not None:
            base_model.withdraw(model.id, root=root, remove_files=True)
    except Exception:  # noqa: BLE001
        logger.debug("Learning couldn't remove the starting model's copy", exc_info=True)


# ── Needs you ───────────────────────────────────────────────────────────────


def _needs_path(root: Path | None) -> Path:
    from row_bot.learning.training import jobs as jobs_module

    return jobs_module.models_dir(root) / NEEDS_FILE


def dismissed(root: Path | None = None) -> dict[str, float]:
    try:
        data = json.loads(_needs_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(key): float(value) for key, value in (data or {}).items()
            if isinstance(value, (int, float))} if isinstance(data, dict) else {}


def dismiss(item_id: str, *, root: Path | None = None, clock: Callable[[], float] = time.time) -> None:
    from row_bot.learning import atomic

    found = dismissed(root)
    found[str(item_id)[:128]] = round(float(clock()), 3)
    atomic.write_text(_needs_path(root), json.dumps(dict(sorted(found.items())[-500:]), indent=1))


def deleted_chat_marks(version: Version, root: Path | None = None) -> list[float]:
    """When chats this version was trained on were deleted (or switched off): mark times."""

    if not version.release_id or not gate.store_exists(root):
        return []
    from row_bot.learning.store import MARK_CHAT_DELETED, MARK_CHAT_EXCLUDED, LearningStore

    try:
        marks = LearningStore(root).marks("release", version.release_id)
    except Exception:  # noqa: BLE001
        return []
    return sorted(mark.marked_at for mark in marks if mark.mark in (MARK_CHAT_DELETED, MARK_CHAT_EXCLUDED))


def needs_you(root: Path | None = None, *, jobs: Any = None, now: float | None = None) -> list[dict[str, Any]]:
    """What Overview's Needs you shows for the personal model, newest first."""

    moment = time.time() if now is None else float(now)
    done = dismissed(root)
    found: list[dict[str, Any]] = []
    versions = all_versions(root)
    for item in versions:
        comparison = item.comparison
        key = f"compare:v{item.version}"
        if item.ready and comparison is not None and not comparison.get("reviewed_at") and key not in done:
            found.append({"id": key, "type": "compare", "version": item.version, "kind": None,
                          "title": f"Personal model v{item.version} is ready to compare",
                          "at": float(comparison.get("compared_at") or 0)})
    for assignment in use.read(root).assignments.values():
        if use.keep_due(assignment, now=moment):
            found.append({"id": f"keep:{assignment.kind}", "type": "keep", "version": assignment.version,
                          "kind": assignment.kind,
                          "title": f"Keep v{assignment.version} for {kinds.phrase(assignment.kind)}?",
                          "at": assignment.since})
    newest_planned = 0.0
    newest_version = 0
    try:
        all_jobs = (jobs or _jobs(root)).all() if gate.store_exists(root) else []
        for job in all_jobs:
            if job.state not in ("failed", "stopped"):
                newest_planned = max(newest_planned, job.created_at)
                newest_version = max(newest_version, int(job.version or 0))
    except Exception:  # noqa: BLE001
        all_jobs = []
    for item in versions:
        marks = deleted_chat_marks(item, root)
        key = f"retrain:v{item.version}"
        if not marks or item.version < newest_version and newest_planned > marks[-1]:
            continue
        if done.get(key, 0) >= marks[-1]:
            continue
        found.append({"id": key, "type": "retrain", "version": item.version, "kind": None,
                      "title": f"A deleted chat was used to train v{item.version}.",
                      "at": marks[-1]})
    return sorted(found, key=lambda entry: entry["at"], reverse=True)


def retrain_without(version: int, *, planner: Any, root: Path | None = None,
                    clock: Callable[[], float] = time.time) -> Any:
    """"Retrain without it": plan the next version now, without the deleted chat's examples (they are
    already gone from the data). The Needs you line goes once a newer version is planned."""

    if get(version, root) is None:
        raise VersionError("version_missing")
    outcome = planner.plan(force=True, retrain=True)
    if outcome.job is not None:
        dismiss(f"retrain:v{int(version)}", root=root, clock=clock)
    return outcome


__all__ = [
    "Version",
    "VersionError",
    "all_versions",
    "delete_version",
    "deleted_chat_marks",
    "dismiss",
    "dismissed",
    "export_files",
    "export_version",
    "get",
    "needs_you",
    "offered",
    "ready_ref",
    "retrain_without",
    "suggested_folder",
]
