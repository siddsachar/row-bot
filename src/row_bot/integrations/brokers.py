"""Hosted connector brokers: one service that connects many apps. Off until the person turns one on in
Apps › Advanced › Catalogs, after reading what it means: a separate account with that company, and
every request and result for those apps passing through it under its terms.

Composio is the only one. Its personal endpoint signs in with standard MCP OAuth (no SDK, CLI or
server of Row-Bot's), or takes a consumer key kept in the keychain; both are the existing hosted
plan. Its catalog can't be listed without an account, so Row-Bot keeps its own list of the app
names it finds Composio by (no descriptions or logos of Composio's). Its remote code tools start
off and its tools that act ask every time (see ``tool_rules``).
"""
from __future__ import annotations

import json
from functools import cache
from pathlib import Path

from row_bot.integrations import catalogs

COMPOSIO = "composio"
ENDPOINT = "https://connect.composio.dev/mcp"
LINKS = (("Composio's terms", "https://composio.dev/terms"), ("Privacy policy", "https://composio.dev/privacy"),
         ("Data processing agreement", "https://composio.dev/legal/dpa"))
DISCLOSURE = ("Composio is a separate company and service, with its own account. Apps you use through it get "
              "Row-Bot's requests from Composio, and their results come back through Composio, which keeps them "
              "for up to a year by default. Composio's terms and privacy policy apply. Row-Bot still asks before "
              "anything changes, and you can turn this off at any time.")
# Composio's own tools: remote code starts off (switch it on to be asked each time); acting always asks.
RUNS_CODE = frozenset({"COMPOSIO_REMOTE_WORKBENCH", "COMPOSIO_REMOTE_BASH_TOOL"})
ALWAYS_ASKS = frozenset({"COMPOSIO_MULTI_EXECUTE_TOOL", "COMPOSIO_MANAGE_CONNECTIONS"})


def on(broker: str = COMPOSIO) -> bool:
    saved = catalogs.setting("brokers")
    return isinstance(saved, dict) and saved.get(broker) is True


def set_on(broker: str, value: bool) -> None:
    if broker != COMPOSIO:
        raise ValueError("not_found")
    saved = catalogs.setting("brokers")
    catalogs.set_setting("brokers", {**(saved if isinstance(saved, dict) else {}), broker: bool(value)})


def opt_in(broker: str = COMPOSIO) -> dict:
    """What the Catalogs switch shows: on or off, and what turning it on means."""
    return {"on": on(broker), "disclosure": DISCLOSURE, "links": [{"label": label, "url": url} for label, url in LINKS]}


@cache
def app_names() -> tuple[str, ...]:
    """Row-Bot's own list of apps people find Composio by."""
    raw = json.loads(Path(__file__).with_name("broker_apps.json").read_text(encoding="utf-8"))
    names = raw.get(COMPOSIO) if raw.get("schema_version") == 1 else None
    if not isinstance(names, list) or not all(isinstance(name, str) and 0 < len(name) <= 80 for name in names):
        raise ValueError("invalid_broker_catalog")
    return tuple(names)


def entries() -> list:
    """The two ways to connect Composio: sign in (OAuth), or a consumer key."""
    from row_bot.integrations.inputs import declaration
    from row_bot.mcp_client.marketplace import MarketplaceEntry
    common = {"source": COMPOSIO, "url": "https://composio.dev", "publisher": "Composio", "transport": "streamable_http",
              "requires_auth": True, "classification": "broker", "trust_tier": "community"}
    notes = ["A separate Composio account. Requests and results for apps you connect there pass through Composio, "
             "which keeps them for up to a year by default.",
             "After you connect, Composio asks you to connect each app in your Composio account."]
    oauth = MarketplaceEntry(id=COMPOSIO, name="Composio", description="Many apps through one Composio account: "
                             "sign in to Composio, then connect the apps you want there.",
                             install={"transport": "streamable_http", "url": ENDPOINT},
                             metadata={"auth_mode": "oauth", "evidence": "Hosted MCP endpoint; untested with a live account."},
                             notes=notes, **common)
    key = MarketplaceEntry(id=COMPOSIO + "-key", name="Composio with a consumer key", description="Many apps through one "
                           "Composio account, using a consumer key from your Composio settings.",
                           install={"transport": "streamable_http", "url": ENDPOINT,
                                    "headers": {"x-consumer-api-key": "{consumer_key}"},
                                    "inputs": [declaration("consumer_key", target="header", name="x-consumer-api-key",
                                                           label="Consumer key", secret=True, required=True,
                                                           description="From your Composio account settings; it starts with ck_.")]},
                           metadata={"auth_mode": "api_key", "evidence": "Hosted MCP endpoint; untested with a live account."},
                           notes=notes, **common)
    return [oauth, key]


def tool_rules(name: str) -> dict:
    """Stricter handling for Composio's own tools, wherever they appear: never looser."""
    upper = name.upper()
    if upper in RUNS_CODE:
        return {"effect": "unknown", "requires_approval": True, "runs_code": True}
    if upper in ALWAYS_ASKS:
        return {"effect": "unknown", "requires_approval": True}
    return {}
