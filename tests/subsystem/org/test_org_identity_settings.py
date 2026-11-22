"""Sign-in settings: the identity provider, its client secret, the public origin, the pepper, session times,
the sign-in rate limit and the trusted proxies.

Every refusal names the setting and never its value; the secret ones leave the environment.
"""

from __future__ import annotations

import json

import pytest

from row_bot_org.settings import OrgSettings, SettingsError, load_settings, remove_secret_variables

MARKER = "marker-value-5d2e"
TENANT = "6f1c2a3b-4d5e-4f60-8a7b-9c0d1e2f3a4b"
PEPPER = "p" * 40
ENTRA = {"kind": "entra", "issuer": f"https://login.microsoftonline.com/{TENANT}/v2.0",
         "client_id": "client-1", "allowed_tenants": [TENANT.upper()], "group_map": {"g-1": "engineering"}}
OKTA = {"kind": "okta", "issuer": "https://example.okta.com/oauth2/default", "client_id": "client-2",
        "allowed_email_domains": ["Example.com"]}


def _env(idp: dict, secret: str | None = MARKER, **extra: str) -> dict[str, str]:
    environ = {"ROW_BOT_ORG_IDP": json.dumps(idp), "ROW_BOT_ORG_PUBLIC_URL": "https://org.example.test",
               "ROW_BOT_ORG_HMAC_PEPPER": PEPPER, **extra}
    if secret is not None:
        environ["ROW_BOT_ORG_IDP_CLIENT_SECRET"] = secret
    return environ


def _problems(environ: dict[str, str]) -> list[str]:
    with pytest.raises(SettingsError) as raised:
        load_settings(environ)
    assert MARKER not in str(raised.value) and PEPPER not in str(raised.value)
    return raised.value.problems


def test_without_a_provider_nobody_can_sign_in() -> None:
    settings = load_settings({})

    assert (settings.idp, settings.public_url, settings.hmac_pepper) == (None, None, None)
    assert (settings.session_lifetime, settings.session_idle_timeout) == (12 * 3600, 3600)
    assert (settings.sign_in_rate_limit, settings.trusted_proxies) == (60, ())


def test_the_provider_is_read_with_its_client_secret_and_never_shown() -> None:
    settings = load_settings(_env(ENTRA, ROW_BOT_ORG_SESSION_LIFETIME="7200", ROW_BOT_ORG_SESSION_IDLE_TIMEOUT="600"))

    entra = settings.idp
    assert (entra.kind, entra.allowed_tenants, entra.group_map, entra.client_secret) == (
        "entra", (TENANT,), (("g-1", "engineering"),), MARKER)
    assert (settings.session_lifetime, settings.session_idle_timeout) == (7200, 600)
    assert MARKER not in repr(settings) and PEPPER not in repr(settings)
    okta = load_settings(_env(OKTA)).idp
    assert (okta.groups_claim, okta.allowed_email_domains) == ("groups", ("example.com",))


def test_a_list_of_providers_is_refused() -> None:
    (problem,) = _problems(_env([OKTA]))  # type: ignore[arg-type]
    assert problem == "ROW_BOT_ORG_IDP must be a JSON object describing the identity provider"


@pytest.mark.parametrize("authority", ["common", "organizations", "consumers", "{tenantid}"])
def test_an_entra_multi_tenant_authority_is_refused(authority: str) -> None:
    entry = {**ENTRA, "issuer": f"https://login.microsoftonline.com/{authority}/v2.0"}

    (problem,) = _problems(_env(entry))
    assert "multi-tenant authority" in problem


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"allowed_tenants": []}, "needs allowed_tenants"),
        ({"allowed_tenants": None}, "allowed_tenants must be a list"),
        ({"allowed_tenants": ["not-a-tenant"]}, "allowed_tenants has an entry"),
        ({"issuer": "http://login.example.test/x"}, "issuer must be an https:// address"),
        ({"issuer": f"https://{MARKER}:pw@login.example.test/x"}, "without credentials"),
        ({"issuer": None}, "needs its issuer"),
        ({"kind": "saml"}, "needs a kind"),
        ({"extra": MARKER}, "unknown keys: extra"),
        ({"client_auth": "private_key_jwt"}, "unknown keys: client_auth"),
        ({"name": "contoso"}, "unknown keys: name"),
        ({"groups_claim": "a b"}, "groups_claim isn't a valid claim name"),
    ],
)
def test_a_bad_provider_is_named_without_its_value(change: dict, expected: str) -> None:
    entry = {key: value for key, value in {**ENTRA, **change}.items() if value is not None}

    (problem,) = _problems(_env(entry))
    assert problem.startswith("ROW_BOT_ORG_IDP ") and expected in problem


def test_a_loopback_http_issuer_is_allowed_for_a_provider_on_this_machine() -> None:
    entry = {**OKTA, "issuer": "http://127.0.0.1:9000/oauth2/default"}
    assert load_settings(_env(entry)).idp.issuer == entry["issuer"]


def test_allowed_tenants_are_for_entra_only() -> None:
    (problem,) = _problems(_env({**OKTA, "allowed_tenants": [TENANT]}))
    assert "for Entra only" in problem


GOOGLE = {"kind": "google", "issuer": "https://accounts.google.com", "client_id": "client-3"}


def test_google_needs_the_hosted_domains_whose_accounts_may_sign_in() -> None:
    # Without them any Google account in the world could sign in.
    for entry in (GOOGLE, {**GOOGLE, "hosted_domains": []}, {**GOOGLE, "allowed_email_domains": ["example.com"]}):
        (problem,) = _problems(_env(entry))
        assert problem.startswith("ROW_BOT_ORG_IDP (Google) needs hosted_domains")
    assert load_settings(_env({**GOOGLE, "hosted_domains": ["Example.com"]})).idp.hosted_domains == ("example.com",)
    assert _problems(_env({**OKTA, "hosted_domains": ["example.com"]})) == [
        "ROW_BOT_ORG_IDP hosted_domains is for Google only"]


def test_entra_takes_no_email_domains_because_it_sends_no_email_verified() -> None:
    (problem,) = _problems(_env({**ENTRA, "allowed_email_domains": ["contoso.com"]}))
    assert problem.startswith("ROW_BOT_ORG_IDP allowed_email_domains doesn't work with Entra")
    assert "allowed_tenants" in problem and "Assignment required" in problem


def test_the_provider_needs_its_client_secret_and_a_secret_needs_a_provider() -> None:
    assert _problems(_env(OKTA, secret=None)) == ["ROW_BOT_ORG_IDP needs ROW_BOT_ORG_IDP_CLIENT_SECRET"]
    assert _problems(_env(OKTA, secret=MARKER + "\x01")) == [
        "ROW_BOT_ORG_IDP_CLIENT_SECRET must not contain control characters"]
    assert _problems({"ROW_BOT_ORG_IDP_CLIENT_SECRET": MARKER}) == ["ROW_BOT_ORG_IDP_CLIENT_SECRET needs ROW_BOT_ORG_IDP"]


def test_the_provider_needs_the_public_url_and_the_pepper() -> None:
    environ = _env(OKTA)
    del environ["ROW_BOT_ORG_PUBLIC_URL"], environ["ROW_BOT_ORG_HMAC_PEPPER"]

    assert _problems(environ) == ["ROW_BOT_ORG_IDP needs ROW_BOT_ORG_PUBLIC_URL",
                                  "ROW_BOT_ORG_IDP needs ROW_BOT_ORG_HMAC_PEPPER"]


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


def test_trusted_proxies_are_addresses_or_networks() -> None:
    settings = load_settings({"ROW_BOT_ORG_TRUSTED_PROXIES": " 10.0.0.0/8, 172.18.0.2 ,fd00::/8,10.0.0.0/8"})
    assert settings.trusted_proxies == ("10.0.0.0/8", "172.18.0.2/32", "fd00::/8")

    for value in ("*", "proxy.internal", "10.0.0.1/8"):
        assert _problems({"ROW_BOT_ORG_TRUSTED_PROXIES": value}) == [
            "ROW_BOT_ORG_TRUSTED_PROXIES must be a comma-separated list of IP addresses or networks (10.0.0.0/8)"]


def test_the_sign_in_rate_limit_is_a_positive_count() -> None:
    assert load_settings({"ROW_BOT_ORG_SIGN_IN_RATE_LIMIT": "5"}).sign_in_rate_limit == 5
    for value in ("0", "-1", "10001", "lots"):
        assert _problems({"ROW_BOT_ORG_SIGN_IN_RATE_LIMIT": value}) == [
            "ROW_BOT_ORG_SIGN_IN_RATE_LIMIT must be a number of requests a minute from 1 to 10000"]


def test_the_pepper_and_client_secret_leave_the_environment() -> None:
    environ = {**_env(OKTA), "PATH": "unrelated"}

    remove_secret_variables(environ)

    assert set(environ) == {"ROW_BOT_ORG_IDP", "ROW_BOT_ORG_PUBLIC_URL", "PATH"}
    assert OrgSettings().hmac_pepper is None
