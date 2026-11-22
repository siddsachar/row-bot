"""The gateway's server key (``ROW_BOT_ORG_GATEWAY_KEY``) and the sealing of endpoint keys with it.

The key is a secret: refusals never show it, it leaves the environment and the settings' ``repr``
doesn't carry it. A sealed endpoint key opens only with the same server key, on the same endpoint.
"""

from __future__ import annotations

import base64
import secrets

import pytest

from row_bot_org.gateway.keys import KeyUnavailable, seal, unseal
from row_bot_org.settings import SettingsError, load_settings, remove_secret_variables

RAW = secrets.token_bytes(32)


@pytest.mark.parametrize("encoded", [base64.urlsafe_b64encode(RAW).decode(),
                                     base64.b64encode(RAW).decode().rstrip("="),
                                     base64.b64encode(RAW).decode() + "\n"],
                         ids=["urlsafe", "standard-unpadded", "trailing-newline"])
def test_the_gateway_key_is_32_bytes_in_base64(encoded: str) -> None:
    settings = load_settings({"ROW_BOT_ORG_GATEWAY_KEY": encoded})
    assert settings.gateway_key == RAW
    assert encoded.strip() not in repr(settings) and "gateway_key" not in repr(settings)


def test_without_a_gateway_key_there_is_none() -> None:
    assert load_settings({}).gateway_key is None


@pytest.mark.parametrize("value", [base64.b64encode(secrets.token_bytes(16)).decode(), "not*base64!" * 4,
                                   base64.b64encode(secrets.token_bytes(48)).decode()])
def test_a_bad_gateway_key_is_refused_without_its_value(value: str) -> None:
    with pytest.raises(SettingsError) as raised:
        load_settings({"ROW_BOT_ORG_GATEWAY_KEY": value})
    assert raised.value.problems == ["ROW_BOT_ORG_GATEWAY_KEY must be 32 random bytes, base64-encoded"]
    assert value not in str(raised.value)


def test_the_gateway_key_comes_from_the_secrets_folder_and_leaves_the_environment(tmp_path) -> None:
    (tmp_path / "ROW_BOT_ORG_GATEWAY_KEY").write_text(base64.b64encode(RAW).decode() + "\n", encoding="utf-8")
    assert load_settings({"ROW_BOT_SECRETS_DIR": str(tmp_path)}).gateway_key == RAW

    environ = {"ROW_BOT_ORG_GATEWAY_KEY": "x", "ROW_BOT_ORG_PORT": "8100"}
    remove_secret_variables(environ)
    assert environ == {"ROW_BOT_ORG_PORT": "8100"}


def test_a_sealed_key_opens_only_with_its_server_key_on_its_endpoint() -> None:
    sealed = seal(RAW, "endpoint-1", "sk-endpoint-key")

    assert b"sk-endpoint-key" not in sealed
    assert unseal(RAW, "endpoint-1", sealed) == "sk-endpoint-key"
    assert seal(RAW, "endpoint-1", "sk-endpoint-key") != sealed  # a fresh nonce each time
    for key, endpoint in ((secrets.token_bytes(32), "endpoint-1"), (RAW, "endpoint-2")):
        with pytest.raises(KeyUnavailable):
            unseal(key, endpoint, sealed)
