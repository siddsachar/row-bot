"""Sign in with Hugging Face (C1, O134): OAuth, the token kept in the system keychain only.

Hugging Face's OAuth (checked 26 Oct in its own docs) has a ``jobs`` scope, public clients without a secret,
the device-code flow and dynamic client registration, so Row-Bot never asks for a pasted token:

1. The first sign-in on a computer registers Row-Bot as a public client of Hugging Face
   (``/oauth/register``: its name, the device-code and refresh grants, no secret kept on a server).
2. **Sign in with Hugging Face** asks for a device code with exactly the scopes training needs
   (:data:`SCOPES`) and shows Hugging Face's own page with the code filled in; the person signs in there (on
   this computer or any phone or browser) and clicks Authorize. Row-Bot polls the token endpoint meanwhile.
3. The access and refresh tokens are kept in the system keychain (``secret_store``, namespace
   :data:`NAMESPACE`), never in a file, a log or the snapshot; they are refreshed before they expire.
   **Sign out** removes them from the keychain (the person can also remove Row-Bot's access on Hugging Face,
   Settings › Connected apps).

Every request goes to ``huggingface.co`` only, with an ``httpx`` client Row-Bot builds (no proxy from the
environment, short timeouts); tests give it a fake transport.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

ENDPOINT = "https://huggingface.co"
# The device page Hugging Face names in its answer: on hf.co (checked live 28 Oct: "https://hf.co/oauth/device",
# no verification_uri_complete, no interval), or on huggingface.co. Any other host is refused.
DEVICE_PAGES = ("https://hf.co/", "https://huggingface.co/")
# openid/profile: who is signed in; jobs: run training jobs; contribute-repos: make the private dataset and model
# repositories for a run and delete them after (only the ones Row-Bot made); read-billing: whether the account
# can pay, so the guided start can say "add credit" before a run fails for it.
SCOPES = ("openid", "profile", "jobs", "contribute-repos", "read-billing")
NAMESPACE = "learning"
SECRET = "hugging_face"
CLIENT_NAME = "Row-Bot"
REFRESH_MARGIN_SECONDS = 600.0
TIMEOUT_SECONDS = 20.0


class SignInError(RuntimeError):
    """``code``: ``signed_out``, ``expired`` (the code ran out), ``denied``, ``network``, ``refused``,
    ``keychain`` (the system keychain can't hold the sign-in)."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class DeviceCode:
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    interval: float
    expires_at: float


@dataclass(frozen=True)
class SignInState:
    """What the person sees: ``signed_out``, ``signing_in`` (with the code and page), ``signed_in`` (with the
    account name) or ``expired`` (the sign-in can't be refreshed: sign in again)."""

    state: str
    user: str = ""
    code: DeviceCode | None = None
    failure: str | None = None


def _client(transport: Any = None) -> Any:
    import httpx

    return httpx.Client(base_url=ENDPOINT, timeout=TIMEOUT_SECONDS, trust_env=False, transport=transport,
                        headers={"User-Agent": "Row-Bot", "Accept": "application/json"})


def _error_code(response: Any) -> str:
    """Hugging Face's error name for a refused request (``invalid_scope``), never its description."""

    try:
        body = response.json()
    except ValueError:
        return "no error name"
    found = str(body.get("error") or "no error name") if isinstance(body, dict) else "no error name"
    return "".join(ch for ch in found if ch.isalnum() or ch in "_-")[:48] or "no error name"


class HuggingFaceSignIn:
    """The sign-in, one per process (:func:`get`). ``transport`` and ``store`` are the tests' fakes."""

    def __init__(self, *, transport: Any = None, clock: Callable[[], float] = time.time,
                 store: Any = None, start_threads: bool = True) -> None:
        self._transport = transport
        self._clock = clock
        self._store = store
        self._start_threads = start_threads
        self._lock = threading.RLock()
        self._pending: tuple[DeviceCode, str] | None = None
        self._failure: str | None = None
        self._thread: threading.Thread | None = None

    # ── The keychain ────────────────────────────────────────────────────

    def _secrets(self) -> Any:
        if self._store is not None:
            return self._store
        from row_bot import secret_store

        return secret_store

    def _read(self) -> dict[str, Any]:
        try:
            raw = self._secrets().get_secret(SECRET, namespace=NAMESPACE)
        except Exception:  # noqa: BLE001 - a keychain that can't be read reads as signed out
            logger.debug("Learning couldn't read its Hugging Face sign-in", exc_info=True)
            return {}
        try:
            value = json.loads(raw) if raw else {}
        except ValueError:
            return {}
        return value if isinstance(value, dict) else {}

    def _write(self, value: dict[str, Any]) -> None:
        try:
            self._secrets().set_secret(SECRET, json.dumps(value, sort_keys=True), namespace=NAMESPACE)
        except Exception as error:  # noqa: BLE001
            raise SignInError("keychain") from error

    # ── Registration and the device code ────────────────────────────────

    def _client_id(self, http: Any) -> str:
        saved = self._read()
        if saved.get("client_id"):
            return str(saved["client_id"])
        response = http.post("/oauth/register", json={
            "client_name": CLIENT_NAME, "token_endpoint_auth_method": "none", "scope": " ".join(SCOPES),
            "grant_types": ["urn:ietf:params:oauth:grant-type:device_code", "refresh_token"], "redirect_uris": [],
        })
        if response.status_code not in (200, 201):
            logger.warning("Hugging Face refused Row-Bot's client registration (HTTP %s, %s)",
                           response.status_code, _error_code(response))
            raise SignInError("refused")
        client_id = str(response.json().get("client_id") or "")
        if not client_id:
            raise SignInError("refused")
        self._write({**saved, "client_id": client_id})
        return client_id

    def start(self) -> SignInState:
        """Ask Hugging Face for a sign-in code (a new one each time); Row-Bot waits for the person's Authorize."""

        import httpx

        with self._lock:
            try:
                with _client(self._transport) as http:
                    client_id = self._client_id(http)
                    response = http.post("/oauth/device", data={"client_id": client_id, "scope": " ".join(SCOPES)})
            except httpx.HTTPError as error:
                raise SignInError("network") from error
            if response.status_code != 200:
                logger.warning("Hugging Face refused Row-Bot's sign-in code (HTTP %s, %s)",
                               response.status_code, _error_code(response))
                raise SignInError("refused")
            found = response.json()
            uri = str(found.get("verification_uri") or f"{ENDPOINT}/oauth/device")
            user_code = str(found.get("user_code") or "")
            if not user_code or not found.get("device_code") or not uri.startswith(DEVICE_PAGES):
                logger.warning("Hugging Face's sign-in answer wasn't usable (a code: %s, its page on %s)",
                               bool(user_code and found.get("device_code")), uri.split("/")[2] if "//" in uri else "?")
                raise SignInError("refused")
            complete = str(found.get("verification_uri_complete") or f"{uri}?user_code={user_code}")
            if not complete.startswith(DEVICE_PAGES):
                complete = f"{uri}?user_code={user_code}"
            code = DeviceCode(user_code[:32], uri, complete, max(1.0, float(found.get("interval") or 5)),
                              self._clock() + float(found.get("expires_in") or 900))
            self._pending = (code, str(found["device_code"]))
            self._failure = None
            if self._start_threads:
                self._thread = threading.Thread(target=self._wait, name="learning-hf-sign-in", daemon=True)
                self._thread.start()
            return self.status()

    def poll(self) -> str:
        """One poll of the token endpoint: ``waiting``, ``signed_in``, ``expired``, ``denied`` or ``none``."""

        import httpx

        with self._lock:
            pending = self._pending
            if pending is None:
                return "none"
            code, device = pending
            if self._clock() > code.expires_at:
                self._pending, self._failure = None, "expired"
                return "expired"
            client_id = str(self._read().get("client_id") or "")
        try:
            with _client(self._transport) as http:
                response = http.post("/oauth/token", data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code", "device_code": device,
                    "client_id": client_id})
        except httpx.HTTPError:
            return "waiting"  # no answer this time: ask again at the next interval
        body = _json(response)
        if response.status_code == 200 and body.get("access_token"):
            with self._lock:
                if self._pending is None or self._pending[1] != device:
                    return "none"
                self._save_tokens(body)
                self._pending = None
                user = self._whoami(str(body["access_token"]))
                saved = self._read()
                self._write({**saved, "user": user})
            _changed()
            return "signed_in"
        error = str(body.get("error") or "")
        if error in ("authorization_pending", "slow_down"):
            return "waiting"
        with self._lock:
            self._pending = None
            self._failure = "denied" if error == "access_denied" else "expired" if error == "expired_token" else "refused"
        _changed()
        return self._failure

    def _wait(self) -> None:
        while True:
            with self._lock:
                pending = self._pending
            if pending is None:
                return
            time.sleep(pending[0].interval)
            if self.poll() != "waiting":
                return

    def _save_tokens(self, body: dict[str, Any]) -> None:
        saved = self._read()
        self._write({
            "client_id": saved.get("client_id"), "user": saved.get("user", ""),
            "access_token": str(body["access_token"]),
            "refresh_token": str(body.get("refresh_token") or saved.get("refresh_token") or ""),
            "expires_at": self._clock() + float(body.get("expires_in") or 3600),
            "scope": str(body.get("scope") or " ".join(SCOPES)),
        })

    def _whoami(self, token: str) -> str:
        import httpx

        try:
            with _client(self._transport) as http:
                response = http.get("/api/whoami-v2", headers={"Authorization": f"Bearer {token}"})
            return str(_json(response).get("name") or "")[:96] if response.status_code == 200 else ""
        except httpx.HTTPError:
            return ""

    # ── Using it ────────────────────────────────────────────────────────

    def token(self, *, valid_for: float = REFRESH_MARGIN_SECONDS) -> str:
        """A current access token, refreshed when it would expire within ``valid_for`` seconds (a job's token
        must last the job). ``SignInError("signed_out")`` when there is no sign-in, ``("expired")`` when Hugging
        Face won't refresh it."""

        import httpx

        with self._lock:
            saved = self._read()
            access = str(saved.get("access_token") or "")
            if not access:
                raise SignInError("signed_out")
            if float(saved.get("expires_at") or 0) - self._clock() > max(REFRESH_MARGIN_SECONDS, valid_for):
                return access
            refresh = str(saved.get("refresh_token") or "")
            if not refresh:
                raise SignInError("expired")
            try:
                with _client(self._transport) as http:
                    response = http.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": refresh,
                                                              "client_id": str(saved.get("client_id") or "")})
            except httpx.HTTPError as error:
                raise SignInError("network") from error
            body = _json(response)
            if response.status_code != 200 or not body.get("access_token"):
                self._write({key: value for key, value in saved.items() if key in ("client_id", "user")})
                raise SignInError("expired")
            self._save_tokens(body)
            return str(body["access_token"])

    def status(self) -> SignInState:
        with self._lock:
            if self._pending is not None:
                return SignInState("signing_in", code=self._pending[0])
            saved = self._read()
            if saved.get("access_token"):
                return SignInState("signed_in", user=str(saved.get("user") or ""))
            if saved.get("user"):
                return SignInState("expired", user=str(saved.get("user") or ""), failure=self._failure)
            return SignInState("signed_out", failure=self._failure)

    def cancel(self) -> None:
        with self._lock:
            self._pending = None

    def sign_out(self) -> None:
        """The sign-in leaves the keychain (the registration stays: it holds no secret)."""

        with self._lock:
            self._pending = None
            saved = self._read()
            client_id = saved.get("client_id")
            try:
                if client_id:
                    self._write({"client_id": client_id})
                else:
                    self._secrets().delete_secret(SECRET, namespace=NAMESPACE)
            except Exception as error:  # noqa: BLE001
                raise SignInError("keychain") from error
        _changed()


def _json(response: Any) -> dict[str, Any]:
    try:
        value = response.json()
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _changed() -> None:
    try:
        from row_bot import learning

        learning.changed()
    except Exception:  # noqa: BLE001 - a live refresh is a courtesy
        logger.debug("Learning couldn't announce a sign-in change", exc_info=True)


_lock = threading.Lock()
_sign_in: HuggingFaceSignIn | None = None


def get() -> HuggingFaceSignIn:
    global _sign_in
    with _lock:
        if _sign_in is None:
            _sign_in = HuggingFaceSignIn()
        return _sign_in


def configure(sign_in: HuggingFaceSignIn | None) -> None:
    """Tests and the browser fixture put their own (fake) sign-in in place; None forgets it."""

    global _sign_in
    with _lock:
        _sign_in = sign_in


__all__ = ["DeviceCode", "HuggingFaceSignIn", "NAMESPACE", "SCOPES", "SignInError", "SignInState", "configure",
           "get"]
