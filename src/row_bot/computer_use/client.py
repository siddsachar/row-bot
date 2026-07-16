"""Private, allowlisted Cua Driver MCP adapter."""

from __future__ import annotations

import base64
import binascii
import io
import os
import platform
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from row_bot.mcp_client.results import RawCallResult, raw_call_result
from row_bot.mcp_client.runtime import PrivateMcpSession

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_DIMENSION = 1456
MAX_ELEMENTS = 250
MAX_TREE_DEPTH = 12
MAX_FIELD_CHARS = 512
MAX_SEMANTIC_TEXT = 48 * 1024
ALLOWED_IMAGE_MIME = frozenset({"image/png", "image/jpeg"})

MODEL_ACTION_TO_CUA = {
    "list_apps": "list_apps",
    "list_windows": "list_windows",
    "launch_app": "launch_app",
    "capture": "get_window_state",
    "focus": "bring_to_front",
    "click": "click",
    "double_click": "double_click",
    "right_click": "right_click",
    "type": "type_text",
    "key": "press_key",
    "scroll": "scroll",
    "drag": "drag",
}

INTERNAL_TOOLS = frozenset({"set_config", "health_report", "check_permissions", "start_session", "end_session"})
ALLOWED_CUA_TOOLS = frozenset(MODEL_ACTION_TO_CUA.values()) | INTERNAL_TOOLS | {"hotkey"}
FORBIDDEN_TOOL_FAMILIES = frozenset({
    "page", "get_desktop_state", "start_recording", "stop_recording",
    "get_recording_state", "check_for_update", "install_ffmpeg", "kill_app",
    "set_agent_cursor", "zoom", "move_cursor", "get_config",
})


class CuaTransport(Protocol):
    def open(self) -> None: ...
    def call_raw(self, tool_name: str, arguments: dict[str, Any] | None = None) -> Any: ...
    def close(self) -> None: ...


@dataclass(frozen=True)
class CuaElement:
    token: str
    index: int
    role: str
    label: str
    value: str
    bounds: tuple[float, float, float, float]
    depth: int


@dataclass(frozen=True)
class CuaResponse:
    text: str = ""
    structured: dict[str, Any] = field(default_factory=dict)
    image_bytes: bytes | None = None
    image_mime: str = ""
    image_width: int = 0
    image_height: int = 0
    elements: tuple[CuaElement, ...] = ()
    truncated: bool = False
    is_error: bool = False
    error_code: str = ""


def build_cua_environment(session_id: str, environ: dict[str, str] | None = None) -> dict[str, str]:
    """Return the deliberately small child environment for Cua."""

    source = dict(os.environ if environ is None else environ)
    common = {"PATH", "Path", "HOME", "USERPROFILE", "TMP", "TEMP", "TMPDIR", "LANG", "LC_ALL"}
    windows = {"SystemRoot", "WINDIR", "COMSPEC", "APPDATA", "LOCALAPPDATA", "SESSIONNAME"}
    desktop = {"DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"}
    allowed = common | windows | desktop
    result = {key: str(value) for key, value in source.items() if key in allowed and value is not None}
    result["CUA_DRIVER_RS_UPDATE_CHECK"] = "0"
    result["ROW_BOT_CUA_SESSION_ID"] = str(session_id)
    if platform.system() == "Darwin":
        result["CUA_DRIVER_EMBEDDED"] = "1"
    result.pop("CUA_DRIVER_RS_TELEMETRY_ENABLED", None)
    result.pop("CUA_DRIVER_RS_TELEMETRY_DEBUG", None)
    return result


def _trim(value: Any) -> str:
    text = str(value or "")
    return text[:MAX_FIELD_CHARS] + ("…" if len(text) > MAX_FIELD_CHARS else "")


def _decode_image(data: str, mime: str) -> tuple[bytes, int, int]:
    if mime not in ALLOWED_IMAGE_MIME:
        raise ValueError(f"Unsupported Cua image MIME type: {mime or 'missing'}")
    try:
        decoded = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Cua returned malformed base64 image data") from exc
    if len(decoded) > MAX_IMAGE_BYTES:
        raise ValueError("Cua screenshot exceeds the 8 MiB decoded limit")
    if mime == "image/png" and not decoded.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Cua screenshot MIME and PNG magic bytes do not agree")
    if mime == "image/jpeg" and not decoded.startswith(b"\xff\xd8\xff"):
        raise ValueError("Cua screenshot MIME and JPEG magic bytes do not agree")
    try:
        from PIL import Image

        with Image.open(io.BytesIO(decoded)) as image:
            width, height = image.size
            actual = (image.format or "").upper()
            expected = "PNG" if mime == "image/png" else "JPEG"
            if actual != expected:
                raise ValueError("Cua screenshot MIME and decoded format do not agree")
    except ImportError:
        if mime == "image/png" and len(decoded) >= 24:
            width = int.from_bytes(decoded[16:20], "big")
            height = int.from_bytes(decoded[20:24], "big")
        else:
            raise ValueError("Pillow is required to validate JPEG Cua screenshots")
    if width <= 0 or height <= 0 or max(width, height) > MAX_IMAGE_DIMENSION:
        raise ValueError("Cua screenshot dimensions exceed the 1456 pixel limit")
    return decoded, int(width), int(height)


def parse_cua_result(result: Any) -> CuaResponse:
    raw = result if isinstance(result, RawCallResult) else raw_call_result(result)
    texts: list[str] = []
    image_bytes: bytes | None = None
    image_mime = ""
    image_width = image_height = 0
    for block in raw.content:
        if block.kind == "image" or block.data:
            if image_bytes is not None:
                continue
            image_bytes, image_width, image_height = _decode_image(block.data, block.mime_type)
            image_mime = block.mime_type
        elif block.text:
            texts.append(_trim(block.text))
    structured = raw.structured_content if isinstance(raw.structured_content, dict) else {}
    elements_raw = structured.get("elements") if isinstance(structured.get("elements"), list) else []
    elements: list[CuaElement] = []
    truncated = len(elements_raw) > MAX_ELEMENTS
    semantic_chars = 0
    for item in elements_raw:
        if len(elements) >= MAX_ELEMENTS or not isinstance(item, dict):
            truncated = True
            continue
        depth = int(item.get("depth") or 0)
        if depth > MAX_TREE_DEPTH:
            truncated = True
            continue
        frame = item.get("frame") if isinstance(item.get("frame"), dict) else {}
        element = CuaElement(
            token=_trim(item.get("element_token")),
            index=int(item.get("element_index") or 0),
            role=_trim(item.get("role")),
            label=_trim(item.get("label")),
            value=_trim(item.get("value")),
            bounds=(
                float(frame.get("x") or 0), float(frame.get("y") or 0),
                float(frame.get("w") or frame.get("width") or 0),
                float(frame.get("h") or frame.get("height") or 0),
            ),
            depth=depth,
        )
        semantic_chars += sum(len(value) for value in (element.token, element.role, element.label, element.value))
        if semantic_chars > MAX_SEMANTIC_TEXT:
            truncated = True
            break
        elements.append(element)
    error = structured.get("error") if isinstance(structured.get("error"), dict) else {}
    error_code = str(
        error.get("code")
        or structured.get("error_code")
        or (structured.get("code") if raw.is_error else "")
        or ""
    )
    return CuaResponse(
        text="\n".join(texts)[:MAX_SEMANTIC_TEXT],
        structured=dict(structured),
        image_bytes=image_bytes,
        image_mime=image_mime,
        image_width=image_width,
        image_height=image_height,
        elements=tuple(elements),
        truncated=truncated,
        is_error=bool(raw.is_error),
        error_code=error_code,
    )


class CuaClient:
    """One private Cua MCP connection with a hard tool allowlist."""

    def __init__(
        self,
        executable: str | Path,
        *,
        session_id: str | None = None,
        transport_factory: Callable[[str, str, dict[str, str]], CuaTransport] | None = None,
    ) -> None:
        self.executable = str(Path(executable))
        self.session_id = session_id or f"row-bot-{uuid.uuid4().hex}"
        self._transport_factory = transport_factory or self._default_transport
        self._transport: CuaTransport | None = None
        self.connection_generation = 0

    @staticmethod
    def _default_transport(executable: str, _session_id: str, env: dict[str, str]) -> CuaTransport:
        return PrivateMcpSession(command=executable, args=["mcp"], env=env, timeout=120.0)

    def start(self) -> None:
        if self._transport is not None:
            return
        from row_bot.computer_use.readiness import require_cua_disclosure

        require_cua_disclosure()
        transport = self._transport_factory(
            self.executable,
            self.session_id,
            build_cua_environment(self.session_id),
        )
        transport.open()
        self._transport = transport
        self.connection_generation += 1
        try:
            self.call_internal("set_config", {"capture_scope": "window", "max_image_dimension": MAX_IMAGE_DIMENSION})
            self.call_internal("start_session", {"session": self.session_id})
        except BaseException:
            self.close()
            raise

    def close(self, *, graceful: bool = True) -> None:
        transport = self._transport
        self._transport = None
        if transport is None:
            return
        if graceful:
            try:
                transport.call_raw("end_session", {"session": self.session_id})
            except Exception:
                pass
        transport.close()

    def _call(self, tool_name: str, arguments: dict[str, Any]) -> CuaResponse:
        if tool_name not in ALLOWED_CUA_TOOLS or tool_name in FORBIDDEN_TOOL_FAMILIES:
            raise PermissionError(f"Cua tool is not allowlisted: {tool_name}")
        if self._transport is None:
            self.start()
        assert self._transport is not None
        return parse_cua_result(self._transport.call_raw(tool_name, arguments))

    def call_internal(self, tool_name: str, arguments: dict[str, Any] | None = None) -> CuaResponse:
        if tool_name not in INTERNAL_TOOLS:
            raise PermissionError(f"Cua internal tool is not allowlisted: {tool_name}")
        return self._call(tool_name, dict(arguments or {}))

    def call_action(self, action: str, arguments: dict[str, Any] | None = None) -> CuaResponse:
        tool_name = MODEL_ACTION_TO_CUA.get(str(action))
        if not tool_name:
            raise ValueError(f"Unsupported Computer action: {action}")
        safe = dict(arguments or {})
        safe.setdefault("session", self.session_id)
        return self._call(tool_name, safe)

    def call_reviewed_driver_tool(self, tool_name: str, arguments: dict[str, Any] | None = None) -> CuaResponse:
        """Service-only access to a reviewed input tool not in the model schema."""

        if tool_name not in set(MODEL_ACTION_TO_CUA.values()) | {"hotkey"}:
            raise PermissionError(f"Cua driver tool is not approved for Computer actions: {tool_name}")
        safe = dict(arguments or {})
        safe.setdefault("session", self.session_id)
        return self._call(tool_name, safe)
