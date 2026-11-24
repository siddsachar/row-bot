"""Local model servers already running on this computer, found when the person opens "On this computer".

Only when that page asks (no background polling), Row-Bot asks 127.0.0.1 on the ports each runtime uses
by default, with its standard model list and a short timeout: Ollama (11434, ``/api/tags``), LM Studio
(1234), vLLM (8000) and a llama.cpp server (8080, 8081), each ``/v1/models``. Nothing goes anywhere
but loopback; Row-Bot's own port is never asked. What answers is listed with its models.

**Use** connects one: Ollama through its own provider, the others as an ordinary local custom endpoint
(the profile of that runtime), whose models are then read from the same loopback address. The chosen
model is then checked with the probe an endpoint added by hand gets (a reply, a tool call, the tool-result
round trip, streaming and streamed tool calls), so agent mode and streamed tool replies rest on evidence.
The check can load the model first, so it runs after Use has answered; the endpoint reads Not checked
until it finishes.
"""

from __future__ import annotations

import concurrent.futures
import logging
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

PROBE_SECONDS = 0.8
MAX_MODELS = 64
# Models a server lists that never chat: embedding and reranking models (LM Studio types them
# "embeddings" in some versions; their names say so in every runtime).
_NOT_CHAT_TYPES = frozenset({"embedding", "embeddings", "rerank", "reranker"})
_NOT_CHAT_MARKERS = ("embed", "rerank")


@dataclass(frozen=True)
class Known:
    kind: str  # ollama | lmstudio | vllm | llama_cpp (the custom endpoint profile)
    label: str
    port: int
    path: str


KNOWN: tuple[Known, ...] = (
    Known("ollama", "Ollama", 11434, "/api/tags"),
    Known("lmstudio", "LM Studio", 1234, "/v1/models"),
    Known("vllm", "vLLM", 8000, "/v1/models"),
    Known("llama_cpp", "llama.cpp server", 8080, "/v1/models"),
    Known("llama_cpp", "llama.cpp server", 8081, "/v1/models"),
)
HOST = "127.0.0.1"


def _known() -> Sequence[Known]:
    return KNOWN


# Replaced in tests (loopback fakes on free ports).
known: Callable[[], Sequence[Known]] = _known


def _own_port() -> int:
    from row_bot.app_port import get_app_port

    return get_app_port()


def _chats(item: dict[str, Any], name: str) -> bool:
    """Whether a listed model takes chat requests (not an embedding or reranking model)."""

    kind = str(item.get("type") or "").lower()
    return kind not in _NOT_CHAT_TYPES and not any(marker in name.lower() for marker in _NOT_CHAT_MARKERS)


def _models(kind: str, payload: Any) -> list[str] | None:
    """The chat models a server lists, or None when the answer isn't a model list."""

    if not isinstance(payload, dict):
        return None
    items = payload.get("models" if kind == "ollama" else "data")
    if not isinstance(items, list):
        return None
    names = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str((item.get("name") or item.get("model") if kind == "ollama" else item.get("id")) or "")
        if name and len(name) <= 256 and _chats(item, name):
            names.append(name)
    return names[:MAX_MODELS]


def _probe(target: Known) -> dict[str, Any] | None:
    import httpx

    url = f"http://{HOST}:{target.port}{target.path}"
    try:
        with httpx.Client(trust_env=False, timeout=PROBE_SECONDS, follow_redirects=False) as client:
            response = client.get(url)
        if response.status_code != 200:
            return None
        names = _models(target.kind, response.json())
    except Exception:  # noqa: BLE001 - nothing there, or not a model server
        return None
    if names is None:
        return None
    return {"kind": target.kind, "label": target.label, "port": target.port,
            "base_url": f"http://{HOST}:{target.port}" + ("" if target.kind == "ollama" else "/v1"),
            "models": names}


def _endpoint_id(target: Known) -> str:
    return f"{target.kind.replace('_', '')}-{target.port}"


def _saved_endpoints() -> dict[str, str]:
    """Base URL -> provider id of the custom endpoints already saved."""

    try:
        from row_bot.providers.custom import list_custom_endpoints

        # "localhost" and 127.0.0.1 are the same server here.
        return {str(item.get("base_url") or "").rstrip("/").replace("//localhost:", f"//{HOST}:"):
                str(item["provider_id"]) for item in list_custom_endpoints()}
    except Exception:  # noqa: BLE001
        return {}


def detect() -> dict[str, Any]:
    """Ask each known loopback port once, together, and list the servers that answered."""

    own = _own_port()
    targets = [target for target in known() if target.port != own]
    servers: list[dict[str, Any]] = []
    if targets:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(targets),
                                                   thread_name_prefix="row-bot-local-servers") as pool:
            found = list(pool.map(_probe, targets))
        saved = _saved_endpoints()
        for target, server in zip(targets, found):
            if server is None:
                continue
            server["provider_id"] = "ollama" if target.kind == "ollama" else saved.get(server["base_url"])
            servers.append(server)
    return {"schema_version": 1, "servers": servers}


def _in_background(work: Callable[[], None]) -> None:
    threading.Thread(target=work, name="row-bot-local-server-check", daemon=True).start()


# Replaced in tests (the check runs at once).
in_background: Callable[[Callable[[], None]], None] = _in_background


def _check(provider_id: str, model_id: str) -> None:
    """Probe *model_id* as adding the endpoint by hand does, unless it was already checked and answered."""

    from row_bot.providers import custom

    try:
        if custom.custom_probe_for_model(provider_id, model_id).get("ok"):
            return
        custom.probe_custom_endpoint(provider_id, model_id)
    except Exception:  # noqa: BLE001 - connected; the check can run again from Settings › Models
        logger.warning("The check of a connected local server's model didn't finish", exc_info=True)


def connect(kind: str, port: int, model: str | None = None) -> dict[str, Any]:
    """**Use** on a found server: connect it and return its models as refs the model picker knows.

    Only a known runtime on its known loopback port can be connected this way. The model the person chose
    (*model*, else the first chat model listed) is then checked in the background."""

    from row_bot.application.client_platform import ClientPlatformError

    target = next((item for item in known() if item.kind == kind and item.port == int(port)), None)
    if target is None:
        raise ClientPlatformError("invalid_command")
    server = _probe(target)
    if server is None:
        raise ClientPlatformError("model_configuration_unavailable")
    if target.kind == "ollama":
        from row_bot.providers.model_catalog_cache import refresh_model_catalog_cache

        refresh_model_catalog_cache(reason="first_run", force=True, provider_id="ollama")
        return {"schema_version": 1, "provider_id": "ollama",
                "model_refs": [f"model:ollama:{name}" for name in server["models"]]}
    from row_bot.providers import custom

    provider_id = _saved_endpoints().get(server["base_url"])
    if provider_id is None:
        endpoint_id = _endpoint_id(target)
        custom.save_custom_endpoint({
            "id": endpoint_id, "name": target.label, "base_url": server["base_url"], "profile": target.kind,
            "execution_location": "local", "auth_required": False,
        }, manage_secret=False)
        provider_id = custom.custom_provider_id(endpoint_id)
    infos = custom.refresh_custom_endpoint_models(provider_id)
    chat = set(server["models"])
    listed = [info.model_id for info in infos if info.model_id in chat][:MAX_MODELS]
    chosen = model if model in listed else listed[0] if listed else None
    if chosen:
        in_background(lambda: _check(provider_id, chosen))
    return {"schema_version": 1, "provider_id": provider_id,
            "model_refs": [f"model:{provider_id}:{name}" for name in listed]}


__all__ = ["KNOWN", "Known", "connect", "detect", "known"]
