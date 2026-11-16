"""Local model servers already running on this computer, found when the person opens "On this computer".

Only when that page asks (no background polling), Row-Bot asks 127.0.0.1 on the ports each runtime uses
by default, with its standard model list and a short timeout: Ollama (11434, ``/api/tags``), LM Studio
(1234), vLLM (8000) and a llama.cpp server (8080, 8081), each ``/v1/models``. Nothing goes anywhere
but loopback; Row-Bot's own port is never asked. What answers is listed with its models.

**Use** connects one: Ollama through its own provider, the others as an ordinary local custom endpoint
(the profile of that runtime), whose models are then read from the same loopback address.
"""

from __future__ import annotations

import concurrent.futures
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

PROBE_SECONDS = 0.8
MAX_MODELS = 64


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


def _models(kind: str, payload: Any) -> list[str] | None:
    """The model names a server lists, or None when the answer isn't a model list."""

    if not isinstance(payload, dict):
        return None
    if kind == "ollama":
        items = payload.get("models")
        if not isinstance(items, list):
            return None
        names = [str(item.get("name") or item.get("model") or "") for item in items if isinstance(item, dict)]
    else:
        items = payload.get("data")
        if not isinstance(items, list):
            return None
        names = [str(item.get("id") or "") for item in items if isinstance(item, dict)]
    return [name for name in names if name and len(name) <= 256][:MAX_MODELS]


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


def connect(kind: str, port: int) -> dict[str, Any]:
    """**Use** on a found server: connect it and return its models as refs the model picker knows.

    Only a known runtime on its known loopback port can be connected this way."""

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
    return {"schema_version": 1, "provider_id": provider_id,
            "model_refs": [f"model:{provider_id}:{info.model_id}" for info in infos][:MAX_MODELS]}


__all__ = ["KNOWN", "Known", "connect", "detect", "known"]
