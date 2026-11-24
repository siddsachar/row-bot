"""The OpenID Connect relying party: discovery, the authorization request, the code exchange and the ID token.

One ``OidcProvider`` for the server's identity provider. What it checks, in order:

- **Discovery** (``<issuer>/.well-known/openid-configuration``), fetched with a size limit and a timeout,
  never following a redirect, HTTPS only (HTTP only to a loopback address). Its ``issuer`` must equal the
  configured issuer exactly; it is cached for an hour.
- **The authorization request** carries PKCE S256, ``state`` and ``nonce`` (made and kept by ``web``),
  the exact redirect URI and the minimal scopes ``openid email profile``: no ``offline_access``, so the
  server never holds an IdP refresh token (REVIEW-1 M9).
- **The ID token**: an algorithm on the allow-list (RS256, PS256, ES256, EdDSA; never ``none`` or HS*),
  a ``kid`` found in the JWKS (refetched when it is an hour old, or when an unknown ``kid`` appears, at
  most once a minute), a good signature, then ``iss`` (for Google's issuer also without its scheme, as
  OpenID Connect Core 3.1.3.7 allows for Google alone), ``aud`` (and ``azp`` with several audiences, or
  whenever present), ``exp``, ``iat`` and ``nbf`` with a minute's skew, ``nonce``, for Entra ``tid`` among
  the allowed tenants, for Google ``hd`` among the hosted domains and ``email_verified``, ``email`` only when ``email_verified`` is true, and the groups claim (Entra's
  group overage is refused: see ``_groups``).

Every failure is a ``SignInError`` with a short reason for the person's admin, never a token or code.
"""

from __future__ import annotations

import base64
import json
import math
import re
import threading
import time
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode

import httpx
from joserfc import jwk, jws, jwt
from joserfc.errors import JoseError

from row_bot_org.identity.config import IdpConfig, IdpConfigError, check_endpoint_url

# RFC 9864 renames EdDSA to Ed25519; providers still sign with "EdDSA", which stays on the list.
warnings.filterwarnings("ignore", message="EdDSA is deprecated", module=r"joserfc\.")

ALLOWED_ALGORITHMS = ("RS256", "PS256", "ES256", "EdDSA")
SCOPES = "openid email profile"
CLOCK_SKEW = 60
HTTP_TIMEOUT = 5.0
MAX_DOCUMENT_BYTES = 256 * 1024
MAX_ID_TOKEN_BYTES = 16 * 1024
DISCOVERY_TTL = 3600.0
JWKS_MAX_AGE = 3600.0
JWKS_REFETCH_INTERVAL = 60.0
MAX_GROUPS = 1000
MAX_GROUP_NAME = 256
# Google's ID tokens carry this issuer, or the same without "https://".
GOOGLE_ISSUER = "https://accounts.google.com"
_ERROR_CODE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
# An Entra object id (the ID token's oid): what Microsoft Graph names a person by.
_OBJECT_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class SignInError(Exception):
    """Sign-in can't go on. ``reason`` is a short code for the response and logs; ``detail`` explains it."""

    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}")


@dataclass(frozen=True)
class Discovery:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str


@dataclass(frozen=True)
class VerifiedIdentity:
    """Who the ID token says signed in: the stable ``(issuer, subject)`` and what may be shown of them."""

    issuer: str
    subject: str
    display_name: str
    email: str | None
    groups: tuple[str, ...]
    # Entra only: the person's object id (``oid``), which connectors match Graph's sharing by.
    entra_oid: str | None = None


def new_http_client() -> httpx.Client:
    return httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=False)


def _read_json(client: httpx.Client, method: str, url: str, **kwargs: Any) -> tuple[int, Any]:
    try:
        check_endpoint_url(url, what="the provider's endpoint")
    except IdpConfigError as problem:
        raise SignInError("idp_misconfigured", str(problem)) from None
    try:
        with client.stream(method, url, **kwargs) as response:
            body = bytearray()
            for chunk in response.iter_bytes():
                body += chunk
                if len(body) > MAX_DOCUMENT_BYTES:
                    raise SignInError("idp_unavailable", "the provider's answer is larger than 256 KiB")
            status = response.status_code
    except httpx.HTTPError as error:
        raise SignInError("idp_unavailable", f"the provider can't be reached ({type(error).__name__})") from None
    try:
        return status, json.loads(bytes(body))
    except ValueError:
        return status, None


def _number(claims: dict[str, Any], name: str, *, required: bool) -> float | None:
    value = claims.get(name)
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise SignInError("id_token_rejected", f"the ID token's {name} is missing or not a number")
    return float(value)


def _clean_text(value: object, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return "".join(c for c in value if ord(c) >= 32 and ord(c) != 127).strip()[:limit]


class OidcProvider:
    """The identity provider. Thread-safe: the discovery and JWKS caches are behind a lock."""

    def __init__(self, config: IdpConfig, *, client: httpx.Client | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.config = config
        self._issuers = {config.issuer}
        if config.kind == "google" and config.issuer == GOOGLE_ISSUER:
            self._issuers.add(GOOGLE_ISSUER.removeprefix("https://"))
        self._client = client or new_http_client()
        self._clock = clock
        self._lock = threading.Lock()
        self._discovery: tuple[float, Discovery] | None = None
        self._keys: dict[str, Any] = {}
        self._keys_fetched: float | None = None
        self._keys_tried: float | None = None

    def close(self) -> None:
        self._client.close()

    # -- discovery and keys ------------------------------------------------------------------------

    def discovery(self) -> Discovery:
        with self._lock:
            return self._discovery_locked()

    def _discovery_locked(self) -> Discovery:
        now = self._clock()
        if self._discovery is not None and now - self._discovery[0] < DISCOVERY_TTL:
            return self._discovery[1]
        url = self.config.issuer.rstrip("/") + "/.well-known/openid-configuration"
        status, document = _read_json(self._client, "GET", url)
        if status != 200 or not isinstance(document, dict):
            raise SignInError("idp_unavailable", f"the provider's discovery document answered {status}")
        if document.get("issuer") != self.config.issuer:
            raise SignInError("idp_misconfigured",
                              "the discovery document's issuer isn't exactly the configured issuer")
        try:
            found = Discovery(
                issuer=self.config.issuer,
                **{name: check_endpoint_url(document.get(name), what=f"the discovery document's {name}")
                   for name in ("authorization_endpoint", "token_endpoint", "jwks_uri")},
            )
        except IdpConfigError as problem:
            raise SignInError("idp_misconfigured", str(problem)) from None
        self._discovery = (now, found)
        return found

    def _fetch_keys_locked(self) -> None:
        self._keys_tried = self._clock()
        status, document = _read_json(self._client, "GET", self._discovery_locked().jwks_uri)
        if status != 200 or not isinstance(document, dict) or not isinstance(document.get("keys"), list):
            raise SignInError("idp_unavailable", f"the provider's JWKS answered {status}")
        keys: dict[str, Any] = {}
        for entry in document["keys"]:
            if not (isinstance(entry, dict) and isinstance(entry.get("kid"), str)
                    and entry.get("kty") in {"RSA", "EC", "OKP"} and entry.get("use", "sig") == "sig"
                    and "d" not in entry):
                continue
            try:
                keys[entry["kid"]] = jwk.import_key(entry)
            except (JoseError, ValueError, TypeError):
                continue
        self._keys = keys
        self._keys_fetched = self._keys_tried

    def _key_for(self, kid: str) -> Any:
        with self._lock:
            now = self._clock()
            if self._keys_fetched is None or now - self._keys_fetched >= JWKS_MAX_AGE:
                self._fetch_keys_locked()
            elif kid not in self._keys and (self._keys_tried is None
                                            or now - self._keys_tried >= JWKS_REFETCH_INTERVAL):
                # A new kid: the provider may have rotated its keys. At most once a minute, so tokens
                # with made-up kids can't make the server hammer the provider.
                self._fetch_keys_locked()
            key = self._keys.get(kid)
        if key is None:
            raise SignInError("id_token_rejected", "the ID token's signing key isn't in the provider's JWKS")
        return key

    # -- the flow ----------------------------------------------------------------------------------

    def authorization_url(self, *, redirect_uri: str, state: str, nonce: str, code_challenge: str) -> str:
        endpoint = self.discovery().authorization_endpoint
        query = urlencode({
            "response_type": "code",
            "client_id": self.config.client_id,
            "redirect_uri": redirect_uri,
            "scope": SCOPES,
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        })
        return endpoint + ("&" if "?" in endpoint else "?") + query

    def exchange_code(self, *, code: str, redirect_uri: str, code_verifier: str) -> str:
        """The ID token the token endpoint gives for ``code``. Its other tokens are dropped unread."""
        endpoint = self.discovery().token_endpoint
        # client_secret_basic. RFC 6749 2.3.1: each part form-encoded before the Basic encoding.
        pair = f"{quote(self.config.client_id, safe='')}:{quote(self.config.client_secret, safe='')}"
        basic = "Basic " + base64.b64encode(pair.encode("utf-8")).decode("ascii")
        status, answer = _read_json(self._client, "POST", endpoint,
                                    headers={"Accept": "application/json", "Authorization": basic},
                                    data={"grant_type": "authorization_code", "code": code,
                                          "redirect_uri": redirect_uri, "code_verifier": code_verifier})
        if status != 200 or not isinstance(answer, dict):
            error = answer.get("error") if isinstance(answer, dict) else None
            named = f" ({error})" if isinstance(error, str) and _ERROR_CODE.fullmatch(error) else ""
            raise SignInError("token_exchange_failed", f"the token endpoint answered {status}{named}")
        id_token = answer.get("id_token")
        if not isinstance(id_token, str) or not id_token:
            raise SignInError("token_exchange_failed", "the token endpoint gave no ID token")
        return id_token

    # -- the ID token ------------------------------------------------------------------------------

    def validate_id_token(self, id_token: str, *, nonce_matches: Callable[[str], bool]) -> VerifiedIdentity:
        """The identity in ``id_token``, after every check in the module's docstring."""
        if len(id_token) > MAX_ID_TOKEN_BYTES:
            raise SignInError("id_token_rejected", "the ID token is too large")
        try:
            header = jws.extract_compact(id_token.encode("ascii")).protected
        except (JoseError, ValueError, UnicodeError):
            raise SignInError("id_token_rejected", "the ID token isn't a signed JWT") from None
        alg = header.get("alg")
        if alg not in ALLOWED_ALGORITHMS:
            raise SignInError("id_token_rejected", "the ID token's algorithm isn't allowed")
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise SignInError("id_token_rejected", "the ID token names no signing key")
        key = self._key_for(kid)
        if key.get("alg") not in (None, alg):
            raise SignInError("id_token_rejected", "the ID token's algorithm isn't its key's")
        try:
            claims = jwt.decode(id_token, key, algorithms=[alg]).claims
        except JoseError:
            raise SignInError("id_token_rejected", "the ID token's signature doesn't verify") from None
        if not isinstance(claims, dict):
            raise SignInError("id_token_rejected", "the ID token has no claims")
        self._check_claims(claims, nonce_matches)
        subject = claims.get("sub")
        if not isinstance(subject, str) or not 0 < len(subject) <= 255:
            raise SignInError("id_token_rejected", "the ID token has no usable sub")
        email = self._email(claims)
        name = (_clean_text(claims.get("name"), 200) or _clean_text(claims.get("preferred_username"), 200)
                or (email or ""))
        oid = claims.get("oid") if self.config.kind == "entra" else None
        entra_oid = oid.lower() if isinstance(oid, str) and _OBJECT_ID.fullmatch(oid.lower()) else None
        return VerifiedIdentity(self.config.issuer, subject, name, email, self._groups(claims), entra_oid)

    def _check_claims(self, claims: dict[str, Any], nonce_matches: Callable[[str], bool]) -> None:
        config = self.config
        if claims.get("iss") not in self._issuers:
            raise SignInError("id_token_rejected", "the ID token's iss isn't the configured issuer")
        audience = claims.get("aud")
        audiences = [audience] if isinstance(audience, str) else audience
        if not (isinstance(audiences, list) and audiences and all(isinstance(a, str) for a in audiences)
                and config.client_id in audiences):
            raise SignInError("id_token_rejected", "the ID token's aud isn't this server's client id")
        azp = claims.get("azp")
        if (len(audiences) > 1 or azp is not None) and azp != config.client_id:
            raise SignInError("id_token_rejected", "the ID token's azp isn't this server's client id")
        now = self._clock()
        expires = _number(claims, "exp", required=True)
        issued = _number(claims, "iat", required=True)
        not_before = _number(claims, "nbf", required=False)
        if expires is not None and now >= expires + CLOCK_SKEW:
            raise SignInError("id_token_rejected", "the ID token has expired")
        if issued is not None and issued > now + CLOCK_SKEW:
            raise SignInError("id_token_rejected", "the ID token was issued in the future")
        if not_before is not None and not_before > now + CLOCK_SKEW:
            raise SignInError("id_token_rejected", "the ID token isn't valid yet (nbf)")
        nonce = claims.get("nonce")
        if not isinstance(nonce, str) or not nonce_matches(nonce):
            raise SignInError("id_token_rejected", "the ID token's nonce isn't this sign-in's")
        if config.kind == "entra":
            tenant = claims.get("tid")
            if not isinstance(tenant, str) or tenant.lower() not in config.allowed_tenants:
                raise SignInError("tenant_not_allowed", "the ID token's tenant (tid) isn't an allowed tenant")
        if config.kind == "google":
            # Any Google account can get a token for any Google client: only `hd` says the account is one
            # of the company's Workspace accounts (a personal account with a company address has none).
            hosted = claims.get("hd")
            if not isinstance(hosted, str) or hosted.lower() not in config.hosted_domains:
                raise SignInError("hosted_domain_not_allowed",
                                  "the Google account isn't in an allowed Workspace domain (hd)")
            if claims.get("email_verified") is not True:
                raise SignInError("email_not_verified", "the provider didn't say the email is verified")

    def _email(self, claims: dict[str, Any]) -> str | None:
        email = claims.get("email")
        verified = (isinstance(email, str) and 3 <= len(email) <= 320 and "@" in email
                    and claims.get("email_verified") is True)
        domains = self.config.allowed_email_domains
        if domains:
            if not verified:
                raise SignInError("email_not_verified", "sign-in here needs a verified email, and the provider "
                                  "didn't say this one is verified (email_verified)")
            if email.rsplit("@", 1)[1].lower() not in domains:
                raise SignInError("email_domain_not_allowed", "the email's domain isn't an allowed domain")
        return _clean_text(email, 320) if verified else None

    def _groups(self, claims: dict[str, Any]) -> tuple[str, ...]:
        claim = self.config.groups_claim
        names = claims.get("_claim_names")
        # Entra's group overage: past 200 groups the token carries a pointer to Microsoft Graph instead of
        # the groups. Sign-in is refused rather than done with no groups (which could hide a deny group)
        # or with a Graph call this server doesn't make yet.
        if (isinstance(names, dict) and claim in names) or claims.get("hasgroups") is True:
            raise SignInError(
                "group_overage",
                "the provider sent a reference to the groups instead of the groups (Entra's group overage, "
                "over 200 groups). An admin can limit the groups claim to the groups assigned to the "
                "application, or emit application roles instead",
            )
        raw = claims.get(claim, [])
        if not isinstance(raw, list) or len(raw) > MAX_GROUPS or not all(
            isinstance(group, str) and 0 < len(group) <= MAX_GROUP_NAME for group in raw
        ):
            raise SignInError("id_token_rejected", f"the ID token's {claim} claim isn't a list of group names")
        if self.config.group_map:
            mapping = dict(self.config.group_map)
            return tuple(sorted({mapping[group] for group in raw if group in mapping}))
        return tuple(sorted(set(raw)))


__all__ = ["ALLOWED_ALGORITHMS", "CLOCK_SKEW", "JWKS_REFETCH_INTERVAL", "SCOPES", "Discovery", "OidcProvider",
           "SignInError", "VerifiedIdentity", "new_http_client"]
