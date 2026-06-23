from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from row_bot.channels.base import Channel, ChannelCapabilities, ConfigField


@dataclass
class SentMessage:
    target: str | int
    text: str


class FakeChannel(Channel):
    """In-memory channel adapter used by registry and workflow tests."""

    def __init__(
        self,
        *,
        name: str = "fake",
        display_name: str = "Fake Channel",
        configured: bool = True,
        default_target: str | int = "fake-user",
        capabilities: ChannelCapabilities | None = None,
    ) -> None:
        self._name = name
        self._display_name = display_name
        self._configured = configured
        self._running = False
        self._default_target = default_target
        self._capabilities = capabilities or ChannelCapabilities(
            photo_out=True,
            document_out=True,
            buttons=True,
            streaming=True,
            typing=True,
        )
        self.messages: list[SentMessage] = []
        self.photos: list[tuple[str | int, str, str | None]] = []
        self.documents: list[tuple[str | int, str, str | None]] = []
        self.approvals: list[dict[str, Any]] = []
        self.approval_updates: list[tuple[str, str, str]] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        return self._display_name

    @property
    def capabilities(self) -> ChannelCapabilities:
        return self._capabilities

    @property
    def config_fields(self) -> list[ConfigField]:
        return [
            ConfigField(
                key="fake_target",
                label="Fake Target",
                field_type="text",
                storage="config",
                default=str(self._default_target),
            )
        ]

    async def start(self) -> bool:
        self._running = self._configured
        return self._running

    async def stop(self) -> None:
        self._running = False

    def is_configured(self) -> bool:
        return self._configured

    def is_running(self) -> bool:
        return self._running

    def send_message(self, target: str | int, text: str) -> None:
        if not self._running:
            raise RuntimeError("Fake channel is not running")
        self.messages.append(SentMessage(target, text))

    def send_photo(self, target: str | int, file_path: str, caption: str | None = None) -> None:
        if not self.capabilities.photo_out:
            super().send_photo(target, file_path, caption)
        self.photos.append((target, file_path, caption))

    def send_document(self, target: str | int, file_path: str, caption: str | None = None) -> None:
        if not self.capabilities.document_out:
            super().send_document(target, file_path, caption)
        self.documents.append((target, file_path, caption))

    def send_approval_request(self, target: str | int, interrupt_data: Any, config: dict) -> str | None:
        if not self.capabilities.buttons:
            super().send_approval_request(target, interrupt_data, config)
        message_ref = f"{self.name}-approval-{len(self.approvals) + 1}"
        self.approvals.append(
            {
                "target": target,
                "interrupt_data": interrupt_data,
                "config": dict(config),
                "message_ref": message_ref,
            }
        )
        return message_ref

    def update_approval_message(self, message_ref: str, status: str, source: str = "") -> None:
        self.approval_updates.append((message_ref, status, source))

    def get_default_target(self) -> str | int:
        if self._default_target in ("", None):
            raise RuntimeError("Fake channel has no default target")
        return self._default_target


def approval_payload(resume_token: str, approved: bool) -> dict[str, Any]:
    return {"resume_token": resume_token, "approved": approved, "source": "fake"}
