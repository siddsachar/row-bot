from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from row_bot.providers.capabilities import snapshot_supports_surface
from row_bot.providers.auth_store import get_provider_secret, provider_secret_status
from row_bot.providers.catalog import get_provider_definition
from row_bot.providers.custom import (
    custom_endpoint_auth_missing_message,
    custom_endpoint_secret,
    get_custom_endpoint,
    is_custom_openai_provider,
)

if TYPE_CHECKING:
    from row_bot.providers.reasoning import ReasoningRequestPlan


@dataclass(frozen=True, repr=False)
class CapturedChatRuntime:
    """Private immutable request selection; never persist credentials or expose it."""

    resolved: Any
    credential: str = field(repr=False)
    reasoning_plan: Any = None
    context_size: int | None = None
    organization: str = ""
    project: str = ""
    oauth_snapshot: Any = field(default=None, repr=False)
    headers: dict[str, str] = field(default_factory=dict, repr=False)
    capabilities: dict[str, Any] = field(default_factory=dict)


def capture_chat_runtime(
    model_name: str, provider_id: str | None = None, *, reasoning_plan=None,
) -> CapturedChatRuntime:
    """Capture canonical configuration/auth without creating a model or network call."""
    from dataclasses import replace
    from row_bot.providers.resolution import resolve_provider_config

    resolved = deepcopy(resolve_provider_config(model_name, provider_id, allow_legacy_local=False))
    provider = resolved.provider_id
    capabilities = deepcopy(_capability_snapshot_for_selection(resolved.runtime_model,provider))
    import json
    if len(json.dumps(capabilities,allow_nan=False).encode()) > 65536:
        raise ValueError("document_provider_capabilities_unavailable")
    ensure_chat_model_compatible(resolved.runtime_model,provider,capability_snapshot=capabilities)
    if provider == "google":
        resolved = replace(resolved, base_url=resolved.base_url.removesuffix("/v1beta"))
    elif provider == "anthropic":
        resolved = replace(resolved, base_url=resolved.base_url.removesuffix("/v1"))
    plan = deepcopy(_validated_runtime_reasoning_plan(reasoning_plan, resolved.selection_ref))
    oauth_snapshot, headers = None, {}
    context_size = None
    if provider in {"codex", "claude_subscription", "xai_oauth"}:
        from row_bot.providers.auth_store import read_provider_oauth_bundle_snapshot
        from row_bot.providers import codex, claude_subscription, xai_oauth
        oauth_snapshot = deepcopy(read_provider_oauth_bundle_snapshot(provider))
        owner = {"codex":codex,"claude_subscription":claude_subscription,"xai_oauth":xai_oauth}[provider]
        credentials = getattr(owner, provider + "_runtime_credentials")(refresh_if_needed=False, _snapshot=oauth_snapshot)
        credential = credentials.access_token
        if not credential or (provider == "codex" and not credentials.account_id):
            raise ValueError("document_provider_unavailable")
        if provider == "codex":
            from row_bot.providers.transports.codex_responses import CODEX_RESPONSES_BASE_URL
            resolved = replace(resolved,base_url=CODEX_RESPONSES_BASE_URL)
        elif provider == "xai_oauth":
            # Unlike the compatibility getter, this read must never repair config.
            base = xai_oauth._validated_xai_base_url(str(oauth_snapshot[1].get("base_url") or xai_oauth.XAI_OAUTH_BASE_URL))
            resolved = replace(resolved, base_url=base)
            headers = {"User-Agent":xai_oauth.xai_oauth_user_agent()}
        elif provider == "claude_subscription":
            resolved = replace(resolved, base_url=claude_subscription.CLAUDE_SUBSCRIPTION_API_ROOT_URL)
            headers = claude_subscription.claude_subscription_oauth_headers(accept="",allow_cli_probe=False)
    elif provider == "ollama":
        from row_bot.models import _ollama_base_url, _ollama_runtime_model_name, get_context_size
        resolved = replace(resolved, base_url=_ollama_base_url(),
                           runtime_model=_ollama_runtime_model_name(resolved.selection_ref,allow_probe=False))
        context_size = get_context_size(resolved.selection_ref,allow_probe=False)
        credential = ""
    elif is_custom_openai_provider(provider):
        endpoint = resolved.endpoint
        if not endpoint or endpoint.get("enabled", True) is not True:
            raise ValueError("document_provider_unavailable")
        credential = custom_endpoint_secret(provider) or ""
        if endpoint.get("auth_required") and not credential:
            raise ValueError("document_provider_unavailable")
        credential = credential or "not-needed"
        if endpoint.get("supports_runtime_context_override"):
            from row_bot.models import get_context_policy
            context_size = get_context_policy(resolved.selection_ref,allow_probe=False).effective_limit_tokens
    else:
        credential = get_provider_secret(provider) or ""
        if not credential:
            raise ValueError("document_provider_unavailable")
    if provider in {"opencode_zen", "opencode_go"}:
        from row_bot.providers.opencode import opencode_model_route, opencode_base_url, opencode_anthropic_base_url
        route = opencode_model_route(provider,resolved.runtime_model)
        transport = route.transport.value
        base = opencode_anthropic_base_url(provider) if transport in {"anthropic_messages","google_genai"} else opencode_base_url(provider)
        resolved = replace(resolved,transport=route.transport,base_url=base,endpoint={
            "provider_id":provider,"base_url":base,"profile":"opencode","transport":transport})
    _captured_endpoint(resolved.base_url)
    if len(credential) > 16384:
        raise ValueError("document_provider_unavailable")
    return CapturedChatRuntime(resolved, credential, plan, context_size,
        oauth_snapshot=oauth_snapshot,headers=headers,capabilities=capabilities)


def _captured_endpoint(base_url: str) -> None:
    from urllib.parse import urlsplit
    parsed = urlsplit(base_url)
    if (not parsed.hostname or parsed.scheme not in {"http", "https"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("document_provider_endpoint_unavailable")


@contextmanager
def captured_http_clients(base_url: str, validate: Callable[[], None]) -> Iterator[tuple]:
    """Bound captured requests and observed response streams without automatic retry."""
    import asyncio
    import httpx
    from urllib.parse import unquote, urlsplit
    from row_bot.providers.transports.cancellable_http import (
        cancellable_http_client, cancellable_async_http_client,
    )
    _captured_endpoint(base_url)
    expected = urlsplit(base_url)
    def origin(parsed):
        port = parsed.port
        if port is None:
            port = {"http":80,"https":443}.get(parsed.scheme)
        return parsed.scheme, parsed.hostname, port

    def scoped_path(path):
        segments = []
        for segment in path.split("/"):
            first = None
            for _ in range(4):
                try:
                    decoded = unquote(segment, errors="strict")
                except UnicodeError as error:
                    raise ValueError("document_provider_endpoint_changed") from error
                if decoded in {".", ".."} or "/" in decoded or "\\" in decoded:
                    raise ValueError("document_provider_endpoint_changed")
                if first is None:
                    first = decoded
                if decoded == segment:
                    break
                segment = decoded
            else:
                raise ValueError("document_provider_endpoint_changed")
            segments.append(first)
        return "/".join(segments)

    expected_path = scoped_path(expected.path).rstrip("/")
    def check(request):
        validate()
        actual = urlsplit(str(request.url))
        actual_path = scoped_path(actual.path)
        if (origin(actual) != origin(expected)
                or not (actual_path == expected_path
                        or actual_path.startswith(expected_path + "/"))):
            raise ValueError("document_provider_endpoint_changed")
        request.extensions["row_bot_captured_deadline"] = time.monotonic() + 120
        request.headers["Accept-Encoding"] = "identity"
    async def async_check(request):
        check(request)

    def check_response(response, size):
        validate()
        if response.headers.get("Content-Encoding", "identity").strip().lower() not in {"", "identity"}:
            raise ValueError("document_provider_response_encoding")
        deadline = response.request.extensions.get("row_bot_captured_deadline")
        if deadline is None or time.monotonic() >= deadline:
            raise ValueError("document_provider_response_deadline")
        if size > 8 * 1024 * 1024:
            raise ValueError("document_provider_response_too_large")

    class BoundedStream(httpx.SyncByteStream):
        def __init__(self, response):
            self.response, self.inner, self.closed = response, response.stream, False

        def __iter__(self):
            size = 0
            try:
                for chunk in self.inner:
                    size += len(chunk)
                    check_response(self.response, size)
                    yield chunk
                check_response(self.response, size)
            except BaseException:
                try:
                    self.close()
                except BaseException:
                    pass  # Preserve the active authority, budget or transport error.
                raise
            else:
                self.close()

        def close(self):
            if not self.closed:
                self.closed = True
                self.inner.close()

    class BoundedAsyncStream(httpx.AsyncByteStream):
        def __init__(self, response):
            self.response, self.inner, self.closed = response, response.stream, False

        async def __aiter__(self):
            size = 0
            try:
                async for chunk in self.inner:
                    size += len(chunk)
                    check_response(self.response, size)
                    yield chunk
                check_response(self.response, size)
            except BaseException:
                try:
                    await self.aclose()
                except BaseException:
                    pass  # Preserve the active authority, budget or transport error.
                raise
            else:
                await self.aclose()

        async def aclose(self):
            if not self.closed:
                self.closed = True
                await self.inner.aclose()

    def response_check(response):
        try:
            check_response(response, len(response.content) if response.is_stream_consumed else 0)
            if not response.is_stream_consumed:
                response.stream = BoundedStream(response)
        except BaseException:
            try:
                response.close()
            except BaseException:
                pass
            raise

    async def async_response_check(response):
        try:
            check_response(response, len(response.content) if response.is_stream_consumed else 0)
            if not response.is_stream_consumed:
                response.stream = BoundedAsyncStream(response)
        except BaseException:
            try:
                await response.aclose()
            except BaseException:
                pass
            raise

    validate()
    sync = cancellable_http_client(trust_env=False, follow_redirects=False,
        timeout=120, event_hooks={"request":[check], "response":[response_check]})
    try:
        asynchronous = cancellable_async_http_client(trust_env=False, follow_redirects=False,
            timeout=120, event_hooks={"request":[async_check], "response":[async_response_check]})
    except BaseException:
        try:
            sync.close()
        except BaseException:
            pass
        raise

    def close_clients():
        error = None
        try:
            sync.close()
        except BaseException as caught:
            error = caught
        try:
            # This factory is for synchronous durable workers, never an event-loop API.
            asyncio.run(asynchronous.aclose())
        except BaseException:
            if error is None:
                raise
        if error is not None:
            raise error

    try:
        yield sync, asynchronous
    except BaseException:
        try:
            close_clients()
        except BaseException:
            pass
        raise
    else:
        close_clients()


class _CapturedValidation:
    """Keep an authority rejection intact when an SDK wraps request-hook errors."""
    def __init__(self, validate):
        self.validate, self.error = validate, None

    def __call__(self):
        if self.error is not None:
            raise self.error
        try:
            self.validate()
        except Exception as error:
            self.error = error
            raise


class _CapturedChatInvocation:
    def __init__(self, model, validate):
        self._model, self._validate = model, validate

    def invoke(self, messages):
        self._validate()
        try:
            result = self._model.invoke(messages)
        except Exception:
            if getattr(self._validate,"error",None) is not None:
                raise self._validate.error
            raise
        self._validate()
        return result


@contextmanager
def captured_chat_model(capture: CapturedChatRuntime, *, validate: Callable[[], None]) -> Iterator[Any]:
    """Construct an uncached exact-account model for a synchronous admitted worker."""
    if type(capture) is not CapturedChatRuntime:
        raise ValueError("document_provider_unavailable")
    capture = deepcopy(capture)
    validate = _CapturedValidation(validate)
    resolved = capture.resolved
    provider = resolved.provider_id
    transport = str(getattr(resolved.transport, "value", resolved.transport))
    validate()
    with captured_http_clients(resolved.base_url, validate) as (sync, asynchronous):
        if provider == "ollama":
            from langchain_ollama import ChatOllama
            from row_bot.providers.ollama import is_ollama_reasoning_model
            kwargs = {"model":resolved.runtime_model,"base_url":resolved.base_url,"num_ctx":capture.context_size,
                "client_kwargs":{"trust_env":False,"follow_redirects":False,"timeout":120,
                    "event_hooks":sync.event_hooks},
                "async_client_kwargs":{"trust_env":False,"follow_redirects":False,"timeout":120,
                    "event_hooks":asynchronous.event_hooks}}
            if is_ollama_reasoning_model(resolved.runtime_model):
                kwargs["reasoning"] = True
            kwargs.update(_reasoning_constructor_kwargs(capture.reasoning_plan, provider=provider))
            model = ChatOllama(**kwargs)
        elif provider == "ollama_cloud":
            from row_bot.providers.transports.ollama_cloud import ChatOllamaCloud
            model = ChatOllamaCloud(model_name=resolved.runtime_model,api_key=capture.credential,
                base_url=resolved.base_url,http_client=sync,reasoning_plan=capture.reasoning_plan)
        elif provider in {"codex", "xai_oauth"}:
            if provider == "codex":
                from row_bot.providers.transports.codex_responses import ChatCodexResponses as Transport
            else:
                from row_bot.providers.transports.xai_oauth_responses import ChatXAIOAuthResponses as Transport
            model = Transport(model_name=resolved.runtime_model,base_url=resolved.base_url,
                http_client=sync,reasoning_plan=capture.reasoning_plan)
            model.bind_captured_credentials(capture.oauth_snapshot,validate,headers=capture.headers)
        elif provider == "claude_subscription":
            import anthropic
            from row_bot.providers.transports.claude_subscription_messages import ChatClaudeSubscriptionMessages
            root = anthropic.Anthropic(api_key="",auth_token=capture.credential,
                base_url=resolved.base_url,default_headers=capture.headers,max_retries=0,http_client=sync)
            model = ChatClaudeSubscriptionMessages(model_name=resolved.runtime_model,
                base_url=resolved.base_url,anthropic_client=root,reasoning_plan=capture.reasoning_plan)
            model.bind_captured_credentials(capture.oauth_snapshot,validate)
        elif transport == "openai_chat" and (is_custom_openai_provider(provider) or provider in {"opencode_zen","opencode_go"}):
            from row_bot.providers.transports.openai_compatible import ChatOpenAICompatible
            model = ChatOpenAICompatible(model_name=resolved.runtime_model,api_key=capture.credential,
                base_url=resolved.base_url,endpoint=resolved.endpoint,http_client=sync,timeout=120,
                reasoning_plan=capture.reasoning_plan,captured_context_size=capture.context_size,
                context_captured=True)
        elif provider in {"openai", "xai", "requesty", "atlascloud"} or transport == "openai_responses":
            from langchain_openai import ChatOpenAI
            responses = transport == "openai_responses" or (
                provider == "openai" and openai_model_uses_responses_api(resolved.runtime_model))
            kwargs = {"model":resolved.runtime_model,"api_key":capture.credential,"base_url":resolved.base_url,
                "organization":capture.organization,"openai_proxy":"","max_retries":0,
                "http_client":sync,"http_async_client":asynchronous}
            # Explicit SDK clients prevent OPENAI_PROJECT/BASE_URL fallback as well.
            import openai
            headers = deepcopy(resolved.endpoint.get("headers") or {})
            root = openai.OpenAI(api_key=capture.credential, base_url=resolved.base_url, default_headers=headers,
                organization=capture.organization, project=capture.project, max_retries=0, http_client=sync)
            async_root = openai.AsyncOpenAI(api_key=capture.credential, base_url=resolved.base_url, default_headers=headers,
                organization=capture.organization, project=capture.project, max_retries=0, http_client=asynchronous)
            kwargs.update(root_client=root, root_async_client=async_root,
                          client=root.chat.completions, async_client=async_root.chat.completions)
            if responses:
                kwargs.update(use_responses_api=True, output_version="responses/v1")
            endpoint = resolved.endpoint
            if endpoint:
                baseline = _custom_endpoint_responses_baseline(endpoint)
                if baseline:
                    kwargs["extra_body"] = baseline
                if endpoint.get("headers"):
                    kwargs["default_headers"] = deepcopy(endpoint["headers"])
            kwargs.update(_reasoning_constructor_kwargs(capture.reasoning_plan, provider=provider, responses=responses))
            model = ChatOpenAI(**kwargs)
        elif provider in {"anthropic", "minimax"} or transport == "anthropic_messages":
            import anthropic
            from functools import cached_property
            from row_bot.providers.transports.anthropic_cancellable import CancellableChatAnthropic
            class CapturedAnthropic(CancellableChatAnthropic):
                @cached_property
                def _client(self):
                    return anthropic.Client(api_key=capture.credential, base_url=resolved.base_url,
                        max_retries=0, http_client=sync)
                @cached_property
                def _async_client(self):
                    return anthropic.AsyncClient(api_key=capture.credential, base_url=resolved.base_url,
                        max_retries=0, http_client=asynchronous)
            model = CapturedAnthropic(model=resolved.runtime_model, api_key=capture.credential,
                base_url=resolved.base_url, anthropic_proxy="", max_retries=0,
                **_reasoning_constructor_kwargs(capture.reasoning_plan, provider=provider))
        elif provider == "openrouter":
            import openrouter
            from row_bot.providers.transports.openrouter_cancellable import CancellableChatOpenRouter
            root = openrouter.OpenRouter(api_key=capture.credential, server_url=resolved.base_url,
                                        client=sync, async_client=asynchronous,retry_config=None)
            model = CancellableChatOpenRouter(model_name=resolved.runtime_model,
                openrouter_api_key=capture.credential, openrouter_api_base=resolved.base_url,
                max_retries=0, client=root,
                **_reasoning_constructor_kwargs(capture.reasoning_plan, provider=provider))
        elif provider == "google" or transport == "google_genai":
            from langchain_google_genai import ChatGoogleGenerativeAI
            from pydantic import model_validator
            client = captured_google_client(capture.credential, resolved.base_url, sync, asynchronous,
                **({"api_version":"v1"} if provider in {"opencode_zen","opencode_go"} else {}))
            class CapturedGoogle(ChatGoogleGenerativeAI):
                @model_validator(mode="after")
                def validate_environment(self):
                    # The canonical wrapper otherwise creates another SDK client
                    # whose Vertex selection can fall back to process environment.
                    self.client = client
                    self.default_metadata = ()
                    return self
            model = CapturedGoogle(model=resolved.runtime_model, google_api_key=capture.credential,
                vertexai=False, base_url=resolved.base_url, max_retries=0,
                **_reasoning_constructor_kwargs(capture.reasoning_plan, provider="google"))
        else:
            raise ValueError("document_provider_capture_unsupported")
        validate()
        try:
            yield _CapturedChatInvocation(model, validate)
        finally:
            if provider == "ollama":
                client = getattr(getattr(model, "_client", None), "_client", None)
                if client is not None:
                    client.close()
                asynchronous_client = getattr(getattr(model,"_async_client",None),"_client",None)
                if asynchronous_client is not None:
                    import asyncio
                    asyncio.run(asynchronous_client.aclose())


def captured_google_client(credential: str, base_url: str, sync, asynchronous, *, api_version: str | None = None):
    """Exact non-Vertex SDK client shared by captured chat/embedding factories."""
    from google import genai
    return genai.Client(api_key=credential, vertexai=False, http_options={
        "base_url":base_url,"timeout":120000,"retry_options":{"attempts":1},
        "httpx_client":sync,"httpx_async_client":asynchronous,
        **({"api_version":api_version} if api_version else {}),
    })


def is_provider_available(provider_id: str) -> bool:
    return bool(get_provider_secret(provider_id, "api_key"))


def list_configured_provider_ids() -> list[str]:
    configured = [
        provider_id for provider_id in (
            "openai",
            "ollama_cloud",
            "openrouter",
            "requesty",
            "opencode_zen",
            "opencode_go",
            "atlascloud",
            "anthropic",
            "google",
            "xai",
            "minimax",
        )
        if is_provider_available(provider_id)
    ]
    try:
        if provider_status("codex").get("configured"):
            configured.append("codex")
    except Exception:
        pass
    try:
        if provider_status("claude_subscription").get("configured"):
            configured.append("claude_subscription")
    except Exception:
        pass
    try:
        if provider_status("xai_oauth").get("configured"):
            configured.append("xai_oauth")
    except Exception:
        pass
    try:
        from row_bot.providers.custom import list_custom_endpoints
        configured.extend(str(endpoint["provider_id"]) for endpoint in list_custom_endpoints() if endpoint.get("enabled", True))
    except Exception:
        pass
    return configured


def provider_status(provider_id: str, *, refresh_tokens: bool = True) -> dict:
    if provider_id == "codex":
        from row_bot.providers.codex import check_codex_token_health
        from row_bot.providers.codex import discover_codex_credentials
        from row_bot.providers.config import load_provider_config

        token_health_started = time.perf_counter()
        token_health = check_codex_token_health(refresh_if_needed=refresh_tokens)
        token_health_ms = (time.perf_counter() - token_health_started) * 1000.0
        token_status = provider_secret_status("codex", "access_token")
        provider_cfg = load_provider_config().get("providers", {}).get("codex", {})
        external_configured = bool(
            provider_cfg.get("source") == "external_cli"
            and provider_cfg.get("external_reference_exists")
        )
        discovered = discover_codex_credentials()
        configured = bool(token_status.get("configured") or external_configured)
        source = ""
        if token_status.get("configured"):
            source = str(provider_cfg.get("source") or token_status.get("source") or "keyring")
        elif external_configured:
            source = "external_cli"
        elif provider_cfg.get("source") == "external_cli":
            source = "external_cli"
        elif discovered.get("exists") or discovered.get("cli_installed"):
            source = "external_cli_detected"
        return {
            "provider_id": provider_id,
            "configured": configured,
            "source": source,
            "fingerprint": token_status.get("fingerprint") or provider_cfg.get("fingerprint") or "",
            "auth_method": provider_cfg.get("auth_method") or "",
            "expires_at": provider_cfg.get("expires_at") or "",
            "account_id_hash": provider_cfg.get("account_id_hash") or "",
            "plan_type": provider_cfg.get("plan_type") or "",
            "external_reference_label": provider_cfg.get("external_reference_label") or discovered.get("label") or "",
            "external_reference_path_hash": provider_cfg.get("external_reference_path_hash") or discovered.get("path_hash") or "",
            "external_reference_exists": bool(provider_cfg.get("external_reference_exists") or discovered.get("exists")),
            "cli_installed": bool(discovered.get("cli_installed")),
            "runtime_enabled": token_health.runnable,
            "token_health": token_health.status,
            "token_health_detail": token_health.detail,
            "token_refresh_allowed": bool(refresh_tokens),
            "token_health_refreshed": token_health.status == "refreshed",
            "token_health_ms": round(token_health_ms, 3),
            "last_error": provider_cfg.get("last_error") or "",
        }
    if provider_id == "claude_subscription":
        from row_bot.providers.claude_subscription import (
            CLAUDE_SUBSCRIPTION_PROVIDER_ID,
            check_claude_subscription_token_health,
            discover_claude_subscription_credentials,
        )
        from row_bot.providers.config import load_provider_config

        token_health_started = time.perf_counter()
        token_health = check_claude_subscription_token_health(refresh_if_needed=refresh_tokens)
        token_health_ms = (time.perf_counter() - token_health_started) * 1000.0
        token_status = provider_secret_status(CLAUDE_SUBSCRIPTION_PROVIDER_ID, "access_token")
        provider_cfg = load_provider_config().get("providers", {}).get(CLAUDE_SUBSCRIPTION_PROVIDER_ID, {})
        external_configured = bool(
            provider_cfg.get("source") == "external_cli"
            and provider_cfg.get("external_reference_exists")
        )
        discovered = discover_claude_subscription_credentials()
        configured = bool(token_status.get("configured") or external_configured)
        source = ""
        if token_status.get("configured"):
            source = str(provider_cfg.get("source") or token_status.get("source") or "keyring")
        elif external_configured:
            source = "external_cli"
        elif provider_cfg.get("source") == "external_cli":
            source = "external_cli"
        elif discovered.get("exists") or discovered.get("cli_installed"):
            source = "external_cli_detected"
        return {
            "provider_id": provider_id,
            "configured": configured,
            "source": source,
            "fingerprint": token_status.get("fingerprint") or provider_cfg.get("fingerprint") or "",
            "auth_method": provider_cfg.get("auth_method") or "",
            "expires_at": provider_cfg.get("expires_at") or discovered.get("expires_at") or "",
            "account_id_hash": provider_cfg.get("account_id_hash") or discovered.get("account_id_hash") or "",
            "user_hash": provider_cfg.get("user_hash") or discovered.get("user_hash") or "",
            "plan_type": provider_cfg.get("plan_type") or "",
            "external_reference_label": provider_cfg.get("external_reference_label") or discovered.get("label") or "",
            "external_reference_path_hash": provider_cfg.get("external_reference_path_hash") or discovered.get("path_hash") or "",
            "external_reference_exists": bool(provider_cfg.get("external_reference_exists") or discovered.get("exists")),
            "external_reference_source": provider_cfg.get("external_reference_source") or "claude_code",
            "external_reference_metadata_only": True,
            "cli_installed": bool(provider_cfg.get("cli_installed") or discovered.get("cli_installed")),
            "cli_version": provider_cfg.get("cli_version") or discovered.get("cli_version") or "",
            "auth_status": provider_cfg.get("auth_status") or discovered.get("auth_status") or "",
            "runtime_enabled": token_health.runnable,
            "token_health": token_health.status,
            "token_health_detail": token_health.detail,
            "token_refresh_allowed": bool(refresh_tokens),
            "token_health_refreshed": token_health.status == "refreshed",
            "token_health_ms": round(token_health_ms, 3),
            "last_runtime_probe": dict(provider_cfg.get("last_runtime_probe") or {}),
            "last_error": provider_cfg.get("last_error") or "",
        }
    if provider_id == "xai_oauth":
        from row_bot.providers.config import load_provider_config
        from row_bot.providers.xai_oauth import (
            XAI_OAUTH_PROVIDER_ID,
            check_xai_oauth_token_health,
            xai_oauth_client_id_status,
        )

        token_health_started = time.perf_counter()
        token_health = check_xai_oauth_token_health(refresh_if_needed=refresh_tokens)
        token_health_ms = (time.perf_counter() - token_health_started) * 1000.0
        token_status = provider_secret_status(XAI_OAUTH_PROVIDER_ID, "access_token")
        refresh_status = provider_secret_status(XAI_OAUTH_PROVIDER_ID, "refresh_token")
        provider_cfg = load_provider_config().get("providers", {}).get(XAI_OAUTH_PROVIDER_ID, {})
        client_id_status = xai_oauth_client_id_status()
        configured = bool(token_status.get("configured") or refresh_status.get("configured"))
        return {
            "provider_id": XAI_OAUTH_PROVIDER_ID,
            "configured": configured,
            "source": str(token_status.get("source") or refresh_status.get("source") or provider_cfg.get("source") or ""),
            "fingerprint": token_status.get("fingerprint") or refresh_status.get("fingerprint") or provider_cfg.get("fingerprint") or "",
            "auth_method": provider_cfg.get("auth_method") or "",
            "expires_at": provider_cfg.get("expires_at") or "",
            "account_id_hash": provider_cfg.get("account_id_hash") or "",
            "user_hash": provider_cfg.get("user_hash") or "",
            "email_hash": provider_cfg.get("email_hash") or "",
            "scope": provider_cfg.get("scope") or "",
            "base_url": provider_cfg.get("base_url") or "",
            "runtime_enabled": token_health.runnable,
            "token_health": token_health.status,
            "token_health_detail": token_health.detail,
            "token_refresh_allowed": bool(refresh_tokens),
            "token_health_refreshed": token_health.status == "refreshed",
            "token_health_ms": round(token_health_ms, 3),
            "last_runtime_probe": dict(provider_cfg.get("last_runtime_probe") or {}),
            "last_vision_probe": dict(provider_cfg.get("last_vision_probe") or {}),
            "last_error": provider_cfg.get("last_error") or "",
            "model_count": provider_cfg.get("model_count"),
            "model_count_source": provider_cfg.get("model_count_source") or "",
            "model_count_status": provider_cfg.get("model_count_status") or "",
            "oauth_client_id_configured": bool(client_id_status.get("configured")),
            "oauth_client_id_source": str(client_id_status.get("source") or ""),
            "oauth_client_id_fingerprint": str(client_id_status.get("fingerprint") or ""),
            "oauth_client_id_detail": str(client_id_status.get("detail") or ""),
        }
    if provider_id == "ollama":
        try:
            from row_bot.models import _ollama_reachable, list_local_models
            running = _ollama_reachable()
            count = len(list_local_models()) if running else 0
        except Exception:
            running = False
            count = 0
        return {
            "provider_id": provider_id,
            "configured": running,
            "source": "local_daemon" if running else "not_running",
            "fingerprint": "",
            "model_count": count,
        }
    if provider_id == "atlascloud":
        from row_bot.providers.config import load_provider_config

        status = provider_secret_status(provider_id, "api_key")
        provider_cfg = load_provider_config().get("providers", {}).get("atlascloud", {})
        status["provider_id"] = provider_id
        if isinstance(provider_cfg.get("last_runtime_probe"), dict):
            status["last_runtime_probe"] = dict(provider_cfg.get("last_runtime_probe") or {})
        if isinstance(provider_cfg.get("runtime_probes"), dict):
            status["runtime_probes"] = {
                str(model_id): dict(probe)
                for model_id, probe in provider_cfg.get("runtime_probes", {}).items()
                if isinstance(probe, dict)
            }
        status["last_error"] = provider_cfg.get("last_error") or ""
        return status
    if is_custom_openai_provider(provider_id):
        endpoint = get_custom_endpoint(provider_id)
        configured = bool(endpoint and endpoint.get("base_url") and endpoint.get("enabled", True))
        status = provider_secret_status(provider_id, "api_key")
        return {
            "provider_id": provider_id,
            "configured": configured and (not endpoint.get("auth_required") or bool(status.get("configured"))),
            "source": status.get("source") or ("no_auth" if endpoint and not endpoint.get("auth_required") else ""),
            "fingerprint": status.get("fingerprint") or "",
            "base_url": endpoint.get("base_url") if endpoint else "",
        }
    status = provider_secret_status(provider_id, "api_key")
    status["provider_id"] = provider_id
    return status


def create_chat_model(
    model_name: str,
    provider_id: str | None = None,
    *,
    reasoning_plan: ReasoningRequestPlan | None = None,
):
    primary = _create_chat_model(model_name, provider_id, reasoning_plan=reasoning_plan)
    if reasoning_plan is None or reasoning_plan.is_default:
        return primary
    from row_bot.providers.resolution import resolve_provider_config
    from row_bot.providers.transports.reasoning_fallback import ReasoningFallbackChatModel

    resolved = resolve_provider_config(
        model_name,
        provider_id,
        allow_legacy_local=provider_id is not None,
    )
    provider_default = _create_chat_model(model_name, provider_id, reasoning_plan=None)
    return ReasoningFallbackChatModel(
        primary=primary,
        provider_default=provider_default,
        plan=reasoning_plan,
        provider_id=resolved.provider_id,
    )


def _create_chat_model(
    model_name: str,
    provider_id: str | None = None,
    *,
    reasoning_plan: ReasoningRequestPlan | None = None,
):
    """Create the LangChain chat model for an existing API-key provider."""
    from row_bot.providers.resolution import resolve_provider_config

    resolved = resolve_provider_config(
        model_name,
        provider_id,
        allow_legacy_local=provider_id is not None,
    )
    provider = resolved.provider_id
    model_name = resolved.runtime_model
    reasoning_plan = _validated_runtime_reasoning_plan(reasoning_plan, resolved.selection_ref)
    ensure_chat_model_compatible(model_name, provider)
    if provider == "ollama":
        from langchain_ollama import ChatOllama
        from row_bot.models import _ollama_base_url, _ollama_runtime_model_name, get_context_size
        from row_bot.providers.ollama import is_ollama_reasoning_model

        runtime_model = _ollama_runtime_model_name(resolved.selection_ref)
        kwargs = {
            "model": runtime_model,
            "base_url": _ollama_base_url(),
            "num_ctx": get_context_size(resolved.selection_ref),
        }
        if is_ollama_reasoning_model(runtime_model):
            kwargs["reasoning"] = True
        kwargs.update(_reasoning_constructor_kwargs(reasoning_plan, provider="ollama"))
        return ChatOllama(**kwargs)
    if provider == "ollama_cloud":
        from row_bot.providers.transports.ollama_cloud import ChatOllamaCloud

        api_key = get_provider_secret("ollama_cloud")
        if not api_key:
            raise ValueError("Ollama Cloud API key not configured. Set it in Settings -> Providers.")
        definition = get_provider_definition("ollama_cloud")
        base_url = definition.base_url if definition and definition.base_url else "https://ollama.com"
        return ChatOllamaCloud(
            model_name=model_name,
            api_key=api_key,
            base_url=base_url,
            reasoning_plan=reasoning_plan,
        )
    if provider in {"opencode_zen", "opencode_go"}:
        from row_bot.providers.opencode import (
            OpenCodeUnsupportedRouteError,
            opencode_anthropic_base_url,
            opencode_base_url,
            opencode_model_route,
        )

        provider_label = "OpenCode Zen" if provider == "opencode_zen" else "OpenCode Go"
        api_key = get_provider_secret(provider)
        if not api_key:
            raise ValueError(f"{provider_label} API key not configured. Set it in Settings -> Providers.")
        try:
            route = opencode_model_route(provider, model_name)
        except OpenCodeUnsupportedRouteError:
            raise
        transport = route.transport
        if transport == "openai_chat" or transport.value == "openai_chat":
            from row_bot.providers.transports.openai_compatible import ChatOpenAICompatible

            return ChatOpenAICompatible(
                model_name=model_name,
                api_key=api_key,
                base_url=opencode_base_url(provider),
                endpoint={
                    "provider_id": provider,
                    "display_name": provider_label,
                    "base_url": opencode_base_url(provider),
                    "transport": transport.value,
                    "profile": "opencode",
                },
                reasoning_plan=reasoning_plan,
            )
        if transport == "openai_responses" or transport.value == "openai_responses":
            from langchain_openai import ChatOpenAI
            from row_bot.providers.transports.cancellable_http import cancellable_http_client

            kwargs = _reasoning_constructor_kwargs(reasoning_plan, provider=provider, responses=True)
            return ChatOpenAI(
                model=model_name,
                api_key=api_key,
                base_url=opencode_base_url(provider),
                use_responses_api=True,
                output_version="responses/v1",
                http_client=cancellable_http_client(),
                **kwargs,
            )
        if transport == "anthropic_messages" or transport.value == "anthropic_messages":
            from row_bot.providers.transports.anthropic_cancellable import CancellableChatAnthropic

            return CancellableChatAnthropic(
                model=model_name,
                api_key=api_key,
                base_url=opencode_anthropic_base_url(provider),
                **_reasoning_constructor_kwargs(reasoning_plan, provider=provider),
            )
        if transport == "google_genai" or transport.value == "google_genai":
            from langchain_google_genai import ChatGoogleGenerativeAI

            return ChatGoogleGenerativeAI(
                model=model_name,
                google_api_key=api_key,
                base_url=opencode_anthropic_base_url(provider),
                api_version="v1",
                **_reasoning_constructor_kwargs(reasoning_plan, provider="google"),
            )
        raise OpenCodeUnsupportedRouteError(
            f"OpenCode route for {provider_label} model '{model_name}' uses unsupported/deferred transport {transport.value}."
        )
    if is_custom_openai_provider(provider):
        from row_bot.providers.transports.openai_compatible import ChatOpenAICompatible
        endpoint = get_custom_endpoint(provider)
        if not endpoint or not endpoint.get("base_url"):
            raise ValueError("Custom OpenAI-compatible endpoint is missing a base URL.")
        api_key = custom_endpoint_secret(provider)
        if endpoint.get("auth_required") and not api_key:
            raise ValueError(custom_endpoint_auth_missing_message(endpoint))
        api_key = api_key or "not-needed"
        if endpoint.get("transport") == "openai_responses":
            from langchain_openai import ChatOpenAI
            from row_bot.providers.transports.cancellable_http import cancellable_http_client

            kwargs = {
                "model": model_name,
                "api_key": api_key,
                "base_url": endpoint["base_url"],
                "http_client": cancellable_http_client(),
            }
            baseline = _custom_endpoint_responses_baseline(endpoint)
            if baseline:
                kwargs["extra_body"] = baseline
            headers = endpoint.get("headers")
            if isinstance(headers, dict) and headers:
                kwargs["default_headers"] = headers
            kwargs.update({"use_responses_api": True, "output_version": "responses/v1"})
            kwargs.update(_reasoning_constructor_kwargs(reasoning_plan, provider=provider, responses=True))
            return ChatOpenAI(**kwargs)
        return ChatOpenAICompatible(
            model_name=model_name,
            api_key=api_key,
            base_url=str(endpoint["base_url"]),
            endpoint=endpoint,
            reasoning_plan=reasoning_plan,
        )
    if provider == "codex":
        from row_bot.providers.codex import codex_runtime_available
        from row_bot.providers.transports.codex_responses import ChatCodexResponses

        if not codex_runtime_available():
            raise ValueError(
                "Codex subscription runtime needs an in-app ChatGPT login with runnable OAuth tokens. "
                "Connect ChatGPT in Settings -> Providers, then try the Codex model again."
            )
        return ChatCodexResponses(model_name=model_name, reasoning_plan=reasoning_plan)
    if provider == "claude_subscription":
        from row_bot.providers.claude_subscription import claude_subscription_runtime_available
        from row_bot.providers.transports.claude_subscription_messages import ChatClaudeSubscriptionMessages

        if not claude_subscription_runtime_available():
            raise ValueError(
                "Claude Subscription runtime needs Row-Bot-owned OAuth tokens. "
                "Connect Claude Subscription in Settings -> Providers, then try the provider-qualified model again."
            )
        return ChatClaudeSubscriptionMessages(model_name=model_name, reasoning_plan=reasoning_plan)
    if provider == "xai_oauth":
        from row_bot.providers.transports.xai_oauth_responses import ChatXAIOAuthResponses
        from row_bot.providers.xai_oauth import xai_oauth_runtime_available

        if not xai_oauth_runtime_available():
            raise ValueError(
                "xAI Grok runtime needs Row-Bot-owned OAuth tokens. "
                "Connect xAI Grok in Settings -> Providers, then try the provider-qualified model again."
            )
        return ChatXAIOAuthResponses(model_name=model_name, reasoning_plan=reasoning_plan)
    if provider == "openai":
        from langchain_openai import ChatOpenAI
        from row_bot.providers.transports.cancellable_http import cancellable_http_client
        api_key = get_provider_secret("openai")
        if not api_key:
            raise ValueError("OpenAI API key not configured. Set it in Settings → Providers.")
        kwargs = {"model": model_name, "api_key": api_key, "http_client": cancellable_http_client()}
        if openai_model_uses_responses_api(model_name):
            kwargs.update({"use_responses_api": True, "output_version": "responses/v1"})
        kwargs.update(
            _reasoning_constructor_kwargs(
                reasoning_plan,
                provider="openai",
                responses=openai_model_uses_responses_api(model_name),
            )
        )
        return ChatOpenAI(**kwargs)
    if provider == "anthropic":
        from row_bot.providers.transports.anthropic_cancellable import CancellableChatAnthropic
        api_key = get_provider_secret("anthropic")
        if not api_key:
            raise ValueError("Anthropic API key not configured. Set it in Settings → Providers.")
        return CancellableChatAnthropic(
            model=model_name,
            api_key=api_key,
            **_reasoning_constructor_kwargs(reasoning_plan, provider="anthropic"),
        )
    if provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI
        api_key = get_provider_secret("google")
        if not api_key:
            raise ValueError("Google AI API key not configured. Set it in Settings → Providers.")
        return ChatGoogleGenerativeAI(
            model=model_name,
            google_api_key=api_key,
            **_reasoning_constructor_kwargs(reasoning_plan, provider="google"),
        )
    if provider == "xai":
        from langchain_xai import ChatXAI
        from row_bot.providers.transports.cancellable_http import cancellable_http_client
        api_key = get_provider_secret("xai")
        if not api_key:
            raise ValueError("xAI API key not configured. Set it in Settings → Providers.")
        return ChatXAI(
            model=model_name,
            api_key=api_key,
            http_client=cancellable_http_client(),
            **_reasoning_constructor_kwargs(reasoning_plan, provider="xai"),
        )
    if provider == "minimax":
        from row_bot.providers.transports.anthropic_cancellable import CancellableChatAnthropic
        api_key = get_provider_secret("minimax")
        if not api_key:
            raise ValueError("MiniMax API key not configured. Set it in Settings → Providers.")
        definition = get_provider_definition("minimax")
        api_url = definition.base_url if definition and definition.base_url else "https://api.minimax.io/anthropic"
        return CancellableChatAnthropic(
            model=model_name,
            api_key=api_key,
            base_url=api_url,
            **_reasoning_constructor_kwargs(reasoning_plan, provider="minimax"),
        )
    if provider == "atlascloud":
        from row_bot.providers.transports.openai_compatible import ChatOpenAICompatible

        api_key = get_provider_secret("atlascloud")
        if not api_key:
            raise ValueError("Atlas Cloud API key not configured. Set it in Settings → Providers.")
        definition = get_provider_definition("atlascloud")
        base_url = definition.base_url if definition and definition.base_url else "https://api.atlascloud.ai/v1"
        return ChatOpenAICompatible(
            model_name=model_name,
            api_key=api_key,
            base_url=base_url,
            endpoint={
                "provider_id": "atlascloud",
                "display_name": "Atlas Cloud",
                "base_url": base_url,
                "transport": "openai_chat",
                "profile": "atlascloud",
            },
            reasoning_plan=reasoning_plan,
        )

    if provider == "requesty":
        from row_bot.providers.transports.openai_compatible import ChatOpenAICompatible

        api_key = get_provider_secret("requesty")
        if not api_key:
            raise ValueError("Requesty API key not configured. Set it in Settings → Providers.")
        definition = get_provider_definition("requesty")
        base_url = definition.base_url if definition and definition.base_url else "https://router.requesty.ai/v1"
        return ChatOpenAICompatible(
            model_name=model_name,
            api_key=api_key,
            base_url=base_url,
            endpoint={
                "provider_id": "requesty",
                "display_name": "Requesty",
                "base_url": base_url,
                "transport": "openai_chat",
                "profile": "requesty",
            },
            reasoning_plan=reasoning_plan,
        )

    from row_bot.providers.transports.openrouter_cancellable import CancellableChatOpenRouter
    api_key = get_provider_secret("openrouter")
    if not api_key:
        raise ValueError("OpenRouter API key not configured. Set it in Settings → Providers.")
    return CancellableChatOpenRouter(
        model_name=model_name,
        openrouter_api_key=api_key,
        **_reasoning_constructor_kwargs(reasoning_plan, provider="openrouter"),
    )


def _validated_runtime_reasoning_plan(
    plan: ReasoningRequestPlan | None,
    selection_ref: str,
) -> ReasoningRequestPlan | None:
    if plan is None or plan.is_default:
        return None
    if plan.model_ref != selection_ref:
        raise ValueError("Reasoning request plan does not match the selected provider-qualified model.")
    from row_bot.providers.reasoning import validate_reasoning_selection

    validate_reasoning_selection(plan.selection, plan.capabilities)
    return plan


def _reasoning_constructor_kwargs(
    plan: ReasoningRequestPlan | None,
    *,
    provider: str,
    responses: bool = False,
) -> dict[str, Any]:
    if plan is None or plan.is_default:
        return {}
    selection = plan.selection
    capabilities = plan.capabilities
    style = capabilities.request_style if capabilities else ""
    if provider == "openrouter" or style == "openrouter":
        if selection.kind == "effort":
            return {"reasoning": {"effort": selection.effort}}
        if selection.kind in {"on", "off"}:
            return {"reasoning": {"enabled": selection.kind == "on"}}
        if selection.kind == "budget":
            return {"reasoning": {"max_tokens": selection.budget}}
    if provider in {"anthropic", "claude_subscription", "minimax"} or style == "anthropic":
        kwargs: dict[str, Any] = {}
        if selection.kind == "effort":
            kwargs["effort"] = selection.effort
            if capabilities and capabilities.thinking_mode == "adaptive":
                kwargs["thinking"] = {"type": "adaptive"}
        elif selection.kind == "budget":
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": selection.budget}
        elif selection.kind == "off":
            kwargs["thinking"] = {"type": "disabled"}
        return kwargs
    if provider == "google" or style.startswith("google_"):
        if selection.kind == "effort":
            return {"thinking_level": selection.effort}
        if selection.kind == "budget":
            return {"thinking_budget": selection.budget}
        if selection.kind == "off":
            return {"thinking_budget": 0}
    if provider in {"ollama", "ollama_cloud"} or style == "ollama":
        if selection.kind == "effort":
            return {"reasoning": selection.effort}
        if selection.kind in {"on", "off"}:
            return {"reasoning": selection.kind == "on"}
    if selection.kind == "effort":
        return {"reasoning_effort": selection.effort}
    return {}


def _custom_endpoint_responses_baseline(endpoint: dict[str, Any]) -> dict[str, Any]:
    baseline = dict(endpoint.get("extra_body") or {}) if isinstance(endpoint.get("extra_body"), dict) else {}
    mode = str(endpoint.get("reasoning_mode") or "auto").strip().lower()
    if mode == "on":
        baseline["reasoning"] = {"enabled": True}
    elif mode == "off":
        baseline["reasoning"] = {"enabled": False}
    budget = endpoint.get("thinking_budget")
    try:
        parsed_budget = int(budget or 0)
    except (TypeError, ValueError):
        parsed_budget = 0
    if parsed_budget > 0:
        reasoning = baseline.get("reasoning") if isinstance(baseline.get("reasoning"), dict) else {}
        baseline["reasoning"] = {**reasoning, "max_tokens": parsed_budget}
    return baseline


def ensure_chat_model_compatible(model_name: str, provider_id: str | None = None, *, capability_snapshot: dict | None = None) -> None:
    provider = provider_id or _infer_provider(model_name)
    snapshot = _capability_snapshot_for_selection(model_name, provider) if capability_snapshot is None else capability_snapshot
    if snapshot and not snapshot_supports_surface(snapshot, "chat"):
        raise ValueError(
            f"{model_name} is not compatible with chat for provider {provider}. "
            "Choose a chat-capable Quick Choice from Settings -> Providers."
        )


def _capability_snapshot_for_selection(model_name: str, provider_id: str) -> dict[str, Any]:
    if is_custom_openai_provider(provider_id):
        endpoint = get_custom_endpoint(provider_id)
        models = endpoint.get("models") if isinstance(endpoint, dict) else []
        if isinstance(models, list):
            for model in models:
                if not isinstance(model, dict):
                    continue
                if str(model.get("model_id") or model.get("id") or "") != model_name:
                    continue
                snapshot = model.get("capabilities_snapshot")
                return dict(snapshot) if isinstance(snapshot, dict) else {}
        return {}
    if provider_id in {"opencode_zen", "opencode_go"}:
        return {}
    from row_bot.providers.capability_resolution import resolve_capability_snapshot

    return resolve_capability_snapshot(provider_id, model_name)


def _cached_provider_capability_snapshot(provider_id: str, model_name: str) -> dict[str, Any]:
    from row_bot.providers.capability_resolution import cached_provider_capability_snapshot

    return cached_provider_capability_snapshot(provider_id, model_name)


def _infer_provider(model_name: str) -> str:
    from row_bot.providers.resolution import resolve_provider_config

    return resolve_provider_config(model_name, allow_legacy_local=False).provider_id


def openai_model_uses_responses_api(model_name: str) -> bool:
    """Return True for direct OpenAI models that are not chat-completions native."""
    bare = str(model_name or "").split("/")[-1].strip().lower()
    return bare.startswith("gpt-5")
