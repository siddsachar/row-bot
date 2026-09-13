"""Private request-scoped media credentials; never a persisted/wire DTO."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass, field
from urllib.parse import urlsplit


@dataclass(frozen=True, repr=False)
class CapturedMediaAuth:
    selection: str
    provider: str
    credential: str = field(repr=False)
    identity: str
    base_url: str
    organization: str = ""
    project: str = ""


@dataclass
class _Scope:
    auth: CapturedMediaAuth
    validate: Callable[[], None]
    clients: list = field(default_factory=list)


_current: ContextVar[_Scope | None] = ContextVar("captured_media_auth", default=None)


def validate_auth(auth: CapturedMediaAuth, selection: str) -> None:
    if (
        type(auth) is not CapturedMediaAuth
        or auth.selection != selection
        or selection.split("/", 1)[0] != auth.provider
        or not auth.credential
        or len(auth.credential) > 16384
        or not auth.identity
    ):
        raise ValueError("media_auth_unavailable")
    parsed = urlsplit(auth.base_url)
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.port not in {None, 443}
    ):
        raise ValueError("media_endpoint_unavailable")
    if auth.provider == "openai":
        allowed = auth.base_url.rstrip("/") == "https://api.openai.com/v1"
    elif auth.provider == "google":
        allowed = (
            auth.base_url.rstrip("/") == "https://generativelanguage.googleapis.com"
        )
    else:
        allowed = auth.provider in {"xai", "xai_oauth"} and (
            parsed.hostname == "x.ai" or (parsed.hostname or "").endswith(".x.ai")
        )
    if not allowed:
        raise ValueError("media_endpoint_unavailable")


@contextmanager
def media_auth_scope(
    auth: CapturedMediaAuth | None, *, selection: str, validate: Callable[[], None]
) -> Iterator[None]:
    if auth is not None:
        validate_auth(auth, selection)
    state = _Scope(auth, validate) if auth is not None else None
    token = _current.set(state)
    try:
        validate()
        yield
    finally:
        _current.reset(token)
        for client in reversed(state.clients if state else []):
            with suppress(Exception):
                client.close()


def current_media_auth(provider: str | None = None) -> CapturedMediaAuth | None:
    state = _current.get()
    if state is None:
        return None
    state.validate()
    if provider is not None and state.auth.provider != provider:
        raise ValueError("media_auth_unavailable")
    return state.auth


def register_media_client(client):
    state = _current.get()
    if state is None or len(state.clients) >= 4:
        with suppress(Exception):
            client.close()
        raise ValueError("media_client_budget_exceeded")
    state.clients.append(client)
    state.validate()
    return client


def google_client():
    from google import genai

    auth = current_media_auth("google")
    if auth is None:
        raise ValueError("media_auth_unavailable")
    return register_media_client(
        genai.Client(
            api_key=auth.credential,
            vertexai=False,
            http_options={
                "base_url": auth.base_url,
                "retry_options": {"attempts": 1},
                "client_args": {"trust_env": False, "follow_redirects": False},
                "async_client_args": {"trust_env": False, "follow_redirects": False},
            },
        )
    )


def openai_client():
    import openai

    auth = current_media_auth("openai")
    if auth is None:
        raise ValueError("media_auth_unavailable")
    transport = register_media_client(
        openai.DefaultHttpxClient(trust_env=False, follow_redirects=False)
    )
    return register_media_client(
        openai.OpenAI(
            api_key=auth.credential,
            base_url=auth.base_url,
            organization=auth.organization,
            project=auth.project,
            max_retries=0,
            http_client=transport,
        )
    )
