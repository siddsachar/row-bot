from __future__ import annotations

from starlette.responses import JSONResponse

from row_bot.mobile.cookies import (
    HTTP_LAN_COOKIE_NAME,
    HTTPS_COOKIE_NAME,
    cookie_settings_for_scheme,
    extract_cookie_from_header,
    set_mobile_session_cookie,
)


def test_https_cookie_uses_host_prefix_and_secure_flags() -> None:
    settings = cookie_settings_for_scheme("https")

    assert settings["key"] == HTTPS_COOKIE_NAME
    assert settings["httponly"] is True
    assert settings["secure"] is True
    assert settings["samesite"] == "lax"
    assert settings["path"] == "/"


def test_http_cookie_uses_lan_name_without_secure_flag() -> None:
    settings = cookie_settings_for_scheme("http")

    assert settings["key"] == HTTP_LAN_COOKIE_NAME
    assert settings["httponly"] is True
    assert settings["secure"] is False
    assert settings["samesite"] == "lax"
    assert settings["path"] == "/"


def test_extract_cookie_prefers_scheme_specific_cookie() -> None:
    header = f"{HTTP_LAN_COOKIE_NAME}=lan-token; {HTTPS_COOKIE_NAME}=https-token"

    assert extract_cookie_from_header(header, scheme="http") == "lan-token"
    assert extract_cookie_from_header(header, scheme="https") == "https-token"


def test_set_cookie_does_not_put_token_in_response_body() -> None:
    response = JSONResponse({"ok": True})

    set_mobile_session_cookie(response, "secret-token", scheme="https")

    assert response.body == b'{"ok":true}'
    assert "secret-token" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "Secure" in response.headers["set-cookie"]
