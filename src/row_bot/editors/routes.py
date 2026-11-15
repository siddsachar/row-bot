"""The app's editor routes: Settings' (the person's own app) and ``row-bot mcp``'s call.

- ``GET /api/editors``: every editor, connected or not, with its last use.
- ``POST /api/editors/{id}/connect``: a new token in the keychain; returns the snippet (no token).
- ``GET /api/editors/{id}/snippet``: the exact config snippet.
- ``GET /api/editors/{id}/config``: what writing the editor's config would change. Changes nothing.
- ``POST /api/editors/{id}/config``: write it, for the fingerprint the person reviewed (a copy first).
- ``POST /api/editors/{id}/revoke``: its token stops working now.
- ``POST /api/editors/call``: one tool call from ``row-bot mcp`` with its token, from this computer
  only (``RouteKind.LOCAL_TOKEN``): never a browser (an ``Origin`` is refused), never forwarded.

Access middleware signs the Settings routes in (a person's window or device for a change, same
origin); the call route proves itself with the editor's token.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from row_bot.editors import catalog, config_files, store

logger = logging.getLogger(__name__)

_MAX_CALL_BYTES = 64 * 1024
_MAX_SETTINGS_BYTES = 4 * 1024
_HEADERS = {"Cache-Control": "no-store"}


def _json(body: dict, status: int = 200) -> JSONResponse:
    return JSONResponse(body, status_code=status, headers=_HEADERS)


def _error(status: int, code: str) -> JSONResponse:
    return _json({"ok": False, "code": code}, status)


def _editor(request: Request) -> catalog.Editor | None:
    return catalog.editor(request.path_params.get("editor_id"))


def _view(editor: catalog.Editor, connection: dict[str, str] | None) -> dict[str, Any]:
    return {"id": editor.id, "name": editor.name, "writes_config": editor.writes_config,
            "connected": connection is not None,
            "connected_at": (connection or {}).get("connected_at") or None,
            "last_used_at": (connection or {}).get("last_used_at") or None}


async def _body(request: Request, limit: int) -> dict | None:
    raw = await request.body()
    if len(raw) > limit:
        return None
    try:
        value = json.loads(raw or b"{}")
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def register_editor_routes(app: Any, service: Any = None) -> None:
    """Add the editor routes; ``service`` is the client platform that runs ``ask_row_bot``'s turns."""

    def platform() -> Any:
        if service is not None:
            return service
        from row_bot.application.client_platform import client_platform_service

        return client_platform_service

    async def list_editors(request: Request) -> JSONResponse:
        connected = {item["editor_id"]: item for item in await asyncio.to_thread(store.connections)}
        return _json({"ok": True, "editors": [_view(editor, connected.get(editor.id))
                                              for editor in catalog.EDITORS.values()]})

    async def connect(request: Request) -> JSONResponse:
        editor = _editor(request)
        if editor is None:
            return _error(404, "not_found")
        try:
            await asyncio.to_thread(store.connect, editor.id)
        except store.EditorError as exc:
            return _error(503, exc.code)
        connected = {item["editor_id"]: item for item in await asyncio.to_thread(store.connections)}
        return _json({"ok": True, "editor": _view(editor, connected.get(editor.id)),
                      "snippet": await asyncio.to_thread(config_files.snippet, editor)})

    async def snippet(request: Request) -> JSONResponse:
        editor = _editor(request)
        if editor is None:
            return _error(404, "not_found")
        return _json({"ok": True, "snippet": await asyncio.to_thread(config_files.snippet, editor)})

    async def config_preview(request: Request) -> JSONResponse:
        editor = _editor(request)
        if editor is None:
            return _error(404, "not_found")
        try:
            preview = await asyncio.to_thread(config_files.preview, editor)
        except store.EditorError as exc:
            return _error(409, exc.code)
        return _json({"ok": True, "preview": preview})

    async def config_write(request: Request) -> JSONResponse:
        editor = _editor(request)
        if editor is None:
            return _error(404, "not_found")
        body = await _body(request, _MAX_SETTINGS_BYTES)
        fingerprint = (body or {}).get("fingerprint")
        if not isinstance(fingerprint, str) or len(fingerprint) != 64:
            return _error(422, "invalid_command")
        try:
            written = await asyncio.to_thread(config_files.write, editor, fingerprint)
        except store.EditorError as exc:
            return _error(409, exc.code)
        except OSError:
            logger.warning("%s's config couldn't be written", editor.name, exc_info=True)
            return _error(409, "config_unwritable")
        return _json({"ok": True, "written": written})

    async def revoke(request: Request) -> JSONResponse:
        editor = _editor(request)
        if editor is None:
            return _error(404, "not_found")
        revoked = await asyncio.to_thread(store.revoke, editor.id)
        return _json({"ok": True, "revoked": revoked})

    async def call(request: Request) -> JSONResponse:
        from row_bot.access.policy import context_from_scope

        context = context_from_scope(request.scope)
        # Loopback only, never through a proxy, never a web page (the middleware checked too).
        if (context is None or not context.direct_loopback or context.forwarding_headers
                or request.headers.get("origin") is not None):
            return _error(403, "local_only")
        authorization = str(request.headers.get("authorization") or "")
        token = authorization[7:] if authorization.startswith("Bearer ") else ""
        editor_id = await asyncio.to_thread(store.authenticate, token) if token else None
        if editor_id is None:
            return _error(401, "not_connected")
        body = await _body(request, _MAX_CALL_BYTES)
        if body is None or body.get("tool") not in catalog.TOOL_NAMES:
            return _error(422, "invalid_command")
        from row_bot.editors.service import run_tool

        editor = catalog.EDITORS[editor_id]
        result = await asyncio.to_thread(run_tool, editor_id, str(body["tool"]), body.get("arguments") or {},
                                         service=platform(), wait_seconds=editor.answer_wait_seconds)
        return _json({"ok": True, "text": result.text, "is_error": result.is_error})

    app.add_route("/api/editors", list_editors, methods=["GET"])
    app.add_route("/api/editors/call", call, methods=["POST"])
    app.add_route("/api/editors/{editor_id}/connect", connect, methods=["POST"])
    app.add_route("/api/editors/{editor_id}/snippet", snippet, methods=["GET"])
    app.add_route("/api/editors/{editor_id}/config", config_preview, methods=["GET"])
    app.add_route("/api/editors/{editor_id}/config", config_write, methods=["POST"])
    app.add_route("/api/editors/{editor_id}/revoke", revoke, methods=["POST"])
