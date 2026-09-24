"""Framework-neutral semantic adapter contract for marketing captures."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from scripts.marketing.capture_contract import SceneSpec


class ClientAdapterError(RuntimeError):
    """Raised when a client cannot reach a requested semantic story state."""


@dataclass(frozen=True)
class SceneCapture:
    """Raw evidence returned by a client adapter for one scene."""

    scene_id: str
    image_path: Path
    video_path: Path | None
    width: int
    height: int
    captured_at: str
    masked_selectors: tuple[str, ...] = ()


class ClientAdapter(ABC):
    """Semantic operations shared by present and future Row-Bot clients.

    Manifests describe product intent. Only adapters may contain routes,
    selectors, timing rules, or client-specific interactions.
    """

    name: str

    def __init__(self, base_url: str, *, page: Any, records: Mapping[str, str]):
        self.base_url = base_url.rstrip("/")
        self.page = page
        self.records = dict(records)

    def record_id(self, record_key: str) -> str:
        try:
            value = self.records[record_key]
        except KeyError as exc:
            raise ClientAdapterError(f"missing persisted story record: {record_key}") from exc
        if not str(value).strip():
            raise ClientAdapterError(f"empty persisted story record: {record_key}")
        return str(value)

    @abstractmethod
    def open_conversation(self, record_key: str) -> None:
        """Open a persisted conversation without generating new content."""

    @abstractmethod
    def open_home_surface(self, name: str) -> None:
        """Open a top-level application surface by semantic name."""

    @abstractmethod
    def open_model_picker(self) -> None:
        """Open the current conversation's model chooser."""

    @abstractmethod
    def open_activity(self) -> None:
        """Expose the current conversation's activity/tool evidence."""

    @abstractmethod
    def open_workflow(self, record_key: str) -> None:
        """Open a persisted workflow definition and run history."""

    @abstractmethod
    def open_designer_project(self, record_key: str) -> None:
        """Open a persisted editable Designer project."""

    @abstractmethod
    def open_approval(self, record_key: str | None = None) -> None:
        """Open a real pending approval without resolving it."""

    @abstractmethod
    def await_settled_state(self, scene: SceneSpec) -> None:
        """Wait for a semantic terminal scene state, never a fixed sleep."""

    @abstractmethod
    def capture_scene(self, scene: SceneSpec, output_dir: Path) -> SceneCapture:
        """Capture lossless raw evidence for the requested semantic scene."""
