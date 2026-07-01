"""Bot Framework Connector authentication helpers for plugin channels."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse

import requests

from row_bot.plugins.api import BotFrameworkAuthResult

log = logging.getLogger(__name__)

BOT_FRAMEWORK_OPENID_METADATA_URL = (
    "https://login.botframework.com/v1/.well-known/openidconfiguration"
)
BOT_FRAMEWORK_ISSUER = "https://api.botframework.com"
DEFAULT_CACHE_TTL_SECONDS = 24 * 60 * 60
DEFAULT_CLOCK_SKEW_SECONDS = 300

_FetchJson = Callable[[str, float], dict[str, Any]]


@dataclass(frozen=True)
class _JwksCacheEntry:
    keys_by_kid: dict[str, dict[str, Any]]
    expires_at: float
    jwks_uri: str


_jwks_cache: dict[str, _JwksCacheEntry] = {}


def verify_bot_framework_jwt(
    authorization_header: str,
    *,
    app_id: str,
    service_url: str = "",
    channel_id: str = "",
    required_endorsement: str = "",
    metadata_url: str = "",
    issuer: str = "",
    timeout_seconds: float = 5.0,
    cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
    clock_skew_seconds: int = DEFAULT_CLOCK_SKEW_SECONDS,
    _fetch_json: _FetchJson | None = None,
    _now: Callable[[], float] | None = None,
) -> BotFrameworkAuthResult:
    """Verify a Bot Framework Connector bearer token and return fail-closed state."""
    token = _bearer_token(authorization_header)
    if not token:
        return _failure("missing bearer token")
    if not app_id:
        return _failure("app_id is required")

    metadata_endpoint = metadata_url or BOT_FRAMEWORK_OPENID_METADATA_URL
    expected_issuer = issuer or BOT_FRAMEWORK_ISSUER
    if not _is_https_url(metadata_endpoint):
        return _failure("metadata_url must be an HTTPS URL")

    try:
        import jwt
        from jwt import PyJWKError, PyJWTError
        from jwt.algorithms import RSAAlgorithm
    except Exception as exc:
        log.warning("PyJWT with crypto support is unavailable: %s", exc)
        return _failure("jwt verification support is unavailable")

    try:
        header = jwt.get_unverified_header(token)
    except PyJWTError:
        return _failure("invalid bearer token header")

    algorithm = str(header.get("alg") or "")
    key_id = str(header.get("kid") or "")
    if algorithm != "RS256":
        return _failure("unsupported bearer token algorithm", key_id=key_id)
    if not key_id:
        return _failure("bearer token is missing a key id")

    try:
        key = _get_signing_key(
            metadata_endpoint,
            key_id,
            timeout_seconds=max(0.1, float(timeout_seconds)),
            cache_ttl_seconds=max(1, int(cache_ttl_seconds)),
            fetch_json=_fetch_json or _request_json,
            now=(_now or time.time)(),
        )
    except Exception as exc:
        log.debug("Unable to resolve Bot Framework signing key: %s", exc)
        return _failure("unable to resolve bearer token signing key", key_id=key_id)

    endorsement_error = _endorsement_error(
        key,
        channel_id=channel_id,
        required_endorsement=required_endorsement,
    )
    if endorsement_error:
        return _failure(endorsement_error, key_id=key_id)

    try:
        signing_key = RSAAlgorithm.from_jwk(json.dumps(key))
    except PyJWKError:
        return _failure("invalid bearer token signing key", key_id=key_id)

    try:
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            audience=app_id,
            issuer=expected_issuer,
            leeway=max(0, int(clock_skew_seconds)),
            options={"require": ["aud", "exp", "iss"]},
        )
    except PyJWTError as exc:
        log.debug("Bot Framework JWT validation failed: %s", exc)
        return _failure("bearer token validation failed", key_id=key_id)

    service_error = _service_url_error(claims, expected_service_url=service_url)
    if service_error:
        return _failure(service_error, claims=claims, key_id=key_id)

    return BotFrameworkAuthResult(
        ok=True,
        claims=dict(claims),
        status_code=200,
        service_url=str(claims.get("serviceurl") or claims.get("serviceUrl") or ""),
        key_id=key_id,
    )


def _bearer_token(authorization_header: str) -> str:
    parts = str(authorization_header or "").strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return ""
    return parts[1].strip()


def _get_signing_key(
    metadata_url: str,
    key_id: str,
    *,
    timeout_seconds: float,
    cache_ttl_seconds: int,
    fetch_json: _FetchJson,
    now: float,
) -> dict[str, Any]:
    entry = _jwks_cache.get(metadata_url)
    if entry and entry.expires_at > now and key_id in entry.keys_by_kid:
        return entry.keys_by_kid[key_id]

    entry = _refresh_jwks_cache(
        metadata_url,
        timeout_seconds=timeout_seconds,
        cache_ttl_seconds=cache_ttl_seconds,
        fetch_json=fetch_json,
        now=now,
    )
    if key_id not in entry.keys_by_kid:
        raise KeyError(f"unknown signing key id: {key_id}")
    return entry.keys_by_kid[key_id]


def _refresh_jwks_cache(
    metadata_url: str,
    *,
    timeout_seconds: float,
    cache_ttl_seconds: int,
    fetch_json: _FetchJson,
    now: float,
) -> _JwksCacheEntry:
    metadata = fetch_json(metadata_url, timeout_seconds)
    jwks_uri = str(metadata.get("jwks_uri") or "")
    if not _is_https_url(jwks_uri):
        raise ValueError("OpenID metadata is missing an HTTPS jwks_uri")

    jwks = fetch_json(jwks_uri, timeout_seconds)
    keys = jwks.get("keys")
    if not isinstance(keys, list):
        raise ValueError("JWKS payload is missing keys")

    keys_by_kid = {
        str(key.get("kid")): key
        for key in keys
        if isinstance(key, dict) and key.get("kid")
    }
    entry = _JwksCacheEntry(
        keys_by_kid=keys_by_kid,
        expires_at=now + cache_ttl_seconds,
        jwks_uri=jwks_uri,
    )
    _jwks_cache[metadata_url] = entry
    return entry


def _request_json(url: str, timeout_seconds: float) -> dict[str, Any]:
    response = requests.get(url, timeout=timeout_seconds)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("response JSON must be an object")
    return payload


def _endorsement_error(
    key: dict[str, Any],
    *,
    channel_id: str,
    required_endorsement: str,
) -> str:
    endorsements = key.get("endorsements") or []
    endorsement_set = {
        str(endorsement).lower()
        for endorsement in endorsements
        if isinstance(endorsement, str)
    }
    channel = str(channel_id or "").lower()
    required = str(required_endorsement or "").lower()

    if not required and channel == "msteams":
        required = "msteams"
    if required and required not in endorsement_set:
        return f"signing key is not endorsed for {required}"
    if channel and endorsement_set and channel not in endorsement_set:
        return f"signing key is not endorsed for {channel}"
    return ""


def _service_url_error(claims: dict[str, Any], *, expected_service_url: str) -> str:
    expected = _normalize_service_url(expected_service_url)
    if not expected:
        return ""
    actual = _normalize_service_url(claims.get("serviceurl") or claims.get("serviceUrl"))
    if not actual:
        return "bearer token is missing serviceUrl"
    if actual != expected:
        return "bearer token serviceUrl does not match the activity"
    return ""


def _normalize_service_url(value: Any) -> str:
    return str(value or "").strip().rstrip("/")


def _is_https_url(value: str) -> bool:
    parsed = urlparse(str(value or ""))
    return parsed.scheme == "https" and bool(parsed.netloc)


def _failure(
    error: str,
    *,
    claims: dict[str, Any] | None = None,
    key_id: str = "",
) -> BotFrameworkAuthResult:
    return BotFrameworkAuthResult(
        ok=False,
        claims=dict(claims or {}),
        error=error,
        status_code=401,
        key_id=key_id,
    )


def _reset_cache_for_tests() -> None:
    _jwks_cache.clear()
