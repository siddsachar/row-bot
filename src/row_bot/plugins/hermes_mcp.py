"""Bounded mapping of pinned Hermes optional-MCP recipes, without its runtime."""
from __future__ import annotations

from collections.abc import Callable
import json
import re
import time

import httpx

from row_bot.data_paths import get_row_bot_data_dir
from row_bot.integrations.safe import write_atomic
from row_bot.plugins.hermes_catalog import _public_bytes

_REPO = "https://api.github.com/repos/NousResearch/hermes-agent"


def read_catalog(*, refresh: bool = False, cancelled: Callable[[], bool] = lambda: False) -> dict:
    path = get_row_bot_data_dir(create=False) / "hermes_mcp_catalog_cache.json"
    saved = {}
    try:
        if path.is_file() and not path.is_symlink() and path.stat().st_size < 65536:
            saved = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(saved, dict) or not re.fullmatch(r"[a-f0-9]{40}", saved.get("pin", "")):
                saved = {}
    except (ValueError, OSError):
        pass
    status, message = ("cached", "Pinned saved recipes; inspect before adding.") if saved else ("empty", "Search public source to load Hermes MCP recipes.")
    if refresh:
        previous = saved
        try:
            pin = json.loads(_public_bytes(_REPO + "/commits/HEAD"))["sha"]
            if not re.fullmatch(r"[a-f0-9]{40}", pin):
                raise ValueError("invalid_catalog")
            tree = json.loads(_public_bytes(_REPO + "/git/trees/" + pin + "?recursive=1"))
            if tree.get("truncated"):
                raise ValueError("invalid_catalog")
            names = sorted({v["path"].split("/")[1] for v in tree["tree"]
                if re.fullmatch(r"optional-mcps/[a-z0-9_-]{1,80}/manifest.yaml", v.get("path", ""))})
            if len(names) > 128:
                raise ValueError("invalid_catalog")
            saved = {"pin": pin, "names": names, "fetched_at": time.time()}
            if cancelled():
                raise ValueError("integration_search_cancelled")
            write_atomic(path, json.dumps(saved), cancelled=cancelled)
            status, message = "live", "Pinned public recipes; no server or bootstrap has run."
        except (ValueError, OSError, KeyError, TypeError, httpx.HTTPError):
            saved = previous
            status, message = ("stale" if saved else "error"), "Hermes MCP is unavailable or rate limited. Saved recipes remain available."
    return {**saved, "status": status, "message": message}


def normalize_recipe(raw: dict, *, name: str, pin: str, source_url: str) -> dict:
    from row_bot.plugins.portable import validate_remote
    from row_bot.mcp_client.packages import requirement
    if (type(raw) is not dict or raw.get("manifest_version") != 1 or raw.get("name") != name
            or raw.get("install") or raw.get("bootstrap")
            or set(raw) - {"manifest_version", "name", "description", "source", "transport", "auth", "tools", "suggest", "post_install", "connector_slug", "platforms"}):
        raise ValueError("hermes_recipe_unsupported")
    transport, credentials = raw.get("transport", {}), raw.get("auth", {})
    if type(transport) is not dict or type(credentials) is not dict:
        raise ValueError("hermes_recipe_unsupported")
    mode = credentials.get("type", "none")
    if mode not in {"none", "oauth", "api_key"} or credentials.get("oauth") or credentials.get("provider"):
        raise ValueError("hermes_recipe_unsupported")
    cfg = {"enabled": False, "environment_mode": "minimal"}
    if transport.get("type") == "http" and set(transport) <= {"type", "url", "version"}:
        validate_remote(transport.get("url"), {})
        cfg.update(transport="streamable_http", url=transport["url"])
    elif transport.get("type") == "stdio" and set(transport) <= {"type", "command", "args", "env", "version"}:
        cfg.update(transport="stdio", command=transport.get("command", ""), args=transport.get("args", []), env=transport.get("env", {}))
        package = requirement(cfg)
        if package is None or not re.fullmatch(r"\d+\.\d+\.\d+(?:-[\w.-]+)?", package[1]) or mode == "oauth":
            raise ValueError("hermes_recipe_unsupported")
        if type(cfg["env"]) is not dict or any(type(v) is not str or "${" in v for v in cfg["env"].values()):
            raise ValueError("hermes_recipe_unsupported")
    else:
        raise ValueError("hermes_recipe_unsupported")
    bindings = []
    for variable in credentials.get("env", []):
        key = variable.get("name", "")
        if cfg["transport"] != "stdio" or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", key):
            raise ValueError("hermes_recipe_unsupported")
        bindings.append({"kind": "env", "name": key, "key": key})
    if bindings:
        cfg["auth"] = {"mode": "api_key", "bindings": bindings}
    elif mode == "api_key":
        raise ValueError("hermes_recipe_unsupported")
    cfg["source"] = {"marketplace": "hermes_mcp", "url": source_url, "pin": pin}
    return {"name": name, "import_json": json.dumps({"mcpServers": {name: cfg}}), "requires_auth": mode != "none",
        "source_url": source_url, "notes": ["Hermes recipe at commit " + pin + "; no Hermes runtime is required.",
            "Authentication: " + mode + ". Review the tested tool list; upstream default tool selections are not automatically granted.",
            "Data destination: " + (cfg.get("url") or "local process and any services declared by its publisher"),
            "Third-party data collection and telemetry have not been validated by Row-Bot. Inspect the publisher terms before connecting.",
            "Bootstrap commands and preconfigured foreign OAuth clients are unsupported. Live service behavior is untested."]}
