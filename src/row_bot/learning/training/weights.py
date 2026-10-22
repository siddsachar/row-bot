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
    # Another repository's file at its own pinned revision (a training checkpoint takes the starting
    # model's tokenizer and chat template, so it trains on rows rendered as the version is served).
    repo: str = ""
    revision: str = ""


@dataclass(frozen=True)
class PinnedModel:
    """A starting model at one exact revision, file by file.

    ``training``: the checkpoint the trainer loads when it isn't these files (O105: Qwen3.8 27B trains
    from Unsloth's pre-quantized 4-bit checkpoint, because quantizing the 16-bit weights while loading
    them holds them in the computer's memory). The 16-bit files stay: the export merges the trained
    adapter into them and the starting model's own GGUF is made from them. Both are one download."""

    id: str
    name: str
    repo: str
    revision: str
    licence: str
    licence_id: str
    files: tuple[WeightFile, ...]
    training: PinnedModel | None = None

    @property
    def download_bytes(self) -> int:
        """Everything the model downloads, its training checkpoint included."""

        return sum(item.size for item in self.files) + (self.training.download_bytes if self.training else 0)

    @property
    def weights_bytes(self) -> int:
        """Bytes of its weights (every ``.safetensors`` shard): what a merge holds in memory."""

        return sum(item.size for item in self.files if item.name.endswith(".safetensors"))

    def url(self, item: WeightFile) -> str:
        return f"https://huggingface.co/{item.repo or self.repo}/resolve/{item.revision or self.revision}/{item.name}"

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
# The larger tiers' starting models (L2x, O90), each file checked against the Hub's own fingerprints at
# the pinned revision (large files by their LFS SHA-256, small ones by their git blob id) by
# ``scripts/learning_gpu_check.py`` on the RTX 5090, then hashed for these pins.
QWEN35_9B = PinnedModel(
    id="qwen3.5-9b",
    name="Qwen3.5 9B",
    repo="Qwen/Qwen3.5-9B",
    revision="c202236235762e1c871ad0ccb60c8ee5ba337b9a",
    licence="Apache 2.0",
    licence_id="apache-2.0",
    files=(
        WeightFile("LICENSE", 11_544, "bbedc3fda3305820b977265f01b8619d87570a6739de3a5582c3464840f1e57a"),
        WeightFile("README.md", 77_643, "c5f5a8c2dddab69cfbf05279235aa5fddb137939a06539c4c7637aa900fef6d0"),
        WeightFile("chat_template.jinja", 7_756, "a4aee8afcf2e0711942cf848899be66016f8d14a889ff9ede07bca099c28f715"),
        WeightFile("config.json", 3_126, "d0883072e01861ed0b2d47be3c16c36a8e81c224c7ffaa310c6558fb3f932b05"),
        WeightFile("merges.txt", 3_353_259, "a9d356d7bdf1ef4949e3e748e95b8e10ad9d4e2e838eddc38a0a7b6b94d1db8d"),
        WeightFile("model.safetensors-00001-of-00004.safetensors", 5_276_436_216, "db6f444b43d318c92f360a13a25561a6a65b10c0631b8ed305a426dbaa6c380e"),
        WeightFile("model.safetensors-00002-of-00004.safetensors", 5_335_161_512, "31c7d7e2dd5d207840b31cc59083c8f4c4718959149e0358c0364052bb9a0330"),
        WeightFile("model.safetensors-00003-of-00004.safetensors", 5_368_717_440, "7ec36ba3a4176a44c3c0876ad80c56a2f70c84bf008d82e9501df642f17dadec"),
        WeightFile("model.safetensors-00004-of-00004.safetensors", 3_325_995_712, "b62b0c4cd7e44edee103ee8f4fe225f246d5e768e07bfd5f25b63a8aa1fdd0c6"),
        WeightFile("model.safetensors.index.json", 79_657, "26d3539b516be613f39563617cb9d33b3f83d401298125be392c80cefb8f7fe5"),
        WeightFile("preprocessor_config.json", 390, "27225450ac9c6529872ee1924fcb0962ff5634834f817040f444118116f4e516"),
        WeightFile("tokenizer.json", 12_807_982, "5f9e4d4901a92b997e463c1f46055088b6cca5ca61a6522d1b9f64c4bb81cb42"),
        WeightFile("tokenizer_config.json", 16_710, "316230d6a809701f4db5ea8f8fc862bc3a6f3229c937c174e674ff3ca0a64ac8"),
        WeightFile("video_preprocessor_config.json", 385, "7768af27c1fafa9cc9011c1dc20067e03f8915e03b63504550e11d5066986d13"),
        WeightFile("vocab.json", 6_722_759, "ce99b4cb2983d118806ce0a8b777a35b093e2000a503ebde25853284c9dfa003"),
    ),
)
QWEN38_27B = PinnedModel(
    id="qwen3.8-27b",
    name="Qwen3.8 27B",
    repo="Qwen/Qwen3.8-27B",
    revision="1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0",
    licence="Apache 2.0",
    licence_id="apache-2.0",
    files=(
        WeightFile("LICENSE", 11_544, "bbedc3fda3305820b977265f01b8619d87570a6739de3a5582c3464840f1e57a"),
        WeightFile("README.md", 65_012, "57e4bdb258ee1a7d2635c5174ebd4e56abe392505cdb5f8bbb356b0dc4293641"),
        WeightFile("chat_template.jinja", 8_952, "c3cf9e34abf4f9e36c2d72165aa9c132d3e2a725b6c2586aaa3a8af9d7a81041"),
        WeightFile("config.json", 4_312, "191e0af232104ed8b65258cf3fb2b842e288008baca7633c11b82a1ac7203aab"),
        WeightFile("crc32.txt", 238, "b42dd291f5f98b05807e458f5b47849969a9b3326acbf0c97d28b05826740b83"),
        WeightFile("generation_config.json", 202, "e70c136c1b78ddc1fb0905bac8e733a4dc448d4f852a5dd75143fffc70be550e"),
        WeightFile("merges.txt", 3_353_259, "a9d356d7bdf1ef4949e3e748e95b8e10ad9d4e2e838eddc38a0a7b6b94d1db8d"),
        WeightFile("model-00001-of-00018.safetensors", 3_966_730_552, "ba0ce20aae489ad196733da5064bcdf159a1fe84f53336648196e1ebb7751b1c"),
        WeightFile("model-00002-of-00018.safetensors", 3_043_080_328, "06a148c01bfbe3faa14a5f184a7ff29a706f7ae1c8b2705d2058e26d17a001fb"),
        WeightFile("model-00003-of-00018.safetensors", 2_542_796_952, "2e1bf62cbcd406eaa64b60d10353e1f0ef4039d0976e56f05cabe953454f9968"),
        WeightFile("model-00004-of-00018.safetensors", 3_988_973_152, "511e34063187882659753c4d93f3859f93c019fd438d8813071921c81d9a3f1a"),
        WeightFile("model-00005-of-00018.safetensors", 2_099_339_864, "635cb53446dc74f219740fc59e18b774f877b803b9722e289ca62575a6efa701"),
        WeightFile("model-00006-of-00018.safetensors", 3_979_553_696, "0bc5214fac607f0e6cc92eec3789d4b8559410ef9fce66621ba8158e8410dae0"),
        WeightFile("model-00007-of-00018.safetensors", 2_108_759_344, "80b0c49033e9a0d5762562aa12f4acdb7f54da586f3d0110f28c48d91cf07892"),
        WeightFile("model-00008-of-00018.safetensors", 3_979_553_696, "7192c5b66185d3592927daabee1cc19e6f6e0ce75988ee20e824b624765fda79"),
        WeightFile("model-00009-of-00018.safetensors", 2_108_759_344, "af3c48cc37af44f3db6ae0579baf019180d48d9c527caa0a1f03ff85813a56d8"),
        WeightFile("model-00010-of-00018.safetensors", 3_979_553_696, "163490a76f3bea3a40855b7efc04ce6d27afaf1a34f0bbde495b9491f76457c9"),
        WeightFile("model-00011-of-00018.safetensors", 2_108_759_344, "5f3ae1b948aeee39da77aec558e8236cd65fe4d7cb7686a76bb007acc563c6d8"),
        WeightFile("model-00012-of-00018.safetensors", 3_979_553_696, "a3de1c7114677a8f5ac5c4892c90e8238ea5c1e2038c80e757dfc87c3902ca55"),
        WeightFile("model-00013-of-00018.safetensors", 2_108_759_344, "06ab79a41f74c9c5cb734816feb0c7fc364104b227165ee7391231e1155aa02a"),
        WeightFile("model-00014-of-00018.safetensors", 3_979_553_696, "4138ed94603065ba884bbcadedb04d7718bb40117e85e6f5c6fc5b9c05b7a85b"),
        WeightFile("model-00015-of-00018.safetensors", 2_108_759_344, "69224e27b9de4e7dbf6fc936c6eaae08447bda3b80a6c31a871ab451173afd22"),
        WeightFile("model-00016-of-00018.safetensors", 3_979_564_040, "73cb9a1089fb6155cb648609478d6633be8a5c7d9ca5a05bc8925ce8a553cefe"),
        WeightFile("model-00017-of-00018.safetensors", 2_108_759_344, "beb51f01056142ac4984bd800507b0dd0fd18de57f8e9ef6ea41d1a3598983a8"),
        WeightFile("model-00018-of-00018.safetensors", 3_392_197_344, "1d3479509e21494658f9b64d317f5ea8e55c4025d28c702d6c4d0b356ce8ea06"),
        WeightFile("model.safetensors.index.json", 112_216, "77042094076611b69791a610065f28b7013b8c621795fa86ddccc8bac7d1b9df"),
        WeightFile("preprocessor_config.json", 390, "27225450ac9c6529872ee1924fcb0962ff5634834f817040f444118116f4e516"),
        WeightFile("tokenizer.json", 12_809_320, "0997f410c57a1f4e53b09e4be8f4a172d90edd9564368fb0847030937229b9f3"),
        WeightFile("tokenizer_config.json", 17_928, "b11349aafa7cdc6a320767cf7ceb29ed82f7eda5d65e8e0819e76f0ce947bf27"),
        WeightFile("video_preprocessor_config.json", 385, "7768af27c1fafa9cc9011c1dc20067e03f8915e03b63504550e11d5066986d13"),
        WeightFile("vocab.json", 6_722_759, "ce99b4cb2983d118806ce0a8b777a35b093e2000a503ebde25853284c9dfa003"),
    ),
    # The 32 GB tier trains from Unsloth's pre-quantized 4-bit checkpoint (its weights and config: NF4 with
    # double quantization), with Qwen's own licence, tokenizer and chat template from the revision above,
    # so the rows it trains on are rendered as the version is served (O96, O105; L2x).
    training=PinnedModel(
        id="qwen3.8-27b-train-4bit",
        name="Qwen3.8 27B, 4-bit for training",
        repo="unsloth/Qwen3.8-27B-unsloth-bnb-4bit",
        revision="8aa5f05d26b7205477066e1449e0af13f762a299",
        licence="Apache 2.0",
        licence_id="apache-2.0",
        files=(
            WeightFile("config.json", 4_291, "2d239652486841d13eab478b738beaa43bac88e2052f9b4c5afbefd94e59522a"),
            WeightFile("model.safetensors", 22_344_833_801,
                       "d5ced71fee428687bca87ed1b62a1518e936e8698b180fbba35431ebd31e1a25"),
            *(WeightFile(name, size, sha, "Qwen/Qwen3.8-27B", "1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0")
              for name, size, sha in (
                ("LICENSE", 11_544, "bbedc3fda3305820b977265f01b8619d87570a6739de3a5582c3464840f1e57a"),
                ("chat_template.jinja", 8_952, "c3cf9e34abf4f9e36c2d72165aa9c132d3e2a725b6c2586aaa3a8af9d7a81041"),
                ("generation_config.json", 202, "e70c136c1b78ddc1fb0905bac8e733a4dc448d4f852a5dd75143fffc70be550e"),
                ("merges.txt", 3_353_259, "a9d356d7bdf1ef4949e3e748e95b8e10ad9d4e2e838eddc38a0a7b6b94d1db8d"),
                ("preprocessor_config.json", 390, "27225450ac9c6529872ee1924fcb0962ff5634834f817040f444118116f4e516"),
                ("tokenizer.json", 12_809_320, "0997f410c57a1f4e53b09e4be8f4a172d90edd9564368fb0847030937229b9f3"),
                ("tokenizer_config.json", 17_928, "b11349aafa7cdc6a320767cf7ceb29ed82f7eda5d65e8e0819e76f0ce947bf27"),
                ("video_preprocessor_config.json", 385,
                 "7768af27c1fafa9cc9011c1dc20067e03f8915e03b63504550e11d5066986d13"),
                ("vocab.json", 6_722_759, "ce99b4cb2983d118806ce0a8b777a35b093e2000a503ebde25853284c9dfa003"),
            )),
        ),
    ),
)
MODELS: dict[str, PinnedModel] = {model.id: model for model in (QWEN35_2B, QWEN35_9B, QWEN38_27B)}

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
        "licence_file": "LICENSE",
        "files": [{"name": item.name, "bytes": item.size, "sha256": item.sha256,
                   **({"repo": item.repo, "revision": item.revision} if item.repo else {})} for item in model.files],
    }


def status(model: PinnedModel, root: Path | None = None) -> WeightsStatus:
    """Downloaded (every pinned file there, at its size), partly downloaded, or not at all; a model with
    a training checkpoint is downloaded when both are (``folder`` is the starting model's own)."""

    own = _status(model, root)
    if model.training is None:
        return own
    extra = _status(model.training, root)
    states = {own.state, extra.state}
    if states == {"downloaded"}:
        return WeightsStatus("downloaded", own.bytes + extra.bytes, own.folder)
    if "damaged" in states:
        return WeightsStatus("damaged", own.bytes + extra.bytes, own.folder if own.state == "damaged" else extra.folder)
    done = sum(sum(item.size for item in part.files) if found.state == "downloaded" else found.bytes
               for part, found in ((model, own), (model.training, extra)) if found.state in ("downloaded", "partial"))
    return WeightsStatus("partial", done) if done else WeightsStatus("not_downloaded")


def training_dir(model: PinnedModel, root: Path | None = None) -> Path:
    """The folder the trainer loads: the training checkpoint's when the model has one."""

    return model_dir(model.training or model, root)


def _status(model: PinnedModel, root: Path | None = None) -> WeightsStatus:
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
    """Download (or finish downloading) ``model`` and publish it, its training checkpoint first.
    ``progress`` gets the bytes on disk so far; ``check`` raises :class:`fetch.Stopped` to stop.
    Raises ``fetch.FetchError``. Returns the starting model's own folder."""

    fetcher = fetcher or fetch.Fetcher()
    earlier = 0
    if model.training is not None:
        _download(model.training, root, fetcher, progress, check)
        earlier = model.training.download_bytes
    return _download(model, root, fetcher, lambda current: progress(earlier + current), check)


def _download(model: PinnedModel, root: Path | None, fetcher: fetch.Fetcher, progress: Callable[[int], None],
              check: Callable[[], None]) -> Path:
    found = _status(model, root)
    if found.state == "downloaded" and found.folder is not None:
        progress(sum(item.size for item in model.files))
        return found.folder
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
    """Delete the model, its training checkpoint and any partial download. Returns the bytes freed."""

    freed = 0
    parts = (model, model.training) if model.training is not None else (model,)
    for folder in (path for part in parts for path in (model_dir(part, root), partial_dir(part, root))):
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
    "QWEN35_9B",
    "QWEN38_27B",
    "WeightFile",
    "WeightsStatus",
    "delete",
    "download",
    "downloaded_bytes",
    "model_dir",
    "models_dir",
    "partial_dir",
    "status",
    "training_dir",
]
