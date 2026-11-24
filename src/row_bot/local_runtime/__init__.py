"""Run on this computer (E1): a llama.cpp build and a Qwen3.5 model Row-Bot downloads and runs.

Only after the person presses Download (the button names the size) does Row-Bot fetch one pinned
official llama.cpp build for this computer (``builds``) and one pinned model that fits it
(``models``, ``hardware``). Its server runs only while a chat needs it, on loopback, offline
(``server``). It appears as an ordinary local llama.cpp endpoint, so routing, context windows and
every "is this model local?" check work as for any model on this computer.
``docs/LOCAL_RUNTIME.md`` is the review: licences, network use, flags, what is stored where.

Importing this package starts nothing, reads nothing and creates no file.
"""

from __future__ import annotations

from typing import Any

# The runtime's custom endpoint id, and the provider id its models carry
# (``model:custom_openai_llamacpp:<model id>``). Stable: saved defaults and routes name it.
ENDPOINT_ID = "llamacpp"
PROVIDER_ID = f"custom_openai_{ENDPOINT_ID}"
OWNER = "local"
# The provider's name in model pickers and Settings › Models (W12): who runs it, beside Ollama or LM Studio.
DISPLAY_NAME = "Row-Bot (llama.cpp)"


def model_ref(model_id: str) -> str:
    return f"model:{PROVIDER_ID}:{model_id}"


def model_label(model_id: str) -> str | None:
    """A pinned model's name where people see it ("Qwen3.5 4B"), or None for an id that isn't one."""

    from row_bot.local_runtime import models

    found = models.get(model_id)
    return found.label if found is not None else None


def chosen_context(model_id: str) -> int | None:
    """The window the person chose for a pinned model in its settings (U1), or None for Automatic."""

    try:
        from row_bot.models import person_local_model_settings

        value = person_local_model_settings(PROVIDER_ID, model_id).get("context")
    except Exception:  # noqa: BLE001 - no saved settings to read: Automatic
        return None
    return int(value) if value else None


def model_context(model_id: str) -> int | None:
    """The window a pinned model is served at on this computer (``models.context_for``, with the person's
    choice), or None for an id that isn't one."""

    from row_bot.local_runtime import hardware, models

    found = models.get(model_id)
    return models.context_for(found, hardware.peek(), chosen_context(model_id)) if found is not None else None


def is_local_runtime_provider(provider_id: str | None) -> bool:
    return str(provider_id or "") == PROVIDER_ID


def catalog_view(hardware: Any = None) -> dict[str, Any]:
    """This computer and every pinned model with how it would run here (no files, no network)."""

    from row_bot.local_runtime import builds, hardware as probe, models

    found = hardware if hardware is not None else probe.peek()
    build = builds.select_build(found.platform, found.acceleration)
    pick = models.recommend(found)
    return {
        "supported": found.supported and build is not None,
        "computer": found.describe(),
        "acceleration": found.acceleration,
        "note": found.note,
        "build_size": build.size if build is not None else 0,
        "recommended": pick.id if pick is not None else None,
        "models": [
            {"id": model.id, "name": model.name, "size": model.size, "fit": models.fit(model, found),
             "everyday": model.everyday, "quantization": model.quantization, "older": model.older}
            for model in models.CATALOG
        ],
    }


__all__ = [
    "DISPLAY_NAME",
    "ENDPOINT_ID",
    "OWNER",
    "PROVIDER_ID",
    "catalog_view",
    "is_local_runtime_provider",
    "model_context",
    "model_label",
    "model_ref",
]
