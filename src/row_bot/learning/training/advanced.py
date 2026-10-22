"""Advanced training settings (D9; designs/learning-ux.md §5 "Advanced ›").

Simple by default: the certified recipe of this computer's tier is the recommendation, and Advanced
is a disclosure with those values filled in and **Reset to recommended**. What can change:

- **starting model**: the tier's starting models (Qwen3.5 2B on the 8 GB tier);
- **LoRA rank** (alpha follows it), **learning rate**, **epochs** (passes over the examples);
- **context length**: from the 16K minimum (O23: Row-Bot's own envelope is 10.75K tokens) up to what
  the tier measured (16K on 8 GB: 32K ran out of memory, L2b);
- **data mix**: your work, corrections, practice, general replay. Only your own work exists yet
  (corrections come with L3a, practice with L4b), so the mix is shown, not chosen;
- **method**: Standard (LoRA SFT, D8). L2e adds "Learn from corrections (experimental)".

Every value is clamped to the training engine's own limits (``learning_engine/trainer.py``:
``settings_from`` and ``LIMITS``, read from the engine's file, which imports nothing heavy at load)
and to the recipe's certified bounds, so no choice here can ask the engine for something it would
refuse or a card can't fit. The choices are saved in ``learning_advanced.json`` in the data folder (a
setting: "Delete learning data" keeps it) and apply to the **next** training run.
"""

from __future__ import annotations

import functools
import hashlib
import importlib.util
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

logger = logging.getLogger(__name__)

ADVANCED_FILE = "learning_advanced.json"
RANKS = (8, 16, 32, 64)
LEARNING_RATES = (5e-5, 1e-4, 2e-4, 3e-4, 5e-4)
EPOCHS = (1, 2, 3)
METHODS = ("standard",)
DATA_SOURCES = (("your_work", "Your work"), ("corrections", "Corrections"), ("practice", "Practice"),
                ("general", "General replay"))
_KEYS = frozenset({"starting_model", "rank", "learning_rate", "epochs", "context", "method"})


@functools.cache
def engine_trainer() -> Any:
    """The training engine's own trainer module, loaded by path for its limits only."""

    from row_bot.learning.training import lockfile

    spec = importlib.util.spec_from_file_location("_row_bot_engine_trainer_limits", lockfile.ENGINE_DIR / "trainer.py")
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def engine_limits() -> Mapping[str, tuple[float, float]]:
    return dict(engine_trainer().LIMITS)


def path(root: Path | None = None) -> Path:
    from row_bot import learning

    return (root if root is not None else learning.data_dir()) / ADVANCED_FILE


@dataclass(frozen=True)
class Choices:
    """The values in effect (recommended where nothing was changed)."""

    starting_model: str
    rank: int
    learning_rate: float
    epochs: int
    context: int
    method: str = "standard"

    def to_dict(self) -> dict[str, Any]:
        return {"starting_model": self.starting_model, "rank": self.rank, "learning_rate": self.learning_rate,
                "epochs": self.epochs, "context": self.context, "method": self.method}


def recommended(recipe: Any) -> Choices:
    return Choices(recipe.starting_model, int(recipe.settings.get("rank", 16)),
                   float(recipe.settings.get("learning_rate", 2e-4)), int(recipe.epochs), int(recipe.max_seq_length))


def contexts(recipe: Any) -> tuple[int, ...]:
    """The context lengths this tier may train at: from the 16K minimum to what it measured."""

    from row_bot.learning.training.recipes import MIN_TRAINING_CONTEXT

    top = max(MIN_TRAINING_CONTEXT, int(recipe.max_seq_length))
    found, value = [], MIN_TRAINING_CONTEXT
    while value <= top:
        found.append(value)
        value *= 2
    return tuple(found)


def starting_models(recipe: Any) -> tuple[str, ...]:
    from row_bot.learning.training import hardware

    found = hardware.peek()
    tier = found.tier if found is not None else None
    ids = tuple(model.id for model in (tier.models if tier is not None else ())) or (recipe.starting_model,)
    return ids if recipe.starting_model in ids else (recipe.starting_model, *ids)


def _nearest(value: float, options: tuple[Any, ...]) -> Any:
    return min(options, key=lambda option: abs(float(option) - float(value)))


def clamp(values: Mapping[str, Any], recipe: Any) -> Choices:
    """Values within the engine's limits and the recipe's certified bounds (unknown keys dropped)."""

    base = recommended(recipe)
    limits = engine_limits()
    result = base.to_dict()
    for key, value in values.items():
        if key not in _KEYS or value is None:
            continue
        if key == "starting_model":
            result[key] = value if value in starting_models(recipe) else base.starting_model
        elif key == "method":
            result[key] = value if value in METHODS else "standard"
        elif isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        elif key == "rank":
            low, high = limits["rank"]
            result[key] = int(_nearest(min(max(value, low), high), RANKS))
        elif key == "learning_rate":
            low, high = limits["learning_rate"]
            result[key] = float(_nearest(min(max(value, low), high), LEARNING_RATES))
        elif key == "epochs":
            result[key] = int(_nearest(value, EPOCHS))
        elif key == "context":
            low, high = limits["max_seq_length"]
            result[key] = int(_nearest(min(max(value, low), high), contexts(recipe)))
    # The engine's own settings_from keeps whatever reaches it within its limits (a final check).
    settled = engine_trainer().settings_from({"rank": result["rank"], "alpha": result["rank"],
                                              "learning_rate": result["learning_rate"],
                                              "max_seq_length": result["context"]})
    result.update(rank=int(settled["rank"]), learning_rate=float(settled["learning_rate"]),
                  context=int(settled["max_seq_length"]))
    return Choices(**result)


def read(root: Path | None = None) -> dict[str, Any]:
    """The saved changes (only what the person changed); empty when none or unreadable."""

    try:
        data = json.loads(path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("values"), dict):
        return {}
    return {key: value for key, value in data["values"].items() if key in _KEYS}


def current(recipe: Any, root: Path | None = None) -> Choices:
    return clamp(read(root), recipe)


def save(values: Mapping[str, Any], recipe: Any, root: Path | None = None) -> Choices:
    """Save the person's choices (clamped); a value equal to the recommendation isn't kept."""

    chosen = clamp(values, recipe)
    base = recommended(recipe).to_dict()
    changes = {key: value for key, value in chosen.to_dict().items() if value != base.get(key)}
    target = path(root)
    if not changes:
        target.unlink(missing_ok=True)
        return chosen
    from row_bot.learning import atomic

    atomic.write_text(target, json.dumps({"version": 1, "values": changes}, indent=1, sort_keys=True))
    return chosen


def reset(root: Path | None = None) -> None:
    """Reset to recommended."""

    path(root).unlink(missing_ok=True)


def apply(recipe: Any, root: Path | None = None) -> Any:
    """The recipe the next run trains with: the certified one, changed by the saved choices. Its
    identity (``sha256``) changes with them, so a version's release says what it was trained with."""

    saved = read(root)
    if not saved:
        return recipe
    chosen = clamp(saved, recipe)
    settings = {**dict(recipe.settings), "rank": chosen.rank, "alpha": chosen.rank,
                "learning_rate": chosen.learning_rate, "max_seq_length": chosen.context}
    digest = hashlib.sha256((recipe.sha256 + json.dumps(chosen.to_dict(), sort_keys=True)).encode("utf-8")).hexdigest()
    return replace(recipe, settings=MappingProxyType(settings), epochs=chosen.epochs,
                   starting_model=chosen.starting_model, sha256=digest)


def view(recipe: Any, root: Path | None = None) -> dict[str, Any]:
    """What Advanced shows: the values, the recommendation and the choices."""

    chosen = current(recipe, root)
    base = recommended(recipe)
    from row_bot.learning.training import hardware

    names = {model.id: model.name for model in hardware.STARTING_MODELS.values()}
    return {
        "values": chosen.to_dict(), "recommended": base.to_dict(), "changed": chosen != base,
        "choices": {
            "starting_model": [{"id": item, "name": names.get(item, item)} for item in starting_models(recipe)],
            "rank": list(RANKS), "learning_rate": list(LEARNING_RATES), "epochs": list(EPOCHS),
            "context": list(contexts(recipe)), "method": list(METHODS),
        },
        "data_mix": [{"id": key, "label": label, "share": 100 if key == "your_work" else 0,
                      "available": key == "your_work"} for key, label in DATA_SOURCES],
    }


__all__ = [
    "ADVANCED_FILE",
    "Choices",
    "EPOCHS",
    "LEARNING_RATES",
    "METHODS",
    "RANKS",
    "apply",
    "clamp",
    "contexts",
    "current",
    "engine_limits",
    "read",
    "recommended",
    "reset",
    "save",
    "view",
]
