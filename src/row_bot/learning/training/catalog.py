"""The models a personal model can start from: one descriptor each, where it trains and how it runs (C1).

Every model Row-Bot can train, certified or not, is a :class:`ModelChoice`: its pinned files (or, for a model the
person found on Hugging Face, its repository and revision), its licence, which recipes train it **on this
computer** (the card tiers, ``recipes/16gb.json`` ...) and **on Hugging Face** (``recipes/hf-*.json``), and how it
runs here afterwards: the GGUF it is served as and the graphics memory that takes (O131: a trained model always
runs on the person's computer, so a model the card can't run is never offered, wherever it would train).

Certified models come first and carry no checks; a model the person chose themselves (Part 4) carries the checks
Row-Bot ran on it (``checks``), and is offered only once they pass. Nothing here reads the network or the disk.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from row_bot.learning.training import weights

# The licences that only need showing; any other needs the person's OK once (Part 4).
PERMISSIVE = frozenset({"apache-2.0", "mit", "bsd-3-clause", "bsd-2-clause"})


@dataclass(frozen=True)
class ServeOption:
    """One way the model's GGUF runs here: its quantization, about how big the file is, and the graphics memory
    Row-Bot's own server needs free for it at the recipe's serving context (the file, its context and buffers)."""

    quantization: str
    gguf_bytes: int
    need_mib: int


@dataclass(frozen=True)
class ModelChoice:
    id: str
    name: str
    repo: str
    revision: str
    licence: str
    licence_id: str
    # Billions of parameters (the quote and the Hugging Face card follow it).
    params_b: float
    certified: bool
    # Card tiers whose recipe trains it here (``recipes/<tier>.json``), and the Hugging Face recipe.
    local_recipes: tuple[str, ...] = ()
    remote_recipe: str | None = None
    # Best first: the option that fits the card is the one built.
    serve: tuple[ServeOption, ...] = ()
    pinned: weights.PinnedModel | None = None
    # Part 4: what Row-Bot checked on a model the person chose (empty for a certified one).
    checks: Mapping[str, Any] = field(default_factory=dict)

    @property
    def permissive(self) -> bool:
        return self.licence_id in PERMISSIVE

    def serve_for(self, card_mib: int | None, free_mib: int | None = None) -> ServeOption | None:
        """The best way it runs on this card (its whole memory: the server waits for room like training does),
        or None when no option fits it. No NVIDIA card: None (Row-Bot's server runs personal models on one)."""

        if not card_mib:
            return None
        for option in self.serve:
            if option.need_mib <= card_mib:
                return option
        return None


# Measured on the RTX 5090 in Row-Bot's own server (L2x, C1) at 32K context; a smaller card's need is its own
# memory, so the 9B's Q4_K_M runs on an 8 GB card and its Q8_0 from 12 GB.
QWEN35_9B = ModelChoice(
    id=weights.QWEN35_9B.id, name=weights.QWEN35_9B.name, repo=weights.QWEN35_9B.repo,
    revision=weights.QWEN35_9B.revision, licence="Apache 2.0", licence_id="apache-2.0", params_b=9.0, certified=True,
    local_recipes=("16gb", "24gb", "32gb"), remote_recipe="hf-9b",
    serve=(ServeOption("Q8_0", 9_790_000_000, 11_776), ServeOption("Q4_K_M", 5_700_000_000, 7_680)),
    pinned=weights.QWEN35_9B,
)
QWEN38_27B = ModelChoice(
    id=weights.QWEN38_27B.id, name=weights.QWEN38_27B.name, repo=weights.QWEN38_27B.repo,
    revision=weights.QWEN38_27B.revision, licence="Apache 2.0", licence_id="apache-2.0", params_b=27.0,
    certified=True, remote_recipe="hf-27b",
    # O131: "the 27B needs about 24 GB" to run here.
    serve=(ServeOption("Q4_K_M", 16_600_000_000, 20_480),),
    pinned=weights.QWEN38_27B,
)
CERTIFIED: tuple[ModelChoice, ...] = (QWEN35_9B, QWEN38_27B)
BY_ID: dict[str, ModelChoice] = {model.id: model for model in CERTIFIED}


def get(model_id: str) -> ModelChoice | None:
    return BY_ID.get(str(model_id or ""))


def for_hugging_face(card_mib: int | None) -> tuple[ModelChoice, ...]:
    """The certified models Hugging Face can train for this computer, the strongest it can run first (O130,
    O131): a model this card can't run afterwards is never offered."""

    found = [model for model in CERTIFIED if model.remote_recipe and model.serve_for(card_mib) is not None]
    return tuple(sorted(found, key=lambda model: -model.params_b))


__all__ = ["CERTIFIED", "BY_ID", "ModelChoice", "PERMISSIVE", "QWEN35_9B", "QWEN38_27B", "ServeOption",
           "for_hugging_face", "get"]
