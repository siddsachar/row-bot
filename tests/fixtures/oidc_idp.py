"""A fake OpenID Connect provider on loopback: discovery, JWKS and the token endpoint, nothing real.

``FakeIdp(shape)`` serves one issuer shaped like Entra ID (``<base>/<tenant>/v2.0``, ``tid``, GUID groups,
an email without ``email_verified``), Okta (``<base>/oauth2/default``, ``email_verified``, named groups)
or Google (the bare origin, ``azp``, ``hd``, no groups). It signs with keys it generates (RS256 unless
told otherwise), checks the client (its secret by Basic auth), the redirect URI and PKCE S256 at its token endpoint, and hands out each code once.

A test plays the browser: ``authorize(location)`` takes the server's redirect to the provider, "signs
the person in" with the shape's claims (plus overrides) and returns the callback URL to follow.
``id_token(claims, ...)`` makes a token directly, for checks of the validator. Reuse it for device
sign-in later.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

from joserfc import jwt
from joserfc.jwk import ECKey, OKPKey, RSAKey

SHAPES = ("entra", "okta", "google")
TENANT = "6f1c2a3b-4d5e-4f60-8a7b-9c0d1e2f3a4b"
OTHER_TENANT = "0a0b0c0d-1111-4222-8333-944455556666"
CLIENT_ID = "row-bot-org-test-client"
CLIENT_SECRET = "fake-client-secret~with/odd+characters="
PUBLIC_URL = "https://org.example.test"
REDIRECT_URI = PUBLIC_URL + "/auth/callback"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


# RSA key generation takes a fraction of a second, so each process makes three per kind and hands them
# out in turn, each time with a new kid.
_MATERIAL: dict[str, list[dict[str, Any]]] = {}
_HANDED_OUT: dict[str, int] = {}
_KEY_TYPES = {"RS256": (RSAKey, 2048), "PS256": (RSAKey, 2048), "ES256": (ECKey, "P-256"),
              "EdDSA": (OKPKey, "Ed25519")}


def new_key(alg: str = "RS256") -> Any:
    """A signing key for ``alg`` with a kid of its own."""
    key_type, size = _KEY_TYPES[alg]
    kind = key_type.__name__
    material = _MATERIAL.setdefault(kind, [])
    turn = _HANDED_OUT.get(kind, 0)
    _HANDED_OUT[kind] = turn + 1
    if len(material) < 3:
        material.append(key_type.generate_key(size).as_dict(private=True))
    fields = {**material[turn % len(material)], "kid": secrets.token_hex(8), "use": "sig", "alg": alg}
    return key_type.import_key(fields)


class FakeIdp:
    def __init__(self, shape: str = "okta", *, client_id: str = CLIENT_ID, client_secret: str = CLIENT_SECRET,
                 redirect_uri: str = REDIRECT_URI, tenant: str = TENANT) -> None:
        if shape not in SHAPES:
            raise ValueError(shape)
        self.shape = shape
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.tenant = tenant
        self.keys = [new_key()]
        # What discovery says the issuer is (a test can make it differ from the real one).
        self.discovery_issuer: str | None = None
        self.requests: list[str] = []
        self.token_requests: list[dict[str, Any]] = []
        self._codes: dict[str, dict[str, Any]] = {}
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._server.daemon_threads = True
        self._thread: threading.Thread | None = None

    # -- addresses ---------------------------------------------------------------------------------

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    @property
    def issuer(self) -> str:
        return {"entra": f"{self.base_url}/{self.tenant}/v2.0", "okta": f"{self.base_url}/oauth2/default",
                "google": self.base_url}[self.shape]

    def config(self, **overrides: Any) -> dict[str, Any]:
        """This provider for ``ROW_BOT_ORG_IDP``."""
        entry: dict[str, Any] = {"kind": self.shape, "issuer": self.issuer, "client_id": self.client_id}
        if self.shape == "entra":
            entry["allowed_tenants"] = [self.tenant]
        entry.update(overrides)
        return entry

    # -- tokens ------------------------------------------------------------------------------------

    def claims(self, subject: str = "person-1", **overrides: Any) -> dict[str, Any]:
        now = int(time.time())
        base: dict[str, Any] = {"iss": self.issuer, "aud": self.client_id, "sub": subject, "iat": now,
                                "exp": now + 300}
        if self.shape == "entra":
            base.update({"tid": self.tenant, "oid": "11111111-2222-4333-8444-555555555555", "ver": "2.0",
                         "nbf": now, "name": "Ada Example", "preferred_username": "ada@contoso.example",
                         "email": "ada@contoso.example",
                         "groups": ["7c9e6679-7425-40de-944b-e07fc1f90ae7", "9b2f1c3e-0000-4000-8000-000000000001"]})
        elif self.shape == "okta":
            base.update({"name": "Ada Example", "email": "ada@example.com", "email_verified": True,
                         "auth_time": now, "groups": ["Everyone", "Engineering"]})
        else:
            base.update({"azp": self.client_id, "email": "ada@example.com", "email_verified": True,
                         "hd": "example.com", "name": "Ada Example"})
        base.update(overrides)
        return {key: value for key, value in base.items() if value is not None}

    def id_token(self, claims: dict[str, Any], *, key: Any = None, header: dict[str, Any] | None = None) -> str:
        signer = key or self.keys[-1]
        alg = signer.get("alg")
        fields = {"alg": alg, "kid": signer.kid, "typ": "JWT", **(header or {})}
        return jwt.encode(fields, claims, signer, algorithms=[alg])

    def none_token(self, claims: dict[str, Any]) -> str:
        """An unsigned token (``alg=none``) naming the current key."""
        header = {"alg": "none", "kid": self.keys[-1].kid, "typ": "JWT"}
        return _b64(json.dumps(header).encode()) + "." + _b64(json.dumps(claims).encode()) + "."

    def hs256_token(self, claims: dict[str, Any], secret: bytes) -> str:
        """A token MACed with HS256 under ``secret`` (say, the public key's PEM) naming the current key."""
        header = {"alg": "HS256", "kid": self.keys[-1].kid, "typ": "JWT"}
        signing = _b64(json.dumps(header).encode()) + "." + _b64(json.dumps(claims).encode())
        return signing + "." + _b64(hmac.new(secret, signing.encode(), hashlib.sha256).digest())

    def rotate(self, alg: str = "RS256", *, keep_old: bool = False) -> Any:
        """A new signing key, used from now on; the old ones leave the JWKS unless ``keep_old``."""
        key = new_key(alg)
        self.keys = [*self.keys, key] if keep_old else [key]
        return key

    # -- the browser's part ------------------------------------------------------------------------

    def authorize(self, location: str, *, subject: str = "person-1", header: dict[str, Any] | None = None,
                  **claim_overrides: Any) -> str:
        """Sign the person in for the authorization request at ``location``; the callback URL to follow."""
        parts = urlsplit(location)
        assert f"{parts.scheme}://{parts.netloc}" == self.base_url and parts.path == "/authorize"
        query = {name: values[0] for name, values in parse_qs(parts.query).items()}
        assert query["response_type"] == "code" and query["code_challenge_method"] == "S256"
        assert query["client_id"] == self.client_id
        code = secrets.token_urlsafe(24)
        claims = self.claims(subject, **{"nonce": query["nonce"], **claim_overrides})
        self._codes[code] = {"claims": claims, "header": header, "redirect_uri": query["redirect_uri"],
                             "challenge": query["code_challenge"]}
        return query["redirect_uri"] + "?" + urlencode({"code": code, "state": query["state"]})

    # -- the HTTP server ---------------------------------------------------------------------------

    def _discovery(self) -> dict[str, Any]:
        return {"issuer": self.discovery_issuer or self.issuer,
                "authorization_endpoint": f"{self.base_url}/authorize",
                "token_endpoint": f"{self.base_url}/token", "jwks_uri": f"{self.base_url}/jwks",
                "response_types_supported": ["code"], "subject_types_supported": ["public"],
                "id_token_signing_alg_values_supported": ["RS256"], "code_challenge_methods_supported": ["S256"]}

    def _client_ok(self, headers: Any, form: dict[str, str]) -> bool:
        authorization = headers.get("Authorization", "")
        if authorization.startswith("Basic "):
            user, _, password = base64.b64decode(authorization[6:]).decode().partition(":")
            return unquote(user) == self.client_id and unquote(password) == self.client_secret
        return False

    def _token(self, headers: Any, body: bytes) -> tuple[int, dict[str, Any]]:
        form = {name: values[0] for name, values in parse_qs(body.decode()).items()}
        self.token_requests.append(form)
        if not self._client_ok(headers, form):
            return 401, {"error": "invalid_client"}
        grant = self._codes.pop(form.get("code", ""), None)
        if form.get("grant_type") != "authorization_code" or grant is None:
            return 400, {"error": "invalid_grant"}
        if form.get("redirect_uri") != grant["redirect_uri"] or grant["redirect_uri"] != self.redirect_uri:
            return 400, {"error": "invalid_grant"}
        challenge = _b64(hashlib.sha256(form.get("code_verifier", "").encode()).digest())
        if challenge != grant["challenge"]:
            return 400, {"error": "invalid_grant"}
        return 200, {"access_token": secrets.token_urlsafe(16), "token_type": "Bearer", "expires_in": 300,
                     "id_token": self.id_token(grant["claims"], header=grant["header"])}

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        idp = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _send(self, status: int, document: dict[str, Any]) -> None:
                payload = json.dumps(document).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self) -> None:  # noqa: N802 - http.server's name
                idp.requests.append(self.path)
                if self.path == urlsplit(idp.issuer).path.rstrip("/") + "/.well-known/openid-configuration":
                    self._send(200, idp._discovery())
                elif self.path == "/jwks":
                    self._send(200, {"keys": [key.as_dict(private=False) for key in idp.keys]})
                else:
                    self._send(404, {"error": "not_found"})

            def do_POST(self) -> None:  # noqa: N802
                idp.requests.append(self.path)
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                if self.path == "/token":
                    self._send(*idp._token(self.headers, body))
                else:
                    self._send(404, {"error": "not_found"})

        return Handler

    def start(self) -> FakeIdp:
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.01},
                                        name="fake-idp", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)


@contextmanager
def running_idp(shape: str = "okta", **options: Any) -> Iterator[FakeIdp]:
    idp = FakeIdp(shape, **options).start()
    try:
        yield idp
    finally:
        idp.stop()


__all__ = ["CLIENT_ID", "CLIENT_SECRET", "OTHER_TENANT", "PUBLIC_URL", "REDIRECT_URI", "SHAPES", "TENANT",
           "FakeIdp", "new_key", "running_idp"]
