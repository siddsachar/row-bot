"""Row-Bot's own stores that a migration brings things into: read for the preview, written on apply.

Every item lands where Row-Bot reads it, never in a side file:

* a persona becomes the personality (``user_config.json`` › ``identity.personality``), shortened to the
  personality's limit and passed through :func:`row_bot.identity.sanitize_personality`;
* agent instructions become a user skill, which nothing pins, so nothing new reaches every chat;
* memory files go to Import memories as a batch to review (``application.memory_import``): nothing
  is saved to memory until the person checks it there;
* MCP servers join ``mcp_servers.json`` through :mod:`row_bot.mcp_client.config`, turned off, with
  no setting values copied; one with the same name is never replaced;
* the default model is saved in ``model_settings.json``, and a model server in ``providers.json`` as
  a custom endpoint, never replacing one already set up.

Writers take the data folder the migration targets. MCP servers and memories only go into the data
folder this Row-Bot runs on, because their stores are the running app's.
"""

from __future__ import annotations

import ipaddress
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

PERSONALITY = "personality"
INSTRUCTIONS_SKILL = "instructions_skill"
MEMORY_REVIEW = "memory_review"
MCP_SERVER = "mcp_server"
DEFAULT_MODEL = "default_model"
CUSTOM_ENDPOINT = "custom_endpoint"

# Where each store shows in Row-Bot, for the preview.
PLACES = {
    PERSONALITY: "Settings › Preferences › Personality",
    INSTRUCTIONS_SKILL: "Settings › Skills",
    MEMORY_REVIEW: "Import memories",
    MCP_SERVER: "Settings › Apps",
    DEFAULT_MODEL: "Settings › Models › Main model",
    CUSTOM_ENDPOINT: "Settings › Models › Custom endpoints",
}

# Another app's provider names that are Row-Bot providers too.
_PROVIDER_IDS = {
    "anthropic": "anthropic",
    "gemini": "google",
    "google": "google",
    "minimax": "minimax",
    "ollama": "ollama",
    "openai": "openai",
    "openrouter": "openrouter",
    "requesty": "requesty",
    "xai": "xai",
}
_MCP_SETTINGS = ("env", "headers")
_PROSE_SKIP = ("#", "<!--", "---", "```", "~~~", "|")


def is_live_data_dir(root: Path) -> bool:
    """Whether ``root`` is the data folder this Row-Bot runs on."""
    from row_bot.data_paths import get_row_bot_data_dir

    try:
        return Path(root).resolve() == get_row_bot_data_dir(create=False).resolve()
    except OSError:
        return False


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, UnicodeError, ValueError, RecursionError):
        return {}
    return value if isinstance(value, dict) else {}


# Personality --------------------------------------------------------------------

def personality_from(text: str) -> tuple[str, bool]:
    """A persona file's prose as one personality line, and whether it had to be shortened to fit.

    Headings, comments, tables and code are left out. A longer persona is cut at the last sentence
    (or word) that fits the personality's limit, then sanitized as Settings sanitizes it.
    """
    from row_bot.identity import PERSONALITY_MAX_LEN, sanitize_personality

    lines = []
    fenced = False
    for line in text.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if fenced or not stripped or stripped.startswith(_PROSE_SKIP):
            continue
        lines.append(stripped.lstrip("-*+> ").strip())
    prose = " ".join(" ".join(lines).split())
    shortened = len(prose.encode("utf-8")) > PERSONALITY_MAX_LEN
    if shortened:
        fit = prose.encode("utf-8")[:PERSONALITY_MAX_LEN].decode("utf-8", "ignore")
        ends = [index for index, char in enumerate(fit) if char in ".!?" and (index + 1 == len(fit) or fit[index + 1] == " ")]
        if ends and ends[-1] >= len(fit) // 3:
            prose = fit[: ends[-1] + 1]
        else:
            prose = fit.rsplit(" ", 1)[0] if " " in fit else fit
    return sanitize_personality(prose), shortened


def current_personality(root: Path) -> str:
    identity = _read_json(Path(root) / "user_config.json").get("identity")
    value = identity.get("personality") if isinstance(identity, dict) else ""
    return value if isinstance(value, str) else ""


def write_personality(root: Path, text: str) -> None:
    """Save ``text`` as the personality, keeping the rest of the identity and the settings file."""
    from row_bot.identity import sanitize_personality
    from row_bot.providers.config import provider_config_transaction, write_provider_metadata

    path = Path(root) / "user_config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with provider_config_transaction(path):
        try:
            document = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        except (OSError, UnicodeError, ValueError, RecursionError):
            document = None
        if not isinstance(document, dict):
            raise ValueError("Row-Bot's settings file could not be read; the personality was not changed.")
        identity = document.get("identity") if isinstance(document.get("identity"), dict) else {}
        document["identity"] = {**identity, "personality": sanitize_personality(text)}
        write_provider_metadata(path, document)


# Agent instructions as a skill -----------------------------------------------------

def instructions_skill_name(app: str) -> str:
    return f"instructions-from-{app}"


def instructions_skill(text: str, app: str, app_label: str, file_name: str) -> str:
    """A user skill holding another agent's instructions, as written; empty when there are none."""
    import yaml

    body = text.replace("\r\n", "\n").strip()
    if not body:
        return ""
    meta = {
        "name": instructions_skill_name(app),
        "display_name": f"Instructions from {app_label}",
        "description": f"The instructions {app_label} followed ({file_name}), brought over by the import. "
                       "Use it when you want Row-Bot to work the same way.",
        "icon": "📜",
        "tags": ["imported"],
        "version": "1.0",
    }
    return f"---\n{yaml.safe_dump(meta, allow_unicode=True, sort_keys=False)}---\n\n{body}\n"


# Memories ---------------------------------------------------------------------------

def count_memories(paths: list[Path]) -> int:
    """How many memories Import memories finds in these files (a read only scan)."""
    from row_bot.memory_import import scan_files

    return len(scan_files(paths).facts)


# MCP servers -----------------------------------------------------------------------

def mcp_server_names(root: Path) -> set[str]:
    servers = _read_json(Path(root) / "mcp_servers.json").get("servers")
    return {str(name) for name in servers} if isinstance(servers, dict) else set()


def mcp_server(raw: Any) -> tuple[dict[str, Any] | None, list[str]]:
    """What Row-Bot keeps of another app's MCP server, and the setting names it leaves for the person.

    Setting values (``env``, ``headers``) are never copied: they are often keys. None when the server
    has neither a command nor an address.
    """
    record = dict(raw) if isinstance(raw, Mapping) else {}
    command = record.get("command")
    url = record.get("url")
    args = record.get("args")
    server: dict[str, Any] = {}
    if isinstance(command, str) and command.strip():
        server["command"] = command.strip()
        server["args"] = [str(arg) for arg in args] if isinstance(args, list) else []
    elif isinstance(url, str) and urlsplit(url.strip()).scheme in {"http", "https"}:
        server["url"] = url.strip()
    else:
        return None, []
    transport = record.get("transport") or record.get("type")
    if isinstance(transport, str) and transport.strip():
        server["transport"] = transport.strip()
    if isinstance(record.get("cwd"), str) and record["cwd"].strip():
        server["cwd"] = record["cwd"].strip()
    for key, target in (("connect_timeout", "connect_timeout"), ("timeout", "tool_timeout")):
        if isinstance(record.get(key), (int, float)) and not isinstance(record.get(key), bool) and record[key] > 0:
            server[target] = record[key]
    needs = sorted({str(name) for key in _MCP_SETTINGS if isinstance(record.get(key), Mapping) for name in record[key]})
    return server, needs


def add_mcp_server(root: Path, name: str, server: dict[str, Any]) -> None:
    """Add one server, turned off, to this Row-Bot's MCP configuration. A name in use is refused."""
    from row_bot.mcp_client import config

    if Path(config.CONFIG_PATH).parent.resolve() != Path(root).resolve():
        raise ValueError("MCP servers are only added to this Row-Bot's own data folder.")
    with config.configuration_transaction():
        current = config.normalize_config(config.read_saved_configuration().document)
        if name in current["servers"]:
            raise FileExistsError(f"An app named {name} is already set up; it was left as it is.")
        current["servers"][name] = config.normalize_server_config(name, {**server, "enabled": False})
        config.save_config(current)


# Default model and model servers ---------------------------------------------------

def row_bot_provider(name: str | None) -> str | None:
    """The Row-Bot provider another app's provider name stands for, when Row-Bot has it."""
    from row_bot.providers.catalog import PROVIDER_DEFINITIONS

    provider = _PROVIDER_IDS.get(str(name or "").strip().lower())
    return provider if provider in PROVIDER_DEFINITIONS else None


def model_reference(provider_id: str, model_id: str) -> str:
    from row_bot.providers.selection import model_ref

    return model_ref(provider_id, model_id)


def current_default_model(root: Path) -> str | None:
    value = _read_json(Path(root) / "model_settings.json").get("model")
    return value if isinstance(value, str) and value.strip() else None


def write_default_model(root: Path, reference: str) -> None:
    from row_bot.providers.saved_model_settings import update_saved_model_settings

    update_saved_model_settings(lambda raw: {**raw, "model": reference}, path=Path(root) / "model_settings.json")
    if is_live_data_dir(root):
        # A running chat runtime picks the new default up; a cold start reads the file.
        runtime = sys.modules.get("row_bot.models")
        if runtime is not None:
            runtime.adopt_saved_default(reference)
        from row_bot.application.client_diagnosis import recheck_default_model

        recheck_default_model()  # Home stops saying no model is chosen


def endpoint_address(value: Any) -> tuple[str, bool] | None:
    """A model server's address Row-Bot can save (http or https, no sign-in in it), and whether it is
    on this computer."""
    if not isinstance(value, str):
        return None
    address = value.strip().rstrip("/")
    try:
        parts = urlsplit(address)
        host = parts.hostname
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"} or not host or parts.username or parts.password or parts.query or parts.fragment:
        return None
    try:
        local = ipaddress.ip_address(host).is_loopback
    except ValueError:
        local = host == "localhost"
    return address, local


def endpoint_id(name: str) -> str:
    from row_bot.providers.custom import slugify_endpoint_id

    return slugify_endpoint_id(name)


def endpoint_provider(name: str) -> str:
    from row_bot.providers.custom import custom_provider_id

    return custom_provider_id(name)


def custom_endpoint_ids(root: Path) -> set[str]:
    endpoints = _read_json(Path(root) / "providers.json").get("custom_endpoints")
    return {str(item.get("id")) for item in endpoints if isinstance(item, dict)} if isinstance(endpoints, list) else set()


def add_custom_endpoint(root: Path, endpoint: dict[str, Any]) -> None:
    """Add a model server as a custom endpoint. One with the same id is never replaced."""
    from row_bot.providers.config import load_provider_config, provider_config_transaction, save_provider_config
    from row_bot.providers.custom import normalize_custom_endpoint

    path = Path(root) / "providers.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with provider_config_transaction(path):
        config = load_provider_config(path, strict=True)
        normalized = normalize_custom_endpoint(endpoint)
        if any(isinstance(item, dict) and item.get("id") == normalized["id"] for item in config["custom_endpoints"]):
            raise FileExistsError(f"A provider named {normalized['name']} is already set up; it was left as it is.")
        normalized["credential_scope"] = uuid4().hex
        config["custom_endpoints"] = [*config["custom_endpoints"], normalized]
        save_provider_config(config, path)
