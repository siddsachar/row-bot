"""Favourite models first, the rest of the list, and hidden ones under "More models" (F10).

A display preference per provider-qualified model reference, saved in
``model_settings.json`` beside the default model:

- ``favourites``: listed first in every model list, in the order they were added;
- ``hidden``: listed under "More models" (still usable, never deleted).

Nothing is hidden by default, so lists don't change until someone uses this.
Preferences are keyed by reference, not by what a provider reports, so an update
or a re-pull of a local model keeps them; a model that is gone is simply not
listed. A model is a favourite or hidden, never both.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import re
from typing import Any, Callable, Iterable, Literal

logger = logging.getLogger(__name__)

KEY = "model_visibility"
# Bounds, so the settings file stays small. Adding past one drops the oldest.
MAX_FAVOURITES = 64
MAX_HIDDEN = 512
MAX_REF_LENGTH = 647

Action = Literal["favourite", "unfavourite", "hide", "show", "show_all"]
ACTIONS: tuple[str, ...] = ("favourite", "unfavourite", "hide", "show", "show_all")

_PROVIDER = re.compile(r"[A-Za-z0-9_.-]{1,128}")


@dataclass(frozen=True)
class ModelVisibility:
    favourites: tuple[str, ...] = ()
    hidden: tuple[str, ...] = ()

    def rank(self, ref: str) -> int | None:
        """A favourite's place, from 1; None for any other model."""
        try:
            return self.favourites.index(ref) + 1
        except ValueError:
            return None

    def is_hidden(self, ref: str) -> bool:
        return ref in self.hidden

    def as_dict(self) -> dict[str, Any]:
        return {"schema_version": 1, "favourites": list(self.favourites), "hidden": list(self.hidden)}


# Learning's personal models (L2c) run under reserved names (an Ollama model or a local endpoint
# called ``row-bot-personal…``). Model lists leave them out unless Learning offers them itself: every
# ready version, as "Personal model v1" with a Personal tag (``learning.offered_personal_models``,
# L2c2); the untouched starting model it compares with, never.
WITHHELD_NAME = "row-bot-personal"


def withheld(provider_id: object, model_id: object = "") -> bool:
    """Whether a model (or a whole endpoint) is one of Learning's personal models, not offered in lists."""
    return WITHHELD_NAME in f"{provider_id or ''}:{model_id or ''}".lower()


def canonical_ref(value: object) -> str | None:
    """``model:<provider>:<model>`` with the provider's canonical id, or None."""
    from row_bot.providers.selection import model_ref, parse_model_ref

    if not isinstance(value, str) or len(value) > MAX_REF_LENGTH:
        return None
    parsed = parse_model_ref(value)
    if not parsed:
        return None
    provider_id, model_id = parsed
    model_id = model_id.strip()
    if not _PROVIDER.fullmatch(provider_id) or not model_id or not model_id.isprintable():
        return None
    ref = model_ref(provider_id, model_id)
    return ref if len(ref) <= MAX_REF_LENGTH else None


def _unique(values: Iterable[object]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        ref = canonical_ref(value)
        if ref and ref not in seen:
            seen.add(ref)
            result.append(ref)
    return result


def normalize(raw: object) -> ModelVisibility:
    """Keep only valid references, once each; a favourite is never also hidden."""
    if not isinstance(raw, dict):
        return ModelVisibility()
    favourites = raw.get("favourites")
    hidden = raw.get("hidden")
    kept = _unique(favourites if isinstance(favourites, list) else ())[-MAX_FAVOURITES:]
    chosen = set(kept)
    tucked = [ref for ref in _unique(hidden if isinstance(hidden, list) else ()) if ref not in chosen]
    return ModelVisibility(tuple(kept), tuple(tucked[-MAX_HIDDEN:]))


def _settings_path() -> Path:
    from row_bot.data_paths import get_row_bot_data_dir

    return get_row_bot_data_dir(create=False) / "model_settings.json"


def read_model_visibility(path: Path | None = None) -> ModelVisibility:
    """The saved preference; an unreadable file reads as nothing chosen."""
    from row_bot.providers.saved_model_settings import read_saved_model_settings

    try:
        raw, _revision, _exists = read_saved_model_settings(path or _settings_path())
    except Exception:
        logger.warning("Model visibility could not be read; every model is listed")
        return ModelVisibility()
    return normalize(raw.get(KEY))


def apply_action(current: ModelVisibility, action: str, ref: str | None) -> ModelVisibility:
    """The preference after one change. Adding moves a model to the end of its list."""
    if action == "show_all":
        return ModelVisibility(current.favourites, ())
    if action not in ACTIONS or ref is None:
        raise ValueError("invalid_command")
    favourites = [item for item in current.favourites if item != ref]
    hidden = [item for item in current.hidden if item != ref]
    if action == "favourite":
        favourites.append(ref)
    elif action == "hide":
        hidden.append(ref)
    return ModelVisibility(tuple(favourites[-MAX_FAVOURITES:]), tuple(hidden[-MAX_HIDDEN:]))


def update_model_visibility(action: str, model_ref: str | None = None, *, path: Path | None = None,
                            validate: Callable[[], None] = lambda: None) -> ModelVisibility:
    """Favourite, unfavourite, hide or show one model, or show every hidden one.

    Written through the shared model settings writer, so the default model and
    the context settings beside it are kept as they are.
    """
    from row_bot.providers.saved_model_settings import update_saved_model_settings

    if action not in ACTIONS:
        raise ValueError("invalid_command")
    ref = None if action == "show_all" else canonical_ref(model_ref)
    if action != "show_all" and ref is None:
        raise ValueError("invalid_model_selection")
    result: list[ModelVisibility] = []

    def merge(raw: dict) -> dict:
        updated = apply_action(normalize(raw.get(KEY)), action, ref)
        result.append(updated)
        merged = {key: value for key, value in raw.items() if key != KEY}
        if updated.favourites or updated.hidden:
            merged[KEY] = {"favourites": list(updated.favourites), "hidden": list(updated.hidden)}
        return merged

    update_saved_model_settings(merge, path=path or _settings_path(), validate=validate)
    return result[-1]


def annotate(items: Iterable[dict[str, Any]], *, key: str, visibility: ModelVisibility | None = None) -> list[dict[str, Any]]:
    """Add ``favourite_rank`` and ``hidden`` to model rows, only where set.

    Lists without a preference stay exactly as they were.
    """
    rows = list(items)
    current = read_model_visibility() if visibility is None else visibility
    if not current.favourites and not current.hidden:
        return rows
    marked = []
    for row in rows:
        ref = canonical_ref(row.get(key))
        rank = current.rank(ref) if ref else None
        if rank is not None:
            row = {**row, "favourite_rank": rank}
        elif ref and current.is_hidden(ref):
            row = {**row, "hidden": True}
        marked.append(row)
    return marked
