"""Validated, client-neutral contract for landing-page product captures."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


SCHEMA_VERSION = 1
MAX_GENERATION_ATTEMPTS = 12
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MODEL_REF_PATTERN = re.compile(r"^model:[a-z0-9][a-z0-9_-]*:.+$")
ALLOWED_OUTPUTS = frozenset({"webp", "webm"})
ALLOWED_SURFACES = frozenset(
    {
        "approval",
        "conversation",
        "designer",
        "home",
        "knowledge",
        "model-picker",
        "workflow",
    }
)
ALLOWED_FRAMINGS = frozenset({"app-window", "detail", "mobile"})
ALLOWED_VIEWPORTS = frozenset({"desktop", "mobile"})
FORBIDDEN_SCENE_KEYS = frozenset({"actions", "route", "selector", "selectors"})


class CaptureContractError(ValueError):
    """Raised when a landing-story manifest violates the capture contract."""


@dataclass(frozen=True)
class StorySpec:
    id: str
    prompt_version: int
    max_generation_attempts: int
    buddy_pack: str


@dataclass(frozen=True)
class ModelSpec:
    local: str
    frontier: str


@dataclass(frozen=True)
class ViewportSpec:
    width: int
    height: int


@dataclass(frozen=True)
class SceneSpec:
    id: str
    surface: str
    state: str
    framing: str
    viewport: str
    outputs: tuple[str, ...]
    asset: str
    alt: str
    public_safe: bool
    model_role: str | None = None
    record: str | None = None


@dataclass(frozen=True)
class LandingStoryManifest:
    story: StorySpec
    models: ModelSpec
    viewports: dict[str, ViewportSpec]
    canonical_task: str
    scenes: tuple[SceneSpec, ...]

    @property
    def scenes_by_id(self) -> dict[str, SceneSpec]:
        return {scene.id: scene for scene in self.scenes}


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CaptureContractError(f"{label} must be a mapping")
    return value


def _nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CaptureContractError(f"{label} must be a non-empty string")
    return value.strip()


def _slug(value: Any, label: str) -> str:
    selected = _nonempty_string(value, label)
    if not SLUG_PATTERN.fullmatch(selected):
        raise CaptureContractError(f"{label} must be a lowercase hyphenated slug")
    return selected


def _positive_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise CaptureContractError(f"{label} must be a positive integer")
    return value


def _model_ref(value: Any, label: str) -> str:
    selected = _nonempty_string(value, label)
    if not MODEL_REF_PATTERN.fullmatch(selected):
        raise CaptureContractError(
            f"{label} must be a provider-qualified model reference"
        )
    return selected


def _viewport(name: str, value: Any) -> ViewportSpec:
    selected = _mapping(value, f"viewports.{name}")
    width = _positive_int(selected.get("width"), f"viewports.{name}.width")
    height = _positive_int(selected.get("height"), f"viewports.{name}.height")
    if width > 7680 or height > 4320:
        raise CaptureContractError(f"viewports.{name} exceeds the capture size limit")
    return ViewportSpec(width=width, height=height)


def _scene(value: Any, index: int, *, model_roles: set[str]) -> SceneSpec:
    label = f"scenes[{index}]"
    selected = _mapping(value, label)
    forbidden = FORBIDDEN_SCENE_KEYS.intersection(selected)
    if forbidden:
        names = ", ".join(sorted(forbidden))
        raise CaptureContractError(
            f"{label} contains client-specific keys ({names}); put them in a client adapter"
        )

    scene_id = _slug(selected.get("id"), f"{label}.id")
    surface = _nonempty_string(selected.get("surface"), f"{label}.surface")
    if surface not in ALLOWED_SURFACES:
        raise CaptureContractError(f"{label}.surface is not supported: {surface}")
    framing = _nonempty_string(selected.get("framing"), f"{label}.framing")
    if framing not in ALLOWED_FRAMINGS:
        raise CaptureContractError(f"{label}.framing is not supported: {framing}")
    viewport = _nonempty_string(selected.get("viewport"), f"{label}.viewport")
    if viewport not in ALLOWED_VIEWPORTS:
        raise CaptureContractError(f"{label}.viewport is not supported: {viewport}")

    raw_outputs = selected.get("outputs")
    if not isinstance(raw_outputs, list) or not raw_outputs:
        raise CaptureContractError(f"{label}.outputs must be a non-empty list")
    outputs = tuple(_nonempty_string(item, f"{label}.outputs") for item in raw_outputs)
    if len(outputs) != len(set(outputs)):
        raise CaptureContractError(f"{label}.outputs must be unique")
    unsupported = set(outputs).difference(ALLOWED_OUTPUTS)
    if unsupported:
        raise CaptureContractError(
            f"{label}.outputs contains unsupported formats: {', '.join(sorted(unsupported))}"
        )
    if "webm" in outputs and "webp" not in outputs:
        raise CaptureContractError(f"{label} video requires a WebP poster fallback")

    asset = _slug(selected.get("asset"), f"{label}.asset")
    public_safe = selected.get("public_safe")
    if public_safe is not True:
        raise CaptureContractError(f"{label}.public_safe must be true")

    model_role_value = selected.get("model_role")
    model_role = None
    if model_role_value is not None:
        model_role = _nonempty_string(model_role_value, f"{label}.model_role")
        if model_role not in model_roles:
            raise CaptureContractError(f"{label}.model_role is not declared in models")

    record_value = selected.get("record")
    record = (
        _slug(record_value, f"{label}.record") if record_value is not None else None
    )
    if surface in {"conversation", "designer", "workflow"} and record is None:
        raise CaptureContractError(f"{label}.record is required for {surface}")

    return SceneSpec(
        id=scene_id,
        surface=surface,
        state=_slug(selected.get("state"), f"{label}.state"),
        framing=framing,
        viewport=viewport,
        outputs=outputs,
        asset=asset,
        alt=_nonempty_string(selected.get("alt"), f"{label}.alt"),
        public_safe=True,
        model_role=model_role,
        record=record,
    )


def parse_manifest(data: Any) -> LandingStoryManifest:
    """Parse and validate a deserialized landing-story manifest."""

    root = _mapping(data, "manifest")
    if root.get("schema") != SCHEMA_VERSION:
        raise CaptureContractError(f"schema must equal {SCHEMA_VERSION}")

    raw_story = _mapping(root.get("story"), "story")
    attempts = _positive_int(
        raw_story.get("max_generation_attempts"),
        "story.max_generation_attempts",
    )
    if attempts > MAX_GENERATION_ATTEMPTS:
        raise CaptureContractError(
            f"story.max_generation_attempts must not exceed {MAX_GENERATION_ATTEMPTS}"
        )
    story = StorySpec(
        id=_slug(raw_story.get("id"), "story.id"),
        prompt_version=_positive_int(
            raw_story.get("prompt_version"), "story.prompt_version"
        ),
        max_generation_attempts=attempts,
        buddy_pack=_slug(raw_story.get("buddy_pack"), "story.buddy_pack"),
    )

    raw_models = _mapping(root.get("models"), "models")
    expected_model_roles = {"local", "frontier"}
    if set(raw_models) != expected_model_roles:
        raise CaptureContractError(
            "models must declare exactly local and frontier roles"
        )
    models = ModelSpec(
        local=_model_ref(raw_models.get("local"), "models.local"),
        frontier=_model_ref(raw_models.get("frontier"), "models.frontier"),
    )
    if models.local == models.frontier:
        raise CaptureContractError("local and frontier model references must differ")

    raw_viewports = _mapping(root.get("viewports"), "viewports")
    if set(raw_viewports) != ALLOWED_VIEWPORTS:
        raise CaptureContractError("viewports must declare exactly desktop and mobile")
    viewports = {
        name: _viewport(name, raw_viewports[name]) for name in sorted(raw_viewports)
    }

    raw_scenes = root.get("scenes")
    if not isinstance(raw_scenes, list) or not raw_scenes:
        raise CaptureContractError("scenes must be a non-empty list")
    scenes = tuple(
        _scene(value, index, model_roles=expected_model_roles)
        for index, value in enumerate(raw_scenes)
    )
    scene_ids = [scene.id for scene in scenes]
    if len(scene_ids) != len(set(scene_ids)):
        raise CaptureContractError("scene IDs must be unique")
    assets = [scene.asset for scene in scenes]
    if len(assets) != len(set(assets)):
        raise CaptureContractError("scene asset names must be unique")

    return LandingStoryManifest(
        story=story,
        models=models,
        viewports=viewports,
        canonical_task=_nonempty_string(root.get("canonical_task"), "canonical_task"),
        scenes=scenes,
    )


def load_manifest(path: Path) -> LandingStoryManifest:
    """Load a UTF-8 YAML landing-story manifest from ``path``."""

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CaptureContractError(f"could not read manifest: {path}") from exc
    except yaml.YAMLError as exc:
        raise CaptureContractError(f"manifest is not valid YAML: {path}") from exc
    return parse_manifest(raw)
