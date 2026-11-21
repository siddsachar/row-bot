"""Sampling settings for local models, by model family (L1 of the local-models uplift).

A local engine samples with its own defaults unless a request says otherwise, and those defaults are
not what the model was tuned for: llama-server's are temperature 0.8, top_k 40, min_p 0.05 and no
presence penalty. Qwen's model cards ask for their own values, and for a presence penalty against
"endless repetition"; without one, Qwen3.5 9B on llama.cpp wrote replies of up to 49K tokens.

So a chat request to a model on this computer (Ollama, Run on this computer, or a server the person
runs) carries its family's values for the mode it runs in: thinking or not. A value the caller or the
endpoint's ``extra_body`` sets is never replaced. Families not listed here get nothing, and so do
Learning's personal models and evaluation arms, which are served the way they were trained.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from row_bot.providers.model_visibility import withheld
from row_bot.providers.ollama import normalize_ollama_family

# The general-task values from each family's own model card, as read on 21 Nov 2026. Both families
# think unless a request turns thinking off (their cards: "operate in thinking mode by default").
#
# Qwen3.5: https://huggingface.co/Qwen/Qwen3.5-9B/blob/c202236235762e1c871ad0ccb60c8ee5ba337b9a/README.md
#   ("Thinking mode for general tasks" and "Instruct (or non-thinking) mode for general tasks"); the
#   4B (851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a) and 27B (81a287ca484a5e71148f5beb7f367bb5286d70f9)
#   cards give the same values.
# Qwen3.8: https://huggingface.co/Qwen/Qwen3.8-27B/blob/37561816e66b5b9153b1b64c10968c18c1e6f4a0/README.md
#   ("Best Practices"): its thinking mode asks for no presence penalty, its instruct mode for 1.5.
# Qwen3 is left out: its card gives thinking temperature 0.6 and no presence penalty value.
# Every card sets repetition_penalty 1.0, which is the engines' own default, so it isn't sent.
_QWEN35 = {
    "thinking": {"temperature": 1.0, "top_p": 0.95, "top_k": 20, "min_p": 0.0, "presence_penalty": 1.5},
    "non_thinking": {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0.0, "presence_penalty": 1.5},
}
_QWEN38 = {
    "thinking": {"temperature": 1.0, "top_p": 0.95, "top_k": 20, "min_p": 0.0, "presence_penalty": 0.0},
    "non_thinking": {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0.0, "presence_penalty": 1.5},
}
PRESETS: dict[str, dict[str, dict[str, float | int]]] = {"qwen3.5": _QWEN35, "qwen3.8": _QWEN38}

# The family, then the end of the name or a size ("qwen3.5:9b" on Ollama, "qwen3.8-27b-q4" for Run on
# this computer, "Qwen3.5-35B-A3B-Q4_K_M.gguf" or "qwen/qwen3.5-9b" on a server). Other models whose
# names start alike (Qwen3.8 Flash-Next) have their own cards and get nothing.
_FAMILY = re.compile(r"^(qwen3\.[58])(?:$|[-_]\d+(?:\.\d+)?b)")

# OpenAI-compatible servers the person runs on their own machine (or their own GPU server).
LOCAL_PROFILES = frozenset({"llama_cpp", "lmstudio", "vllm", "sglang", "localai", "omlx"})
# Fields a server's documentation doesn't list are left out: LM Studio's chat API lists top_k but not
# min_p (https://lmstudio.ai/docs/developer/openai-compat/chat-completions).
_UNLISTED_FIELDS = {"lmstudio": frozenset({"min_p"})}


def family_of(model_id: str) -> str | None:
    """The preset family a model belongs to, or None."""
    found = _FAMILY.match(normalize_ollama_family(model_id))
    return found.group(1) if found else None


def preset_for(model_id: str, *, thinking: bool | None) -> dict[str, float | int]:
    """The model family's sampling values for a request that thinks or not (None: the model's default,
    which is thinking for every family here), or ``{}`` for a family without a preset."""
    family = family_of(model_id)
    if family is None:
        return {}
    return dict(PRESETS[family]["non_thinking" if thinking is False else "thinking"])


def _fill(target: dict[str, Any], preset: Mapping[str, Any]) -> None:
    for key, value in preset.items():
        target.setdefault(key, value)


def is_local_endpoint(endpoint: Mapping[str, Any]) -> bool:
    """A custom endpoint whose server runs on the person's own machine."""
    return endpoint.get("execution_location") == "local" or \
        str(endpoint.get("profile") or "").strip().lower() in LOCAL_PROFILES


def _request_thinks(body: Mapping[str, Any]) -> bool | None:
    """Whether an OpenAI-compatible request asks the model to think, as far as the request says."""
    template = body.get("chat_template_kwargs")
    if isinstance(template, Mapping) and isinstance(template.get("enable_thinking"), bool):
        return template["enable_thinking"]
    if isinstance(body.get("enable_thinking"), bool):
        return body["enable_thinking"]
    reasoning = body.get("reasoning")
    if isinstance(reasoning, Mapping) and isinstance(reasoning.get("enabled"), bool):
        return reasoning["enabled"]
    if body.get("reasoning_effort") == "none":
        return False
    return None


def apply_to_openai_compatible(body: dict[str, Any], endpoint: Mapping[str, Any], model_id: str) -> None:
    """Fill in the family's values on a chat request to a local OpenAI-compatible server, as top-level
    fields (llama.cpp, vLLM, SGLang, LocalAI and oMLX read ``top_k`` and ``min_p`` there)."""
    if not is_local_endpoint(endpoint) or endpoint.get("managed") == "learning":
        return
    if withheld(endpoint.get("provider_id") or endpoint.get("id"), model_id):
        return
    preset = preset_for(model_id, thinking=_request_thinks(body))
    for field in _UNLISTED_FIELDS.get(str(endpoint.get("profile") or "").strip().lower(), ()):
        preset.pop(field, None)
    _fill(body, preset)


def apply_to_ollama(options: dict[str, Any], model_id: str, think: Any) -> None:
    """Fill in the family's values in an Ollama chat request's ``options``.

    Ollama from 0.30 runs every GGUF model on llama-server and passes ``presence_penalty`` on to it
    (``llm/llama_server.go``, checked at v0.40.17), and its MLX runner applies it too
    (``mlxrunner/sample/sample.go``). Older versions ran Qwen3.5 on Ollama's Go engine, whose sampler
    takes only temperature, top_k, top_p and min_p (``runner/ollamarunner/runner.go`` at v0.24.0), so
    it drops every penalty, ``repeat_penalty`` included: the card's own penalty is sent as it is.
    """
    if _personal_ollama_model(model_id):
        return
    _fill(options, preset_for(model_id, thinking=None if think is None else think is not False))


def _personal_ollama_model(model_id: str) -> bool:
    """One of Learning's models on Ollama: a personal model's reserved name, or a model Learning saved
    serving settings for (a personal model, or an evaluation arm's model)."""
    if withheld("ollama", model_id):
        return True
    from row_bot.models import local_model_settings

    return bool(local_model_settings(model_id))
