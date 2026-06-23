from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class FakeMcpTool:
    name: str
    description: str = ""
    inputSchema: dict[str, Any] | None = None


@dataclass
class FakeContentBlock:
    type: str
    text: str = ""


@dataclass
class FakeCallResult:
    content: list[Any]
    structuredContent: dict[str, Any] | None = None
    isError: bool = False


class FakeAsyncContext:
    def __init__(self, value: Any):
        self.value = value

    async def __aenter__(self) -> Any:
        return self.value

    async def __aexit__(self, *_args: Any) -> None:
        return None


class FakeClientSession:
    def __init__(self, read_stream: Any, write_stream: Any):
        self.read_stream = read_stream
        self.write_stream = write_stream
        self.initialized = False

    async def __aenter__(self) -> "FakeClientSession":
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    async def initialize(self) -> None:
        self.initialized = True

    async def list_tools(self):
        return type("ToolList", (), {"tools": [FakeMcpTool("read_file", "Read", {"type": "object"})]})()
