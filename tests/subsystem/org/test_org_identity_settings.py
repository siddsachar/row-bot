"""Sign-in settings: identity providers, their credentials, the public origin, the pepper and session times.

Every refusal names the setting and never its value; the secret ones leave the environment.
"""

from __future__ import annotations

import json

import pytest

from row_bot_org.settings import OrgSettings, SettingsError, load_settings, remove_secret_variables

MARKER = "marker-value-5d2e"
TENANT = "6f1c2a3b-4d5e-4f60-8a7b-9c0d1e2f3a4b"
PEPPER = "p" * 40
ENTRA = {"name": "contoso", "kind": "entra", "issuer": f"https://login.microsoftonline.com/{TENANT}/v2.0",
         "client_id": "client-1", "allowed_tenants": [TENANT.upper()], "group_map": {"g-1": "engineering"}}
OKTA = {"name": "okta", "kind": "okta", "issuer": "https://example.okta.com/oauth2/default", "client_id": "client-2",
        "allowed_email_domains": ["Example.com"]}


def _env(idps: list[dict], secrets: dict | None = None, **extra: str) -> dict[str, str]:
    return {"ROW_BOT_ORG_IDPS": json.dumps(idps),
            "ROW_BOT_ORG_IDP_CLIENT_SECRETS": json.dumps(secrets if secrets is not None
                                                         else {idp["name"]: MARKER for idp in idps}),
            "ROW_BOT_ORG_PUBLIC_URL": "https://org.example.test", "ROW_BOT_ORG_HMAC_PEPPER": PEPPER, **extra}


def _problems(environ: dict[str, str]) -> list[str]:
    with pytest.raises(SettingsError) as raised:
        load_settings(environ)
    assert MARKER not in str(raised.value) and PEPPER not in str(raised.value)
    return raised.value.problems


def test_without_providers_nobody_can_sign_in() -> None:
    settings = load_settings({})

    assert (settings.idps, settings.public_url, settings.hmac_pepper) == ((), None, None)
    assert (settings.session_lifetime, settings.session_idle_timeout) == (12 * 3600, 3600)


def test_providers_are_read_with_their_credentials_and_never_shown() -> None:
    settings = load_settings(_env([ENTRA, OKTA], ROW_BOT_ORG_SESSION_LIFETIME="7200",
                                  ROW_BOT_ORG_SESSION_IDLE_TIMEOUT="600"))

    entra, okta = settings.idps
    assert (entra.name, entra.kind, entra.allowed_tenants, entra.group_map) == (
        "contoso", "entra", (TENANT,), (("g-1", "engineering"),))
    assert (okta.groups_claim, okta.allowed_email_domains, okta.credential) == ("groups", ("example.com",), MARKER)
    assert (settings.session_lifetime, settings.session_idle_timeout) == (7200, 600)
    assert MARKER not in repr(settings) and PEPPER not in repr(settings)


@pytest.mark.parametrize("authority", ["common", "organizations", "consumers", "{tenantid}"])
def test_an_entra_multi_tenant_authority_is_refused(authority: str) -> None:
    entry = {**ENTRA, "issuer": f"https://login.microsoftonline.com/{authority}/v2.0"}

    (problem,) = _problems(_env([entry]))
    assert "multi-tenant authority" in problem


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"allowed_tenants": []}, "needs allowed_tenants"),
        ({"allowed_tenants": None}, "allowed_tenants must be a list"),
        ({"allowed_tenants": ["not-a-tenant"]}, "allowed_tenants has an entry"),
        ({"issuer": "http://login.example.test/x"}, "issuer must be an https:// address"),
        ({"issuer": f"https://{MARKER}:pw@login.example.test/x"}, "without credentials"),
        ({"kind": "saml"}, "needs a kind"),
        ({"client_auth": "none"}, "client_auth must be one of"),
        ({"extra": MARKER}, "unknown keys: extra"),
        ({"groups_claim": "a b"}, "groups_claim isn't a valid claim name"),
        ({"name": "Not Valid"}, "needs a name"),
    ],
)
def test_a_bad_provider_entry_is_named_without_its_value(change: dict, expected: str) -> None:
    entry = {key: value for key, value in {**ENTRA, **change}.items() if value is not None}

    (problem,) = _problems(_env([entry], {entry.get("name", "x"): MARKER} if "name" not in change else {}))
    assert problem.startswith("ROW_BOT_ORG_IDPS ") and expected in problem


def test_a_loopback_http_issuer_is_allowed_for_a_provider_on_this_machine() -> None:
    entry = {**OKTA, "issuer": "http://127.0.0.1:9000/oauth2/default"}
    assert load_settings(_env([entry])).idps[0].issuer == entry["issuer"]


def test_allowed_tenants_are_for_entra_only() -> None:
    (problem,) = _problems(_env([{**OKTA, "allowed_tenants": [TENANT]}]))
    assert "for Entra only" in problem


def test_each_provider_needs_its_credential_and_no_credential_is_left_over() -> None:
    assert _problems(_env([OKTA], {})) == ["ROW_BOT_ORG_IDPS identity provider okta needs its client secret"]
    assert "credentials of stray" in _problems(_env([OKTA], {"okta": MARKER, "stray": MARKER}))[0]
    assert "private RSA or EC JWK" in _problems(_env([{**OKTA, "client_auth": "private_key_jwt"}]))[0]
    assert _problems({"ROW_BOT_ORG_IDP_CLIENT_SECRETS": json.dumps({"okta": MARKER})}) == [
        "ROW_BOT_ORG_IDP_CLIENT_SECRETS needs ROW_BOT_ORG_IDPS"]


def test_providers_need_the_public_url_and_the_pepper() -> None:
    environ = _env([OKTA])
    del environ["ROW_BOT_ORG_PUBLIC_URL"], environ["ROW_BOT_ORG_HMAC_PEPPER"]

    assert _problems(environ) == ["ROW_BOT_ORG_IDPS needs ROW_BOT_ORG_PUBLIC_URL",
                                  "ROW_BOT_ORG_IDPS needs ROW_BOT_ORG_HMAC_PEPPER"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [("https://Org.Example.test/", "https://org.example.test"), ("https://org.example.test:443", "https://org.example.test"),
     ("https://org.example.test:8443", "https://org.example.test:8443"), ("http://127.0.0.1:8100", "http://127.0.0.1:8100"),
     ("http://localhost:8100", "http://localhost:8100")],
)
def test_the_public_url_is_kept_as_an_origin(value: str, expected: str) -> None:
    assert load_settings({"ROW_BOT_ORG_PUBLIC_URL": value}).public_url == expected


@pytest.mark.parametrize("value", ["http://org.example.test", "https://org.example.test/console",
                                   "https://org.example.test/?x=1", f"https://{MARKER}@org.example.test", "org.example.test"])
def test_a_public_url_that_isnt_an_https_origin_is_refused(value: str) -> None:
    (problem,) = _problems({"ROW_BOT_ORG_PUBLIC_URL": value})
    assert problem.startswith("ROW_BOT_ORG_PUBLIC_URL must be an https:// origin")


def test_a_short_pepper_and_an_idle_timeout_past_the_lifetime_are_refused() -> None:
    assert _problems({"ROW_BOT_ORG_HMAC_PEPPER": "short"}) == [
        "ROW_BOT_ORG_HMAC_PEPPER must be 32 to 1024 printable characters (a random secret)"]
    assert _problems({"ROW_BOT_ORG_SESSION_LIFETIME": "600", "ROW_BOT_ORG_SESSION_IDLE_TIMEOUT": "3600"}) == [
        "ROW_BOT_ORG_SESSION_IDLE_TIMEOUT must not be longer than ROW_BOT_ORG_SESSION_LIFETIME"]
    assert "from 60 to 2592000" in _problems({"ROW_BOT_ORG_SESSION_LIFETIME": "59"})[0]


def test_the_pepper_and_client_secrets_leave_the_environment() -> None:
    environ = {**_env([OKTA]), "PATH": "unrelated"}

    remove_secret_variables(environ)

    assert set(environ) == {"ROW_BOT_ORG_IDPS", "ROW_BOT_ORG_PUBLIC_URL", "PATH"}
    assert OrgSettings().hmac_pepper is None
