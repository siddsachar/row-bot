"""Your models: the starred models every picker lists.

A list of provider-qualified model references, saved in ``model_settings.json``
beside the default model, in the order they were starred. A picker lists the
stars that can do its job, plus its current value; every other model is one
search away. A new profile has no stars, and its pickers list ready models.

Stars are keyed by reference, not by what a provider reports, so an update or a
re-pull of a local model keeps them; a model that is gone is simply not listed.

5.3.3 folded the older lists into this one (``migrate_to_stars``): models pinned
for a job and the legacy starred cloud models became stars, and hidden models
went (anything not starred is already out of the way).
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from pathlib import Path
import re
from typing import Any, Callable, Iterable, Literal

logger = logging.getLogger(__name__)

KEY = "model_visibility"
# Bounds, so the settings file stays small. Adding past it drops the oldest.
MAX_FAVOURITES = 256
MAX_REF_LENGTH = 647

Action = Literal["favourite", "unfavourite"]
ACTIONS: tuple[str, ...] = ("favourite", "unfavourite")

_PROVIDER = re.compile(r"[A-Za-z0-9_.-]{1,128}")


@dataclass(frozen=True)
class ModelVisibility:
    favourites: tuple[str, ...] = ()

    def rank(self, ref: str) -> int | None:
        """A star's place, from 1; None for any other model."""
        try:
            return self.favourites.index(ref) + 1
        except ValueError:
            return None

    def as_dict(self) -> dict[str, Any]:
        return {"schema_version": 2, "favourites": list(self.favourites)}


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
    """Keep only valid references, once each."""
    if not isinstance(raw, dict):
        return ModelVisibility()
    favourites = raw.get("favourites")
    return ModelVisibility(tuple(_unique(favourites if isinstance(favourites, list) else ())[-MAX_FAVOURITES:]))


def _settings_path() -> Path:
    from row_bot.data_paths import get_row_bot_data_dir

    return get_row_bot_data_dir(create=False) / "model_settings.json"


def read_model_visibility(path: Path | None = None) -> ModelVisibility:
    """The saved stars; an unreadable file reads as none."""
    from row_bot.providers.saved_model_settings import read_saved_model_settings

    try:
        raw, _revision, _exists = read_saved_model_settings(path or _settings_path())
    except Exception:
        logger.warning("Starred models could not be read; pickers list ready models")
        return ModelVisibility()
    return normalize(raw.get(KEY))


def apply_action(current: ModelVisibility, action: str, ref: str | None) -> ModelVisibility:
    """The stars after one change. Starring moves a model to the end."""
    if action not in ACTIONS or ref is None:
        raise ValueError("invalid_command")
    favourites = [item for item in current.favourites if item != ref]
    if action == "favourite":
        favourites.append(ref)
    return ModelVisibility(tuple(favourites[-MAX_FAVOURITES:]))


def _write(raw: dict, stars: Iterable[str]) -> dict:
    merged = {key: value for key, value in raw.items() if key != KEY}
    kept = list(stars)
    if kept:
        merged[KEY] = {"favourites": kept}
    return merged


def update_model_visibility(action: str, model_ref: str | None = None, *, path: Path | None = None,
                            validate: Callable[[], None] = lambda: None) -> ModelVisibility:
    """Star or unstar one model.

    Written through the shared model settings writer, so the default model and
    the context settings beside it are kept as they are.
    """
    from row_bot.providers.saved_model_settings import update_saved_model_settings

    if action not in ACTIONS:
        raise ValueError("invalid_command")
    ref = canonical_ref(model_ref)
    if ref is None:
        raise ValueError("invalid_model_selection")
    result: list[ModelVisibility] = []

    def merge(raw: dict) -> dict:
        updated = apply_action(normalize(raw.get(KEY)), action, ref)
        result.append(updated)
        return _write(raw, updated.favourites)

    update_saved_model_settings(merge, path=path or _settings_path(), validate=validate)
    return result[-1]


def star_model(ref: str) -> None:
    """Star one model if it isn't already (choosing a first model, say); its place is kept."""
    from row_bot.providers.saved_model_settings import update_saved_model_settings

    canonical = canonical_ref(ref)
    if canonical is None:
        raise ValueError("invalid_model_selection")

    def merge(raw: dict) -> dict:
        current = normalize(raw.get(KEY))
        if canonical in current.favourites:
            return raw
        return _write(raw, (*current.favourites, canonical)[-MAX_FAVOURITES:])

    update_saved_model_settings(merge, path=_settings_path())


def annotate(items: Iterable[dict[str, Any]], *, key: str, visibility: ModelVisibility | None = None) -> list[dict[str, Any]]:
    """Add ``favourite_rank`` to starred model rows; other rows stay exactly as they were."""
    rows = list(items)
    current = read_model_visibility() if visibility is None else visibility
    if not current.favourites:
        return rows
    marked = []
    for row in rows:
        ref = canonical_ref(row.get(key))
        rank = current.rank(ref) if ref else None
        marked.append({**row, "favourite_rank": rank} if rank is not None else row)
    return marked


# ── 5.3.3: one list ──────────────────────────────────────────────────────────

BACKUP_SUFFIX = ".pre-5.3.3.json"
_LEGACY_PROVIDER_KEYS = ("quick_choices", "routes")


def _backup(path: Path) -> None:
    """Keep the file as it was, once, beside it (``providers.pre-5.3.3.json``)."""
    target = path.with_name(path.stem + BACKUP_SUFFIX)
    if path.exists() and not target.exists():
        target.write_bytes(path.read_bytes())


def _pinned_refs(quick_choices: object) -> list[str]:
    from row_bot.providers.selection import model_ref

    refs: list[str] = []
    for choice in quick_choices if isinstance(quick_choices, list) else ():
        if not isinstance(choice, dict) or choice.get("kind", "model") != "model":
            continue
        provider_id, model_id = str(choice.get("provider_id") or ""), str(choice.get("model_id") or "")
        refs.append(str(choice.get("id") or "") or (model_ref(provider_id, model_id) if provider_id and model_id else ""))
    return refs


def _legacy_starred_cloud_refs(data_dir: Path) -> list[str]:
    """Cloud models starred before Quick Choices (``cloud_config.json``'s ``starred_models``)."""
    from row_bot.providers.catalog import infer_provider_id
    from row_bot.providers.selection import model_ref

    try:
        raw = json.loads((data_dir / "cloud_config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    starred = raw.get("starred_models") if isinstance(raw, dict) else None
    refs = []
    for model_id in starred if isinstance(starred, list) else ():
        provider_id = infer_provider_id(str(model_id)) if isinstance(model_id, str) else None
        if provider_id:
            refs.append(model_ref(provider_id, model_id))
    return refs


def migrate_to_stars(data_dir: Path | None = None) -> bool:
    """Fold pins, legacy stars and hidden models into one list of stars, once; idempotent.

    Each file is copied to ``<name>.pre-5.3.3.json`` before it changes. A failure
    leaves the files as they were. The default models, credentials and every
    other setting are untouched.
    """
    from row_bot.data_paths import get_row_bot_data_dir
    from row_bot.docs_capture import is_docs_real_data_capture
    from row_bot.providers.config import provider_config_transaction, write_provider_metadata
    from row_bot.providers.saved_model_settings import read_saved_model_settings, update_saved_model_settings

    if is_docs_real_data_capture():
        return False
    root = data_dir or get_row_bot_data_dir(create=False)
    providers_path = root / "providers.json"
    settings_path = root / "model_settings.json"
    with provider_config_transaction(providers_path):
        try:
            providers = json.loads(providers_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            providers = {}
        if not isinstance(providers, dict):
            return False
        settings, _revision, _exists = read_saved_model_settings(settings_path)
        legacy_visibility = settings.get(KEY) if isinstance(settings.get(KEY), dict) else {}
        if not any(key in providers for key in _LEGACY_PROVIDER_KEYS) and "hidden" not in legacy_visibility:
            return False
        stars = normalize(legacy_visibility).favourites
        folded = _unique((*stars, *_pinned_refs(providers.get("quick_choices")), *_legacy_starred_cloud_refs(root)))
        if len(folded) > MAX_FAVOURITES:
            logger.warning("Kept the first %d of %d listed models as stars", MAX_FAVOURITES, len(folded))
        folded = folded[:MAX_FAVOURITES]

        balanced = next((route for route in providers.get("routes") or [] if isinstance(route, dict)
                         and route.get("id") == "balanced"), {})
        task_routes = balanced.get("task_routes") if isinstance(balanced.get("task_routes"), dict) else {}

        _backup(settings_path)
        _backup(providers_path)
        settings_before = settings_path.read_bytes() if settings_path.exists() else None
        update_saved_model_settings(lambda raw: _write(raw, folded), path=settings_path)
        cleaned = {key: value for key, value in providers.items() if key not in _LEGACY_PROVIDER_KEYS}
        if task_routes and not cleaned.get("task_routes"):
            cleaned["task_routes"] = dict(task_routes)
        try:
            write_provider_metadata(providers_path, cleaned)
        except Exception:
            # Both files change together or not at all.
            if settings_before is None:
                settings_path.unlink(missing_ok=True)
            else:
                settings_path.write_bytes(settings_before)
            raise
    logger.info("Your models: %d stars (pins and hidden models folded in)", len(folded))
    return True
