"""Designer — publish self-contained HTML decks as static links."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import pathlib
from html import escape
from collections.abc import Callable

from row_bot.designer.export import build_html_export
from row_bot.designer.preview import (
    INTERACTIVE_MODES,
    get_preview_chrome,
    render_multi_route_html,
)
from row_bot.designer.state import DesignerProject
from row_bot.designer.storage import DESIGNER_DIR, save_project
from row_bot.app_port import get_app_port
from row_bot.tunnel import tunnel_manager

logger = logging.getLogger(__name__)

PUBLISHED_DIR = DESIGNER_DIR / "published"


def ensure_published_dir() -> pathlib.Path:
    """Create and return the published deck directory."""
    PUBLISHED_DIR.mkdir(parents=True, exist_ok=True)
    return PUBLISHED_DIR


def resolve_publish_path(project: DesignerProject) -> pathlib.Path:
    """Return the stable static-file path for a published project."""
    return ensure_published_dir() / f"{project.id}.html"


def delete_published_project(project_id: str) -> bool:
    """Remove a project's local published HTML without stopping shared tunnels."""

    from row_bot.thread_cleanup import resolve_managed_path

    path = resolve_managed_path(PUBLISHED_DIR, f"{str(project_id or '')}.html")
    if not path.exists():
        return False
    path.unlink()
    return True


def resolve_publish_base_url(ensure_public: bool = True, *, strict: bool = False) -> tuple[str, bool]:
    """Return the base URL for published links and whether it is public."""
    app_port = get_app_port()
    public_url = tunnel_manager.get_url(app_port)
    if ensure_public and not public_url and tunnel_manager.is_available():
        try:
            public_url = tunnel_manager.start_tunnel(app_port, label="designer publish")
        except Exception:
            if strict:
                raise
            logger.warning("Could not open a public tunnel for designer publishing", exc_info=True)
    if public_url:
        return public_url.rstrip("/"), True
    return f"http://127.0.0.1:{app_port}", False


def build_publish_bytes(project: DesignerProject, pages: str | None = None, *, isolated: bool = False) -> bytes:
    """Render the publishable HTML bytes for a project.

    Interactive modes (landing / app_mockup / storyboard) use the
    multi-route renderer so the published page carries the runtime
    bridge and behaves like the editor preview. Deck / document modes
    go through the classic export pipeline (with page-range support).
    """
    mode = getattr(project, "mode", "deck")
    if mode in INTERACTIVE_MODES:
        html = render_multi_route_html(project)
        if isolated:
            from row_bot.designer.preview import isolate_preview_html
            html = isolate_preview_html(html, scripts=True, brand=project.brand)
        # Guarantee the published document declares UTF-8 at the top of
        # <head> — the static file server does not attach a charset to
        # the Content-Type, so browsers fall back to Windows-1252 and
        # every emoji renders as mojibake ("ðŸ'") if <meta charset>
        # is missing.
        if "<meta charset" not in html.lower():
            if "<head>" in html:
                html = html.replace("<head>", "<head><meta charset=\"utf-8\">", 1)
            else:
                html = "<meta charset=\"utf-8\">" + html
        # Phase 2.2 — app_mockup should publish inside the same phone
        # bezel that the editor preview uses, so the prototype looks
        # like a phone on a desktop browser rather than a full-width web
        # page.
        chrome = get_preview_chrome(project)
        if chrome.get("kind") == "phone":
            html = _wrap_in_phone_bezel(html, chrome, project, isolated=isolated)
        return html.encode("utf-8")
    return build_html_export(project, pages)


def _wrap_in_phone_bezel(html: str, chrome: dict, project: DesignerProject, *, isolated: bool = False) -> str:
    """Wrap a published app_mockup document in a phone bezel shell.

    The original document stays unchanged inside an iframe; we layer a
    simple host page around it that paints the bezel. Using an iframe
    keeps the published page's CSS isolated from the host styles so the
    runtime bridge, route host, and mockup CSS keep working exactly as
    they did in the preview.
    """
    import base64

    cw = int(getattr(project, "canvas_width", 390) or 390)
    ch = int(getattr(project, "canvas_height", 844) or 844)
    # The inner document must advertise UTF-8 so the browser decodes
    # the base64 payload correctly. ``build_publish_bytes`` already
    # guarantees this, but we defend against direct callers too.
    if "<meta charset" not in html.lower():
        if "<head>" in html:
            html = html.replace("<head>", "<head><meta charset=\"utf-8\">", 1)
        else:
            html = "<meta charset=\"utf-8\">" + html
    b64 = base64.b64encode(html.encode("utf-8")).decode("ascii")
    bezel_style = chrome.get("bezel_style", "")
    screen_style = chrome.get("screen_style", "")
    notch_style = chrome.get("notch_style", "")
    return (
        "<!DOCTYPE html><html><head>"
        "<meta charset=\"utf-8\">"
        f"<title>{escape(project.name)}</title>"
        "<style>"
        "html,body{margin:0;padding:0;background:#0B1220;color:#F8FAFC;"
        "font-family:Inter,system-ui,sans-serif;min-height:100vh;}"
        ".row-bot-stage{display:flex;align-items:center;justify-content:center;"
        "min-height:100vh;padding:40px;}"
        ".row-bot-screen{width:" + str(cw) + "px;height:" + str(ch) + "px;"
        "max-width:100%;}"
        "iframe{border:0;width:100%;height:100%;display:block;background:#000;}"
        "</style>"
        "</head><body>"
        "<div class=\"row-bot-stage\">"
        f"<div style=\"{bezel_style}\">"
        f"<div style=\"{notch_style}\"></div>"
        f"<div class=\"row-bot-screen\" style=\"{screen_style}\">"
        f"<iframe src=\"data:text/html;charset=utf-8;base64,{b64}\" "
        + ('sandbox="allow-scripts" ' if isolated else '') +
        "allow=\"fullscreen\"></iframe>"
        "</div></div></div>"
        "</body></html>"
    )


def publish_project(
    project: DesignerProject,
    pages: str | None = None,
    *,
    ensure_public: bool = True,
    validate: Callable[[], None] | None = None,
    checkpoint: Callable[[str, int, int], None] | None = None,
    publish_bytes: Callable[[pathlib.Path, bytes], None] | None = None,
    isolated: bool = False,
    local_only: bool = False,
) -> dict:
    """Render a self-contained HTML deck and expose it through the app's static route."""
    html_bytes = build_publish_bytes(project, pages, isolated=isolated)
    if validate:
        validate()
    # The strict adapter owns contained directory creation as well as bytes.
    publish_path = PUBLISHED_DIR / f'{project.id}.html' if isolated and publish_bytes else resolve_publish_path(project)
    if checkpoint:
        checkpoint('publish_file_started', 0, 1)
    if validate:
        validate()
    if publish_bytes:
        publish_bytes(publish_path, html_bytes)
    else:
        publish_path.write_bytes(html_bytes)
    if checkpoint:
        checkpoint('publish_file_completed', 0, 1)

    if validate:
        validate()
    if checkpoint and ensure_public:
        checkpoint('tunnel_started', 0, 1)
    base_url, is_public = (f'http://127.0.0.1:{get_app_port()}', False) if local_only else resolve_publish_base_url(ensure_public=ensure_public, strict=isolated)
    if isolated:
        from urllib.parse import urlsplit
        parsed = urlsplit(base_url)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('publish_url_unavailable')
    if checkpoint and ensure_public:
        checkpoint('tunnel_completed' if is_public else 'tunnel_unavailable', 0, 1)
    url = f"{base_url}/published/{publish_path.name}"

    if validate:
        validate()
    project.publish_url = url
    project.published_at = datetime.now(timezone.utc).isoformat()
    save_project(project)
    if checkpoint:
        checkpoint('publish_metadata_completed', 0, 1)

    return {
        "url": url,
        "path": str(publish_path),
        "public": is_public,
        "pages": pages or "all",
        "mode": getattr(project, "mode", "deck"),
    }
