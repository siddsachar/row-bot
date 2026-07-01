from __future__ import annotations

import json
import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from row_bot.plugins.bot_framework_auth import (
    BOT_FRAMEWORK_ISSUER,
    _reset_cache_for_tests,
    verify_bot_framework_jwt,
)


pytestmark = pytest.mark.subsystem

APP_ID = "00000000-0000-0000-0000-000000000000"
SERVICE_URL = "https://smba.trafficmanager.net/amer/"
METADATA_URL = "https://metadata.example/openid"
JWKS_URL = "https://metadata.example/keys"


def test_bot_framework_verifier_accepts_valid_teams_token_and_uses_cache() -> None:
    private_key = _private_key()
    token = _token(private_key, kid="teams-key")
    fetcher = _FakeOpenIdFetcher(
        jwks={"keys": [_jwk(private_key, kid="teams-key", endorsements=["msteams"])]}
    )
    _reset_cache_for_tests()

    first = verify_bot_framework_jwt(
        f"Bearer {token}",
        app_id=APP_ID,
        service_url=SERVICE_URL,
        channel_id="msteams",
        metadata_url=METADATA_URL,
        _fetch_json=fetcher,
    )
    second = verify_bot_framework_jwt(
        f"Bearer {token}",
        app_id=APP_ID,
        service_url=SERVICE_URL.rstrip("/"),
        channel_id="msteams",
        metadata_url=METADATA_URL,
        _fetch_json=fetcher,
    )

    assert first.ok is True
    assert first.status_code == 200
    assert first.key_id == "teams-key"
    assert first.claims["aud"] == APP_ID
    assert second.ok is True
    assert fetcher.urls == [METADATA_URL, JWKS_URL]


def test_bot_framework_verifier_rejects_wrong_audience() -> None:
    private_key = _private_key()
    token = _token(private_key, kid="teams-key", aud="wrong-app")
    fetcher = _FakeOpenIdFetcher(
        jwks={"keys": [_jwk(private_key, kid="teams-key", endorsements=["msteams"])]}
    )
    _reset_cache_for_tests()

    result = verify_bot_framework_jwt(
        f"Bearer {token}",
        app_id=APP_ID,
        service_url=SERVICE_URL,
        channel_id="msteams",
        metadata_url=METADATA_URL,
        _fetch_json=fetcher,
    )

    assert result.ok is False
    assert result.error == "bearer token validation failed"
    assert result.status_code == 401


def test_bot_framework_verifier_rejects_service_url_mismatch() -> None:
    private_key = _private_key()
    token = _token(private_key, kid="teams-key")
    fetcher = _FakeOpenIdFetcher(
        jwks={"keys": [_jwk(private_key, kid="teams-key", endorsements=["msteams"])]}
    )
    _reset_cache_for_tests()

    result = verify_bot_framework_jwt(
        f"Bearer {token}",
        app_id=APP_ID,
        service_url="https://smba.trafficmanager.net/emea/",
        channel_id="msteams",
        metadata_url=METADATA_URL,
        _fetch_json=fetcher,
    )

    assert result.ok is False
    assert result.error == "bearer token serviceUrl does not match the activity"
    assert result.claims["serviceurl"] == SERVICE_URL


def test_bot_framework_verifier_rejects_unendorsed_teams_key() -> None:
    private_key = _private_key()
    token = _token(private_key, kid="generic-key")
    fetcher = _FakeOpenIdFetcher(
        jwks={"keys": [_jwk(private_key, kid="generic-key", endorsements=["webchat"])]}
    )
    _reset_cache_for_tests()

    result = verify_bot_framework_jwt(
        f"Bearer {token}",
        app_id=APP_ID,
        service_url=SERVICE_URL,
        channel_id="msteams",
        metadata_url=METADATA_URL,
        _fetch_json=fetcher,
    )

    assert result.ok is False
    assert result.error == "signing key is not endorsed for msteams"


def test_bot_framework_verifier_rejects_missing_bearer_without_network() -> None:
    called = False

    def fetcher(_url: str, _timeout: float) -> dict[str, Any]:
        nonlocal called
        called = True
        return {}

    result = verify_bot_framework_jwt(
        "",
        app_id=APP_ID,
        service_url=SERVICE_URL,
        channel_id="msteams",
        metadata_url=METADATA_URL,
        _fetch_json=fetcher,
    )

    assert result.ok is False
    assert result.error == "missing bearer token"
    assert called is False


class _FakeOpenIdFetcher:
    def __init__(self, *, jwks: dict[str, Any]) -> None:
        self.jwks = jwks
        self.urls: list[str] = []

    def __call__(self, url: str, _timeout: float) -> dict[str, Any]:
        self.urls.append(url)
        if url == METADATA_URL:
            return {"jwks_uri": JWKS_URL}
        if url == JWKS_URL:
            return self.jwks
        raise AssertionError(f"unexpected fetch: {url}")


def _private_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwk(
    private_key: rsa.RSAPrivateKey,
    *,
    kid: str,
    endorsements: list[str],
) -> dict[str, Any]:
    payload = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    payload.update(
        {
            "kid": kid,
            "use": "sig",
            "alg": "RS256",
            "endorsements": endorsements,
        }
    )
    return payload


def _token(
    private_key: rsa.RSAPrivateKey,
    *,
    kid: str,
    aud: str = APP_ID,
    service_url: str = SERVICE_URL,
) -> str:
    now = int(time.time())
    claims = {
        "iss": BOT_FRAMEWORK_ISSUER,
        "aud": aud,
        "exp": now + 600,
        "nbf": now - 10,
        "iat": now - 10,
        "serviceurl": service_url,
    }
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": kid})
