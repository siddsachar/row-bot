"""The exact rows a version trained on, kept with it (C1, A10): what the paper's exploratory 27B uploads.

A version's ``dataset.json`` names its rows by id and their SHA-256, but the rows themselves were only in the
run's lease folder. Rebuilding them later depends on the build (the served shape renders Row-Bot's own prompt,
tools and skills into every row) and on the person's settings then, so a version trained by one build could
not be rebuilt by the next (28 Oct). Now the release keeps ``rows/examples.jsonl`` and ``rows/validation.jsonl``
beside the version, written only when they match the version's own SHA-256, and :func:`read` hands them back
only while they still match and every conversation they came from still exists.

They are learning data like the rest of ``learning_models/``: deleted with the version, with Delete learning
data, and as soon as a conversation they came from is deleted or switched off (:func:`forget`). About 50 KB a
row (the 9B's 90 seeded rows: 5 MB).
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

FOLDER = "rows"
EXAMPLES = "examples.jsonl"
VALIDATION = "validation.jsonl"


def _rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def digest(manifest: Mapping[str, Any], train: list[Any], validation: list[Any]) -> str:
    """The SHA-256 the compiler gives these rows (``dataset.compile_dataset``'s body, canonical JSON)."""

    # The manifest names the compiler's version ``version`` (``dataset.CompiledDataset.manifest``).
    body = {"compiler": manifest.get("version"), "recipe": manifest.get("recipe"),
            "max_seq_length": manifest.get("max_seq_length"), "rendering": _rendering(manifest),
            "train": train, "validation": validation}
    text = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _rendering(manifest: Mapping[str, Any]) -> Any:
    rendering = manifest.get("rendering")
    return rendering.get("name") if isinstance(rendering, Mapping) else rendering


def keep(data: Path, version_folder: Path, manifest: Mapping[str, Any] | None) -> bool:
    """Keep the run's rows with its version when they are the rows the version records (else nothing)."""

    if not manifest or not manifest.get("sha256"):
        return False
    try:
        train, validation = _rows(Path(data) / EXAMPLES), _rows(Path(data) / VALIDATION)
    except (OSError, ValueError):
        return False
    if digest(manifest, train, validation) != manifest.get("sha256"):
        logger.warning("Learning didn't keep a version's rows: they don't match its record")
        return False
    target = Path(version_folder) / FOLDER
    temporary = Path(version_folder) / f".{FOLDER}.tmp"
    shutil.rmtree(temporary, ignore_errors=True)
    temporary.mkdir(parents=True)
    for name in (EXAMPLES, VALIDATION):
        shutil.copyfile(Path(data) / name, temporary / name)
    shutil.rmtree(target, ignore_errors=True)
    temporary.replace(target)
    return True


def read(version_folder: Path, manifest: Mapping[str, Any] | None, *,
         exists: Callable[[str], bool] | None = None) -> tuple[str, list[Any], list[Any]]:
    """``(state, train, validation)``: ``kept`` with the rows when they match the version's SHA-256 and every
    source conversation still exists (``exists``); else ``not_kept`` (a version from before this),
    ``changed`` (they don't match) or ``source_deleted`` (a conversation they came from is gone)."""

    folder = Path(version_folder) / FOLDER
    if not manifest or not (folder / EXAMPLES).is_file():
        return "not_kept", [], []
    try:
        train, validation = _rows(folder / EXAMPLES), _rows(folder / VALIDATION)
    except (OSError, ValueError):
        return "changed", [], []
    if digest(manifest, train, validation) != manifest.get("sha256"):
        return "changed", [], []
    if exists is not None and not all(exists(str(item)) for item in manifest.get("conversations") or ()):
        return "source_deleted", [], []
    return "kept", train, validation


def forget(thread_ids: Iterable[str], root: Path | None = None) -> int:
    """A conversation was deleted or switched off: every version's kept rows that came from it go."""

    from row_bot.learning.training import releases

    wanted = {str(item) for item in thread_ids if item}
    folder = releases.versions_dir(root)
    removed = 0
    if not wanted or not folder.is_dir():
        return 0
    for entry in sorted(folder.iterdir()):
        rows = entry / FOLDER
        if not rows.is_dir():
            continue
        try:
            manifest = json.loads((entry / releases.DATASET_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            manifest = {}
        if not isinstance(manifest, dict) or wanted & set(manifest.get("conversations") or ()) or \
                not manifest.get("conversations"):
            shutil.rmtree(rows, ignore_errors=True)
            removed += 1
    return removed


__all__ = ["EXAMPLES", "FOLDER", "VALIDATION", "digest", "forget", "keep", "read"]
