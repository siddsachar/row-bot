"""What OpenRouter says when a request fails: logged in full, and a busy free model in plain words (F6).

OpenRouter wraps an upstream provider's failure as "Provider returned error" and
keeps the reason in ``error.metadata``: ``provider_name`` and the upstream's own
``raw`` reply. The SDK's exception text is only the message, so the log and the
chat could not tell a free model's rate limit from an outage. The log line here
keeps the HTTP status, ``error.code``, the provider and the start of ``raw``,
with secrets, paths and emails taken out; never ``user_id`` or the request.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_RAW_LIMIT = 300
# langchain-openrouter's text for an error chunk, or for an error in a 200 reply.
_LANGCHAIN_ERROR = re.compile(r"^OpenRouter API returned an error(?: during streaming)?: (?P<message>.*) "
                              r"\(code: (?P<code>[^)]*)\)$", re.DOTALL)
_BUSY_WORDS = ("rate-limit", "rate limit", "rate_limit", "too many requests", "overloaded", "capacity", "busy")
# The account's own free allowance (per day, per minute): ``turn_failure`` says those in its own words.
_ACCOUNT_FREE_LIMIT = "free-models-per-"


class FreeModelBusy(RuntimeError):
    """A free model OpenRouter couldn't run just now: its provider is limiting free use or is busy."""

    def __init__(self, model_id: str, status_code: int) -> None:
        super().__init__(
            f"{model_id} is busy right now: its provider is limiting free use. Wait a moment and resume, "
            "or switch to the Free Models Router, which picks a free model that is available."
        )
        self.status_code = status_code


@dataclass(frozen=True)
class OpenRouterErrorDetails:
    status: int
    code: str
    message: str
    provider: str
    raw: str


def error_details(exc: BaseException) -> OpenRouterErrorDetails | None:
    """OpenRouter's account of a failed request, or None for any other exception."""
    from openrouter.errors import OpenRouterError

    if isinstance(exc, OpenRouterError):
        try:
            payload = json.loads(exc.body or "")
        except (TypeError, ValueError):
            payload = None
        error = payload.get("error") if isinstance(payload, dict) else None
        error = error if isinstance(error, dict) else {}
        metadata = error.get("metadata") if isinstance(error.get("metadata"), dict) else {}
        raw = metadata.get("raw")
        return OpenRouterErrorDetails(
            status=int(exc.status_code or 0),
            code=str(error.get("code") or ""),
            message=str(error.get("message") or exc.message or ""),
            provider=str(metadata.get("provider_name") or ""),
            raw=raw if isinstance(raw, str) else json.dumps(raw) if raw is not None else "",
        )
    found = _LANGCHAIN_ERROR.match(str(exc)) if isinstance(exc, ValueError) else None
    if found is None:
        return None
    return OpenRouterErrorDetails(status=0, code=found.group("code"), message=found.group("message"),
                                  provider="", raw="")


def _shown(text: str) -> str:
    from row_bot.evolution import redact_text

    value = " ".join(redact_text(text).split())
    return value if len(value) <= _RAW_LIMIT else value[: _RAW_LIMIT - 1].rstrip() + "…"


def log_error(details: OpenRouterErrorDetails, model_id: str) -> None:
    logger.warning(
        "OpenRouter request failed model=%s status=%s code=%s provider=%s message=%s raw=%s",
        model_id, details.status or "-", details.code or "-", details.provider or "-",
        _shown(details.message) or "-", _shown(details.raw) or "-",
    )


def is_free_model_busy(details: OpenRouterErrorDetails, model_id: str) -> bool:
    """A ``:free`` model whose provider is rate-limiting or busy, not the account's own free allowance."""
    if not str(model_id or "").endswith(":free"):
        return False
    words = f"{details.message} {details.raw}".lower()
    if _ACCOUNT_FREE_LIMIT in words:
        return False
    return details.status == 429 or details.code == "429" or any(word in words for word in _BUSY_WORDS)


def described_failure(exc: BaseException, model_id: str) -> FreeModelBusy | None:
    """Log OpenRouter's details of ``exc``; a busy free model's failure to raise in its place."""
    details = error_details(exc)
    if details is None:
        return None
    log_error(details, model_id)
    if not is_free_model_busy(details, model_id):
        return None
    status = details.status or (int(details.code) if details.code.isdigit() else 429)
    return FreeModelBusy(model_id, status)
