"""Calls to the company's models through its gateway (Row-Bot for Teams, C2).

- Row-Bot's own transports carry them: OpenAI-compatible chat for a model on the OpenAI wire
  (``/api/gateway/openai/v1``), the Anthropic SDK for one on the Anthropic wire (``/api/gateway/anthropic``),
  streaming as they always do.
- Every request is sent to the joined company's origin, resolved as it goes (a Row-Bot that left, rejoined or
  was disconnected since the model was made never sends it elsewhere). The device's bearer is added then, from
  the C1b client, refreshed when it is about to end and once more on a 401. No model key ever sits on this
  computer: the request carries no ``x-api-key``, no other ``Authorization`` and no cookie.
- The gateway's own refusals (not allowed, over budget, a model switched off or not answering) reach the
  person in plain words, through the app's usual provider-error path: ``CompanyModelError``, whose message
  is what the chat shows. They are no provider outage, so the backup model never answers for them silently.
  An upstream model's own error (a context too long, say) passes through as that provider's error.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Generator
from typing import Any

import anthropic
import httpx

from row_bot.company import client, provider, store
from row_bot.providers.transports.anthropic_cancellable import AnthropicRequestStopped, CancellableChatAnthropic
from row_bot.providers.transports.cancellable_http import cancellable_async_http_client, cancellable_http_client
from row_bot.providers.transports.openai_compatible import ChatOpenAICompatible

logger = logging.getLogger(__name__)

OPENAI_PATH = "/api/gateway/openai/v1"
ANTHROPIC_PATH = "/api/gateway/anthropic"
_GATEWAY = "/api/gateway/"
# Not a key: the Anthropic SDK needs one to be built, and every request has it taken off (_Bearer).
NO_KEY = "row-bot-company"

# The words, by why. None names the company's host: the chat's error words are matched for kinds of failure.
NOT_JOINED = "This Row-Bot isn't joined to a company any more. Choose another model."
DISCONNECTED = "Your company disconnected this Row-Bot. Sign in again in Settings › Company."
GONE = "Your company no longer offers this model. Choose another model."
KEYCHAIN = "Row-Bot can't read your company sign-in: this computer's keychain is locked. Unlock it, then try again."
BUSY = "Your company's server is busy. Try again in a minute."
OFFLINE = "Row-Bot can't reach your company's server right now. Try again in a moment."
NO_MODELS = "Your company's server doesn't offer models yet. Ask your IT team."
TURNED_DOWN = "Your company's server turned this request down"
_ANSWERING = "Your company's model isn't answering right now. Try again later, or choose another model."
_OFF = "Your company has switched this model off for now. Choose another model, or try again later."
WORDS = {
    "model_not_allowed": "Your company doesn't let you use this model. Choose another model.",
    "budget_exceeded": ("The monthly budget your company set for its models is used up. Ask your IT team, "
                        "or choose another model."),
    "too_many_calls": ("Your company's budget allows only a few model calls at once. Wait for one to finish, "
                       "then try again."),
    "endpoint_disabled": _OFF,
    "endpoint_key_unavailable": _OFF,
    "endpoint_timeout": "Your company's model didn't answer in time. Try again.",
    "endpoint_unreachable": _ANSWERING,
    "endpoint_auth_failed": _ANSWERING,
    "bad_endpoint_response": _ANSWERING,
    "endpoint_error": _ANSWERING,
    "endpoint_response_too_large": _ANSWERING,
    "wrong_api": "Your company changed this model. Choose it again in the model picker.",
    "wrong_model_kind": "Your company changed this model. Choose it again in the model picker.",
}
# The gateway's refusals of the request itself: its own plain message follows.
_REQUEST = frozenset({"invalid_request", "request_too_large"})


class CompanyModelError(Exception):
    """A company model can't answer; the message says why and what to do."""


class _AnthropicCompanyModelError(CompanyModelError, anthropic.AnthropicError):
    """The same, for the Anthropic SDK, which passes its own errors on as they are and doesn't retry them."""


def _origin(url: httpx.URL) -> str | None:
    try:
        return client.normalise_origin(f"{url.scheme}://{url.netloc.decode('ascii')}")
    except (client.InvalidAddress, UnicodeError):
        return None


def _joined(error: type[CompanyModelError]) -> store.Record:
    record = store.read_record()
    if record is None:
        raise error(NOT_JOINED)
    if record.disconnected:
        raise error(DISCONNECTED)
    return record


def _base(path: str, error: type[CompanyModelError]) -> str:
    return _joined(error).origin + path


class _Bearer(httpx.Auth):
    """The device's bearer on each gateway request, refreshed once on a 401; nothing else that authenticates."""

    def __init__(self, error: type[CompanyModelError]) -> None:
        self.error = error

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        record = _joined(self.error)
        if _origin(request.url) != record.origin:
            raise self.error(NOT_JOINED)  # Never the bearer to another address.
        for name in ("cookie", "x-api-key"):
            if name in request.headers:
                del request.headers[name]
        try:
            credential = client.bearer(record.origin)
            request.headers["Authorization"] = f"Bearer {credential.access_token}"
            response = yield request
            if response.status_code == 401:
                credential = client.refreshed(record.origin, credential)
                request.headers["Authorization"] = f"Bearer {credential.access_token}"
                response = yield request
                if response.status_code == 401:
                    client.disconnect()
                    raise client.Disconnected
        except client.Disconnected:
            raise self.error(DISCONNECTED) from None
        except client.Busy:
            raise self.error(BUSY) from None
        except client.Unreachable:
            raise self.error(OFFLINE) from None
        except store.StorageUnavailable:
            raise self.error(KEYCHAIN) from None


def _error_of(body: bytes) -> tuple[str | None, str | None]:
    """The gateway's error code and message, in either wire's shape."""
    try:
        document = json.loads(body)
    except ValueError:
        return None, None
    error = document.get("error") if isinstance(document, dict) else None
    if not isinstance(error, dict):
        return None, None
    code, message = error.get("type"), error.get("message")
    return (code if isinstance(code, str) else None,
            " ".join(message.split())[:300] if isinstance(message, str) and message.strip() else None)


def refusal_words(status: int, body: bytes) -> str | None:
    """The plain words for one of the gateway's own refusals; None for anything else (an upstream's error)."""
    code, message = _error_of(body)
    if code in WORDS:
        return WORDS[code]
    if code in _REQUEST:
        return f"{TURNED_DOWN}: {message}" if message else f"{TURNED_DOWN}."
    if status == 404 and code is None:
        return NO_MODELS
    return None


def _ours(response: httpx.Response) -> bool:
    return (response.status_code >= 400 and response.status_code != 401
            and response.request.url.path.startswith(_GATEWAY))


def _hooks(error: type[CompanyModelError], *, asynchronous: bool) -> dict[str, list[Any]]:
    if asynchronous:
        async def arefused(response: httpx.Response) -> None:
            if _ours(response):
                await response.aread()
                words = refusal_words(response.status_code, response.content)
                if words:
                    raise error(words)

        return {"response": [arefused]}

    def refused(response: httpx.Response) -> None:
        if _ours(response):
            response.read()
            words = refusal_words(response.status_code, response.content)
            if words:
                raise error(words)

    return {"response": [refused]}


def _client_kwargs(error: type[CompanyModelError], *, asynchronous: bool) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"auth": _Bearer(error), "event_hooks": _hooks(error, asynchronous=asynchronous),
                              "follow_redirects": False}
    if client.transport is not None:
        kwargs["transport"] = client.transport  # Tests: the fake company server.
    return kwargs


class CompanyChatOpenAI(ChatOpenAICompatible):
    """OpenAI-compatible chat through the company's gateway; the address is the joined company's, per request."""

    def _target(self) -> tuple[str, str]:
        return _base(OPENAI_PATH, CompanyModelError), ""


class CompanyChatAnthropic(CancellableChatAnthropic):
    """Anthropic Messages through the company's gateway: the SDK client is made for each call, for the joined
    company's address then."""

    @property
    def _client(self) -> anthropic.Client:  # type: ignore[override]
        params = {**self._client_params, "base_url": _base(ANTHROPIC_PATH, _AnthropicCompanyModelError)}
        http = cancellable_http_client(
            stopped=AnthropicRequestStopped, base_url=params["base_url"],
            **({"timeout": params["timeout"]} if "timeout" in params else {}),
            **_client_kwargs(_AnthropicCompanyModelError, asynchronous=False))
        return anthropic.Client(**params, http_client=http)

    @property
    def _async_client(self) -> anthropic.AsyncClient:  # type: ignore[override]
        params = {**self._client_params, "base_url": _base(ANTHROPIC_PATH, _AnthropicCompanyModelError)}
        http = cancellable_async_http_client(
            stopped=AnthropicRequestStopped, base_url=params["base_url"],
            **({"timeout": params["timeout"]} if "timeout" in params else {}),
            **_client_kwargs(_AnthropicCompanyModelError, asynchronous=True))
        return anthropic.AsyncClient(**params, http_client=http)


def _catalog_model(model_id: str) -> provider.CatalogModel:
    record = _joined(CompanyModelError)
    found = provider.find(model_id)
    if found is None:
        try:
            provider.refresh()  # A model chosen before this process read the catalog.
        except client.Disconnected:
            raise CompanyModelError(DISCONNECTED) from None
        except client.Busy:
            raise CompanyModelError(BUSY) from None
        except client.Unreachable:
            raise CompanyModelError(OFFLINE) from None
        except store.StorageUnavailable:
            raise CompanyModelError(KEYCHAIN) from None
        found = provider.find(model_id)
    if found is None:
        logger.info("The company no longer offers a model chosen here (%s)", record.company)
        raise CompanyModelError(GONE)
    return found


def chat_model(model_id: str) -> Any:
    """The chat model for company model ``model_id``, on the wire its catalog names."""
    model = _catalog_model(model_id)
    origin = store.read_record().origin  # type: ignore[union-attr] - _catalog_model checked it
    if model.api == "anthropic":
        return CompanyChatAnthropic(model=model_id, api_key=NO_KEY, base_url=origin + ANTHROPIC_PATH)
    return CompanyChatOpenAI(
        model_name=model_id, api_key="", base_url=origin + OPENAI_PATH,
        endpoint={"provider_id": provider.PROVIDER_ID, "display_name": "Your company's model",
                  "base_url": origin + OPENAI_PATH, "transport": "openai_chat", "profile": "company",
                  "execution_location": "remote"},
        http_client=cancellable_http_client(**_client_kwargs(CompanyModelError, asynchronous=False)),
    )
