"""Runtime-bridge loader.

Reads ``runtime_bridge.js`` / ``runtime_bridge.css`` from disk and
injects them into an assembled multi-route HTML document for preview or
publish.  The injected ``<script>`` is tagged with
``data-row-bot-runtime="1"`` so ``sanitize_agent_html`` leaves it alone.
"""

from __future__ import annotations

import json
import pathlib
from functools import lru_cache

RUNTIME_MARKER_ATTR = "data-row-bot-runtime"

_RUNTIME_DIR = pathlib.Path(__file__).resolve().parent
_JS_PATH = _RUNTIME_DIR / "runtime_bridge.js"
_CSS_PATH = _RUNTIME_DIR / "runtime_bridge.css"


@lru_cache(maxsize=1)
def read_runtime_assets() -> tuple[str, str]:
    """Return ``(js_text, css_text)`` for the runtime bridge."""
    js_text = _JS_PATH.read_text(encoding="utf-8")
    css_text = _CSS_PATH.read_text(encoding="utf-8")
    return js_text, css_text


def build_routes_payload(
    *,
    initial: str,
    order: list[str],
    labels: dict[str, str] | None = None,
) -> str:
    """Serialize the route-table metadata as an embedded JSON blob."""
    payload = {
        "initial": initial or "",
        "order": list(order or []),
        "labels": dict(labels or {}),
    }
    return json.dumps(payload, ensure_ascii=False)


def inject_runtime(
    html: str,
    *,
    routes_payload: str,
) -> str:
    """Inject the runtime CSS + JS (+ route metadata) into a full HTML doc.

    The HTML is expected to already contain one or more
    ``<section data-row-bot-route="…">`` blocks inside <body>.
    """
    js_text, css_text = read_runtime_assets()
    style_block = (
        '<style data-row-bot-runtime="1">\n' + css_text + "\n</style>"
    )
    routes_block = (
        '<script type="application/json" id="__row_bot_routes__" '
        'data-row-bot-runtime="1">'
        + (routes_payload or "{}")
        + "</script>"
    )
    script_block = (
        '<script data-row-bot-runtime="1">\n' + js_text + "\n</script>"
    )

    # Style goes in <head>; route payload + script go just before </body>.
    if "</head>" in html:
        html = html.replace("</head>", style_block + "</head>", 1)
    else:
        html = style_block + html

    closing_body = "</body>"
    if closing_body in html:
        html = html.replace(
            closing_body,
            routes_block + script_block + closing_body,
            1,
        )
    else:
        html = html + routes_block + script_block

    return html
