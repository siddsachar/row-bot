"""Starting-model weights (D13): pinned, downloaded only after the person agrees.

The rest of Row-Bot offers no model downloads (``models.py``); this is the one exception, inside
personal-model setup. Each starting model is pinned here: its Hugging Face repository, the exact
revision, and every file with its size and SHA-256 (taken from L0d's download of that revision
and checked against the Hub's own fingerprints for the large files). A download fetches exactly
those files from ``huggingface.co/<repo>/resolve/<revision>/<file>`` through the safe download
path (no Hugging Face token, ever; nothing at import), resumes after a Stop or a restart, and is
published only when every file matches: into ``<data>/runtimes/learning/models/<id>/``, with the
model's own licence file (``LICENSE``) and a ``model.json`` that names the source, revision and
licence. **Delete** removes the folder and says how much space it freed.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from row_bot.learning.training import fetch
from row_bot.learning.training.engine import _plain_dir, _remove_tree, _tidy, folder_bytes, learning_runtimes

logger = logging.getLogger(__name__)

FORMAT = 1
MANIFEST = "model.json"


@dataclass(frozen=True)
class WeightFile:
    name: str
    size: int
    sha256: str


@dataclass(frozen=True)
class PinnedModel:
    """A starting model at one exact revision, file by file."""

    id: str
    name: str
    repo: str
    revision: str
    licence: str
    licence_id: str
    files: tuple[WeightFile, ...]

    @property
    def download_bytes(self) -> int:
        return sum(item.size for item in self.files)

    def url(self, item: WeightFile) -> str:
        return f"https://huggingface.co/{self.repo}/resolve/{self.revision}/{item.name}"

    def pinned(self) -> list[fetch.Pinned]:
        return [fetch.Pinned(self.url(item), item.name, item.sha256, item.size) for item in self.files]


# Qwen/Qwen3.5-2B at the revision L0d measured (O23: the certified 8 GB tier's starting model).
QWEN35_2B = PinnedModel(
    id="qwen3.5-2b",
    name="Qwen3.5 2B",
    repo="Qwen/Qwen3.5-2B",
    revision="15852e8c16360a2fea060d615a32b45270f8a8fc",
    licence="Apache 2.0",
    licence_id="apache-2.0",
    files=(
        WeightFile("LICENSE", 11_544, "bbedc3fda3305820b977265f01b8619d87570a6739de3a5582c3464840f1e57a"),
        WeightFile("README.md", 62_814, "c0e83a849c776e6fa843d011f023132d942a0f0140d903205bf1c363adad2275"),
        WeightFile("chat_template.jinja", 7_755, "273d8e0e683b885071fb17e08d71e5f2a5ddfb5309756181681de4f5a1822d80"),
        WeightFile("config.json", 2_908, "ed1c1723241f23f7f4e23430759cbd7dcfb4103cbdfe052bfe7626b57c2615b4"),
        WeightFile("merges.txt", 3_353_259, "a9d356d7bdf1ef4949e3e748e95b8e10ad9d4e2e838eddc38a0a7b6b94d1db8d"),
        WeightFile("model.safetensors-00001-of-00001.safetensors", 4_548_221_488,
                   "aa33250c4fc64891ddfaba3a314fd9542ea371843c387178b425fbcc5ed680b1"),
        WeightFile("model.safetensors.index.json", 64_460,
                   "aca8afed9da75b0f050b408d270766fd77627f1af401e240f61c3b47d0db02f9"),
        WeightFile("preprocessor_config.json", 390, "27225450ac9c6529872ee1924fcb0962ff5634834f817040f444118116f4e516"),
        WeightFile("tokenizer.json", 12_807_982, "5f9e4d4901a92b997e463c1f46055088b6cca5ca61a6522d1b9f64c4bb81cb42"),
        WeightFile("tokenizer_config.json", 16_709, "49e2b6e395f959f077f1e992b338919c0d4a9732fc6e613995e06557f843500c"),
        WeightFile("video_preprocessor_config.json", 385,
                   "7768af27c1fafa9cc9011c1dc20067e03f8915e03b63504550e11d5066986d13"),
        WeightFile("vocab.json", 6_722_759, "ce99b4cb2983d118806ce0a8b777a35b093e2000a503ebde25853284c9dfa003"),
    ),
)
MODELS: dict[str, PinnedModel] = {QWEN35_2B.id: QWEN35_2B}

WeightsState = Literal["not_downloaded", "partial", "downloaded", "damaged"]


@dataclass(frozen=True)
class WeightsStatus:
    state: WeightsState
    bytes: int = 0
    folder: Path | None = None


def models_dir(root: Path | None = None) -> Path:
    return learning_runtimes(root) / "models"


def model_dir(model: PinnedModel, root: Path | None = None) -> Path:
    return models_dir(root) / model.id


def partial_dir(model: PinnedModel, root: Path | None = None) -> Path:
    return models_dir(root) / f".partial-{model.id}-{model.revision[:12]}"


def _manifest(model: PinnedModel) -> dict:
    return {
        "format": FORMAT, "id": model.id, "name": model.name, "source": f"https://huggingface.co/{model.repo}",
        "repo": model.repo, "revision": model.revision, "licence": model.licence, "licence_id": model.licence_id,
        "licence_file": "LICENSE", "files": [{"name": item.name, "bytes": item.size, "sha256": item.sha256}
                                             for item in model.files],
    }


def status(model: PinnedModel, root: Path | None = None) -> WeightsStatus:
    """Downloaded (every pinned file there, at its size), partly downloaded, or not at all."""

    folder = model_dir(model, root)
    if _plain_dir(folder):
        try:
            manifest = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            manifest = None
        expected = _manifest(model)
        good = (isinstance(manifest, dict) and manifest.get("revision") == model.revision
                and manifest.get("files") == expected["files"])
        sizes_ok = good and all((folder / item.name).is_file() and (folder / item.name).stat().st_size == item.size
                                for item in model.files)
        if sizes_ok:
            return WeightsStatus("downloaded", folder_bytes(folder), folder)
        return WeightsStatus("damaged", folder_bytes(folder), folder)
    partial = partial_dir(model, root)
    if _plain_dir(partial):
        done = sum(fetch.on_disk(partial, item) for item in model.pinned())
        if done:
            return WeightsStatus("partial", done)
    return WeightsStatus("not_downloaded")


def downloaded_bytes(model: PinnedModel, root: Path | None = None) -> int:
    """Bytes of the model on disk so far (whole or in progress)."""

    found = status(model, root)
    if found.state == "downloaded":
        return model.download_bytes
    if found.state == "partial":
        return found.bytes
    return 0


def download(
    model: PinnedModel,
    *,
    root: Path | None = None,
    fetcher: fetch.Fetcher | None = None,
    progress: Callable[[int], None] = lambda _bytes: None,
    check: Callable[[], None] = lambda: None,
) -> Path:
    """Download (or finish downloading) ``model`` and publish it. ``progress`` gets the bytes
    on disk so far; ``check`` raises :class:`fetch.Stopped` to stop. Raises ``fetch.FetchError``."""

    found = status(model, root)
    if found.state == "downloaded" and found.folder is not None:
        progress(model.download_bytes)
        return found.folder
    fetcher = fetcher or fetch.Fetcher()
    partial = partial_dir(model, root)
    partial.mkdir(parents=True, exist_ok=True)
    files = model.pinned()
    done = {item.name: fetch.on_disk(partial, item) for item in files}
    progress(sum(done.values()))
    for item in files:
        def moved(current: int, name: str = item.name) -> None:
            done[name] = current
            progress(sum(done.values()))

        fetcher.fetch(item, partial, progress=moved, check=check)
    (partial / MANIFEST).write_text(json.dumps({**_manifest(model), "downloaded_at": time.time()}, indent=1),
                                    encoding="utf-8", newline="\n")
    check()
    target = model_dir(model, root)
    if os.path.lexists(target):
        doomed = target.with_name(f".removing-{uuid.uuid4().hex[:8]}")
        fetch.rename(target, doomed)
        _remove_tree(doomed)
    fetch.rename(partial, target)
    logger.info("Learning downloaded its starting model %s", model.name)
    return target


def delete(model: PinnedModel, root: Path | None = None) -> int:
    """Delete the model (and any partial download). Returns the bytes freed."""

    freed = 0
    for folder in (model_dir(model, root), partial_dir(model, root)):
        if os.path.lexists(folder):
            freed += folder_bytes(folder)
            doomed = folder.with_name(f".removing-{uuid.uuid4().hex[:8]}")
            try:
                fetch.rename(folder, doomed, seconds=5.0)
            except OSError:
                doomed = folder
            _remove_tree(doomed)
    base = models_dir(root)
    try:
        if base.is_dir() and not any(base.iterdir()):
            base.rmdir()
    except OSError:
        pass
    _tidy(root)
    return freed


__all__ = [
    "MODELS",
    "PinnedModel",
    "QWEN35_2B",
    "WeightFile",
    "WeightsStatus",
    "delete",
    "download",
    "downloaded_bytes",
    "model_dir",
    "models_dir",
    "partial_dir",
    "status",
]
