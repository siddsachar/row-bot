"""The OIDC relying party against the fake provider (tests/fixtures/oidc_idp.py), without a database:
discovery, the authorization request, and every way an ID token is refused.

Security-sensitive: the org tier runs these (marker ``org_deps``: they need the ``org`` group's joserfc).
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest

pytest.importorskip("joserfc", reason="joserfc is not installed: uv sync --locked --all-extras --group test --group org")

from row_bot_org.identity.config import IdpConfig, parse_idp  # noqa: E402
from row_bot_org.identity.oidc import (  # noqa: E402
    JWKS_REFETCH_INTERVAL,
    OidcProvider,
    SignInError,
    VerifiedIdentity,
)
from tests.fixtures.oidc_idp import CLIENT_SECRET, OTHER_TENANT, REDIRECT_URI, FakeIdp, running_idp  # noqa: E402

pytestmark = pytest.mark.org_deps

NOW = 1_900_000_000.0
NONCE = "the-nonce-of-this-sign-in"


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> float:
        return self.now


@pytest.fixture(params=["entra", "okta", "google"])
def idp(request: pytest.FixtureRequest) -> Iterator[FakeIdp]:
    with running_idp(request.param) as running:
        yield running


def _provider(idp: FakeIdp, clock: Clock | None = None, **overrides: object) -> OidcProvider:
    return OidcProvider(parse_idp(json.dumps(idp.config(**overrides)), CLIENT_SECRET), clock=clock or Clock())


def _claims(idp: FakeIdp, **overrides: object) -> dict:
    now = int(NOW)
    return idp.claims(**{"iat": now, "exp": now + 300, "nonce": NONCE, **({"nbf": now} if idp.shape == "entra" else {}),
                         **overrides})


def _validate(provider: OidcProvider, token: str) -> VerifiedIdentity:
    return provider.validate_id_token(token, nonce_matches=lambda nonce: nonce == NONCE)


def _refused(provider: OidcProvider, token: str) -> SignInError:
    with pytest.raises(SignInError) as raised:
        _validate(provider, token)
    return raised.value


def test_a_good_token_of_each_shape_gives_the_stable_subject_and_groups(idp: FakeIdp) -> None:
    identity = _validate(_provider(idp), idp.id_token(_claims(idp, subject="sub-42")))

    assert (identity.issuer, identity.subject, identity.display_name) == (idp.issuer, "sub-42", "Ada Example")
    expected_groups = {"entra": ("7c9e6679-7425-40de-944b-e07fc1f90ae7", "9b2f1c3e-0000-4000-8000-000000000001"),
                       "okta": ("Engineering", "Everyone"), "google": ()}[idp.shape]
    assert identity.groups == expected_groups
    # Entra's email carries no email_verified, so it isn't taken.
    assert identity.email == (None if idp.shape == "entra" else "ada@example.com")


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated")
@pytest.mark.parametrize("alg", ["PS256", "ES256", "EdDSA"])
def test_each_allowed_algorithm_verifies(alg: str) -> None:
    with running_idp("okta") as idp:
        idp.rotate(alg)
        assert _validate(_provider(idp), idp.id_token(_claims(idp))).subject == "person-1"


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("bad signature", "id_token_rejected"),
        ("alg none", "id_token_rejected"),
        ("HS256 keyed with the public key", "id_token_rejected"),
        ("wrong iss", "id_token_rejected"),
        ("wrong aud", "id_token_rejected"),
        ("several audiences without azp", "id_token_rejected"),
        ("wrong azp", "id_token_rejected"),
        ("wrong nonce", "id_token_rejected"),
        ("no nonce", "id_token_rejected"),
        ("expired", "id_token_rejected"),
        ("nbf in the future", "id_token_rejected"),
        ("iat in the future", "id_token_rejected"),
        ("unknown kid", "id_token_rejected"),
        ("no sub", "id_token_rejected"),
    ],
)
def test_a_bad_id_token_is_refused(case: str, reason: str) -> None:
    with running_idp("okta") as idp:
        provider = _provider(idp)
        good = _claims(idp)
        if case == "bad signature":
            header, payload, signature = idp.id_token(good).split(".")
            token = ".".join((header, payload, ("A" if signature[0] != "A" else "B") + signature[1:]))
        elif case == "alg none":
            token = idp.none_token(good)
        elif case == "HS256 keyed with the public key":
            token = idp.hs256_token(good, idp.keys[-1].as_pem(private=False))
        elif case == "unknown kid":
            from tests.fixtures.oidc_idp import new_key

            token = idp.id_token(good, key=new_key())
        else:
            changes = {
                "wrong iss": {"iss": idp.issuer + "/other"},
                "wrong aud": {"aud": "someone-else"},
                "several audiences without azp": {"aud": [idp.client_id, "someone-else"]},
                "wrong azp": {"aud": [idp.client_id, "someone-else"], "azp": "someone-else"},
                "wrong nonce": {"nonce": "another-sign-in"},
                "no nonce": {"nonce": None},
                "expired": {"exp": int(NOW) - 61},
                "nbf in the future": {"nbf": int(NOW) + 120},
                "iat in the future": {"iat": int(NOW) + 120},
                "no sub": {"sub": None},
            }[case]
            token = idp.id_token({key: value for key, value in {**good, **changes}.items() if value is not None})

        assert _refused(provider, token).reason == reason


def test_skew_of_under_a_minute_is_tolerated() -> None:
    with running_idp("okta") as idp:
        token = idp.id_token(_claims(idp, exp=int(NOW) - 30, nbf=int(NOW) + 30, iat=int(NOW) + 30))
        assert _validate(_provider(idp), token).subject == "person-1"


def test_several_audiences_with_this_client_as_azp_are_accepted() -> None:
    with running_idp("okta") as idp:
        token = idp.id_token(_claims(idp, aud=[idp.client_id, "api://other"], azp=idp.client_id))
        assert _validate(_provider(idp), token).subject == "person-1"


GOOGLE = "https://accounts.google.com"


def _google_provider(idp: FakeIdp) -> OidcProvider:
    """A provider configured with Google's real issuer, its documents served from memory (no network)."""
    import httpx

    def answer(request: httpx.Request) -> httpx.Response:
        if str(request.url) == GOOGLE + "/.well-known/openid-configuration":
            return httpx.Response(200, json={"issuer": GOOGLE, "authorization_endpoint": GOOGLE + "/o/oauth2/v2/auth",
                                             "token_endpoint": "https://oauth2.googleapis.com/token",
                                             "jwks_uri": "https://www.googleapis.com/oauth2/v3/certs"})
        if str(request.url) == "https://www.googleapis.com/oauth2/v3/certs":
            return httpx.Response(200, json={"keys": [key.as_dict(private=False) for key in idp.keys]})
        return httpx.Response(404)

    config = IdpConfig(kind="google", issuer=GOOGLE, client_id=idp.client_id, hosted_domains=("example.com",),
                       client_secret=CLIENT_SECRET)
    return OidcProvider(config, client=httpx.Client(transport=httpx.MockTransport(answer)), clock=Clock())


@pytest.mark.parametrize(
    ("iss", "accepted"),
    [(GOOGLE, True), ("accounts.google.com", True), ("http://accounts.google.com", False),
     ("accounts.google.com/", False), ("https://accounts.google.com.example.test", False), ("google.com", False)],
)
def test_googles_issuer_is_accepted_with_and_without_its_scheme_and_nothing_else(iss: str, accepted: bool) -> None:
    with running_idp("google") as idp:
        provider = _google_provider(idp)
        token = idp.id_token(_claims(idp, iss=iss))
        if accepted:
            # Both forms are the same issuer, so the same person is the same principal.
            assert _validate(provider, token).issuer == GOOGLE
        else:
            assert _refused(provider, token).reason == "id_token_rejected"


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        # A personal Google account, even one using a company address: no hd.
        ({"hd": None, "email": "ex-employee@example.com"}, "hosted_domain_not_allowed"),
        ({"hd": "gmail.com", "email": "mallory@gmail.com"}, "hosted_domain_not_allowed"),
        ({"hd": "example.com.evil.test"}, "hosted_domain_not_allowed"),
        ({"hd": ["example.com"]}, "hosted_domain_not_allowed"),
        ({"email_verified": False}, "email_not_verified"),
        ({"email_verified": None}, "email_not_verified"),
    ],
)
def test_google_admits_only_verified_accounts_of_the_hosted_domains(change: dict, reason: str) -> None:
    with running_idp("google") as idp:
        provider = _provider(idp)
        assert _validate(provider, idp.id_token(_claims(idp, hd="Example.COM"))).subject == "person-1"
        assert _refused(provider, idp.id_token(_claims(idp, **change))).reason == reason


def test_only_google_gets_an_issuer_without_its_scheme() -> None:
    with running_idp("okta") as idp:
        token = idp.id_token(_claims(idp, iss=idp.issuer.removeprefix("http://")))
        assert _refused(_provider(idp), token).reason == "id_token_rejected"


def test_entra_needs_an_allowed_tenant() -> None:
    with running_idp("entra") as idp:
        provider = _provider(idp)

        assert _refused(provider, idp.id_token(_claims(idp, tid=OTHER_TENANT))).reason == "tenant_not_allowed"
        assert _refused(provider, idp.id_token(_claims(idp, tid=None))).reason == "tenant_not_allowed"


def test_entra_group_overage_is_refused_with_an_admin_facing_reason() -> None:
    with running_idp("entra") as idp:
        provider = _provider(idp)
        overage = {"groups": None, "_claim_names": {"groups": "src1"},
                   "_claim_sources": {"src1": {"endpoint": "https://graph.microsoft.com/v1.0/users/x/getMemberObjects"}}}

        refused = _refused(provider, idp.id_token(_claims(idp, **overage)))
        assert refused.reason == "group_overage"
        assert "200 groups" in refused.detail
        assert _refused(provider, idp.id_token(_claims(idp, groups=None, hasgroups=True))).reason == "group_overage"


def test_a_group_map_keeps_only_mapped_groups_under_their_row_bot_names() -> None:
    with running_idp("okta") as idp:
        provider = _provider(idp, group_map={"Engineering": "engineering", "Absent": "absent"})
        assert _validate(provider, idp.id_token(_claims(idp))).groups == ("engineering",)


def test_allowed_email_domains_need_a_verified_email_in_one_of_them() -> None:
    with running_idp("okta") as idp:
        provider = _provider(idp, allowed_email_domains=["example.com"])

        assert _validate(provider, idp.id_token(_claims(idp))).email == "ada@example.com"
        unverified = _refused(provider, idp.id_token(_claims(idp, email_verified=False)))
        assert unverified.reason == "email_not_verified"
        # A string "true" is not a verified email.
        assert _refused(provider, idp.id_token(_claims(idp, email_verified="true"))).reason == "email_not_verified"
        elsewhere = _refused(provider, idp.id_token(_claims(idp, email="ada@example.com.evil.test")))
        assert elsewhere.reason == "email_domain_not_allowed"


def test_groups_that_arent_a_list_of_names_are_refused() -> None:
    with running_idp("okta") as idp:
        assert _refused(_provider(idp), idp.id_token(_claims(idp, groups="Engineering"))).reason == "id_token_rejected"


def test_a_rotated_key_is_fetched_and_refetching_is_rate_limited() -> None:
    with running_idp("okta") as idp:
        clock = Clock()
        provider = _provider(idp, clock)
        _validate(provider, idp.id_token(_claims(idp)))
        assert idp.requests.count("/jwks") == 1

        idp.rotate()
        clock.now += JWKS_REFETCH_INTERVAL
        # The new kid isn't known yet: the JWKS is fetched again, once.
        assert _validate(provider, idp.id_token(_claims(idp))).subject == "person-1"
        assert idp.requests.count("/jwks") == 2

        idp.rotate()
        # Within a minute of the last fetch, a token with an unknown kid doesn't make the server fetch again.
        assert _refused(provider, idp.id_token(_claims(idp))).reason == "id_token_rejected"
        assert idp.requests.count("/jwks") == 2
        clock.now += JWKS_REFETCH_INTERVAL
        assert _validate(provider, idp.id_token(_claims(idp))).subject == "person-1"
        assert idp.requests.count("/jwks") == 3


def test_a_key_dropped_from_the_jwks_stops_verifying_within_the_hour() -> None:
    with running_idp("okta") as idp:
        clock = Clock()
        provider = _provider(idp, clock)
        old = idp.keys[-1]
        _validate(provider, idp.id_token(_claims(idp)))
        idp.rotate()
        clock.now += 3600

        assert _refused(provider, idp.id_token(_claims(idp, exp=int(clock.now) + 300, iat=int(clock.now)),
                                               key=old)).reason == "id_token_rejected"


def test_a_discovery_document_for_another_issuer_is_refused() -> None:
    with running_idp("okta") as idp:
        idp.discovery_issuer = idp.issuer + "/"
        with pytest.raises(SignInError) as raised:
            _provider(idp).discovery()
        assert raised.value.reason == "idp_misconfigured"


def test_an_unreachable_or_oversized_provider_is_unavailable() -> None:
    import httpx

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    def oversized(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"{" + b" " * (300 * 1024) + b"}")

    with running_idp("okta") as idp:
        config = parse_idp(json.dumps(idp.config()), CLIENT_SECRET)
    for transport in (unreachable, oversized):
        provider = OidcProvider(config, client=httpx.Client(transport=httpx.MockTransport(transport)))
        with pytest.raises(SignInError) as raised:
            provider.discovery()
        assert raised.value.reason == "idp_unavailable"


def test_the_authorization_request_uses_pkce_s256_minimal_scopes_and_the_exact_redirect_uri() -> None:
    from urllib.parse import parse_qs, urlsplit

    with running_idp("google") as idp:
        url = _provider(idp).authorization_url(redirect_uri=REDIRECT_URI, state="s" * 43, nonce="n" * 43,
                                               code_challenge="c" * 43)
    query = {name: values[0] for name, values in parse_qs(urlsplit(url).query).items()}

    assert query == {"response_type": "code", "client_id": idp.client_id, "redirect_uri": REDIRECT_URI,
                     "scope": "openid email profile", "state": "s" * 43, "nonce": "n" * 43,
                     "code_challenge": "c" * 43, "code_challenge_method": "S256"}
