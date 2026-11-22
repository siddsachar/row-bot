"""Sign-in routes: ``GET /auth/login``, ``GET /auth/callback``, ``POST /auth/logout``, ``GET /api/me`` and the
``GET /admin/`` landing.

The sign-in's ``state``, ``nonce``, PKCE verifier and a fourth secret for the browser are new random
secrets each time. The database keeps the first three's HMAC verifiers and the PKCE verifier itself
for ten minutes (``auth_attempts``); the browser gets the fourth in the ``__Host-`` sign-in cookie
(``Secure``, ``HttpOnly``, ``SameSite=Lax``, so it comes back on the provider's top-level redirect and
on nothing cross-site else). The callback deletes the attempt for its ``state`` first (single use, even
when what follows fails), then needs the browser's cookie to match: a callback link made in another
browser (login CSRF) or replayed finds nothing. Both steps are limited per client address
(``identity.limits``), before anything reaches the database.

A sign-in ends with a new session (rotated: the browser's previous session is revoked), in the
``__Host-`` session cookie with the same attributes, and lands on ``/admin/``. The database keeps only
the session's verifier.
"""

from __future__ import annotations

import hmac
import logging
from collections.abc import Iterator

import psycopg
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from psycopg_pool import ConnectionPool

from row_bot_org.db.rls import principal_transaction
from row_bot_org.identity import store
from row_bot_org.identity.gate import SESSION_COOKIE, SIGN_IN_COOKIE, RequestPrincipal, request_principal
from row_bot_org.identity.limits import WINDOW_SECONDS, AddressRateLimit
from row_bot_org.identity.oidc import OidcProvider, SignInError
from row_bot_org.identity.tokens import (
    is_secret,
    new_secret,
    new_session_token,
    pkce_challenge,
    session_secret,
    verifier,
)
from row_bot_org.settings import OrgSettings

logger = logging.getLogger(__name__)
CALLBACK_PATH = "/auth/callback"
# Where a sign-in lands: the admin console's address (until the console exists, a JSON "signed in").
LANDING_PATH = "/admin/"
SIGN_IN_COOKIE_SECONDS = int(store.SIGN_IN_LIFETIME.total_seconds())
MAX_CODE_LENGTH = 4096
# Failures the provider (or its configuration) causes, rather than the browser.
_PROVIDER_FAILURES = {"idp_unavailable", "idp_misconfigured", "token_exchange_failed"}


class IdentityService:
    """Sign-in for one server: its provider, its pool and pepper, and the session settings."""

    def __init__(self, settings: OrgSettings, pool: ConnectionPool, provider: OidcProvider | None = None) -> None:
        if not (settings.public_url and settings.hmac_pepper and settings.idp):
            raise ValueError("sign-in needs ROW_BOT_ORG_IDP, ROW_BOT_ORG_PUBLIC_URL and ROW_BOT_ORG_HMAC_PEPPER")
        self.settings = settings
        self.pool = pool
        self.origin = settings.public_url
        self.redirect_uri = settings.public_url + CALLBACK_PATH
        self._pepper = settings.hmac_pepper
        self.provider = provider or OidcProvider(settings.idp)
        self.limit = AddressRateLimit(settings.sign_in_rate_limit)

    def close(self) -> None:
        self.provider.close()

    def verifier(self, purpose: str, secret: str) -> bytes:
        return verifier(self._pepper, purpose, secret)

    def resolve(self, token: str) -> RequestPrincipal | None:
        """The gate's lookup: the live session for a session cookie's value."""
        secret = session_secret(token)
        if secret is None:
            return None
        found = store.resume_session(self.pool, self.verifier("session", secret))
        return None if found is None else RequestPrincipal(found.principal_id, found.groups, found.session_id)


def principal_connection(request: Request,
                         principal: RequestPrincipal = Depends(request_principal)) -> Iterator[psycopg.Connection]:
    """A connection in ``principal_transaction`` for the caller: commits when the route returns.

    Use as ``Depends(principal_connection, scope="function")``, so the commit happens before the response.
    """
    with principal_transaction(request.app.state.pool, principal.principal_id, principal.groups) as conn:
        yield conn


def _client_address(request: Request) -> str:
    return request.client.host if request.client else ""


def _set_cookie(response: Response, name: str, value: str, max_age: int) -> None:
    # __Host-: Secure, Path=/ and no Domain, so no other host or path can set or read it.
    response.set_cookie(name, value, max_age=max_age, path="/", secure=True, httponly=True, samesite="lax")


def _clear_cookie(response: Response, name: str) -> None:
    response.delete_cookie(name, path="/", secure=True, httponly=True, samesite="lax")


def _failed(reason: str, status: int = 400) -> JSONResponse:
    response = JSONResponse({"error": "sign_in_failed", "reason": reason}, status_code=status)
    _clear_cookie(response, SIGN_IN_COOKIE)
    return response


def _too_many() -> JSONResponse:
    return JSONResponse({"error": "too_many_requests"}, status_code=429,
                        headers={"Retry-After": str(int(WINDOW_SECONDS))})


def identity_router(service: IdentityService) -> APIRouter:
    router = APIRouter()
    settings = service.settings
    provider = service.provider
    kind = provider.config.kind

    @router.get("/auth/login")
    def login(request: Request) -> Response:
        if not service.limit.allow(_client_address(request)):
            return _too_many()
        state, nonce, browser, code_verifier = new_secret(), new_secret(), new_secret(), new_secret()
        try:
            location = provider.authorization_url(redirect_uri=service.redirect_uri, state=state, nonce=nonce,
                                                  code_challenge=pkce_challenge(code_verifier))
        except SignInError as error:
            logger.warning("sign-in with %s can't start: %s (%s)", kind, error.reason, error.detail)
            return _failed(error.reason, 502)
        store.begin_sign_in(service.pool, state=service.verifier("sign-in-state", state),
                            browser=service.verifier("sign-in-browser", browser),
                            nonce=service.verifier("sign-in-nonce", nonce), code_verifier=code_verifier)
        response = RedirectResponse(location, status_code=302)
        _set_cookie(response, SIGN_IN_COOKIE, browser, SIGN_IN_COOKIE_SECONDS)
        return response

    @router.get(CALLBACK_PATH)
    def callback(request: Request, state: str | None = None, code: str | None = None,
                 error: str | None = None) -> Response:
        if not service.limit.allow(_client_address(request)):
            return _too_many()
        if not is_secret(state):
            return _failed("invalid_request")
        attempt = store.take_sign_in(service.pool, service.verifier("sign-in-state", state or ""))
        if attempt is None:
            return _failed("unknown_or_used_state")
        browser = request.cookies.get(SIGN_IN_COOKIE)
        if not is_secret(browser) or not hmac.compare_digest(
                service.verifier("sign-in-browser", browser or ""), attempt.browser):
            return _failed("not_this_browser")
        if error is not None:
            logger.info("sign-in with %s ended at the provider", kind)
            return _failed("idp_error")
        if not code or len(code) > MAX_CODE_LENGTH:
            return _failed("invalid_request")
        try:
            id_token = provider.exchange_code(code=code, redirect_uri=service.redirect_uri,
                                              code_verifier=attempt.code_verifier)
            identity = provider.validate_id_token(id_token, nonce_matches=lambda nonce: hmac.compare_digest(
                service.verifier("sign-in-nonce", nonce), attempt.nonce))
        except SignInError as failure:
            logger.warning("sign-in with %s failed: %s (%s)", kind, failure.reason, failure.detail)
            return _failed(failure.reason, 502 if failure.reason in _PROVIDER_FAILURES else 400)
        previous = session_secret(request.cookies.get(SESSION_COOKIE))
        token = new_session_token()
        principal_id, status = store.sign_in(
            service.pool, identity, verifier=service.verifier("session", session_secret(token) or ""),
            lifetime=settings.session_lifetime, idle=settings.session_idle_timeout,
            user_agent=request.headers.get("user-agent", "")[:200], address=_client_address(request),
            replaces=None if previous is None else service.verifier("session", previous))
        if status != "active":
            logger.warning("sign-in refused: principal %s is %s", principal_id, status)
            return _failed("principal_suspended", 403)
        logger.info("principal %s signed in with %s", principal_id, kind)
        response = RedirectResponse(service.origin + LANDING_PATH, status_code=303)
        _set_cookie(response, SESSION_COOKIE, token, settings.session_lifetime)
        _clear_cookie(response, SIGN_IN_COOKIE)
        return response

    @router.post("/auth/logout")
    def logout(principal: RequestPrincipal = Depends(request_principal),
               conn: psycopg.Connection = Depends(principal_connection, scope="function")) -> Response:
        store.revoke_session(conn, principal.session_id)
        response = Response(status_code=204)
        _clear_cookie(response, SESSION_COOKIE)
        return response

    @router.get("/api/me")
    def me(principal: RequestPrincipal = Depends(request_principal),
           conn: psycopg.Connection = Depends(principal_connection, scope="function")) -> JSONResponse:
        found = store.current_principal(conn, principal.principal_id)
        if found is None:
            return JSONResponse({"error": "sign_in_required"}, status_code=401)
        return JSONResponse(found)

    @router.get(LANDING_PATH)
    def landing(principal: RequestPrincipal = Depends(request_principal)) -> JSONResponse:
        # The admin console (S6) takes this address over.
        return JSONResponse({"status": "signed_in"})

    return router


__all__ = ["CALLBACK_PATH", "LANDING_PATH", "IdentityService", "identity_router", "principal_connection"]
