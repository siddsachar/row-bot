"""Shared GitHub account/auth helpers for Thoth features."""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from email.message import Message
from typing import Any

import api_keys
import secret_store

GITHUB_TOKEN_ENV = "GITHUB_TOKEN"
GH_TOKEN_ENV = "GH_TOKEN"
GITHUB_API_ROOT = "https://api.github.com"
USER_AGENT = "Thoth-GitHub/1.0"
_TOKEN_CACHE_TTL_SECONDS = 300
_STATUS_CACHE_TTL_SECONDS = 300
_token_cache: tuple[float, bool, "GitHubToken"] | None = None
_status_cache: tuple[float, str, "GitHubAccountStatus"] | None = None

GITHUB_STATE_NOT_CONFIGURED = "not_configured"
GITHUB_STATE_CONFIGURED_UNCHECKED = "configured_unchecked"
GITHUB_STATE_CONNECTED = "connected"
GITHUB_STATE_ANONYMOUS = "anonymous"
GITHUB_STATE_INVALID_TOKEN = "invalid_token"
GITHUB_STATE_RATE_LIMITED = "rate_limited"
GITHUB_STATE_SECONDARY_LIMITED = "secondary_limited"
GITHUB_STATE_OFFLINE = "offline"


@dataclass(frozen=True)
class GitHubToken:
    value: str = ""
    source: str = ""
    fingerprint: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.value)


@dataclass(frozen=True)
class GitHubRateLimit:
    limit: int = 0
    remaining: int = 0
    used: int = 0
    reset_epoch: int = 0
    resource: str = ""
    retry_after_seconds: int = 0
    limited: bool = False
    secondary: bool = False

    @property
    def reset_display(self) -> str:
        if not self.reset_epoch:
            return ""
        return datetime.fromtimestamp(self.reset_epoch, tz=timezone.utc).astimezone().strftime("%H:%M")


@dataclass(frozen=True)
class GitHubAccountStatus:
    connected: bool
    source: str = ""
    user: str = ""
    gh_installed: bool = False
    gh_authenticated: bool = False
    gh_path: str = ""
    message: str = ""
    fingerprint: str = ""
    rate_limit: GitHubRateLimit | None = None
    state: str = GITHUB_STATE_CONFIGURED_UNCHECKED
    authenticated: bool = False
    anonymous_ok: bool = False
    token_valid: bool = False
    token_limited: bool = False
    anonymous_rate_limit: GitHubRateLimit | None = None
    last_checked: float = 0.0
    action_label: str = ""
    settings_message: str = ""


def resolve_github_token(*, include_cli: bool = True, use_cache: bool = True) -> GitHubToken:
    """Return the best available GitHub token without logging or exposing it."""
    global _token_cache
    now = time.time()
    if use_cache and _token_cache is not None and now - _token_cache[0] < _TOKEN_CACHE_TTL_SECONDS:
        cached_include_cli = _token_cache[1]
        cached_token = _token_cache[2]
        if cached_include_cli == include_cli:
            return cached_token
        if include_cli and cached_token.configured:
            return cached_token

    env_value = os.environ.get(GITHUB_TOKEN_ENV) or os.environ.get(GH_TOKEN_ENV) or ""
    if env_value:
        token = GitHubToken(env_value, "environment", secret_store.fingerprint(env_value))
        _token_cache = (now, include_cli, token)
        return token

    saved = api_keys.get_key(GITHUB_TOKEN_ENV)
    if saved:
        token = GitHubToken(saved, "keyring", secret_store.fingerprint(saved))
        _token_cache = (now, include_cli, token)
        return token

    if include_cli:
        gh_token = _github_cli_token()
        if gh_token:
            token = GitHubToken(gh_token, "github_cli", secret_store.fingerprint(gh_token))
            _token_cache = (now, include_cli, token)
            return token

    token = GitHubToken()
    _token_cache = (now, include_cli, token)
    return token


def github_api_headers(*, user_agent: str = USER_AGENT, include_cli: bool = True) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": user_agent,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = resolve_github_token(include_cli=include_cli)
    if token.value:
        headers["Authorization"] = f"Bearer {token.value}"
    return headers


def github_public_api_headers(*, user_agent: str = USER_AGENT, include_cli: bool = True) -> dict[str, str]:
    """Return headers for public GitHub reads, omitting unusable auth.

    A revoked or exhausted token can make public GitHub endpoints fail even when
    anonymous access still has quota. Public callers such as Skills Hub should
    therefore include Authorization only after the token has been verified.
    """
    headers = _base_headers(user_agent)
    if not include_cli:
        return headers
    status = get_verified_github_account_status(use_cache=True)
    if status.connected:
        token = resolve_github_token(include_cli=True)
        if token.value:
            headers["Authorization"] = f"Bearer {token.value}"
    return headers


def _legacy_discovery_status() -> GitHubAccountStatus:
    """Return discovery status, or verified API status when requested.

    Discovery alone must not be presented as connected. A token or CLI account
    can exist while the API token is revoked, expired, or currently exhausted.
    """
    token = resolve_github_token(include_cli=False)
    gh_status = _github_cli_status()
    cli_token = resolve_github_token(include_cli=True) if not token.configured else token
    source = token.source or (cli_token.source if cli_token.configured else "")
    fingerprint = token.fingerprint or cli_token.fingerprint
    if not source:
        state = GITHUB_STATE_NOT_CONFIGURED
        message = "Not connected. Use GitHub CLI login or save a token."
        action = "Connect GitHub"
    else:
        state = GITHUB_STATE_CONFIGURED_UNCHECKED
        message = f"GitHub credential discovered via {source.replace('_', ' ')}. Check GitHub to verify access."
        action = "Check GitHub"
    return GitHubAccountStatus(
        connected=False,
        source=source,
        user=gh_status.user,
        gh_installed=gh_status.installed,
        gh_authenticated=gh_status.authenticated,
        gh_path=gh_status.path,
        message=message,
        fingerprint=fingerprint,
        state=state,
        action_label=action,
        settings_message=message,
    )


def get_verified_github_account_status(*, use_cache: bool = True, timeout: int = 10) -> GitHubAccountStatus:
    """Return GitHub API status after verifying the selected credential."""
    global _status_cache
    token = resolve_github_token(include_cli=True, use_cache=use_cache)
    gh_status = _github_cli_status()
    cache_key = f"{token.source}:{token.fingerprint}:{bool(token.value)}"
    now = time.time()
    if (
        use_cache
        and _status_cache is not None
        and _status_cache[1] == cache_key
        and now - _status_cache[0] < _STATUS_CACHE_TTL_SECONDS
    ):
        return _status_cache[2]

    if token.value:
        status = check_github_token_access(token, timeout=timeout)
        status = _merge_cli_status(status, gh_status)
        if status.connected:
            _status_cache = (now, cache_key, status)
            return status
        anonymous = check_github_anonymous_access(timeout=timeout)
        status = _with_anonymous_fallback(status, anonymous)
        _status_cache = (now, cache_key, status)
        return status

    anonymous = check_github_anonymous_access(timeout=timeout)
    anonymous = _merge_cli_status(anonymous, gh_status)
    _status_cache = (now, cache_key, anonymous)
    return anonymous


def check_github_token_access(token: GitHubToken | str, source: str = "", timeout: int = 10) -> GitHubAccountStatus:
    """Verify an explicit GitHub token against the public API."""
    token_obj = token if isinstance(token, GitHubToken) else GitHubToken(str(token or ""), source, secret_store.fingerprint(str(token or "")))
    source = source or token_obj.source or "token"
    headers = _base_headers(USER_AGENT)
    if token_obj.value:
        headers["Authorization"] = f"Bearer {token_obj.value}"
    now = time.time()
    try:
        payload, rate = _fetch_github_json(f"{GITHUB_API_ROOT}/rate_limit", headers=headers, timeout=timeout)
        if _rate_exhausted(rate):
            return GitHubAccountStatus(
                connected=False,
                source=source,
                message=rate_limit_message(rate),
                fingerprint=token_obj.fingerprint,
                rate_limit=rate,
                state=GITHUB_STATE_SECONDARY_LIMITED if rate.secondary else GITHUB_STATE_RATE_LIMITED,
                authenticated=True,
                token_valid=True,
                token_limited=True,
                last_checked=now,
                action_label="Use anonymous or wait",
                settings_message=rate_limit_message(rate),
            )
        user = _user_from_rate_limit_payload_bytes(payload)
        if not user:
            user = _fetch_authenticated_user(headers, timeout=timeout)
        message = "GitHub API access OK."
        return GitHubAccountStatus(
            connected=True,
            source=source,
            user=user,
            message=message,
            fingerprint=token_obj.fingerprint,
            rate_limit=rate,
            state=GITHUB_STATE_CONNECTED,
            authenticated=True,
            token_valid=True,
            last_checked=now,
            action_label="Connected",
            settings_message=message,
        )
    except urllib.error.HTTPError as exc:
        body = _safe_error_body(exc)
        rate = rate_limit_from_headers(exc.headers, status_code=exc.code, body=body)
        if exc.code == 401:
            message = "GitHub token is invalid or expired. Reconnect GitHub."
            state = GITHUB_STATE_INVALID_TOKEN
            action = "Reconnect GitHub"
            token_valid = False
            token_limited = False
        elif rate.limited:
            message = rate_limit_message(rate)
            state = GITHUB_STATE_SECONDARY_LIMITED if rate.secondary else GITHUB_STATE_RATE_LIMITED
            action = "Use anonymous or wait"
            token_valid = True
            token_limited = True
        else:
            message = f"GitHub API check failed: HTTP {exc.code}"
            state = GITHUB_STATE_OFFLINE
            action = "Check GitHub"
            token_valid = False
            token_limited = False
        return GitHubAccountStatus(
            connected=False,
            source=source,
            message=message,
            fingerprint=token_obj.fingerprint,
            rate_limit=rate,
            state=state,
            authenticated=token_valid,
            token_valid=token_valid,
            token_limited=token_limited,
            last_checked=now,
            action_label=action,
            settings_message=message,
        )
    except Exception as exc:
        message = f"GitHub API check failed: {exc}"
        return GitHubAccountStatus(
            connected=False,
            source=source,
            message=message,
            fingerprint=token_obj.fingerprint,
            state=GITHUB_STATE_OFFLINE,
            last_checked=now,
            action_label="Check GitHub",
            settings_message=message,
        )


def check_github_anonymous_access(timeout: int = 10) -> GitHubAccountStatus:
    """Check anonymous public GitHub API availability."""
    now = time.time()
    headers = _base_headers(USER_AGENT)
    try:
        payload, rate = _fetch_github_json(f"{GITHUB_API_ROOT}/rate_limit", headers=headers, timeout=timeout)
        if _rate_exhausted(rate):
            message = rate_limit_message(rate) or "Anonymous GitHub API limit reached."
            return GitHubAccountStatus(
                connected=False,
                message=message,
                rate_limit=rate,
                state=GITHUB_STATE_RATE_LIMITED,
                anonymous_ok=False,
                anonymous_rate_limit=rate,
                last_checked=now,
                action_label="Connect GitHub",
                settings_message=message,
            )
        message = "Using anonymous GitHub public access."
        return GitHubAccountStatus(
            connected=False,
            message=message,
            rate_limit=rate,
            state=GITHUB_STATE_ANONYMOUS,
            anonymous_ok=True,
            anonymous_rate_limit=rate,
            last_checked=now,
            action_label="Connect GitHub",
            settings_message=message,
        )
    except urllib.error.HTTPError as exc:
        body = _safe_error_body(exc)
        rate = rate_limit_from_headers(exc.headers, status_code=exc.code, body=body)
        state = GITHUB_STATE_SECONDARY_LIMITED if rate.secondary else GITHUB_STATE_RATE_LIMITED if rate.limited else GITHUB_STATE_OFFLINE
        message = rate_limit_message(rate) if rate.limited else f"Anonymous GitHub API check failed: HTTP {exc.code}"
        return GitHubAccountStatus(
            connected=False,
            message=message,
            rate_limit=rate,
            state=state,
            anonymous_ok=False,
            anonymous_rate_limit=rate,
            last_checked=now,
            action_label="Connect GitHub",
            settings_message=message,
        )
    except Exception as exc:
        message = f"Anonymous GitHub API check failed: {exc}"
        return GitHubAccountStatus(
            connected=False,
            message=message,
            state=GITHUB_STATE_OFFLINE,
            anonymous_ok=False,
            last_checked=now,
            action_label="Check GitHub",
            settings_message=message,
        )


def get_github_account_status(*, check_rate_limit: bool = False) -> GitHubAccountStatus:
    return get_verified_github_account_status(use_cache=True) if check_rate_limit else _legacy_discovery_status()


def check_github_access(timeout: int = 10) -> GitHubAccountStatus:
    clear_github_status_cache()
    return get_verified_github_account_status(use_cache=False, timeout=timeout)


def rate_limit_from_headers(
    headers: Message | dict[str, str] | None,
    *,
    status_code: int = 0,
    body: str = "",
) -> GitHubRateLimit:
    header_get = headers.get if headers is not None else lambda _key, _default=None: _default
    remaining = _int_header(header_get("x-ratelimit-remaining"))
    retry_after = _int_header(header_get("retry-after"))
    body_lower = (body or "").lower()
    secondary = "secondary rate limit" in body_lower or "abuse" in body_lower
    limited = status_code in {403, 429} and (
        remaining == 0
        or retry_after > 0
        or "rate limit" in body_lower
        or secondary
    )
    return GitHubRateLimit(
        limit=_int_header(header_get("x-ratelimit-limit")),
        remaining=remaining,
        used=_int_header(header_get("x-ratelimit-used")),
        reset_epoch=_int_header(header_get("x-ratelimit-reset")),
        resource=str(header_get("x-ratelimit-resource") or ""),
        retry_after_seconds=retry_after,
        limited=limited,
        secondary=secondary,
    )


def rate_limit_from_exception(exc: BaseException) -> GitHubRateLimit | None:
    if isinstance(exc, urllib.error.HTTPError):
        body = _safe_error_body(exc)
        rate = rate_limit_from_headers(exc.headers, status_code=exc.code, body=body)
        return rate if rate.limited else None
    return None


def rate_limit_message(rate: GitHubRateLimit | None) -> str:
    if rate is None:
        return ""
    exhausted = bool(rate.limit and rate.remaining <= 0)
    if not rate.limited and not exhausted:
        return ""
    if rate.secondary:
        wait = f" Retry after {rate.retry_after_seconds}s." if rate.retry_after_seconds else ""
        return "GitHub secondary rate limit reached." + wait
    reset = f" Resets at {rate.reset_display}." if rate.reset_display else ""
    return "GitHub rate limit reached. Connect GitHub in Settings -> Accounts for a higher limit." + reset


def _base_headers(user_agent: str = USER_AGENT) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "User-Agent": user_agent,
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _fetch_github_json(url: str, *, headers: dict[str, str], timeout: int) -> tuple[bytes, GitHubRateLimit]:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - explicit GitHub API probe
        body = response.read(20_000)
        status_code = int(getattr(response, "status", 200) or 200)
        return body, rate_limit_from_headers(response.headers, status_code=status_code)


def _rate_exhausted(rate: GitHubRateLimit | None) -> bool:
    if rate is None:
        return False
    if rate.limited or rate.secondary:
        return True
    return bool(rate.limit and rate.remaining <= 0)


def _user_from_rate_limit_payload_bytes(body: bytes) -> str:
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except Exception:
        return ""
    if isinstance(data, dict):
        login = data.get("login")
        if login:
            return str(login)
    return ""


def _fetch_authenticated_user(headers: dict[str, str], *, timeout: int) -> str:
    if "Authorization" not in headers:
        return ""
    try:
        body, _rate = _fetch_github_json(f"{GITHUB_API_ROOT}/user", headers=headers, timeout=timeout)
    except Exception:
        return ""
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    return str(data.get("login") or "")


def _merge_cli_status(status: GitHubAccountStatus, gh_status: Any) -> GitHubAccountStatus:
    user = status.user or getattr(gh_status, "user", "")
    return replace(
        status,
        user=user,
        gh_installed=bool(getattr(gh_status, "installed", False)),
        gh_authenticated=bool(getattr(gh_status, "authenticated", False)),
        gh_path=str(getattr(gh_status, "path", "") or ""),
    )


def _with_anonymous_fallback(
    token_status: GitHubAccountStatus,
    anonymous_status: GitHubAccountStatus,
) -> GitHubAccountStatus:
    message = token_status.message
    settings_message = token_status.settings_message or token_status.message
    if anonymous_status.anonymous_ok:
        fallback = " Public GitHub reads will use anonymous access until auth is repaired."
        message = (message + fallback).strip()
        settings_message = (settings_message + fallback).strip()
    return replace(
        token_status,
        anonymous_ok=anonymous_status.anonymous_ok,
        anonymous_rate_limit=anonymous_status.anonymous_rate_limit or anonymous_status.rate_limit,
        message=message,
        settings_message=settings_message,
    )


def _github_cli_status():
    try:
        from developer.github import get_gh_status

        return get_gh_status(timeout=4)
    except Exception:
        try:
            from developer.github import GhStatus

            return GhStatus(False, False, message="Unable to check GitHub CLI.")
        except Exception:
            @dataclass(frozen=True)
            class _FallbackGhStatus:
                installed: bool = False
                authenticated: bool = False
                version: str = ""
                user: str = ""
                message: str = "Unable to check GitHub CLI."
                path: str = ""

            return _FallbackGhStatus()


def _github_cli_token(timeout: int = 6) -> str:
    try:
        from developer.executables import resolve_github_cli

        gh_path = resolve_github_cli()
        if not gh_path:
            return ""
        proc = subprocess.run(
            [gh_path, "auth", "token"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except Exception:
        return ""
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip()


def _user_from_rate_limit_payload(body: bytes) -> str:
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except Exception:
        return ""
    resources = data.get("resources") if isinstance(data, dict) else None
    core = resources.get("core") if isinstance(resources, dict) else None
    return str(core.get("resource") or "") if isinstance(core, dict) else ""


def _safe_error_body(exc: urllib.error.HTTPError) -> str:
    try:
        return exc.read(20_000).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _int_header(value: object) -> int:
    try:
        return int(str(value or "0"))
    except Exception:
        return 0


def clear_github_token_cache() -> None:
    global _token_cache
    _token_cache = None


def clear_github_status_cache() -> None:
    global _status_cache
    _status_cache = None


def clear_github_caches() -> None:
    clear_github_token_cache()
    clear_github_status_cache()


def _clear_token_cache_for_tests() -> None:
    clear_github_caches()
