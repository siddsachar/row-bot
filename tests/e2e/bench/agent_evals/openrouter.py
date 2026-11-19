"""Every paid bench model through OpenRouter (the owner's choice, 19 Nov 2026: one key, one balance).

- **Key:** ``OPENROUTER_API_KEY``, from the eval profile's saved key first, else the keys file (``keys``).
- **Matched runs pin the host.** OpenRouter would otherwise route a model to any provider that serves it,
  at any quantisation. :func:`pin_for` names the model's first-party provider (OpenAI, Anthropic, ...),
  ``only`` that one, with ``allow_fallbacks: false``, and the precision when the published row needs it
  (Flint: ``bf16``). A call that provider can't serve fails instead of running elsewhere.
- **The serving provider is recorded.** :func:`install` sends the pin on every OpenRouter request this
  worker makes (Row-Bot's own turns and the bench's plain calls) and keeps each response's generation
  id; :func:`served_providers` asks OpenRouter which provider served each one.
- **Cost:** OpenRouter returns each call's cost, which Row-Bot's usage ledger already records and the
  bench ledger books as the provider's own figure. :func:`rates_document` writes the pinned endpoints'
  public prices into a rates file, so the ledger can hold every paid call to the cap *before* it is sent.

Prices and providers come from OpenRouter's public model API (no key): ``/api/v1/models/<id>/endpoints``.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Iterable

API = "https://openrouter.ai/api/v1"
#: The first-party provider of each model family, as OpenRouter names providers.
FIRST_PARTY = {"openai": "OpenAI", "anthropic": "Anthropic", "qwen": "Alibaba", "recursive": "Recursive",
               "z-ai": "Z.AI", "deepseek": "DeepSeek", "google": "Google"}


class RoutingError(RuntimeError):
    """The model can't be pinned the way a matched run needs."""


def first_party(model: str) -> str:
    author = str(model).split("/", 1)[0].lower()
    if author not in FIRST_PARTY:
        raise RoutingError(f"no first-party provider is known for {model!r}")
    return FIRST_PARTY[author]


def pin_for(model: str, *, quantization: str = "") -> dict[str, Any]:
    """OpenRouter's ``provider`` routing object for a matched run: the first-party host only, no fallbacks."""
    host = first_party(model)
    pin: dict[str, Any] = {"order": [host], "only": [host], "allow_fallbacks": False}
    if quantization:
        pin["quantizations"] = [quantization]
    return pin


def check_endpoints(model: str, endpoints: list[dict[str, Any]], *, quantization: str = "") -> dict[str, Any]:
    """The endpoint the pin will use, from OpenRouter's endpoint listing, or :class:`RoutingError` saying why
    the model can't be matched there (no first-party host, or not at the needed precision)."""
    host = first_party(model)
    hosted = [item for item in endpoints if str(item.get("provider_name")) == host]
    if not hosted:
        others = sorted({str(item.get("provider_name")) for item in endpoints})
        raise RoutingError(f"{model}: OpenRouter has no {host} endpoint (served by {', '.join(others) or 'nobody'})")
    if quantization:
        hosted = [item for item in hosted if str(item.get("quantization") or "").lower() == quantization.lower()]
        if not hosted:
            raise RoutingError(f"{model}: {host} serves it on OpenRouter, but not at {quantization}")
    # The dearest of the host's endpoints (a host can list several tiers): the cap is never under-held.
    return max(hosted, key=lambda item: float((item.get("pricing") or {}).get("prompt") or 0))


def rates_entry(endpoint: dict[str, Any]) -> dict[str, float]:
    """The bench rates file's entry (US dollars per million tokens) from an endpoint's per-token prices."""
    pricing = endpoint.get("pricing") or {}
    per_million = lambda key: round(float(pricing.get(key) or 0.0) * 1_000_000, 6)  # noqa: E731
    entry = {"input": per_million("prompt"), "output": per_million("completion")}
    if pricing.get("input_cache_read") not in (None, ""):
        entry["cache_read"] = per_million("input_cache_read")
    return entry


def rates_document(pinned: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """A rates file (``ROW_BOT_LIVE_EVAL_RATES``) for the pinned endpoints: ``openrouter:<model>`` entries."""
    return {"source": "OpenRouter public endpoint prices, the pinned first-party host",
            "models": {f"openrouter:{model}": rates_entry(endpoint) for model, endpoint in pinned.items()}}


def fetch_endpoints(model: str, *, client: Any = None) -> list[dict[str, Any]]:
    import httpx

    http = client or httpx.Client(timeout=30)
    data = http.get(f"{API}/models/{model}/endpoints").json().get("data") or {}
    return list(data.get("endpoints") or [])


def install(pins: dict[str, dict[str, Any]], generation_ids: list[str]) -> Callable[[], None]:
    """Send *pins* (model -> provider object) on every OpenRouter request this process makes, and collect each
    response's generation id into *generation_ids*. A model with no pin is refused: nothing unpinned runs."""
    from row_bot.providers.transports.openrouter_cancellable import CancellableChatOpenRouter

    original_params = CancellableChatOpenRouter._default_params
    original_generate, original_stream = CancellableChatOpenRouter._generate, CancellableChatOpenRouter._stream

    def params(self: Any) -> dict[str, Any]:
        found = dict(original_params.fget(self))  # type: ignore[union-attr]
        pin = pins.get(str(self.model_name or ""))
        if pin is None:
            raise RoutingError(f"refused: {self.model_name} has no provider pin in this run")
        found["provider"] = pin
        return found

    def keep(message: Any) -> None:
        identifier = (getattr(message, "response_metadata", None) or {}).get("id")
        if identifier and identifier not in generation_ids:
            generation_ids.append(str(identifier))

    def generate(self: Any, *args: Any, **kwargs: Any) -> Any:
        result = original_generate(self, *args, **kwargs)
        for generation in getattr(result, "generations", []) or []:
            keep(getattr(generation, "message", None))
        return result

    def stream(self: Any, *args: Any, **kwargs: Any) -> Any:
        for chunk in original_stream(self, *args, **kwargs):
            keep(getattr(chunk, "message", None))
            yield chunk

    CancellableChatOpenRouter._default_params = property(params)  # type: ignore[assignment]
    CancellableChatOpenRouter._generate, CancellableChatOpenRouter._stream = generate, stream  # type: ignore[method-assign]

    def undo() -> None:
        CancellableChatOpenRouter._default_params = original_params  # type: ignore[assignment]
        CancellableChatOpenRouter._generate, CancellableChatOpenRouter._stream = original_generate, original_stream

    return undo


def served_providers(generation_ids: Iterable[str], key: str, *, client: Any = None) -> dict[str, int]:
    """How many calls each provider served, from OpenRouter's generation records (``provider_name``)."""
    import httpx

    http = client or httpx.Client(timeout=30)
    counts: Counter = Counter()
    for identifier in generation_ids:
        try:
            data = http.get(f"{API}/generation", params={"id": identifier},
                            headers={"Authorization": f"Bearer {key}"}).json().get("data") or {}
            counts[str(data.get("provider_name") or "unknown")] += 1
        except Exception:  # noqa: BLE001 - a lookup that fails is counted as unknown, never a stop
            counts["lookup failed"] += 1
    return dict(counts)


def write_rates(path: Path, pinned: dict[str, dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rates_document(pinned), indent=1), encoding="utf-8")
    return path
