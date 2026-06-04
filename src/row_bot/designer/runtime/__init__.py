"""Designer interactive runtime bridge.

Provides the sandbox-safe JS + CSS injected into preview and publish
artifacts for landing-page and app-mockup modes.  Loader in ``loader.py``.
"""

from row_bot.designer.runtime.loader import (
    RUNTIME_MARKER_ATTR,
    build_routes_payload,
    inject_runtime,
    read_runtime_assets,
)

__all__ = [
    "RUNTIME_MARKER_ATTR",
    "build_routes_payload",
    "inject_runtime",
    "read_runtime_assets",
]
