"""Freeing the graphics card for quiet-hours training: Row-Bot's own resident model only (O51).

Training waits while another app holds more than a tenth of the card's memory (L2c): beside a
model Ollama kept loaded, training spilled into system memory and made no step. In **quiet hours**,
when **nobody has used Row-Bot for 30 minutes** and **the only thing on the card is Row-Bot's own
local chat model**, Row-Bot asks Ollama to unload it (it loads again on the next chat, as after
Ollama's own keep-alive). Everything else is left alone:

- a model Ollama holds that Row-Bot didn't use (another app's, or the person's own in Ollama's app)
  means nothing is unloaded;
- memory held by anything that isn't one of those Ollama models (a game, another program) means
  nothing is unloaded either, since unloading Row-Bot's model wouldn't free the card.

Row-Bot's own models are its default chat model when that runs in Ollama, the Ollama models its own
usage ledger shows it called in the last day, and its personal models. Nothing here is asked
outside quiet hours, and nothing is unloaded while anyone uses Row-Bot.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

IDLE_SECONDS = 30 * 60.0
OWN_WINDOW_SECONDS = 24 * 3600.0
MIB = 1024 * 1024


def _names(value: str) -> set[str]:
    """A model's name as Ollama may list it (with and without ``:latest``)."""

    text = str(value or "").strip().lower()
    if not text:
        return set()
    return {text, text if ":" in text else f"{text}:latest", text.removesuffix(":latest")}


def own_models(*, now: float | None = None, ledger: Path | None = None) -> set[str]:
    """The Ollama models Row-Bot itself uses: its default chat model (when local), the Ollama models
    its usage ledger shows it called in the last day, and its personal models (by name)."""

    found: set[str] = set()
    try:
        from row_bot.models import get_current_model
        from row_bot.providers.selection import parse_model_ref

        parsed = parse_model_ref(str(get_current_model() or ""))
        if parsed and parsed[0] == "ollama":
            found |= _names(parsed[1])
    except Exception:
        logger.debug("Learning couldn't read the default model", exc_info=True)
    path = ledger
    if path is None:
        from row_bot.data_paths import get_usage_db_path

        path = Path(get_usage_db_path(create_parent=False))
    if path.is_file():
        since = (time.time() if now is None else float(now)) - OWN_WINDOW_SECONDS
        try:
            with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5)) as connection:
                for (model,) in connection.execute(
                        "SELECT DISTINCT model FROM model_usage WHERE provider = 'ollama' AND created_at >= ?", (since,)):
                    found |= _names(str(model or ""))
        except sqlite3.Error:
            logger.debug("Learning couldn't read the usage ledger", exc_info=True)
    return found


def _personal(name: str) -> bool:
    return str(name or "").lower().startswith("row-bot-personal")


class OwnModelUnloader:
    """Unloads Row-Bot's own resident Ollama model when it alone holds the card (see the module)."""

    def __init__(self, *, ollama: Any = None, memory: Callable[[], tuple[int, int] | None] | None = None,
                 own: Callable[[], set[str]] = own_models, free_share: float | None = None) -> None:
        self._ollama = ollama
        self._memory = memory
        self._own = own
        self._free_share = free_share
        self.unloaded: list[str] = []

    @property
    def ollama(self) -> Any:
        if self._ollama is None:
            from row_bot.learning.training.serving import OllamaRuntime

            self._ollama = OllamaRuntime()
        return self._ollama

    def free_card(self, *, idle_seconds: float | None) -> bool:
        """Unload Row-Bot's own resident model(s) when they alone keep training off the card, and
        nobody used Row-Bot for 30 minutes. True when something was unloaded."""

        from row_bot.learning.training import hardware

        if idle_seconds is None or idle_seconds < IDLE_SECONDS:
            return False
        loaded = self.ollama.loaded()
        if not loaded:
            return False
        own = self._own()
        if any(not (_personal(item["name"]) or _names(item["name"]) & own) for item in loaded):
            logger.info("Learning leaves the graphics card as it is: another app's model is loaded in Ollama")
            return False
        reading = (self._memory or hardware.gpu_memory)()
        if reading is None:
            return False
        used, total = reading
        held = sum(int(item.get("size_vram") or 0) for item in loaded) / MIB
        share = self._free_share if self._free_share is not None else hardware.TRAINING_FREE_SHARE
        if total <= 0 or used - held > (1 - share) * total:
            logger.info("Learning leaves the graphics card as it is: another program holds its memory")
            return False
        names = [item["name"] for item in loaded]
        for name in names:
            self.ollama.unload(name)
        self.unloaded.extend(names)
        logger.info("Learning unloaded Row-Bot's own resident model from the graphics card for training")
        return True


__all__ = ["IDLE_SECONDS", "OwnModelUnloader", "own_models"]
