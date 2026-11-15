"""Fakes for any open model (C1 Part 4, O138): Hugging Face's public pages and the check's steps, in memory.

:class:`FakeHubPages` stands in for Row-Bot's safe fetch to ``huggingface.co`` (search, a model's card, its file
tree and small files), so no test or browser spec reaches the network; :class:`FakeSteps` stands in for the check's
work on this computer (the download, the trial on the card, the export and the tool sample), so nothing needs a GPU.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from row_bot.learning.training import open_models

REV = "40c069824f4251a91eefaf281ebe4c544efd3e18"
CONFIG = b'{"architectures": ["Qwen3ForCausalLM"], "model_type": "qwen3"}'


def card(repo: str = "Qwen/Qwen3-14B", **extra: Any) -> dict[str, Any]:
    """A model's card as Hugging Face's API answers it (``/api/models/<repo>``), Qwen3-14B unless changed."""

    found = {"id": repo, "sha": REV, "gated": False, "pipeline_tag": "text-generation", "library_name": "transformers",
             "config": {"architectures": ["Qwen3ForCausalLM"], "model_type": "qwen3"},
             "cardData": {"license": "apache-2.0"}, "safetensors": {"total": 14_768_307_200}, "downloads": 11_000_000,
             "tags": ["qwen3", "license:apache-2.0"]}
    found.update(extra)
    return found


def tree() -> list[dict[str, Any]]:
    """A model's files at its revision (``/api/models/<repo>/tree/<revision>``)."""

    return [
        {"type": "file", "path": ".gitattributes", "size": 1570, "oid": "x"},
        {"type": "file", "path": "README.md", "size": 16670, "oid": "x"},
        {"type": "file", "path": "LICENSE", "size": 5, "oid": open_models.git_blob_id(b"terms")},
        {"type": "file", "path": "config.json", "size": len(CONFIG), "oid": open_models.git_blob_id(CONFIG)},
        {"type": "file", "path": "model-00001-of-00002.safetensors", "size": 3_841_788_544,
         "oid": "y", "lfs": {"oid": "a" * 64, "size": 3_841_788_544}},
        {"type": "file", "path": "model-00002-of-00002.safetensors", "size": 1_000, "oid": "y",
         "lfs": {"oid": "b" * 64, "size": 1_000}},
        {"type": "directory", "path": "original", "oid": "z"},
    ]


class FakeHubPages:
    """A reader (``open_models.Reader``) answering from ``pages``: the first key found in the address wins."""

    def __init__(self, pages: dict[str, Any]) -> None:
        self.pages = pages
        self.asked: list[str] = []

    def __call__(self, url: str, max_bytes: int) -> bytes:
        self.asked.append(url)
        for key, value in self.pages.items():
            if key in url:
                if isinstance(value, Exception):
                    raise value
                return value if isinstance(value, bytes) else json.dumps(value).encode("utf-8")
        raise open_models.HubError("not_found")

    def add(self, found: dict[str, Any]) -> FakeHubPages:
        """One more model: its card, its file tree and its small files."""

        repo = found["id"]
        self.pages.update({f"/api/models/{repo}/tree/{REV}": tree(), f"/{repo}/resolve/{REV}/LICENSE": b"terms",
                           f"/{repo}/resolve/{REV}/config.json": CONFIG})
        self.pages[f"/api/models/{repo}"] = found
        return self


def hub(found: dict[str, Any] | None = None) -> FakeHubPages:
    return FakeHubPages({}).add(found or card())


class FakeSteps(open_models.Steps):
    """The check's work on this computer, played as told: whether the trial fits (at full and short length), its
    losses and gradients, whether the export converts, which tool tasks come back right; ``stop_at`` stops the
    check there (with ``checker``, a list the check's :class:`open_models.Checker` is put in)."""

    def __init__(self, tmp: Path, *, fits: bool = True, short_fits: bool = True,
                 losses=(2.1, 1.9, 1.8, 1.6), grads=(1.2, 0.9, 0.8, 0.7), export_ok: bool = True,
                 tools=(True, True, False), card_mib: int = 32_607, stop_at: str | None = None,
                 checker: list | None = None) -> None:
        self.tmp, self.fits, self.short_fits = tmp, fits, short_fits
        self.losses, self.grads, self.export_ok, self.tools = losses, grads, export_ok, tools
        self.card_mib, self.stop_at, self.calls = card_mib, stop_at, []
        self.checker = checker

    def _maybe_stop(self, name: str) -> None:
        if self.stop_at == name and self.checker:
            self.checker[0].stop()

    def pin(self, record, check):
        self.calls.append("pin")
        return [{"name": "config.json", "size": 10, "sha256": "c" * 64},
                {"name": "model.safetensors", "size": 100, "sha256": "d" * 64}]

    def download(self, model, progress, check):
        self.calls.append(("download", model.repo, model.revision))
        self._maybe_stop("download")
        check()
        progress(110)
        return self.tmp / "weights"

    def card(self):
        return self.card_mib, 0.8

    def fit(self, weights_dir, *, max_seq, cap_share, work, check):
        self.calls.append(("fit", max_seq))
        check()
        ran = self.fits if max_seq == open_models.FULL_SEQ else self.short_fits
        return open_models.FitResult(ran=ran, peak_mib=21_712 if ran else 26_044, cap_mib=int(self.card_mib * cap_share),
                                     losses=tuple(self.losses) if ran else (), grad_norms=tuple(self.grads) if ran else (),
                                     step_seconds=31.7, adapter=work / "adapter")

    def export(self, weights_dir, adapter, work, check):
        self.calls.append("export")
        self._maybe_stop("export")
        check()
        if not self.export_ok:
            raise RuntimeError("convert")
        gguf = work / "row-bot-check-Q4_K_M.gguf"
        gguf.write_bytes(b"g" * 2048)
        return gguf

    def tool_sample(self, gguf, work, check):
        self.calls.append("tools")
        return [{"id": item["id"], "prompt": item["prompt"], "passed": ok, "tools": ["calculator"] if ok else [],
                 "answered": ok} for item, ok in zip(open_models.TOOL_SAMPLE, self.tools)]


__all__ = ["CONFIG", "REV", "FakeHubPages", "FakeSteps", "card", "hub", "tree"]
