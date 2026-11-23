"""The admin console, served at ``/console/`` from ``ROW_BOT_ORG_CONSOLE_DIR`` (the built ``frontend/dist-console``).

- ``/console/assets/<file>``: the build's scripts, stylesheets and fonts, for anyone signed in.
- Any other ``/console/...`` path: the console's page for an admin (it routes in the browser), or, for a
  signed-in member who isn't one, the plain "You need to be an admin" page with 403.
- Someone not signed in is sent to ``/auth/login?next=<the path>`` by the gate (``identity.gate``), and comes
  back there after sign-in.

Every ``/console`` response carries ``CONSOLE_CSP`` instead of the API's ``default-src 'none'``: the console's
own scripts, styles, fonts and requests only, nothing inline, never in a frame. Without the setting none of
this exists: ``/console/`` is an unknown path like any other. (The app imports this module without sign-in's
libraries; the router imports them when it is made.)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response

from row_bot_org.identity.gate import RequestPrincipal, request_principal

CONSOLE_PATH = "/console"
ASSETS_PATH = CONSOLE_PATH + "/assets/"
CONSOLE_CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; base-uri 'none'; "
               "form-action 'self'; frame-ancestors 'none'")
_ASSET_NAME = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,199}")
# What a Vite build puts in assets/; anything else isn't served.
_ASSET_TYPES = {".js": "text/javascript", ".css": "text/css", ".woff2": "font/woff2", ".svg": "image/svg+xml",
                ".png": "image/png"}


def is_console_path(path: str) -> bool:
    return path == CONSOLE_PATH or path.startswith(CONSOLE_PATH + "/")


def console_router(folder: Path) -> APIRouter:
    from row_bot_org.identity import roles
    from row_bot_org.identity.web import principal_connection

    router = APIRouter()
    page, not_admin = folder / "console.html", folder / "console-not-admin.html"

    @router.get(CONSOLE_PATH)
    def console_root() -> Response:
        return RedirectResponse(CONSOLE_PATH + "/", status_code=308)

    @router.get(ASSETS_PATH + "{name}")
    def asset(name: str) -> Response:
        path = folder / "assets" / name
        kind = _ASSET_TYPES.get(path.suffix.lower())
        if not _ASSET_NAME.fullmatch(name) or kind is None or not path.is_file():
            return JSONResponse({"error": "not_found"}, status_code=404)
        return FileResponse(path, media_type=kind)

    @router.get(CONSOLE_PATH + "/{route:path}")
    def console_page(request: Request, principal: RequestPrincipal = Depends(request_principal),
                     conn: Any = Depends(principal_connection, scope="function")) -> Response:
        if roles.is_admin(conn, principal, request.app.state.settings):
            return FileResponse(page, media_type="text/html")
        return FileResponse(not_admin, media_type="text/html", status_code=403)

    return router


__all__ = ["ASSETS_PATH", "CONSOLE_CSP", "CONSOLE_PATH", "console_router", "is_console_path"]
